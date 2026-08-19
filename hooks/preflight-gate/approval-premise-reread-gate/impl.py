#!/usr/bin/env python3
"""PreToolUse guard: an irreversible production call re-reads its approval premise.

Two failures this gate exists for, both observed in one session minutes apart
(issue #1043), neither reachable by any existing hook:

1. PREMISE_DISSOLVED — approval was granted on a stated justification ("we have
   to run it to see whether the failing step passes"). Between the approval and
   the execution, a direct query showed the premise no longer held, and that
   observation was written up in the same turn. The call fired anyway.

2. COHORT_INHERITED — a blast radius measured empirically on the first target of
   a cohort was inherited by the rest without re-measurement. The third target
   was a different failure mode whose deletion steps had never executed, so two
   of the three axes were unmeasured for it.

Detection: fires on a mutating call (MCP tool classified as a mutation, or a
Bash segment invoking one of the mutating wrappers) whose arguments carry a
production phase marker. On a match it emits permissionDecision "ask" carrying
the two questions, rather than a hard block — the gate cannot decide whether the
premise still holds; only the operator can, and forcing that decision to be made
out loud at the call site is the whole mechanism.

Known ceiling, stated here rather than discovered later: this gate checks that an
approval record exists, names a justification, and names THIS target. It cannot
check that the justification is TRUE. Routing a guess through a schema check
converts it into something that reads like independent confirmation, which is its
own documented failure mode — see `Own-greencheck and SUT-comment are not
evidence` in the rules. The reach here is partial by construction.

Opt-out: embed `# approval-premise:ack <one-line premise re-read>` in the Bash
command, or pass `approval_premise_ack` in an MCP call's arguments. The marker is
not a bypass token — it asserts that the premise was re-read and states what it
now says. Attaching it without having done so is a false attestation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402

ACK_MARKER = "# approval-premise:ack"
ACK_ARG = "approval_premise_ack"

# A production phase marker can arrive as a flag value, an MCP argument, or a
# substring of a target identifier. Kept literal rather than regex so the match
# set is auditable.
PROD_MARKERS = ("phase=prod", "--phase prod", '"prod"', "'prod'", "prod-")

# Mutating MCP verbs. Read-only calls are out of scope entirely — the gate must
# never fire on a query, or it becomes the noise it is meant to replace.
MUTATING_MCP_VERBS = (
    "trigger", "clear", "mark", "delete", "drop", "create", "update",
    "send", "upload", "invite", "kick", "archive", "rename", "set_",
)


def _is_mutating_mcp(tool_name: str) -> bool:
    if not tool_name.startswith("mcp__"):
        return False
    leaf = tool_name.rsplit("__", 1)[-1]
    return any(verb in leaf for verb in MUTATING_MCP_VERBS)


def _carries_prod_marker(blob: str) -> bool:
    return any(marker in blob for marker in PROD_MARKERS)


def _message(tool_name: str, target: str) -> str:
    return (
        "⚠️ APPROVAL-PREMISE-REREAD required\n\n"
        f"Call: {tool_name}\n"
        f"Target: {target or '(unnamed)'}\n\n"
        "Answer both before this executes:\n\n"
        "  1. PREMISE — restate, in one line, the justification the approval was\n"
        "     granted on. Has anything observed SINCE that approval made it false?\n"
        "     If it has, this is not an approved action any more; re-ask.\n\n"
        "  2. TARGET — was the blast radius measured on THIS target, or inherited\n"
        "     from another member of the same cohort? An enumeration measured on\n"
        "     target A is not evidence about target B.\n\n"
        "     The enumeration includes the outward side-effect axis (mail, webhook,\n"
        "     channel post, customer notification), not only the data surfaces.\n\n"
        "Reference: hooks/preflight-gate/approval-premise-reread-gate/spec.md"
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail open — a malformed payload must not block the session

    tool_name = payload.get("tool_name", "") or ""
    tool_input = payload.get("tool_input", {}) or {}
    blob = json.dumps(tool_input, ensure_ascii=False)

    if tool_input.get(ACK_ARG) or ACK_MARKER in blob:
        return 0

    if tool_name == "Bash":
        command = tool_input.get("command", "") or ""
        if ACK_MARKER in command:
            return 0
        if not _carries_prod_marker(command):
            return 0
        target = command.strip().splitlines()[0][:120]
    elif _is_mutating_mcp(tool_name):
        if not _carries_prod_marker(blob):
            return 0
        target = str(tool_input.get("dag_id") or tool_input.get("conf") or "")[:120]
    else:
        return 0

    emit_decision("ask", _message(tool_name, target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
