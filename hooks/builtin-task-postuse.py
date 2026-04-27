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


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)

    # Claude Code uses snake_case "tool_name"; camelCase fallback for forward-compat
    tool_name = payload.get("tool_name") or payload.get("toolName") or ""
    if tool_name not in BUILTIN_TASK_MGMT_TOOLS:
        sys.exit(0)

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


if __name__ == "__main__":
    main()
