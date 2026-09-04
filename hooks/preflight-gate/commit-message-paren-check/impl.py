#!/usr/bin/env python3
"""PreToolUse(Bash) guard: reject a commit message release-please cannot parse.

release-please parses a commit with `@conventional-commits/parser`, and that
parser tries EVERY LINE of the message as a `type(scope): summary` header. A
line whose leading non-space run is immediately followed by `(` opens that
paren as a *scope*, and inside a scope only `)` is valid — so the line is
rejected when the paren does not close cleanly:

  nested    another `(` opens first      -> unexpected token '('
  unclosed  the line ends first          -> unexpected token '\\n'

A rejected commit is SKIPPED, and the release workflow still ends
`completed/success`, so the commit silently loses its CHANGELOG entry. Three
commits were dropped from two releases that way before anyone read the log
(issue #1228). The loss is permanent: release-please computes the next release
from the previous tag, so a commit skipped once never returns.

Detection paths (message text, not just the title):
  git commit -m/-F ...                     -> `extract_git_message_texts`
  git commit -m "$(cat <<'EOF' … EOF)"     -> the heredoc body, since the
                                              tokenizer only ever sees `$(cat`

Config:
  PRAXIS_COMMIT_PAREN_STRICT — default "1" (block, exit 2); "0" -> advisory
                               (exit 0, message to stderr)
"""
from __future__ import annotations

import os
import sys
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    compound_cascade_hint,
    heredoc_bodies,
    iter_command_starts,
    safe_tokenize,
)
from _payload import read_bash_payload  # type: ignore[import-not-found]  # noqa: E402
from block_message import format_block  # type: ignore[import-not-found]  # noqa: E402
from git_commit_titles import (  # type: ignore[import-not-found]  # noqa: E402
    _scan_commit_message_values,
    extract_git_message_texts,
)

STRICT_ENV = "PRAXIS_COMMIT_PAREN_STRICT"


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

def offending_lines(message: str) -> list[tuple[int, str, str]]:
    """Every `(lineno, kind, line)` the parser would reject in `message`.

    Each condition below is a PASS the real parser was measured to grant, not a
    guess about its grammar — see spec.md for the probe table:

      no `(`, or `(` at column 1   the scope position is never entered
      whitespace before the `(`    the word ends, so no scope opens
      `!` or `:` before the `(`    the separator was already consumed
      the `(` closes first         the scope closed; later parens are free text
    """
    hits: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(message.split("\n"), 1):
        idx = line.find("(")
        if idx <= 0:
            continue
        prefix = line[:idx]
        if any(ch.isspace() for ch in prefix):
            continue
        if "!" in prefix or ":" in prefix:
            continue
        kind = "unclosed"
        for ch in line[idx + 1:]:
            if ch == ")":
                kind = ""
                break
            if ch == "(":
                kind = "nested"
                break
        if kind:
            hits.append((lineno, kind, line))
    return hits


# ---------------------------------------------------------------------------
# Message sources
# ---------------------------------------------------------------------------

def _message_texts(argv: list[str], command: str) -> list[str]:
    """Every commit message text this argv contributes.

    The heredoc fallback is scoped to a `git commit` argv that yielded no
    readable message: that is precisely the `-m "$(cat <<'EOF' …)"` and
    `-F -` shape, where the heredoc body IS the message and nothing else in
    the command produced one. Reading heredocs unconditionally would grade
    prose belonging to some other command in the same chain.
    """
    texts = extract_git_message_texts(argv, command)
    if texts:
        return texts
    if _scan_commit_message_values(argv) is None:
        return []
    return heredoc_bodies(command)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _build_message(hits: list[tuple[int, str, str]], command: str) -> str:
    lineno, kind, line = hits[0]
    detail = "\n".join(
        f"  line {n}: [{k}] {ln!r}" for n, k, ln in hits[:5]
    )
    more = "" if len(hits) <= 5 else f"\n  … and {len(hits) - 5} more"
    base = format_block(
        rule_name="commit-message-paren-check",
        why=(
            "release-please cannot parse this commit message, so the commit is "
            "skipped and loses its CHANGELOG entry while the release workflow "
            "still reports success (issue #1228). Every line is parsed as a "
            "`type(scope):` header, and a leading word glued to `(` opens a "
            "scope that must close on the same line.\n"
            f"{detail}{more}"
        ),
        correct_path=(
            "put a space before the `(`, move the parenthesised text off the "
            f"start of line {lineno}, or close the paren on that line"
        ),
        bypass_env=STRICT_ENV,
        bypass_reason_hint="=0 to switch to advisory mode (exit 0, stderr warning only)",
        reference="hooks/preflight-gate/commit-message-paren-check/spec.md",
    )
    return base + compound_cascade_hint(command)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

@fail_open
def main() -> int:
    parsed = read_bash_payload()
    if parsed is None:
        return 0  # non-Bash tool or malformed stdin — fail-open
    payload, raw_command = parsed
    if not raw_command.strip():
        return 0

    # Collapse backslash-newline continuations (mirrors sibling hooks). Only
    # the tokenizer gets the collapsed form: a heredoc body is data, and a
    # message line ending in `\` would be silently joined to the next one.
    command = raw_command.replace("\\\n", " ")

    tokens = safe_tokenize(command)
    if not tokens:
        return 0

    for argv in iter_command_starts(tokens):
        for text in _message_texts(argv, raw_command):
            hits = offending_lines(text)
            if not hits:
                continue
            reason = _build_message(hits, command)
            if os.environ.get(STRICT_ENV, "1") != "0":
                sys.stderr.write(reason + "\n")
                return 2
            sys.stderr.write(
                f"[commit-message-paren-check] ADVISORY (STRICT=0):\n{reason}\n"
            )
            return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
