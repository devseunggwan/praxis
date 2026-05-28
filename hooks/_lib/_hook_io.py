"""Standard decision-emit format for praxis PreToolUse hooks (issue #470).

13 PreToolUse hooks each hand-rolled the identical `permissionDecision` JSON
dump to stdout — `json.dump({"hookSpecificOutput": {"hookEventName":
"PreToolUse", "permissionDecision": <ask|deny>, "permissionDecisionReason":
<reason>}}, sys.stdout)` followed by a trailing newline. When the Claude Code
decision protocol changes (field rename, new field, event-name variant), that
is 13 edit sites instead of one.

This module pins that shape in a single place, mirroring the sibling
`block_message.py` (issue #439) extraction: a pure `format_*` function plus a
thin I/O `emit_*` wrapper with an injectable stream for testing. Neither
function exits — the caller owns the exit code (2 for a PreToolUse block path,
0 for the ask/advisory path).

Scope note: `builtin-task-postuse` emits a different shape (`additionalContext`
plus a top-level `"continue": True` field) and is the *only* such caller, so it
is intentionally NOT covered here — extracting a helper for a single call site
would add indirection without removing duplication (DRY rule-of-three / YAGNI).

Byte-identity guarantee: `format_decision` inserts keys in the exact order the
hand-rolled form used (`hookEventName`, `permissionDecision`,
`permissionDecisionReason`), and `emit_decision` serializes via `json.dump`
with no `separators=` kwarg — i.e. the default `(', ', ': ')` (note the
spaces) — plus a trailing `"\\n"`, so the bytes written are identical to every
pre-extraction call site. A re-implementation in another language must use the
spaced form, not the compact `(',', ':')`.
"""
from __future__ import annotations

import json
import sys
from typing import Optional, TextIO


def format_decision(
    decision: str,
    reason: str,
    event_name: str = "PreToolUse",
) -> dict:
    """Return the `hookSpecificOutput` decision dict (no I/O).

    Args:
      decision: "ask" or "deny". "allow" is never emitted — a silent pass is
        signaled by writing no JSON at all (the caller just returns 0).
      reason: the `permissionDecisionReason` text shown to the agent.
      event_name: the `hookEventName`. Defaults to "PreToolUse" (every current
        caller); parameterized so a future UserPromptSubmit caller can reuse it.

    Returns:
      The dict ready for `json.dump`. Key order is fixed to match the
      pre-extraction hand-rolled form for byte-identical serialization.
    """
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }


def emit_decision(
    decision: str,
    reason: str,
    event_name: str = "PreToolUse",
    stream: Optional[TextIO] = None,
) -> None:
    """Write the `permissionDecision` JSON to stdout (+ trailing newline).

    Mirrors `block_message.emit_block`: `stream` is injectable for testing and
    defaults to `sys.stdout`. Does NOT exit — the caller owns the exit code.
    """
    out = stream if stream is not None else sys.stdout
    json.dump(format_decision(decision, reason, event_name), out)
    out.write("\n")
