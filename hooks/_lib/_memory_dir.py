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
  2. fallback `~/.claude/projects/{slugified-cwd}/memory` if it exists
  3. otherwise None

Public API:
  SLUG_CHAR_RE                       — per-character slugify pattern
  slugify_project_path(path) -> str  — Claude Code project-slug rule
  resolve_memory_dir() -> str | None — resolved memory dir or None
"""
from __future__ import annotations

import os
import re

SLUG_CHAR_RE = re.compile(r"[^a-zA-Z0-9]")


def slugify_project_path(path: str) -> str:
    """Slugify an absolute path per Claude Code's project-slug rule.

    Claude Code replaces every non-alphanumeric character individually
    (not collapsed runs) — e.g. /Users/nathan.song/.claude slugs to
    -Users-nathan-song--claude (double dash, since "/." is two chars).
    A prior cwd.replace("/", "-") only touched slashes, so any other
    special character (a literal "." in a username, for one) left the
    fallback path permanently unresolvable — this fallback ran, found
    None, and the entire hookable/hookKeywords surface silently never
    fired for any memory in the store.
    """
    return SLUG_CHAR_RE.sub("-", path)


def resolve_memory_dir() -> str | None:
    """Return the resolved memory directory path or None if missing."""
    env_dir = os.environ.get("PRAXIS_MEMORY_DIR", "").strip()
    if env_dir:
        return env_dir if os.path.isdir(env_dir) else None

    home = os.path.expanduser("~")
    cwd = os.getcwd()
    slug = slugify_project_path(cwd)
    fallback = os.path.join(home, ".claude", "projects", slug, "memory")
    return fallback if os.path.isdir(fallback) else None
