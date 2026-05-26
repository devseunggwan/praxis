#!/usr/bin/env python3
"""PreToolUse(AskUserQuestion) guard: warn on mechanical end-option surfacing.

When AskUserQuestion is invoked with `options` whose labels match end-option
markers (e.g. "여기서 종료", "session end", "stop here", "take a break"),
check the most recent user message in the transcript for an explicit stop
signal. If no signal is present, emit a stderr block (strict by default).

Background:
  Skill guides authoring "Step N: chaining" sections frequently include an
  "end here" boilerplate option. Agents mechanically transcribe this into
  AskUserQuestion call sites even when the conversation has a clearly
  chained intent or the user has expressed no desire to stop. This pattern
  has been observed 6+ times in a single session, fragmenting decisions
  and ignoring user intent.

  Indirect phrasing ("take a break", "pause for now", "다른 작업 우선") is
  used as a bypass when direct keywords are detected — this hook catches
  both direct and indirect patterns.

  Text rules in CLAUDE.md or skill bodies alone cannot enforce this — the
  `loaded != retrieved` limit. This hook enforces the rule at the tool
  boundary, where the check runs mechanically regardless of retrieval state.

Default mode: strict (exit 2 + stderr).
Advisory mode (opt-out): PRAXIS_ASK_END_ADVISORY=1 → exit 0 + stderr.

Deprecated: PRAXIS_ASK_END_STRICT=1 is still respected when explicitly set
  but superseded by the new default-strict behavior. If both env vars are
  set, PRAXIS_ASK_END_STRICT=1 forces strict; PRAXIS_ASK_END_ADVISORY=1
  forces advisory. PRAXIS_ASK_END_STRICT takes precedence.

Allow conditions (no block/advisory emitted):
  1. tool_name != "AskUserQuestion"
  2. No options match any end marker
  3. Most recent user message contains an explicit stop signal
  4. transcript_path is missing or unreadable (graceful degrade — block
     is suppressed to avoid noise when transcript inspection is impossible)
"""
from __future__ import annotations

import json
import os
import sys

# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

# End-option markers in option labels. Case-insensitive.
# Korean entries are unicode literals; English entries cover common phrasings.
# Direct markers: explicit end/stop/session-termination language.
#
# Bare KO tokens (종료/그만/마무리) are intentionally NOT used here. Korean
# productively inflects: "종료된 이슈 목록", "회의 마무리 방식 검토",
# "종료 시각 변경" are legitimate triage labels that would substring-match
# bare tokens. The asymmetry with STOP_SIGNALS_KO is intentional —
# stop-signal matching scans free-form prose where inflected forms are
# rare; option labels are exactly where these noun forms cluster.
# Same rationale that excludes bare "보류" (issue #236 review).
#
# To still catch the issue-#236 trigger ("종료 — context"), we list the
# heading-style separator patterns explicitly: "{token} —", "{token} -",
# "{token}:". These require a separator after the token, so they do not
# collide with inflected nouns.
END_OPTION_MARKERS_KO = (
    "여기서 종료",
    "세션 종료",
    "여기서 끝",
    "여기까지",
    # Heading-separator patterns for bare KO end-tokens (issue #236).
    # "{token} —" / "{token} -" / "{token}:" require a separator, so they
    # do not match inflected forms like "종료된" / "마무리 방식".
    "종료 —", "종료 -", "종료:",
    "그만 —", "그만 -", "그만:",
    "마무리 —", "마무리 -", "마무리:",
    # Indirect Korean: pause / break / defer / other-work framing.
    # Bare "보류" intentionally omitted: substring match would false-block
    # legitimate work labels like "보류 중인 이슈 확인" / "보류 상태 검토".
    # The "잠시 보류" phrase below is the session-pause-specific form we
    # want to catch.
    "잠시 멈춰",
    "잠시 보류",
    "휴식",
    "다른 작업 우선",
    "다음 세션",
)
END_OPTION_MARKERS_EN = (
    "end here",
    "session end",
    "stop here",
    "end the session",
    "wrap up here",
    # Indirect English: pause / break / defer / other-work framing
    "take a break",
    "prioritize other work",
    "pause for now",
    "resume in a later session",
    "other work first",
)

