#!/usr/bin/env python3
"""merge-gate: every praxis check on the merge lifecycle, in one hook (issue #1650).

The checks used to be six hooks. Each is now a module next to this file and
keeps its own verdict and messages; this entry point picks the checks for the
event and folds their results into one answer.

  PreToolUse(Bash)            approval  ask before every `gh pr merge`
                              worktree  block a `--delete-branch` merge whose
                                        head is checked out in a worktree
                              briefing  deny a merge with no Pre-Merge
                                        Reporting briefing and answer
  PreToolUse(AskUserQuestion) refetch   re-read a PR's live state before a
                                        merge question
                              menu      a merge menu without a review option
  Stop / SubagentStop         claim     a merge/PR state claim with no fresh
                                        state query behind it

Folding follows the dispatcher's PreToolUse lane (`_lib/_dispatch.py`): the
first deny (exit 2 or `permissionDecision: "deny"`) ends the run and is the
answer; otherwise the first ask is; stderr from every check that ran is kept.
The briefing check runs only on the hosts its old hook was registered for.

Fail-open: a check that raises is reported as a pass, as `fail_open` does for
a whole hook.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType
from typing import Callable, Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent / "_lib"))
from _hook_io import read_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open, member_host  # type: ignore[import-not-found]  # noqa: E402

BASH_CHECKS = ("approval", "worktree", "briefing")
ASK_CHECKS = ("refetch", "menu")
STOP_CHECKS = ("claim",)
STOP_EVENTS = frozenset({"Stop", "SubagentStop"})

# momentum-rule-retrieval-gate, which held the briefing check, was registered
# for these hosts only; None is a standalone run (tests, replay scripts).
BRIEFING_HOSTS = frozenset({"claude", "codex", "hermes"})


def _load(check: str) -> ModuleType:
    name = f"_praxis_merge_gate_{check}"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{check}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"merge-gate check {check!r} not loadable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _run(main: Callable[[], int], payload_raw: str) -> tuple[int, str, str]:
    """Run one check's `main()` on the payload; (exit, stdout, stderr).

    The check's own `fail_open` wrapper is peeled off: it would record a fire
    per check, and this hook's `main()` already records one per call.
    """
    inner = getattr(main, "__wrapped__", main)
    out, err = io.StringIO(), io.StringIO()
    saved_stdin = sys.stdin
    sys.stdin = io.StringIO(payload_raw)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = inner() or 0
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else 0
    except Exception as exc:  # noqa: BLE001 - fail open, but say which check broke
        return 0, "", f"[merge-gate] check failed open: {type(exc).__name__}: {exc}\n"
    finally:
        sys.stdin = saved_stdin
    return rc, out.getvalue(), err.getvalue()


def _checks_for(payload: dict) -> tuple[str, ...]:
    event = payload.get("hook_event_name")
    tool = payload.get("tool_name")
    if event in STOP_EVENTS or (event is None and tool is None):
        return STOP_CHECKS
    if tool == "Bash":
        host = member_host()
        if host is None or host in BRIEFING_HOSTS:
            return BASH_CHECKS
        return tuple(c for c in BASH_CHECKS if c != "briefing")
    if tool == "AskUserQuestion":
        return ASK_CHECKS
    return ()


@fail_open
def main() -> int:
    payload_raw = sys.stdin.read()
    try:
        payload = json.loads(payload_raw)
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0

    stderr_parts: list[str] = []
    other_stdout: list[str] = []
    first_ask: Optional[str] = None
    for check in _checks_for(payload):
        try:
            main_fn = _load(check).main
        except Exception as exc:  # noqa: BLE001 - a broken check must not block
            stderr_parts.append(f"[merge-gate] {check} import failed: {exc}\n")
            continue
        rc, out, err = _run(main_fn, payload_raw)
        stderr_parts.append(err)
        decision = read_decision(out)[0] if out.strip() else None
        if rc == 2 or decision == "deny":
            sys.stderr.write("".join(stderr_parts))
            sys.stdout.write(out)
            return rc
        if decision == "ask":
            if first_ask is None:
                first_ask = out
        elif out.strip():
            other_stdout.append(out)

    sys.stderr.write("".join(stderr_parts))
    if first_ask is not None:
        sys.stdout.write(first_ask)
    elif other_stdout:
        # Stop checks speak through stdout JSON (`systemMessage` / block); one
        # check per Stop event, so there is never more than one object here.
        sys.stdout.write(other_stdout[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
