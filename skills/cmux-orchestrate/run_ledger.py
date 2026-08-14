"""Run/Task ledger for delegated work (issue #982).

A delegation is a single workspace, and `cmux-delegate --distribute` opens N of
them at once. Nothing binds those N together, so "how far did that work get?"
has only ever been answerable by reading the sidebar tabs with your eyes. This
module gives that set an identity, a history, and a state that is more than
"report file exists / does not exist".

APPEND-ONLY, FOLDED ON READ. State lives in `<state>/runs/<run-id>/events.jsonl`
as one JSON object per line, and the current picture is derived by folding the
events in file order. There is deliberately no mutable state document: N
delegations write to one run concurrently, and while a read-modify-write of a
single JSON file races, an append does not. `_fire_ledger._atomic_append` makes
the same trade for the same reason, and its per-line `os.write` under `O_APPEND`
is reused here rather than reimplemented.

FAIL-OPEN, ALWAYS. A ledger that breaks a delegation is worse than no ledger.
Every write swallows its own failure, every cmux call is best-effort, and the
process exits 0 for any well-formed argument list. The one thing that earns a
non-zero exit is a malformed invocation (usage, exit 2) — a caller that passed
the wrong arguments has a bug the silence would hide.

THE LEDGER IS THE RECORD; CMUX IS A VIEW. `cmux workspace-group` already binds
workspaces natively, but a group dies with the cmux process and carries no
history, so it cannot be the record. The group, the status pill, and the
progress bar are all painted from the ledger and none of them is read back —
on a host with no cmux, every query still answers.

WHAT THIS DELIBERATELY DOES NOT TOUCH: `cmux todo`. Issue #982 asked for the
task lifecycle to drive it, but `cmux todo --help` reserves that checklist for
the user in as many words — "Do not add, edit, complete, remove, or replace
items on your own initiative". `set-status` and `set-progress` carry no such
reservation, so the sidebar is reflected through those instead.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "hooks", "_lib"),
)

try:  # praxis path conventions; absent only in a stripped checkout
    from _paths import praxis_state_dir  # type: ignore
except Exception:  # pragma: no cover - exercised by the fallback test
    def praxis_state_dir() -> str:
        override = os.environ.get("PRAXIS_STATE_DIR")
        if override:
            return os.path.expanduser(override)
        home = os.path.expanduser(os.environ.get("PRAXIS_HOME") or "~/.praxis")
        return os.path.join(home, "state")


_CMUX_TIMEOUT_S = 5
_STATUS_KEY = "praxis_run"

# One event per line. `task.*` events all carry `workspace`, which is the task's
# identity — a run's task list is the set of workspaces bound to it, so a task
# needs no separate id to be addressed later.
_RUN_CREATED = "run.created"
_TASK_ADDED = "task.added"
_TASK_STARTED = "task.started"
_TASK_DONE = "task.done"
_TASK_BLOCKED = "task.blocked"
_RUN_CLOSED = "run.closed"


def _runs_root() -> str:
    return os.path.join(praxis_state_dir(), "runs")


def _events_path(run_id: str) -> str:
    return os.path.join(_runs_root(), run_id, "events.jsonl")


def _new_run_id() -> str:
    """epoch seconds + PID — the collision-avoidance convention cmux-delegate
    already uses for its prompt files, and runs are minted in that same flow."""
    return f"{int(time.time())}-{os.getpid()}"


def _append(run_id: str, event: dict[str, Any]) -> bool:
    """One JSONL line, best-effort. Returns whether it landed."""
    path = _events_path(run_id)
    line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # O_APPEND with a single write per line: a record this small lands
        # whole, so concurrent delegations interleave lines but never tear one.
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except Exception:
        return False


def _read_events(run_id: str) -> list[dict[str, Any]]:
    """Events in file order. A corrupt line is skipped, not fatal — a partially
    readable history answers more questions than a raised exception."""
    try:
        with open(_events_path(run_id), encoding="utf-8") as handle:
            raw = handle.readlines()
    except OSError:
        return []
    events = []
    for line in raw:
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict) and parsed.get("event"):
            events.append(parsed)
    return events


def fold(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Current picture from the whole history, in file order.

    Later events win, which is what makes `start → done` and a re-bind of the
    same workspace both behave. `blocked` is not special-cased as sticky: a task
    that was blocked and then finished is done, and a run that re-opens work
    should read as re-opened.
    """
    label = ""
    closed = False
    tasks: dict[str, dict[str, str]] = {}
    for event in events:
        kind = event.get("event")
        workspace = str(event.get("workspace") or "")
        if kind == _RUN_CREATED:
            label = str(event.get("label") or label)
        elif kind == _RUN_CLOSED:
            closed = True
        elif kind == _TASK_ADDED and workspace:
            tasks[workspace] = {
                "state": "pending",
                "label": str(event.get("label") or ""),
            }
        elif kind in (_TASK_STARTED, _TASK_DONE, _TASK_BLOCKED) and workspace:
            task = tasks.setdefault(workspace, {"state": "pending", "label": ""})
            task["state"] = {
                _TASK_STARTED: "running",
                _TASK_DONE: "done",
                _TASK_BLOCKED: "blocked",
            }[kind]
            if event.get("label"):
                task["label"] = str(event["label"])
    counts = {"pending": 0, "running": 0, "done": 0, "blocked": 0}
    for task in tasks.values():
        counts[task["state"]] = counts.get(task["state"], 0) + 1
    total = len(tasks)
    return {
        "label": label,
        "closed": closed,
        "tasks": tasks,
        "counts": counts,
        "total": total,
        "state": _run_state(closed, total, counts),
    }


