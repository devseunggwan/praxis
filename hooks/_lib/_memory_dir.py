#!/usr/bin/env python3
"""Shared memory-directory resolver — single source of truth (#823).

Previously this resolver was re-implemented per hook:

  - memory-hint carried the fixed per-character slugify (#800)
  - momentum-rule-retrieval-gate kept a stale private copy still using
    `cwd.replace("/", "-")`, so on any host whose absolute cwd contains a
    non-alphanumeric character beyond `/` (e.g. a literal `.` in the
    username) its fallback path never resolved and the gate's dynamic
    memory cites silently never fired — the exact dual-SoT drift the #800
    fix was supposed to eliminate.

Both hooks now import from here so the resolution semantics cannot drift.

Resolution order:
  1. `PRAXIS_MEMORY_DIR` env var — when set, it is authoritative: return it
     if it is an existing directory, else None (no fallback attempt)
  2. fallback `${CLAUDE_CONFIG_DIR:-~/.claude}/projects/{slugified-cwd}/memory`
     if it exists (#853: `CLAUDE_CONFIG_DIR` relocates Claude Code's config
     root off the `~/.claude` default — a hardcoded `~/.claude` silently
     reads the wrong/stale store on any host where that var is set and not
     symlinked back to `~/.claude`)
  3. linked-worktree fallback (#824): when 2 misses, resolve the main
     worktree via `git rev-parse --git-common-dir` (its parent dir is the
     main worktree) and retry with THAT path's slug — Claude Code keys the
     memory dir to the main project path, so a worktree cwd slug never
     resolves. Any git error / absence falls through to step 4.
  4. otherwise None

Public API:
  SLUG_CHAR_RE                       — per-character slugify pattern
  slugify_project_path(path) -> str  — Claude Code project-slug rule
  resolve_memory_dir() -> str | None — resolved memory dir or None
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent))
from _git import run_git  # type: ignore[import-not-found]  # noqa: E402

SLUG_CHAR_RE = re.compile(r"[^a-zA-Z0-9]")

_GIT_TIMEOUT_SEC = 5


def slugify_project_path(path: str) -> str:
    """Slugify an absolute path per Claude Code's project-slug rule.

    Claude Code replaces every non-alphanumeric character individually
    (not collapsed runs) — e.g. /Users/jane.doe/.claude slugs to
    -Users-jane-doe--claude (double dash, since "/." is two chars).
    A prior cwd.replace("/", "-") only touched slashes, so any other
    special character (a literal "." in a username, for one) left the
    fallback path permanently unresolvable — this fallback ran, found
    None, and the entire hookable/hookKeywords surface silently never
    fired for any memory in the store.
    """
    return SLUG_CHAR_RE.sub("-", path)


def _claude_config_dir(home: str) -> str:
    """Claude Code's config root: `CLAUDE_CONFIG_DIR` override, else `~/.claude`.

    #853: this resolver previously hardcoded `home + "/.claude"`, silently
    ignoring `CLAUDE_CONFIG_DIR` when Claude Code's config root is relocated.
    """
    override = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return override if override else os.path.join(home, ".claude")


def _main_worktree_path() -> str | None:
    """Return the main worktree's absolute path, or None outside git.

    `git rev-parse --git-common-dir` prints the shared `.git` directory —
    from a linked worktree that is `<main-worktree>/.git` (absolute); from
    the main worktree it is the relative `.git`, which abspath()s back to
    the current cwd so the caller's `!= cwd` guard skips it. Fail-safe:
    any git error (git absent, not a repo, timeout) → None.

    The spawn goes through the shared budget-aware runner (issue #1178): this
    resolver is reached from inside the Bash dispatch group, where a fixed
    timeout can outlive the group deadline and get the whole dispatcher killed
    by the host (issue #1167).
    """
    out = run_git(["rev-parse", "--git-common-dir"], timeout=_GIT_TIMEOUT_SEC)
    if out is None:
        return None
    common = out.strip()
    if not common:
        return None
    return os.path.dirname(os.path.abspath(common))


def resolve_memory_dir() -> str | None:
    """Return the resolved memory directory path or None if missing."""
    env_dir = os.environ.get("PRAXIS_MEMORY_DIR", "").strip()
    if env_dir:
        return env_dir if os.path.isdir(env_dir) else None

    home = os.path.expanduser("~")
    config_dir = _claude_config_dir(home)
    cwd = os.getcwd()
    slug = slugify_project_path(cwd)
    fallback = os.path.join(config_dir, "projects", slug, "memory")
    if os.path.isdir(fallback):
        return fallback

    # Linked-worktree fallback (#824): memory dirs are keyed to the main
    # project path, so a worktree cwd slug misses even though the store
    # exists. Retry with the main worktree's slug.
    main_path = _main_worktree_path()
    if main_path and main_path != cwd:
        main_fallback = os.path.join(
            config_dir, "projects", slugify_project_path(main_path), "memory"
        )
        if os.path.isdir(main_fallback):
            return main_fallback
    return None
