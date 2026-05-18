#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: emit stderr warning when a `cd <path>` target
does not exist on disk or is not a registered git worktree.

Trigger pattern (issue #322):
  `cd <path>` at the start of a Bash command, or `cd <path> && ...` in a
  compound command. Each segment is inspected for a leading `cd <path>`.

Advisory messages (stderr only — exit 0 always):
  [worktree-missing] <path> does not exist — check 'git worktree list' before retry
    When the resolved path does not exist on disk.
  [worktree-stale] <path> exists but is not a registered worktree
    When the path exists but `git worktree list --porcelain` does not include it.

Deduplication:
  Per-session marker files in
  `${TMPDIR:-/tmp}/praxis-bash-worktree-advisory/<session_id>.<path-hash>`
  suppress repeat advisories for the same (session, path) pair, reducing
  noise in long-running sessions.

Design:
  - Advisory only — writes to stderr, exits 0. Never blocks tool execution.
  - Fail-open — malformed JSON, missing session_id, git failure, timeout
    all result in a silent exit 0.
  - Role-aware tokenization via `tokenize_with_roles` / `filter_argv` from
    `_hook_utils.py` — consistent with sibling advisory hooks (cli-flag-incompat-advisory,
    cross-repo-worktree-preflight).

Opt-out: embed `# worktree-advisory:ack` anywhere in the command to suppress.

Relationship to sibling hooks:
  cross-repo-worktree-preflight — fires on `git worktree remove <path>` cross-repo
    mismatch. This hook fires earlier: on the `cd <path>` step itself before
    any git operation, catching the case where the worktree directory was
    already removed externally and the agent doesn't know yet.
  cross-boundary-preflight — gh write subcommands across --repo boundary.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    Token,
    TokenRole,
    filter_argv,
    tokenize_with_roles,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OPT_OUT_MARKER = "# worktree-advisory:ack"

# Dedupe dir — per-session markers suppress repeat advisories for the same path.
_DEDUPE_BASE = os.path.join(os.environ.get("TMPDIR", "/tmp"), "praxis-bash-worktree-advisory")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_path(path: str) -> str:
    """Resolve symlinks and strip trailing slash for comparison."""
    return os.path.realpath(path.rstrip("/"))


def _extract_cd_target(seg: list[Token]) -> str | None:
    """Return the path argument of a `cd <path>` segment, else None.

    Resolution:
      - argv[0] must be COMMAND `cd`
      - first POSITIONAL / SUBST_RUN / POST_DD argument after COMMAND
      - if no such token → None (bare `cd`, cd to home)
      - if target starts with `$` → None (variable expansion, unresolvable)
      - if target is `-` → None (cd to previous dir, unresolvable)

    Relative targets are returned as-is; the caller resolves them against
    the effective cwd.
    """
    argv = filter_argv(seg)
    if not argv or argv[0].text != "cd":
        return None
    for tok in argv[1:]:
        if tok.role in (TokenRole.FLAG, TokenRole.FLAG_VALUE, TokenRole.SEPARATOR_DD):
            continue
        target = tok.text
        if not target or target == "-" or target.startswith("$"):
            return None
        return target
    return None


def _list_registered_worktrees() -> list[str] | None:
    """Return absolute (realpath) of every registered worktree, else None on failure."""
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    paths: list[str] = []
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            paths.append(_normalize_path(line[len("worktree "):]))
    return paths


def _dedupe_marker(session_id: str, path: str) -> str:
    """Return path to the per-session dedupe marker file for this path."""
    path_hash = hashlib.sha1(path.encode()).hexdigest()[:12]
    safe_sid = session_id.replace("/", "_").replace("\\", "_")[:64]
    return os.path.join(_DEDUPE_BASE, f"{safe_sid}.{path_hash}")


def _is_deduped(session_id: str, path: str) -> bool:
    return os.path.exists(_dedupe_marker(session_id, path))


def _record_dedupe(session_id: str, path: str) -> None:
    os.makedirs(_DEDUPE_BASE, exist_ok=True)
    marker = _dedupe_marker(session_id, path)
    try:
        open(marker, "w").close()
    except OSError:
        pass


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

    if OPT_OUT_MARKER in command:
        return 0

    session_id = payload.get("session_id", "") or ""

    segments = tokenize_with_roles(command.replace("\\\n", " "), {})
    if not segments:
        return 0

    # Thread effective_cwd across compound segments so that relative paths
    # are resolved correctly (matching the shell's sequential execution).
    effective_cwd = os.getcwd()

    # Lazy-load registered worktrees once per invocation.
    registered: list[str] | None = None
    registered_loaded = False

    for seg in segments:
        target = _extract_cd_target(seg)
        if target is None:
            # This segment is not a `cd` — track any effective cwd update for
            # relative target resolution in subsequent segments.
            # (No cwd update here since this segment doesn't cd.)
            continue

        # Resolve target to absolute path.
        if target.startswith("/"):
            abs_target = os.path.normpath(target)
        else:
            abs_target = os.path.normpath(os.path.join(effective_cwd, target))

        real_target = _normalize_path(abs_target)

        # Dedupe: skip if we already emitted an advisory for this (session, path).
        if session_id and _is_deduped(session_id, real_target):
            # Even if suppressed, update effective_cwd for downstream segments.
            if os.path.isdir(abs_target):
                effective_cwd = abs_target
            continue

        # Check 1: path does not exist.
        if not os.path.isdir(abs_target):
            sys.stderr.write(
                f"[worktree-missing] {real_target} does not exist"
                " — check 'git worktree list' before retry\n"
            )
            if session_id:
                _record_dedupe(session_id, real_target)
            continue

        # Path exists — update effective_cwd for downstream segments.
        effective_cwd = abs_target

        # Check 2: path exists but is not a registered worktree.
        if not registered_loaded:
            registered = _list_registered_worktrees()
            registered_loaded = True

        if registered is None:
            # git not available or not in a git repo — skip worktree check.
            continue

        if real_target not in registered:
            sys.stderr.write(
                f"[worktree-stale] {real_target} exists but is not a registered worktree"
                " — verify with 'git worktree list'\n"
            )
            if session_id:
                _record_dedupe(session_id, real_target)

    return 0


def main() -> int:
    """Advisory hook — must NEVER break tool execution."""
    try:
        return _main_inner()
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
