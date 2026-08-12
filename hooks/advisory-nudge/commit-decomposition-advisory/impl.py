#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: the commit message already says it is N commits.

Rule (AGENTS.md `Atomic Commits`; memory
`feedback_commit_axis_signal_is_authored_not_recalled`): the decomposition
signal does not have to be recalled — the agent just wrote it. A message body
listing N bullets *is* the judgement "this is N commits", and a title typed
`fix` over a body that also says `refactor` is the type field disagreeing with
itself. Both are visible in the argv of the `git commit` about to run.

Why a hook and not rule text: the failing layer is retrieval, not wording. In
the originating session the rule file was opened 0 times across the 435
transcript entries between the instruction and the commit, and the rule text
was then *improved* inside that same session — landing on the same unread
layer. This hook fires at the one moment the message exists and the commit has
not happened yet.

Axis direction is part of the message, deliberately. Splitting a commit is free
(same branch, same PR, same review round); splitting an issue or a PR buys a
review round, a merge ordering, and a tracker entry. An advisory that nudged
toward "split things" in general would convert this defect into its mirror —
tracker fragmentation — so the emitted text names the commit axis and says the
issue axis is not the target.

Advisory only (stderr, exit 0 always).

Silent cases (no output):
  - non-Bash tool, empty command, or no live `git commit` in the command
  - `--amend` (rewriting the previous commit, not authoring a split point),
    `--dry-run`, `--help` / `-h`
  - the message is not readable from argv: no `-m` / `--message` at all, or
    `-F` / `--file` / `-C` / `--reuse-message` / `--squash` / `--fixup` supply
    it from elsewhere
  - fewer than 3 body bullets AND no title/body type disagreement
  - opt-out marker `# [commit-decomp-ack]` anywhere in the command
  - PRAXIS_SKIP_COMMIT_DECOMPOSITION_ADVISORY=1 in the environment

Limitations:
  - argv-only: a message assembled by an editor or a heredoc into `-F -` is
    invisible here, matching the sibling hooks' refusal to grow a shell parser
    (memory `feedback_shell_parser_diminishing_returns`).
  - `result=$(git commit ...)` is missed, and a `git commit` literal inside a
    heredoc body false-surfaces — the same boundaries documented in
    `hooks/advisory-nudge/pre-commit-staged-file-enumeration/spec.md`.
  - Tokenization is local (`_tokenize`), not the shared `safe_tokenize`: see
    that function's docstring for why the shared line pre-split cannot carry a
    multi-line `-m` body.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    strip_prefix,
)

OPT_OUT_MARKER = "# [commit-decomp-ack]"
BULLET_THRESHOLD = 3

# The Conventional Commits type set. `revert` is included: a revert commit
# carrying a second type in its body is the same disagreement.
_TYPES = (
    "feat", "fix", "docs", "style", "refactor", "test", "chore", "perf",
    "ci", "build", "revert",
)
_TITLE_TYPE_RE = re.compile(r"^(" + "|".join(_TYPES) + r")(\([^)]*\))?!?:")
# A type prefix inside the body — at line start, optionally behind a bullet.
_BODY_TYPE_RE = re.compile(
    r"^\s*(?:[-*]\s+)?(" + "|".join(_TYPES) + r")(?:\([^)]*\))?!?:", re.MULTILINE
)
_BULLET_RE = re.compile(r"^\s*[-*]\s+\S", re.MULTILINE)

_GIT_GLOBAL_VALUE_FLAGS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env",
     "--exec-path", "--super-prefix"}
)
_TERMINAL_GLOBAL_FLAGS = frozenset({"--help", "-h", "--version"})
# Flags that make this call not a fresh authoring point, or put the message
# somewhere argv cannot see.
_EXEMPT_COMMIT_FLAGS = frozenset(
    {"--amend", "--dry-run", "--help", "-h", "-F", "--file", "-C",
     "--reuse-message", "-c", "--reedit-message", "--squash", "--fixup"}
)
_MESSAGE_FLAGS = frozenset({"-m", "--message"})


def _tokenize(command: str) -> list[str]:
    """Tokenize, splitting on newlines that are OUTSIDE quotes.

    This deliberately diverges from the shared `safe_tokenize`, which
    pre-splits the raw string on every `\\n` before handing each line to shlex.
    That is right for the hooks that scan for a command hiding on line 3, and
    wrong here: a `-m` body is *inherently* multi-line, and the shared splitter
    severs the flag from its value — its own docstring records the boundary
    ("a multi-line value inside a quoted flag is split at the unescaped
    newline"). A quote-aware split keeps the body intact while still inserting
    the synthetic `;` between real command lines, so `iter_command_starts`
    behaves as it does elsewhere.
    """
    command = re.sub(r"(?<!\\)((?:\\\\)*)\\\n", r"\1 ", command)
    lines: list[str] = []
    buf: list[str] = []
    single = double = escaped = False
    for ch in command:
        if escaped:
            buf.append(ch)
            escaped = False
            continue
        if ch == "\\" and not single:
            buf.append(ch)
            escaped = True
            continue
        if ch == "'" and not double:
            single = not single
        elif ch == '"' and not single:
            double = not double
        if ch == "\n" and not single and not double:
            lines.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    lines.append("".join(buf))

    tokens: list[str] = []
    for line in (ln for ln in lines if ln.strip()):
        if tokens:
            tokens.append(";")
        try:
            lex = shlex.shlex(line, posix=True, punctuation_chars=";|&")
            lex.whitespace_split = True
            lex.commenters = ""  # raw `#` is not a comment here; opt-out marker
            tokens.extend(list(lex))
        except ValueError:
            continue
    return tokens


