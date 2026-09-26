#!/usr/bin/env python3
"""PreToolUse guard: look at remote state before mutating again after a refusal.

Issue #1488. A Bash call running a CLI that creates a branch, pushes it and
opens a pull request was recorded as `user-rejected` with the runtime's fixed
refusal sentence. The pull request existed, created at the time of that call.
The agent read the refusal as "nothing happened" and moved on.

A refused or interrupted call may still have run. This hook reads the session's
refusals and, until probes have looked at every surface the newest refused
MUTATING call could have changed, asks before the next mutating call.

  1. the pending call is mutating and is not itself a probe;
  2. an earlier mutating call in this session was refused: `toolDenialKind`
     `user-rejected` with the refusal sentence, or `interrupted`;
  3. since that refusal, some surface of it is still unprobed and no mutating
     call has run.

All three -> `permissionDecision: "ask"`.

MUTATING is `_mutating_call.is_mutating_call`.

SURFACES a refused call could have changed: local git, a remote ref, GitHub,
local files, kubectl / aws / docker, or one MCP server. A CLI the gate does not
know (the incident's branch-push-PR tool) could have changed any of local git,
a remote ref and GitHub, so all three must be probed.

A PROBE is a call that ran and reads state, and it covers the surfaces it
reads: a read-only `git` command covers local git (`git ls-remote` also a
remote ref), a read-only `gh` command covers GitHub and remote refs, any
read-only Bash command and the Read / Grep / Glob tools cover local files, and
an MCP call with no write verb covers its own server. A probe aimed at a
different directory or repository than the refused call covers nothing.

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
    _NONWRITING_REDIRECT_RE,
    _SUBSTITUTION_RE,
    READONLY_ANY_ARGS,
    _segment_is_readonly,
    _subcommand,
    bash_is_readonly,
    has_state_changing_redirect,
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
# Refusals pending a probe; bounded because the state crosses the cursor file.
MAX_PENDING = 16
SUMMARY_MAX_CHARS = 200

PATH_FIELDS = {"Edit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path"}
_NEEDLES = (b'"tool_use"', b'"tool_result"')

LOCAL_GIT = "local git"
REMOTE_REF = "remote ref"
GITHUB = "GitHub"
LOCAL_FILES = "local files"
UNKNOWN_CLI_SURFACES = (LOCAL_GIT, REMOTE_REF, GITHUB)

_GIT_REMOTE_SUBCOMMANDS = frozenset({"push", "fetch", "pull", "clone", "remote",
                                     "ls-remote", "submodule"})
_OWN_SURFACE_BINARIES = frozenset({"kubectl", "aws", "docker"})
# Writers whose whole effect lands in local files, so a local read shows it.
_LOCAL_FILE_WRITERS = frozenset({
    "rm", "rmdir", "mv", "cp", "mkdir", "touch", "ln", "chmod", "chown", "tee",
    "truncate", "install", "patch", "tar", "unzip", "zip", "gzip", "gunzip",
    "sed", "sort", "uniq", "find", "yq", "cd", "set",
}) | READONLY_ANY_ARGS
_FILE_READ_TOOLS = frozenset({"Read", "Grep", "Glob"})
_LEADING_CD_RE = re.compile(r"""^\s*cd\s+("[^"]*"|'[^']*'|[^\s;&|]+)\s*(?:&&|;)""")


def is_mutating(tool_name: str, tool_input: dict) -> bool:
    return is_mutating_call(tool_name, tool_input)


def _bash_command(tool_name: str, tool_input: dict) -> str:
    command = tool_input.get("command") if tool_name == "Bash" else None
    return command if isinstance(command, str) else ""


def _segments(command: str) -> list[list[str]]:
    return [list(seg) for seg in iter_command_starts(safe_tokenize(command)) if seg]


def _mcp_server(tool_name: str) -> str:
    return "MCP " + tool_name.split("__")[1] if tool_name.count("__") >= 2 else tool_name


def _segment_surfaces(argv: list[str]) -> set[str]:
    binary = argv[0].rsplit("/", 1)[-1]
    if binary == "git":
        sub = _subcommand("git", argv)
        return {LOCAL_GIT} if sub and sub not in _GIT_REMOTE_SUBCOMMANDS else {LOCAL_GIT, REMOTE_REF}
    if binary == "gh":
        return {GITHUB}
    if binary in _OWN_SURFACE_BINARIES:
        return {binary}
    if binary in _LOCAL_FILE_WRITERS:
        return {LOCAL_FILES}
    return set(UNKNOWN_CLI_SURFACES)


def mutation_surfaces(tool_name: str, tool_input: dict) -> set[str]:
    """What a mutating call could have changed (see SURFACES)."""
    if tool_name in PATH_FIELDS:
        return {LOCAL_FILES}
    if tool_name.startswith("mcp__"):
        return {_mcp_server(tool_name)}
    command = _bash_command(tool_name, tool_input)
    if not command:
        return set(UNKNOWN_CLI_SURFACES)
    surfaces: set[str] = set()
    if has_state_changing_redirect(_NONWRITING_REDIRECT_RE.sub(" ", command)):
        surfaces.add(LOCAL_FILES)
    if _SUBSTITUTION_RE.search(command):
        surfaces.update(UNKNOWN_CLI_SURFACES)
    for argv in _segments(command):
        if not _segment_is_readonly(argv):
            argv = strip_prefix(argv) or argv
            surfaces |= _segment_surfaces(argv)
    return surfaces or set(UNKNOWN_CLI_SURFACES)


