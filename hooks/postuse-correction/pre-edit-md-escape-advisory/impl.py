#!/usr/bin/env python3
r"""Markdown escape-sensitive Edit advisory (PreToolUse + PostToolUse).

Issue #230 (sub-task of #219). Recurring failure mode: Obsidian-style
markdown files use backslash-escaped pipes inside table-cell wikilinks
(`[[01-summary\|01]]`). The agent constructs ``old_string`` with an
unescaped pipe based on prior-context recall and Edit fails exact-match.
The same risk applies to HTML entities (`&amp;`, `&lt;`) and to escaped
brackets (`\[`, `\]`) used inside table cells.

This hook acts as a structural attention-shift at Edit construction
time:

  PreToolUse  → if `tool_name == "Edit"`, `file_path` ends with `.md`,
                and `old_string` contains an escape-sensitive token,
                check whether the file was Read in this session. If not
                → advisory (default) or deny (opt-in).
  PostToolUse → if `tool_name == "Read"` and the path ends with `.md`,
                record the absolute path in the session-scoped history
                file so subsequent Edits pass.

Invocation:

    python3 pre-edit-md-escape-advisory.py pre   # PreToolUse(Edit)
    python3 pre-edit-md-escape-advisory.py post  # PostToolUse(Read)

Two shell wrappers (`pre-edit-md-escape-advisory-pre.sh`, `-post.sh`)
delegate to this module so `hooks.json` can register each event
independently.

Session state — design rationale
================================

Standard praxis paired-hook approach. PreToolUse and PostToolUse hooks
are independent processes; there is no shared in-memory state, so the
Read-history must be persisted to disk.

Resolution order:

  1. `PRAXIS_MD_READ_HISTORY_FILE` env var — explicit override, used by
     tests for isolation.
  2. `session_id` from the hook payload (primary key — stable across
     PreToolUse / PostToolUse invocations within a single Claude Code
     session) → `${TMPDIR:-/tmp}/praxis-md-read-history-<session_id>.json`.
  3. `${TMPDIR:-/tmp}/praxis-md-read-history-${PPID}.json` — last-resort
     back-compat fallback when the payload does not carry a `session_id`
     (e.g., direct CLI / test invocation).

State is keyed by `session_id` rather than `$CLAUDE_PROJECT_DIR` for the
standard praxis paired-hook reason: project-rooted state would silently
satisfy a later session's gate with a Read recorded by an earlier
session — breaking the "in this session" contract.

Read failures → empty history, fail-open. Write failures → silently
skip recording.

Detection patterns
==================

Conservative v1 set, chosen to minimize false positives:

  - `\|` (escaped pipe — Obsidian table wikilink convention)
  - `\[` / `\]` (escaped brackets — markdown table or escaped wikilink)
  - `&[A-Za-z]+;` (HTML entity — `&amp;`, `&lt;`, `&gt;`, `&quot;`,
    `&nbsp;`, etc.)

Other markdown escape forms (backslash-escaped backtick, asterisk,
underscore) are intentionally excluded from v1. They are far more common
in body prose than the table-cell escapes above, and would generate
high-noise advisories. Add to the pattern set if a recurrence is
observed in production sessions.

Default mode
============

Default mode = **advisory** (stderr message, exit 0). Opt-in **block**
mode via `PRAXIS_MD_ESCAPE_MODE=block` emits
`permissionDecision: "deny"`.

Skip rules
==========

  - `tool_name != "Edit"` (Write / NotebookEdit excluded — they do not
    carry `old_string`; Read of the target before a fresh Write is not a
    meaningful gate).
  - `file_path` does not end with `.md` (case-insensitive).
  - `old_string` does not contain any escape-sensitive token.
  - File already Read in this session (path-level granularity; line-range
    overlap deferred to v2).
  - `PRAXIS_MD_ESCAPE_SKIP=1` (full opt-out for the session).
  - Malformed stdin / missing fields → exit 0 (fail-open).

Acceptance criteria mapping (issue #230)
========================================

  - PreToolUse(Edit) advisory hook registered            → `run_pre()`
  - Detection patterns: `\|`, `\[`, `\]`, `&[a-z]+;`     → ESCAPE_PATTERN
  - Read absence → advisory; Read present → silent       → state file gate
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Escape-sensitive token patterns. Each row is a (label, compiled regex)
# pair. The label is included in the advisory message so the agent knows
# which specific escape variant tripped the gate.
ESCAPE_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    (r"\|", re.compile(r"\\\|")),
    (r"\[", re.compile(r"\\\[")),
    (r"\]", re.compile(r"\\\]")),
    ("HTML entity", re.compile(r"&[A-Za-z]+;")),
)

WARN_PREFIX = (
    "[pre-edit:md-escape] Edit target {path} contains escape-sensitive "
    "token(s): {tokens}. The file has not been Read in this session — "
    "old_string may not match the file's actual escape format "
    "(Obsidian table wikilinks, HTML entities, etc. differ across files). "
    "Read the exact line range first before constructing old_string."
)

BLOCK_REASON_PREFIX = (
    "pre-edit:md-escape — Edit target {path} contains escape-sensitive "
    "token(s): {tokens}, and the file has not been Read in this session. "
    "Read the exact line range first before constructing old_string. "
    "(Block mode active via PRAXIS_MD_ESCAPE_MODE=block.)"
)


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------


def _extract_session_id(payload: dict) -> str | None:
    """Return the trimmed `session_id` from the hook payload, or None.

    Canonical praxis hook session key — same field consumed by
    `completion-verify.sh`, `retrospect-mix-check.sh`, and
    `strike-counter.sh`.
    """
    sid = payload.get("session_id")
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    return None


def resolve_history_path(session_id: str | None = None) -> str:
    """Resolve the session-scoped md-read-history JSON path.

    See module docstring "Session state" for the resolution order.
    """
    override = os.environ.get("PRAXIS_MD_READ_HISTORY_FILE", "").strip()
    if override:
        return override

    tmp = os.environ.get("TMPDIR", "/tmp").rstrip("/") or "/tmp"
    if session_id:
        return os.path.join(tmp, f"praxis-md-read-history-{session_id}.json")
    ppid = os.getppid()
    return os.path.join(tmp, f"praxis-md-read-history-{ppid}.json")


def load_history(path: str) -> dict:
    """Return the parsed history dict, or an empty dict on any failure."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
        return {}
    except (OSError, ValueError, UnicodeDecodeError):
        return {}


