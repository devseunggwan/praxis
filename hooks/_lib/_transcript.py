#!/usr/bin/env python3
"""Shared transcript-scanning helpers — single source of truth (#643).

Previously these JSONL transcript readers were re-implemented per hook:

  - _load_transcript / _get_current_turn / _extract_last_assistant_text
    duplicated across readonly-verify-deferral-gate, completion-signal-gate,
    merge-state-claim-gate (Stop hooks)
  - _read_last_user_message duplicated across block-ask-end-option,
    block-manufactured-action-menu (PreToolUse AskUserQuestion gates)
  - bounded whole-file readers (_read_transcript_tail, _load_transcript_objs)
    in block-gh-issue-create-without-dup-search, block-sciomc-finding-commit —
    both since retired for the backward `tail_lines` reader (#1240, #1279)
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
  iter_transcript(path, needle=None)                     -> Iterator[dict]  (needle: str | tuple, any-of)
  iter_transcript_bounded(path, max_bytes, needle=None)  -> Iterator[dict]
  json_needle(value)                                     -> bytes | None
  load_recent_events(path, min_events, max_bytes)        -> list[dict]
  load_current_turn(path, max_bytes)                     -> list[dict]
  get_current_turn(events)                               -> list[dict]
  resolve_stop_transcript(payload)                       -> (path, is_agent)
  load_stop_turn(payload, max_bytes)                     -> list[dict]
  stop_last_assistant_text(payload, turn)                -> str
  extract_last_assistant_text(turn)                      -> str
  has_tool_in_turn(turn, tool_name)                      -> bool
  read_last_user_message(transcript_path)                -> str | None
  scan_user_rejections(path, max_bytes, max_records)     -> list[dict] | None
  stop_scan_cursor_path(hook, session_id)               -> str | None
  reduce_transcript_resumable(path, cursor_path, new_state, reduce_event, encode, decode)
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import deque
from typing import cast
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


def iter_transcript(path: str, needle: str | tuple[str, ...] | None = None):
    """Yield each event dict in `path`, one line at a time. Fail-open.

    Same parse contract as `load_transcript` (non-JSON and non-dict lines are
    skipped, a missing or unreadable file yields nothing) without holding the
    whole transcript in memory. For a consumer that genuinely must see the
    whole session but can reduce as it goes — a 224MB session materialized as
    a list cost 741MB of RSS per Stop hook (issue #1076).

    `needle` is a literal every record the caller can use must contain, or a
    tuple of them (any-of), so a line without one is rejected before
    `json.loads` (issue #1278). The substring test runs in C; the parse is
    what a whole-session walk actually pays for (42,000 parses cost 440 ms on
    a 36 MB session). It is a necessary condition only: the caller still
    checks the parsed record, so a needle that appears in unrelated text costs
    a parse, never a wrong answer.

    Write the needle as the exact JSON token, delimiting quotes included —
    `'"tool_use"'`, not `'tool_use'`: the bare form also matches
    `"tool_use_id"` on every tool_result line, the largest lines in a
    session, and the filter stops filtering. Keep it to characters the
    encoder leaves alone inside a string value (no backslash, inner quote,
    control character, or non-ASCII under `ensure_ascii`), or it will miss
    records that do match.
    """
    needles = (needle,) if isinstance(needle, str) else needle
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    with f:
        for line in f:
            if needles is not None and not any(n in line for n in needles):
                continue
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(obj, dict):
                yield obj


def _read_bounded_text(path: str, max_bytes: int) -> str | None:
    """Read at most `max_bytes` bytes; None when missing or over the bound.

    The bound is enforced on the bytes actually read (not a stat() pre-check):
    a live session can append to the transcript between a stat and the read,
    which would defeat the contract.
    """
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


def _is_over_byte_bound(path: str, max_bytes: int) -> bool:
    """True when `path` exists and is larger than `max_bytes`.

    Separates the two reasons `_read_bounded_text` answers None, so a caller
    that must distinguish "scanned, found nothing" from "never scanned" can ask
    which one it got. Any stat error answers False: an unreadable file is not
    evidence of length, and the callers that branch on this treat False as the
    ordinary path.
    """
    try:
        return Path(path).stat().st_size > max_bytes
    except OSError:
        return False


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


def tail_lines(
    path: str, max_lines: int, max_bytes: int | None = None, *, strict: bool = False
) -> list[str]:
    """The last `max_lines` lines of `path`, in file order, decoded leniently.

    Reads from the end (see `_iter_lines_backwards`) so a hook that only
    matches against the recent tail never loads a 200 MB transcript into
    memory (issue #1240). `[]` when the file is missing or unreadable — the
    same fail-open value the former `readlines()` callers used.

    `strict=True` raises `TranscriptReadError` instead of returning `[]` for
    a missing or unreadable file, for the caller that must act on "read it
    all, found nothing" and fail open on "could not read" — the two answers
    `[]` folds together (issue #1279). It is one open: a probe-then-read
    leaves a window in which a file that passed the probe is gone by the
    read, and the gate then blocks on an emptiness it never saw.

    Unbounded by default: the callers' contract is "the last N lines", and a
    byte cap that stopped short would hand them a shorter window that looks
    complete — a `grep` or a rejection just outside the suffix would go unseen
    by a gate that then blocks or waves through on it. Memory is bounded by
    the N lines themselves, which is what `readlines()` never bounded.
    `max_bytes` exists for callers that prefer a partial answer to a long read.
    """
    out: list[str] = []
    first = True
    try:
        for raw in _iter_lines_backwards(path, sys.maxsize if max_bytes is None else max_bytes):
            # Only the very first piece can be the split artifact after a
            # trailing newline; a later b"" is a real blank line, and dropping
            # it would pull an older record into the window.
            if first and raw == b"":
                first = False
                continue
            first = False
            out.append(raw.decode("utf-8", errors="replace"))
            if len(out) >= max_lines:
                break
    except TranscriptReadError:
        if strict:
            raise
        return []
    out.reverse()
    return out


def load_recent_events(
    path: str,
    min_events: int = 0,
    max_bytes: int = CURRENT_TURN_SCAN_MAX_BYTES,
    *,
    drop_sidechain: bool = False,
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

    `drop_sidechain` removes each event's `isSidechain` marker as it is
    parsed. It is for a SubagentStop `agent_transcript_path` and nothing else
    (`load_stop_turn` is the only caller that sets it): every event in a
    per-agent transcript belongs to that agent, so the marker — which exists
    to keep a subagent's events out of the MAIN transcript's turn — would make
    `is_turn_boundary` and every downstream helper that filters on it answer
    "nothing here" for a file that is entirely the subagent's. Default False
    leaves the main-transcript reading of every existing caller untouched.
    """
    tail: deque[dict] = deque()
    try:
        for raw in _iter_lines_backwards(path, max_bytes):
            obj = _parse_line(raw)
            if obj is None:
                continue
            if drop_sidechain:
                obj.pop("isSidechain", None)
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


# Process-local memo for `load_current_turn` (issue #1281). None = disabled,
# which is the standalone state: one hook process makes one call, so there is
# nothing to share and no staleness to reason about. The dispatcher enables it
# for the span of one group run — eight Stop members read the same transcript
# tail, and under one process the second through eighth parse would only
# repeat the first — and disables it again on the way out, so a test that
# drives several groups through one interpreter never sees a previous run's
# entry. The key carries the file's size and mtime so a transcript appended
# between two members (the host writes it only between turns, but a memo must
# not rely on that) re-reads instead of answering from the stale tail.
_TURN_MEMO: dict[tuple, list[dict]] | None = None


def enable_turn_memo() -> None:
    """Start memoizing `load_current_turn` results in this process (dispatcher)."""
    global _TURN_MEMO
    _TURN_MEMO = {}


def disable_turn_memo() -> None:
    """Drop the memo and return `load_current_turn` to its standalone behavior."""
    global _TURN_MEMO
    _TURN_MEMO = None


def load_current_turn(
    path: str, max_bytes: int = CURRENT_TURN_SCAN_MAX_BYTES
) -> list[dict]:
    """Events since the last real user input, read from the tail of `path`.

    Same result as `get_current_turn(load_transcript(path))` without parsing
    the whole transcript — see `load_recent_events` for the bound and for what
    the two capped terminations return.

    Under the dispatcher's memo (`enable_turn_memo`) a repeat call for the same
    `(path, max_bytes)` on an unchanged file answers from the first parse. The
    list handed back is a fresh copy each time, so one member's `.pop()` or
    `.append()` cannot leak into a sibling's view of the turn.
    """
    memo = _TURN_MEMO
    if memo is None:
        return get_current_turn(load_recent_events(path, max_bytes=max_bytes))
    try:
        st = os.stat(path)
        key: tuple | None = (path, max_bytes, st.st_size, st.st_mtime_ns)
    except OSError:
        key = None
    if key is not None and key in memo:
        return list(memo[key])
    turn = get_current_turn(load_recent_events(path, max_bytes=max_bytes))
    if key is not None:
        memo[key] = turn
    return list(turn)


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


class TranscriptTooLarge(TranscriptReadError):
    """`iter_transcript_bounded` stopped because `path` is past its byte bound.

    A subclass of `TranscriptReadError` so a caller that only wants "could not
    scan" catches one class; a caller that must tell the two apart catches
    this one first.
    """


def json_needle(value: str) -> bytes | None:
    """`value` exactly as JSON encodes it as a string value — quotes included —
    or None when the encoding would rewrite it.

    The needle a bounded scan rejects lines on must be a *necessary*
    condition: every record that can satisfy the caller carries it. Two
    things break that. Dropping the quotes makes `praxis:x` match prose that
    merely mentions the skill, costing a parse per mention. Encoding with
    `ensure_ascii=False` makes a non-ASCII name match only a writer that
    emits raw UTF-8, while a writer that escapes it (`json.dumps` default)
    would slip every real record past the filter — a fail-closed miss. So
    the probe is built with the default encoder, and a value it escapes at
    all answers None: the caller then parses every line.
    """
    encoded = json.dumps(value)
    if encoded != f'"{value}"':
        return None
    return encoded.encode("ascii")


def _line_has(raw: bytes, needle) -> bool:
    if needle is None:
        return True
    if isinstance(needle, (bytes, bytearray)):
        return needle in raw
    return any(n in raw for n in needle)


def iter_transcript_bounded(path: str, max_bytes: int, needle=None):
    """Yield each event dict of `path` whose raw line carries `needle`, reading
    at most `max_bytes` (issues #1277, #1312).

    One reader for every gate that scans a whole session under a size cap —
    three hooks carried byte-identical copies of this loop and the copies had
    already diverged (one lost its read-error guard, one its NUL-path guard)
    by the time the bounded-`readline` fix had to be applied to each by hand.

    `needle` is bytes, a tuple of bytes (any-of), or None. A line without it
    is rejected before `json.loads`; the test runs in C and is what a
    whole-session walk otherwise pays for. Build it with `json_needle` when it
    is a JSON string value, or use a bare literal when it must also match
    non-JSON text (a slash command inside a message).

    Raises `TranscriptReadError` when the file is missing, not a regular
    file, or fails to read — a FIFO at the path would otherwise block
    `open()` for the caller's whole budget — and `TranscriptTooLarge` when the
    file is past `max_bytes` at open (`fstat`, an O(1) early-out: a session
    only grows) or grows past it while being read. Each read is capped at
    the budget left, so one oversized line is refused before it is
    allocated. Callers map both to their fail-open value; nothing here
    decides for them.
    """
    try:
        if not Path(path).is_file():
            raise TranscriptReadError(path)
        fh = open(path, "rb")
    except TranscriptReadError:
        raise
    except (OSError, ValueError) as exc:
        raise TranscriptReadError(path) from exc
    with fh:
        try:
            if os.fstat(fh.fileno()).st_size > max_bytes:
                raise TranscriptTooLarge(path)
            consumed = 0
            while True:
                raw = fh.readline(max_bytes - consumed + 1)
                if not raw:
                    return
                consumed += len(raw)
                if consumed > max_bytes:
                    raise TranscriptTooLarge(path)
                if not _line_has(raw, needle):
                    continue
                obj = _parse_line(raw)
                if obj is not None:
                    yield obj
        except TranscriptReadError:
            raise
        except OSError as exc:
            raise TranscriptReadError(path) from exc


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


# --------------------------------------------------------------------------- #
# Stop / SubagentStop payload readers (issue #1337 item 2)
# --------------------------------------------------------------------------- #
#
# SubagentStop fires when a subagent finishes and uses the same decision
# format as Stop, so the completion gates can grade a subagent's final turn
# the way they grade the main one. Its payload carries TWO transcripts:
# `transcript_path` is the main session's, `agent_transcript_path` is the
# subagent's own, "stored in a nested subagents/ folder" (hooks reference,
# read 2026-09-06). A gate that keeps reading `transcript_path` on
# SubagentStop grades the parent's turn against the subagent's claim — the
# defect these two helpers exist to prevent.


def resolve_stop_transcript(payload: dict) -> tuple[str, bool]:
    """Return `(path, is_agent)` for a Stop / SubagentStop payload.

    A payload that is about a subagent — `hook_event_name` says SubagentStop,
    or it carries an `agent_transcript_path` — resolves to that agent
    transcript **or to nothing**. It never falls back to `transcript_path`.

    The fallback is what the earlier draft of this function did, and it
    reintroduced the exact defect the SubagentStop registration exists to
    remove (CodeRabbit on #1358). With an unflushed agent transcript the gates
    read the PARENT's turn while `stop_last_assistant_text` takes the
    SUBAGENT's `last_assistant_message`: a subagent that ran nothing and
    merely repeated a number from the parent's output ("9 tests passed. All
    done.") satisfied `completion-verify`'s evidence and paste checks against
    evidence it never produced. Grading one conversation's claim with another
    conversation's evidence is worse than not grading it, so the unreadable
    case returns "" and every caller's "no path → pass" check fires.

    A plain Stop payload carries neither signal and resolves to
    `transcript_path` as before.
    """
    if not isinstance(payload, dict):
        return "", False
    agent_path = payload.get("agent_transcript_path")
    has_agent_path = isinstance(agent_path, str) and bool(agent_path)
    is_subagent = (
        payload.get("hook_event_name") == "SubagentStop" or has_agent_path
    )
    if is_subagent:
        if has_agent_path and os.path.isfile(cast(str, agent_path)):
            return cast(str, agent_path), True
        return "", True
    main_path = payload.get("transcript_path")
    return (main_path if isinstance(main_path, str) else ""), False


def load_stop_turn(
    payload: dict, max_bytes: int = CURRENT_TURN_SCAN_MAX_BYTES
) -> list[dict]:
    """Current turn for a Stop / SubagentStop payload — `[]` when unreadable.

    On a main-session transcript this is exactly `load_current_turn`, memo and
    all. On a subagent's own transcript the sidechain markers are dropped as
    the tail is parsed (`load_recent_events(drop_sidechain=True)`); see that
    function for why the marker cannot survive into a per-agent file's turn.
    The memo is deliberately not used on the agent path: it keys on
    `(path, max_bytes)` alone, and the two readings of one path must never
    answer each other.
    """
    path, is_agent = resolve_stop_transcript(payload)
    if not path or not os.path.isfile(path):
        return []
    if not is_agent:
        return load_current_turn(path, max_bytes)
    return get_current_turn(
        load_recent_events(path, max_bytes=max_bytes, drop_sidechain=True)
    )


def stop_last_assistant_text(payload: dict, turn: list[dict]) -> str:
    """Final assistant text for a Stop / SubagentStop payload.

    Prefers the payload's `last_assistant_message`. The transcript "is written
    asynchronously and may lag the in-memory conversation, so it may not yet
    include the current turn's most recent messages when a hook fires. Hooks
    that need the final assistant text of the current turn should use
    `last_assistant_message` on Stop and SubagentStop instead of reading the
    transcript" (hooks reference, read 2026-09-06). Falls back to the turn's
    own last assistant message when the field is absent or not a string —
    every payload from a host that predates the field.

    `turn` is still the caller's gate: a caller passes the turn it already
    loaded and returns early on an empty one, so a payload-supplied claim can
    never be graded against evidence that was never read.
    """
    if isinstance(payload, dict):
        text = payload.get("last_assistant_message")
        if isinstance(text, str) and text.strip():
            return text
    return extract_last_assistant_text(turn)


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
# The same literals as bytes, for the pre-parse probes over raw lines.
_DENIAL_KIND_MARKER_B = _DENIAL_KIND_MARKER.encode("ascii")
REJECTION_PHRASE_B = REJECTION_PHRASE.encode("ascii")
_TOOL_USE_MARKER_B = _TOOL_USE_MARKER.encode("ascii")

# Bounds. The rejection scan reads the WHOLE file (not a tail): a rejection is a
# standing NO for the rest of the session, so a tail window would expire it
# after N lines of unrelated work. The byte bound is what keeps that affordable,
# and both passes pre-filter on a cheap substring before any json.loads, so the
# common line is never parsed. Both passes STREAM the file (issue #1280): the
# earlier shape read the bound's worth into one string and then split it into
# a list — two copies of up to 20 MB, plus the parsed records — on every
# destructive command that reached the gate. Streaming keeps the peak at one
# line plus the handful of records that matched.
REJECTION_SCAN_MAX_BYTES = 20 * 1024 * 1024
# Most recent N rejections. A gate only needs the standing refusals, and the
# resolution pass costs one substring probe per needle per candidate line.
REJECTION_SCAN_MAX_RECORDS = 20
# Flattened-input text bound, per rejection (an AskUserQuestion payload with
# many options is the large case).
REJECTION_TEXT_MAX_CHARS = 20000


# How many recent `tool_use` blocks a rejection can still be resolved against.
# A rejection is the tool_result of the user turn right after the assistant
# turn that carried the tool_use, so the distance between the two is the
# number of parallel tool calls in that one turn — rarely above ten. The ring
# is state, so it is persisted with the cursor: a rejection read in one call
# still resolves against a tool_use read in an earlier one.
REJECTION_RECENT_TOOL_USES = 32
# A stored tool_input larger than this (serialized) is kept as text only: the
# ring travels through the cursor file on every call, and a Write body would
# otherwise be rewritten to disk on every destructive command.
_REJECTION_INPUT_MAX_CHARS = REJECTION_TEXT_MAX_CHARS


def _rejection_state() -> dict:
    """The empty reducer state: rejections found so far and the tool_use ring."""
    return {"rejections": [], "recent": []}


def _note_tool_uses(state: dict, ev: dict) -> None:
    """Append the assistant record's tool_use blocks to the recent ring."""
    msg = ev.get("message")
    if not isinstance(msg, dict) or msg.get("role") != "assistant":
        return
    content = msg.get("content")
    if not isinstance(content, list):
        return
    uuid = ev.get("uuid")
    recent = state["recent"]
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        block_id = block.get("id")
        if not isinstance(block_id, str) or not block_id:
            continue
        name = block.get("name")
        tool_input = block.get("input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        text = _flatten_strings(tool_input)
        try:
            small = len(json.dumps(tool_input)) <= _REJECTION_INPUT_MAX_CHARS
        except (TypeError, ValueError):
            small = False
        recent.append({
            "id": block_id,
            "uuid": uuid if isinstance(uuid, str) else "",
            "name": name if isinstance(name, str) else "",
            "input": tool_input if small else {},
            "text": text,
        })
    if len(recent) > REJECTION_RECENT_TOOL_USES:
        del recent[: len(recent) - REJECTION_RECENT_TOOL_USES]


def _note_rejection(state: dict, ev: dict, max_records: int) -> None:
    """Append the record's rejection, resolved against the recent ring."""
    if ev.get("toolDenialKind") != REJECTION_DENIAL_KIND:
        return
    block = _rejected_tool_result(ev)
    if block is None:
        return
    tool_use_id = block.get("tool_use_id")
    if not isinstance(tool_use_id, str) or not tool_use_id:
        return
    source_uuid = ev.get("sourceToolAssistantUUID")
    source_uuid = source_uuid if isinstance(source_uuid, str) else ""
    timestamp = ev.get("timestamp")
    entry = {
        "tool_use_id": tool_use_id,
        "tool_name": "",
        "tool_input": {},
        "text": "",
        "source_uuid": source_uuid,
        "timestamp": timestamp if isinstance(timestamp, str) else "",
    }
    # Newest first: a replayed / resumed transcript can repeat a tool_use id,
    # and when the rejection names its source record only that record may
    # attribute the refusal (the uuid cross-check the two-pass scan had).
    for use in reversed(state["recent"]):
        if use["id"] != tool_use_id:
            continue
        if source_uuid and use["uuid"] != source_uuid:
            continue
        entry["tool_name"] = use["name"]
        entry["tool_input"] = use["input"]
        entry["text"] = use["text"]
        break
    rejections = state["rejections"]
    rejections.append(entry)
    if max_records > 0 and len(rejections) > max_records:
        del rejections[: len(rejections) - max_records]


def _reduce_rejection_event(state: dict, ev: dict, max_records: int) -> None:
    """Route one record: assistant records feed the tool_use ring, the rest
    are tested as rejections. `message` is guarded like every other reader
    here — a record whose `message` is a string must not abort the scan."""
    msg = ev.get("message")
    role = msg.get("role") if isinstance(msg, dict) else None
    if ev.get("type") == "assistant" or role == "assistant":
        _note_tool_uses(state, ev)
        return
    _note_rejection(state, ev, max_records)


_REJECTION_NEEDLES = (_TOOL_USE_MARKER_B, _DENIAL_KIND_MARKER_B)


def scan_user_rejections(
    path: str,
    max_bytes: int = REJECTION_SCAN_MAX_BYTES,
    max_records: int = REJECTION_SCAN_MAX_RECORDS,
    cursor_path: str | None = None,
) -> list[dict] | None:
    """Return structurally-recorded user tool rejections, oldest → newest.

    Each entry:
      tool_use_id  — the rejected tool_use block's id
      tool_name    — resolved originating tool ("" when unresolvable)
      tool_input   — resolved tool_use input dict ({} when unresolvable, and
                     {} when the input serializes past the text bound — the
                     `text` is still carried)
      text         — every string leaf of `tool_input`, newline-joined and
                     bounded; the identifier/keyword surface both consumers read
      source_uuid  — `sourceToolAssistantUUID` ("" when absent)
      timestamp    — record timestamp ("" when absent)

    One forward pass through `scan_transcript_resumable`: an assistant record
    parks its `tool_use` blocks in a bounded ring, a rejection record resolves
    against the ring. Only lines carrying the `tool_use` or `toolDenialKind`
    literal are parsed. With `cursor_path` (see `scan_cursor_path`) the pass
    resumes where the previous call stopped, so the byte bound is a budget
    per call rather than a ceiling on the session: a transcript past it is
    indeterminate for the calls it takes to catch up, then costs its delta.

    Returns None when the scan did not reach the end of the file within
    `max_bytes` — INDETERMINATE, not "no rejections" (issue #1231). Folding
    that into [] made the two indistinguishable at exactly the wrong place:
    the bound is hit by long sessions, and a long session is where standing
    refusals accumulate, so the answer went silent precisely where it carried
    the most. Every consumer decides for itself which way to fail on None.

    A missing or unreadable file still returns [] rather than None. The two are
    different evidence: a file past the bound is proof that a session history
    exists and is long, while an absent one says nothing about whether there is
    any history to be blind to, and routing it through the indeterminate branch
    would fire the consumers' fail-closed paths on every host that hands over a
    path it never wrote.

    Skips any record it cannot parse. An unresolvable `tool_use_id` yields an
    entry with empty `tool_name`/`tool_input` rather than being dropped — the
    rejection happened either way, and a consumer that needs the tool identity
    filters on `tool_name` itself.
    """
    try:
        state, complete = scan_transcript_resumable(
            path,
            cursor_path,
            _rejection_state,
            lambda st, ev: _reduce_rejection_event(st, ev, max_records),
            needle=_REJECTION_NEEDLES,
            max_bytes=max_bytes,
        )
    except TranscriptReadError:
        # Unreadable: still [] — unless the file is provably past the bound,
        # where the honest answer stays the indeterminate one a readable
        # oversized file gives (#1231): a long session the hook cannot open
        # is exactly where standing refusals have had time to accumulate.
        return None if _is_over_byte_bound(path, max_bytes) else []
    if not complete:
        return None  # not caught up this call — indeterminate (#1231)
    rejections = state["rejections"]
    if max_records > 0:
        rejections = rejections[-max_records:]
    return [dict(r) for r in rejections]


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


def _flatten_strings(value, limit: int = REJECTION_TEXT_MAX_CHARS) -> str:
    """Newline-join every string leaf of `value`, bounded at `limit` chars.

    Structure-agnostic on purpose: an AskUserQuestion input nests its text under
    `questions[].question` / `.header` / `.options[].label` / `.description`,
    and a consumer extracting literal identifiers wants all of it without
    encoding that schema here (which would silently miss a renamed field).
    """
    parts: list[str] = []
    total = 0
    stack: deque = deque([value])
    while stack:
        if total >= limit:
            break
        item = stack.popleft()
        if isinstance(item, str):
            parts.append(item)
            total += len(item)
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    joined = "\n".join(parts)
    return joined[:limit]


# ---------------------------------------------------------------------------
# Resumable whole-transcript reduction (issue #1237)
# ---------------------------------------------------------------------------

# A gate that needs the whole session (a PR created anywhere in it, a verdict
# stated in any earlier turn) used to re-parse the transcript from byte 0 on
# every Stop, so its cost tracked session length: 214MB cost 2-5s per gate
# against a 10s timeout, past which the gate renders no decision at all. The
# reduction such a gate keeps is tiny, so it is persisted beside a byte
# offset and only the bytes appended since the last Stop are parsed.
_CURSOR_VERSION = 1


def _cursor_matches(cursor: dict, st: os.stat_result, fh) -> bool:
    """True when `cursor` still describes the open transcript `fh` (stat `st`).

    The offset is trusted only when the file is the same inode, has not
    shrunk, and the bytes just before the offset are the ones the cursor
    sampled when it was saved (a cursor from before the sample only proves
    a newline there) — a truncate-and-rewrite to a longer file would
    otherwise resume mid-record, and one whose new content happens to break
    a line at the old offset would resume past records it never read.
    Identity comes
    from the handle that is about to be scanned, never from a fresh stat of
    the path: a transcript replaced in between would otherwise pair the new
    inode with an offset and state derived from the old one.
    """
    if cursor.get("version") != _CURSOR_VERSION:
        return False
    offset = cursor.get("offset")
    if not isinstance(offset, int) or offset < 0 or offset > st.st_size:
        return False
    if (cursor.get("ino"), cursor.get("dev")) != (st.st_ino, st.st_dev):
        return False
    if offset == 0:
        return True
    try:
        tail = cursor.get("tail")
        if tail is None:
            # A pre-fingerprint cursor: the newline before the offset is the
            # only boundary evidence there is.
            fh.seek(offset - 1)
            return fh.read(1) == b"\n"
        if not isinstance(tail, str):
            return False
        want = bytes.fromhex(tail)
        if not want:
            return False
        fh.seek(offset - len(want))
        return fh.read(len(want)) == want
    except (OSError, ValueError):
        return False


def _load_cursor(cursor_path: str) -> dict | None:
    """The saved cursor as a dict, or None when absent, unreadable or not a dict."""
    try:
        with open(cursor_path, encoding="utf-8") as fh:
            cursor = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(cursor, dict) or not isinstance(cursor.get("state"), dict):
        return None
    return cursor


_CURSOR_TAIL_BYTES = 32


def _save_cursor(
    cursor_path: str, st: os.stat_result, offset: int, state: dict, tail: bytes = b""
) -> None:
    """Atomic write; a failed save costs one full re-scan, never a wrong one.

    `tail` is the sample of bytes ending at `offset` that `_cursor_matches`
    re-reads before trusting the offset.
    """
    try:
        payload = {
            "version": _CURSOR_VERSION,
            "offset": offset,
            "ino": st.st_ino,
            "dev": st.st_dev,
            "tail": tail.hex(),
            "state": state,
        }
        tmp = f"{cursor_path}.{os.getpid()}.tmp"
        os.makedirs(os.path.dirname(cursor_path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, cursor_path)
    except (OSError, TypeError, ValueError):
        pass


def stop_scan_cursor_path(hook: str, session_id) -> str | None:
    """Cache path for `hook`'s scan cursor, or None when the payload carries no
    session — then there is nothing to key the cursor on and the caller does a
    full scan. Session-keyed (`<prefix>-<session_id>.json`) so the cache sweep
    spares the live session's entry and ages the others out.
    """
    if not isinstance(session_id, str) or not session_id:
        return None
    from _paths import resolve_cache_file  # type: ignore[import-not-found]

    return resolve_cache_file(f"stop-scan-{hook}-{session_id}.json", session_id)


def scan_cursor_path(hook: str, session_id, part: str = "root") -> str | None:
    """Cache path for one resumable scan cursor of `hook`, or None when the
    payload carries no session (the caller then scans without persistence).

    `part` names the file the cursor follows when a hook scans more than one
    — the root transcript and each subagent's — or the trigger it scans for
    when two triggers of one hook must not share a cursor. It sits *before*
    the session id in the name (`scan-<hook>-<part>-<session_id>.json`) so
    the cache sweep's boundary match on the session token still holds and
    the live session's cursors survive the sweep (`_paths.prune_stale`).
    """
    if not isinstance(session_id, str) or not session_id:
        return None
    if not isinstance(part, str) or not part:
        return None
    safe_part = re.sub(r"[^A-Za-z0-9_.-]", "_", part)
    from _paths import resolve_cache_file  # type: ignore[import-not-found]

    return resolve_cache_file(f"scan-{hook}-{safe_part}-{session_id}.json", session_id)


def scan_transcript_resumable(
    path: str,
    cursor_path: str | None,
    new_state,
    reduce_event,
    *,
    needle=None,
    max_bytes: int | None = None,
    stop_when=None,
    encode=None,
    decode=None,
):
    """Fold the events of `path` into a reducer state, resuming from the
    offset saved at `cursor_path`, reading at most `max_bytes` new bytes per
    call. Returns `(state, complete)`.

    One scanner for every hook that needs a fact about the whole session and
    used to pay for it either on every call (a 214 MB transcript re-parsed
    from byte 0 on every Stop, issue #1237) or by giving up past a fixed byte
    cap (the 50 MB / 20 MB bounds of issues #1277, #1278, #1280 — past which
    the gate is silent or asks forever, in exactly the long sessions it was
    written for). Both are the same defect: the answer is a small reduction
    over an append-only file, so it is persisted beside a byte offset and
    only the bytes appended since the last call are read.

    `new_state()` builds the empty state; `reduce_event(state, ev)` folds one
    event dict in place. The state crosses a JSON file, so pass
    `encode(state) -> jsonable` / `decode(jsonable) -> state` when it holds
    anything JSON cannot carry. `needle` (bytes, a tuple of bytes, or None)
    rejects a raw line before `json.loads` when it lacks the literal — build
    it with `json_needle`. `stop_when(state)` ends the walk as soon as the
    state has nothing left to learn; a state that satisfies it on resume is
    returned without reading the file body at all.

    `complete` is True when every complete line was folded (EOF, or
    `stop_when` said so) and False when `max_bytes` cut the walk short. In
    the second case the offset saved is the last complete line consumed, so
    the next call continues instead of restarting: a session that once was
    "too large to scan" now catches up over a few calls and then costs only
    its delta. Each read is capped at the budget left, so one oversized line
    is refused before it is allocated; such a line is never consumed and
    the call reports `complete=False` on it (a gate should not page a
    record larger than its whole budget, and the caller's fail-open answer
    is the honest one).

    Only complete lines advance the offset: a record still being written
    when the hook fires is re-read next time. The cursor is trusted only
    while it still describes the file (`_cursor_matches`): same inode, not
    shrunk, and a newline right before the offset.

    Raises `TranscriptReadError` when `path` is missing, not a regular file
    (a FIFO would block `open()` for the whole budget), or fails to read;
    the caller maps that to its own fail-open value. A cursor that cannot be
    saved costs one re-scan next time, never a wrong answer.
    """
    cursor = _load_cursor(cursor_path) if cursor_path else None

    def resumed():
        """The state the cursor carried, decoded; None when there is none."""
        if cursor is None:
            return None
        try:
            return decode(cursor["state"]) if decode else cursor["state"]
        except (KeyError, TypeError, ValueError, AttributeError):
            return None  # a stale shape costs one full re-scan

    try:
        if not Path(path).is_file():
            raise TranscriptReadError(path)
        fh = open(path, "rb")
    except TranscriptReadError:
        raise
    except (OSError, ValueError) as exc:
        raise TranscriptReadError(path) from exc
    with fh:
        try:
            st = os.fstat(fh.fileno())
        except OSError as exc:
            raise TranscriptReadError(path) from exc
        # The offset is only ever read off a cursor that produced a state, so
        # both are bound in one branch: a resumed state without its cursor
        # cannot exist, and the shape says so rather than relying on it.
        offset = 0
        state = None
        if cursor is not None and _cursor_matches(cursor, st, fh):
            state = resumed()
            if state is not None:
                offset = cursor["offset"]
        if state is None:
            state = new_state()
        if stop_when is not None and stop_when(state):
            return state, True  # nothing left to learn; the cursor stands
        complete = True
        consumed = 0
        try:
            fh.seek(offset)
            while True:
                limit = -1 if max_bytes is None else max_bytes - consumed + 1
                raw = fh.readline(limit)
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    if max_bytes is not None and len(raw) >= limit:
                        complete = False  # this line alone is past the budget left
                        break
                    # The trailing record has no newline yet. A JSONL writer
                    # emits the object and its newline in one write, so a
                    # fragment that parses is a complete record whose newline
                    # is all that is missing (or a fixture written without
                    # one): fold it and step over it. One that does not parse
                    # is still being written and is re-read once complete.
                    obj = _parse_line(raw) if _line_has(raw, needle) else None
                    if obj is not None or _parse_line(raw) is not None:
                        offset += len(raw)
                        if obj is not None:
                            reduce_event(state, obj)
                    break
                consumed += len(raw)
                if max_bytes is not None and consumed > max_bytes:
                    complete = False
                    break  # the line that crossed the budget waits for the next call
                offset += len(raw)
                if _line_has(raw, needle):
                    obj = _parse_line(raw)
                    if obj is not None:
                        reduce_event(state, obj)
                        if stop_when is not None and stop_when(state):
                            break
                if max_bytes is not None and consumed >= max_bytes:
                    # Budget spent exactly at a line end: complete only if
                    # nothing follows, so a caller is not told "not caught
                    # up" about a file it has in fact finished.
                    complete = not fh.peek(1)
                    break
        except TranscriptReadError:
            raise
        except OSError as exc:
            raise TranscriptReadError(path) from exc
        tail = b""
        if cursor_path and offset:
            try:
                fh.seek(max(0, offset - _CURSOR_TAIL_BYTES))
                tail = fh.read(offset - max(0, offset - _CURSOR_TAIL_BYTES))
            except OSError:
                cursor_path = None  # cannot fingerprint: do not persist a blind offset
    if cursor_path:
        _save_cursor(cursor_path, st, offset, encode(state) if encode else state, tail)
    return state, complete


def reduce_transcript_resumable(
    path: str,
    cursor_path: str | None,
    new_state,
    reduce_event,
    encode=None,
    decode=None,
):
    """Fold every event of `path` into a reducer state, resuming from the
    offset saved at `cursor_path` when it still describes the file.

    The Stop gates' entry point (issue #1237): `scan_transcript_resumable`
    with no needle, no budget and no early stop, and fail-open — an
    unreadable transcript yields the resumed or empty state, never an
    exception. `cursor_path` None disables persistence (the full-scan path a
    session-less payload takes).
    """
    try:
        state, _complete = scan_transcript_resumable(
            path, cursor_path, new_state, reduce_event, encode=encode, decode=decode
        )
        return state
    except TranscriptReadError:
        cursor = _load_cursor(cursor_path) if cursor_path else None
        if cursor is not None:
            try:
                resumed = decode(cursor["state"]) if decode else cursor["state"]
                if resumed is not None:
                    return resumed  # unreadable: what the last Stop knew
            except (KeyError, TypeError, ValueError, AttributeError):
                pass
        return new_state()