# Stop signals in the most recent user message. Case-insensitive.
#
# Korean entries stay substring-matched: CJK lacks ASCII-style word boundaries
# and these specific tokens have low collision risk inside free-form user
# prose (e.g., "그만" / "종료" rarely appear as substrings of unrelated terms
# in the kind of message a user types). The collision risk does NOT extend
# symmetrically to option labels — see END_OPTION_MARKERS_KO comment above.
#
# English entries are phrase-only (no bare-word matching) to prevent the
# "send" → "end" / "backend" → "end" / "don't stop" → "stop" false-allow class
# (codex review #193 F1). A negation prefix check additionally disqualifies
# matches preceded by "don't" / "do not" / "never" / etc. within a small
# preceding window.
STOP_SIGNALS_KO = (
    "종료",
    "여기까지",
    "그만",
    "마무리",
    "스톱",
    "중단",
)
STOP_SIGNALS_EN_PHRASES = (
    "stop here", "stop now", "let's stop", "lets stop",
    "we're done", "we are done", "i'm done", "i am done",
    "end here", "end now", "end this", "end the session",
    "wrap up", "wrap this up",
    "that's all", "that is all",
    "no more",
    "quit now",
    "cancel this",
    "finish here", "finish up",
    "session end",
)
NEGATION_PATTERNS_EN = (
    "don't ", "do not ", "never ", "no ", "not ",
    "won't ", "wouldn't ", "shouldn't ", "can't ", "cannot ",
)
NEGATION_WINDOW = 30  # characters preceding the phrase match

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_markers() -> tuple[str, ...]:
    return END_OPTION_MARKERS_KO + END_OPTION_MARKERS_EN


def _collect_option_labels(tool_input: dict) -> list[str]:
    """Walk questions[].options[].label and return all label strings.

    Tolerant of partial schemas — any missing field results in an empty
    return rather than an exception. Hook must never crash on malformed
    payloads.
    """
    labels: list[str] = []
    questions = tool_input.get("questions") or []
    if not isinstance(questions, list):
        return labels
    for q in questions:
        if not isinstance(q, dict):
            continue
        options = q.get("options") or []
        if not isinstance(options, list):
            continue
        for o in options:
            if not isinstance(o, dict):
                continue
            label = o.get("label")
            if isinstance(label, str):
                labels.append(label)
    return labels


def _has_end_marker(labels: list[str]) -> bool:
    if not labels:
        return False
    markers = _all_markers()
    for label in labels:
        lower = label.lower()
        for marker in markers:
            if marker.lower() in lower:
                return True
    return False


def _read_last_user_message(transcript_path: str) -> str | None:
    """Return the text of the most recent user-authored message in the transcript.

    Returns None when the transcript is missing or unreadable — the caller
    must fail open per the project hook design contract (`Fail-open on
    infrastructure errors`). Returns empty string when the transcript was
    read successfully but no user message contained extractable human
    text — that is a real "no stop-signal" answer and may be acted on.

    Transcript format: JSONL where each line is a JSON object with at
    least `type` ('user' / 'assistant' / 'system') and either `message`
    (an Anthropic API message dict with `role` + `content`) or a flatter
    `content` field. Both shapes are handled.

    tool_result handling (codex review #193 F2): an Anthropic user role
    message may carry only `tool_result` content blocks when the
    assistant invoked tools in the same turn before invoking
    AskUserQuestion. Such entries are NOT human authored — they are the
    runtime's bridge for tool outputs. We must skip them and keep walking
    backward until we find a user entry that contains actual `type: text`
    content. Returning the empty string on the first tool_result-only
    entry causes false-block in strict mode even though the real user
    message earlier in the transcript did signal stop.
    """
    if not transcript_path or not os.path.isfile(transcript_path):
        return None
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None

    # Walk in reverse to find the most recent user-role entry whose
    # content includes human-authored text. tool_result-only entries are
    # skipped (continue), not returned as empty (which would block the
    # search at the wrong layer).
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
        continue

    return ""


