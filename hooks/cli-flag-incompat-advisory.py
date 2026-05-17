#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: nudge `<cli> <subcmd> --help` before known
mode-incompatible flag combinations land on external CLIs.

Companion to `gh-flag-verify.py` — that hook hard-blocks (exit 2) when a
gh subcommand sees an unknown flag, leveraging the deterministic gh COMPAT
table. This hook is the **advisory** counterpart for other CLIs (`git`,
`kubectl`) where:

  • the surface area is much larger than gh
  • flag-subcommand tables are not as cleanly versioned
  • false-positives on a hard block would cost more than a missed nudge

Trigger surface (issue #248):

  • `git merge-tree --name-only <3+ positional args>`
    `--name-only` is part of the modern `--write-tree` mode, but supplying
    3 positional args drops merge-tree into the deprecated `--trivial-merge`
    mode which does NOT accept `--name-only`. The resulting error message
    references `--trivial-merge` even though the caller never wrote it,
    making the failure non-obvious.

  • `kubectl --use-protocol-buffers`
    Deprecated since Kubernetes 1.27 — kubectl prints a deprecation warning
    but still accepts the flag, so the warning often goes unnoticed in long
    pipelines.

Design:

  • **Advisory only** — writes to stderr, exits 0. Never blocks the command.
  • **Plugin-style dispatch** — each CLI gets a `_check_<cli>` function that
    returns Optional[str] (the advisory text). New CLIs are added by writing
    a new check function and registering it in CHECKS.
  • **Sibling-pattern toolchain** — reuses `safe_tokenize`,
    `iter_command_starts`, `strip_prefix` from `_hook_utils.py`, matching
    `gh-flag-verify.py` / `cross-boundary-preflight.py` / `block-gh-state-all.py`.

Fail-open contract:

  • malformed JSON / non-Bash payload → exit 0
  • empty command → exit 0
  • any uncaught exception in the inner logic → swallowed, exit 0

Relationship to memory entry `feedback_external_cli_verify_first` —
memory has documented the rule for ≥1 prior incident, but retrieval at
command-composition time keeps failing. This hook moves a fragment of
the enforcement to the Bash boundary. The memory entry still covers the
broader "verify with --help before guessing" rule for CLIs not enumerated
here.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)


# ---------------------------------------------------------------------------
# git merge-tree advisory
# ---------------------------------------------------------------------------

# `--name-only` is the canonical trip-wire flag of modern merge-tree. Other
# modern-mode flags (`--write-tree`, `--[no-]messages`, `--no-stat`) trigger
# the same legacy/modern conflict but `--name-only` accounts for ~all of the
# observed friction. Keep the trip-wire set small — false-positives here are
# the primary cost.
_MERGE_TREE_MODERN_FLAGS = frozenset({"--name-only", "--write-tree"})

# git options that consume the following token as a value; needed so the
# positional-argument count is not inflated by `-C <dir>` / similar.
_GIT_GLOBAL_FLAGS_WITH_ARG = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace",
})

# `git merge-tree` subcommand-level flags that consume the next token as
# their value. Without this set, `--merge-base A --name-only HEAD origin/main`
# (the canonical correct modern usage) counts `A` as a 3rd positional and
# trips the advisory. Source: `git merge-tree -h` —
#   --[no-]merge-base <tree-ish>
#   -X, --[no-]strategy-option <option=value>
# Negation forms (`--no-merge-base`) are valueless suppression markers and
# are intentionally NOT in this set.
_MERGE_TREE_FLAGS_WITH_ARG = frozenset({
    "--merge-base",
    "-X", "--strategy-option",
})


def _coalesce_subst_runs(tokens: list[str]) -> list[str]:
    """Collapse unquoted `$(...)` command-substitution runs into single tokens.

    `safe_tokenize` uses shlex with whitespace_split=True, which is unaware
    of POSIX command substitution. An unquoted `$(git merge-base HEAD x)`
    therefore splits into `['$(git', 'merge-base', 'HEAD', 'x)']` — four
    tokens — and any flag-value-skip logic only consumes the first token.
    The remaining tokens then look like positional arguments and inflate
    the count.

    This helper walks the token list and merges any run that starts with a
    token containing `$(` and ends with a token containing the matching `)`
    into a single logical token. Quoted substitutions (e.g. `"$(...)"`)
    are already a single token at this layer and need no special handling.

    The merge is conservative — `$(` and `)` are required to be present in
    SOME token (not necessarily at start/end) so the helper correctly
    handles tokens like `--merge-base=$(git` ... `merge-base` ... `x)`.
    Unbalanced runs (`$(` with no closing `)`) fall through to the end of
    argv and are treated as a single token — the caller still sees them
    as one element rather than several spurious positionals.
    """
    out: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if "$(" in tok and ")" not in tok[tok.index("$(") + 2:]:
            j = i + 1
            parts = [tok]
            while j < n:
                parts.append(tokens[j])
                if ")" in tokens[j]:
                    break
                j += 1
            out.append(" ".join(parts))
            i = j + 1
        else:
            out.append(tok)
            i += 1
    return out


