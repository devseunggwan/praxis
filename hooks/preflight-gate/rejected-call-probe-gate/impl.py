#!/usr/bin/env python3
"""PreToolUse guard: look at remote state before mutating again after a refusal.

Issue #1488. A Bash call running a CLI that creates a branch, pushes it and
opens a pull request was recorded as `user-rejected` with the runtime's fixed
refusal sentence. The pull request existed, created at the time of that call.
The agent read the refusal as "nothing happened" and moved on.

A refused or interrupted call may still have run. This hook reads the session's
refusals and, when the newest refused MUTATING call has not been followed by a
probe of state, asks before the next mutating call.

  1. the pending call is mutating and is not itself a probe;
  2. an earlier mutating call in this session was refused: `toolDenialKind`
     `user-rejected` with the refusal sentence, or `interrupted`;
  3. since that refusal, no probe has run and no mutating call has run.

All three -> `permissionDecision: "ask"`.

MUTATING is `_mutating_call.is_mutating_call`, applied after peeling leading
`cd <dir> &&` segments: the shared allowlist has no `cd`, so without the peel
`cd <repo> && git status` reads as a mutation.

A PROBE is a call that ran and reads state (the maintainer's-call default, see
spec): a read-only Bash command with at least one `git` / `gh` / `kubectl` /
`aws` / `docker` segment (the subcommand-gated families of the shared
allowlist), or an MCP call whose name carries no write verb. `ls` and `cat`
read local files only and are not probes. Any probe disarms, whatever surface
the refused call touched: in the incident the CLI's surface was a branch, a
push and a pull request, and `git status` / `gh pr list` are what saw them.

NOT A REFUSAL: a hook block (`permission-rule`) or a declined hook ask
(`user-rejected` without the refusal sentence). Both stop the call before it
runs, so there is nothing to look for.

DISARM on a mutating call that ran: the operator approved this very ask, or
chose to act; asking on every later mutation would be noise.

INDETERMINATE SCAN IS SILENT: the reach is every mutating call.

TIER: ask, no bypass marker. Approving the ask is the operator's decision to
act without looking.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _compound import compound_cascade_hint  # type: ignore[import-not-found]  # noqa: E402
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _mutating_call import (  # type: ignore[import-not-found]  # noqa: E402
    READONLY_SUBCOMMANDS,
    bash_is_readonly,
    is_mutating_call,
    mcp_is_mutating,
)
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _shell_tokenize import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)
from _transcript import (  # type: ignore[import-not-found]  # noqa: E402
    REJECTION_DENIAL_KIND,
    REJECTION_PHRASE,
    TranscriptReadError,
    scan_cursor_path,
    scan_transcript_resumable,
)
from block_message import format_block  # type: ignore[import-not-found]  # noqa: E402

_HOOK_NAME = "rejected-call-probe-gate"

INTERRUPTED_DENIAL_KIND = "interrupted"
SCAN_MAX_BYTES = 20 * 1024 * 1024
# A refusal resolves against the tool_use it refused, in the same or the
# previous assistant turn, so a short ring suffices.
RECENT_TOOL_USES = 32
SUMMARY_MAX_CHARS = 200

PROBE_BINARIES = frozenset(READONLY_SUBCOMMANDS)
PATH_FIELDS = {"Edit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path"}

_LEADING_CD_RE = re.compile(r"""^\s*cd\s+(?:"[^"]*"|'[^']*'|[^\s;&|]+)\s*(?:&&|;)\s*""")
_NEEDLES = (b'"tool_use"', b'"tool_result"')


def _peel_cd(command: str) -> str:
    while match := _LEADING_CD_RE.match(command):
        command = command[match.end():]
    return command


def _command(tool_name: str, tool_input: dict) -> str:
    command = tool_input.get("command") if tool_name == "Bash" else None
    return _peel_cd(command) if isinstance(command, str) else ""


def is_mutating(tool_name: str, tool_input: dict) -> bool:
    if tool_name == "Bash":
        return is_mutating_call("Bash", {"command": _command(tool_name, tool_input)})
    return is_mutating_call(tool_name, tool_input)


def is_probe(tool_name: str, tool_input: dict) -> bool:
    """Whether the call reads state (see PROBE in the module docstring)."""
    if tool_name.startswith("mcp__"):
        return not mcp_is_mutating(tool_name)
    command = _command(tool_name, tool_input)
    if not command or not bash_is_readonly(command):
        return False
    for start in iter_command_starts(safe_tokenize(command)):
        argv = strip_prefix(list(start))
        if argv and argv[0].rsplit("/", 1)[-1] in PROBE_BINARIES:
            return True
    return False


