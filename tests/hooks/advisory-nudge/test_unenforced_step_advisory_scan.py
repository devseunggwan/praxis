"""The resumable session scan behind unenforced-step-advisory (issue #1278).

The advisory's facts are monotone, so a commit should read only the bytes
appended since the previous one, never re-derive a settled fact, and never
let one trigger's cursor skip lines another trigger reads.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
IMPL = REPO_ROOT / "hooks" / "advisory-nudge" / "unenforced-step-advisory" / "impl.py"
_spec = importlib.util.spec_from_file_location("unenforced_step_advisory", IMPL)
assert _spec is not None and _spec.loader is not None
adv = importlib.util.module_from_spec(_spec)
sys.modules["unenforced_step_advisory"] = adv
_spec.loader.exec_module(adv)
# The parse the advisory pays for lives in the shared reader, not in the hook.
_reader = sys.modules["_transcript"]


def _use(name: str, tool_input: dict) -> str:
    """One assistant record carrying a single tool_use block."""
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_1", "name": name, "input": tool_input}]}})


def _bash(command: str) -> str:
    """A Bash tool_use record."""
    return _use("Bash", {"command": command})


def _write(path: Path, lines: list[str]) -> str:
    """Write `lines` as JSONL and return the path as a string."""
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _append(path: str, lines: list[str]) -> None:
    """Append `lines` as JSONL."""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _cursors(tmp_path: Path, monkeypatch) -> list:
    """Route cursors into tmp_path, one file per (trigger, part); return the parts asked for."""
    parts: list = []

    def fake(hook, session_id, part="root"):
        """Record `part` and hand back its cursor file."""
        parts.append(part)
        return str(tmp_path / f"cursor-{part}.json")

    monkeypatch.setattr(adv, "scan_cursor_path", fake)
    return parts


def _count_parses(monkeypatch) -> list:
    """Count every line the shared reader parses from now on."""
    calls: list = []
    real = _reader._parse_line

    def counting(raw):
        """Record the raw line, then parse it."""
        calls.append(raw)
        return real(raw)

    monkeypatch.setattr(_reader, "_parse_line", counting)
    return calls


def test_second_commit_reads_only_the_delta(tmp_path, monkeypatch):
    """A review scan that settles nothing re-reads none of the old lines."""
    parts = _cursors(tmp_path, monkeypatch)
    path = _write(tmp_path / "t.jsonl", [_bash("git status")] * 30)
    facts = adv._scan_session(path, "review", "sess-1")
    assert facts is not None and not facts.review_agent and not facts.codex_review
    assert parts == ["review-root"]
    _append(path, [_use("Agent", {"subagent_type": "oh-my-claudecode:code-reviewer", "prompt": "r"})])
    calls = _count_parses(monkeypatch)
    facts = adv._scan_session(path, "review", "sess-1")
    assert facts is not None and facts.review_agent
    assert len(calls) == 1  # the appended Agent line and nothing else


def test_settled_facts_are_not_rederived(tmp_path, monkeypatch):
    """Once every wanted fact is True the next commit parses nothing."""
    _cursors(tmp_path, monkeypatch)
    path = _write(tmp_path / "t.jsonl", [_bash("gh pr list --state open")])
    assert adv._scan_session(path, "in-flight", "sess-1").open_pr_scan
    _append(path, [_bash("gh pr list --state open")] * 5)
    calls = _count_parses(monkeypatch)
    assert adv._scan_session(path, "in-flight", "sess-1").open_pr_scan
    assert calls == []


def test_triggers_do_not_share_a_cursor(tmp_path, monkeypatch):
    """A review walk skips Bash lines; the in-flight walk must still see them."""
    parts = _cursors(tmp_path, monkeypatch)
    path = _write(tmp_path / "t.jsonl", [_bash("gh pr list --state open"), _bash("git status")])
    facts = adv._scan_session(path, "review", "sess-1")
    assert facts is not None and not facts.codex_review
    facts = adv._scan_session(path, "in-flight", "sess-1")
    assert facts is not None and facts.open_pr_scan
    assert parts == ["review-root", "in-flight-root"]


def test_budget_cut_is_silent_until_caught_up(tmp_path, monkeypatch):
    """Past the per-call budget the advisory stays silent, then catches up."""
    _cursors(tmp_path, monkeypatch)
    path = _write(tmp_path / "t.jsonl", [_bash("git status")] * 20 + [_bash("gh pr list")])
    monkeypatch.setattr(adv, "_MAX_TRANSCRIPT_BYTES", Path(path).stat().st_size // 2)
    # Half a file per call, and a line cut by the budget is re-read next
    # time, so the third call is the one that catches up.
    assert adv._scan_session(path, "in-flight", "sess-1") is None
    assert adv._scan_session(path, "in-flight", "sess-1") is None
    facts = adv._scan_session(path, "in-flight", "sess-1")
    assert facts is not None and facts.open_pr_scan


def test_missing_transcript_answers_none(tmp_path, monkeypatch):
    """No file: nothing to measure against, the caller stays silent."""
    _cursors(tmp_path, monkeypatch)
    assert adv._scan_session(str(tmp_path / "absent.jsonl"), "review", "sess-1") is None


def test_subagent_not_caught_up_keeps_the_advisory_silent(tmp_path, monkeypatch):
    """The root has no PR scan; a subagent's unread tail may. Until that
    subagent is caught up the answer is None (silent), then the fact."""
    _cursors(tmp_path, monkeypatch)
    root = _write(tmp_path / "sess.jsonl", [_bash("git status")])
    sub = tmp_path / "sess" / "subagents"
    sub.mkdir(parents=True)
    agent = sub / "agent-a.jsonl"
    _write(agent, [_bash("git status")] * 20 + [_bash("gh pr list")])
    monkeypatch.setattr(adv, "_MAX_TRANSCRIPT_BYTES", agent.stat().st_size // 2)
    assert adv._scan_session(root, "in-flight", "sess-1") is None
    assert adv._scan_session(root, "in-flight", "sess-1") is None
    facts = adv._scan_session(root, "in-flight", "sess-1")
    assert facts is not None and facts.open_pr_scan
