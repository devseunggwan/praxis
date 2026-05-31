#!/usr/bin/env python3
"""PreToolUse advisory: pre-output falsification gate (issue #487).

Two independent advisory lanes, both **advisory-only** (exit 0 + stderr,
never block, fail-open on any infrastructure error):

  Lane A — AskUserQuestion evaluative-option gate
  ----------------------------------------------
  Fires when an option label/description carries an evaluative marker
  (A1: `Recommended`, `natural`, `safer`, `권장`, `안전한`, `더 안전`) AND
  recent transcript context shows negative evidence (A2: `fail`, `timeout`,
  `0 row`, `blank`, `error`, `not found`, `verification failing`) AND the
  question body does NOT already contain a falsification/disconfirming
  phrase (A3-alt).

  A3 INFEASIBILITY NOTE: the issue's original A3 leg asked the hook to
  detect a disconfirming-probe statement in the *in-flight assistant turn*
  before the (Recommended) option is surfaced. A PreToolUse hook cannot see
  the assistant turn being authored — only the tool_input being submitted.
  We substitute: scan the AskUserQuestion `questions[].question` body and
  the option descriptions for any falsification phrase. The presence of
  such a phrase in the question body suppresses the advisory; its absence
  is the surfaced-without-probe signal. This is a question-body proxy for
  the unreachable in-flight-turn check.

  Lane B — repeated read-only status command gate (B-i)
  ----------------------------------------------------
  Fires when an identical read-only `(binary, subcommand)` pair (subcommand
  in {status, get, list}) repeats >= REPEAT_THRESHOLD times within a recency
  window (_REPEAT_WINDOW_SECONDS); the per-key window resets after the advisory
  fires so routine repeats spread across a long session do not nag. Counts
  COMMANDS, not outputs.

  B-i OUTPUT-IDENTITY LIMITATION: the issue's broader Lane B would also
  detect identical command *outputs* (a PostToolUse output-hash leg). This
  hook implements B-i only — command repetition. It cannot tell a status
  check that returns changing output (legitimate polling of an evolving
  state) from one that returns the same output every time (the actual
  depth-probe-needed signal). It nudges purely on command-string identity.

Opt-out:
  PRAXIS_FALSIFY_GATE_BYPASS=1 — disable both lanes for the session.

Fail-open contract (project hook design):
  - Malformed / missing stdin JSON -> exit 0
  - Unknown tool_name -> exit 0
  - Missing target field (questions / command) -> exit 0
  - Missing / unreadable transcript (Lane A) -> exit 0 (no advisory)
  - Any uncaught exception -> exit 0
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_utils import (  # type: ignore[import-not-found]  # noqa: E402
    iter_command_starts,
    safe_tokenize,
    strip_prefix,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BYPASS_ENV = "PRAXIS_FALSIFY_GATE_BYPASS"

# --- Lane A1: evaluative markers ---
# EN tokens use ASCII lookaround instead of `\b`. Python's `\b` is
# Unicode-aware and misfires when Korean text sits adjacent to ASCII (no
# boundary between Hangul and ASCII word chars). Mirrors the lookaround-vs-
# substring split documented in output-block-falsify-advisory/impl.py.
_EVAL_EN_PATTERN = re.compile(
    r"(?<![A-Za-z])(?:recommended|natural|safer)(?![A-Za-z])",
    re.IGNORECASE,
)
# KO substrings — Hangul has no ASCII word-boundary issue; these tokens do
# not appear as substrings of unrelated words in option-label/desc context.
_EVAL_KO_SUBSTRINGS = ("권장", "안전한", "더 안전")

# --- Lane A2: negative-evidence signals (substring, case-insensitive EN) ---
_NEGATIVE_SIGNALS = (
    "fail",
    "timeout",
    "0 row",
    "blank",
    "error",
    "not found",
    "verification failing",
)

# --- Lane A3-alt: falsification / disconfirming phrases ---
_FALSIFY_REGEXES = (
    re.compile(r"if .* wrong", re.IGNORECASE),
    re.compile(r"if .* incorrect", re.IGNORECASE),
)
_FALSIFY_SUBSTRINGS = (
    "invalidating link",
    "disconfirm",
    "반증",
    "missing observation",
    "이게 틀렸을 때",
)

ADVISORY_A = (
    "[falsification-gate] (Recommended) option surfaced under negative "
    "evidence. Pre-output disconfirming probe statement is missing. "
    "Bypass: PRAXIS_FALSIFY_GATE_BYPASS=1"
)

# --- Lane B (B-i): read-only repeated-command gate ---
READONLY_SUBCOMMANDS = {"status", "get", "list"}
REPEAT_THRESHOLD = 3

# Sliding recency window: only repeats within this many seconds count toward
# the threshold, so a status command run a few times across a long session does
# not accumulate into a false nag. After the advisory fires, the per-key window
# is reset (codex review #487: monotonic counter nagged on legitimate repeats).
_REPEAT_WINDOW_SECONDS = 300

_STATE_BASE = os.path.join(
    os.environ.get("TMPDIR", "/tmp"), "praxis-pre-output-falsification-gate"
)

# How many trailing JSONL lines of the transcript Lane A scans.
_TRANSCRIPT_SCAN_LINES = 400


# ---------------------------------------------------------------------------
# Lane A — AskUserQuestion evaluative-option gate
# ---------------------------------------------------------------------------

def _collect_option_texts(options: list) -> list[str]:
    """Collect option.label + option.description into a single text list."""
    texts: list[str] = []
    for o in options:
        if not isinstance(o, dict):
            continue
        label = o.get("label")
        if isinstance(label, str):
            texts.append(label)
        desc = o.get("description")
        if isinstance(desc, str):
            texts.append(desc)
    return texts


def _has_evaluative_marker(texts: list[str]) -> bool:
    """A1: any text contains an evaluative marker (EN regex or KO substring)."""
    for text in texts:
        if not isinstance(text, str):
            continue
        if _EVAL_EN_PATTERN.search(text):
            return True
        for kw in _EVAL_KO_SUBSTRINGS:
            if kw in text:
                return True
    return False


def _has_falsify_phrase(texts: list[str]) -> bool:
    """A3-alt: any text contains a falsification/disconfirming phrase."""
    for text in texts:
        if not isinstance(text, str):
            continue
        lower = text.lower()
        for pat in _FALSIFY_REGEXES:
            if pat.search(text):
                return True
        for sub in _FALSIFY_SUBSTRINGS:
            # EN substrings are lowercased; KO substrings match as-is.
            if sub.lower() in lower or sub in text:
                return True
    return False


def _has_negative_signal(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    return any(sig in lower for sig in _NEGATIVE_SIGNALS)


def _recent_transcript_negative(transcript_path: str) -> bool:
    """A2: scan the recent transcript for negative-evidence substrings in
    tool_result content blocks AND user messages.

    Fail-open: returns False when transcript is missing/unreadable, so the
    advisory never fires on infrastructure failure.
    """
    if not transcript_path or not os.path.isfile(transcript_path):
        return False
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return False

    for raw in lines[-_TRANSCRIPT_SCAN_LINES:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            if _has_negative_signal(content):
                return True
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                if _has_negative_signal(block.get("text") or ""):
                    return True
            elif btype == "tool_result":
                # tool_result content can be a string or a list of blocks.
                tr = block.get("content")
                if isinstance(tr, str):
                    if _has_negative_signal(tr):
                        return True
                elif isinstance(tr, list):
                    for sub in tr:
                        if isinstance(sub, dict) and _has_negative_signal(
                            sub.get("text") or ""
                        ):
                            return True
    return False


def _lane_a(tool_input: dict, transcript_path: str) -> bool:
    """Return True if the Lane A advisory should fire (A1 AND A2 AND NOT A3)."""
    questions = tool_input.get("questions")
    if not isinstance(questions, list):
        return False

    # Collect this call's option texts + question bodies.
    option_texts: list[str] = []
    body_texts: list[str] = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        options = q.get("options")
        if isinstance(options, list):
            option_texts.extend(_collect_option_texts(options))
        q_body = q.get("question")
        if isinstance(q_body, str):
            body_texts.append(q_body)

    # A1: evaluative marker in any option label/description.
    if not _has_evaluative_marker(option_texts):
        return False

    # A3-alt: disconfirming phrase in the question body OR option descriptions
    # suppresses the advisory (the probe is already present).
    if _has_falsify_phrase(body_texts + option_texts):
        return False

    # A2: recent transcript negative evidence.
    return _recent_transcript_negative(transcript_path)


# ---------------------------------------------------------------------------
# Lane B (B-i) — repeated read-only status command gate
# ---------------------------------------------------------------------------

def _state_dir(session_id: str) -> str:
    sid_hash = hashlib.sha1(session_id.encode()).hexdigest()[:16]
    return os.path.join(_STATE_BASE, sid_hash)


def _counter_file(session_id: str, key: str) -> str:
    key_hash = hashlib.sha1(key.encode()).hexdigest()[:16]
    return os.path.join(_state_dir(session_id), key_hash)


def _record_and_count(session_id: str, key: str) -> int:
    """Append the current timestamp to the per-(session, key) window file, prune
    entries older than the recency window, and return the count of timestamps
    remaining within the window.

    Storing timestamps (not a monotonic integer) is what makes the gate decay:
    a status command run a handful of times across a long session never crosses
    the threshold because old stamps fall outside the window.

    Returns 0 on any OSError (fail-open — the advisory simply never fires).
    """
    sd = _state_dir(session_id)
    try:
        os.makedirs(sd, exist_ok=True)
        path = _counter_file(session_id, key)
        now = time.time()
        stamps: list[float] = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    stamps = [
                        float(x)
                        for x in json.loads(fh.read() or "[]")
                        if isinstance(x, (int, float))
                    ]
            except (OSError, ValueError, json.JSONDecodeError):
                stamps = []
        cutoff = now - _REPEAT_WINDOW_SECONDS
        stamps = [t for t in stamps if t >= cutoff]
        stamps.append(now)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(stamps))
        return len(stamps)
    except OSError:
        return 0


def _reset_counter(session_id: str, key: str) -> None:
    """Clear the window file for a key (best-effort) after the advisory fires,
    so routine subsequent repeats do not nag on every following call."""
    try:
        os.remove(_counter_file(session_id, key))
    except OSError:
        pass


def _readonly_pairs(command: str) -> list[str]:
    """Return distinct '<binary> <subcommand>' keys for read-only subcommands
    found across the command's segments.

    A pair qualifies when, after prefix-stripping, the second token is a
    bare read-only subcommand (status / get / list).
    """
    tokens = safe_tokenize(command)
    if not tokens:
        return []
    pairs: list[str] = []
    seen: set[str] = set()
    for raw_argv in iter_command_starts(tokens):
        argv = strip_prefix(raw_argv)
        if len(argv) < 2:
            continue
        binary = argv[0].rsplit("/", 1)[-1]
        subcommand = argv[1]
        if subcommand in READONLY_SUBCOMMANDS:
            key = f"{binary} {subcommand}"
            if key not in seen:
                seen.add(key)
                pairs.append(key)
    return pairs


def _lane_b(tool_input: dict, session_id: str) -> list[str]:
    """Return a list of advisory message lines (one per pair that crossed the
    threshold this call). Empty list = no advisory."""
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return []
    messages: list[str] = []
    for key in _readonly_pairs(command):
        count = _record_and_count(session_id, key)
        if count >= REPEAT_THRESHOLD:
            messages.append(
                f"[probe-gate] Same status check repeated {count} times in the "
                f"last {_REPEAT_WINDOW_SECONDS}s. Try a depth probe (logs, "
                "describe, alternative path)."
            )
            _reset_counter(session_id, key)
    return messages


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

@fail_open
def main() -> int:
    if os.environ.get(BYPASS_ENV, "").strip() == "1":
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # fail-open on malformed stdin

    if not isinstance(payload, dict):
        return 0

    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    if tool_name == "AskUserQuestion":
        transcript_path = payload.get("transcript_path") or ""
        if _lane_a(tool_input, transcript_path):
            sys.stderr.write(ADVISORY_A + "\n")
    elif tool_name == "Bash":
        session_id = payload.get("session_id") or "unknown"
        for msg in _lane_b(tool_input, session_id):
            sys.stderr.write(msg + "\n")

    return 0




if __name__ == "__main__":
    sys.exit(main())
