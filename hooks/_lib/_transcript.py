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
  iter_transcript(path)                                  -> Iterator[dict]
  load_transcript_objs(path, max_bytes)                  -> list | None
  read_transcript_tail(path, max_lines, max_bytes)       -> str | None
  load_recent_events(path, min_events, max_bytes)        -> list[dict]
  load_current_turn(path, max_bytes)                     -> list[dict]
  get_current_turn(events)                               -> list[dict]
  extract_last_assistant_text(turn)                      -> str
  has_tool_in_turn(turn, tool_name)                      -> bool
  read_last_user_message(transcript_path)                -> str | None
  scan_user_rejections(path, max_bytes, max_records)     -> list[dict]
"""
from __future__ import annotations

import json
import os
from collections import deque
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


def iter_transcript(path: str):
    """Yield each event dict in `path`, one line at a time. Fail-open.

    Same parse contract as `load_transcript` (non-JSON and non-dict lines are
    skipped, a missing or unreadable file yields nothing) without holding the
    whole transcript in memory. For a consumer that genuinely must see the
    whole session but can reduce as it goes — a 224MB session materialized as
    a list cost 741MB of RSS per Stop hook (issue #1076).
    """
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(obj, dict):
                yield obj


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


# Bytes scanned backwards from EOF before `load_current_turn` gives up looking
# for the turn boundary. A turn is one user message and the assistant work that
# answers it; Claude Code truncates individual tool results, so a real turn does
# not approach this. The cap exists so a transcript whose tail carries no
# boundary at all (a corrupted or non-Claude JSONL) degrades to a bounded read
# instead of the whole-file read this function was written to remove.
CURRENT_TURN_SCAN_MAX_BYTES = 8 * 1024 * 1024

# Reverse-read granularity. Large enough that the common case (boundary within
# the last few events) finishes in one seek.
_TAIL_CHUNK_BYTES = 256 * 1024


class TranscriptReadError(OSError):
    """The backward reader could not finish reading the transcript.

    A read that fails partway is not the same fact as a transcript with no
    signal in it, and callers act on the two differently: every gate here
    fails open on "could not read" and acts on "read it all, found nothing".
    Ending the iterator silently collapsed the first into the second.
    """


def _iter_lines_backwards(path: str, max_bytes: int):
    """Yield complete lines of `path` as bytes, from the end towards the start.

    Stops at the start of the file or once `max_bytes` have been read,
    whichever comes first. Raises `TranscriptReadError` when the file cannot
    be opened or read; callers translate that into their own fail-open value.

    Returns True when the walk reached the start of the file and False when
    the cap cut it short (read via `StopIteration.value`). A caller cannot
    infer this from the file size: the transcript is appended to live, so a
    size sampled before the walk can say "small enough" about a file that has
    since grown past the cap (codex review #1083 P2).
    """
    try:
        fh = open(path, "rb")
    except OSError as exc:
        raise TranscriptReadError(path) from exc
    with fh:
        try:
            fh.seek(0, os.SEEK_END)
            pos = fh.tell()
        except OSError as exc:
            raise TranscriptReadError(path) from exc
        partial = b""
        scanned = 0
        while pos > 0 and scanned < max_bytes:
            step = min(_TAIL_CHUNK_BYTES, pos, max_bytes - scanned)
            pos -= step
            try:
                fh.seek(pos)
                chunk = fh.read(step) + partial
            except OSError as exc:
                raise TranscriptReadError(path) from exc
            scanned += step
            lines = chunk.split(b"\n")
            # Unless this chunk reached the start of the file, its first element
            # is the tail of a line whose head is still unread — carry it into
            # the next iteration rather than parsing a fragment as a record.
            partial = b"" if pos == 0 else lines.pop(0)
            for raw in reversed(lines):
                yield raw
        return pos == 0


def load_recent_events(
    path: str,
    min_events: int = 0,
    max_bytes: int = CURRENT_TURN_SCAN_MAX_BYTES,
) -> list[dict]:
    """Tail of the transcript, read backwards, containing the current turn.

    Returns the last events of `path` — in file order — guaranteed to reach
    back past the last real user input (the turn boundary) and to hold at
    least `min_events` events. Reading stops as soon as both hold, so a caller
    that only needs the current turn pays for the current turn.

    A Stop-event session JSONL reaches hundreds of MB and every gate that
    needed only the tail was parsing all of it (issue #1076). Same empty-list
    fail-open as `load_transcript` on a missing or unreadable file.

    `min_events` is for a caller that also reads a fixed recent window past the
    turn (`events[-80:]`); the boundary alone would not guarantee that window
    is present.

    Reaching the start of the file without a boundary returns everything
    scanned, which is what `get_current_turn` returns in the same case
    (`start = 0`). Exhausting `max_bytes` without one returns `[]` instead:
    the scan runs end-to-start, so a capped tail holds the *last* slice of an
    over-long turn and has lost its earliest events — a subset of the turn,
    not a superset. A gate handed that would miss evidence that is present and
    block on it, so the honest answer is the same empty fail-open this returns
    for an unreadable file.
    """
    tail: deque[dict] = deque()
    try:
        for raw in _iter_lines_backwards(path, max_bytes):
            obj = _parse_line(raw)
            if obj is None:
                continue
            tail.appendleft(obj)
            if is_turn_boundary(obj) and len(tail) >= min_events:
                return list(tail)
    except TranscriptReadError:
        return []
    # The loop ran to completion: either the file start was reached (the tail
    # is the whole file, boundary or not) or the cap cut it short.
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    return list(tail) if size <= max_bytes else []


def load_current_turn(
    path: str, max_bytes: int = CURRENT_TURN_SCAN_MAX_BYTES
) -> list[dict]:
    """Events since the last real user input, read from the tail of `path`.

    Same result as `get_current_turn(load_transcript(path))` without parsing
    the whole transcript — see `load_recent_events` for the bound and for what
    the two capped terminations return.
    """
    return get_current_turn(load_recent_events(path, max_bytes=max_bytes))


def _parse_line(raw: bytes) -> dict | None:
    """Parse one JSONL line into a dict; None for blank, malformed, non-dict.

    Mirrors `load_transcript`, which keeps dicts and skips everything else.
    """
    raw = raw.strip()
    if not raw:
        return None
    try:
        obj = json.loads(raw.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def is_turn_boundary(ev: dict) -> bool:
    """True when `ev` is a real user input — the event a turn starts after.

    Shared by `get_current_turn` (forward, over an in-memory list) and
    `load_current_turn` (backward, over a file). One predicate so the two
    directions cannot drift into disagreeing about where a turn begins.
    """
    msg = ev.get("message", {})
    if not isinstance(msg, dict) or msg.get("role") != "user":
        return False
    if ev.get("isSidechain"):
        return False
    content = msg.get("content", [])
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return any(
            isinstance(b, dict) and b.get("type") != "tool_result"
            for b in content
        )
    return False


def get_current_turn(events: list[dict]) -> list[dict]:
    """Return events since the last real user input (non-tool-result user msg).

    Assumes every list item is a dict (the `load_transcript` contract);
    callers constructing event lists by other means must pre-filter.
    """
    last_user_idx: int | None = None
    for i, ev in enumerate(events):
        if is_turn_boundary(ev):
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

    # Walk in reverse to find the most recent user-role entry whose content
    # includes human-authored text. Reading backwards from the end (#1076):
    # this used to `readlines()` the whole transcript, which on a long session
    # is hundreds of MB for a message that sits within the last turn.
    # Driven by hand rather than by `for` so a mid-read failure is
    # distinguishable from exhaustion, without buffering the tail.
    tail = _iter_lines_backwards(transcript_path, CURRENT_TURN_SCAN_MAX_BYTES)
    reached_start = False
    while True:
        try:
            raw_bytes = next(tail)
        except StopIteration as exhausted:
            reached_start = bool(exhausted.value)
            break
        except TranscriptReadError:
            return None
        raw = raw_bytes.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue

        role = entry.get("type") or entry.get("role")
        message = entry.get("message")
        if isinstance(message, dict) and not role:
            role = message.get("role")

        if role != "user":
            continue

        # Skip sidechain (Task-subagent) events: their user-role prompt is
        # assistant-authored, not the human's message. Matches the
        # isSidechain guard the sibling scanners already apply
        # (get_current_turn / extract_last_assistant_text / has_tool_in_turn)
        # so this reader cannot surface an agent prompt as the last user
        # message (#1097).
        if entry.get("isSidechain"):
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

    # Nothing found. "" means "read it all, there is no signal" and callers may
    # act on it; that is only true when the backward walk actually reached the
    # start of the file. If the scan cap cut it short, the honest answer is the
    # unreadable one — None, which every caller fails open on. The reader
    # reports which of the two happened; a size sampled here cannot.
    return "" if reached_start else None


# ---------------------------------------------------------------------------
# User-rejection scan (issues #1007, #1013)
# ---------------------------------------------------------------------------
#
# Two consumers need the same enumeration and must not drift:
#   - preflight-gate/rejected-mutation-reconsent-gate (#1007) asks again before
#     a mutation whose target the user already refused;
#   - retrospect pre-scan lane 6 / retrospect-mix-check Gate-12 (#1013) supply
#     the denied-actions candidates a confession-biased friction scan misses.
#
# Shape, verified against a live transcript (2 records in
# ~/.claude/projects/<project>/<session>.jsonl, 659 events) rather than assumed.
# A rejection is a role:user event carrying ALL of:
#
#   {"type": "user",
#    "toolUseResult": "User rejected tool use",
#    "toolDenialKind": "user-rejected",
#    "sourceToolAssistantUUID": "<uuid of the assistant record that asked>",
#    "message": {"role": "user", "content": [
#       {"type": "tool_result", "tool_use_id": "toolu_…", "is_error": true,
#        "content": "The user doesn't want to proceed with this tool use. …"}]}}
#
# The rejection record itself carries NO tool name and NO tool input — only the
# `tool_use_id`. The name/input live in the assistant record whose `uuid` equals
# `sourceToolAssistantUUID`, so the scan is two-pass: collect rejections, then
# resolve each one's originating `tool_use` block.
#
# STRUCTURAL ONLY, belt-and-braces (#1007): all three independent markers must
# agree — the `toolDenialKind` field, `is_error: true` on the tool_result, and
# the runtime's fixed refusal sentence. No natural-language judgement is made
# anywhere in this scan; option-label text is never classified. The cost is
# stated plainly: should the runtime reword that sentence, this scan goes silent
# rather than guessing, and both consumers degrade to their pre-#1007 behaviour
# (no ask / no lane rows) — the fail-open direction ETHOS requires of a gate.

REJECTION_DENIAL_KIND = "user-rejected"
# Fixed runtime string, copied from a live record. Its apostrophe is ASCII.
REJECTION_PHRASE = "doesn't want to proceed"
_DENIAL_KIND_MARKER = '"toolDenialKind"'
_TOOL_USE_MARKER = '"tool_use"'

# Bounds. The rejection scan reads the WHOLE file (not a tail): a rejection is a
# standing NO for the rest of the session, so a tail window would expire it
# after N lines of unrelated work. The byte bound is what keeps that affordable,
# and both passes pre-filter on a cheap substring before any json.loads, so the
# common line is never parsed.
REJECTION_SCAN_MAX_BYTES = 20 * 1024 * 1024
# Most recent N rejections. A gate only needs the standing refusals, and the
# resolution pass costs one substring probe per needle per candidate line.
REJECTION_SCAN_MAX_RECORDS = 20
# Flattened-input text bound, per rejection (an AskUserQuestion payload with
# many options is the large case).
REJECTION_TEXT_MAX_CHARS = 20000


def scan_user_rejections(
    path: str,
    max_bytes: int = REJECTION_SCAN_MAX_BYTES,
    max_records: int = REJECTION_SCAN_MAX_RECORDS,
) -> list[dict]:
    """Return structurally-recorded user tool rejections, oldest → newest.

    Each entry:
      tool_use_id  — the rejected tool_use block's id
      tool_name    — resolved originating tool ("" when unresolvable)
      tool_input   — resolved tool_use input dict ({} when unresolvable)
      text         — every string leaf of `tool_input`, newline-joined and
                     bounded; the identifier/keyword surface both consumers read
      source_uuid  — `sourceToolAssistantUUID` ("" when absent)
      timestamp    — record timestamp ("" when absent)

    Fail-open: returns [] when the file is missing, unreadable, or larger than
    `max_bytes`, and skips any record it cannot parse. An unresolvable
    `tool_use_id` yields an entry with empty `tool_name`/`tool_input` rather
    than being dropped — the rejection happened either way, and a consumer that
    needs the tool identity filters on `tool_name` itself.
    """
    text = _read_bounded_text(path, max_bytes)
    if text is None:
        return []
    lines = text.splitlines()

    rejections: list[dict] = []
    for line in lines:
        # Cheap structural pre-filter: both markers are literal, so a line
        # missing either cannot be a rejection record.
        if _DENIAL_KIND_MARKER not in line or REJECTION_PHRASE not in line:
            continue
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("toolDenialKind") != REJECTION_DENIAL_KIND:
            continue
        block = _rejected_tool_result(ev)
        if block is None:
            continue
        tool_use_id = block.get("tool_use_id")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            continue
        source_uuid = ev.get("sourceToolAssistantUUID")
        timestamp = ev.get("timestamp")
        rejections.append({
            "tool_use_id": tool_use_id,
            "tool_name": "",
            "tool_input": {},
            "text": "",
            "source_uuid": source_uuid if isinstance(source_uuid, str) else "",
            "timestamp": timestamp if isinstance(timestamp, str) else "",
        })

    if not rejections:
        return []
    if max_records > 0:
        rejections = rejections[-max_records:]
    _resolve_rejected_tool_uses(lines, rejections)
    return rejections


def _rejected_tool_result(ev: dict) -> dict | None:
    """Return the rejection's tool_result block, or None if it does not qualify.

    Belt-and-braces: the block must be a `tool_result` with `is_error: true`
    AND carry the fixed refusal sentence. `toolDenialKind` is checked by the
    caller — three independent markers, no natural-language judgement.
    """
    msg = ev.get("message")
    if not isinstance(msg, dict):
        return None
    content = msg.get("content")
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        if block.get("is_error") is not True:
            continue
        if REJECTION_PHRASE not in _flatten_strings(block.get("content")):
            continue
        return block
    return None


def _resolve_rejected_tool_uses(lines: list[str], rejections: list[dict]) -> None:
    """Fill `tool_name` / `tool_input` / `text` in place from the uuid index.

    The originating assistant record is located by `sourceToolAssistantUUID`
    against the record's own `uuid`; the `tool_use` block inside it is then
    picked by `tool_use_id`. A rejection that carries no `sourceToolAssistantUUID`
    falls back to the `tool_use_id` alone, which is unique per tool call.
    """
    by_id: dict[str, dict] = {}
    for rec in rejections:
        by_id.setdefault(rec["tool_use_id"], rec)
    needles = set(by_id)
    needles.update(r["source_uuid"] for r in rejections if r["source_uuid"])

    for line in lines:
        if _TOOL_USE_MARKER not in line:
            continue
        if not any(n in line for n in needles):
            continue
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(ev, dict):
            continue
        msg = ev.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            rec = by_id.get(block.get("id"))
            if rec is None or rec["tool_name"]:
                continue
            # uuid cross-check when the rejection named its source record: a
            # replayed / resumed transcript can repeat a tool_use id, and the
            # wrong record would attribute the refusal to the wrong tool.
            if rec["source_uuid"] and ev.get("uuid") != rec["source_uuid"]:
                continue
            name = block.get("name")
            tool_input = block.get("input")
            rec["tool_name"] = name if isinstance(name, str) else ""
            rec["tool_input"] = tool_input if isinstance(tool_input, dict) else {}
            rec["text"] = _flatten_strings(rec["tool_input"])


def _flatten_strings(value, limit: int = REJECTION_TEXT_MAX_CHARS) -> str:
    """Newline-join every string leaf of `value`, bounded at `limit` chars.

    Structure-agnostic on purpose: an AskUserQuestion input nests its text under
    `questions[].question` / `.header` / `.options[].label` / `.description`,
    and a consumer extracting literal identifiers wants all of it without
    encoding that schema here (which would silently miss a renamed field).
    """
    parts: list[str] = []
    total = 0
    stack = [value]
    while stack:
        if total >= limit:
            break
        item = stack.pop(0)
        if isinstance(item, str):
            parts.append(item)
            total += len(item)
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    joined = "\n".join(parts)
    return joined[:limit]
