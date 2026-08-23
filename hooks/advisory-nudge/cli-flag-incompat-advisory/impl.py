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
  • **Role-aware tokenization (issue #263)** — uses `tokenize_with_roles`
    from `_hook_utils.py` so each check receives typed Token segments
    (COMMAND / FLAG / FLAG_VALUE / POSITIONAL / SEPARATOR_DD / POST_DD /
    SUBST_RUN). Previously each caller re-implemented role state which
    leaked 7 defects across PR #251 / #252 codex review rounds. The
    proof-migration in this file (issue #263 Phase 1) demonstrates the
    same 4 codex-review-passed cases (R1 false positive, R2 `$()`
    advisory, R3 `--` boundary, R3 relative path) with strictly less
    caller-side parsing.

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
import sys
from pathlib import Path
from typing import Callable, Optional
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    Token,
    TokenRole,
    filter_argv,
    tokenize_with_roles,
)

# Shell grouping / command-substitution chars that may prefix a binary token
# when it sits inside a subshell or substitution (`(git …)`, `$(git …)`,
# `` `git …` ``). Stripped before the basename comparison so the binary-name
# check is not fooled by the wrapper syntax. Mirrors `_is_git_binary` in the
# commit-gate hooks and `_is_gh_binary` in `_hook_utils`.
_GROUP_PREFIX_CHARS = "(){}$`"


def _is_git_binary(token: str) -> bool:
    stripped = token.lstrip(_GROUP_PREFIX_CHARS)
    return stripped == "git" or stripped.endswith("/git")


# ---------------------------------------------------------------------------
# Role-aware flag-value spec (issue #263)
# ---------------------------------------------------------------------------
#
# Migrated from per-caller `_coalesce_subst_runs` + ad-hoc value-flag sets
# to the centralized `tokenize_with_roles` API. The spec is keyed by
# `(command, subcommand)` so `git -C /tmp merge-tree --merge-base A` is
# resolved with both git-global and merge-tree-specific value flags.
#
# Source:
#   - git globals: `git --help` — -C, -c key=val, --git-dir, --work-tree,
#     --namespace
#   - git merge-tree: `git merge-tree -h` —
#       --[no-]merge-base <tree-ish>
#       -X, --[no-]strategy-option <option=value>
#     Negation forms (`--no-merge-base`) are valueless suppression markers
#     and are intentionally NOT in this set.
_FLAG_VALUE_SPEC: dict[str, set[str]] = {
    "git": {"-C", "-c", "--git-dir", "--work-tree", "--namespace"},
    "git merge-tree": {"--merge-base", "-X", "--strategy-option"},
    "kubectl": set(),
}


# ---------------------------------------------------------------------------
# git merge-tree advisory
# ---------------------------------------------------------------------------

# `--name-only` is the canonical trip-wire flag of modern merge-tree. Other
# modern-mode flags (`--write-tree`, `--[no-]messages`, `--no-stat`) trigger
# the same legacy/modern conflict but `--name-only` accounts for ~all of the
# observed friction. Keep the trip-wire set small — false-positives here are
# the primary cost.
_MERGE_TREE_MODERN_FLAGS = frozenset({"--name-only", "--write-tree"})


def _count_merge_tree_positionals(seg: list[Token]) -> int:
    """Count positional (non-flag) arguments to `git merge-tree` from a
    typed Token segment.

    The role-aware tokenizer has already:
      - coalesced unquoted `$(...)` runs into single tokens (SUBST_RUN
        when free, FLAG_VALUE when consumed by a value-taking flag)
      - classified value tokens as FLAG_VALUE (excluded from positional
        count)
      - marked the `--` boundary; any POST_DD tokens count as positionals
        per merge-tree's POSIX convention

    Returns the positional count starting from the token AFTER the
    `merge-tree` subcommand token, or -1 if this is not a merge-tree
    invocation.
    """
    argv = filter_argv(seg)
    if not argv or not _is_git_binary(argv[0].text):
        return -1

    # Find the merge-tree subcommand token. Skip past command-global
    # FLAG / FLAG_VALUE pairs (already typed correctly).
    i = 1
    n = len(argv)
    while i < n:
        tok = argv[i]
        if tok.role in (TokenRole.FLAG, TokenRole.FLAG_VALUE):
            i += 1
            continue
        if tok.role == TokenRole.POSITIONAL and tok.text == "merge-tree":
            break
        # Some other token (SUBST_RUN, SEPARATOR_DD, unrelated POSITIONAL)
        # — not a merge-tree invocation.
        return -1
    else:
        return -1

    # Skip the `merge-tree` token itself.
    i += 1

    positionals = 0
    while i < n:
        tok = argv[i]
        if tok.role == TokenRole.POSITIONAL:
            positionals += 1
        elif tok.role == TokenRole.SUBST_RUN:
            # A free $() run that is NOT bound to a value-flag is a
            # positional argument from the shell's perspective.
            positionals += 1
        elif tok.role == TokenRole.POST_DD:
            # Tokens after `--` are positional per POSIX convention.
            positionals += 1
        # FLAG / FLAG_VALUE / SEPARATOR_DD / COMMAND do NOT increment
        # the positional count.
        i += 1
    return positionals


def _check_git(seg: list[Token]) -> Optional[str]:
    """Return advisory text for known git mode-incompatible combos, else None."""
    argv = filter_argv(seg)
    if not argv or not _is_git_binary(argv[0].text):
        return None

    # merge-tree: --name-only + 3+ positionals → legacy/modern conflict
    # We strip the `=value` suffix on FLAG tokens when checking for modern
    # mode trip-wires so `--name-only=true` (unusual but legal) still
    # triggers the check.
    has_modern_flag = any(
        tok.role == TokenRole.FLAG
        and tok.text.split("=", 1)[0] in _MERGE_TREE_MODERN_FLAGS
        for tok in argv
    )
    if has_modern_flag:
        positionals = _count_merge_tree_positionals(seg)
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


def _check_kubectl(seg: list[Token]) -> Optional[str]:
    """Return advisory text when a deprecated kubectl flag appears
    BEFORE the `--` argv separator, else None.

    The role-aware tokenizer already marks post-`--` tokens as POST_DD,
    so this function simply iterates FLAG tokens and is naturally immune
    to the `kubectl exec pod -- mytool --use-protocol-buffers` false
    positive (R3) — the deprecated flag would be POST_DD in that case,
    not FLAG.
    """
    argv = filter_argv(seg)
    if not argv or argv[0].text != "kubectl":
        return None
    hits: list[str] = []
    for tok in argv[1:]:
        if tok.role != TokenRole.FLAG:
            continue
        bare = tok.text.split("=", 1)[0]
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

CHECKS: tuple[Callable[[list[Token]], Optional[str]], ...] = (
    _check_git,
    _check_kubectl,
)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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

    segments = tokenize_with_roles(
        command.replace("\\\n", " "),
        _FLAG_VALUE_SPEC,
    )
    if not segments:
        return 0

    for seg in segments:
        for check in CHECKS:
            msg = check(seg)
            if msg:
                sys.stderr.write(msg + "\n")
                return 0  # advisory — never blocks; first match suffices

    return 0




if __name__ == "__main__":
    sys.exit(main())
