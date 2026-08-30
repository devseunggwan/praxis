#!/usr/bin/env python3
"""PreToolUse guard: block Edit/Write on source files when the repo is on a base branch.

Fires on Edit and Write tool calls only (NOT NotebookEdit — notebook edits are
a separate concern and do not follow the same source-file extension classification).

## Ambiguity resolution (issue #437)

The original issue listed a 4th condition "cwd is not registered in git worktree
list". However, the Acceptance Criteria state "Does NOT block: edits inside a
registered worktree on any branch." A primary checkout sitting on a base branch
IS present in `git worktree list`, which would contradict the intent.

Resolution: block on HEAD ∈ base-branches, regardless of worktree-list status.
A feature-branch worktree never has HEAD ∈ base-branches, so it passes naturally.
The real signal is "editing a source file while the repo is on a base branch" —
not worktree registration status.

## Block conditions (ALL must be true)

1. The target file's repo root basename (or "org/repo") is in the configured
   enforced-repo list (`PRAXIS_WORKTREE_ENFORCED_REPOS`).
2. The file extension (or compound extension) matches the configured source-file
   extension set (`PRAXIS_WORKTREE_SOURCE_EXTENSIONS`).
3. The repo's current HEAD branch is in the configured base-branch set
   (`PRAXIS_WORKTREE_BASE_BRANCHES`).

## Default behavior

When `PRAXIS_WORKTREE_ENFORCED_REPOS` is unset or empty, the hook is a complete
no-op (exit 0). This is opt-in only — the hook must not interfere by default.

## Env vars

- `PRAXIS_WORKTREE_ENFORCED_REPOS`   — comma-separated repo identifiers.
    Matched by repo root basename (e.g. "praxis") OR "org/repo" form
    (e.g. "myorg/praxis"). Empty / unset → no-op.
- `PRAXIS_WORKTREE_BASE_BRANCHES`    — default "main,dev,prod"
- `PRAXIS_WORKTREE_SOURCE_EXTENSIONS` — default "py,ts,tsx,go,sql"
    No leading dot. Compound extensions like "j2.sql" are supported.
- `PRAXIS_HOOK_BYPASS_WORKTREE_GATE` — set to any non-empty value → bypass.

## Fail-open

All infrastructure errors (missing git, subprocess timeout, malformed stdin JSON,
file not inside a git repo, detached HEAD) → exit 0 (pass).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from block_message import emit_block  # type: ignore[import-not-found]  # noqa: E402

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

DEFAULT_BASE_BRANCHES = frozenset({"main", "dev", "prod"})
DEFAULT_SOURCE_EXTENSIONS = frozenset({"py", "ts", "tsx", "go", "sql"})

TARGET_TOOLS = frozenset({"Edit", "Write"})

# Regex for parsing owner/repo from GitHub remote URLs:
#   https://github.com/owner/repo.git
#   git@github.com:owner/repo.git
#   ssh://git@github.com/owner/repo
_REMOTE_SLUG_RE = re.compile(
    r"(?:github\.com[:/])([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+?)(?:\.git)?/?$"
)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _parse_csv_env(name: str, default: frozenset[str]) -> frozenset[str]:
    """Read a comma-separated env var. Return default if unset/empty."""
    val = os.environ.get(name, "").strip()
    if not val:
        return default
    return frozenset(item.strip() for item in val.split(",") if item.strip())


def get_enforced_repos() -> frozenset[str]:
    """Return the set of enforced repo identifiers. Empty → hook is no-op."""
    val = os.environ.get("PRAXIS_WORKTREE_ENFORCED_REPOS", "").strip()
    if not val:
        return frozenset()
    return frozenset(item.strip() for item in val.split(",") if item.strip())


def get_base_branches() -> frozenset[str]:
    return _parse_csv_env("PRAXIS_WORKTREE_BASE_BRANCHES", DEFAULT_BASE_BRANCHES)


def get_source_extensions() -> frozenset[str]:
    return _parse_csv_env("PRAXIS_WORKTREE_SOURCE_EXTENSIONS", DEFAULT_SOURCE_EXTENSIONS)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _run_git(args: list[str], cwd: str) -> tuple[int, str]:
    """Run a git command in cwd. Returns (returncode, stdout). Fail-open on error."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode, result.stdout
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        return -1, ""


def _get_remote_slug(repo_root: str) -> str | None:
    """Return the 'owner/repo' slug from the origin remote URL, or None.

    Parses both HTTPS and SSH remote URL forms. Fail-open on any error.
    """
    rc, out = _run_git(["remote", "get-url", "origin"], repo_root)
    if rc != 0:
        return None
    url = out.strip()
    if not url:
        return None
    m = _REMOTE_SLUG_RE.search(url)
    return m.group(1) if m else None


