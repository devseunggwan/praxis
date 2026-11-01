"""Every `delegate_task` shape Hermes spawns from reaches the fan-out gate (#1621).

Hermes's `_normalize_task_list` accepts `tasks` as a list or a JSON string, and
the legacy single-goal shape (top-level `goal` / `context`) as a one-task batch.

Run: python3 -m pytest tests/hooks/_lib/test_hermes_delegate_shapes.py -q
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


base = _load("test_hermes_base_for_delegate", HERE / "test_hermes.py")
hermes = base.hermes

_ONE = [("Agent", {"description": "a", "prompt": "a\n\nc", "subagent_type": "general-purpose"})]


# --- translation ---------------------------------------------------------------

def test_legacy_single_goal_is_one_agent_call():
    assert hermes.claude_calls("delegate_task", {"goal": "a", "context": "c"}) == _ONE


def test_tasks_sent_as_a_json_string_are_parsed():
    tasks = json.dumps([{"goal": "a", "context": "c"}, {"goal": "b"}])
    assert [i["description"] for _, i in hermes.claude_calls("delegate_task", {"tasks": tasks})] == ["a", "b"]


def test_empty_tasks_beside_a_goal_is_the_single_goal():
    """Hermes: "Small models emit tasks=[] alongside a single goal"."""
    assert hermes.claude_calls("delegate_task", {"tasks": [], "goal": "a", "context": "c"}) == _ONE


def test_tasks_win_over_a_goal():
    calls = hermes.claude_calls("delegate_task", {"tasks": [{"goal": "t"}], "goal": "g"})
    assert [i["description"] for _, i in calls] == ["t"]


def test_shapes_hermes_rejects_spawn_nothing():
    assert hermes.claude_calls("delegate_task", {"tasks": "{not json"}) == []
    assert hermes.claude_calls("delegate_task", {"tasks": '{"goal": "a"}'}) == []
    assert hermes.claude_calls("delegate_task", {"goal": "   "}) == []
    assert hermes.claude_calls("delegate_task", {"action": "list", "goal": "a"}) == []


def test_transcript_renders_the_legacy_shape(tmp_path):
    call = base._call("d1", "delegate_task", {"goal": "a", "context": "c"})
    db = base._db(tmp_path / "state.db", [
        (1, "s1", "user", "go", None, None, None),
        (2, "s1", "assistant", "", None, json.dumps([call]), None),
    ])
    blocks = json.loads(hermes.sync_transcript("s1", tmp_path / "t", db).read_text().splitlines()[-1])
    assert [(b["id"], b["name"]) for b in blocks["message"]["content"]] == [("d1", "Agent")]


# --- through the bridge --------------------------------------------------------

def _payload(call_id: str, args: dict, tmp_path: Path) -> dict:
    return {"hook_event_name": "pre_tool_call", "tool_name": "delegate_task", "session_id": "s1",
            "tool_input": args, "cwd": str(tmp_path), "extra": {"tool_call_id": call_id}}


def test_second_delegation_in_legacy_shape_asks(tmp_path):
    legacy = {"goal": "more", "context": "c"}
    base._db(tmp_path / "state.db", [
        (1, "s1", "user", "verify this one PR", None, None, None),
        (2, "s1", "assistant", "", None, json.dumps([base._call("d1", "delegate_task", legacy)]), None),
        (3, "s1", "tool", json.dumps({"results": [{"summary": "ok"}]}), "d1", None, "delegate_task"),
        (4, "s1", "assistant", "", None, json.dumps([base._call("d2", "delegate_task", legacy)]), None),
    ])
    response = base._bridge(_payload("d2", legacy, tmp_path), tmp_path)
    assert response.get("action") == "approve" and base._fan_out_asked(response), response
    assert "delegation target #2" in response["message"]


def test_first_legacy_delegation_is_not_asked(tmp_path):
    """Control: the gate's own first-target pass-through holds for the legacy shape."""
    legacy = {"goal": "verify", "context": "c"}
    base._db(tmp_path / "state.db", [
        (1, "s1", "user", "verify this one PR", None, None, None),
        (2, "s1", "assistant", "", None, json.dumps([base._call("d1", "delegate_task", legacy)]), None),
    ])
    assert not base._fan_out_asked(base._bridge(_payload("d1", legacy, tmp_path), tmp_path))
