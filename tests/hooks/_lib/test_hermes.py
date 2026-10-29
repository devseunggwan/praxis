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
               "the skill invocation: quoted-in-body\n\n[Skill directory: /x]\nResolve any relative paths in "
               "this skill against that directory, then run them with the terminal tool using the absolute path.")
_SKILL_SAYS = "\n\nThe user has provided the following instruction alongside the skill invocation: "
_BUNDLE_HEAD = ('[IMPORTANT: The user has invoked the "/clean /work" skill bundle, loading 2 skills together. Treat '
                'every skill below as active guidance for this turn.]\n\nSkills loaded: clean, work')
_BUNDLE_SKILLS = '\n\n[Loaded as part of the "/clean /work" bundle]\nbody\n\nUser instruction: quoted'


@pytest.mark.parametrize("content, typed", [
    (_SKILL_HEAD + _SKILL_BODY + _SKILL_SAYS + "merge", "merge"),  # a marker quoted in the body is skipped
    (_SKILL_HEAD + _SKILL_BODY + _SKILL_SAYS + "merge\n\n[Runtime note: x]", "merge"),
    (_SKILL_HEAD + _SKILL_BODY, "/worktree-merge-cleanup"),  # bare, body still quotes the marker
    # the user's text quotes the footer sentence; the directory line still anchors the marker
    (_SKILL_HEAD + _SKILL_BODY + _SKILL_SAYS + "why: then run them with the terminal tool using the absolute path.",
     "why: then run them with the terminal tool using the absolute path."),
    # no skill directory, so no anchor: the last marker is taken, as Hermes's extractor does
    (_SKILL_HEAD + "# s" + _SKILL_SAYS + "quoted-in-body", "quoted-in-body"),
    (_BUNDLE_HEAD + "\n\nUser instruction: ship it" + _BUNDLE_SKILLS, "ship it"),
    (_BUNDLE_HEAD + _BUNDLE_SKILLS, "/clean /work"),  # bare bundle, a skill body quotes the marker
])
def test_skill_invocation_splits_into_meta_body_and_typed_text(tmp_path, content, typed):
    rows = [(1, "s1", "user", content, None, None, None)]
    out = hermes.sync_transcript("s1", tmp_path / "t", _db(tmp_path / "state.db", rows))
    meta, user = [json.loads(line) for line in out.read_text().splitlines()]
    assert meta["isMeta"] is True and meta["message"]["content"] == content
    assert "isMeta" not in user and user["message"]["content"] == typed


@pytest.mark.parametrize("content", [
    "merge it",
    '[IMPORTANT: The user has invoked the "x" skill in my notes, see below]\nmerge',  # starts alike, no scaffold
])
def test_plain_user_message_is_not_split(tmp_path, content):
    rows = [(1, "s1", "user", content, None, None, None)]
    out = hermes.sync_transcript("s1", tmp_path / "t", _db(tmp_path / "state.db", rows))
    [event] = [json.loads(line) for line in out.read_text().splitlines()]
    assert "isMeta" not in event and event["message"]["content"] == content


# --- decisions -----------------------------------------------------------------

def test_parse_decision_and_stable_approval_key():
    out = json.dumps({"hookSpecificOutput": {"permissionDecision": "ask", "permissionDecisionReason": "why",
                                             "updatedInput": {"command": "x"}}})
    assert hermes.parse_decision(out) == ("ask", "why", "", {"command": "x"})
    assert hermes.parse_decision("not json") == ("", "", "", {})
    assert hermes.approve_response([("", "a")]) == hermes.approve_response([("", "a")])
    assert hermes.approve_response([("", "a")])["rule_key"] != hermes.approve_response([("", "b")])["rule_key"]


def test_approval_key_is_the_gate_and_category_not_the_call():
    """The same gate and category share one "always" key across calls."""
    title = "Commit title too long: {n} chars (max 50). Title: '{t}'"
    a = hermes.approval_rule_key([("commit-title-length-check", title.format(n=52, t="x" * 52))])
    b = hermes.approval_rule_key([("commit-title-length-check", title.format(n=71, t="y" * 71))])
    assert a == b == "praxis:commit-title-length-check"
    push = "[git-push] remote trigger (git push) — target. If this run is intended, re-invoke."
    assert hermes.approval_rule_key([("side-effect-scan", push)]) == "praxis:side-effect-scan[git-push]"


