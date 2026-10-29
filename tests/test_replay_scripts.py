"""Tests for the gate-measurement scripts ported for issue #1613.

Covers ``scripts/hermes-gate-effectiveness.py``, ``scripts/replay-clarify-corpus.py``,
``scripts/replay-merge-gate-corpus.py``, ``scripts/replay-session-merges.py`` and
the fixture generator ``tests/fixtures/merge_gate_fixtures.py``. Every input is
synthetic: Hermes ``state.db`` files built in ``tmp_path``, transcripts written
by the generator, and stand-in gates where a verdict flip is needed.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, _REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


effect = _load("scripts/hermes-gate-effectiveness.py", "hermes_gate_effectiveness")
clarify = _load("scripts/replay-clarify-corpus.py", "replay_clarify_corpus")
corpus = _load("scripts/replay-merge-gate-corpus.py", "replay_merge_gate_corpus")
session = _load("scripts/replay-session-merges.py", "replay_session_merges")
fixtures = _load("tests/fixtures/merge_gate_fixtures.py", "merge_gate_fixtures")


def _db(path: Path, rows: list[tuple]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
                 "content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, "
                 "timestamp REAL, active INTEGER DEFAULT 1)")
    now = time.time()
    conn.executemany("INSERT INTO messages (id, session_id, role, content, tool_call_id, tool_calls, "
                     "tool_name, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     [(*r, now) for r in rows])
    conn.commit()
    conn.close()
    return path


def _call(cid: str, name: str, args: dict) -> dict:
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _assistant(mid: int, sid: str, *calls: dict) -> tuple:
    return (mid, sid, "assistant", "", None, json.dumps(list(calls)), None)


def _tool(mid: int, sid: str, cid: str, name: str, content: str) -> tuple:
    return (mid, sid, "tool", content, cid, None, name)


# --- hermes-gate-effectiveness ---------------------------------------------------

def test_effectiveness_classifies_next_action(tmp_path, capsys):
    cmd = {"command": "git push origin x"}
    acked = {"command": "git push origin x # side-effect:ack"}
    blocked = json.dumps({"error": "[praxis:side-effect-scan] blocked: pushes"})
    db = _db(tmp_path / "state.db", [
        _assistant(1, "s1", _call("c1", "terminal", cmd)),
        _tool(2, "s1", "c1", "terminal", blocked),
        _assistant(3, "s1", _call("c2", "terminal", acked)),
        _tool(4, "s1", "c2", "terminal", "{\"output\": \"ok\"}"),
        _assistant(5, "s1", _call("c3", "read_file", {"path": "x"})),
        _assistant(6, "s2", _call("c4", "terminal", {"command": "ls"})),
        _tool(7, "s2", "c4", "terminal", json.dumps({"error": "[praxis self-check] confirm"})),
    ])
    out = tmp_path / "out"
    assert effect.main(["--db", str(db), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "tool calls 4 · bridge-matched 3 · interventions 2" in text
    rows = {r["sid"]: r for r in json.loads((out / "events.json").read_text())}
    assert rows["s1"]["outcome"] == "ack-only"
    assert rows["s1"]["gates"] == ["side-effect-scan"]
    assert rows["s2"]["kind"] == "self-check" and rows["s2"]["outcome"] == "abandoned"


def test_effectiveness_next_action_classes():
    later = [(9, "id", "terminal", '{"command": "git commit -m \\"fix: a\\""}')]
    assert effect.next_action('{"command": "git commit -m \\"fix: a\\""}', later) == "identical"
    assert effect.next_action('{"command": "git commit -m \\"fix: b\\""}', later) == "minor-edit"
    assert effect.next_action('{"command": "ls"}', later) == "changed"
    assert effect.classify("plain failure") is None


def test_effectiveness_reads_hermes_home(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "missing"))
    assert effect.main(["--out", str(tmp_path / "o")]) == 2
    assert "no database" in capsys.readouterr().err


# --- replay-clarify-corpus ---------------------------------------------------------

def test_clarify_replay_counts_fires_across_profiles(tmp_path, capsys):
    impl = tmp_path / "impl.py"
    impl.write_text("import json, sys\n"
                    "q = json.load(sys.stdin)['tool_input']['questions'][0]['question']\n"
                    "sys.exit(2 if 'FIRE' in q else 0)\n")
    home = tmp_path / "hermes"
    ask = {"questions": [{"question": "FIRE: merge it?", "choices": ["merge", "hold"]}]}
    _db(home / "state.db", [
        _assistant(1, "s1", _call("k1", "clarify", ask)),
        _tool(2, "s1", "k1", "clarify",
              json.dumps({"responses": [{"question": "FIRE: merge it?", "user_response": "merge"}]})),
    ])
    _db(home / "profiles" / "p1" / "state.db", [
        _assistant(1, "s9", _call("k9", "clarify", {"questions": [{"question": "quiet?"}]})),
    ])
    assert clarify.main(["--hermes-home", str(home), str(impl)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "questions=2 fired=1 (50.0%)"
    fire = json.loads(lines[1])
    assert fire["rc"] == 2 and fire["labels"] == ["merge", "hold"] and fire["answer"] == "merge"


def test_clarify_replay_missing_impl(tmp_path):
    assert clarify.main([str(tmp_path / "nope.py")]) == 2


# --- fixture generator -------------------------------------------------------------

def _example(tmp_path: Path, capsys=None) -> Path:
    assert fixtures.main(["--out", str(tmp_path)]) == 0
    if capsys is not None:
        capsys.readouterr()  # drop the generator's own output
    path = tmp_path / "momentum-merge-example.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    assert rows[-2]["message"]["content"][0]["input"]["command"].startswith("gh pr merge 833")
    return path


def test_fixture_generator_writes_jsonl(tmp_path):
    _example(tmp_path)


# --- merge-gate replays ------------------------------------------------------------

_STUB = """
def _merge_escalation_reason(payload):
    return {verdict!r}
