#!/usr/bin/env python3
"""PreToolUse(Bash) guard: enforce branch naming convention at branch creation.

Global CLAUDE.md rule: "Git Commit & Title Rules — use Conventional Commits format".
This hook intercepts AI-authored branch-creation Bash calls and blocks (exit 2)
or warns (exit 0 + stderr) when the new branch name does not match the configured
regex.

Intercepted creation commands:
  git checkout -b <name>
  git checkout --orphan <name>        (new unborn branch)
  git checkout --track origin/<name>  (implicit local branch from remote ref basename)
  git switch -c <name>
  git switch --create <name>
  git switch --force-create <name>    (long form of -C)
  git switch --orphan <name>
  git switch --track origin/<name>    (implicit local branch from remote ref basename)
  git branch <name> [<start>]         (plain creation, no -d/-m/-r/-a/query flags)
  git worktree add <path> -b <name>
  git worktree add -b <name> <path>   (reversed argument order)
  git worktree add <path>             (implicit: branch = basename(path))
  git worktree add --orphan <path>    (implicit: orphan branch = basename(path))

Bare checkout/switch without -b/-c (NOT a creation) → always pass.
git branch -d/-D/-m/-M/-r/-a/--list/--format/--sort/... → always pass.

Config:
  PRAXIS_BRANCH_NAME_REGEX    — pattern to match (default below)
  PRAXIS_BRANCH_NAME_STRICT   — "1" (block, default) / "0" (advisory only)
  PRAXIS_BRANCH_NAME_WHITELIST — comma-separated always-allow names
                                 (default: main,master,dev,prod,staging)

Default regex:
  ^(hub|issue)-[0-9]+-(feat|fix|docs|style|refactor|chore|test|perf|ci|build|hotfix)-[a-z0-9-]+$
"""
from __future__ import annotations

import json
import os
import re
import sys
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)
from block_message import format_block  # type: ignore[import-not-found]  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------

DEFAULT_REGEX = (
    r"^(hub|issue)-[0-9]+-(feat|fix|docs|style|refactor|chore|test|perf|ci|build|hotfix)-[a-z0-9-]+$"
)
DEFAULT_WHITELIST = {"main", "master", "dev", "prod", "staging"}

# Git global flags that appear between `git` and the subcommand and consume the next token.
_GIT_GLOBAL_FLAGS_WITH_ARG = frozenset({
    "-C", "-c",
    "--git-dir", "--work-tree", "--namespace",
    "--exec-path", "--super-prefix",
    "--config-env", "--attr-source",
    "-L", "--list-cmds",
})
# Bare git global flags (no argument consumed).
_GIT_GLOBAL_BARE_FLAGS = frozenset({
    "--no-pager", "--paginate", "-p",
    "--bare", "--no-replace-objects",
    "--no-lazy-fetch", "--no-optional-locks",
    "--no-advice", "--literal-pathspecs",
    "--glob-pathspecs", "--noglob-pathspecs",
    "--icase-pathspecs",
    "--help", "--version", "-h", "-v",
})

# Shell separators that end a single command's argument list.
_SHELL_SEPARATORS = {";", "&&", "||", "|", "&"}


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _get_regex() -> re.Pattern:
    raw = os.environ.get("PRAXIS_BRANCH_NAME_REGEX", "").strip()
    pattern = raw if raw else DEFAULT_REGEX
    try:
        return re.compile(pattern)
    except re.error:
        return re.compile(DEFAULT_REGEX)


def _get_strict() -> bool:
    return os.environ.get("PRAXIS_BRANCH_NAME_STRICT", "1").strip() != "0"


def _get_whitelist() -> frozenset[str]:
    raw = os.environ.get("PRAXIS_BRANCH_NAME_WHITELIST", "").strip()
    if raw:
        return frozenset(name.strip() for name in raw.split(",") if name.strip())
    return frozenset(DEFAULT_WHITELIST)


# ---------------------------------------------------------------------------
# Token-walk extraction
#
# Walk the argv list returned by iter_command_starts and extract the new branch
# name for creation commands.  Uses a token walker (not naive space-split) so
# that `-b` values containing spaces or other edge forms are handled correctly.
# ---------------------------------------------------------------------------

