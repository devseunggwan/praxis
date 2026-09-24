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
command. An MCP call carries no comment surface and its arguments are the
server's schema to define, so its acknowledgement is written to a hook-owned
file instead, whose path the gate's own message prints, and consumed by
the next call that would
otherwise be asked about. The marker is not a bypass token — it asserts that the
premise was re-read and states what it now says. Attaching it without having
done so is a false attestation.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _paths import resolve_cache_file  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from block_message import format_block  # type: ignore[import-not-found]  # noqa: E402
from _mutating_call import (  # type: ignore[import-not-found]  # noqa: E402
    bash_is_readonly,
    mcp_is_mutating,
)
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    heredoc_sources,
    iter_command_starts,
    safe_tokenize,
    strip_heredoc_bodies,
    strip_prefix,
)

ACK_MARKER = "# approval-premise:ack"

# How to attest for a call that has no comment surface. The message prints the
# resolved path rather than the procedure: an opt-out nobody can find is an
# opt-out that does not exist.
def _ack_hint(path: str) -> str:
    return (
        "this call has no comment surface, so the acknowledgement goes in a "
        f"file: write {{\"premise\": \"<one line>\"}} to {path} and re-issue. "
        "It is consumed on read -- one acknowledgement covers one call."
    )

# The acknowledgement is an attestation that the premise was re-read and a
# statement of what it now says, so the statement is the part that matters: a
# bare marker attests nothing. Each surface is parsed only where it is declared
# -- the comment in the Bash command, the argument in the MCP input -- because a
# substring search over serialized input accepts the marker sitting inside an
# unrelated field, which no one wrote as an attestation.
ACK_WORD = ACK_MARKER.split("#", 1)[1].strip()

# A production phase marker arrives as a flag value, an MCP argument, or a
# quoted identifier. The named flags absorb the spacing and casing variants a
# literal list cannot (`--phase=prod`, `--phase  prod`, `-p PROD`); the quoted
# forms catch a serialized MCP argument.
#
# Bare `production` is deliberately absent. The read-only filter below narrows
# the Bash branch but is not exhaustive, so a marker that common on read-only
# commands (`kubectl get pods -n production`) would lean on the filter for
# every namespace query and turn this gate into the noise it exists to replace.
PROD_MARKER_RE = re.compile(
    r"""(?ix)
      (?: --phase | --profile | --env(?:ironment)? | -p ) \s*[=\s]\s* prod\w*
    | \bphase \s*=\s* ["']? prod \b
    | ["'] prod ["']
    | \bprod-
    """
)


def _carries_prod_marker(blob: str) -> bool:
    return PROD_MARKER_RE.search(blob) is not None


# Languages whose inline program is data to the shell. A shell is absent on
# purpose: `sh -c 'kubectl --context prod-x delete pod p'` names a production
# target in its program, so dropping that program would silence the one call
# this gate exists for.
_INTERPRETERS = frozenset((
    "python", "python2", "python3", "node", "nodejs", "deno", "bun",
    "perl", "ruby", "php", "Rscript", "osascript", "jq",
))

# `python -c "..."` / `node -e "..."` — the quoted program, so that a `prod`
# literal printed by the script is not read as the shell's own argument. A path
# prefix (`/usr/bin/python3`) is the same interpreter, as the heredoc opener
# check already treats it.
_INLINE_PROGRAM_RE = re.compile(
    r"""(?<![A-Za-z0-9_./-])(?:[^\s'"/]*/)*(?:%s)(?:[0-9.]*)\s+(?:-\w+\s+)*-[ce]\s+
        (?P<prog>'(?:[^']*)'|"(?:\\.|[^"\\])*")"""
    % "|".join(sorted(_INTERPRETERS)),
    re.VERBOSE,
)


# A `$(…)` or backtick run anywhere the shell reads: the shell runs its
# contents, so nothing around it is program text any more.
_SHELL_RUNS_RE = re.compile(r"\$\(|`")


def _strip_inline_programs(text: str) -> str:
    return _INLINE_PROGRAM_RE.sub(lambda m: m.group(0).replace(m.group("prog"), ""), text)


