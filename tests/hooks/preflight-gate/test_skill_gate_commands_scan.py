"""Bounded streaming scan behind skill-gate-commands (issue #1312)."""
from __future__ import annotations

import importlib.util
import json
import sys
import tracemalloc
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
IMPL = REPO_ROOT / "hooks" / "preflight-gate" / "skill-gate-commands" / "impl.py"
_spec = importlib.util.spec_from_file_location("skill_gate_commands", IMPL)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
sys.modules["skill_gate_commands"] = gate
_spec.loader.exec_module(gate)

SKILL = "praxis:codex-review-wrap"


def _skill_use(skill: str) -> str:
    """Skill use."""
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_1", "name": "Skill", "input": {"skill": skill}}]}})


def _filler(n: int) -> list[str]:
    """Filler."""
    return [json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": f"step {i} " + "x" * 200}]}}) for i in range(n)]


def _write(path: Path, lines: list[str]) -> str:
    """Write."""
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def test_invocation_among_filler_is_found(tmp_path):
    """Invocation among filler is found."""
    path = _write(tmp_path / "t.jsonl", _filler(300) + [_skill_use(SKILL)] + _filler(300))
    assert gate._scan_transcript(path, SKILL) is True
    assert gate._scan_transcript(path, "praxis:other") is False


def test_prefilter_falls_back_when_the_name_needs_escaping(tmp_path):
    # A name json.dumps would escape cannot be probed as a raw substring; the
    # scan must parse every line rather than miss the record.
    """Prefilter falls back when the name needs escaping."""
    odd = 'skill "quoted"'
    path = _write(tmp_path / "t.jsonl", _filler(5) + [_skill_use(odd)])
    assert gate._scan_transcript(path, odd) is True


def test_missing_and_oversized_answer_none(tmp_path, monkeypatch):
    """Missing and oversized answer none."""
    assert gate._scan_transcript(str(tmp_path / "absent.jsonl"), SKILL) is None
    path = _write(tmp_path / "t.jsonl", _filler(20) + [_skill_use(SKILL)])
    monkeypatch.setattr(gate, "_MAX_BYTES", 100)
    assert gate._scan_transcript(path, SKILL) is None


def test_one_oversized_line_is_refused_before_allocation(tmp_path, monkeypatch):
    """One oversized line is refused before allocation."""
    path = tmp_path / "t.jsonl"
    path.write_bytes(b'{"type": "assistant", "pad": "' + b"x" * (4 * 1024 * 1024) + b'"}\n')
    monkeypatch.setattr(gate, "_MAX_BYTES", 64 * 1024)
    tracemalloc.start()
    try:
        assert gate._scan_transcript(str(path), SKILL) is None
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 1024 * 1024


def test_non_ascii_name_written_escaped_is_still_found(tmp_path):
    # A host that escapes non-ASCII (json.dumps default) writes the skill as
    # "praxis:\\ud68c\\uace0"; a raw-UTF-8 needle would never match it and
    # the gate would wrongly block. json_needle answers None for such a name,
    # so the scan parses every line instead.
    """Non ascii name written escaped is still found."""
    name = "praxis:회고"
    line = json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "Skill", "input": {"skill": name}}]}}, ensure_ascii=True)
    assert "\\u" in line
    path = _write(tmp_path / "t.jsonl", _filler(5) + [line])
    assert gate._scan_transcript(path, name) is True