def _strip_git_globals(argv: list[str]) -> list[str]:
    """Strip git global flags between 'git' (argv[0]) and the subcommand.

    Returns the argv slice starting at the subcommand token.
    """
    i = 1  # skip 'git'
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if not tok.startswith("-"):
            break
        if "=" in tok:
            i += 1
            continue
        if tok in _GIT_GLOBAL_BARE_FLAGS:
            i += 1
            continue
        if tok in _GIT_GLOBAL_FLAGS_WITH_ARG:
            i += 2
            continue
        break
    return argv[i:]


def _extract_new_branch(argv: list[str]) -> str | None:
    """Return the new branch name if argv represents a branch-creation command,
    or None if it is not a branch-creation or cannot be parsed.

    Handles:
      git checkout -b <name> [<start>]
      git checkout --orphan <name>
      git checkout --track origin/<name>
      git switch -c <name> [<start>]
      git switch --create <name> [<start>]
      git switch --force-create <name> [<start>]
      git switch --orphan <name>
      git switch --track origin/<name>
      git branch <name> [<start>]
      git worktree add <path> -b <name> [<start>]
      git worktree add -b <name> <path> [<start>]
      git worktree add <path>            (implicit: branch = basename(path))
      git worktree add --orphan <path>   (implicit: orphan branch = basename(path))
    """
    argv = strip_prefix(argv)
    if not argv or argv[0] != "git":
        return None

    sub = _strip_git_globals(argv)
    if not sub:
        return None

    subcmd = sub[0]

    if subcmd == "checkout":
        return _extract_checkout_b(sub[1:])
    if subcmd == "switch":
        return _extract_switch_c(sub[1:])
    if subcmd == "branch":
        return _extract_branch_new(sub[1:])
    if subcmd == "worktree" and len(sub) > 1 and sub[1] == "add":
        return _extract_worktree_add_b(sub[2:])
    return None


def _token_walk(args: list[str], new_branch_flags: frozenset[str]) -> str | None:
    """Walk args looking for a flag in new_branch_flags and return the next token
    as the branch name.  Stops at shell separators.
    """
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in _SHELL_SEPARATORS:
            break
        if tok in new_branch_flags:
            if i + 1 < len(args) and args[i + 1] not in _SHELL_SEPARATORS:
                return args[i + 1]
            return None
        # Handle `-b<name>` attached form (no space).
        for flag in new_branch_flags:
            if (
                flag.startswith("-")
                and not flag.startswith("--")
                and tok.startswith(flag)
                and len(tok) > len(flag)
            ):
                return tok[len(flag):]
        i += 1
    return None


def _extract_track_branch(args: list[str]) -> str | None:
    """Extract branch name from --track/-t without an explicit -b flag.

    `git checkout --track origin/foo` creates local branch `foo` (basename of
    the remote ref). If -b is also present, that explicit name takes priority
    and is handled by the caller before this is reached.

    Handled forms:
      --track <remote>/<name>        (two-token, next arg is the remote ref)
      --track=direct <remote>/<name> (equals-mode form; mode is consumed, next is ref)
      --track=inherit <remote>/<name>
      -t <remote>/<name>             (short form, same as --track)

    Returns the basename of the remote-tracking ref, or None if no --track/-t
    found or if the ref has no explicit path component.
    """
    remote_ref: str | None = None
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in _SHELL_SEPARATORS:
            break
        if tok in {"--track", "-t"}:
            # --track <remote>/<name> — next token is the remote ref.
            if i + 1 < len(args) and args[i + 1] not in _SHELL_SEPARATORS:
                remote_ref = args[i + 1]
            break
        if tok.startswith("--track=") or tok.startswith("-t="):
            value = tok.split("=", 1)[1]
            if "/" in value:
                # --track=origin/foo (remote ref embedded after =).
                remote_ref = value
            else:
                # --track=direct / --track=inherit — value is a mode string.
                # Next token is the remote ref.
                if i + 1 < len(args) and args[i + 1] not in _SHELL_SEPARATORS:
                    remote_ref = args[i + 1]
            break
        i += 1
    if remote_ref is None:
        return None
    # Branch name = last component of the remote ref (e.g. origin/foo → foo).
    return remote_ref.split("/")[-1] if "/" in remote_ref else None


