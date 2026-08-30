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

import os
import re
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _transcript import read_last_user_message  # type: ignore[import-not-found]  # noqa: E402

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
    "여기서 끝",
    "여기까지",
    # Heading-separator patterns for bare KO end-tokens (issue #236).
    # "{token} —" / "{token} -" / "{token}:" require a separator, so they
    # do not match inflected forms like "종료된" / "마무리 방식".
    "종료 —", "종료 -", "종료:",
    "그만 —", "그만 -", "그만:",
    "마무리 —", "마무리 -", "마무리:",
    # "세션 종료" demoted to the same heading-separator condition (issue
    # #922). "세션" is an everyday noun for process/workspace/agent
    # sessions in this tool environment, so bare "세션 종료" clusters in
    # legitimate work labels ("중복 MCP만, 부모 세션 종료 후",
    # "21873 세션 종료 여부를 먼저 확인") — same false-positive risk the
    # bare-token exception above already guards against.
    "세션 종료 —", "세션 종료 -", "세션 종료:",
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

# Phrases that read as stop signals in isolation but are routinely action
# directives when followed by an action verb ("I'm done with the analysis,
# proceed ..."): a stop match here is disqualified when an action verb follows
# within ACTION_FOLLOWUP_WINDOW chars (issue #515). Termination-specific
# phrases ("stop here", "that's all", "quit now") are deliberately excluded —
# they stay unconditional stop signals.
AMBIGUOUS_STOP_PHRASES_EN = (
    "wrap up", "wrap this up",
    "finish up",
    "no more",
    "we're done", "we are done", "i'm done", "i am done",
)
# Action verbs that, after an ambiguous stop phrase, mark the message as a
# directive to keep working. Single words matched whole-word ("run the tests"
# counts, "running" does not); multi-word entries matched as phrases.
ACTION_VERBS_EN = (
    "proceed", "continue", "implement", "deploy", "merge", "push",
    "run", "execute", "review", "test", "build", "create", "start",
    "move on", "go ahead", "commit", "ship", "release", "fix", "add",
    "update", "write", "refactor", "investigate", "analyze", "check",
)
# Characters after an ambiguous phrase to scan for an action verb.
ACTION_FOLLOWUP_WINDOW = 80

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
        ambiguous = phrase_lower in AMBIGUOUS_STOP_PHRASES_EN
        start = 0
        while True:
            idx = lower.find(phrase_lower, start)
            if idx < 0:
                break
            prefix = lower[max(0, idx - NEGATION_WINDOW):idx]
            if not _has_negation(prefix):
                # Ambiguous phrases are NOT a stop signal when an action verb
                # follows (the user is directing further work, not stopping).
                if ambiguous:
                    suffix = lower[idx + len(phrase_lower):
                                   idx + len(phrase_lower) + ACTION_FOLLOWUP_WINDOW]
                    if not _has_action_verb(suffix):
                        return True
                else:
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


def _has_action_verb(suffix: str) -> bool:
    """True if the window after an ambiguous stop phrase contains an action verb.

    Single-word verbs use ASCII-lookaround whole-word matching (mirroring the
    suite's boundary strategy) so "run the tests" counts but "running" does
    not; multi-word phrases are matched as substrings.
    """
    for verb in ACTION_VERBS_EN:
        if " " in verb:
            if verb in suffix:
                return True
        else:
            if re.search(r"(?<![a-z])" + re.escape(verb) + r"(?![a-z])", suffix):
                return True
    return False


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


@fail_open
def main() -> int:
    payload = read_payload()
    if payload is None:
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
    user_message = read_last_user_message(transcript_path)
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