def _lone_interpreter(command: str, delimiters: frozenset[str]) -> bool:
    """True when the whole command is one simple call to an interpreter.

    Anything else — a pipeline, an `&&` chain, a substitution running a second
    command — keeps all of its text, because binding a body or a program to
    the command that owns it needs an analysis this gate does not have and
    every earlier attempt at one produced a bypass (issue #1449): a `$(python3
    -c …)` inside `kubectl --context` stripped the argument the gate exists to
    read, and a heredoc body line shaped like `python3 <<EOF` was credited
    with a `kubectl apply` manifest.
    """
    # A heredoc terminator survives `strip_heredoc_bodies` on its own line, so
    # the tokenizer reads it as a second command; it is punctuation, not one.
    segments = [seg for seg in iter_command_starts(safe_tokenize(command))
                if not (len(seg) == 1 and seg[0] in delimiters)]
    if len(segments) != 1:
        return False
    argv = strip_prefix(segments[0])
    return bool(argv) and argv[0].rsplit("/", 1)[-1] in _INTERPRETERS


def _bash_marker_text(command: str) -> str:
    """The part of `command` that can name the command's own target.

    A prod marker inside a non-shell interpreter's program is a string literal
    in another language, not an argument to anything the shell runs — issue
    #1428, where `python3 - <<'EOF'` rewriting a scratch file asked about a
    production target. The exclusion is deliberately narrow: only a command
    that is nothing but that interpreter call drops any text, and only its own
    single heredoc and inline `-c`/`-e` program. Everything else is kept.

    An unquoted heredoc body holding a substitution is kept whole: bash runs
    it before the interpreter ever sees the body, so that text is the shell's
    own call, and extracting only the substitution missed a nesting the
    extractor could not parse.
    """
    sources = heredoc_sources(command)
    if not _lone_interpreter(command, frozenset(delim for delim, _b, _q in sources)):
        return command
    if len(sources) > 1:
        return command  # a second heredoc is not this interpreter's program
    shell_text = strip_heredoc_bodies(command)
    if _SHELL_RUNS_RE.search(shell_text):
        return command  # the shell runs part of the argument text
    stripped = _strip_inline_programs(shell_text)
    if sources:
        _delim, body, quoted = sources[0]
        if not quoted and _SHELL_RUNS_RE.search(body):
            return command
    return stripped


# The two questions are the whole point of the gate, so they live in the
# `correct_path` field the shared renderer prints under "Do this instead".
_ANSWER_BOTH = (
    "answer both before this executes -- "
    "(1) PREMISE: restate in one line the justification the approval was granted "
    "on, and say whether anything observed SINCE has made it false; if it has, "
    "this is not an approved action any more, so re-ask. "
    "(2) TARGET: was the blast radius measured on THIS target, or inherited from "
    "another member of the same cohort? An enumeration measured on target A is "
    "not evidence about target B, and it covers the outward side-effect axis "
    "(mail, webhook, channel post, customer notification), not only the data "
    "surfaces."
)


def _message(tool_name: str, target: str, session_id: str = "") -> str:
    answer = _ANSWER_BOTH
    if tool_name.startswith("mcp__") and session_id:
        answer = f"{_ANSWER_BOTH} {_ack_hint(_ack_file(session_id))}"
    return format_block(
        rule_name="approval premise re-read",
        why="an approval covers the option, not an option whose premise has "
            f"since dissolved -- {tool_name} names a production target "
            f"({target or 'unnamed'})",
        correct_path=answer,
        # The acknowledgement comment is an attestation the agent writes after
        # actually re-reading, not an env-var bypass; an env var would let the
        # session disable the gate for itself.
        bypass_env=None,
        reference="hooks/preflight-gate/approval-premise-reread-gate/spec.md",
    )


_COMMENT_BOUNDARY = " \t;|&()<>"


