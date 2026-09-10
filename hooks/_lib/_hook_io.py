"""Standard decision-emit format for praxis PreToolUse and Stop hooks.

PreToolUse `permissionDecision` shape: issue #470. Stop advisory/block
shapes: issue #647 H3 (see the Stop-event emitters section below).

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


# --- PreToolUse advisory context (issue #1265) ------------------------------
#
# A PreToolUse hook that exits 0 has exactly one channel that reaches the model:
# `hookSpecificOutput.additionalContext`. Bare stderr at exit 0 goes to the
# debug log, so an advisory written only there fires correctly and is
# indistinguishable, to the actor, from a hook that does not exist.
#
# Callers keep writing the same text to stderr as well. The two channels carry
# different jobs and neither replaces the other: `classify_decision` derives the
# `advise` grade from a non-empty stderr (`_fire_ledger.py`), so an advisory
# that moved wholesale to stdout would be recorded as `pass` and disappear from
# the fire-rate metric.
#
# Three hooks hand-roll this dict today — `pipefail-advisory`,
# `foreground-poll-loop-guard` and `anchor-comment-gate` — which is the
# rule-of-three that put it here; they keep their own copies until a change
# needs them. The `builtin-task-postuse` shape in the scope note above stays
# out: it carries a top-level `continue` field alongside the context and is
# still a single call site.


def format_additional_context(
    context: str,
    event_name: str = "PreToolUse",
) -> dict:
    """Return the bare `additionalContext` dict (no I/O)."""
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": context,
        }
    }


def emit_additional_context(
    context: str,
    event_name: str = "PreToolUse",
    stream: Optional[TextIO] = None,
) -> None:
    """Write the `additionalContext` JSON to stdout (+ trailing newline).

    Does NOT exit, and does NOT write stderr — the caller owns both, because
    the stderr line is what the fire ledger grades the fire on.
    """
    out = stream if stream is not None else sys.stdout
    json.dump(format_additional_context(context, event_name), out)
    out.write("\n")


# --- PreToolUse input rewrite (issue #1334) ---------------------------------
#
# A PreToolUse hook may hand the harness a corrected tool input instead of
# blocking: `hookSpecificOutput.updatedInput` replaces the input and the call
# proceeds. Measured on Claude Code 2.1.266 — the harness ran the hook's
# command, not the model's.
#
# `context` is not optional in this API. The measurement also showed the
# `tool_use` record keeps the ORIGINAL command, so a rewrite with no
# additionalContext is invisible to everyone downstream: the actor, a reviewer
# reading the transcript, and the praxis hooks that scan prior calls all see a
# command that never ran. The context line is what makes the correction
# legible, which is why it rides in the same object.


def format_updated_input(
    updated_input: dict,
    context: str,
    event_name: str = "PreToolUse",
) -> dict:
    """Return the `hookSpecificOutput` input-rewrite dict (no I/O)."""
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "updatedInput": updated_input,
            "additionalContext": context,
        }
    }


def emit_updated_input(
    updated_input: dict,
    context: str,
    event_name: str = "PreToolUse",
    stream: Optional[TextIO] = None,
) -> None:
    """Write the `updatedInput` JSON to stdout (+ trailing newline).

    Does NOT exit — the caller returns 0, since a rewrite lets the call through.
    """
    out = stream if stream is not None else sys.stdout
    json.dump(format_updated_input(updated_input, context, event_name), out)
    out.write("\n")


# --- Stop-event emitters (issue #647 H3) ------------------------------------
#
# completion-verify Stop hooks signal in two tiers, both as stdout JSON so the
# whole role shares one mechanism (the shell hooks already emit
# `{decision: "block", reason}` via jq):
#
#   advisory  → `{"systemMessage": ...}` + exit 0. Shown to the user in the
#               transcript; does NOT block the stop and is NOT fed to the
#               model. (stderr with exit 0 only reaches the debug log, so the
#               pre-#647 stderr advisories were effectively invisible.)
#   block     → `{"decision": "block", "reason": ...}` + exit 0. Blocks the
#               stop; `reason` is fed to the model so it can self-correct.
#
# Callers own the exit code (always 0 for both shapes — blocking is carried by
# the JSON `decision` field, not the exit code).


def format_stop_advisory(message: str) -> dict:
    """Return the non-blocking Stop advisory dict (no I/O)."""
    return {"systemMessage": message}


def format_stop_block(reason: str) -> dict:
    """Return the blocking Stop decision dict (no I/O).

    Key order matches the shell siblings' `jq -n '{decision: ..., reason: ...}'`
    output for cross-implementation consistency.
    """
    return {"decision": "block", "reason": reason}


def emit_stop_advisory(message: str, stream: Optional[TextIO] = None) -> None:
    """Write the Stop `systemMessage` JSON to stdout (+ trailing newline)."""
    out = stream if stream is not None else sys.stdout
    json.dump(format_stop_advisory(message), out)
    out.write("\n")


def emit_stop_block(reason: str, stream: Optional[TextIO] = None) -> None:
    """Write the Stop `{decision: block}` JSON to stdout (+ trailing newline)."""
    out = stream if stream is not None else sys.stdout
    json.dump(format_stop_block(reason), out)
    out.write("\n")
