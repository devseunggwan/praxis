#!/usr/bin/env python3
"""PreToolUse(Bash) gate: block a bare `git worktree prune` that has no
prior same-session snapshot.

Issue #870 (Refs #865). `git worktree prune` has repository-wide blast
radius: it takes no target argument and removes the administrative files of
EVERY unlocked worktree whose directory has disappeared — not just the one
the agent intended to clean up. Issue #865 / PR #867 documented a
snapshot -> dry-run -> STOP gate -> post-diff procedure in prose (see the
`praxis:worktree-merge-cleanup` skill), but no hook enforced it — a session
retrospect (2026-07-27, finding #3, HIGH) flagged the gap: the procedure can
be silently skipped and nothing catches it.

This hook enforces the first, structurally-checkable step of that procedure:
a bare `git worktree prune` (no `-n`/`--dry-run`) must be preceded, somewhere
in the same session, by a `git worktree list` snapshot. Without one, the
agent has no record of which worktrees existed before the prune, so a
mistaken prune cannot be diffed back.

## Snapshot-recorded decision (transcript tail vs state file)

Chosen: **session-scoped state file**, keyed by `session_id` — the same
primitive `session-intent` already uses (`hooks/preflight-gate/
session-intent/impl.py`), not a transcript-tail scan. A transcript-tail scan
would need to re-parse the full session's tool-call history on every Bash
invocation (unbounded cost, growing with session length) and has no stable
API in the hook payload; the state file is O(1) per call and mirrors the
established repo convention (session-intent, retrospect-active-marker).

## Detection

1. `tool_name == "Bash"` — non-Bash tools exit 0 silently.
2. Tokenize with `_hook_utils.safe_tokenize` + `iter_command_starts` (compound
   commands decomposed segment by segment, in order).
3. Any segment matching `git [global flags] worktree list [flags]` marks the
   snapshot as taken for this session (persisted immediately) AND for the
   rest of the current call (so `git worktree list --porcelain > /tmp/x &&
   git worktree prune` is self-satisfying within one compound command).
4. Any segment matching `git [global flags] worktree prune [flags]` without
   `-n`/`--dry-run` is the gated action:
   - snapshot recorded (this call or a prior one this session) -> silent pass.
   - no snapshot recorded -> **block** (exit 2), citing the
     `praxis:worktree-merge-cleanup` skill's snapshot -> dry-run -> STOP gate
     -> post-diff procedure.
   - `-n`/`--dry-run` present -> always silent pass (no destructive effect).

## Fail-open

Malformed JSON stdin, non-Bash tool, empty command, state-file read/write
failure, or any uncaught exception -> exit 0. This hook only blocks on a
positively-confirmed bare-prune-without-snapshot; it never blocks on an
inability to determine session state.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    compound_cascade_hint,
    iter_command_starts,
    safe_tokenize,
)
from block_message import emit_block  # type: ignore[import-not-found]  # noqa: E402

_BYPASS_ENV = "PRAXIS_HOOK_BYPASS_WORKTREE_PRUNE_SNAPSHOT"
_STATE_FILE_ENV = "PRAXIS_WORKTREE_PRUNE_SNAPSHOT_FILE"

# `git` global flags that consume one separate-token argument, needed to walk
# past them before checking for the `worktree` subcommand. Mirrors the same
# walk in `destructive-bash-guard`'s `_is_git_clean_force`/`_is_git_reset_hard`.
_GIT_GLOBAL_FLAGS_WITH_ARG = frozenset({"-C", "-c"})

_DRY_RUN_FLAGS = frozenset({"-n", "--dry-run"})


def _git_subcommand_words(argv: list[str], count: int) -> list[str] | None:
    """Return the first `count` non-flag words after `git`, or None.

    Skips `-C <dir>` / `-c <key=val>` (each consumes one extra token) and any
    other leading global flag (bare, no argument). Returns None if fewer than
    `count` words are found before the argv is exhausted.
    """
    if not argv or os.path.basename(argv[0]) != "git":
        return None
    words: list[str] = []
    i = 1
    n = len(argv)
    while i < n and len(words) < count:
        tok = argv[i]
        if tok in _GIT_GLOBAL_FLAGS_WITH_ARG and i + 1 < n:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        words.append(tok)
        i += 1
    if len(words) < count:
        return None
    return words


def _is_worktree_list(argv: list[str]) -> bool:
    return _git_subcommand_words(argv, 2) == ["worktree", "list"]


def _worktree_prune_dry_run(argv: list[str]) -> bool | None:
    """Return True/False for a `git worktree prune` segment (dry-run or
    not), or None if the segment is not a `git worktree prune` at all."""
    if not argv or os.path.basename(argv[0]) != "git":
        return None
    words: list[str] = []
    i = 1
    n = len(argv)
    tail_start = None
    while i < n and len(words) < 2:
        tok = argv[i]
        if tok in _GIT_GLOBAL_FLAGS_WITH_ARG and i + 1 < n:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        words.append(tok)
        i += 1
    if words != ["worktree", "prune"]:
        return None
    tail_start = i
    return any(tok in _DRY_RUN_FLAGS for tok in argv[tail_start:])


# ---------------------------------------------------------------------------
# State file IO — mirrors session-intent's atomic-write / fail-open pattern.
# ---------------------------------------------------------------------------

def _extract_session_id(payload: dict) -> str | None:
    sid = payload.get("session_id")
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    return None


def resolve_state_path(session_id: str | None = None) -> str:
    explicit = os.environ.get(_STATE_FILE_ENV, "").strip()
    if explicit:
        return explicit
    tmp = os.environ.get("TMPDIR", "/tmp").rstrip("/")
    if session_id:
        return os.path.join(tmp, f"praxis-worktree-prune-snapshot-{session_id}.json")
    ppid = os.getppid()
    return os.path.join(tmp, f"praxis-worktree-prune-snapshot-{ppid}.json")


def read_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    return {}


def write_state(path: str, state: dict) -> None:
    try:
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=parent or ".", prefix=".prune-snapshot-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError:
        pass


_BLOCK_TEXT = (
    "`git worktree prune` takes no target argument — it removes the "
    "administrative files of EVERY unlocked worktree whose directory has "
    "disappeared, not just the one you intended to clean up (issue #865)."
)
_CORRECT_PATH = (
    "Run `git worktree list --porcelain` first to snapshot the current "
    "worktree set, then re-run the prune (add `> /tmp/<file>` to keep the "
    "snapshot for a post-prune diff). See `praxis:worktree-merge-cleanup` "
    "for the full snapshot -> dry-run -> STOP gate -> post-diff procedure. "
    "A dry run (`git worktree prune -n`) is always allowed without a "
    "snapshot."
)


@fail_open
def main() -> int:
    if os.environ.get(_BYPASS_ENV, "").strip():
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "") or ""
    if not command.strip():
        return 0

    tokens = safe_tokenize(command.replace("\\\n", " "))
    if not tokens:
        return 0

    state_path = resolve_state_path(_extract_session_id(payload))
    state = read_state(state_path)
    snapshot_taken = bool(state.get("snapshot_taken"))

    for argv in iter_command_starts(tokens):
        if _is_worktree_list(argv):
            if not snapshot_taken:
                snapshot_taken = True
                state["snapshot_taken"] = True
                write_state(state_path, state)
            continue

        dry_run = _worktree_prune_dry_run(argv)
        if dry_run is None:
            continue
        if dry_run:
            continue
        if snapshot_taken:
            continue

        emit_block(
            rule_name="worktree-prune-snapshot-gate",
            why=_BLOCK_TEXT,
            correct_path=_CORRECT_PATH,
            bypass_env=_BYPASS_ENV,
            reference="issue #870, #865",
        )
        sys.stderr.write(compound_cascade_hint(command))
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
