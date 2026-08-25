#!/usr/bin/env python3
"""PreToolUse(Bash|Agent) guard: surface an approval prompt from the 2nd
delegation target created in one turn.

A delegation request names its targets. When a turn starts creating more of
them than the request named, the extra ones come from somewhere else — in the
motivating incident, from adjacent items that happened to sit in the same
document the named target was read from. That expansion reads as thoroughness
at authoring time, which is why the rule layer does not catch it: three clauses
that forbid widening the requested scope were all loaded and none fired.

Detection: counts SUCCESSFUL delegation-target creations already in the current
turn (workspace-creation shell commands and `Agent` tool calls). If the turn
already has one and the intercepted call is another, emit permissionDecision
"ask" so the user sees the fan-out growing and decides.

No opt-out marker and no environment bypass. An agent-attachable marker would
let the agent self-bypass the gate it is meant to enforce — the same contract
`pre-merge-approval-gate` states for `# merge-approval:ack`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    is_help_invocation,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)
from _transcript import (  # type: ignore[import-not-found]  # noqa: E402
    load_current_turn,
    read_last_user_message,
)
from block_message import format_block  # type: ignore[import-not-found]  # noqa: E402

# Tools whose invocation creates a delegation target.
AGENT_TOOL = "Agent"

# cmux flags that consume one following argument. Needed so `--name --help`
# style values are not mistaken for a help invocation.
CMUX_VALUE_FLAGS = frozenset({
    "--name", "--cwd", "--command", "--window", "--title", "--id",
    "--model", "--agent", "--workspace",
})

# A rehearsal prints usage or plans nothing; either way no worker starts.
REHEARSAL_FLAGS = frozenset({"--dry-run", "--dryrun"})

MAX_UTTERANCE_CHARS = 160


def is_workspace_creation(argv: list[str]) -> bool:
    """True iff the argv segment creates a cmux workspace.

    Both the canonical `cmux workspace create` and the legacy alias
    `cmux new-workspace` count — the alias still works indefinitely, so
    matching only the canonical form would leave the observed incident's exact
    command unmatched.
    """
    argv = strip_prefix(argv)
    if len(argv) < 2 or _Path(argv[0]).name != "cmux":
        return False

    if argv[1] == "new-workspace":
        rest = argv[2:]
    elif argv[1] == "workspace" and len(argv) >= 3 and argv[2] == "create":
        rest = argv[3:]
    else:
        return False

    if is_help_invocation(rest, CMUX_VALUE_FLAGS):
        return False
    return not any(tok in REHEARSAL_FLAGS for tok in rest)


def _command_creates_target(command: str) -> bool:
    if not command.strip():
        return False
    tokens = safe_tokenize(command)
    if not tokens:
        return False
    return any(is_workspace_creation(argv) for argv in iter_command_starts(tokens))


def count_targets_in_turn(turn: list[dict]) -> int:
    """Number of SUCCESSFUL delegation-target creations already in this turn.

    A failed call created no worker, so it must not push the count toward the
    prompt. Correlation is tool_use `id` <-> tool_result `tool_use_id`,
    mirroring pr-claim-mutation-gate. A tool_use with no result yet counts as
    success, biasing toward asking — the whole point of the gate is to be seen
    while the fan-out is still growing.
    """
    candidates: list[str] = []
    untracked = 0
    result_is_error: dict[str, bool] = {}

    for ev in turn:
        msg = ev.get("message", {})
        if not isinstance(msg, dict) or ev.get("isSidechain"):
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        role = msg.get("role")
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "tool_result":
                tid = block.get("tool_use_id")
                if isinstance(tid, str):
                    result_is_error[tid] = block.get("is_error") is True
                continue
            if kind != "tool_use" or role != "assistant":
                continue
            name = block.get("name", "") or ""
            if name == AGENT_TOOL:
                hit = True
            elif name == "Bash":
                inp = block.get("input", {})
                cmd = inp.get("command", "") if isinstance(inp, dict) else ""
                hit = _command_creates_target(cmd)
            else:
                hit = False
            if not hit:
                continue
            tid = block.get("id")
            if isinstance(tid, str):
                candidates.append(tid)
            else:
                untracked += 1

    succeeded = sum(1 for tid in candidates if result_is_error.get(tid) is not True)
    return succeeded + untracked


def _first_line(text: str | None) -> str:
    if not text:
        return "(the turn's request could not be read)"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            if len(stripped) > MAX_UTTERANCE_CHARS:
                return stripped[:MAX_UTTERANCE_CHARS] + "…"
            return stripped
    return "(the turn's request could not be read)"


def build_reason(ordinal: int, utterance: str) -> str:
    return format_block(
        rule_name="fan-out scope",
        why=f"this is delegation target #{ordinal} in one turn — the request "
            f"asked for: {utterance}",
        correct_path="approve only the targets that map to a span of that "
            "request; a target with no span in it is scope you added, not "
            "scope you were given",
        # No agent-attachable bypass by design — a self-bypass would defeat the
        # gate.
        bypass_env=None,
        reference="CLAUDE.md → Scope Discipline / Delivering work; "
            "hooks/preflight-gate/fan-out-scope-gate/spec.md",
    )


@fail_open
def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed input

    tool_name = payload.get("tool_name")
    if tool_name == AGENT_TOOL:
        pass
    elif tool_name == "Bash":
        command = payload.get("tool_input", {}).get("command", "") or ""
        if not _command_creates_target(command):
            return 0
    else:
        return 0

    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return 0

    prior = count_targets_in_turn(load_current_turn(transcript_path))
    if prior < 1:
        return 0

    utterance = _first_line(read_last_user_message(transcript_path))
    emit_decision("ask", build_reason(prior + 1, utterance))
    return 0


if __name__ == "__main__":
    sys.exit(main())
