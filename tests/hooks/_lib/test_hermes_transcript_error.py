"""A transcript that cannot be built leaves the other gates running (#1622).

ARCHITECTURE.md promises that an unreadable transcript is empty, which only
the transcript-reading gates treat as fail-open. An exception escaping to the
bridge's last-resort handler instead printed `{}` and ran no gate at all.

Run: python3 -m pytest tests/hooks/_lib/test_hermes_transcript_error.py -q
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


base = _load("test_hermes_base_for_transcript_error", HERE / "test_hermes.py")

MERGE = "gh pr merge 1 --squash"  # blocked by side-effect-scan's self-check in any git dir


def _merge_payload(tmp_path: Path) -> dict:
    return {"hook_event_name": "pre_tool_call", "tool_name": "terminal", "session_id": "s1",
            "tool_input": {"command": MERGE}, "cwd": str(tmp_path), "extra": {"tool_call_id": "m1"}}


def test_transcript_error_becomes_an_empty_path(tmp_path, monkeypatch, capsys):
    bridge = _load("hermes_bridge_transcript_error", base.BRIDGE)

    def boom(*_args, **_kwargs):
        raise ValueError("bad row")

    monkeypatch.setattr(bridge._hermes, "sync_transcript", boom)
    assert bridge._transcript("s1") == ""
    assert "transcript unavailable: ValueError('bad row')" in capsys.readouterr().err


def test_gates_still_run_when_a_row_breaks_the_transcript(tmp_path):
    """A user row the transcript builder cannot read (an integer in a column
    with no TEXT type), then a call the bridge must block."""
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id, role, content, "
                 "tool_call_id, tool_calls, tool_name, active DEFAULT 1)")
    conn.executemany("INSERT INTO messages (session_id, role, content, tool_calls) VALUES (?, ?, ?, ?)", [
        ("s1", "user", 5, None),
        ("s1", "assistant", "", json.dumps([base._call("m1", "terminal", {"command": MERGE})])),
    ])
    conn.commit()
    conn.close()
    assert base._bridge(_merge_payload(tmp_path), tmp_path).get("action") == "block"


def test_readable_transcript_control(tmp_path):
    base._db(tmp_path / "state.db", [
        (1, "s1", "user", "merge it", None, None, None),
        (2, "s1", "assistant", "", None, json.dumps([base._call("m1", "terminal", {"command": MERGE})]), None),
    ])
    assert base._bridge(_merge_payload(tmp_path), tmp_path).get("action") == "block"
