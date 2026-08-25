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

One call can also carry the whole fan-out by itself. The motivating incident
created three workspaces from a single Bash call — a shell function invoked
three times — so a per-call counter would have stayed silent on the very
command it was built for. A command that can create more than one target (two
literal invocations, or one inside a loop or function body) therefore asks on
its own, before any prior target exists.

No opt-out marker and no environment bypass. An agent-attachable marker would
let the agent self-bypass the gate it is meant to enforce — the same contract
`pre-merge-approval-gate` states for `# merge-approval:ack`.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    is_help_invocation,
    iter_command_starts,
    safe_tokenize,
    strip_heredoc_bodies,
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


MAX_SUBST_DEPTH = 4


def _iter_command_texts(command: str, depth: int = 0):
    """Yield `command` and the inner text of every ACTIVE `$( ... )` / backtick.

    A creation is routinely written as `WS=$(cmux workspace create ...)`, and
    the tokenizer coalesces a substitution run into one token — so a scan of
    the outer text alone sees no command start there at all. That is how the
    motivating incident's own command read as zero targets.

    Quoting decides whether a substitution is a substitution. Inside single
    quotes, and after a backslash, `$(` and a backtick are literal text that
    starts no process, so recursing into them would count a workspace that
    never gets created — `printf '%s' '$(cmux workspace create ...)'` prints a
    string. Double quotes do not disable either form, so those are followed.

    A heredoc body is data for the same reason. A script written with
    `python3 - <<'PY' ... PY` puts arbitrary text where the per-line scan
    expects commands, so a fixture that merely spells out a creation is
    counted as one — which is exactly how this gate first fired on a call
    that edited its own test file.
    """
    command = strip_heredoc_bodies(command)
    yield command
    if depth >= MAX_SUBST_DEPTH:
        return
    for inner in _active_substitutions(command):
        yield from _iter_command_texts(inner, depth + 1)


def _active_substitutions(command: str) -> list[str]:
    """Inner text of each `$( ... )` / backtick span that shell would expand."""
    spans: list[str] = []
    i, n = 0, len(command)
    in_single = in_double = False
    while i < n:
        ch = command[i]
        if in_single:
            if ch == "'":
                in_single = False
            i += 1
            continue
        if ch == "\\":
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = True
            i += 1
            continue
        if ch == '"':
            in_double = not in_double
            i += 1
            continue
        if command.startswith("$(", i):
            end = _closing_paren(command, i + 2)
            if end is not None:
                spans.append(command[i + 2:end])
                i = end + 1
                continue
        elif ch == "`":
            end = _closing_backtick(command, i + 1)
            if end is not None:
                spans.append(command[i + 1:end])
                i = end + 1
                continue
        i += 1
    return spans


def _closing_paren(command: str, start: int) -> int | None:
    """Index of the `)` closing a `$(` opened before `start`, or None.

    Quote state is tracked here too, so a parenthesis inside a quoted string
    (`$(echo ")")`) does not close the span early.
    """
    level = 1
    i, n = start, len(command)
    in_single = in_double = False
    while i < n:
        ch = command[i]
        if in_single:
            if ch == "'":
                in_single = False
            i += 1
            continue
        if ch == "\\":
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = True
        elif ch == '"':
            in_double = not in_double
        elif not in_double:
            if command.startswith("$(", i):
                level += 1
                i += 2
                continue
            if ch == "(":
                level += 1
            elif ch == ")":
                level -= 1
                if not level:
                    return i
        i += 1
    return None


def _closing_backtick(command: str, start: int) -> int | None:
    i, n = start, len(command)
    while i < n:
        if command[i] == "\\":
            i += 2
            continue
        if command[i] == "`":
            return i
        i += 1
    return None


def _count_creation_segments(command: str) -> int:
    total = 0
    for text in _iter_command_texts(command):
        if not text.strip():
            continue
        tokens = safe_tokenize(text)
        if not tokens:
            continue
        total += sum(
            1 for argv in iter_command_starts(tokens) if is_workspace_creation(argv)
        )
    return total


def _command_creates_target(command: str) -> bool:
    return _count_creation_segments(command) > 0


# A creation reached through a loop or a function body runs an unknown number
# of times; static text cannot say how many, and "unknown" is not one.
_LOOP_RE = re.compile(r"(?:^|[;&|\n(]|\s)(?:for|while|until)\s", re.MULTILINE)
_FUNCDEF_RE = re.compile(
    r"(?:^|[;&|\n]|\s)(?:function\s+[A-Za-z_][A-Za-z0-9_]*|"
    r"[A-Za-z_][A-Za-z0-9_]*\s*\(\s*\))\s*\{",
    re.MULTILINE,
)


def command_is_multi_target(command: str) -> bool:
    """True when ONE command can create more than one delegation target.

    Two shapes qualify: two or more literal creation segments, and a single
    creation reached through a loop or a shell function — the latter runs as
    many times as it is called, which no amount of static reading recovers.
    """
    creations = _count_creation_segments(command)
    if creations >= 2:
        return True
    if creations == 0:
        return False
    return bool(_LOOP_RE.search(command) or _FUNCDEF_RE.search(command))


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


def build_reason(ground: str, utterance: str) -> str:
    return format_block(
        rule_name="fan-out scope",
        why=f"{ground} — the request asked for: {utterance}",
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
    multi = False
    if tool_name == AGENT_TOOL:
        pass
    elif tool_name == "Bash":
        command = payload.get("tool_input", {}).get("command", "") or ""
        if not _command_creates_target(command):
            return 0
        multi = command_is_multi_target(command)
    else:
        return 0

    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return 0

    prior = count_targets_in_turn(load_current_turn(transcript_path))
    if multi:
        ground = ("this single command creates more than one delegation "
                  "target — a loop, a function body, or repeated invocations")
    elif prior >= 1:
        ground = f"this is delegation target #{prior + 1} in one turn"
    else:
        return 0

    utterance = _first_line(read_last_user_message(transcript_path))
    emit_decision("ask", build_reason(ground, utterance))
    return 0


if __name__ == "__main__":
    sys.exit(main())