def _is_git_binary(token: str) -> bool:
    stripped = token.lstrip("(){}$`")
    return stripped == "git" or stripped.endswith("/git")


def _commit_argv(command: str) -> list[str] | None:
    """Return the argv tail of a fresh `git commit`, or None.

    None covers both "no git commit here" and "this commit is exempt" — the
    caller treats them identically (silent).
    """
    tokens = _tokenize(command)
    if not tokens:
        return None
    for argv in iter_command_starts(tokens):
        argv = strip_prefix(argv)
        if not argv or not _is_git_binary(argv[0]):
            continue
        i = 1
        while i < len(argv):
            tok = argv[i]
            if tok in _TERMINAL_GLOBAL_FLAGS:
                i = len(argv)
                break
            if tok in _GIT_GLOBAL_VALUE_FLAGS and i + 1 < len(argv):
                i += 2
                continue
            if tok.startswith("-"):
                i += 1
                continue
            break
        if i >= len(argv) or argv[i] != "commit":
            continue
        tail = argv[i + 1:]
        if _has_exempt_flag(tail):
            continue
        return tail
    return None


def _has_exempt_flag(tail: list[str]) -> bool:
    """True iff an exempt flag appears in flag position.

    The scan must skip the value token consumed by `-m` / `--message`, or a
    message that merely mentions a flag (`-m "--amend is scary"`) silences the
    hook — the value is data, not a flag.
    """
    i = 0
    while i < len(tail):
        tok = tail[i]
        if tok in _MESSAGE_FLAGS:
            i += 2
            continue
        if tok in _EXEMPT_COMMIT_FLAGS or tok.split("=", 1)[0] in _EXEMPT_COMMIT_FLAGS:
            return True
        i += 1
    return False


def _messages(tail: list[str]) -> list[str]:
    """Values of every -m / --message in argv order.

    Handles the three spellings git accepts: `-m body`, `-mbody`,
    `--message=body`.
    """
    out: list[str] = []
    i = 0
    while i < len(tail):
        tok = tail[i]
        if tok in _MESSAGE_FLAGS:
            if i + 1 < len(tail):
                out.append(tail[i + 1])
            i += 2
            continue
        if tok.startswith("--message="):
            out.append(tok.split("=", 1)[1])
        elif tok.startswith("-m") and len(tok) > 2:
            out.append(tok[2:])
        i += 1
    return out


def _signals(messages: list[str]) -> tuple[int, str | None, str | None]:
    """(bullet count, title type, first disagreeing body type)."""
    title = messages[0] if messages else ""
    body = "\n".join(messages[1:])
    bullets = len(_BULLET_RE.findall(body))

    m = _TITLE_TYPE_RE.match(title.strip())
    title_type = m.group(1) if m else None
    other: str | None = None
    if title_type:
        for hit in _BODY_TYPE_RE.finditer(body):
            if hit.group(1) != title_type:
                other = hit.group(1)
                break
    return bullets, title_type, other


def _emit(bullets: int, title_type: str | None, other: str | None) -> None:
    reasons = []
    if bullets >= BULLET_THRESHOLD:
        reasons.append(f"the body lists {bullets} bullets")
    if other and title_type:
        reasons.append(f"the title is `{title_type}` and the body also says `{other}`")
    sys.stderr.write(
        "[commit-decomp] This message is describing more than one change: "
        + "; ".join(reasons)
        + ".\n"
        "Splitting a commit is free — same branch, same PR, same review round — "
        "so one commit too many is cheap and one too few can be neither reverted "
        "nor read alone. Stage selectively (`git add <file>` / `git add -p`) and "
        "commit each change on its own.\n"
        "This is the COMMIT axis only. Do not split the issue or the PR: that "
        "axis buys a review round, a merge ordering, and a tracker entry every "
        "time.\n"
        f"Ack with `{OPT_OUT_MARKER}` in the command, or set "
        "PRAXIS_SKIP_COMMIT_DECOMPOSITION_ADVISORY=1 to silence.\n"
    )


@fail_open
def main() -> int:
    if os.environ.get("PRAXIS_SKIP_COMMIT_DECOMPOSITION_ADVISORY") == "1":
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command", "") or ""
    if not command.strip() or OPT_OUT_MARKER in command:
        return 0

    tail = _commit_argv(command)
    if tail is None:
        return 0

    messages = _messages(tail)
    if not messages:
        return 0  # message not readable from argv → silent

    bullets, title_type, other = _signals(messages)
    if bullets >= BULLET_THRESHOLD or other:
        _emit(bullets, title_type, other)
    return 0


if __name__ == "__main__":
    sys.exit(main())