def test_approval_key_separates_categories_and_prod():
    push = hermes.approval_rule_key([("side-effect-scan", "[git-push] remote trigger.")])
    merge = hermes.approval_rule_key([("side-effect-scan", "[gh-merge] remote trigger via gh.")])
    prod = hermes.approval_rule_key([("side-effect-scan", "[prod] PROD scope — [git-push] remote trigger.")])
    assert len({push, merge, prod}) == 3
    both = hermes.approval_rule_key([("side-effect-scan", "[git-push] a. [gh-merge] b."),
                                     ("commit-title-length-check", "too long")])
    assert both == "praxis:commit-title-length-check+side-effect-scan[gh-merge][git-push]"


PER_CALL_GATES = [
    "approval-premise-reread-gate", "cross-boundary-preflight", "destructive-bash-guard",
    "fan-out-scope-gate", "pre-merge-approval-gate", "rejected-mutation-reconsent-gate",
    "session-intent", "a-gate-added-later",
]

ADVISORY_GROUPED_GATES = [
    "cwd-relative-exec-advisory", "menu-mutation-tier-advisory",
    "output-block-falsify-advisory",
]


@pytest.mark.parametrize("hook", ADVISORY_GROUPED_GATES)
def test_advisory_nudges_share_one_key_across_calls(hook):
    """An advisory nudge asks about the call's shape, not for consent, so one
    "always" retires it instead of prompting again on every call."""
    first = hermes.approval_rule_key([(hook, f"[{hook}] nudge about call one")], {"command": "./a.sh"})
    second = hermes.approval_rule_key([(hook, f"[{hook}] nudge about call two")], {"command": "./b.sh"})
    assert first == second == f"praxis:{hook}[{hook}]"
    # Behind a per-call gate the key is still per-call.
    behind = [(hook, "nudge"), ("session-intent", "same reason")]
    assert hermes.approval_rule_key(behind, {"command": "a"}) != hermes.approval_rule_key(behind, {"command": "b"})


@pytest.mark.parametrize("hook", PER_CALL_GATES)
def test_per_call_gates_keep_a_call_specific_key(hook):
    """An "always" on a merge or external write must not cover the next one,
    even when the gate's reason is the same text both times."""
    first = hermes.approval_rule_key([(hook, "same reason")], {"command": "gh pr merge 1"})
    second = hermes.approval_rule_key([(hook, "same reason")], {"command": "gh pr merge 2"})
    assert first != second
    # A per-call gate behind another gate's ask still makes the key per-call.
    behind = [("commit-title-length-check", "too long"), (hook, "same reason")]
    assert hermes.approval_rule_key(behind, {"command": "a"}) != hermes.approval_rule_key(behind, {"command": "b"})


def test_per_call_reason_leads_the_message():
    asks = [("commit-title-length-check", "x" * 1600), ("destructive-bash-guard", "rm -rf")]
    assert hermes.approve_response(asks)["message"].startswith("[praxis] [praxis:destructive-bash-guard] rm -rf")


def test_unattributed_ask_keeps_a_call_specific_key():
    assert hermes.approval_rule_key([("", "why 1")]) != hermes.approval_rule_key([("", "why 2")])


def test_approval_message_names_the_gate():
    response = hermes.approve_response([("side-effect-scan", "[git-push] remote trigger.")])
    assert response["message"] == "[praxis] [praxis:side-effect-scan] [git-push] remote trigger."


def test_bridge_turns_a_bash_rewrite_into_modify(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    bridge = _load("hermes_bridge", BRIDGE)
    rewrite = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "updatedInput": {"command": "ls -a"}}})
    monkeypatch.setattr(bridge, "_groups", lambda event, tool: ["Bash"])
    monkeypatch.setattr(bridge, "_run", lambda event, matcher, payload: (0, rewrite, "", ""))
    response = bridge.pre_tool_call({"tool_name": "terminal", "tool_input": {"command": "ls"},
                                     "cwd": str(tmp_path), "session_id": ""})
    assert response == {"action": "modify", "args": {"command": "ls -a"}}


# --- end to end through the real manifest --------------------------------------

def _bridge(payload: dict, tmp_path: Path, extra_env: dict[str, str] | None = None) -> dict:
    env = {"HERMES_HOME": str(tmp_path), "PRAXIS_HOME": str(tmp_path / "praxis"),
           "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(Path.home()),
           **(extra_env or {})}
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