def summarize(tool_name: str, tool_input: dict) -> str:
    """A short, single-line description of a call for the ask reason."""
    command = tool_input.get("command") if tool_name == "Bash" else None
    if isinstance(command, str):
        text = command
    elif isinstance(tool_input.get(PATH_FIELDS.get(tool_name, "")), str):
        text = tool_input[PATH_FIELDS[tool_name]]
    else:
        text = ""
    text = " ".join(text.split())[:SUMMARY_MAX_CHARS]
    return f"{tool_name}: {text}" if text else tool_name


# ---------------------------------------------------------------------------
# Transcript reduction
# ---------------------------------------------------------------------------


def new_state() -> dict:
    return {"recent": [], "armed": None}


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def refusal_kind(ev: dict, block: dict) -> str | None:
    """The denial kind when the result is a refusal that may hide a run."""
    if block.get("is_error") is not True:
        return None
    kind = ev.get("toolDenialKind")
    if kind == INTERRUPTED_DENIAL_KIND:
        return kind
    if kind == REJECTION_DENIAL_KIND and REJECTION_PHRASE in _result_text(block):
        return kind
    return None


def _note_tool_uses(state: dict, ev: dict, msg: dict) -> None:
    uuid = ev.get("uuid") if isinstance(ev.get("uuid"), str) else ""
    for block in msg.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        use_id, name = block.get("id"), block.get("name")
        tool_input = block.get("input")
        if not isinstance(use_id, str) or not isinstance(name, str):
            continue
        if not isinstance(tool_input, dict):
            tool_input = {}
        state["recent"].append({
            "id": use_id, "uuid": uuid,
            "mutating": is_mutating(name, tool_input),
            "probe": is_probe(name, tool_input),
            "summary": summarize(name, tool_input),
        })
    del state["recent"][:-RECENT_TOOL_USES]


def _note_results(state: dict, ev: dict, msg: dict) -> None:
    source = ev.get("sourceToolAssistantUUID")
    for block in msg.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        use_id = block.get("tool_use_id")
        use = next((
            u for u in reversed(state["recent"])
            if u["id"] == use_id
            and not (isinstance(source, str) and source and u["uuid"] != source)
        ), None)
        if use is None:
            continue
        kind = refusal_kind(ev, block)
        if kind:
            if use["mutating"]:
                state["armed"] = {"summary": use["summary"], "kind": kind}
        elif ev.get("toolDenialKind"):
            continue  # stopped before it ran: neither a probe nor a mutation
        elif use["probe"] or use["mutating"]:
            state["armed"] = None


def reduce_event(state: dict, ev: dict) -> None:
    """Fold one transcript record into the state."""
    msg = ev.get("message")
    if not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
        return
    if msg.get("role") == "assistant":
        _note_tool_uses(state, ev, msg)
    elif msg.get("role") == "user":
        _note_results(state, ev, msg)


def build_reason(armed: dict, tool_name: str) -> str:
    return format_block(
        rule_name="rejected call probe",
        why=(
            f"an earlier mutating call was refused ({armed['kind']}) and may "
            f"still have run: {armed['summary']}. No probe of state has run "
            f"since, and this `{tool_name}` call mutates again"
        ),
        correct_path=(
            "look before acting: run a read-only probe of the surface that call "
            "touched (git status, git log, gh pr list, a remote ref lookup, a "
            "read-only MCP query), then retry. Approve here only if the user "
            "has chosen to act without looking."
        ),
        bypass_env=None,
        reference="issue #1488",
    )


@fail_open
def main() -> int:
    """Hook entry point: read the payload, fold refusals, emit the verdict."""
    payload = read_payload()
    if payload is None:
        return 0
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
        return 0

    # Cheapest discriminator first: a read or a probe is never held.
    if not is_mutating(tool_name, tool_input) or is_probe(tool_name, tool_input):
        return 0

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return 0
    try:
        state, complete = scan_transcript_resumable(
            transcript_path,
            scan_cursor_path(_HOOK_NAME, payload.get("session_id")),
            new_state,
            reduce_event,
            needle=_NEEDLES,
            max_bytes=SCAN_MAX_BYTES,
        )
    except TranscriptReadError:
        return 0
    if not complete or not state["armed"]:
        return 0

    reason = build_reason(state["armed"], tool_name)
    command = tool_input.get("command") if tool_name == "Bash" else None
    if isinstance(command, str):
        reason += compound_cascade_hint(command)
    emit_decision("ask", reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
