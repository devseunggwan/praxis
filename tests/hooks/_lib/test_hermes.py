"""Tests for the Hermes Agent host adapter (`hooks/_lib/_hermes.py`, `plugins/hermes/bridge.py`).

The adapter is the only thing between a Hermes tool call and every praxis gate
registered for the `hermes` host, so each translation it owns is pinned here:

  - Hermes tool -> Claude tool name and input shape (a wrong name silently
    reaches no gate);
  - Hermes `state.db` rows -> Claude transcript events, including the
    `"<question>"="<answer>"` shape praxis reads approvals from;
  - praxis decisions -> Hermes responses (deny, ask, rewrite, advisory);
  - the manifest's `hosts` filter: a gate excluded from `hermes` must not run
    there while it still runs for `claude`.

Run: python3 -m pytest tests/hooks/_lib/test_hermes.py -q
"""
from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB_DIR = REPO_ROOT / "hooks" / "_lib"
BRIDGE = REPO_ROOT / "plugins" / "hermes" / "bridge.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


hermes = _load("_hermes", LIB_DIR / "_hermes.py")


# --- tool translation ----------------------------------------------------------

def test_terminal_maps_to_bash_command():
    assert hermes.claude_calls("terminal", {"command": "ls", "background": True}) == [
        ("Bash", {"command": "ls", "description": "", "run_in_background": True})]


def test_file_tools_map_path_to_file_path():
    assert hermes.claude_calls("write_file", {"path": "/a", "content": "x"}) == [
        ("Write", {"file_path": "/a", "content": "x"})]
    assert hermes.claude_calls("patch", {"path": "/a", "old_string": "o", "new_string": "n"}) == [
        ("Edit", {"file_path": "/a", "old_string": "o", "new_string": "n", "replace_all": False})]
    assert hermes.claude_calls("read_file", {"path": "/a", "offset": 3}) == [
        ("Read", {"file_path": "/a", "offset": 3})]


def test_clarify_choices_become_option_labels():
    [(name, tool_input)] = hermes.claude_calls("clarify", {"questions": [
        {"question": "Merge PR #1?", "choices": ["승인 — 머지", "보류"], "multi_select": False}]})
    assert name == "AskUserQuestion"
    [q] = tool_input["questions"]
    assert q["question"] == "Merge PR #1?"
    assert [o["label"] for o in q["options"]] == ["승인 — 머지", "보류"]
    assert q["multiSelect"] is False


def test_skill_view_invokes_skill_only_without_file_path():
    assert hermes.claude_calls("skill_view", {"name": "merge-briefing"}) == [
        ("Skill", {"skill": "merge-briefing", "args": ""})]
    assert hermes.claude_calls("skill_view", {"name": "x", "file_path": "references/a.md"}) == []


def test_delegate_task_spawn_is_one_agent_call_per_task():
    calls = hermes.claude_calls("delegate_task", {"tasks": [{"goal": "a"}, {"goal": "b", "context": "c"}]})
    assert [name for name, _ in calls] == ["Agent", "Agent"]
    assert calls[1][1]["prompt"] == "b\n\nc"
    assert hermes.claude_calls("delegate_task", {"action": "list"}) == []


def test_mcp_passes_through_and_unknown_tools_reach_no_gate():
    assert hermes.claude_calls("mcp__s__t", {"q": 1}) == [("mcp__s__t", {"q": 1})]
    assert hermes.claude_calls("process_manage", {"action": "list"}) == []
    assert hermes.claude_calls("terminal", None) == [("Bash", {"command": "", "description": ""})]


# --- transcript ----------------------------------------------------------------