def test_ask_becomes_an_agent_self_check_not_a_human_approval(tmp_path):
    """A praxis ask sends the call back to the agent; re-issuing the same call runs it."""
    first = _bridge(_terminal("git push origin main", tmp_path), tmp_path)
    assert first["action"] == "block"
    assert first["message"].startswith("[praxis self-check]")
    assert "[praxis:side-effect-scan]" in first["message"]
    again = _bridge(_terminal("git push origin main", tmp_path), tmp_path)
    assert again.get("action") is None
    # The acknowledgement is consumed: a third identical call is checked again.
    assert _bridge(_terminal("git push origin main", tmp_path), tmp_path)["action"] == "block"


def test_self_check_is_per_call(tmp_path):
    """Re-checking one call does not let a different call through."""
    assert _bridge(_terminal("git push origin main", tmp_path), tmp_path)["action"] == "block"
    other = _bridge(_terminal("git push origin issue-1-fix-other", tmp_path), tmp_path)
    assert other["action"] == "block"


def test_self_check_lists_every_gate_that_asked(tmp_path):
    """commit-title-length-check runs before destructive-bash-guard; both asks reach the agent."""
    strict = {"PRAXIS_DESTRUCTIVE_BASH_STRICT": "1"}
    title = "fix: an intentionally long commit title well over fifty characters"
    first = _bridge(_terminal(f'git commit -m "{title}" && rm -rf ~/a', tmp_path), tmp_path, strict)
    assert first["action"] == "block"
    assert "[praxis:destructive-bash-guard]" in first["message"]
    assert "[praxis:commit-title-length-check]" in first["message"]


