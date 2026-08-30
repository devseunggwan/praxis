#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: perf multiplier / lever verdict surfaced to a
deliverable (`gh issue|pr create|comment`) without an adjacent controlled-
timing artifact in the same body text.

Why this exists (issue #850):

`feedback_perf_deliverable_requires_controlled_magnitude` (memory, created
71st retrospect) recurred at the SHORTEST interval on record — 6 days later
(74th retrospect, ordercycle precompute 49m task). Memory-only remediation
already failed once. The recurrence pattern: perf optimization levers were
sized from proxy/arithmetic estimates (grid row-count x microseconds,
cardinality-correlation, dim string-width) and carried to a near-deliverable
(draft GitHub issue) with NO controlled measurement. The controlled
measurement tool (DuckDB CLI) was reachable the entire session and was only
run after the user asked "did a quantitative test actually happen?" — at
which point ALL THREE estimates were overturned:

  | lever                        | pre-measurement estimate | controlled | error |
  |-------------------------------|---------------------------|------------|-------|
  | dim integer encoding          | ~1.76x                    | 3.06x      | 74% under-estimate |
  | grid NOT MATERIALIZED         | proposed as improvement   | 0.73x      | SIGN error (regression proposed as a win) |
  | memory 14->8GB                | candidate lever           | no change  | non-lever |

The `NOT MATERIALIZED` case is not a magnitude error — it is a **sign**
error: without measurement, a change that makes performance WORSE would
have shipped as an "improvement".

Root cause (tracer + analyst convergence): both failure axes (magnitude
inaccuracy AND lever sign error) are the same root — the corrective action
is identical (measure first) — so one gate covers both, rather than two
separate rules.

Root discriminator: all upstream investigation sub-agents were dispatched
READ-ONLY (no CTAS execution possible), so proxy estimates were the only
possible output. The gap is NOT in the sub-agent layer — it is the main
loop laundering a read-only proxy estimate into a deliverable without a
controlled-measurement checkpoint. The gate therefore fires at the
OUTPUT-BLOCK / deliverable-write boundary (this hook), not at sub-agent
dispatch time.

Design — same family as `output-block-falsify-advisory` /
`secret-print-redaction-advisory` (advisory nudge, never blocks):

  1. A **perf multiplier notation** (`3x`, `3×`, `1.76배`, `300%`,
     `A -> B` timing pair, `from 49m to 12m`) OR a **lever verdict** token
     (채택/개선/역효과/lever) appears in body text destined for
     `gh issue create|comment` / `gh pr create|comment` (inline `--body`/
     `-b`, or a readable `--body-file`/`-F` target), AND
  2. NO adjacent controlled-timing artifact (a cited `$ command -> output`
     line, `wall-clock:`, `elapsed`, or `real Nm` timing output) appears
     anywhere in the same body text.

Pass condition: an adjacent timing artifact anywhere in the body silences
the whole scan for that invocation — this hook does not try to prove the
artifact actually measures the SAME lever the multiplier claims (that
adequacy judgment is out of scope, mirroring the sibling gates' presence-
only enforcement).

Each body is bound to its OWN invocation (issue #973). One command can chain
several `gh` calls, and a timing artifact posted by one of them measures
nothing about a multiplier posted by another — so the scan runs per
invocation, not over the command as a whole. The pre-fix shape read only the
first `--body` in the flat argv and matched `gh (issue|pr) (create|comment)`
as a regex over the raw command text, which produced three wrong answers:
a claim in a second chained call was never seen; a non-`gh` command's
`--body` was attributed to the `gh` scan; and the words `gh issue create`
inside a quoted `echo` argument fired the advisory with no deliverable at
all. The sibling `n1-quantitative-claim-advisory` was corrected the same way
(PR #969).

Scope (deliberately narrow, per issue): PreToolUse(Bash) only, `gh issue|pr
create|comment` invocations only. A prose "synthesis block" surfaced with
no tool call at all is a Stop-lane concern (see `proposal-premise-gate`,
issue #846) and is explicitly out of scope here — YAGNI, one gate per
surface.

Fail-open contract:
  - malformed JSON / non-Bash tool / empty command -> exit 0
  - no `gh issue|pr create|comment` invocation -> exit 0
  - `--body-file`/`-F` target unreadable -> exit 0 for that invocation (no
    body text to scan; under-firing here is the accepted tradeoff over a
    disk read failure blocking the whole hook)
  - any uncaught exception -> exit 0 (`@fail_open`)

Advisory only -- writes to stderr, exits 0. Never blocks.
"""
from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

_HOOK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_HOOK_DIR.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_bash_payload  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    strip_prefix,
)

# ---------------------------------------------------------------------------
# Trigger: gh issue|pr create|comment
#
# Structural tokenization, not regex (DESIGN.md "Design mechanisms shared by
# all hooks"). A regex over raw command text cannot tell a real invocation
# from the same words inside a quoted string, and it cannot tell which body
# flag belongs to which invocation when several are chained (issue #973).
# ---------------------------------------------------------------------------

_GH_OBJECTS = ("issue", "pr")
_GH_VERBS = ("create", "comment")

_BODY_TEXT_FLAGS = ("--body", "-b")
_BODY_FILE_FLAGS = ("--body-file", "-F")

# ---------------------------------------------------------------------------
# (1) Perf multiplier notation
# ---------------------------------------------------------------------------

# `3x`, `3.06x`, `3×` — a digit run immediately followed by x/X/× with no
# intervening space required (matches the issue's own examples verbatim).
_MULT_X_RE = re.compile(r"\b\d+(?:\.\d+)?\s*[x×]\b", re.IGNORECASE)
# `1.76배` — Korean multiplier suffix.
_MULT_BAE_RE = re.compile(r"\d+(?:\.\d+)?\s*배\b")
# `300%`, `49% faster`, `73% slower`, `향상 30%`, `역효과 27%` — percentage
# tied to a direction word nearby (either side, within a short window).
_PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")
_DIRECTION_WORDS = ("faster", "slower", "regression", "improvement", "빠름", "느림", "향상", "저하")
# `A -> B` / `A → B` timing pair, e.g. `49m -> 12m`, `49분 → 12분`.
_TIMING_PAIR_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:m|min|분|s|sec|초|h|시간)\b\s*(?:->|→)\s*"
    r"\d+(?:\.\d+)?\s*(?:m|min|분|s|sec|초|h|시간)\b",
    re.IGNORECASE,
)
# `from 49m to 12m` prose form of the same timing pair.
_FROM_TO_RE = re.compile(
    r"\bfrom\s+\d+(?:\.\d+)?\s*(?:m|min|s|sec|h)\b\s+to\s+"
    r"\d+(?:\.\d+)?\s*(?:m|min|s|sec|h)\b",
    re.IGNORECASE,
)

_LEVER_KO = ("채택", "개선", "역효과")
_LEVER_EN_RE = re.compile(r"\blever\b", re.IGNORECASE)


def _has_percent_with_direction(text: str) -> bool:
    for m in _PERCENT_RE.finditer(text):
        window = text[max(0, m.start() - 24):m.end() + 24].lower()
        if any(w in window for w in _DIRECTION_WORDS):
            return True
    return False


def _has_multiplier(text: str) -> bool:
    if _MULT_X_RE.search(text):
        return True
    if _MULT_BAE_RE.search(text):
        return True
    if _TIMING_PAIR_RE.search(text):
        return True
    if _FROM_TO_RE.search(text):
        return True
    return _has_percent_with_direction(text)


def _has_lever_verdict(text: str) -> bool:
    if any(k in text for k in _LEVER_KO):
        return True
    return bool(_LEVER_EN_RE.search(text))


# ---------------------------------------------------------------------------
# (2) Controlled-timing artifact — pass condition
# ---------------------------------------------------------------------------

# `$ command -> output` cited line (mirrors completion-signal-gate's
# _CITED_OUTPUT_RE convention).
_CITED_OUTPUT_RE = re.compile(r"^\s*\$\s+\S+.*(?:->|→)", re.MULTILINE)
_TIMING_MARKER_RE = re.compile(
    r"wall-clock|elapsed|\breal\s+\d+m|\bwall\s+clock\b",
    re.IGNORECASE,
)


def _has_timing_artifact(text: str) -> bool:
    if _CITED_OUTPUT_RE.search(text):
        return True
    return bool(_TIMING_MARKER_RE.search(text))


# ---------------------------------------------------------------------------
# Body-text extraction from a `gh ... create|comment` command
# ---------------------------------------------------------------------------


def _is_gh_deliverable(argv: list[str]) -> bool:
    """True iff this argv slice is a `gh (issue|pr) (create|comment)` call.

    The object and verb are located by scanning past `gh`'s own global flags
    (`--repo owner/name`, `-R …`), so a flag sitting between them does not
    hide the invocation.
    """
    if not argv:
        return False
    head = argv[0].lstrip("(${")
    if not (head == "gh" or head.endswith("/gh")):
        return False

    words = [tok for tok in argv[1:] if not tok.startswith("-")]
    for i, word in enumerate(words):
        if word in _GH_OBJECTS:
            return any(w in _GH_VERBS for w in words[i + 1:i + 3])
    return False


def _body_of(argv: list[str]) -> str | None:
    """Return the body text THIS invocation would post, or None.

    Inline `--body`/`-b` values are read directly from the parsed argv.
    `--body-file`/`-F` values are read from disk (best-effort — an
    unreadable path silently yields None for THIS invocation, not a hook
    failure).
    """
    for i, tok in enumerate(argv):
        flag, sep, inline_value = tok.partition("=")
        if tok in _BODY_TEXT_FLAGS and i + 1 < len(argv):
            return argv[i + 1]
        if sep and flag in _BODY_TEXT_FLAGS:
            return inline_value
        if tok in _BODY_FILE_FLAGS and i + 1 < len(argv):
            return _read_body_file(argv[i + 1])
        if sep and flag in _BODY_FILE_FLAGS:
            return _read_body_file(inline_value)
    return None


def _newlines_to_separators(command: str) -> str:
    """Rewrite command-separating newlines as `;` so segment splitting sees them.

    A newline separates commands in Bash, but `shlex.split` consumes it as
    generic whitespace — `git status\\ngh pr comment --body "3x"` flattens into
    ONE segment whose argv[0] is `git`, and the `gh` body is never scanned.
    `safe_tokenize` already solves this by inserting a synthetic `;` per line,
    but it splits on every raw newline, which severs `--body` from a multi-line
    GitHub body — this hook's primary input. So the newline is rewritten only
    when it sits OUTSIDE quotes: a body's internal newlines are quoted and stay
    untouched, while a real command break becomes a separator token.

    Backslash-escaped characters are copied through verbatim, so a `\\`-newline
    line continuation is not turned into a break.
    """
    out: list[str] = []
    quote: str | None = None
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if quote == "'":
            # Single quotes are literal in Bash — no escapes inside them.
            if ch == "'":
                quote = None
            out.append(ch)
            i += 1
        elif ch == "\\" and i + 1 < n:
            out.append(ch)
            out.append(command[i + 1])
            i += 2
        elif quote == '"':
            if ch == '"':
                quote = None
            out.append(ch)
            i += 1
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
            i += 1
        elif ch == "\n":
            out.append(" ; ")
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _extract_bodies(command: str) -> list[str]:
    """Every body a `gh` deliverable in this command would post.

    One command can chain several invocations; each carries its own body, and
    a timing artifact cited in one measures nothing about a multiplier claimed
    by another (issue #973).
    """
    # `shlex.split` rather than the shared `safe_tokenize`: this hook's inputs
    # are multi-line GitHub bodies. `safe_tokenize` pre-splits on every raw
    # newline (its own docstring flags this as a caller note), so a quoted body
    # is cut mid-string, every resulting line fails to parse on the unmatched
    # quote, and the skip-on-ValueError arm drops them all — measured:
    # `gh pr comment 1 --body "### Verification\n3x speedup expected"`
    # tokenizes to `[';']`, losing the invocation entirely. `_newlines_to_
    # separators` above recovers the command breaks that `shlex.split` would
    # otherwise swallow, without cutting a quoted body. Command-boundary
    # splitting still uses the shared primitives below.
    try:
        tokens = shlex.split(_newlines_to_separators(command), posix=True)
    except ValueError:
        return []
    if not tokens:
        return []

    bodies = []
    for segment in iter_command_starts(tokens):
        argv = strip_prefix(segment)
        if not _is_gh_deliverable(argv):
            continue
        body = _body_of(argv)
        if body:
            bodies.append(body)
    return bodies


def _read_body_file(path_text: str) -> str | None:
    try:
        p = Path(path_text).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Advisory text
# ---------------------------------------------------------------------------


def _advisory_text() -> str:
    return (
        "[perf-multiplier-evidence-advisory] perf multiplier / lever verdict "
        "in this deliverable has no adjacent controlled-timing artifact "
        "(no cited '$ command -> output' line, 'wall-clock:', or 'elapsed').\n"
        "  Rule: size the lever with a real measurement (e.g. DuckDB CLI "
        "wall-clock) before it reaches a deliverable — proxy/arithmetic "
        "estimates have overturned on measurement, including a sign-flip "
        "(a proposed 'improvement' that was actually a 0.73x regression).\n"
        "  Reference: issue #850 (gen-2 recurrence, 6 days after the first "
        "memory-only remediation — measure before it locks)."
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


@fail_open
def main() -> int:
    parsed = read_bash_payload()
    if parsed is None:
        return 0  # non-Bash tool or malformed stdin — fail-open
    payload, command = parsed
    if not isinstance(command, str) or not command.strip():
        return 0

    # Each chained invocation is scanned against its OWN body: a timing
    # artifact cited in one body does not silence a multiplier claimed in
    # another (issue #973). The first unsatisfied body is reported.
    for body in _extract_bodies(command):
        if not (_has_multiplier(body) or _has_lever_verdict(body)):
            continue

        if _has_timing_artifact(body):
            continue

        sys.stderr.write(_advisory_text() + "\n")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
