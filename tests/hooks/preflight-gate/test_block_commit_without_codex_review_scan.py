"""The transcript scan behind block-commit-without-codex-review (issue #1277).

The gate used to load the whole transcript into a list and `json.loads` every
line — 490 ms and ~70 MB of RSS per `git commit` on a 36 MB session. It now
streams and parses only the lines that carry the skill's name. These pin the
three properties that rewrite must keep: the prefilter cannot hide a genuine
invocation, the byte bound still answers None (not a partial verdict), and
the memory stays flat with the file size.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tracemalloc
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
IMPL = REPO_ROOT / "hooks" / "preflight-gate" / "block-commit-without-codex-review" / "impl.py"

_spec = importlib.util.spec_from_file_location("codex_review_gate", IMPL)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
sys.modules["codex_review_gate"] = gate
_spec.loader.exec_module(gate)
# The parse the gate pays for lives in the shared reader, not in the gate.
_reader = sys.modules["_transcript"]


def _skill_use(skill: str) -> str:
    """Skill use."""
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_1", "name": "Skill", "input": {"skill": skill}},
    ]}})


def _slash(text: str, role: str = "user") -> str:
    """Slash."""
    return json.dumps({"type": role, "message": {"role": role, "content": text}})


def _filler(n: int) -> list[str]:
    """Filler."""
    return [json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": f"step {i} " + "x" * 200}]}}) for i in range(n)]


def _write(tmp_path: Path, lines: list[str], name: str = "t.jsonl") -> str:
    """Write."""
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def test_needle_matches_the_encoded_skill_name():
    # The prefilter is only sound if JSON encoding leaves the literal intact.
    """Needle matches the encoded skill name."""
    for value in ("praxis:codex-review-wrap", "/praxis:codex-review-wrap", "/codex-review-wrap"):
        assert gate._SKILL_NEEDLE.decode() in json.dumps(value)


def test_skill_tool_use_is_found_among_filler(tmp_path):
    """Skill tool use is found among filler."""
    path = _write(tmp_path, _filler(500) + [_skill_use("praxis:codex-review-wrap")] + _filler(500))
    assert gate._transcript_invokes_skill(path, check_slash=True) is True
    assert gate._transcript_invokes_skill(path, check_slash=False) is True


def test_user_slash_command_is_found_on_root_only(tmp_path):
    """User slash command is found on root only."""
    path = _write(tmp_path, _filler(50) + [_slash("/praxis:codex-review-wrap")])
    assert gate._transcript_invokes_skill(path, check_slash=True) is True
    assert gate._transcript_invokes_skill(path, check_slash=False) is False


def test_assistant_mention_is_not_an_invocation(tmp_path):
    # The needle appears (prose suggestion) but the record is not a genuine
    # invocation: the prefilter admits it, the structural check rejects it.
    """Assistant mention is not an invocation."""
    path = _write(tmp_path, [_slash("run /praxis:codex-review-wrap first?", role="assistant")])
    assert gate._transcript_invokes_skill(path, check_slash=True) is False


def test_other_skill_is_not_an_invocation(tmp_path):
    """Other skill is not an invocation."""
    path = _write(tmp_path, [_skill_use("praxis:retrospect")])
    assert gate._transcript_invokes_skill(path, check_slash=True) is False


def test_missing_and_oversized_answer_none(tmp_path, monkeypatch):
    """Missing and oversized answer none."""
    assert gate._transcript_invokes_skill(str(tmp_path / "absent.jsonl"), check_slash=True) is None
    path = _write(tmp_path, _filler(20) + [_skill_use("praxis:codex-review-wrap")])
    monkeypatch.setattr(gate, "_MAX_BYTES", 100)
    # Past the bound the answer is "cannot enforce", never a verdict from the
    # prefix that was read — a fail-open the caller turns into a pass.
    assert gate._transcript_invokes_skill(path, check_slash=True) is None


def test_scan_memory_does_not_track_file_size(tmp_path):
    """Scan memory does not track file size."""
    small = _write(tmp_path, _filler(200), name="small.jsonl")
    big = _write(tmp_path, _filler(20000), name="big.jsonl")
    assert Path(big).stat().st_size > 20 * Path(small).stat().st_size

    def peak(path: str) -> int:
        """Peak."""
        tracemalloc.start()
        try:
            assert gate._transcript_invokes_skill(path, check_slash=True) is False
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    # A materializing reader's peak grows with the file; a streaming one holds
    # a line at a time, so the two peaks stay within a small constant of each
    # other regardless of the 100x size gap.
    assert peak(big) < 4 * peak(small) + 1 * 1024 * 1024


def test_one_oversized_line_is_refused_before_allocation(tmp_path, monkeypatch):
    # A single line past the bound must not be pulled into memory whole and
    # only then measured: the read itself is capped at the budget left.
    """One oversized line is refused before allocation."""
    path = tmp_path / "t.jsonl"
    path.write_bytes(b'{"type": "assistant", "pad": "' + b"x" * (4 * 1024 * 1024) + b'"}\n')
    monkeypatch.setattr(gate, "_MAX_BYTES", 64 * 1024)
    tracemalloc.start()
    try:
        assert gate._transcript_invokes_skill(str(path), check_slash=True) is None
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 1024 * 1024


def test_non_regular_path_answers_none_without_blocking(tmp_path):
    # A FIFO at the path must not be opened: open() would block for the whole
    # shared dispatch deadline. The is_file() guard answers None at once.
    """Non regular path answers none without blocking."""
    import os
    fifo = tmp_path / "t.fifo"
    os.mkfifo(fifo)
    assert gate._transcript_invokes_skill(str(fifo), check_slash=True) is None
    assert gate._transcript_invokes_skill("bad\x00path", check_slash=True) is None


def test_cursor_continues_across_calls_and_a_found_state_reads_nothing(tmp_path, monkeypatch):
    """The budget is per call: None until caught up, then True, then True
    without parsing once the invocation is on record."""
    path = _write(tmp_path, _filler(20) + [_skill_use("praxis:codex-review-wrap")])
    cursor = str(tmp_path / "cursor.json")
    monkeypatch.setattr(gate, "_MAX_BYTES", Path(path).stat().st_size // 2)
    assert gate._transcript_invokes_skill(path, check_slash=True, cursor_path=cursor) is None
    assert gate._transcript_invokes_skill(path, check_slash=True, cursor_path=cursor) is None
    assert gate._transcript_invokes_skill(path, check_slash=True, cursor_path=cursor) is True
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(_filler(5)) + "\n")
    calls: list = []
    monkeypatch.setattr(_reader, "_parse_line", lambda raw: calls.append(raw))
    assert gate._transcript_invokes_skill(path, check_slash=True, cursor_path=cursor) is True
    assert calls == []


def test_scan_keys_one_cursor_per_transcript_file(tmp_path, monkeypatch):
    """Root and each subagent file get their own cursor under the session."""
    root = _write(tmp_path, _filler(3), name="sess.jsonl")
    sub = tmp_path / "sess" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-a.jsonl").write_text("\n".join(_filler(2)) + "\n", encoding="utf-8")
    (sub / "agent-b.jsonl").write_text(_skill_use("praxis:codex-review-wrap") + "\n", encoding="utf-8")
    parts: list = []

    def fake_cursor(hook, session_id, part="root"):
        """Record the part and hand back a per-part cursor file."""
        parts.append((hook, session_id, part))
        return str(tmp_path / f"cursor-{part}.json")

    monkeypatch.setattr(gate, "scan_cursor_path", fake_cursor)
    assert gate._scan_transcript(root, "sess-1") is True
    assert parts == [
        (gate._HOOK_NAME, "sess-1", "root"),
        (gate._HOOK_NAME, "sess-1", "agent-a"),
        (gate._HOOK_NAME, "sess-1", "agent-b"),
    ]
    # A second call answers from the cursors without re-reading the bodies.
    calls: list = []
    monkeypatch.setattr(_reader, "_parse_line", lambda raw: calls.append(raw))
    assert gate._scan_transcript(root, "sess-1") is True
    assert calls == []


def test_subagent_not_caught_up_is_indeterminate_not_false(tmp_path, monkeypatch):
    """The root reads to EOF and says no; a subagent's unread tail may still
    hold the invocation, so the gate answers None until it has caught up,
    then True — never a False that strict mode would block on."""
    root = _write(tmp_path, _filler(2), name="sess.jsonl")
    sub = tmp_path / "sess" / "subagents"
    sub.mkdir(parents=True)
    agent = sub / "agent-a.jsonl"
    agent.write_text("\n".join(_filler(20) + [_skill_use("praxis:codex-review-wrap")]) + "\n",
                     encoding="utf-8")
    monkeypatch.setattr(gate, "scan_cursor_path",
                        lambda hook, sid, part="root": str(tmp_path / f"cursor-{part}.json"))
    monkeypatch.setattr(gate, "_MAX_BYTES", agent.stat().st_size // 2)
    assert gate._scan_transcript(root, "sess-1") is None
    assert gate._scan_transcript(root, "sess-1") is None
    assert gate._scan_transcript(root, "sess-1") is True


def test_unreadable_subagent_is_skipped_not_indeterminate(tmp_path, monkeypatch):
    """A subagent file that cannot be read contributes nothing: the root's
    definitive answer stands (spec: 'skipped, not a fail-open')."""
    root = _write(tmp_path, _filler(2), name="sess.jsonl")
    sub = tmp_path / "sess" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-a.jsonl").mkdir()  # a directory where a file is expected
    monkeypatch.setattr(gate, "scan_cursor_path",
                        lambda hook, sid, part="root": str(tmp_path / f"cursor-{part}.json"))
    assert gate._scan_transcript(root, "sess-1") is False