def test_merge_consent_still_asks_the_human(tmp_path):
    """pre-merge-approval-gate is consent, not a self-check: it stays a Hermes approval,
    and re-issuing the call does not get past it."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    payload = _terminal("gh pr merge 11 --squash # briefing-surfaced: test # side-effect:ack", tmp_path)
    first = _bridge(payload, tmp_path)
    assert first["action"] == "approve", first
    assert "[praxis:pre-merge-approval-gate]" in first["message"]
    assert _bridge(payload, tmp_path)["action"] == "approve"


def _delegate(call_id: str, goals: list[str]) -> dict:
    return _call(call_id, "delegate_task", {"tasks": [{"goal": g} for g in goals]})


def _delegate_payload(call_id: str, goals: list[str], tmp_path: Path) -> dict:
    return {"hook_event_name": "pre_tool_call", "tool_name": "delegate_task", "session_id": "s1",
            "tool_input": {"tasks": [{"goal": g} for g in goals]}, "cwd": str(tmp_path),
            "extra": {"tool_call_id": call_id}}


def _fan_out_asked(response: dict) -> bool:
    return "[praxis:fan-out-scope-gate]" in str(response.get("message", ""))


def test_first_delegation_is_not_its_own_prior_target(tmp_path):
    """Hermes stores the in-flight call before the hook runs, so the rebuilt
    transcript already holds it; fan-out-scope-gate must not count it."""
    _db(tmp_path / "state.db", [
        (1, "s1", "user", "verify this one PR", None, None, None),
        (2, "s1", "assistant", "", None, json.dumps([_delegate("d1", ["verify"])]), None),
    ])
    response = _bridge(_delegate_payload("d1", ["verify"], tmp_path), tmp_path)
    assert not _fan_out_asked(response), response


def test_second_delegation_in_a_turn_still_asks(tmp_path):
    _db(tmp_path / "state.db", [
        (1, "s1", "user", "verify this one PR", None, None, None),
        (2, "s1", "assistant", "", None, json.dumps([_delegate("d1", ["verify"])]), None),
        (3, "s1", "tool", json.dumps({"results": [{"summary": "ok"}]}), "d1", None, "delegate_task"),
        (4, "s1", "assistant", "", None, json.dumps([_delegate("d2", ["more"])]), None),
    ])
    response = _bridge(_delegate_payload("d2", ["more"], tmp_path), tmp_path)
    assert response.get("action") == "approve" and _fan_out_asked(response), response
    assert "delegation target #2" in response["message"]


def test_multi_task_delegation_asks_from_its_second_task(tmp_path):
    _db(tmp_path / "state.db", [
        (1, "s1", "user", "verify this one PR", None, None, None),
        (2, "s1", "assistant", "", None, json.dumps([_delegate("d1", ["a", "b"])]), None),
    ])
    response = _bridge(_delegate_payload("d1", ["a", "b"], tmp_path), tmp_path)
    assert response.get("action") == "approve" and _fan_out_asked(response), response
    assert "delegation target #2" in response["message"]


def test_bridge_tool_use_ids_match_the_rebuilt_transcript(tmp_path, monkeypatch):
    """The id a gate receives is the id of its own block in the transcript."""
    db = _db(tmp_path / "state.db", [
        (1, "s1", "user", "go", None, None, None),
        (2, "s1", "assistant", "", None, json.dumps([_delegate("d1", ["a", "b", "c"])]), None),
    ])
    out = hermes.sync_transcript("s1", tmp_path / "t", db)
    blocks = json.loads(out.read_text().splitlines()[-1])["message"]["content"]
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("PRAXIS_HOME", str(tmp_path / "praxis"))
    bridge = _load("hermes_bridge_ids", BRIDGE)
    sent: list[str] = []
    monkeypatch.setattr(bridge, "_groups", lambda event, tool: ["Agent"])
    monkeypatch.setattr(bridge, "_run", lambda event, matcher, payload: (sent.append(payload["tool_use_id"]),
                                                                         (0, "", "", []))[1])
    bridge.pre_tool_call(_delegate_payload("d1", ["a", "b", "c"], tmp_path))
    assert sent == [b["id"] for b in blocks] == ["d1", "d1#1", "d1#2"]


def test_take_self_check_expires_and_separates_sessions(tmp_path):
    take = hermes.take_self_check
    assert take(tmp_path, "s1", "k", 1000.0) is False
    assert take(tmp_path, "s2", "k", 1001.0) is False      # another session has its own record
    assert take(tmp_path, "s1", "k", 1002.0) is True
    assert take(tmp_path, "s2", "k", 1001.0 + hermes.SELF_CHECK_TTL_SEC) is False  # expired: checked again
    (tmp_path / "s1.json").write_text("not json", encoding="utf-8")
    assert take(tmp_path, "s1", "k", 2000.0) is False      # a corrupt record is a first sight


def test_split_asks_separates_consent_from_self_check():
    human, self_check = hermes.split_asks(
        [("side-effect-scan", "x"), ("pre-merge-approval-gate", "y"), ("", "z")])
    assert human == [("pre-merge-approval-gate", "y")]
    assert self_check == [("side-effect-scan", "x"), ("", "z")]


def test_mixed_asks_self_check_first_then_human_consent(monkeypatch, tmp_path):
    """When a consent gate and a self-check gate both ask, the agent re-checks first;
    only the re-issued call goes to the human, with the consent ask alone."""
    bridge = _load("hermes_bridge_mixed", BRIDGE)
    monkeypatch.setenv("PRAXIS_HOME", str(tmp_path / "praxis"))
    monkeypatch.setattr(bridge, "_groups", lambda event, tool: ["Bash"])
    ask = json.dumps({"hookSpecificOutput": {"permissionDecision": "ask", "permissionDecisionReason": "r"}})
    asked = [("pre-merge-approval-gate", "merge consent"), ("cross-boundary-preflight", "checklist")]
    monkeypatch.setattr(bridge, "_run", lambda event, matcher, payload: (0, ask, "", asked))
    call = {"tool_name": "terminal", "tool_input": {"command": "gh pr merge 1"}, "session_id": "s",
            "cwd": str(tmp_path), "extra": {"tool_call_id": "t"}}
    first = bridge.pre_tool_call(call)
    assert first["action"] == "block" and "[praxis:cross-boundary-preflight]" in first["message"]
    assert "pre-merge-approval-gate" not in first["message"]
    second = bridge.pre_tool_call(call)
    assert second["action"] == "approve"
    assert second["message"] == "[praxis] [praxis:pre-merge-approval-gate] merge consent"


def test_self_check_keeps_every_gate_when_a_reason_is_long():
    response = hermes.self_check_response([("gate-a", "x" * 5000), ("gate-b", "short checklist")])
    assert "[praxis:gate-b] short checklist" in response["message"]


def test_parallel_reissues_consume_one_record(tmp_path):
    """Racing identical re-issues behave as if run one after another: each record is
    consumed by exactly one call (8 calls on one record -> pass, record, pass, ... = 4 passes)."""
    import multiprocessing as mp
    _with_lib_path().take_self_check(tmp_path, "s", "k", 1000.0)
    ctx = mp.get_context("spawn")
    with ctx.Pool(8) as pool:
        results = pool.starmap(_take_in_child, [(str(tmp_path),)] * 8)
    assert results.count(True) == 4
    # The 8th call (a first sight after the 7th consumed) leaves one record.
    assert json.loads((tmp_path / "s.json").read_text(encoding="utf-8")) == {"k": 1001.0}


def _with_lib_path():
    """_hermes loaded the way the bridge loads it: with `hooks/_lib` importable, so the real lock applies."""
    lib = str(REPO_ROOT / "hooks" / "_lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    return _load("hermes_with_lib", REPO_ROOT / "hooks" / "_lib" / "_hermes.py")


def _take_in_child(state_dir: str) -> bool:
    return _with_lib_path().take_self_check(Path(state_dir), "s", "k", 1001.0)


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


# --- host notifications --------------------------------------------------------

_BRIEF_7 = ("### PR #7 머지 브리핑\n\n1. **무엇이 바뀌는지**: parser fix\n2. **검증한 것**: 테스트 통과\n"
            "3. **미검증 항목**: prod 경로 미확인\n4. **리스크와 영향 범위**: downstream 없음\n"
            "5. **열린 항목**: 없음\n6. **승인 요청**: Approve merge? PR #7 을 머지할까요?")
_NOTICE = ("[IMPORTANT: Background process proc_1 completed normally (exit code 0).\n"
           "Command: scripts/run-tests.sh\nOutput:\nok]")


def _notified_db(path: Path, kind) -> Path:
    """briefing -> Hermes notification -> one-line reply -> `merge` -> the merge call."""
    rows = [(1, "s1", "user", "브리핑 해줘", None, None, None),
            (2, "s1", "assistant", _BRIEF_7, None, None, None),
            (3, "s1", "user", _NOTICE, None, None, None),
            (4, "s1", "assistant", "테스트 통과, 바뀐 것 없음. PR #7 을 머지할까요?", None, None, None),
            (5, "s1", "user", "merge", None, None, None),
            (6, "s1", "assistant", "", None,
             json.dumps([_call("m1", "terminal", {"command": "gh pr merge 7 --squash"})]), None)]
    _db(path, rows)
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE messages ADD COLUMN display_kind TEXT")
    conn.execute("UPDATE messages SET display_kind = ? WHERE id = 3", (kind,))
    conn.commit()
    conn.close()
    return path


@pytest.mark.parametrize("kind", ["process_complete", "async_delegation_complete", "internal_notification"])
def test_host_notification_row_is_not_a_human_message(tmp_path, kind):
    out = hermes.sync_transcript("s1", tmp_path / "t", _notified_db(tmp_path / "state.db", kind))
    [notice] = [e for e in map(json.loads, out.read_text().splitlines())
                if e["type"] == "user" and e["message"]["content"] == _NOTICE]
    assert notice["origin"] == {"kind": kind}


@pytest.mark.parametrize("kind", [None, "steer", "hidden"])
def test_other_user_rows_stay_human(tmp_path, kind):
    # `steer` is the user's own out-of-band message; an unknown kind stays human (fails closed).
    out = hermes.sync_transcript("s1", tmp_path / "t", _notified_db(tmp_path / "state.db", kind))
    users = [e for e in map(json.loads, out.read_text().splitlines()) if e["type"] == "user"]
    assert all("origin" not in e for e in users)


def _merge_gate(transcript: Path) -> str:
    gate = REPO_ROOT / "hooks" / "advisory-nudge" / "momentum-rule-retrieval-gate" / "impl.py"
    payload = {"tool_name": "Bash", "tool_input": {"command": "gh pr merge 7 --squash"},
               "transcript_path": str(transcript)}
    proc = subprocess.run([sys.executable, str(gate)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30)
    return proc.stdout


def test_notification_between_briefing_and_approval_keeps_the_briefing(tmp_path):
    """The merge gate reads past a Hermes notification, as it does past Claude Code's (#1410)."""
    db = _notified_db(tmp_path / "state.db", "process_complete")
    assert '"deny"' not in _merge_gate(hermes.sync_transcript("s1", tmp_path / "t", db))


def test_untagged_notification_still_closes_the_window(tmp_path):
    """Positive control: the same rows without the kind deny, as they did before the tag."""
    db = _notified_db(tmp_path / "state.db", None)
    assert '"deny"' in _merge_gate(hermes.sync_transcript("s1", tmp_path / "t", db))
