#!/usr/bin/env python3
"""PreToolUse(Bash) guard: require explicit user approval before `gh pr merge`.

Every Claude session MUST receive per-PR merge approval from the user — merge
is shared-state and irreversible.

Detection: tokenizes the Bash command with the praxis shlex pipeline and scans
for any segment whose argv[0..2] == ("gh", "pr", "merge"). If found, emit
permissionDecision "ask" so Claude Code surfaces a confirmation dialog before
executing the merge.

No opt-out marker and no environment bypass are provided. Issue #180's contract
is that a merge ALWAYS surfaces a per-PR approval prompt — an agent-attachable
comment marker would let the agent silently self-bypass the same gate it is
meant to enforce. The background-agent exemption that #180 originally carved out
was removed in #1055: a delegated worker now runs as an ordinary session with a
live stdin, so the human at the pane answers the prompt just as in a direct
session.
"""
from __future__ import annotations

import json
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    GH_MERGE_VALUE_FLAGS,
    _is_gh_binary,
    is_help_invocation,
    compound_cascade_hint,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)
from block_message import format_block  # type: ignore[import-not-found]  # noqa: E402

# gh global flags that consume one additional argument (value).
# When these appear between `gh` and the subcommand object, they must be
# skipped so the subcommand check lands at the correct argv position.
# Mirrors the same constant in external-write-falsify-check.py.
GH_GLOBAL_FLAGS_WITH_ARG = frozenset({
    "-R", "--repo",
    "--hostname",
    "--color",
})

REASON = format_block(
    rule_name="gh pr merge approval",
    why="merge is shared-state and irreversible — direct interactive sessions "
        "require explicit per-PR merge approval",
    correct_path="confirm this merge when the dialog surfaces; approval is "
        "per-PR and does not transfer to sibling/companion PRs",
    # No agent-attachable bypass by design — a self-bypass would defeat the
    # gate.
    bypass_env=None,
    reference="CLAUDE.md → Pre-Merge Reporting / No Approval Transfer Across "
        "Companion PRs; docs/hook/pre-merge-approval-gate.md",
)


def is_gh_pr_merge(argv: list[str]) -> bool:
    """Return True iff the argv segment is a `gh pr merge` invocation.

    gh global flags (-R/--repo, --hostname, --color) may appear between
    `gh` and the subcommand object. Walk past them before checking so
    `gh -R owner/repo pr merge` is detected correctly.
    """
    argv = strip_prefix(argv)
    if not argv or not _is_gh_binary(argv[0]):
        return False

    # Walk past any global flags (and their arguments) to find the subcommand.
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if not tok.startswith("-"):
            break
        i += 1
        if "=" not in tok and tok in GH_GLOBAL_FLAGS_WITH_ARG and i < len(argv):
            i += 1

    if i + 1 >= len(argv):
        return False
    if argv[i] != "pr" or argv[i + 1] != "merge":
        return False
    # `gh pr merge --help` prints usage and merges nothing (issue #985).
    return not is_help_invocation(argv[i + 2:], GH_MERGE_VALUE_FLAGS)


def emit_ask(reason: str) -> None:
    emit_decision("ask", reason)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed input

    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "") or ""
    if not command.strip():
        return 0

    tokens = safe_tokenize(command)
    if not tokens:
        return 0

    found = any(is_gh_pr_merge(argv) for argv in iter_command_starts(tokens))
    if not found:
        return 0

    emit_ask(REASON + compound_cascade_hint(command))
    return 0


if __name__ == "__main__":
    sys.exit(main())
