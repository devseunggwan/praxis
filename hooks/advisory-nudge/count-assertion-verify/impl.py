#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: grep -c with alternation pattern.

Issue #277. Recurring failure mode (2x occurrence):
`grep -c 'pat1\\|pat2'` or `grep -Ec 'pat1|pat2'` produces a combined count
that may be inflated when one alternation arm matches unintended lines. The
count is then cited as a precise figure without verifying each arm.

  Incident: PR #258 body claimed "29 cases" (`grep -c '^run_case\\|^run_test'`).
  Codex reviewer corrected to 28 (`grep -c '^run_case '`).

CLAUDE.md "Information Accuracy → Layer 2" already covers this; memory-only
retrieval has failed twice. This hook adds a structural attention-shift at the
grep invocation point.

Advisory condition (ALL must be true):
  1. Command contains a `grep` subcommand
  2. The grep uses the count flag (`-c` / `--count`, or combined `-cE` etc.)
  3. The pattern contains alternation (`\\|` in BRE; unescaped `|` in ERE/PCRE)

False-positive guard:
  - `grep -c 'single_pattern'` → no advisory (no alternation)
  - `grep -E 'pat'` without `-c` → no advisory (no count assertion)

Opt-out:
  - Append `# count-verified` anywhere in the command

Fail-open contract (project hook design):
  - Malformed / missing stdin JSON → exit 0
  - Any parse exception → exit 0 (never crash the session)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Import shared tokenizer
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)

# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

ADVISORY_TAG = "[count-assertion-verify]"
ADVISORY_MSG = (
    f"{ADVISORY_TAG} `grep -c` with alternation detected. "
    "Alternation can inflate the count if one arm matches unexpected lines "
    "(prior incident: PR #258 body claimed 29 cases, Codex corrected to 28). "
    "Verify each arm separately before citing this count:\n"
    "  grep -c 'pat1' file   # arm 1\n"
    "  grep -c 'pat2' file   # arm 2\n"
    "Opt-out: append `# count-verified` to the command."
)

# ---------------------------------------------------------------------------
# grep flag analysis
# ---------------------------------------------------------------------------

# Long flags that consume the next token as their value (not the pattern).
_GREP_LONG_FLAGS_WITH_ARG = frozenset({
    "--max-count",
    "--after-context",
    "--before-context",
    "--context",
    "--file",
    "--label",
    "--include",
    "--exclude",
    "--exclude-dir",
    "--devices",
    "--directories",
    "--binary-files",
    "--color",
    "--colour",
})

# Long flags that consume the next token as the PATTERN.
_GREP_LONG_PATTERN_FLAGS = frozenset({"--regexp"})

# Long flags that enable ERE / PCRE mode (bare `|` is alternation).
_ERE_LONG_FLAGS = frozenset({"--extended-regexp", "--perl-regexp"})

# Single-char flags that consume the next token as their value (not pattern).
# Lowercase 'e' is NOT here — it is the pattern flag, handled separately.
_GREP_SHORT_FLAGS_WITH_ARG = frozenset({"m", "A", "B", "C", "f", "D", "d"})