def _extract_checkout_b(args: list[str]) -> str | None:
    """Extract branch name from `git checkout` args.

    Handles:
      git checkout -b <name>        (new branch)
      git checkout --orphan <name>  (new unborn branch)
      git checkout --track <remote>/<name>  (implicit local name from remote ref)
    When -b is present, it takes priority; --track without -b triggers
    implicit name extraction.
    """
    # Explicit -b/-B or --orphan flag takes priority.
    explicit = _token_walk(args, frozenset({"-b", "-B", "--orphan"}))
    if explicit is not None:
        return explicit
    # --track without -b creates a local branch from the remote ref basename.
    return _extract_track_branch(args)


def _extract_switch_c(args: list[str]) -> str | None:
    """Extract branch name from `git switch` args.

    Handles short (-c/-C) and long (--create/--force-create/--orphan) forms:
      git switch -c <name>
      git switch --create <name>
      git switch --force-create <name>   (long form of -C)
      git switch --orphan <name>
      git switch --track <remote>/<name> (implicit local name from remote ref)
    """
    # Explicit creation flag takes priority.
    explicit = _token_walk(args, frozenset({"-c", "-C", "--create", "--force-create", "--orphan"}))
    if explicit is not None:
        return explicit
    # --track without explicit -c creates a local branch from the remote ref basename.
    return _extract_track_branch(args)


def _extract_branch_new(args: list[str]) -> str | None:
    """Extract branch name from `git branch` when it creates a new branch.

    `git branch <name> [<start>]` creates a branch.
    `git branch -d/-D/-m/-M/-r/-a/--list/...` does NOT create — return None.
    Mutating flags (-d, -D, -m, -M, -c, -C) and query flags (-v, -r, -a,
    --list, --merged, --no-merged, --contains, --delete, --move, --copy)
    are detected; their presence suppresses extraction.

    Value-consuming query flags (--format, --sort, --color, -l, --points-at,
    --merged, --no-merged, --contains, --no-contains, --abbrev, --column)
    consume one argument that must NOT be treated as a branch name.
    """
    # Flags whose presence means this is NOT a branch-creation invocation.
    _BRANCH_NON_CREATE_FLAGS = frozenset({
        "-d", "-D", "--delete",
        "-m", "-M", "--move",
        "-c", "-C", "--copy",
        "-r", "--remotes",
        "-a", "--all",
        "-v", "-vv", "--verbose",
        "--list",
        "--merged", "--no-merged",
        "--contains", "--no-contains",
        "--points-at",
        "--set-upstream-to", "-u",
        "--unset-upstream",
        "--edit-description",
        "--show-current",
        # Bare -l is also the short form of --list when no pattern follows,
        # but git itself uses -l only for listing; treat as non-create.
        "-l",
    })
    # Flags that consume the NEXT token as a value (not a branch name).
    _BRANCH_VALUE_CONSUMING = frozenset({
        "--format",
        "--sort",
        "--color",
        "--abbrev",
        "--column",
        "--no-column",
    })
    i = 0
    first_non_flag: str | None = None
    while i < len(args):
        tok = args[i]
        if tok in _SHELL_SEPARATORS:
            break
        if tok in _BRANCH_NON_CREATE_FLAGS:
            return None  # not a creation form
        if tok.startswith("-"):
            if "=" in tok:
                # Value embedded in flag (--format=..., --set-upstream-to=...).
                # Check if the base flag name is a non-create flag.
                base_flag = tok.split("=", 1)[0]
                if base_flag in _BRANCH_NON_CREATE_FLAGS:
                    return None  # e.g. --set-upstream-to=origin/main is not creation
                # Otherwise a benign value-carrying flag — skip it.
                i += 1
                continue
            if tok in _BRANCH_VALUE_CONSUMING:
                # Skip flag + its value token.
                i += 2
                continue
            # Other unknown flag — skip token only; not positional.
            i += 1
            continue
        # First non-flag positional = branch name for creation.
        if first_non_flag is None:
            first_non_flag = tok
        i += 1
    return first_non_flag