def probe_surfaces(tool_name: str, tool_input: dict) -> set[str]:
    """What a call reads, if it reads state and writes nothing (see PROBE)."""
    if tool_name.startswith("mcp__"):
        return set() if mcp_is_mutating(tool_name) else {_mcp_server(tool_name)}
    if tool_name in _FILE_READ_TOOLS:
        return {LOCAL_FILES}
    command = _bash_command(tool_name, tool_input)
    if not command or not bash_is_readonly(command):
        return set()
    surfaces = {LOCAL_FILES}
    for argv in _segments(command):
        argv = strip_prefix(argv) or argv
        binary = argv[0].rsplit("/", 1)[-1]
        if binary == "git":
            surfaces.add(LOCAL_GIT)
            if _subcommand("git", argv) == "ls-remote":
                surfaces.add(REMOTE_REF)
        elif binary == "gh":
            surfaces |= {GITHUB, REMOTE_REF}
        elif binary in _OWN_SURFACE_BINARIES:
            surfaces.add(binary)
    return surfaces


def is_probe(tool_name: str, tool_input: dict) -> bool:
    return bool(probe_surfaces(tool_name, tool_input))


def call_target(tool_name: str, tool_input: dict) -> str | None:
    """Where the call is aimed: `dir:<path>` from a leading `cd` or `git -C`,
    `repo:<owner/name>` from `--repo` / `-R`, or None when it names neither."""
    command = _bash_command(tool_name, tool_input)
    if not command:
        return None
    if match := _LEADING_CD_RE.match(command):
        return "dir:" + match.group(1).strip("\"'").rstrip("/")
    for argv in _segments(command):
        for i, tok in enumerate(argv[:-1]):
            if argv[0].rsplit("/", 1)[-1] == "git" and tok == "-C":
                return "dir:" + argv[i + 1].rstrip("/")
            if tok in ("--repo", "-R"):
                return "repo:" + argv[i + 1]
        for tok in argv:
            if tok.startswith("--repo="):
                return "repo:" + tok.split("=", 1)[1]
    return None


def same_target(armed_target: str | None, probe_target: str | None) -> bool:
    # Targets of different kinds (a directory and a repository) cannot be
    # compared, and a call that names none runs wherever the session is.
    if armed_target is None or probe_target is None:
        return True
    if armed_target.split(":", 1)[0] != probe_target.split(":", 1)[0]:
        return True
    return armed_target == probe_target


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
    return {"recent": [], "armed": []}


def decode_state(raw: dict) -> dict:
    """A cursor state in an older shape is refused, costing one re-scan."""
    if not isinstance(raw.get("armed"), list) or not isinstance(raw.get("recent"), list):
        raise ValueError("stale state shape")
    return raw


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
        mutating = is_mutating(name, tool_input)
        state["recent"].append({
            "id": use_id, "uuid": uuid,
            "mutating": mutating,
            "needs": sorted(mutation_surfaces(name, tool_input)) if mutating else [],
            "covers": sorted(probe_surfaces(name, tool_input)),
            "target": call_target(name, tool_input),
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
                state["armed"].append({"summary": use["summary"], "kind": kind,
                                       "unprobed": use["needs"], "target": use["target"]})
                del state["armed"][:-MAX_PENDING]
        elif ev.get("toolDenialKind"):
            continue  # stopped before it ran: neither a probe nor a mutation
        elif use["mutating"]:
            state["armed"] = []
        elif use["covers"] and state["armed"]:
            state["armed"] = [e for e in (_probe_entry(e, use) for e in state["armed"]) if e]


def _probe_entry(entry: dict, use: dict) -> dict | None:
    """The pending refusal after this probe; None once every surface is covered."""
    if not same_target(entry.get("target"), use["target"]):
        return entry
    unprobed = [s for s in entry.get("unprobed", []) if s not in use["covers"]]
    if not unprobed:
        return None
    if len(unprobed) < len(entry.get("unprobed", [])):
        return {**entry, "unprobed": unprobed}
    return entry


def reduce_event(state: dict, ev: dict) -> None:
    """Fold one transcript record into the state."""
    msg = ev.get("message")
    if not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
        return
    if msg.get("role") == "assistant":
        _note_tool_uses(state, ev, msg)
    elif msg.get("role") == "user":
        _note_results(state, ev, msg)


def build_reason(armed: list[dict], tool_name: str) -> str:
    latest = armed[-1]
    unprobed = sorted({s for e in armed for s in (e.get("unprobed") or UNKNOWN_CLI_SURFACES)})
    earlier = f" (and {len(armed) - 1} earlier refused call(s))" if len(armed) > 1 else ""
    return format_block(
        rule_name="rejected call probe",
        why=(
            f"an earlier mutating call was refused ({latest['kind']}) and may "
            f"still have run: {latest['summary']}{earlier}. Not yet probed since: "
            f"{', '.join(unprobed)}; and "
            f"this `{tool_name}` call mutates again"
        ),
        correct_path=(
            "look before acting: probe each surface listed above, in the "
            "directory or repository that call used (local git: git status / "
            "git log; remote ref: git ls-remote or a gh read; GitHub: gh pr "
            "list / gh issue list; local files: ls or Read; an MCP server: a "
            "read-only call on it), then retry. Approve here only if the user "
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
            decode=decode_state,
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
