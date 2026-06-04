#!/usr/bin/env python3
"""PreToolUse(Bash) guard: enforce 50-character limit on git commit titles.

Global CLAUDE.md rule: "Git Commit & Title Rules — Title: max 50 characters".
This hook intercepts AI-authored `git commit` Bash calls before they execute
and emits permissionDecision "ask" when the first line of the commit message
exceeds the configured maximum (default 50, override via CLAUDE_COMMIT_TITLE_MAX).

Why a PreToolUse hook instead of a git commit-msg hook:
  The praxis distribution model ships Claude Code hooks (loaded via hooks.json).
  A git commit-msg hook would require installation into every repo's .git/hooks/
  directory — an out-of-band setup step that is easy to miss, not portable across
  worktrees, and breaks when a repo is freshly cloned. A PreToolUse hook fires
  centrally for every AI-authored Bash call in any repo/worktree, with no per-repo
  setup required. Trade-off: it only catches AI-authored commits (not manual shell
  commits), which is exactly the population that produced the silent violations.

Detection path:
  git commit [-m|-F|--message|--file] <value>  →  extract title (first line)
  len(title) > MAX                              →  emit permissionDecision "ask"

Opt-out: embed `# title-length:ack` anywhere in the command to bypass.
Skip: Merge / Revert commits, -F - (stdin body), unreadable -F files.
Config: CLAUDE_COMMIT_TITLE_MAX=<n> (integer ≥ 1) overrides default 50.
"""
from __future__ import annotations

import json
import os
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    compound_cascade_hint,
    iter_command_starts,
    safe_tokenize,
)
from git_commit_titles import (  # type: ignore[import-not-found]  # noqa: E402
    extract_git_titles,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_MAX = 50
OPT_OUT_MARKER = "# title-length:ack"

# Prefixes that indicate auto-generated merge/revert commits — skip them.
SKIP_PREFIXES = ("Merge ", "Revert ")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_max() -> int:
    """Read CLAUDE_COMMIT_TITLE_MAX; fall back to DEFAULT_MAX on invalid value."""
    raw = os.environ.get("CLAUDE_COMMIT_TITLE_MAX", "")
    if raw.strip():
        try:
            val = int(raw.strip())
            if val >= 1:
                return val
        except ValueError:
            pass
    return DEFAULT_MAX


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _emit_ask(reason: str) -> None:
    emit_decision("ask", reason)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed stdin

    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "") or ""
    if not command.strip():
        return 0

    if OPT_OUT_MARKER in command:
        return 0

    # Bash line continuation: collapse `\<newline>` to a single space so
    # multi-line invocations like `git commit \\\n  -m "..."` parse as one
    # command. Mirrors the same pre-tokenize step in
    # `external-write-falsify-check.py` and `block-gh-state-all.py`.
    command = command.replace("\\\n", " ")

    tokens = safe_tokenize(command)
    if not tokens:
        return 0

    max_len = _get_max()

    for argv in iter_command_starts(tokens):
        titles = extract_git_titles(argv)
        for title in titles:
            if any(title.startswith(p) for p in SKIP_PREFIXES):
                continue
            length = len(title)
            if length > max_len:
                _emit_ask(
                    f"Commit title too long: {length} chars (max {max_len}).\n"
                    f"Title: {title!r}\n"
                    "Shorten to ≤50 chars, or embed `# title-length:ack` to bypass."
                    + compound_cascade_hint(command)
                )
                return 0  # ask emitted; only report first violation

    return 0


if __name__ == "__main__":
    sys.exit(main())