def save_history(path: str, history: dict) -> bool:
    """Atomically write the history dict. Return True on success."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(history, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        return True
    except OSError:
        return False


def normalize_path(file_path: str) -> str:
    """Return an absolute, normalized path key for history storage."""
    if not file_path:
        return ""
    return os.path.abspath(file_path)


def record_read(path: str, file_path: str) -> None:
    """Record `file_path` as Read in the session history."""
    norm = normalize_path(file_path)
    if not norm:
        return
    history = load_history(path)
    read_set = history.setdefault("read", [])
    if not isinstance(read_set, list):
        read_set = []
        history["read"] = read_set
    if norm not in read_set:
        read_set.append(norm)
    history["last_updated"] = int(time.time())
    save_history(path, history)


def get_read_set(path: str) -> set[str]:
    """Return the set of normalized paths Read in this session."""
    history = load_history(path)
    read_list = history.get("read", [])
    if not isinstance(read_list, list):
        return set()
    return {p for p in read_list if isinstance(p, str)}


# ---------------------------------------------------------------------------
# Token detection
# ---------------------------------------------------------------------------


def is_markdown_target(file_path: str) -> bool:
    """True if `file_path` ends with `.md` / `.markdown` (case-insensitive)."""
    if not file_path:
        return False
    lower = file_path.lower()
    return lower.endswith(".md") or lower.endswith(".markdown")


def find_escape_tokens(old_string: str) -> list[str]:
    """Return the labels of escape-sensitive tokens present in `old_string`.

    Returned list is de-duplicated and ordered by ESCAPE_PATTERNS order.
    Empty list = no escape-sensitive content.
    """
    if not old_string:
        return []
    found: list[str] = []
    for label, pattern in ESCAPE_PATTERNS:
        if pattern.search(old_string):
            found.append(label)
    return found


# ---------------------------------------------------------------------------
# Hook entry points
# ---------------------------------------------------------------------------


def emit_deny(reason: str) -> None:
    emit_decision("deny", reason)


def run_pre() -> int:
    if os.environ.get("PRAXIS_MD_ESCAPE_SKIP", "").strip() == "1":
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed input

    tool_name = payload.get("tool_name", "") or ""
    if tool_name != "Edit":
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    file_path = (tool_input.get("file_path") or "").strip()
    if not file_path or not is_markdown_target(file_path):
        return 0

    old_string = tool_input.get("old_string", "") or ""
    if not isinstance(old_string, str):
        return 0

    tokens = find_escape_tokens(old_string)
    if not tokens:
        return 0

    history_path = resolve_history_path(_extract_session_id(payload))
    read_set = get_read_set(history_path)
    if normalize_path(file_path) in read_set:
        return 0  # already Read in this session — silent pass

    mode = os.environ.get("PRAXIS_MD_ESCAPE_MODE", "warn").strip().lower()
    tokens_csv = ", ".join(f"`{t}`" for t in tokens)

    if mode == "block":
        emit_deny(BLOCK_REASON_PREFIX.format(path=file_path, tokens=tokens_csv))
        return 0

    sys.stderr.write(
        WARN_PREFIX.format(path=file_path, tokens=tokens_csv) + "\n"
    )
    return 0


def run_post() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = payload.get("tool_name", "") or ""
    if tool_name != "Read":
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    file_path = (tool_input.get("file_path") or "").strip()
    if not file_path or not is_markdown_target(file_path):
        return 0

    history_path = resolve_history_path(_extract_session_id(payload))
    record_read(history_path, file_path)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return 0
    mode = argv[1]
    if mode == "pre":
        return run_pre()
    if mode == "post":
        return run_post()
    return 0


@fail_open
def _entry() -> int:
    # main(argv) keeps its testable signature; fail_open wraps zero-arg
    # callables only, so the wrap happens here at the process boundary.
    return main(sys.argv)


if __name__ == "__main__":
    sys.exit(_entry())
