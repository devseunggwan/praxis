#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block `gh pr merge --delete-branch` when the PR's
head branch is still checked out in another git worktree.

Issue #798. `gh pr merge --delete-branch` (or `-d`) deletes the local branch
after merging — but git refuses to delete a branch that is checked out in any
worktree, local or the primary checkout. Running the merge before removing
that worktree fails deterministically with:

  fatal: branch '<branch>' is checked out at '<path>'
  (or, on the gh side) cannot delete branch '<branch>' used by worktree at '<path>'

CLAUDE.md's "Pre-merge Worktree Precondition" documents the required manual
sequence (remove the worktree, then merge) after this exact failure recurred
across multiple PRs in this project (#147, #170-173, #175, #796) — a real,
repeated cost this hook now enforces structurally instead of relying on the
rule being retrieved from memory every time.

## Detection

1. `tool_name == "Bash"` — non-Bash tools exit 0 silently.
2. Tokenize with `_hook_utils.safe_tokenize` + `iter_command_starts` and scan
   every command segment for `gh pr merge` (mirrors
   `pre-merge-approval-gate.is_gh_pr_merge` — gh global flags such as
   `-R/--repo`/`--hostname`/`--color` are walked past).
3. Within that segment, the merge must also carry `-d`/`--delete-branch` —
   without it, gh never attempts to delete the local branch, so the worktree
   conflict cannot occur.
4. Extract the PR identifier (number / URL / branch), if any, walking past
   `gh pr merge`'s own value-taking flags (`-A/--author-email`, `-b/--body`,
   `-F/--body-file`, `-t/--subject`, `--match-head-commit`).
