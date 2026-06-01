#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block git commit when a rename sweep has survivors.

A rename sweep is N≥3 identical 1:1 identifier substitutions in a single
commit (SWEEP_THRESHOLD=3, MIN_TOKEN_LEN=5). When detected, the hook checks
whether the old token still exists anywhere in the tracked tree. If survivors
are found, the commit is blocked — the agent must either include the remaining
occurrences in the same commit or mark them exempt.

Root cause: an agent narrates "renamed X→Y across the codebase" but only
stages a subset of files. The old token survives in unstaged files and appears
as a partial-fix sibling gap at review round 4–5.

Diff parser note:
  git diff --unified=0 emits consecutive-line changes as a grouped hunk:
    -line1 / -line2 / -line3 / +line1 / +line2 / +line3
  A simple adjacent-pair algorithm counts only 1 pair for this layout and falls
  below SWEEP_THRESHOLD=3. This hook uses a buffer-flush parser that handles
  both alternating and grouped hunk layouts.

Block condition:
  (a) Bash tool contains a live git commit (not --amend / rebase / merge)
  (b) Staged diff contains ≥SWEEP_THRESHOLD identical 1:1 renames
  (c) The old token still exists in the tracked tree

Allow conditions:
  - PRAXIS_SKIP_RENAME_SWEEP_CHECK=1 env var
  - Append `# [rename-sweep-exempt]` on the surviving line
  - git commit --amend / merge / rebase / cherry-pick / revert are not blocked

Exits 2 (PreToolUse blocking code) when survivors detected.
Exits 0 otherwise (including all fail-open cases).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from block_message import format_block  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    compound_cascade_hint,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SWEEP_THRESHOLD = 3
MIN_TOKEN_LEN = 5
EXEMPT_MARKER = "# [rename-sweep-exempt]"

_IDENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]+")

# git global flags that consume the next token as a value — must be skipped
# when scanning for the subcommand position.
_GIT_GLOBAL_VALUE_FLAGS: frozenset[str] = frozenset(
    {"-C", "--git-dir", "--work-tree", "--namespace", "--config-env",
     "--exec-path", "--super-prefix"}
)

# git commit flags that mean this is not a fresh content commit.
_EXEMPT_COMMIT_FLAGS: frozenset[str] = frozenset({"--amend", "--no-edit"})

# ---------------------------------------------------------------------------
# Detection: is this a live git commit?
# ---------------------------------------------------------------------------


def _is_git_binary(token: str) -> bool:
    """True iff token names the git binary (bare or path-prefixed)."""
    stripped = token.lstrip("(){}$`")
    return stripped == "git" or stripped.endswith("/git")


def _find_git_commits(command: str) -> bool:
    """Return True iff the command contains a live `git commit` invocation."""
    tokens = safe_tokenize(command)
    if not tokens:
        return False

    for argv in iter_command_starts(tokens):
        argv = strip_prefix(argv)
        if not argv or not _is_git_binary(argv[0]):
            continue

        # Skip past git global flags to find the subcommand.
        i = 1
        while i < len(argv):
            tok = argv[i]
            if tok in _GIT_GLOBAL_VALUE_FLAGS and i + 1 < len(argv):
                i += 2
                continue
            if tok.startswith("-"):
                i += 1
                continue
            break  # first non-flag = subcommand

        if i >= len(argv) or argv[i] != "commit":
            continue

        # Exempt: --amend and similar flags that are not fresh content commits.
        commit_args = argv[i + 1:]
        if any(tok in _EXEMPT_COMMIT_FLAGS for tok in commit_args):
            continue

        return True

    return False


# ---------------------------------------------------------------------------
# Sweep detection (buffer-flush diff parser)
# ---------------------------------------------------------------------------


