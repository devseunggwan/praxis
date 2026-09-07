#!/usr/bin/env python3
"""SubagentStart hook: hand every subagent the shared-state isolation contract.

Issue #1369 — a delegated worker overwrote this machine's operational ledger
because the delegation prompt derived its risk axis from rule vocabulary
("do not write to external surfaces") instead of from the target's reach. The
prompt forbade `gh` writes and said nothing about `PRAXIS_HOME`,
`PRAXIS_FIRE_TELEMETRY_FILE`, or `HOME`, so the worker pointed praxis's own
state at the live paths and polluted them. That cost a strike.

Relying on the delegator to paste the same paragraph into every prompt is what
failed. `SubagentStart` fires as the subagent spawns, whatever the delegator
wrote, so the contract arrives structurally.

Why injection is the only available design
==========================================

The payload carries `agent_id`, `agent_type`, `cwd`, `hook_event_name`,
`prompt_id`, `session_id`, `transcript_path` — and nothing else. In particular
there is **no `task` field**, despite the published reference listing one
(measured 2026-09-07 on Claude Code 2.1.263; see RUNTIME_CONSTRAINTS.md entry
8). The delegation prompt is therefore not readable here, so "inspect the
prompt and warn when the isolation paragraph is missing" cannot be built. The
hook can only add context, so it adds it unconditionally.

Why every agent type, with no allowlist
=======================================

A read-only agent is the tempting exemption, but agent types are declared by
whoever wrote the agent, not by this hook, and a type that is read-only today
is one frontmatter edit away from not being. An allowlist would need to track
that and would fail open into silence exactly when it went stale. The contract
is six lines; paying it on every spawn is cheaper than maintaining a list whose
staleness is invisible. `PRAXIS_HOOK_BYPASS_DELEGATION_CONTEXT=1` turns it off
wholesale for anyone who disagrees.
"""
from __future__ import annotations

import json
import os
import sys

from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT.parent.parent / "_lib"))
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402

BYPASS_ENV = "PRAXIS_HOOK_BYPASS_DELEGATION_CONTEXT"

# The variables whose override reaches shared, durable state on this machine.
# Named individually rather than described, because the recorded failure was
# precisely that a general phrase ("don't touch shared state") did not make the
# delegator think of these.
_ISOLATION_VARS = (
    "PRAXIS_HOME",
    "PRAXIS_FIRE_TELEMETRY_FILE",
    "PRAXIS_STATE_DIR",
    "HOME",
)

_CONTRACT = (
    "praxis delegation contract — shared local state:\n"
    "- Do NOT set or override {vars}. They point at this machine's live "
    "praxis state and telemetry; a delegated run that redirects them corrupts "
    "the operating record for every other session.\n"
    "- Scratch files go under your own working directory or $TMPDIR, never "
    "under ~/.praxis or ~/.claude.\n"
    "- Treat every path outside your assigned worktree as read-only unless "
    "the task named it explicitly.\n"
    "- The risk axis is the target's reach, not the verb: a local write that "
    "lands in shared state is as damaging as an external one."
)


def _bypassed() -> bool:
    return os.environ.get(BYPASS_ENV, "").strip() == "1"


def _context() -> str:
    return _CONTRACT.format(vars=", ".join(f"`{v}`" for v in _ISOLATION_VARS))


@fail_open
def main() -> int:
    payload = read_payload()
    if not isinstance(payload, dict):
        return 0
    if _bypassed():
        return 0

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "SubagentStart",
                "additionalContext": _context(),
            }
        },
        sys.stdout,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