def _db(path: Path, rows: list[tuple]) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
                 "content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, active INTEGER DEFAULT 1)")
    conn.executemany("INSERT INTO messages (id, session_id, role, content, tool_call_id, tool_calls, tool_name) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return path


def _call(call_id: str, name: str, args: dict) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _clarify_result(question: str, answer: str) -> str:
    return json.dumps({"responses": [{"question": question, "status": "answered",
                                      "user_response": answer}], "outcome": "submitted"})


ROWS = [
    (1, "s1", "user", "merge it", None, None, None),
    (2, "s1", "assistant", "PR #7 briefing. Approve merge?",
     None, json.dumps([_call("c1", "clarify", {"questions": [{"question": "PR #7 를 머지할까요?",
                                                             "choices": ["승인 — 머지", "보류"]}]})]), None),
    (3, "s1", "tool", _clarify_result("PR #7 를 머지할까요?", "승인 — 머지"), "c1", None, "clarify"),
    (4, "s1", "assistant", "", None, json.dumps([_call("c2", "process_manage", {"action": "list"}),
                                                  _call("c3", "terminal", {"command": "gh pr merge 7"})]), None),
    (5, "s1", "tool", "{}", "c2", None, "process_manage"),
    (6, "other", "user", "unrelated", None, None, None),
]


def test_transcript_events_render_claude_shapes(tmp_path):
    out = hermes.sync_transcript("s1", tmp_path / "t", _db(tmp_path / "state.db", ROWS))
    events = [json.loads(line) for line in out.read_text().splitlines()]
    assert [e["type"] for e in events] == ["user", "assistant", "user", "assistant"]
    text, ask = events[1]["message"]["content"]
    assert text == {"type": "text", "text": "PR #7 briefing. Approve merge?"}
    assert ask["name"] == "AskUserQuestion" and ask["id"] == "c1"
    [result] = events[2]["message"]["content"]
    assert result["tool_use_id"] == "c1" and "is_error" not in result
    # The pair praxis's merge gate parses (`_ASK_ANSWER_RE` in momentum-rule-retrieval-gate).
    pairs = re.findall(r'"((?:[^"\\]|\\.)*)"=\s*"((?:[^"\\]|\\.)*)"', result["content"])
    assert pairs == [("PR #7 를 머지할까요?", "승인 — 머지")]
    # The unmapped process_manage call and its result are dropped; terminal stays.
    [bash] = events[3]["message"]["content"]
    assert bash["name"] == "Bash" and bash["input"]["command"] == "gh pr merge 7"


def test_unanswered_clarify_is_an_error_result(tmp_path):
    rows = ROWS[:2] + [(3, "s1", "tool", json.dumps({"responses": [{"question": "q", "status": "unanswered",
                                                                     "user_response": None}]}), "c1", None, "clarify")]
    out = hermes.sync_transcript("s1", tmp_path / "t", _db(tmp_path / "state.db", rows))
    result = json.loads(out.read_text().splitlines()[2])["message"]["content"][0]
    assert result["is_error"] is True


def test_sync_rebuilds_from_active_rows(tmp_path):
    db = _db(tmp_path / "state.db", ROWS[:3])
    out = hermes.sync_transcript("s1", tmp_path / "t", db)
    assert len(out.read_text().splitlines()) == 3
    assert hermes.sync_transcript("s1", tmp_path / "t", db) == out
    assert len(out.read_text().splitlines()) == 3  # a re-sync never duplicates events
    _db(db, ROWS[3:5])
    hermes.sync_transcript("s1", tmp_path / "t", db)
    lines = out.read_text().splitlines()
    assert len(lines) == 4 and json.loads(lines[3])["message"]["content"][0]["name"] == "Bash"
    # A row Hermes deactivates (rewind, edit) leaves the transcript on the next sync.
    conn = sqlite3.connect(db)
    conn.execute("UPDATE messages SET active = 0 WHERE id = 4")
    conn.commit()
    conn.close()
    hermes.sync_transcript("s1", tmp_path / "t", db)
    assert "gh pr merge 7" not in out.read_text()
    assert [p.name for p in (tmp_path / "t").iterdir()] == [out.name]  # no temp file left


def test_sync_fails_open_without_a_usable_database(tmp_path):
    assert hermes.sync_transcript("s1", tmp_path / "t", tmp_path / "missing.db") is None
    assert hermes.sync_transcript("", tmp_path / "t", _db(tmp_path / "state.db", ROWS)) is None
    bad = tmp_path / "bad.db"
    sqlite3.connect(bad).execute("CREATE TABLE messages (id INTEGER)").connection.commit()
    assert hermes.sync_transcript("s1", tmp_path / "t", bad) is None


@pytest.mark.parametrize("content, failed", [
    (json.dumps({"error": "[praxis:merge-gate] blocked"}), True),        # a gate block: no exit code
    (json.dumps({"output": "", "exit_code": 1, "error": None}), True),   # ran and failed
    (json.dumps({"output": "merged", "exit_code": 0, "error": None}), False),
    (json.dumps({"output": "", "exit_code": True}), False),               # a bool is not an exit status
    ("not json", False),
])
def test_terminal_failure_is_an_error_result(tmp_path, content, failed):
    rows = [(1, "s1", "user", "merge", None, None, None),
            (2, "s1", "assistant", "", None, json.dumps([_call("c1", "terminal", {"command": "gh pr merge 7"})]), None),
            (3, "s1", "tool", content, "c1", None, "terminal")]
    out = hermes.sync_transcript("s1", tmp_path / "t", _db(tmp_path / "state.db", rows))
    result = json.loads(out.read_text().splitlines()[2])["message"]["content"][0]
    assert result.get("is_error", False) is failed


def test_queued_prompt_is_not_a_user_message_yet(tmp_path):
    db = _db(tmp_path / "state.db", ROWS[:3])
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE messages ADD COLUMN display_metadata TEXT")
    conn.execute("INSERT INTO messages (id, session_id, role, content, display_metadata) "
                 "VALUES (4, 's1', 'user', ']', '{\"_queued_prompt\": true}')")
    conn.execute("INSERT INTO messages (id, session_id, role, content, display_metadata) "
                 "VALUES (5, 's1', 'user', 'later', 'not json')")
    conn.commit()
    conn.close()
    out = hermes.sync_transcript("s1", tmp_path / "t", db)
    users = [e["message"]["content"] for e in map(json.loads, out.read_text().splitlines())
             if e["type"] == "user" and isinstance(e["message"]["content"], str)]
    assert users == ["merge it", "later"]  # the undelivered queue row is skipped; bad metadata is not


def test_queued_filter_needs_no_sqlite_json_functions(tmp_path, monkeypatch):
    # An SQLite built without JSON functions: any SQL-side json_* call raises,
    # which sync_transcript would swallow into an empty (fail-open) transcript.
    real_connect = sqlite3.connect

    def no_json(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        for name in ("json_extract", "json_valid"):
            conn.create_function(name, -1, lambda *_: 1 / 0)
        return conn

    db = _db(tmp_path / "state.db", ROWS[:1])
    conn = real_connect(db)
    conn.execute("ALTER TABLE messages ADD COLUMN display_metadata TEXT")
    conn.execute("INSERT INTO messages (id, session_id, role, content, display_metadata) "
                 "VALUES (2, 's1', 'user', ']', '{\"_queued_prompt\": true}')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(hermes.sqlite3, "connect", no_json)
    out = hermes.sync_transcript("s1", tmp_path / "t", db)
    assert out is not None
    assert [json.loads(line)["message"]["content"] for line in out.read_text().splitlines()] == ["merge it"]


_SKILL_HEAD = ('[IMPORTANT: The user has invoked the "worktree-merge-cleanup" skill, indicating they want you '
               'to follow its instructions. The full skill content is loaded below.]\n\n')
_SKILL_BODY = ("# worktree-merge-cleanup\n\nExample: The user has provided the following instruction alongside "
               "the skill invocation: quoted-in-body\n\n[Skill directory: /x]\nResolve any relative paths.")
_SKILL_SAYS = "\n\nThe user has provided the following instruction alongside the skill invocation: "


@pytest.mark.parametrize("content, typed", [
    (_SKILL_HEAD + _SKILL_BODY + _SKILL_SAYS + "merge", "merge"),  # last marker wins over one quoted in the body
    (_SKILL_HEAD + _SKILL_BODY + _SKILL_SAYS + "merge\n\n[Runtime note: x]", "merge"),
    (_SKILL_HEAD + "# worktree-merge-cleanup\n\n[Skill directory: /x]", "/worktree-merge-cleanup"),  # bare invocation
    ('[IMPORTANT: The user has invoked the "/clean /work" skill bundle, loading 2 skills together. Treat every '
     'skill below as active guidance for this turn.]\n\nSkills loaded: clean, work\n\nUser instruction: ship it'
     '\n\n[Loaded as part of the "/clean /work" bundle]\nbody\n\nUser instruction: quoted', "ship it"),
])
def test_skill_invocation_splits_into_meta_body_and_typed_text(tmp_path, content, typed):
    rows = [(1, "s1", "user", content, None, None, None)]
    out = hermes.sync_transcript("s1", tmp_path / "t", _db(tmp_path / "state.db", rows))
    meta, user = [json.loads(line) for line in out.read_text().splitlines()]
    assert meta["isMeta"] is True and meta["message"]["content"] == content
    assert "isMeta" not in user and user["message"]["content"] == typed


def test_plain_user_message_is_not_split(tmp_path):
    out = hermes.sync_transcript("s1", tmp_path / "t", _db(tmp_path / "state.db", ROWS[:1]))
    [event] = [json.loads(line) for line in out.read_text().splitlines()]
    assert "isMeta" not in event and event["message"]["content"] == "merge it"


# --- decisions -----------------------------------------------------------------

def test_parse_decision_and_stable_approval_key():
    out = json.dumps({"hookSpecificOutput": {"permissionDecision": "ask", "permissionDecisionReason": "why",
                                             "updatedInput": {"command": "x"}}})
    assert hermes.parse_decision(out) == ("ask", "why", "", {"command": "x"})
    assert hermes.parse_decision("not json") == ("", "", "", {})
    assert hermes.approve_response(["a"]) == hermes.approve_response(["a"])
    assert hermes.approve_response(["a"])["rule_key"] != hermes.approve_response(["b"])["rule_key"]


def test_bridge_turns_a_bash_rewrite_into_modify(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    bridge = _load("hermes_bridge", BRIDGE)
    rewrite = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "updatedInput": {"command": "ls -a"}}})
    monkeypatch.setattr(bridge, "_groups", lambda event, tool: ["Bash"])
    monkeypatch.setattr(bridge, "_run", lambda event, matcher, payload: (0, rewrite, ""))
    response = bridge.pre_tool_call({"tool_name": "terminal", "tool_input": {"command": "ls"},
                                     "cwd": str(tmp_path), "session_id": ""})
    assert response == {"action": "modify", "args": {"command": "ls -a"}}


# --- end to end through the real manifest --------------------------------------

def _bridge(payload: dict, tmp_path: Path) -> dict:
    env = {"HERMES_HOME": str(tmp_path), "PRAXIS_HOME": str(tmp_path / "praxis"),
           "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(Path.home())}
    proc = subprocess.run([sys.executable, str(BRIDGE)], input=json.dumps(payload), capture_output=True,
                          text=True, timeout=60, env=env, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _terminal(command: str, tmp_path: Path) -> dict:
    return {"hook_event_name": "pre_tool_call", "tool_name": "terminal", "tool_input": {"command": command},
            "session_id": "", "cwd": str(tmp_path), "extra": {"tool_call_id": "t"}}


def test_deny_becomes_block(tmp_path):
    response = _bridge(_terminal("gh search issues foo --state all", tmp_path), tmp_path)
    assert response["action"] == "block" and "--state" in response["message"]


def test_ask_becomes_human_approval(tmp_path):
    response = _bridge(_terminal("git push origin main", tmp_path), tmp_path)
    assert response["action"] == "approve" and response["rule_key"].startswith("praxis:")


def test_advisory_becomes_context(tmp_path):
    response = _bridge(_terminal("rm -rf /", tmp_path), tmp_path)
    assert set(response) == {"context"} and "rm -rf" in response["context"]


POLL_LOOP = "for i in 1 2 3; do sleep 50; done"


def test_gate_excluded_from_hermes_does_not_run(tmp_path):
    assert _bridge(_terminal(POLL_LOOP, tmp_path), tmp_path) == {}


def test_excluded_gate_still_runs_for_claude(tmp_path):
    """Positive control for the test above: the same command is blocked on `claude`."""
    payload = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "",
                          "tool_input": {"command": POLL_LOOP}, "cwd": str(tmp_path), "transcript_path": ""})
    proc = subprocess.run([sys.executable, str(LIB_DIR / "_dispatch.py"), "PreToolUse", "Bash", "claude"],
                          input=payload, capture_output=True, text=True, timeout=60,
                          env={"PRAXIS_HOME": str(tmp_path / "praxis"), "PATH": "/usr/bin:/bin",
                               "HOME": str(Path.home())})
    assert proc.returncode == 2 or '"deny"' in proc.stdout


def test_unmapped_tool_and_unknown_event_are_no_ops(tmp_path):
    assert _bridge({"hook_event_name": "pre_tool_call", "tool_name": "process_manage",
                    "tool_input": {}, "session_id": ""}, tmp_path) == {}
    assert _bridge({"hook_event_name": "post_tool_call", "tool_name": "terminal"}, tmp_path) == {}


def test_print_config_points_at_this_clone():
    proc = subprocess.run([sys.executable, str(BRIDGE), "--print-config"], capture_output=True, text=True,
                          timeout=30)
    assert proc.returncode == 0
    assert "pre_tool_call:" in proc.stdout and "pre_llm_call:" in proc.stdout
    matcher = re.search(r"matcher: '([^']+)'", proc.stdout).group(1)
    for tool in ("terminal", "write_file", "patch", "clarify", "mcp__s__t"):
        assert re.fullmatch(matcher, tool), tool
    assert not re.fullmatch(matcher, "process_manage")


@pytest.mark.parametrize("name", ["zsh-dialect-advisory", "foreground-poll-loop-guard",
                                  "long-foreground-call-advisory"])
def test_bash_dialect_and_ceiling_hooks_exclude_hermes(name):
    manifest = json.loads((REPO_ROOT / "hooks" / "manifest.json").read_text(encoding="utf-8"))
    [hook] = [h for h in manifest["hooks"] if h["name"] == name]
    assert "hermes" not in hook["hosts"] and "claude" in hook["hosts"]