def _analyze_grep_argv(argv: list[str]) -> tuple[bool, bool, list[str]]:
    """Return (has_count, has_ere, patterns) for a grep argv.

    argv[0] is expected to be `grep` (or a path to grep). Returns three values:
      has_count  — True iff `-c` / `--count` (or combined `-cE` etc.) is present.
      has_ere    — True iff ERE/PCRE mode is active (`-E`, `-P`, long equivalents,
                   or combined short flags like `-cE`).
      patterns   — All detected pattern strings (every `-e`/`--regexp` plus the
                   first positional fallback), or `[]` if none found.

    Fail-open: any IndexError / unexpected shape → return (False, False, []).
    """
    has_count = False
    has_ere = False
    patterns: list[str] = []
    positional: list[str] = []  # non-flag args (first is the pattern)

    i = 1  # skip argv[0] = "grep"
    n = len(argv)
    end_of_flags = False

    while i < n:
        tok = argv[i]

        if end_of_flags:
            positional.append(tok)
            i += 1
            continue

        if tok == "--":
            end_of_flags = True
            i += 1
            continue

        if tok.startswith("--"):
            # ---- Long flag ----
            if tok == "--count":
                has_count = True
            elif tok in _ERE_LONG_FLAGS:
                has_ere = True
            elif tok in _GREP_LONG_PATTERN_FLAGS:
                # --regexp=PAT (embedded value)
                if "=" in tok:
                    patterns.append(tok.split("=", 1)[1])
                elif i + 1 < n:
                    patterns.append(argv[i + 1])
                    i += 2
                    continue
            elif tok.startswith("--regexp="):
                patterns.append(tok[len("--regexp="):])
            elif tok in _GREP_LONG_FLAGS_WITH_ARG:
                i += 2  # skip the flag's value token
                continue
            elif "=" in tok:
                pass  # --flag=value, value is embedded; no separate arg
            i += 1
            continue

        if tok.startswith("-") and len(tok) > 1:
            # ---- Short flag(s) ----
            # tok could be combined: '-cE', '-Pci', '-e', etc.
            flag_chars = tok[1:]  # e.g. "cE", "e", "P"

            has_count = has_count or ("c" in flag_chars)
            has_ere = has_ere or ("E" in flag_chars or "P" in flag_chars)

            if "e" in flag_chars:
                # `-e PATTERN` — next token is the pattern.
                if i + 1 < n:
                    patterns.append(argv[i + 1])
                    i += 2
                    continue
            elif flag_chars in _GREP_SHORT_FLAGS_WITH_ARG:
                # Single flag that takes a value argument.
                i += 2
                continue
            i += 1
            continue

        # ---- Non-flag positional argument ----
        positional.append(tok)
        i += 1

    if not patterns and positional:
        patterns.append(positional[0])

    return has_count, has_ere, patterns


def _has_alternation(pattern: str, ere_mode: bool) -> bool:
    """Return True iff `pattern` contains a regex alternation operator.

    BRE (default grep):   alternation is `\\|` (escaped pipe).
    ERE / PCRE (`-E`/`-P`): alternation is unescaped `|`.

    We walk the pattern char-by-char to handle escape sequences correctly.
    An even number of preceding backslashes means the next `|` is unescaped.
    """
    if not pattern:
        return False

    if not ere_mode:
        # BRE: look for \\| (the two-character sequence backslash-pipe).
        # We can simply check for the substring since a doubled backslash
        # (\\\\|) would be \\\\| in source — the first two backslashes form
        # an escaped backslash, and the second pair is also escaped, so the |
        # here is literal. For the advisory purpose, treating \\| as
        # alternation is a conservative heuristic; false-positives are
        # acceptable (advisory only, not blocking).
        return "\\|" in pattern

    # ERE/PCRE: scan for unescaped `|`.
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            i += 2  # skip the escaped character
            continue
        if ch == "|":
            return True
        i += 1
    return False


# ---------------------------------------------------------------------------
# Command-level checks
# ---------------------------------------------------------------------------

_OPT_OUT_MARKER = "# count-verified"


def _is_grep(token: str) -> bool:
    """True iff the token is (or ends with path-separator followed by) 'grep'."""
    if not token:
        return False
    base = token.rsplit("/", 1)[-1]
    return base == "grep" or base == r"\grep"


def check_command(command: str) -> bool:
    """Return True iff the command should trigger the advisory.

    Parses the command into subcommands (via _hook_utils primitives) and
    returns True on the first grep subcommand that has both a count flag and
    alternation in its pattern, unless the opt-out marker is present.
    """
    if _OPT_OUT_MARKER in command:
        return False

    try:
        tokens = safe_tokenize(command)
    except Exception:
        return False

    for segment in iter_command_starts(tokens):
        try:
            argv = strip_prefix(segment)
        except Exception:
            continue
        if not argv or not _is_grep(argv[0]):
            continue
        try:
            has_count, has_ere, patterns = _analyze_grep_argv(argv)
        except Exception:
            continue
        if has_count and any(
            _has_alternation(p, has_ere) for p in patterns
        ):
            return True

    return False


# ---------------------------------------------------------------------------
# Hook entry point
# ---------------------------------------------------------------------------


@fail_open
def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if not isinstance(payload, dict):
        return 0

    tool_name = payload.get("tool_name") or ""
    if tool_name != "Bash":
        return 0

    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    command = tool_input.get("command")
    if not isinstance(command, str):
        return 0

    if check_command(command):
        sys.stderr.write(ADVISORY_MSG + "\n")

    return 0




if __name__ == "__main__":
    sys.exit(main())