def bulk_merge_approved(payload):
    return False
def _ask_question_text(block):
    return block["input"]["questions"][0]["question"]
def _briefing_item_count(text):
    return text.count("- ")
"""


def _stub_root(root: Path, verdict: str | None) -> Path:
    impl = root / corpus.REL
    impl.parent.mkdir(parents=True)
    impl.write_text(_STUB.format(verdict=verdict))
    return root


def test_corpus_replay_reports_flip(tmp_path, capsys):
    tr = _example(tmp_path / "fx", capsys)
    base = _stub_root(tmp_path / "base", "h\nh\nDENY: no briefing")
    branch = _stub_root(tmp_path / "branch", None)
    assert corpus.main([str(branch), "--base-root", str(base), "--corpus", str(tr)]) == 0
    out = capsys.readouterr().out.splitlines()
    stats = json.loads(out[0])
    assert stats == {"transcripts": 1, "calls": 1, "same": 0, "deny_to_allow": 1,
                     "allow_to_deny": 0, "errors": 0}
    assert out[2].startswith("deny_to_allow momentum-merge-example.jsonl")
    assert out[3].startswith("question_items 10 | Q: Merge briefing")


def test_corpus_replay_real_gate_same_both_sides(tmp_path, capsys):
    tr = _example(tmp_path, capsys)
    assert corpus.main([str(_REPO), "--base-root", str(_REPO), "--corpus", str(tr)]) == 0
    stats = json.loads(capsys.readouterr().out.splitlines()[0])
    assert stats["calls"] == 1 and stats["same"] == 1 and stats["errors"] == 0


def test_corpus_replay_cut(tmp_path, capsys):
    tr = _example(tmp_path, capsys)
    assert corpus.main([str(_REPO), "--cut", str(tr), "9", "833"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [x.split()[0] for x in out] == ["plain", "marker"]


def test_session_replay_from_transcript(tmp_path, capsys):
    tr = _example(tmp_path, capsys)
    assert session.main([str(_REPO), str(tr)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1 and out[0].startswith("row    9 bulk=")
    assert out[0].endswith("| gh pr merge 833 --repo a/x --squash")


def test_session_replay_from_state_db(tmp_path, capsys):
    db = _db(tmp_path / "state.db", [
        (1, "s1", "user", "merge it", None, None, None),
        _assistant(2, "s1", _call("m1", "terminal", {"command": "gh pr merge 7 --repo a/x"})),
        _tool(3, "s1", "m1", "terminal", "{\"output\": \"\"}"),
    ])
    out_dir = tmp_path / "keep"
    assert session.main([str(_REPO), "--session", "s1", "--db", str(db), "--out", str(out_dir)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"transcript -> {out_dir / 'session.jsonl'}"
    assert out[1].startswith("row ") and "gh pr merge 7 --repo a/x" in out[1]


def test_session_replay_unknown_session(tmp_path):
    db = _db(tmp_path / "state.db", [])
    assert session.main([str(_REPO), "--session", "nope", "--db", str(db), "--out", str(tmp_path)]) == 2


def test_session_replay_needs_one_source():
    with pytest.raises(SystemExit):
        session.main([str(_REPO)])


# --- review follow-ups ----------------------------------------------------------

def test_effectiveness_events_file_is_owner_only(tmp_path, capsys):
    db = _db(tmp_path / "state.db", [_assistant(1, "s1", _call("c1", "terminal", {"command": "ls"}))])
    out = tmp_path / "new" / "out"
    assert effect.main(["--db", str(db), "--out", str(out)]) == 0
    assert (out.stat().st_mode & 0o777) == 0o700
    assert ((out / "events.json").stat().st_mode & 0o777) == 0o600
    (out / "events.json").chmod(0o644)  # a file left by an older run is tightened
    assert effect.main(["--db", str(db), "--out", str(out)]) == 0
    assert ((out / "events.json").stat().st_mode & 0o777) == 0o600
    assert json.loads((out / "events.json").read_text()) == []


def test_clarify_replay_counts_timeout_and_goes_on(tmp_path, monkeypatch, capsys):
    impl = tmp_path / "impl.py"
    impl.write_text("import sys\nsys.exit(2)\n")
    home = tmp_path / "hermes"
    _db(home / "state.db", [
        _assistant(1, "s1", _call("k1", "clarify", {"questions": [{"question": "SLOW?"}]})),
        _assistant(2, "s1", _call("k2", "clarify", {"questions": [{"question": "fast?"}]})),
    ])
    real_run = clarify.subprocess.run

    def run(cmd, **kw):
        if "SLOW" in kw["input"]:
            raise clarify.subprocess.TimeoutExpired(cmd, kw["timeout"])
        return real_run(cmd, **kw)

    monkeypatch.setattr(clarify.subprocess, "run", run)
    assert clarify.main(["--hermes-home", str(home), str(impl)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "questions=2 fired=1 (50.0%) timeouts=1"
    assert json.loads(lines[1])["q"] == "fast?"


def _hermes_row(*blocks: dict) -> str:
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}})


def _bash_block(tid: str, cmd: str) -> dict:
    return {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd}}


def _result_row(tid: str) -> str:
    return json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}})


def _hermes_lines() -> list[str]:
    """One Hermes message: briefing prose, a probe, the merge, a later call."""
    return [
        json.dumps({"type": "user", "message": {"role": "user", "content": "merge it"}}),
        _hermes_row({"type": "text", "text": "BRIEFED"}, _bash_block("c1", "gh pr checks 5"),
                    _bash_block("m1", "gh pr merge 5 --squash"), _bash_block("c2", "ls")),
        _result_row("c1"), _result_row("m1"), _result_row("c2"),
    ]


@pytest.mark.parametrize("mod", [corpus, session])
def test_cut_keeps_earlier_blocks_of_a_hermes_message(mod):
    lines = _hermes_lines()
    cut = mod.cut_before(lines, 1, 2)
    assert cut[0] == lines[0] and cut[2] == lines[2] and len(cut) == 3
    kept = json.loads(cut[1])["message"]["content"]
    assert [b.get("id") or b["type"] for b in kept] == ["text", "c1"]
    assert mod.cut_before(lines, 1, 0) == lines[:1]  # one-block (Claude Code) row


_READS_CUT = """
def _merge_escalation_reason(payload):
    return None if "BRIEFED" in open(payload["transcript_path"]).read() else "h\\nh\\nDENY"
