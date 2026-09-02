#!/usr/bin/env python3
"""PreToolUse(Edit|NotebookEdit|Write) advisory: nudge to checkpoint memory
before bulk-writing to Source-of-Truth (SOT) directories.

Background (praxis issue #443):
  Three "Loaded ≠ Retrieved" recurrences traced to the CLAUDE.md
  "Bulk-authoring loop pre-checklist" rule (N≥3 files in vault/, wiki/,
  .claude/, skills/, AGENTS.md/CLAUDE.md companions). In each case the
  session had the MEMORY.md rule loaded but failed to retrieve it at
  write-loop entry time.  Memory-addition alone was insufficient enforcement
  — hook-level structural enforcement is required.

  This is an advisory-only re-implementation of the intent from the closed
  PR #428, now targeting the Phase 3 (ADR-0001) role-dir layout.

SOT path set (matches CLAUDE.md "Bulk-authoring loop pre-checklist"):
  • vault/    — knowledge vault (any depth under a `vault` component)
  • wiki/     — wiki pages
  • .claude/  — Claude project config / memory
  • skills/   — plugin skills directory
  • AGENTS.md / CLAUDE.md — project-level agent instructions (and symlinks)

Boundary rules (prevent false positives):
  • Path component matching only — `myproject/.claude/x` triggers, but
    `/my-claude-project/x` does NOT (`.claude` must be its own component).
  • Basename matching for AGENTS.md / CLAUDE.md — the full basename must
    equal one of those two strings (case-sensitive); a file named
    `CLAUDE.md.bak` does NOT trigger.

Bulk definition (mirrors path-probe-gate session-state keying):
  "Bulk" = N≥2 SOT-flagged writes in the same session.  The advisory fires
  at exactly the 2nd SOT write (the first time a "loop" is detectable) and
  again at every subsequent write, but only once per SOT-bucket per session
  (dedup on (session_id, bucket)) — so the agent sees one reminder per
  vault/-vs-skills/ etc. transition, not one per file.

  Session state is keyed on `session_id` from the stdin payload (the canonical
  session identity per Claude Code), stored under
  `<PRAXIS_HOME>/cache/bulk-write-checkpoint/<session_id_hash>/`.
  This mirrors path-probe-gate's (session_id, parent_hash) dedup pattern.

Behavior:
  • Emits structured reminder to stderr ([praxis:bulk-write-checkpoint] prefix)
  • Always exits 0 (never blocks, never asks).
  • Bypass: PRAXIS_BULK_WRITE_BYPASS=1 silences all output and exits 0.

Fail-open contract:
  • Malformed JSON / non-target tool → exit 0 silently
  • Missing session_id → use a constant fallback key (advisory still fires
    for the process lifetime, which is conservative and safe)
  • Any uncaught exception → swallowed, exit 0
  • python3 unavailable → shell wrapper exits 0
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _paths import praxis_cache_dir  # type: ignore[import-not-found]  # noqa: E402

# ---------------------------------------------------------------------------
# Advisory message
# ---------------------------------------------------------------------------

PREFIX = "[praxis:bulk-write-checkpoint]"

_ADVISORY_MSG = """\
{p} ── SOT-FLAGGED WRITE: {target} ──────────────────────────────────
{p}
{p} Rule: Bulk-authoring loop pre-checklist (CLAUDE.md)
{p}   Writing to a Source-of-Truth directory (vault/, wiki/, .claude/,
{p}   skills/, AGENTS.md/CLAUDE.md). Before continuing the write loop:
{p}
{p}   1. Re-read MEMORY.md (or session-scoped memory) for any
{p}      feedback_*  entries overlapping this directory's purpose.
{p}   2. Read the target directory's own SOT (CLAUDE.md / AGENTS.md /
{p}      SKILL.md) to verify enum/template/convention values verbatim.
{p}   3. For each file in the loop, perform a 1-line self-check against
{p}      the captured rule before emitting content.
{p}
{p} Root cause: "Loaded ≠ Retrieved" — the rule is in context but
{p} retrieval fails at write-loop entry. This hook fires at detection
{p} time (2nd SOT write in session) to surface the retrieval gap.
{p}
{p} Bypass: PRAXIS_BULK_WRITE_BYPASS=1
{p} ─────────────────────────────────────────────────────────────────────"""


def _fmt_advisory(target: str) -> str:
    return _ADVISORY_MSG.format(p=PREFIX, target=target)


# ---------------------------------------------------------------------------
# SOT path classification
# ---------------------------------------------------------------------------

# SOT component names — any path component matching one of these (case-sensitive)
# classifies the path as SOT-flagged.
_SOT_COMPONENTS = frozenset({"vault", "wiki", ".claude", "skills"})

# SOT basenames — files whose exact basename matches one of these are SOT-flagged.
_SOT_BASENAMES = frozenset({"AGENTS.md", "CLAUDE.md"})


def _sot_bucket(path: str) -> str | None:
    """Return the SOT bucket name for `path`, or None if not SOT-flagged.

    Returns the first matching SOT component name (for dedup keying).
    Basename-matched files return their basename as the bucket.

    Component matching: a component in the path's parts must equal one of
    _SOT_COMPONENTS exactly.  This prevents `.claude` matching a component
    like `my-claude-project` — only the exact token `.claude` matches.

    Basename matching: only for AGENTS.md / CLAUDE.md (the exact filename).
    """
    if not path:
        return None

    # Normalize: strip trailing separators, resolve ./ etc. without hitting disk.
    norm = os.path.normpath(path)
    parts = norm.replace("\\", "/").split("/")

    # Basename check first (AGENTS.md / CLAUDE.md)
    basename = parts[-1] if parts else ""
    if basename in _SOT_BASENAMES:
        return basename

    # Component check (vault/, wiki/, .claude/, skills/)
    for part in parts[:-1]:  # directories only (not the leaf filename)
        if part in _SOT_COMPONENTS:
            return part

    return None


def _extract_file_path(payload: dict) -> str:
    """Extract the target file path from a PreToolUse payload."""
    tool = payload.get("tool_name", "") or ""
    tool_input = payload.get("tool_input", {}) or {}
    if not isinstance(tool_input, dict):
        return ""

    if tool in ("Write", "Edit"):
        return str(tool_input.get("file_path", "") or "")
    if tool == "NotebookEdit":
        return str(tool_input.get("notebook_path", "") or "")
    return ""


# ---------------------------------------------------------------------------
# Session-scoped write counter (keyed on session_id + SOT bucket)
# ---------------------------------------------------------------------------

_STATE_BASE = os.path.join(praxis_cache_dir(), "bulk-write-checkpoint")

# Per-session global write counter across all SOT paths (tracks total SOT writes).
# Stored as a simple touch-marker per (session_hash, bucket_hash).
_WRITE_COUNT_DIR = "counts"
# Advisory-seen marker: emitted-once per (session_hash, bucket_hash).
_ADVISORY_SEEN_DIR = "seen"


def _session_hash(session_id: str) -> str:
    return hashlib.sha1(session_id.encode()).hexdigest()[:16]


def _bucket_hash(bucket: str) -> str:
    return hashlib.sha1(bucket.encode()).hexdigest()[:8]


def _count_marker(session_hash: str, bucket: str) -> str:
    """Path to the write-count file for this session + bucket."""
    return os.path.join(
        _STATE_BASE, _WRITE_COUNT_DIR, session_hash, _bucket_hash(bucket)
    )


def _seen_marker(session_hash: str, bucket: str) -> str:
    """Path to the advisory-seen marker for this session + bucket."""
    return os.path.join(
        _STATE_BASE, _ADVISORY_SEEN_DIR, session_hash, _bucket_hash(bucket)
    )


def _increment_and_get_count(session_hash: str, bucket: str) -> int:
    """Increment the per-(session, bucket) write counter and return new value.

    Counter is stored as the integer in a UTF-8 text file.  Starts at 0 before
    the first write, returns 1 after the first, 2 after the second, etc.

    Fails silently on OSError — returns 0 (conservative: treats every write
    as the first, so advisory fires immediately rather than being suppressed).
    """
    marker = _count_marker(session_hash, bucket)
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        try:
            count = int(open(marker).read().strip())
        except (OSError, ValueError):
            count = 0
        count += 1
        with open(marker, "w") as fh:
            fh.write(str(count))
        return count
    except OSError:
        return 0  # fail-open: treat as 0 (advisory may or may not fire)


def _advisory_already_seen(session_hash: str, bucket: str) -> bool:
    return os.path.exists(_seen_marker(session_hash, bucket))


def _mark_advisory_seen(session_hash: str, bucket: str) -> None:
    marker = _seen_marker(session_hash, bucket)
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        open(marker, "w").close()
    except OSError:
        pass  # fail-open — advisory will fire again next time, which is fine


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

# Minimum number of SOT-flagged writes before the advisory fires.
# 1 = fire on the very first SOT write.
# 2 = fire when a "loop" is detectable (2nd write = bulk pattern).
BULK_THRESHOLD = 2


@fail_open
def main() -> int:
    if os.environ.get("PRAXIS_BULK_WRITE_BYPASS") == "1":
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed stdin

    tool = payload.get("tool_name", "") or ""
    if tool not in ("Write", "Edit", "NotebookEdit"):
        return 0

    file_path = _extract_file_path(payload)
    if not file_path:
        return 0

    bucket = _sot_bucket(file_path)
    if bucket is None:
        return 0  # not a SOT-flagged path — silent

    # Session keying: use session_id from payload (canonical Claude Code field).
    # Fall back to a fixed string when absent — advisory fires conservatively.
    session_id = str(payload.get("session_id", "") or "unknown-session")
    session_hash = _session_hash(session_id)

    count = _increment_and_get_count(session_hash, bucket)
    if count < BULK_THRESHOLD:
        return 0  # first write in this bucket — no advisory yet

    # 2nd+ write in this SOT bucket this session.
    if _advisory_already_seen(session_hash, bucket):
        return 0  # already emitted for this (session, bucket) — deduplicated

    _mark_advisory_seen(session_hash, bucket)
    sys.stderr.write(_fmt_advisory(file_path) + "\n")
    return 0




if __name__ == "__main__":
    sys.exit(main())
