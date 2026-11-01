"""The Hermes bridge runs gates in the directory the command will run in (#1618).

Hermes runs a `terminal` call without `workdir` in the session's own cwd
record, which the last directory-changing command set; the hook payload's
`cwd` is Hermes's process directory. Gates that read git or gh state must see
the former.

Run: python3 -m pytest tests/hooks/_lib/test_hermes_session_cwd.py -q
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


base = _load("test_hermes_base_for_cwd", HERE / "test_hermes.py")
hermes = base.hermes


def _terminal_call(call_id: str, command: str, **extra) -> str:
    return json.dumps([base._call(call_id, "terminal", {"command": command, **extra})])


def _result(cwd: str | None = None, exit_code: int = 0) -> str:
    data = {"output": "", "exit_code": exit_code, "error": None}
    if cwd is not None:
        data["cwd"] = cwd
    return json.dumps(data)


# --- session_cwd ---------------------------------------------------------------

def test_cd_result_is_the_session_cwd(tmp_path):
    db = base._db(tmp_path / "state.db", [
        (1, "s1", "assistant", "", None, _terminal_call("c1", "cd /work/a"), None),
        (2, "s1", "tool", _result("/work/a"), "c1", None, "terminal"),
    ])
    assert hermes.session_cwd("s1", db) == "/work/a"


def test_the_last_move_wins_and_a_quiet_command_keeps_it(tmp_path):
    db = base._db(tmp_path / "state.db", [
        (1, "s1", "assistant", "", None, _terminal_call("c1", "cd /work/a"), None),
        (2, "s1", "tool", _result("/work/a"), "c1", None, "terminal"),
        (3, "s1", "assistant", "", None, _terminal_call("c2", "cd /work/b"), None),
        (4, "s1", "tool", _result("/work/b"), "c2", None, "terminal"),
        (5, "s1", "assistant", "", None, _terminal_call("c3", "ls"), None),
        (6, "s1", "tool", _result(), "c3", None, "terminal"),
    ])
    assert hermes.session_cwd("s1", db) == "/work/b"


def test_a_workdir_call_does_not_move_the_session(tmp_path):
    """Hermes never records the cwd of a call that passed `workdir`."""
    db = base._db(tmp_path / "state.db", [
        (1, "s1", "assistant", "", None, _terminal_call("c1", "cd /work/a"), None),
        (2, "s1", "tool", _result("/work/a"), "c1", None, "terminal"),
        (3, "s1", "assistant", "", None, _terminal_call("c2", "cd sub", workdir="/elsewhere"), None),
        (4, "s1", "tool", _result("/elsewhere/sub"), "c2", None, "terminal"),
    ])
    assert hermes.session_cwd("s1", db) == "/work/a"


def test_inactive_rows_still_count(tmp_path):
    """Compaction deactivates rows; Hermes's in-memory record keeps the directory."""
    db = base._db(tmp_path / "state.db", [
        (1, "s1", "assistant", "", None, _terminal_call("c1", "cd /work/a"), None),
        (2, "s1", "tool", _result("/work/a"), "c1", None, "terminal"),
    ])
    conn = sqlite3.connect(db)
    conn.execute("UPDATE messages SET active = 0")
    conn.commit()
    conn.close()
    assert hermes.session_cwd("s1", db) == "/work/a"


def test_only_terminal_results_and_only_this_session(tmp_path):
    db = base._db(tmp_path / "state.db", [
        (1, "s1", "assistant", "", None, json.dumps([base._call("r1", "read_file", {"path": "/a"})]), None),
        (2, "s1", "tool", json.dumps({"cwd": "/not/a/move"}), "r1", None, "read_file"),
        (3, "s2", "assistant", "", None, _terminal_call("c1", "cd /other"), None),
        (4, "s2", "tool", _result("/other"), "c1", None, "terminal"),
    ])
    assert hermes.session_cwd("s1", db) is None
    assert hermes.session_cwd("", db) is None
    assert hermes.session_cwd("s1", tmp_path / "missing.db") is None


# --- through the bridge --------------------------------------------------------

def _github_checkout(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin",
                    "https://github.com/example-owner/example-repo.git"], check=True)
    return path


def _comment_payload(process_cwd: Path) -> dict:
    return {"hook_event_name": "pre_tool_call", "tool_name": "terminal", "session_id": "s1",
            "tool_input": {"command": "gh pr comment 1 --body-file body.md"},
            "cwd": str(process_cwd), "extra": {"tool_call_id": "c9"}}


def _asked_by_cross_boundary(response: dict) -> bool:
    return "[praxis:cross-boundary-preflight]" in str(response.get("message", ""))


def test_gates_run_where_the_session_moved(tmp_path):
    """`cd <checkout>` then a repo-less `gh pr comment`: the gate resolves the
    checkout, though the payload names Hermes's non-git process directory."""
    repo = _github_checkout(tmp_path / "repo")
    process_cwd = tmp_path / "install-root"
    process_cwd.mkdir()
    base._db(tmp_path / "state.db", [
        (1, "s1", "user", "comment on the PR", None, None, None),
        (2, "s1", "assistant", "", None, _terminal_call("c1", f"cd {repo}"), None),
        (3, "s1", "tool", _result(str(repo)), "c1", None, "terminal"),
        (4, "s1", "assistant", "", None, _terminal_call("c9", "gh pr comment 1 --body-file body.md"), None),
    ])
    assert _asked_by_cross_boundary(base._bridge(_comment_payload(process_cwd), tmp_path))


def test_terminal_cwd_is_the_default_before_any_move(tmp_path):
    repo = _github_checkout(tmp_path / "repo")
    process_cwd = tmp_path / "install-root"
    process_cwd.mkdir()
    response = base._bridge(_comment_payload(process_cwd), tmp_path, {"TERMINAL_CWD": str(repo)})
    assert _asked_by_cross_boundary(response)


def test_payload_cwd_is_the_last_fallback(tmp_path):
    """Control: no move and no TERMINAL_CWD keeps the payload's directory."""
    repo = _github_checkout(tmp_path / "repo")
    assert _asked_by_cross_boundary(base._bridge(_comment_payload(repo), tmp_path))
    process_cwd = tmp_path / "install-root"
    process_cwd.mkdir()
    assert not _asked_by_cross_boundary(base._bridge(_comment_payload(process_cwd), tmp_path))


def test_a_container_path_is_skipped(tmp_path):
    """A docker/ssh backend records paths that do not exist on this host."""
    repo = _github_checkout(tmp_path / "repo")
    base._db(tmp_path / "state.db", [
        (1, "s1", "assistant", "", None, _terminal_call("c1", "cd /workspace/x"), None),
        (2, "s1", "tool", _result("/workspace/does-not-exist-here"), "c1", None, "terminal"),
    ])
    assert _asked_by_cross_boundary(base._bridge(_comment_payload(repo), tmp_path))