def _run_state(closed: bool, total: int, counts: dict[str, int]) -> str:
    """A run is `blocked` while anything is blocked — the whole point of the
    state is to surface the thing that needs a human, and a run reading `running`
    because two of three tasks are fine hides exactly that."""
    if closed:
        return "closed"
    if total == 0:
        return "empty"
    if counts.get("blocked"):
        return "blocked"
    if counts.get("done") == total:
        return "complete"
    if counts.get("running"):
        return "running"
    return "pending"


def _cmux(argv: list[str]) -> str | None:
    """stdout of a cmux call, or None on any failure. Never raises.

    Mirrors `agent_liveness._run`. Every caller here treats None as "no view was
    painted" and carries on — praxis is host-neutral (ARCHITECTURE.md) and the
    ledger is complete without cmux.
    """
    try:
        proc = subprocess.run(
            ["cmux", *argv],
            capture_output=True,
            text=True,
            timeout=_CMUX_TIMEOUT_S,
            check=False,
            env={**os.environ, "CMUX_QUIET": "1"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _group_ref(run_id: str) -> str | None:
    """The group whose name is this run, if cmux is here and holds one."""
    out = _cmux(["workspace-group", "list", "--json"])
    if not out:
        return None
    try:
        groups = json.loads(out).get("groups") or []
    except ValueError:
        return None
    for group in groups:
        if isinstance(group, dict) and group.get("name") == _group_name(run_id):
            for key in ("ref", "id"):
                if group.get(key):
                    return str(group[key])
    return None


def _group_name(run_id: str) -> str:
    return f"run:{run_id}"


def _paint(run_id: str, workspace: str | None = None) -> None:
    """Push the folded state onto the sidebar. Best-effort throughout.

    The pill key is fixed so this tool owns one entry and never fights another
    tool's; the progress bar is the run's, so it is painted on every bound
    workspace rather than only the one that just moved.
    """
    view = fold(_read_events(run_id))
    done = view["counts"].get("done", 0)
    total = view["total"] or 1
    targets = [workspace] if workspace else list(view["tasks"])
    for target in targets:
        if not target:
            continue
        _cmux(["set-status", _STATUS_KEY, view["state"], "--workspace", target])
        _cmux([
            "set-progress",
            f"{done / total:.2f}",
            "--label",
            f"{done}/{view['total']}",
            "--workspace",
            target,
        ])


def create(label: str) -> dict[str, Any]:
    run_id = _new_run_id()
    _append(run_id, {
        "event": _RUN_CREATED,
        "at": int(time.time()),
        "label": label,
    })
    # Anchor-only group: members join as delegations bind, so creating it with
    # --from would capture whatever happens to be live right now instead.
    _cmux(["workspace-group", "create", "--name", _group_name(run_id)])
    return {"run": run_id, "state": "empty"}


def bind(run_id: str, workspace: str, label: str) -> dict[str, Any]:
    _append(run_id, {
        "event": _TASK_ADDED,
        "at": int(time.time()),
        "workspace": workspace,
        "label": label,
    })
    group = _group_ref(run_id)
    if group:
        _cmux(["workspace-group", "add", "--group", group, "--workspace", workspace])
    _paint(run_id, workspace)
    return {"run": run_id, "workspace": workspace, "state": "pending"}


def mark(run_id: str, workspace: str, kind: str, note: str = "") -> dict[str, Any]:
    event = {
        _TASK_STARTED: _TASK_STARTED,
        _TASK_DONE: _TASK_DONE,
        _TASK_BLOCKED: _TASK_BLOCKED,
    }[kind]
    payload: dict[str, Any] = {
        "event": event,
        "at": int(time.time()),
        "workspace": workspace,
    }
    if note:
        payload["note"] = note
    _append(run_id, payload)
    _paint(run_id, workspace)
    # The task's state as `fold` names it, not as the event names it: an event is
    # `task.started` and the state it produces is `running`, and a caller that
    # reads `state=` from here and from `summary` must not get two vocabularies
    # for one thing.
    return {
        "run": run_id,
        "workspace": workspace,
        "state": fold(_read_events(run_id))["tasks"].get(workspace, {}).get("state", ""),
    }


def close(run_id: str) -> dict[str, Any]:
    _append(run_id, {"event": _RUN_CLOSED, "at": int(time.time())})
    _paint(run_id)
    return {"run": run_id, "state": "closed"}


def summary(run_id: str) -> dict[str, Any]:
    view = fold(_read_events(run_id))
    counts = view["counts"]
    return {
        "run": run_id,
        "label": view["label"],
        "state": view["state"],
        "total": view["total"],
        "done": counts.get("done", 0),
        "running": counts.get("running", 0),
        "blocked": counts.get("blocked", 0),
        "pending": counts.get("pending", 0),
    }


def runs() -> list[str]:
    """Run ids, newest first. Sorted on the id itself: it opens with epoch
    seconds, so lexical order over a fixed-width era is chronological."""
    try:
        entries = os.listdir(_runs_root())
    except OSError:
        return []
    return sorted((e for e in entries if os.path.isdir(os.path.join(_runs_root(), e))), reverse=True)


def _shell_quote(value: str) -> str:
    """POSIX single-quoting: close, escape, reopen for an embedded quote."""
    return "'" + str(value).replace("'", "'\\''") + "'"


def _format(result: dict[str, Any]) -> str:
    """`key='value'` pairs — readable by a shell caller under `eval` without a
    JSON parser. Quoted unconditionally: labels carry user text, and deciding
    per-value which characters are dangerous is how one gets through."""
    parts = []
    for key in ("run", "label", "state", "workspace", "total", "done", "running", "blocked", "pending"):
        if key not in result:
            continue
        value = str(result[key]).replace("\n", " ").strip()
        parts.append(f"{key}={_shell_quote(value)}")
    return " ".join(parts)


_USAGE = (
    "usage: run_ledger.py <command> [args]\n"
    "  create <label>\n"
    "  bind <run-id> <workspace> <label>\n"
    "  start|done|block <run-id> <workspace> [note]\n"
    "  close <run-id>\n"
    "  summary <run-id>\n"
    "  list\n"
)

_ARITY = {
    "create": (1, 1),
    "bind": (3, 3),
    "start": (2, 3),
    "done": (2, 3),
    "block": (2, 3),
    "close": (1, 1),
    "summary": (1, 1),
    "list": (0, 0),
}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in _ARITY:
        print(_USAGE, file=sys.stderr, end="")
        return 2
    command, args = argv[1], argv[2:]
    low, high = _ARITY[command]
    if not low <= len(args) <= high:
        print(_USAGE, file=sys.stderr, end="")
        return 2

    if command == "create":
        print(_format(create(args[0])))
    elif command == "bind":
        print(_format(bind(args[0], args[1], args[2])))
    elif command in ("start", "done", "block"):
        kind = {"start": _TASK_STARTED, "done": _TASK_DONE, "block": _TASK_BLOCKED}[command]
        print(_format(mark(args[0], args[1], kind, args[2] if len(args) > 2 else "")))
    elif command == "close":
        print(_format(close(args[0])))
    elif command == "summary":
        print(_format(summary(args[0])))
    elif command == "list":
        for run_id in runs():
            print(_format(summary(run_id)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
