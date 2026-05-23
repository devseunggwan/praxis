#!/usr/bin/env python3
"""PreToolUse advisory: warn when Write/Edit/NotebookEdit targets a nested
worktree path whose parent directory has not been enumerated this session.

Background (praxis issue #386):
  A retrospect session identified a recurring pattern: the agent's first Write
  after entering a new worktree places the artifact at a shallower path than
  intended.  The root cause is session-compaction summary generalization — a
  path cited in the main worktree's summary is transplanted to a sibling
  worktree without re-probing the layout.

  This hook is the structural enforcement layer for the
  "One-Probe-Before-Action Gate" rule in the global behavioral contract,
  applied to the Write tool surface.

Trigger:
  Fires on Write / Edit / NotebookEdit when:
  (a) the target path is inside a known git worktree, AND
  (b) the target is more than one directory level below the worktree root
      (i.e., the path has a non-root intermediate parent inside the worktree),
      AND
  (c) the intermediate parent directory has NOT been recorded as enumerated in
      the current session (via a session-scoped state marker).

Behavior:
  Advisory mode (default, exit 0): emits a one-line stderr warning naming the
  parent directory that was never enumerated, and records the parent as seen so
  the advisory fires at most once per (session, parent) pair.

  Strict mode (PRAXIS_PATH_PROBE_STRICT=1, exit 2): denies the Write until the
  probe completes.  The denial message names the expected probe command.

Bypass:
  Inline marker `# path-probe:ack` anywhere in the Write `file_path` is not
  applicable (file paths don't carry comments), so bypass is via:
  - Environment: PRAXIS_PATH_PROBE_SKIP=1 (full opt-out for the session)
  - Strict-mode escape: unset PRAXIS_PATH_PROBE_STRICT to revert to advisory
    mode (Write proceeds with a one-time warning on retry), then set
    PRAXIS_PATH_PROBE_SKIP=1 if you need to bypass entirely.
    Note: running `ls` / `git ls-files` in a Bash tool call does NOT record
    enumeration — this hook fires only on Write/Edit/NotebookEdit and has no
    Bash PostToolUse handler.

State:
  Session-scoped enumeration markers under:
    ${TMPDIR:-/tmp}/praxis-path-probe-gate/<session_id_hash>/<parent_hash>
  A marker's presence means the parent was enumerated this session.
  Stale markers are cleaned up by the OS tmp purge policy.

Fail-open:
  Any infrastructure error (malformed stdin, missing git, subprocess timeout,
  unexpected exception) results in a silent exit 0.

Opt-out:
  PRAXIS_PATH_PROBE_SKIP=1   — disable entirely for this session
  PRAXIS_PATH_PROBE_STRICT=1 — escalate advisory to hard deny (exit 2)
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TARGET_TOOLS = frozenset({"Write", "Edit", "NotebookEdit"})

# Advisory message templates
_ADVISORY_TMPL = (
    "[path-probe-gate] Writing to {target_path} — the parent directory "
    "{parent_dir} has not been enumerated this session.\n"
    "Run `ls {parent_dir}` or `git ls-files {parent_dir}` first to confirm "
    "the correct layout before writing.\n"
    "Set PRAXIS_PATH_PROBE_STRICT=1 to convert this advisory to a hard block.\n"
)

_DENY_TMPL = (
    "[path-probe-gate] Write blocked: {target_path} targets a directory "
    "({parent_dir}) that has not been enumerated this session.\n"
    "\n"
    "The target is {depth} level(s) below worktree root {worktree_root}.\n"
    "A session-compaction summary may have generalized a sibling-worktree "
    "path without re-probing the layout.\n"
    "\n"
    "To unblock:\n"
    "  1. Confirm the correct path by running in a shell (outside Claude Code):\n"
    "       ls {parent_dir}\n"
    "       # or: git ls-files {parent_dir}\n"
    "  2. Then either:\n"
    "     a) Set PRAXIS_PATH_PROBE_SKIP=1 to disable the gate for this session, OR\n"
    "     b) Unset PRAXIS_PATH_PROBE_STRICT to revert to advisory mode (Write\n"
    "        proceeds with a one-time warning once you retry).\n"
    "  Note: Bash tool calls do not record enumeration — only Write/Edit/\n"
    "  NotebookEdit are monitored by this hook.\n"
)

_STATE_BASE = os.path.join(
    os.environ.get("TMPDIR", "/tmp"), "praxis-path-probe-gate"
)


# ---------------------------------------------------------------------------
# Session-scoped enumeration state
# ---------------------------------------------------------------------------

def _state_dir(session_id: str) -> str:
    sid_hash = hashlib.sha1(session_id.encode()).hexdigest()[:16]
    return os.path.join(_STATE_BASE, sid_hash)


def _parent_marker(state_dir: str, parent_path: str) -> str:
    parent_hash = hashlib.sha1(os.path.realpath(parent_path).encode()).hexdigest()[:16]
    return os.path.join(state_dir, parent_hash)


def _is_enumerated(session_id: str, parent_path: str) -> bool:
    """Return True if parent_path was recorded as enumerated this session."""
    marker = _parent_marker(_state_dir(session_id), parent_path)
    return os.path.exists(marker)


def _record_enumerated(session_id: str, parent_path: str) -> None:
    """Mark parent_path as enumerated for this session."""
    sd = _state_dir(session_id)
    try:
        os.makedirs(sd, exist_ok=True)
        marker = _parent_marker(sd, parent_path)
        open(marker, "w").close()
    except OSError:
        pass  # fail-open — state recording is best-effort


# ---------------------------------------------------------------------------
# Git worktree helpers
# ---------------------------------------------------------------------------

def _git_worktree_list(cwd: str) -> list[str]:
    """Return list of worktree root paths via `git worktree list --porcelain`.

    Returns empty list on any failure (fail-open).
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        roots: list[str] = []
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                roots.append(line[len("worktree "):].strip())
        return roots
    except (OSError, subprocess.TimeoutExpired):
        return []


