#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: warn when a suppressed-stderr `||` fallback
yields a negative-verdict string.

Why this exists (issue #893):

  <cmd> 2>/dev/null | grep -i <key> || echo "(no session)"

`2>/dev/null` discards stderr and the `||` fallback prints "(no session)".
This collapses three distinct outcomes into one identical string:

  - the subcommand does not exist (exit 2, `Unknown command`)
  - auth expired / daemon not running
  - the grep genuinely matched 0 lines

Real incident: an in-flight-session gate written in exactly this shape used
a subcommand that never existed. The gate never ran, but its `||` fallback
printed "no session" and that was reported to the user as a verified fact —
self-detection was impossible because the failure and the true-negative
looked identical.

Detection is 3-condition AND, scoped per `||` occurrence:

  1. A stderr-to-`/dev/null` redirect appears anywhere in the same
     statement to the LEFT of this `||` (bounded by the nearest
     preceding `;`/`&&`/`&` — a fresh statement does not inherit the
     redirect of a prior one). Recognized forms: `2>/dev/null` (fused or
     whitespace-separated `2> /dev/null`); `>/dev/null 2>&1` (fused or
     whitespace-separated, in this order — stdout-to-null then
     stderr-duped-onto-stdout); and the reverse order, `2>&1 >/dev/null`,
     but ONLY when a `|` follows it in the same pipeline (without a
     following pipe, this order leaves stderr visible at its original
     target — see `_has_stderr_null_redirect`).
  2. The segment immediately to the right of `||` is `echo`, `printf`, or
     `true`.
  3. For `echo`/`printf`, the joined argument text contains a
     negative-verdict word: 없, none, "no " (trailing space — avoids
     matching "known"/"not"), "not found", empty, "(0", skip. `true` never
     carries a message, so it can never satisfy this condition — matches
     issue #893's "`|| true` alone must stay silent" requirement without a
     special case.

Advisory-only — never blocks (issue #893 non-goal: false-positive cost
exceeds the benefit of hard-blocking a legitimate "suppress noise, branch
on exit" idiom, which conditions 1+2 alone do not distinguish from this
hook's target pattern).

Fail-open contract:
  • malformed JSON / non-Bash payload → exit 0
  • empty command → exit 0
  • any uncaught exception in the inner logic → swallowed, exit 0
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_HOOK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_HOOK_DIR.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import safe_tokenize, strip_prefix  # type: ignore[import-not-found]  # noqa: E402


# ---------------------------------------------------------------------------
# Token-stream constants
# ---------------------------------------------------------------------------

# Separators that end a *statement* — a stderr-null redirect before one of
# these does not carry forward to a `||` in the next statement. `|` is
# deliberately excluded: a pipe segment is part of the same statement as the
# `||` that follows the whole pipeline (the motivating pattern in issue #893
# has the redirect on the pipeline's first segment, `||` on the pipeline as
# a whole).
_STATEMENT_BOUNDARIES = frozenset({";", "&&", "&"})

_FALLBACK_BINARIES = frozenset({"echo", "printf", "true"})

_STDERR_NULL_TOKEN = "2>/dev/null"

# Negative-verdict vocabulary from issue #893's proposal, verbatim. "no "
# requires a trailing space so it does not match "known"/"not"/"annoying".
_NEGATIVE_VOCAB_RE = re.compile(
    r"없|\(0|(?i:\bnone\b|no[ \t]|not[ \t]+found|\bempty\b|\bskip\b)"
)


# ---------------------------------------------------------------------------
# `2>&1 >/dev/null` / `>/dev/null 2>&1` fd-dup normalization
# ---------------------------------------------------------------------------
#
# safe_tokenize's shlex punctuation_chars=";|&" does not include `>`, so
# `2>&1` tokenizes as three tokens (`2>`, `&`, `1`) with the bare `&`
# misread as a backgrounding SHELL_SEPARATOR. Mirrors
# `pipefail-advisory._merge_fd_dup_redirects` (second occurrence — not yet
# extracted to `_lib` per this repo's 3rd-occurrence DRY threshold).

_FD_REDIRECT_RE = re.compile(r"^[0-9]*>>?$")
_FD_TARGET_RE = re.compile(r"^[0-9]+$|^-$")


def _merge_fd_dup_redirects(tokens: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        if (
            i + 2 < n
            and _FD_REDIRECT_RE.match(tokens[i])
            and tokens[i + 1] == "&"
            and _FD_TARGET_RE.match(tokens[i + 2])
        ):
            out.append(tokens[i] + tokens[i + 1] + tokens[i + 2])
            i += 3
            continue
        out.append(tokens[i])
        i += 1
    return out


_REDIRECT_TARGET_MERGE_OPS = frozenset({"2>", ">"})


def _merge_spaced_stderr_null(tokens: list[str]) -> list[str]:
    """Merge a whitespace-separated redirect-operator + `/dev/null` pair
    into one token.

    `cmd 2> /dev/null` and `cmd > /dev/null 2>&1` (legal — a space between
    the redirect operator and its target) tokenize the operator and
    target as two separate words, unlike the fused `2>/dev/null` /
    `>/dev/null` forms. Both the stderr operator (`2>`) and the bare
    stdout operator (`>`, needed for the combined `>/dev/null 2>&1`
    idiom) are merged; no other operator (`>>`, `1>`, ...) is in scope.
    """
    out: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        if i + 1 < n and tokens[i] in _REDIRECT_TARGET_MERGE_OPS and tokens[i + 1] == "/dev/null":
            out.append(tokens[i] + tokens[i + 1])
            i += 2
            continue
        out.append(tokens[i])
        i += 1
    return out


def _has_stderr_null_redirect(segment: list[str]) -> bool:
    """True if `segment` contains a stderr-to-/dev/null redirect.

    Handles the bare `2>/dev/null` form and the combined `>/dev/null
    2>&1` form. Order matters for the combined form: bash redirections
    apply left to right, so `>/dev/null 2>&1` first points fd1 (stdout)
    at `/dev/null`, then dups fd2 (stderr) onto fd1's now-`/dev/null`
    target — both streams are discarded unconditionally.

    The reverse order, `2>&1 >/dev/null`, first dups fd2 onto fd1's
    CURRENT target, THEN redirects fd1 to `/dev/null`; fd2 keeps
    pointing at whatever fd1 pointed at before. Whether that's still
    "suppressed" depends on what fd1 pointed at:

      - No pipe follows this segment — fd1 pointed at the original
        stdout target (a terminal, or the Bash tool's captured stdout),
        so fd2 (stderr) stays visible there. Not a suppression; stays
        silent. Verified live: `bogus_cmd 2>&1 >/dev/null || echo none`
        prints the "command not found" error.
      - A pipe DOES follow this segment (`... 2>&1 >/dev/null | grep
        ... || echo ...`) — fd1 pointed at the pipe's write end, so fd2
        is now folded into the pipeline's input, invisible to anyone not
        reading the pipe. If the downstream filter (`grep`) doesn't
        match the error text, it's indistinguishable from a genuine
        0-match — exactly the collapse issue #893 targets, via a
        different mechanism (routed into the pipe, not to `/dev/null`).
        Verified live: `bogus_cmd 2>&1 >/dev/null | grep -i hello ||
        echo "(no session)"` prints ONLY "(no session)" — the error
        text never appears anywhere (codex review round 2, F1).

    `segment` is assumed already fd-dup-merged so `2>&1` is a single
    token.
    """
    if _STDERR_NULL_TOKEN in segment:
        return True
    for i, tok in enumerate(segment):
        if tok == ">/dev/null" and i + 1 < len(segment) and segment[i + 1] == "2>&1":
            return True
        if tok == "2>&1" and i + 1 < len(segment) and segment[i + 1] == ">/dev/null":
            if "|" in segment[i + 2 :]:
                return True
    return False


def _is_negative_fallback(segment: list[str]) -> bool:
    """True if `segment` is an `echo`/`printf` call whose argument text
    contains negative-verdict vocabulary. `true` never has a message, so it
    always returns False here (issue #893: "`|| true` alone" must stay
    silent)."""
    argv = strip_prefix(segment)
    if not argv:
        return False
    cmd = os.path.basename(argv[0])
    if cmd not in _FALLBACK_BINARIES:
        return False
    if cmd == "true":
        return False
    message = " ".join(argv[1:])
    return bool(_NEGATIVE_VOCAB_RE.search(message))


def _fallback_summary(segment: list[str]) -> str:
    argv = strip_prefix(segment)
    return " ".join(argv) if argv else " ".join(segment)


# ---------------------------------------------------------------------------
# `||`-scoped scan
# ---------------------------------------------------------------------------


def _find_advisory(tokens: list[str]) -> str | None:
    n = len(tokens)
    window_start = 0
    i = 0
    while i < n:
        tok = tokens[i]
        if tok in _STATEMENT_BOUNDARIES:
            window_start = i + 1
            i += 1
            continue
        if tok == "||":
            left = tokens[window_start:i]
            j = i + 1
            right: list[str] = []
            while j < n and tokens[j] not in _STATEMENT_BOUNDARIES and tokens[j] not in ("|", "||"):
                right.append(tokens[j])
                j += 1
            if _has_stderr_null_redirect(left) and _is_negative_fallback(right):
                return _advisory_text(left, right)
            i = j
            continue
        i += 1
    return None


def _advisory_text(left: list[str], right: list[str]) -> str:
    left_summary = " ".join(left) if left else "?"
    fallback = _fallback_summary(right)
    return (
        "[fallback-negative-warn] suppressed-stderr fallback yields a negative verdict\n"
        "\n"
        f"  Detected: {left_summary} || {fallback}\n"
        "\n"
        "  This output cannot distinguish a failed command from a genuine\n"
        "  zero-result. Before using it as negative evidence, branch on the\n"
        "  exit code or keep stderr visible.\n"
        "  (이 출력은 커맨드 실패와 0건을 구분하지 못합니다. 부정 판정 근거로\n"
        "  쓸 거라면 exit code 를 분기하거나 stderr 를 살리세요.)\n"
        "\n"
        "  Reference: issue #893"
    )


@fail_open
def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "") or ""
    if not command.strip():
        return 0

    tokens = safe_tokenize(command.replace("\\\n", " "))
    if not tokens:
        return 0

    tokens = _merge_fd_dup_redirects(tokens)
    tokens = _merge_spaced_stderr_null(tokens)
    advisory = _find_advisory(tokens)
    if advisory:
        sys.stderr.write(advisory + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