def bulk_merge_approved(payload):
    return False
def _ask_question_text(block):
    return ""
def _briefing_item_count(text):
    return 0
"""


def test_corpus_replay_sees_prose_of_the_merge_message(tmp_path, capsys):
    tr = tmp_path / "hermes.jsonl"
    tr.write_text("\n".join(_hermes_lines()) + "\n")
    base = _stub_root(tmp_path / "base", "h\nh\nDENY")
    branch = tmp_path / "branch"
    (branch / corpus.REL).parent.mkdir(parents=True)
    (branch / corpus.REL).write_text(_READS_CUT)
    assert corpus.main([str(branch), "--base-root", str(base), "--corpus", str(tr)]) == 0
    stats = json.loads(capsys.readouterr().out.splitlines()[0])
    assert stats["calls"] == 1 and stats["deny_to_allow"] == 1


def test_session_replay_keeps_prefixed_merge_unchanged(tmp_path, capsys):
    root = tmp_path / "root"
    (root / session.REL).parent.mkdir(parents=True)
    (root / session.REL).write_text(_READS_CUT)
    tr = tmp_path / "t.jsonl"
    lines = _hermes_lines()
    lines[1] = _hermes_row({"type": "text", "text": "BRIEFED"},
                           _bash_block("m1", "cd repo && gh pr merge 5 --squash"))
    tr.write_text("\n".join(lines[:2]) + "\n")
    assert session.main([str(root), str(tr)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1 and "ALLOW" in out[0] and out[0].endswith("| cd repo && gh pr merge 5 --squash")