def _count_merge_tree_positionals(argv: list[str]) -> int:
    """Count positional (non-flag) arguments to `git merge-tree`.

    Walks argv past git-level globals, finds `merge-tree`, then counts
    every non-flag token after the subcommand. Value tokens consumed by
    known value-taking flags are NOT counted. Unquoted `$(...)` runs are
    coalesced via `_coalesce_subst_runs` so they count as exactly one
    logical argument, matching what the shell will pass to git.
    """
    argv = _coalesce_subst_runs(argv)
    i = 0
    n = len(argv)
    # Skip past git global flags (the caller already verified argv[0]=="git"
    # via strip_prefix; this function is only called after that check).
    i = 1
    while i < n:
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if not tok.startswith("-"):
            break
        i += 1
        if "=" not in tok and tok in _GIT_GLOBAL_FLAGS_WITH_ARG and i < n:
            i += 1

    if i >= n or argv[i] != "merge-tree":
        return -1  # not a merge-tree call
    i += 1

    positionals = 0
    while i < n:
        tok = argv[i]
        if tok == "--":
            i += 1
            positionals += (n - i)
            break
        if not tok.startswith("-"):
            positionals += 1
            i += 1
            continue
        # Token is a flag. If it consumes a separate value token (and the
        # value is not already embedded via `=value` form), skip the next
        # token so the value is not miscounted as a positional argument.
        if "=" not in tok and tok in _MERGE_TREE_FLAGS_WITH_ARG and i + 1 < n:
            i += 2
            continue
        i += 1
    return positionals


def _check_git(argv: list[str]) -> Optional[str]:
    """Return advisory text for known git mode-incompatible combos, else None."""
    argv = strip_prefix(argv)
    if not argv or argv[0] != "git":
        return None

    # merge-tree: --name-only + 3+ positionals → legacy/modern conflict
    if any(tok in _MERGE_TREE_MODERN_FLAGS for tok in argv):
        positionals = _count_merge_tree_positionals(argv)
        if positionals >= 3:
            return (
                "[cli-flag-incompat-advisory] git merge-tree mode conflict\n"
                "\n"
                "  --name-only / --write-tree are modern-mode options. Supplying\n"
                "  3+ positional args drops merge-tree into the deprecated\n"
                "  --trivial-merge mode which does NOT accept --name-only.\n"
                "\n"
                "  Error you will see:\n"
                "    fatal: --trivial-merge is incompatible with all other options\n"
                "\n"
                "  Fix: pass 1 or 2 branches (modern mode), e.g.\n"
                "    git merge-tree --name-only HEAD origin/main\n"
                "  or pass --merge-base explicitly — quote any command\n"
                "  substitution so the shell does not word-split the value:\n"
                "    git merge-tree --merge-base \"$(git merge-base HEAD origin/main)\" "
                "--name-only HEAD origin/main\n"
                "    git merge-tree --merge-base=\"$(git merge-base HEAD origin/main)\" "
                "--name-only HEAD origin/main\n"
                "\n"
                "  Verify with: git merge-tree --help"
            )
    return None


# ---------------------------------------------------------------------------
# kubectl deprecation advisory (nice-to-have per issue #248 AC)
# ---------------------------------------------------------------------------

_KUBECTL_DEPRECATED = {
    "--use-protocol-buffers": (
        "Removed in kubectl ≥1.27 — kubectl now negotiates protobuf "
        "automatically and the flag is rejected as unknown."
    ),
}


def _check_kubectl(argv: list[str]) -> Optional[str]:
    argv = strip_prefix(argv)
    if not argv or argv[0] != "kubectl":
        return None
    hits: list[str] = []
    for tok in argv[1:]:
        bare = tok.split("=", 1)[0]
        if bare in _KUBECTL_DEPRECATED:
            hits.append(f"  {bare}: {_KUBECTL_DEPRECATED[bare]}")
    if not hits:
        return None
    return (
        "[cli-flag-incompat-advisory] kubectl deprecated flag\n"
        "\n"
        + "\n".join(hits)
        + "\n\n  Verify with: kubectl --help | grep <flag>"
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

CHECKS: tuple[Callable[[list[str]], Optional[str]], ...] = (
    _check_git,
    _check_kubectl,
)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _main_inner() -> int:
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

    for argv in iter_command_starts(tokens):
        argv = list(argv)
        for check in CHECKS:
            msg = check(argv)
            if msg:
                sys.stderr.write(msg + "\n")
                return 0  # advisory — never blocks; first match suffices

    return 0


def main() -> int:
    """Advisory hook — must NEVER break tool execution."""
    try:
        return _main_inner()
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