def _first_unquoted_comment(command: str) -> int | None:
    """Index of the `#` that bash would read as opening the first comment.

    `safe_tokenize` strips quote delimiters, so after it a quoted `'#'` and a
    real comment opener are the same token -- which is why this walks the raw
    text instead. Quote state and the word boundary both have to hold: bash
    reads `#` as a comment only at the start of a word, and not at all inside
    quotes.
    """
    quote = ""
    i = 0
    while i < len(command):
        ch = command[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch == "\\":
            i += 2
            continue
        if ch in "'\"":
            quote = ch
            i += 1
            continue
        if ch == "#" and (i == 0 or command[i - 1] in _COMMENT_BOUNDARY):
            return i
        i += 1
    return None


def _bash_ack(command: str) -> bool:
    """True when the command's FIRST comment is the marker plus a premise.

    Position decides, not presence, and four positions are wrong. A quoted
    occurrence is data whether it is the whole marker (`-f body='# ...'`) or
    just the `#` (`bash -c '...' '#' approval-premise:ack`), so the opener is
    found in the raw text with quote state intact. A marker inside an *earlier*
    comment is prose about the marker. And a marker on an earlier line attests
    for that line, not for a mutation below it, so nothing may follow the
    comment's own line.
    """
    idx = _first_unquoted_comment(command)
    if idx is None:
        return False
    comment = command[idx + 1:]
    newline = comment.find("\n")
    if newline != -1:
        if command[idx + 1 + newline + 1:].strip():
            return False  # a command follows on a later line
        comment = comment[:newline]
    body = comment.strip()
    if not body.startswith(ACK_WORD):
        return False
    premise = body[len(ACK_WORD):]
    if premise and not premise[:1].isspace():
        return False  # the marker ran into a longer word
    return bool(premise.strip())


def _ack_file(session_id: str) -> str:
    return resolve_cache_file(f"approval-premise-ack-{session_id}.json",
                              session_id=session_id)


def _mcp_ack(session_id: str) -> bool:
    """True when this session left a premise in the hook-owned ack file.

    An MCP call has no comment surface, and its arguments belong to the
    server's schema: a synthetic `approval_premise_ack` field is rejected
    outright by a server that validates its input, so the quiet path it
    described was not reachable at runtime for any such tool.

    The file is *claimed* before it is read, by renaming it to a pid-unique
    path. Reading and then unlinking loses the race two concurrent hook
    processes for one session can run: both open the file before either
    unlinks, and one acknowledgement passes two calls. `os.rename` is atomic,
    so exactly one process gets the file and the other sees it gone.
    """
    if not session_id:
        return False
    path = _ack_file(session_id)
    claim = f"{path}.claim-{os.getpid()}"
    try:
        os.rename(path, claim)
    except OSError:
        return False  # absent, or another process claimed it first
    try:
        with open(claim, encoding="utf-8") as fh:
            premise = (json.load(fh) or {}).get("premise")
    except Exception:
        return False  # unreadable or malformed: the gate asks
    finally:
        try:
            os.unlink(claim)
        except OSError:
            pass
    return isinstance(premise, str) and bool(premise.strip())


@fail_open
def main() -> int:
    payload = read_payload()
    if payload is None:
        return 0  # fail open — a malformed payload must not block the session

    session_id = str(payload.get("session_id") or "")
    tool_name = payload.get("tool_name", "") or ""
    tool_input = payload.get("tool_input", {}) or {}
    blob = json.dumps(tool_input, ensure_ascii=False)

    if tool_name == "Bash":
        command = tool_input.get("command", "") or ""
        if _bash_ack(command):
            return 0
        if not _carries_prod_marker(_bash_marker_text(command)):
            return 0
        if bash_is_readonly(command):
            return 0
        target = command.strip().splitlines()[0][:120]
    elif mcp_is_mutating(tool_name):
        # Marker first: the ack file is consumed on read, so testing it before
        # this would let an unrelated non-production mutation eat the premise
        # written for the production call that is about to be re-issued.
        if not _carries_prod_marker(blob):
            return 0
        if _mcp_ack(session_id):
            return 0
        target = str(tool_input.get("dag_id") or tool_input.get("conf") or "")[:120]
    else:
        return 0

    emit_decision("ask", _message(tool_name, target, session_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
