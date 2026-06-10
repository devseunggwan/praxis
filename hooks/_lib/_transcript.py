#!/usr/bin/env python3
"""Shared transcript-scanning helpers — single source of truth (#643).

Previously these JSONL transcript readers were re-implemented per hook:

  - _load_transcript / _get_current_turn / _extract_last_assistant_text
    duplicated across readonly-verify-deferral-gate, completion-signal-gate,
    merge-state-claim-gate (Stop hooks)
  - _read_last_user_message duplicated across block-ask-end-option,
    block-manufactured-action-menu (PreToolUse AskUserQuestion gates)
  - bounded readers (_read_transcript_tail, _load_transcript_objs) in
    block-gh-issue-create-without-dup-search, block-sciomc-finding-commit
  - _TRANSCRIPT_SCAN_LINES = 400 re-declared in external-write-falsify-check,
    pre-output-falsification-gate

All consumers now import from here so the parsing semantics cannot drift.
The canonical bodies adopt the most defensive variant that existed
(merge-state-claim-gate's `isinstance(block, dict)` guards): a malformed
content block is skipped instead of raising into the caller's fail-open
wrapper, which silently disabled the whole scan.

Transcript format: JSONL where each line is a JSON object with at least
`type` ('user' / 'assistant' / 'system') and a nested `message` dict
(Anthropic API shape with `role` + `content`). A flatter top-level
`{"role": ..., "content": ...}` shape is additionally tolerated by
`read_last_user_message` ONLY — live transcripts contain no such events
(probe: 0 of 2,503 events across 3 real session files), so the
turn-scanning helpers deliberately read just the nested shape, matching
the pre-hoist hook behavior.

tool_result handling (codex review #193 F2): an Anthropic user-role message
may carry only `tool_result` content blocks when the assistant invoked tools
in the same turn. Such entries are NOT human authored — they are the
runtime's bridge for tool outputs. Turn-boundary detection and
last-user-message extraction both skip them.

Public API:
  TRANSCRIPT_SCAN_LINES                                  — default tail window
  load_transcript(path)                                  -> list[dict]
  load_transcript_objs(path, max_bytes)                  -> list | None
  read_transcript_tail(path, max_lines, max_bytes)       -> str | None
  get_current_turn(events)                               -> list[dict]
  extract_last_assistant_text(turn)                      -> str
  has_tool_in_turn(turn, tool_name)                      -> bool
  read_last_user_message(transcript_path)                -> str | None
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# Default tail window (in JSONL lines) for substring scans over the recent
# transcript. Shared by external-write-falsify-check and
# pre-output-falsification-gate.
TRANSCRIPT_SCAN_LINES = 400


def load_transcript(path: str) -> list[dict]:
    """Load JSONL transcript, return list of event dicts. Fail-open.

    Unbounded read; non-JSON lines and non-dict objects are skipped.
    Returns [] when the file is missing or unreadable.
    """
    events: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        events.append(obj)
                except Exception:
                    continue
    except Exception:
        pass
    return events


def load_transcript_objs(path: str, max_bytes: int) -> list | None:
    """Bounded loader returning raw parsed objects (dicts and non-dicts).

    Returns None when the file is missing, unreadable, or larger than
    `max_bytes` — callers treat None as "cannot scan, fail open".
    Non-JSON lines are skipped, scanning continues.

    The bound is enforced on the bytes actually read (not a stat()
    pre-check): a live session can append to the transcript between a
    stat and the read, which would defeat the contract.
    """
    text = _read_bounded_text(path, max_bytes)
    if text is None:
        return None
    objs: list = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            objs.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue  # skip non-JSON lines, keep scanning
    return objs


def read_transcript_tail(path: str, max_lines: int, max_bytes: int) -> str | None:
    """Return the last `max_lines` lines of the transcript as raw text.

    Returns None when the file is missing, unreadable, or larger than
    `max_bytes` — callers treat None as "cannot scan, fail open".
    The bound is enforced on the bytes actually read (see
    `load_transcript_objs`).
    """
    text = _read_bounded_text(path, max_bytes)
    if text is None:
        return None
    lines = text.strip().split("\n")
    return "\n".join(lines[-max_lines:])


def _read_bounded_text(path: str, max_bytes: int) -> str | None:
    """Read at most `max_bytes` bytes; None when missing or over the bound."""
    try:
        p = Path(path)
        if not p.is_file():
            return None
        with p.open("rb") as f:
            data = f.read(max_bytes + 1)
    except (OSError, ValueError):
        return None
    if len(data) > max_bytes:
        return None
    return data.decode("utf-8", errors="replace")


def get_current_turn(events: list[dict]) -> list[dict]:
    """Return events since the last real user input (non-tool-result user msg).

    Assumes every list item is a dict (the `load_transcript` contract);
    callers constructing event lists by other means must pre-filter.
    """
    last_user_idx: int | None = None
    for i, ev in enumerate(events):
        msg = ev.get("message", {})
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        if ev.get("isSidechain"):
            continue
        content = msg.get("content", [])
        if isinstance(content, str):
            last_user_idx = i
        elif isinstance(content, list):
            non_tool = [
                b for b in content
                if isinstance(b, dict) and b.get("type") != "tool_result"
            ]
            if non_tool:
                last_user_idx = i
    start = 0 if last_user_idx is None else last_user_idx + 1
    return events[start:]


def extract_last_assistant_text(turn: list[dict]) -> str:
    """Extract text from the last assistant message in the turn."""
    last_msg: dict | None = None
    for ev in turn:
        msg = ev.get("message", {})
        if isinstance(msg, dict) and msg.get("role") == "assistant" \
                and not ev.get("isSidechain"):
            last_msg = msg
    if last_msg is None:
        return ""
    content = last_msg.get("content", [])
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def has_tool_in_turn(turn: list[dict], tool_name: str) -> bool:
    """True if any assistant message in the turn used the named tool."""
    for ev in turn:
        msg = ev.get("message", {})
        if not isinstance(msg, dict) or msg.get("role") != "assistant" \
                or ev.get("isSidechain"):
            continue
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use" \
                        and block.get("name") == tool_name:
                    return True
    return False


def read_last_user_message(transcript_path: str) -> str | None:
    """Return the text of the most recent user-authored message in the transcript.

    Returns None when the transcript is missing or unreadable — the caller
    must fail open per the project hook design contract (`Fail-open on
    infrastructure errors`). Returns empty string when the transcript was
    read successfully but no user message contained extractable human
    text — that is a real "no signal" answer and may be acted on.

    tool_result-only user entries are skipped (continue), not returned as
    empty: returning "" on the first tool_result-only entry would block the
    backward walk at the wrong layer and false-fire strict-mode gates even
    though the real user message earlier in the transcript carried the
    signal (codex review #193 F2).
    """
    if not transcript_path or not os.path.isfile(transcript_path):
        return None
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None

    # Walk in reverse to find the most recent user-role entry whose
    # content includes human-authored text.
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue

        role = entry.get("type") or entry.get("role")
        message = entry.get("message")
        if isinstance(message, dict) and not role:
            role = message.get("role")

        if role != "user":
            continue

        # Extract text. Possible shapes:
        #   {"type": "user", "message": {"role": "user", "content": "text"}}
        #   {"type": "user", "message": {"role": "user", "content": [{"type":"text","text":"..."}]}}
        #   {"type": "user", "message": {"role": "user", "content": [{"type":"tool_result", ...}]}}
        #   {"role": "user", "content": "text"}
        content = None
        if isinstance(message, dict):
            content = message.get("content")
        if content is None:
            content = entry.get("content")

        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    # Skip non-text blocks (tool_result, image, etc.).
                    # Only `type: text` (or items lacking a type but
                    # carrying a `text` field) count as human content.
                    item_type = item.get("type")
                    if item_type and item_type != "text":
                        continue
                    t = item.get("text")
                    if isinstance(t, str):
                        parts.append(t)
                elif isinstance(item, str):
                    parts.append(item)
            text = "\n".join(parts)
        # else: unexpected content shape — fall through to skip

        if text.strip():
            return text
        # No human text in this entry — keep walking backward.

    return ""
