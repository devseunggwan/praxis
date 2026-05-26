#!/usr/bin/env python3
"""PreToolUse(Bash) guard: require explicit user approval before `gh pr merge`.

Direct interactive Claude sessions MUST receive per-PR merge approval from the
user — merge is shared-state and irreversible. Background cmux-delegate agents
(identified by CMUX_DELEGATE=1 in their shell environment) may merge without an
extra confirmation gate because the task prompt already carries the delegation
intent.

Detection: tokenizes the Bash command with the praxis shlex pipeline and scans
for any segment whose argv[0..2] == ("gh", "pr", "merge"). If found:

  - CMUX_DELEGATE=1 in the hook's own env → silent pass-through (background
    agent; the delegation intent is the approval).
  - Otherwise → emit permissionDecision "ask" so Claude Code surfaces a
    confirmation dialog before executing the merge.

No opt-out marker is provided. Issue #180's contract is that direct sessions
ALWAYS surface a per-PR approval prompt — an agent-attachable comment marker
would let the agent silently self-bypass the same gate it is meant to enforce.
The only authoritative bypass is CMUX_DELEGATE=1 set in the *session's* shell
env at startup (see note below); inline `env CMUX_DELEGATE=1 gh pr merge` does
not satisfy this because the hook reads its own process env, not the child's.

Note on inline env prefix (`env CMUX_DELEGATE=1 gh pr merge ...`):
The hook reads its OWN process environment, not the environment of the child
command. An inline `env CMUX_DELEGATE=1` prefix only sets the env for the child
command, NOT for this hook process. Therefore `env CMUX_DELEGATE=1 gh pr merge`
issued from a non-delegate session still triggers the approval gate. This is
intentional — the only authoritative delegation signal is CMUX_DELEGATE=1 set
in the session's shell environment at startup.
"""
from __future__ import annotations

import json
import os
import sys
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    compound_cascade_hint,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)

# gh global flags that consume one additional argument (value).
# When these appear between `gh` and the subcommand object, they must be
# skipped so the subcommand check lands at the correct argv position.
# Mirrors the same constant in external-write-falsify-check.py.
GH_GLOBAL_FLAGS_WITH_ARG = frozenset({
    "-R", "--repo",
    "--hostname",
    "--color",
})

REASON = (
    "gh pr merge detected in a direct interactive session. "
    "Merge is shared-state and irreversible — direct sessions require "
    "explicit per-PR merge approval. "
    "If you are running as a cmux-delegate background agent, set "
    "CMUX_DELEGATE=1 in the session environment at startup."
)


def is_gh_pr_merge(argv: list[str]) -> bool:
    """Return True iff the argv segment is a `gh pr merge` invocation.

    gh global flags (-R/--repo, --hostname, --color) may appear between
    `gh` and the subcommand object. Walk past them before checking so
    `gh -R owner/repo pr merge` is detected correctly.
    """
    argv = strip_prefix(argv)
    if not argv or argv[0] != "gh":
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
    return argv[i] == "pr" and argv[i + 1] == "merge"


def emit_ask(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    sys.stdout.write("\n")


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

    # Background cmux-delegate agents carry CMUX_DELEGATE=1 in their env.
    # This is the hook's OWN env — inline `env CMUX_DELEGATE=1 gh pr merge`
    # does NOT satisfy this check (see module docstring).
    if os.environ.get("CMUX_DELEGATE") == "1":
        return 0

    emit_ask(REASON + compound_cascade_hint(command))
    return 0


if __name__ == "__main__":
    sys.exit(main())
