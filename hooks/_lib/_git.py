"""Shared read-only git subprocess helpers for praxis hooks (issue #1178).

Eight impl.py files carried their own git subprocess wrapper — byte-identical
`_run_git` copies in `pre-edit-protected-branch-guard` and
`worktree-edit-gate`, plus near-variants in `anchor-comment-gate` (`_git`),
`gh-merge-worktree-precondition` (`_run`, git call site),
`postcompact-context` (`_run`, git call site), `block-personal-asset-leak`
(`_git_output`), `pre-commit-staged-file-enumeration` (`_git`), and
`external-write-path-existence-check` (`_git_toplevel`). Three of those also
duplicated the origin-URL owner/repo regex (`worktree-edit-gate`,
`pre-gh-pr-create-dedup-gate`, and a LOOSER, non-host-anchored copy in
`gh-label-verify` that matched gitlab origins too — consolidating on the
strict variant deliberately tightens gh-label-verify, per issue #1178).

This module pins those in a single place, mirroring the sibling `_hook_io.py`
(issue #470) extraction. Contract:

  - `run_git` returns raw stdout on exit code 0, None on ANY failure —
    nonzero exit, missing git binary, timeout, bad cwd, embedded-null argv.
    Callers strip / parse as needed; rc==0-with-empty-stdout is `""` (a str),
    distinguishable from failure (`None`) for callers like `check-ignore -q`.
  - Timeouts default to 5s; sites whose hand-rolled copy used a different
    budget (2s, 3s) pass `timeout=` explicitly to preserve their behavior.
  - Every call is budget-aware (issue #1167). The consolidated sites each
    clamped their own timeout to the member budget the dispatcher publishes
    and refused to spawn below the floor; centralising the subprocess here
    would have dropped that per site, so the clamp moves here with it. A
    caller that shares one deadline across several probes passes `deadline=`
    instead of relying on the per-call accessor.
  - Never raises: the except set is the union of every consolidated site's
    (OSError covers FileNotFoundError; SubprocessError covers TimeoutExpired;
    ValueError covers embedded-null argv/cwd).

The `gh` call sites in `gh-merge-worktree-precondition` and
`postcompact-context` keep their local runners — this module is git-only.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path as _Path
from typing import Optional

sys.path.insert(0, str(_Path(__file__).resolve().parent))
from _hook_runtime import (  # type: ignore[import-not-found]  # noqa: E402
    MIN_SUBPROC_BUDGET_SEC,
    remaining_budget,
)

# Owner/repo extracted from common GitHub origin URL forms — host-anchored so
# non-GitHub origins (gitlab, bitbucket, self-hosted) return None:
#   git@github.com:owner/repo.git
#   https://github.com/owner/repo.git
#   ssh://git@github.com/owner/repo
_ORIGIN_SLUG_RE = re.compile(
    r"(?:github\.com[:/])([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+?)(?:\.git)?/?$"
)


def run_git(
    args: list[str],
    timeout: float = 5,
    cwd: Optional[str] = None,
    deadline: Optional[float] = None,
) -> Optional[str]:
    """Run a read-only git command; return raw stdout, or None on any failure.

    None covers: nonzero exit code, missing git binary, subprocess timeout,
    invalid cwd, embedded-null argv, and no runway to spawn. Never raises.

    The effective timeout is `timeout` clamped to whatever budget is left, so
    a caller inside an already-late dispatch group cannot overrun the group
    deadline (issue #1167). Below `MIN_SUBPROC_BUDGET_SEC` nothing is spawned:
    the fork/exec would be dead on arrival and only burns what remains.

    `deadline` is an absolute `time.monotonic()` value for callers that run
    several probes under ONE shared deadline, so their SUM stays bounded;
    without it each call reads the member budget on its own.
    """
    budget = (
        deadline - time.monotonic()
        if deadline is not None
        else remaining_budget(timeout)
    )
    if budget < MIN_SUBPROC_BUDGET_SEC:
        return None
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=min(timeout, budget),
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def repo_root(
    cwd: Optional[str] = None,
    timeout: float = 5,
    deadline: Optional[float] = None,
) -> Optional[str]:
    """Return the git repo root for cwd, or None if not in a repo / on error."""
    out = run_git(
        ["rev-parse", "--show-toplevel"], timeout=timeout, cwd=cwd, deadline=deadline
    )
    if out is None:
        return None
    root = out.strip()
    return root or None


def origin_slug(
    cwd: Optional[str] = None,
    timeout: float = 5,
    deadline: Optional[float] = None,
) -> Optional[str]:
    """Return the 'owner/repo' slug of the GitHub origin remote, or None.

    Parses HTTPS, SSH, and scp-like origin URL forms. Host-anchored to
    github.com — a non-GitHub origin returns None (fail-open for callers).
    """
    out = run_git(
        ["remote", "get-url", "origin"], timeout=timeout, cwd=cwd, deadline=deadline
    )
    if out is None:
        return None
    url = out.strip()
    if not url:
        return None
    m = _ORIGIN_SLUG_RE.search(url)
    return m.group(1) if m else None
