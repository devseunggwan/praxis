#!/usr/bin/env python3
"""PostToolUse hook: suppress false agent-spawn signals for built-in task management tools.

Claude Code ships two sets of "Task*" tools with completely different semantics:
  - Task            → spawns a subagent (real agent operation)
  - TaskCreate      → creates an entry in the built-in task list (NO subagent)
  - TaskUpdate      → updates a task list entry              (NO subagent)
  - TaskGet         → reads a task list entry                (NO subagent)
  - TaskList        → lists task list entries                (NO subagent)
  - TaskStop        → cancels a task list entry              (NO subagent)
  - TaskOutput      → reads task output                      (NO subagent)

Some upstream hooks (e.g. OMC pre-tool-enforcer) conflate TaskCreate/TaskUpdate
with Task and emit "Spawning agent" signals for them.  This PostToolUse hook
fires after the tool executes and emits a corrective context note so Claude is
not misled by those false positives.

Output path contract (mutually exclusive — exactly one fires per invocation):
  PATH A  tool_name in BUILTIN_TASK_MGMT_TOOLS
          → emit CORRECTION_NOTE ("no subagent was spawned"), then return.
  PATH B  tool_name NOT in BUILTIN_TASK_MGMT_TOOLS
          → silent pass-through (exit 0, no output), then return.

There is no cross-call state: each hook invocation is independent.  No module-
level counter exists because counters that survive across Python process launches
are impossible here (each Claude Code hook call is a fresh process), but the
explicit per-invocation return gates below guard against any future drift where
a counter or second branch is accidentally inserted between the two paths.
"""
from __future__ import annotations

import json
import sys

BUILTIN_TASK_MGMT_TOOLS = frozenset({
    "TaskCreate",
    "TaskUpdate",
    "TaskGet",
    "TaskList",
    "TaskStop",
    "TaskOutput",
})

CORRECTION_NOTE = (
    "Built-in task list operation completed — no subagent was spawned. "
    "Agent-spawn signals emitted before this tool ran were false positives."
)


def _emit_correction() -> None:
    """PATH A: emit corrective context note and return.

    Called exclusively for tools in BUILTIN_TASK_MGMT_TOOLS.  No other output
    is produced after this function returns — the caller must return immediately
    after calling this to preserve the mutually-exclusive output contract.
    """
    json.dump(
        {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": CORRECTION_NOTE,
            },
        },
        sys.stdout,
    )
    sys.stdout.write("\n")


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)  # PATH B (malformed input): fail-open, no output

    # Claude Code uses snake_case "tool_name"; camelCase fallback for forward-compat
    tool_name = payload.get("tool_name") or payload.get("toolName") or ""

    if tool_name in BUILTIN_TASK_MGMT_TOOLS:
        # PATH A: built-in task management tool — emit correction, then stop.
        # Early return here ensures PATH B code can never execute for this
        # invocation, eliminating any possibility of dual-message emission.
        _emit_correction()
        return  # ← explicit gate: nothing below runs for PATH A

    # PATH B: not a built-in task management tool — silent pass-through.
    # sys.exit(0) is used (not plain return) to make the intent unambiguous:
    # this invocation produces zero output.
    sys.exit(0)


if __name__ == "__main__":
    main()
