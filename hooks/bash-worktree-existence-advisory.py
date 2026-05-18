#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: emit stderr warning when a `cd <path>` target
does not exist on disk.

Trigger pattern (issue #322):
  `cd <path>` at the start of a Bash command, or `cd <path> && ...` in a
  compound command. Each segment is inspected for a leading `cd <path>`.

Advisory message (stderr only — exit 0 always):
  [worktree-missing] <path> does not exist — check 'git worktree list' before retry
    When the resolved path does not exist on disk.

Scope (Phase 1):
  Only direct `cd <path>` command is detected. `pushd`, subshell `(cd /path
  && ...)`, heredoc bodies containing `cd` are out of scope for this phase
  to avoid parser complexity. See follow-up issue for Phase 2 expansion.

Silent cases (no output emitted):
  - `cd <path>` where path exists on disk
  - bare `cd` (no argument — cd to $HOME)
  - `cd $VAR` or `cd $(...)` — variable/substitution expansion, unresolvable
  - `cd -` — previous dir, unresolvable
  - `cd ~` or `cd ~/...` — tilde expansion; hook process $HOME may differ
    from the agent's effective $HOME (e.g. sudo to different user)
  - `cd ~user/...` — user-specific tilde, unresolvable
  - `pushd /path` — out of scope (Phase 1)
  - `(cd /path && ...)` — subshell, out of scope (Phase 1)
  - heredoc body containing `cd /path` — not parsed as command
  - opt-out marker `# worktree-advisory:ack` anywhere in command
  - non-Bash tool name
  - malformed JSON stdin

Deduplication:
  Per-session marker files in
  `${TMPDIR:-/tmp}/praxis-bash-worktree-advisory/<session_id>.<path-hash>.<state>`
  suppress repeat advisories for the same (session, path, state) triple.
  State is `missing`. If path state changes (e.g. directory is created),
  the old marker does not suppress the new state's advisory.

Design:
  - Advisory only — writes to stderr, exits 0. Never blocks tool execution.
  - Fail-open — malformed JSON, missing session_id, git failure, timeout
    all result in a silent exit 0.
  - Role-aware tokenization via `tokenize_with_roles` / `filter_argv` from
    `_hook_utils.py` — consistent with sibling advisory hooks.

Opt-out: embed `# worktree-advisory:ack` anywhere in the command to suppress
all advisories for that invocation.

Relationship to sibling hooks:
  cross-repo-worktree-preflight — fires on `git worktree remove <path>` cross-
    repo mismatch. This hook fires earlier: on the `cd <path>` step itself
    before any git operation, catching the case where the worktree directory
    was removed externally and the agent does not know yet.
  cross-boundary-preflight — gh write subcommands across --repo boundary.

Dedupe cleanup:
  Stale marker files are cleaned up by the OS tmp purge policy. No explicit
  cleanup is performed by this hook. Markers accumulate at
  ~1 file/unique-path/session, negligible footprint.
"""
from __future__ import annotations

import hashlib
import json
import os
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

# Dedupe dir — per-session markers suppress repeat advisories for the same
# (path, state) pair. State is encoded in the marker filename suffix so that
# a path transitioning from missing → exists triggers a fresh advisory.
_DEDUPE_BASE = os.path.join(os.environ.get("TMPDIR", "/tmp"), "praxis-bash-worktree-advisory")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_path(path: str) -> str:
    """Resolve symlinks and strip trailing slash for display."""
    return os.path.realpath(path.rstrip("/"))


def _extract_cd_target(seg: list[Token]) -> str | None:
    """Return the path argument of a `cd <path>` segment, else None.

    Silent cases (return None):
      - argv[0] is not `cd` (including `pushd` — out of scope Phase 1)
      - bare `cd` (no positional argument after COMMAND)
      - target starts with `$` (variable expansion, unresolvable)
      - target starts with `$(` (command substitution, unresolvable)
      - target is `-` (cd to previous dir, unresolvable)
      - target is `~` or starts with `~/` or `~<user>` (tilde expansion;
        hook process $HOME may differ from agent's effective $HOME)

    Relative targets are returned as-is; the caller resolves them against
    the effective_cwd by joining with os.path.join and normpath.
    """
    argv = filter_argv(seg)
    if not argv or argv[0].text != "cd":
        return None
    for tok in argv[1:]:
        if tok.role in (TokenRole.FLAG, TokenRole.FLAG_VALUE, TokenRole.SEPARATOR_DD):
            continue
        target = tok.text
        if not target:
            return None
        # Unresolvable cases — silent skip.
        if target == "-":
            return None
        if target.startswith("$"):
            return None
        if target == "~" or target.startswith("~/") or target.startswith("~"):
            # Covers `~`, `~/foo`, `~user/foo` (any tilde prefix).
            return None
        return target
    return None


def _dedupe_marker(session_id: str, path: str, state: str) -> str:
    """Return path to the per-session dedupe marker for (path, state).

    The `state` suffix ensures that if a path transitions from `missing` to
    exists (worktree added), the old marker does not suppress the new state's
    advisory (or silence for an existing worktree path).

    sha1 truncated to 16 hex chars: 64-bit collision space is more than
    sufficient for the ~hundreds of unique paths a single session touches.
    """
    path_hash = hashlib.sha1(path.encode()).hexdigest()[:16]
    safe_sid = session_id.replace("/", "_").replace("\\", "_")[:64]
    return os.path.join(_DEDUPE_BASE, f"{safe_sid}.{path_hash}.{state}")


def _is_deduped(session_id: str, path: str, state: str) -> bool:
    return os.path.exists(_dedupe_marker(session_id, path, state))


def _record_dedupe(session_id: str, path: str, state: str) -> None:
    os.makedirs(_DEDUPE_BASE, exist_ok=True)
    marker = _dedupe_marker(session_id, path, state)
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
    # are resolved correctly (matching the shell's sequential execution order).
    effective_cwd = os.getcwd()

    for seg in segments:
        target = _extract_cd_target(seg)
        if target is None:
            # Not a `cd` segment — no effective_cwd update here since we do
            # not execute the segment; the next `cd` segment will still resolve
            # relative paths against the last known effective_cwd.
            continue

        # Resolve target to absolute path.
        if target.startswith("/"):
            abs_target = os.path.normpath(target)
        else:
            abs_target = os.path.normpath(os.path.join(effective_cwd, target))

        real_target = _normalize_path(abs_target)

        # Check: path does not exist on disk.
        if not os.path.isdir(abs_target):
            # Dedupe: suppress if we already emitted [worktree-missing] this session.
            if session_id and _is_deduped(session_id, real_target, "missing"):
                continue
            sys.stderr.write(
                f"[worktree-missing] {real_target} does not exist"
                " — check 'git worktree list' before retry\n"
            )
            if session_id:
                _record_dedupe(session_id, real_target, "missing")
        else:
            # Path exists — update effective_cwd for downstream segments.
            effective_cwd = abs_target

    return 0


def main() -> int:
    """Advisory hook — must NEVER break tool execution."""
    try:
        return _main_inner()
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