def _extract_worktree_add_b(args: list[str]) -> str | None:
    """Extract branch name from `git worktree add` args.

    Handles:
      git worktree add <path> -b <name>      (explicit, path first)
      git worktree add -b <name> <path>      (explicit, flag first)
      git worktree add --orphan <path>       (implicit: uses basename of path)
      git worktree add --orphan -b <name> <path>  (explicit orphan)
      git worktree add <path>                (implicit: uses basename of path,
                                              only when no -b/-B/--detach)

    For the implicit basename case: `git worktree add <path>` without -b/-B
    creates a new branch named after the final path component (git behavior).
    We extract basename(<path>) only when there is exactly ONE positional
    argument (no second commit-ish) — the two-positional form `add <path>
    <commit-ish>` checks out an existing ref and does NOT create a new branch
    from the basename (it creates if <commit-ish> doesn't exist locally, but
    the name comes from <commit-ish> not <path>).
    """
    # First check for explicit -b/-B flag (handles all flag-order variants).
    explicit = _token_walk(args, frozenset({"-b", "-B"}))
    if explicit is not None:
        return explicit

    # No explicit -b/-B. Check for --detach (-d): no branch created.
    has_detach = False
    positionals: list[str] = []
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in _SHELL_SEPARATORS:
            break
        if tok in {"-d", "--detach"}:
            has_detach = True
            i += 1
            continue
        if tok.startswith("-"):
            if "=" in tok:
                i += 1
                continue
            # Two-token value-consuming flags for worktree add.
            if tok in {"--reason"}:
                i += 2
                continue
            # Bare boolean flags for worktree add (no argument consumed).
            # --lock, --checkout, --no-checkout, --no-lock, --orphan, --force, -f
            i += 1
            continue
        positionals.append(tok)
        i += 1

    if has_detach:
        return None  # detached HEAD, no branch created

    # `git worktree add <path>` — one positional, implicit branch from basename.
    # `git worktree add <path> <commit-ish>` — two positionals; the branch name
    #   comes from <commit-ish>, not path basename (checkout/new from commit-ish).
    #   We cannot determine if that's new vs existing without a git query, so
    #   we conservatively skip to avoid false positives on existing-branch checkouts.
    if len(positionals) == 1:
        return os.path.basename(positionals[0].rstrip("/"))

    return None


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _emit_block(branch: str, regex_pattern: str, strict: bool) -> None:
    reason = format_block(
        rule_name="branch-name-check",
        why=f"branch '{branch}' does not match the required naming convention",
        correct_path=(
            f"rename to hub-<N>-<type>-<desc> or issue-<N>-<type>-<desc> "
            f"(pattern: {regex_pattern}); "
            f"set PRAXIS_BRANCH_NAME_STRICT=0 to switch to advisory-only mode"
        ),
        bypass_env=None,
        reference="CLAUDE.md → Git Commit & Title Rules; hooks/preflight-gate/branch-name-check/spec.md",
    )
    if strict:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            },
            sys.stdout,
        )
        sys.stdout.write("\n")
    else:
        sys.stderr.write(reason + "\n")


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

    # Collapse line continuations before tokenizing.
    command = command.replace("\\\n", " ")

    tokens = safe_tokenize(command)
    if not tokens:
        return 0

    branch_regex = _get_regex()
    strict = _get_strict()
    whitelist = _get_whitelist()

    for argv in iter_command_starts(tokens):
        branch = _extract_new_branch(argv)
        if branch is None:
            continue

        # Whitelist always passes.
        if branch in whitelist:
            continue

        # Regex match passes.
        if branch_regex.fullmatch(branch):
            continue

        # Violation found.
        _emit_block(branch, branch_regex.pattern, strict)
        if strict:
            return 2
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