def _find_worktree_root(file_path: str, worktree_roots: list[str]) -> str | None:
    """Return the worktree root that contains file_path, or None."""
    abs_path = os.path.realpath(os.path.abspath(file_path))
    # Sort longest first so the most-specific worktree wins for nested setups.
    for root in sorted(worktree_roots, key=len, reverse=True):
        real_root = os.path.realpath(root)
        if abs_path == real_root or abs_path.startswith(real_root + os.sep):
            return real_root
    return None


# ---------------------------------------------------------------------------
# Path analysis
# ---------------------------------------------------------------------------

def _get_depth_and_parents(file_path: str, worktree_root: str) -> tuple[int, list[str]]:
    """Return (depth, intermediate_parents) relative to worktree_root.

    depth: number of directory levels between the worktree root and the file's
      immediate parent (0 = file is directly under worktree root).
    intermediate_parents: all unique parent directories between worktree_root
      and file_path (exclusive), ordered from shallowest to deepest.

    Examples:
      worktree_root = /wt/repo, file = /wt/repo/file.md
        → depth=0, parents=[]
      worktree_root = /wt/repo, file = /wt/repo/src/file.md
        → depth=1, parents=[/wt/repo/src]
      worktree_root = /wt/repo, file = /wt/repo/src/content/file.md
        → depth=2, parents=[/wt/repo/src, /wt/repo/src/content]
    """
    abs_path = os.path.realpath(os.path.abspath(file_path))
    real_root = os.path.realpath(worktree_root)

    try:
        rel = os.path.relpath(os.path.dirname(abs_path), real_root)
    except ValueError:
        return 0, []

    if rel == ".":
        return 0, []

    # Build list of intermediate parents from root down to immediate parent.
    parts = rel.split(os.sep)
    # Filter out any '..' escapes (shouldn't occur after realpath, but be safe)
    if any(p == ".." for p in parts):
        return 0, []

    parents: list[str] = []
    current = real_root
    for part in parts:
        current = os.path.join(current, part)
        parents.append(current)

    return len(parents), parents


# ---------------------------------------------------------------------------
# File-path extraction
# ---------------------------------------------------------------------------

def _get_file_path(tool_name: str, tool_input: dict) -> str:
    """Extract the file target path from tool_input."""
    if tool_name == "NotebookEdit":
        return (tool_input.get("notebook_path") or "").strip()
    return (tool_input.get("file_path") or "").strip()


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _emit_advisory(target_path: str, parent_dir: str) -> None:
    msg = _ADVISORY_TMPL.format(target_path=target_path, parent_dir=parent_dir)
    sys.stderr.write(msg)


def _emit_deny(
    target_path: str,
    parent_dir: str,
    depth: int,
    worktree_root: str,
) -> None:
    reason = _DENY_TMPL.format(
        target_path=target_path,
        parent_dir=parent_dir,
        depth=depth,
        worktree_root=worktree_root,
    )
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _main_inner() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed stdin

    tool_name = payload.get("tool_name", "") or ""
    if tool_name not in TARGET_TOOLS:
        return 0

    if os.environ.get("PRAXIS_PATH_PROBE_SKIP", "").strip() == "1":
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    file_path = _get_file_path(tool_name, tool_input)
    if not file_path:
        return 0  # no path — fail-open

    session_id = payload.get("session_id", "") or "unknown"
    cwd = payload.get("cwd", "") or os.getcwd()

    # Resolve worktree list from cwd.
    roots = _git_worktree_list(cwd)
    if not roots:
        return 0  # not a git repo or git unavailable — fail-open

    worktree_root = _find_worktree_root(file_path, roots)
    if not worktree_root:
        return 0  # path not inside any known worktree — fail-open

    depth, parents = _get_depth_and_parents(file_path, worktree_root)
    if depth == 0:
        return 0  # file is directly under worktree root — no intermediate dirs

    # The "dangerous" parent is the shallowest intermediate directory —
    # the one most likely to be a worktree-specific subdir that was
    # generalized from a compaction summary without re-probing.
    # We check the immediate parent (deepest) as the primary gate:
    # if the agent hasn't listed the direct parent, the path is suspect.
    immediate_parent = parents[-1]

    if _is_enumerated(session_id, immediate_parent):
        return 0  # already enumerated this session — pass through

    # Not yet enumerated: emit advisory or deny.
    strict = os.environ.get("PRAXIS_PATH_PROBE_STRICT", "").strip() == "1"

    # Record as seen so advisory fires at most once per (session, parent).
    # (In strict mode we do NOT record — the agent must actually enumerate.)
    if not strict:
        _record_enumerated(session_id, immediate_parent)
        _emit_advisory(file_path, immediate_parent)
        return 0
    else:
        _emit_deny(file_path, immediate_parent, depth, worktree_root)
        return 2


def main() -> int:
    """Advisory/gate hook — must never crash the Claude Code session."""
    try:
        return _main_inner()
    except Exception:
        return 0  # fail-open on any unexpected error


if __name__ == "__main__":
    sys.exit(main())