def _detect_sweeps(diff: str) -> list[tuple[str, str, int]]:
    """Return (before, after, count) tuples for sweeps at or above threshold.

    Handles two hunk layouts emitted by git diff --unified=0:
      (a) alternating: -line / +line / -line / +line
      (b) grouped:     -line1 / -line2 / +line1 / +line2   (consecutive lines)

    Buffer-flush: accumulate removed lines into a buffer. When the run ends
    (context line, file header, or removal following additions), pair the
    buffered removed and added lines positionally. Alternating layout flushes
    a 1-element buffer each time — this is a strict superset of the adjacent-
    pair algorithm.
    """
    pairs: list[tuple[str, str]] = []
    removed_buf: list[str] = []
    added_buf: list[str] = []

    def _flush() -> None:
        if removed_buf and added_buf and len(removed_buf) == len(added_buf):
            pairs.extend(zip(removed_buf, added_buf))
        removed_buf.clear()
        added_buf.clear()

    for line in diff.split("\n"):
        if line.startswith("---") or line.startswith("+++"):
            _flush()
        elif line.startswith("-"):
            if added_buf:
                _flush()
            removed_buf.append(line[1:])
        elif line.startswith("+"):
            added_buf.append(line[1:])
        else:
            _flush()
    _flush()

    counter: Counter[tuple[str, str]] = Counter()
    for before_line, after_line in pairs:
        b_tokens = set(_IDENT_RE.findall(before_line))
        a_tokens = set(_IDENT_RE.findall(after_line))
        removed = b_tokens - a_tokens
        added = a_tokens - b_tokens
        if len(removed) == 1 and len(added) == 1:
            b_tok, a_tok = removed.pop(), added.pop()
            if len(b_tok) >= MIN_TOKEN_LEN and len(a_tok) >= MIN_TOKEN_LEN:
                counter[(b_tok, a_tok)] += 1

    return [(b, a, c) for (b, a), c in counter.items() if c >= SWEEP_THRESHOLD]


# ---------------------------------------------------------------------------
# Survivor check
# ---------------------------------------------------------------------------


def _survivors(token: str) -> list[str]:
    """Return non-exempt lines in the tracked tree that still contain token."""
    result = subprocess.run(
        ["git", "grep", "-n", "--fixed-strings", token],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    lines = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        if EXEMPT_MARKER in line:
            continue
        lines.append(line)
    return lines


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _emit_block(before: str, after: str, count: int, s_lines: list[str], command: str) -> None:
    shown = s_lines[:5]
    extra = len(s_lines) - len(shown)
    survivor_text = "\n".join(f"  {ln}" for ln in shown)
    if extra:
        survivor_text += f"\n  ... +{extra} more"

    msg = format_block(
        rule_name="rename-sweep-survivors",
        why=f"rename sweep detected ({count}x): '{before}' → '{after}' — {len(s_lines)} survivor(s) in tracked tree",
        correct_path=(
            "(a) stage the surviving files in this commit (complete the sweep)"
            f"  (b) append `{EXEMPT_MARKER}` on each surviving line"
            "  (c) split the commit if the survivors are unrelated context"
        ),
        bypass_env="PRAXIS_SKIP_RENAME_SWEEP_CHECK",
        reference="CLAUDE.md §Atomic Commits",
    )
    sys.stderr.write(msg)
    sys.stderr.write(f"\nSurvivors ({len(s_lines)}):\n{survivor_text}\n")
    sys.stderr.write(compound_cascade_hint(command))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


@fail_open
def main() -> int:
    if os.environ.get("PRAXIS_SKIP_RENAME_SWEEP_CHECK") == "1":
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "") or ""
    if not command.strip():
        return 0

    command = command.replace("\\\n", " ")

    if not _find_git_commits(command):
        return 0

    diff = subprocess.run(
        ["git", "diff", "--cached", "--unified=0"],
        capture_output=True,
        text=True,
    ).stdout

    if not diff.strip():
        return 0

    sweeps = _detect_sweeps(diff)
    if not sweeps:
        return 0

    for before, after, count in sweeps:
        s_lines = _survivors(before)
        if s_lines:
            _emit_block(before, after, count, s_lines, command)
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