5. Resolve the PR's head branch via `gh pr view <identifier> --json
   headRefName -q .headRefName` (or with no identifier when none was given —
   gh then infers the PR from the current branch, exactly mirroring what the
   merge command itself would do).
6. List worktrees via `git worktree list --porcelain` and check whether any
   entry's branch equals the resolved head branch.
7. A match → deterministic failure → block (exit 2). No match → pass.

## Scope decision — compound commands not special-cased

CLAUDE.md's own "Unified post-merge cleanup sequence" explicitly tells agents
NOT to collapse worktree-remove + merge into a single `&&`-chain (a git hard
error mid-chain silently short-circuits later steps). This hook does not scan
earlier segments of the same compound command for a preceding `git worktree
remove` that would neutralize the conflict — doing so would require
path-order-sensitive reasoning to support a pattern the project's own
convention already discourages. Run the two steps as separate Bash calls.

## Fail-open

Any infrastructure error (missing git/gh, subprocess timeout, malformed stdin
JSON, `gh pr view` failure, unparseable `git worktree list` output) exits 0 —
this hook only blocks on a positively-confirmed worktree conflict, never on
an inability to determine one.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    _is_gh_binary,
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)
from block_message import emit_block  # type: ignore[import-not-found]  # noqa: E402

_GH_TIMEOUT_SEC = 5
_GIT_TIMEOUT_SEC = 5
_BYPASS_ENV = "PRAXIS_HOOK_BYPASS_MERGE_WORKTREE_GATE"

# gh global flags that consume one additional argument. Mirrors the same
# constant duplicated across ~11 sibling hooks in this repo (pre-merge-
# approval-gate, external-write-falsify-check, gh-flag-verify, etc.) —
# established repo convention keeps this local rather than extracted to
# _lib despite the count; see those files' own comments.
GH_GLOBAL_FLAGS_WITH_ARG = frozenset({
    "-R", "--repo",
    "--hostname",
    "--color",
})

# `gh pr merge` flags that consume one additional argument (per
# `gh pr merge --help`), needed to walk past them when locating the
# positional PR identifier.
_MERGE_FLAGS_WITH_ARG = frozenset({
    "-A", "--author-email",
    "-b", "--body",
    "-F", "--body-file",
    "-t", "--subject",
    "--match-head-commit",
})

# `gh pr merge` boolean flags (per `gh pr merge --help`) — needed to detect
# `--delete-branch`/`-d` and to skip past other boolean flags without
# mistaking them for the positional identifier.
_MERGE_BOOL_FLAGS = frozenset({
    "--admin", "--auto", "-d", "--delete-branch", "--disable-auto",
    "-m", "--merge", "-r", "--rebase", "-s", "--squash", "--help",
})

_DELETE_BRANCH_FLAGS = frozenset({"-d", "--delete-branch"})


def _merge_segment(argv: list[str]) -> list[str] | None:
    """Return the argv slice starting at `merge`'s first flag/arg, or None.

    Walks past `gh` global flags and the `pr merge` subcommand tokens.
    """
    argv = strip_prefix(argv)
    if not argv or not _is_gh_binary(argv[0]):
        return None

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

    if i + 1 >= len(argv) or argv[i] != "pr" or argv[i + 1] != "merge":
        return None
    return argv[i + 2:]


def _has_delete_branch_flag(tail: list[str]) -> bool:
    for tok in tail:
        base = tok.split("=", 1)[0]
        if base in _DELETE_BRANCH_FLAGS:
            return True
    return False


def _extract_identifier(tail: list[str]) -> str | None:
    """Return the first positional (non-flag) token, or None if none present."""
    i = 0
    while i < len(tail):
        tok = tail[i]
        if tok == "--":
            i += 1
            if i < len(tail):
                return tail[i]
            return None
        if not tok.startswith("-"):
            return tok
        i += 1
        if "=" not in tok and tok in _MERGE_FLAGS_WITH_ARG and i < len(tail):
            i += 1
    return None


def _run(cmd: list[str], cwd: str | None, timeout: int) -> tuple[int, str]:
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd or None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        return -1, ""


def _resolve_head_branch(identifier: str | None, cwd: str | None) -> str | None:
    cmd = ["gh", "pr", "view"]
    if identifier:
        cmd.append(identifier)
    cmd += ["--json", "headRefName", "-q", ".headRefName"]
    rc, out = _run(cmd, cwd, _GH_TIMEOUT_SEC)
    if rc != 0:
        return None
    branch = out.strip()
    return branch or None


def _worktree_branches(cwd: str | None) -> dict[str, str]:
    """Return {branch_name: worktree_path} parsed from `git worktree list --porcelain`."""
    rc, out = _run(["git", "worktree", "list", "--porcelain"], cwd, _GIT_TIMEOUT_SEC)
    if rc != 0:
        return {}
    branches: dict[str, str] = {}
    current_path: str | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            current_path = line[len("worktree "):].strip()
        elif line.startswith("branch ") and current_path:
            ref = line[len("branch "):].strip()
            name = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
            branches[name] = current_path
            current_path = None
    return branches


def _emit_block(head_branch: str, conflict_path: str) -> None:
    emit_block(
        rule_name="gh-merge-worktree-precondition",
        why=(
            f"`gh pr merge --delete-branch` for branch '{head_branch}' will "
            f"fail — that branch is still checked out in worktree "
            f"'{conflict_path}'"
        ),
        correct_path=(
            f"remove the worktree first (`git worktree remove {conflict_path}` "
            "— add `--force` only after confirming it is clean), then retry "
            "the merge"
        ),
        bypass_env=_BYPASS_ENV,
        reference=(
            "CLAUDE.md → Pre-merge Worktree Precondition; "
            "hooks/preflight-gate/gh-merge-worktree-precondition/spec.md"
        ),
    )


@fail_open
def main() -> int:
    if os.environ.get(_BYPASS_ENV, "").strip():
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if not isinstance(payload, dict):
        return 0
    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "") or ""
    if not command.strip():
        return 0

    tokens = safe_tokenize(command)
    if not tokens:
        return 0

    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        cwd = None

    for argv in iter_command_starts(tokens):
        tail = _merge_segment(argv)
        if tail is None:
            continue
        if not _has_delete_branch_flag(tail):
            continue

        identifier = _extract_identifier(tail)
        head_branch = _resolve_head_branch(identifier, cwd)
        if not head_branch:
            continue  # cannot determine live head branch — fail-open

        worktrees = _worktree_branches(cwd)
        conflict_path = worktrees.get(head_branch)
        if not conflict_path:
            continue  # branch not checked out anywhere — no conflict

        _emit_block(head_branch, conflict_path)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