def _has_stop_signal(user_message: str) -> bool:
    """True if the user message carries an explicit stop directive.

    Korean signals stay substring-matched (CJK has low collision risk for
    these particular tokens). English signals are phrase-only with a
    negation guard — bare-word matching ("end", "stop", "done") caused
    false-allow on neutral messages like "send" / "backend" / "don't stop"
    (codex review #193 F1).
    """
    if not user_message:
        return False
    lower = user_message.lower()

    # Korean: substring match.
    for ko in STOP_SIGNALS_KO:
        if ko in user_message:
            return True

    # English: phrase match with negation guard.
    for phrase in STOP_SIGNALS_EN_PHRASES:
        phrase_lower = phrase.lower()
        start = 0
        while True:
            idx = lower.find(phrase_lower, start)
            if idx < 0:
                break
            prefix = lower[max(0, idx - NEGATION_WINDOW):idx]
            if not _has_negation(prefix):
                return True
            start = idx + 1

    return False


def _has_negation(prefix: str) -> bool:
    """True if the preceding window contains a negation token.

    Operates on a small window (NEGATION_WINDOW chars) immediately before
    a phrase match. Used to disqualify "don't stop here" / "I do not
    wrap up yet" style messages where a stop phrase appears under
    negation.
    """
    return any(neg in prefix for neg in NEGATION_PATTERNS_EN)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

ADVISORY_MSG = """\
[advisory] AskUserQuestion includes an end-option ("end here" / "take a break" / "여기서 종료" type)
but the most recent user message has no stop signal.

Mechanical surfacing of end-options propagates from skill-guide boilerplate
even when conversation context has a chained intent. Indirect phrasing
("take a break", "pause for now", "다른 작업 우선") is also detected.
Verify that the user actually wants a stop point before this surface —
or remove the end option.

Advisory mode is active (PRAXIS_ASK_END_ADVISORY=1). Remove this env var
to restore the default strict block behavior.
"""

BLOCK_MSG = """\
❌ BLOCKED: AskUserQuestion includes an end-option without user stop signal.

Markers detected in options[].label:
  Direct: "end here" / "session end" / "여기서 종료" type
  Indirect: "take a break" / "pause for now" / "다른 작업 우선" type

Most recent user message contains no stop signal (stop, end, quit, done,
cancel, finish, 종료, 여기까지, 그만, 마무리, etc.).

Why:
  Skill-guide "end here" boilerplate is being mechanically transcribed
  into AskUserQuestion call sites where the conversation has a chained
  intent. Indirect phrasing is used as a bypass when direct keywords are
  detected. Remove the end-option, or wait for the user to express a
  stop signal explicitly.

To opt out: set PRAXIS_ASK_END_ADVISORY=1 (demotes to advisory).
Note: PRAXIS_ASK_END_STRICT=1 (deprecated) also forces strict when set.
"""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if not isinstance(payload, dict):
        return 0
    if payload.get("tool_name") != "AskUserQuestion":
        return 0

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0

    labels = _collect_option_labels(tool_input)
    if not _has_end_marker(labels):
        return 0

    transcript_path = payload.get("transcript_path") or ""
    user_message = _read_last_user_message(transcript_path)
    if user_message is None:
        # Fail open per project hook design contract — transcript missing
        # or unreadable, cannot verify stop-signal absence.
        return 0
    if _has_stop_signal(user_message):
        return 0

    # Mode resolution (precedence: STRICT > ADVISORY > default-strict).
    # PRAXIS_ASK_END_STRICT=1 is deprecated but still honoured when explicitly set.
    strict_env = os.environ.get("PRAXIS_ASK_END_STRICT", "")
    advisory_env = os.environ.get("PRAXIS_ASK_END_ADVISORY", "")
    strict_set = strict_env not in ("", "0", "false", "False")
    advisory_set = advisory_env not in ("", "0", "false", "False")

    # PRAXIS_ASK_END_STRICT takes precedence; otherwise default is strict
    # unless PRAXIS_ASK_END_ADVISORY=1 explicitly opts out.
    if strict_set or not advisory_set:
        sys.stderr.write(BLOCK_MSG)
        return 2

    sys.stderr.write(ADVISORY_MSG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
