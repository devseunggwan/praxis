"""Bounded streaming seen-set scan behind pre-commit-staged-file-enumeration (issue #1312)."""
from __future__ import annotations

import importlib.util
import json
import sys
import tracemalloc
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
IMPL = REPO_ROOT / "hooks" / "advisory-nudge" / "pre-commit-staged-file-enumeration" / "impl.py"
_spec = importlib.util.spec_from_file_location("staged_file_enumeration", IMPL)
assert _spec is not None and _spec.loader is not None
hook = importlib.util.module_from_spec(_spec)
sys.modules["staged_file_enumeration"] = hook
_spec.loader.exec_module(hook)


def _write_use(tool_id: str, path: str) -> str:
    """Write use."""
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tool_id, "name": "Write", "input": {"file_path": path}}]}})


def _result(tool_id: str, is_error: bool) -> str:
    """Result."""
    return json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_id, "is_error": is_error, "content": "x"}]}})


def _filler(n: int) -> list[str]:
    """Filler."""
    return [json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": f"step {i} " + "x" * 200}]}}) for i in range(n)]


def _write(path: Path, lines: list[str]) -> str:
    """Write."""
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def test_seen_set_keeps_successful_writes_and_drops_failed_ones(tmp_path):
    """Seen set keeps successful writes and drops failed ones."""
    ok, bad = str(tmp_path / "ok.txt"), str(tmp_path / "bad.txt")
    path = _write(tmp_path / "t.jsonl", _filler(200) + [
        _write_use("t1", ok), _result("t1", False),
        _write_use("t2", bad), _result("t2", True),
    ] + _filler(200))
    seen = hook._seen_realpaths(path)
    assert seen is not None
    assert hook._canonical(ok) in seen
    assert hook._canonical(bad) not in seen


def test_missing_and_oversized_answer_none(tmp_path, monkeypatch):
    """Missing and oversized answer none."""
    assert hook._seen_realpaths(str(tmp_path / "absent.jsonl")) is None
    path = _write(tmp_path / "t.jsonl", _filler(20))
    monkeypatch.setattr(hook, "_MAX_TRANSCRIPT_BYTES", 100)
    assert hook._seen_realpaths(path) is None


def test_one_oversized_line_is_refused_before_allocation(tmp_path, monkeypatch):
    """One oversized line is refused before allocation."""
    path = tmp_path / "t.jsonl"
    path.write_bytes(b'{"type": "assistant", "pad": "' + b"x" * (4 * 1024 * 1024) + b'"}\n')
    monkeypatch.setattr(hook, "_MAX_TRANSCRIPT_BYTES", 64 * 1024)
    tracemalloc.start()
    try:
        assert hook._seen_realpaths(str(path)) is None
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 1024 * 1024