def get_repo_root(path: str) -> str | None:
    """Return the git repo root for path, or None if not in a repo / on error.

    Resolves symlinks before walking. Walks up ancestor directories so that
    paths to not-yet-created files still resolve to the repo root.
    """
    real_path = os.path.realpath(path)
    cwd = real_path if os.path.isdir(real_path) else os.path.dirname(real_path)
    while cwd and not os.path.isdir(cwd):
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent
    if not cwd or not os.path.isdir(cwd):
        return None
    rc, out = _run_git(["rev-parse", "--show-toplevel"], cwd)
    if rc != 0:
        return None
    root = out.strip()
    return root if root else None


def get_current_branch(repo_root: str) -> str | None:
    """Return the current branch name, or None for detached HEAD / failure."""
    rc, out = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    if rc != 0:
        return None
    branch = out.strip()
    return branch if branch and branch != "HEAD" else None


# ---------------------------------------------------------------------------
# Repo-identity matching
# ---------------------------------------------------------------------------


def _repo_matches_identifier(repo_root: str, identifier: str) -> bool:
    """True if repo_root matches identifier by basename or org/repo form.

    Matching rules:
    - "praxis"          → matches if os.path.basename(repo_root) == "praxis"
    - "myorg/praxis"    → matches if the remote origin URL slug is "myorg/praxis"
                          (parsed from `git remote get-url origin`; handles both
                          HTTPS and SSH URL forms). Falls back to pass (no match)
                          if there is no origin remote or the URL is unparseable.
    Both comparisons are case-sensitive (git repos are typically lowercase).
    """
    if "/" in identifier:
        # org/repo form: resolve via remote origin URL, not filesystem path
        slug = _get_remote_slug(repo_root)
        if slug is None:
            return False  # no origin / unparseable → fail-open (no match)
        return slug == identifier
    # basename form
    return os.path.basename(repo_root) == identifier


def is_repo_enforced(repo_root: str, enforced_repos: frozenset[str]) -> bool:
    """True if repo_root matches any identifier in enforced_repos."""
    return any(_repo_matches_identifier(repo_root, ident) for ident in enforced_repos)


# ---------------------------------------------------------------------------
# Extension matching
# ---------------------------------------------------------------------------


def is_source_file(path: str, source_extensions: frozenset[str]) -> bool:
    """True if path's extension matches the configured source extension set.

    Checks compound extension first, then falls back to simple extension.
    """
    basename = os.path.basename(path)
    if "." not in basename:
        return False
    parts = basename.split(".")
    # Check compound (e.g. j2.sql) if 3+ parts
    if len(parts) >= 3:
        compound = f"{parts[-2]}.{parts[-1]}"
        if compound in source_extensions:
            return True
    # Check simple extension
    simple = parts[-1]
    return simple in source_extensions


# ---------------------------------------------------------------------------
# Block output
# ---------------------------------------------------------------------------


def _emit_block(file_path: str, branch: str, repo_root: str) -> None:
    emit_block(
        rule_name="worktree-edit-gate",
        why=(
            f"editing a source file on base branch '{branch}' "
            f"in '{os.path.basename(repo_root)}' — "
            "the Issue-Driven Worktree Workflow requires a dedicated "
            "issue + branch + worktree before writing code on base branches"
        ),
        correct_path=(
            "create a GitHub issue, then create a feature branch + worktree: "
            "`git worktree add <path> -b issue-N-description` and work there"
        ),
        bypass_env="PRAXIS_HOOK_BYPASS_WORKTREE_GATE",
        reference=(
            "CLAUDE.md → Issue-Driven Worktree Workflow (MANDATORY); "
            "docs/hook/INDEX.md → worktree-edit-gate"
        ),
    )
    sys.stderr.write(f"\nBlocked file: {file_path}\nRepo: {repo_root}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


@fail_open
def main() -> int:
    # Bypass env var — set to any non-empty value
    if os.environ.get("PRAXIS_HOOK_BYPASS_WORKTREE_GATE", "").strip():
        return 0

    payload = read_payload()
    if payload is None:
        return 0  # fail-open on malformed input

    tool_name = payload.get("tool_name", "") or ""
    if tool_name not in TARGET_TOOLS:
        return 0

    # Condition 0 (early exit): no-op when ENFORCED_REPOS is empty
    enforced_repos = get_enforced_repos()
    if not enforced_repos:
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    file_path = (tool_input.get("file_path") or "").strip()
    if not file_path:
        return 0  # no path — fail-open

    # Condition 2: source file extension check
    source_extensions = get_source_extensions()
    if not is_source_file(file_path, source_extensions):
        return 0  # not a source file — pass

    # Condition 1: repo in enforced list
    repo_root = get_repo_root(file_path)
    if not repo_root:
        return 0  # not in a git repo — fail-open
    if not is_repo_enforced(repo_root, enforced_repos):
        return 0  # repo not in enforced list — pass

    # Condition 3: current branch is a base branch
    branch = get_current_branch(repo_root)
    if not branch:
        return 0  # detached HEAD or failure — fail-open
    base_branches = get_base_branches()
    if branch not in base_branches:
        return 0  # feature branch — pass

    # All conditions met: block
    _emit_block(file_path, branch, repo_root)
    return 2


if __name__ == "__main__":
    sys.exit(main())
