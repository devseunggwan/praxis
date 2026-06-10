#!/usr/bin/env python3
"""PreToolUse(AskUserQuestion) guard: warn on manufactured action-menu surfacing.

When AskUserQuestion is invoked with `options` whose labels match
manufactured-menu markers, check the most recent user message in the
transcript for a command-intent signal. If a command-intent signal is
present, the user has already given direction — re-asking via menu is
redundant manufactured friction.

Two marker forms are recognised:
  - question-form ("진행할까요", "계속할까요", "proceed", "continue",
    "go ahead") — the option re-asks for confirmation.
  - affirmative-form ("그대로 진행", "execute now", "as instructed") — the
    option restates the directive as "do exactly what you said". Its very
    presence proves the agent already knows the answer.

Background:
  2026-05-13 retrospect Strike 1: agent completed an action then automatically
  emitted an AskUserQuestion 4-option menu ("다음 액션 진행할까요?") even when
  the user's immediately prior message was a direct command ("진행", "go ahead",
  "실행"). This pattern fragments decisions, ignores established user intent, and
  adds roundtrip latency on simple continuations.

  2026-05-21 retrospect Gen 3: after an explicit scoped directive the agent
  surfaced clarification menus whose first option was an affirmative restatement
  of the directive ("그대로 진행"). The question-form marker set did not match
  those labels, so the hook passed silently. The affirmative-form marker set
  closes that gap — same anti-pattern, detected from the option-label side.

  The existing `block-ask-end-option` hook catches *termination* menu misuse.
  This hook is its sibling: it catches *continuation* menu misuse — surfacing
  "shall we proceed?" confirmation when the user has already said "yes, proceed".

Default mode: advisory (exit 0 + stderr warning).
Strict mode (PRAXIS_BLOCK_MANUFACTURED_MENU_STRICT=1): block (exit 2).

Allow conditions (no block/advisory emitted):
  1. tool_name != "AskUserQuestion"
  2. No options match any manufactured-menu marker
  3. Most recent user message does NOT contain a command-intent signal
  4. transcript_path is missing or unreadable (graceful degrade — suppress to
     avoid noise when transcript inspection is impossible)
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _transcript import read_last_user_message  # type: ignore[import-not-found]  # noqa: E402

# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

# Manufactured-menu markers in option labels. Case-insensitive.
#
# Two forms, same anti-pattern — a menu surfaced when the user already gave a
# directive:
#   - question-form: the option RE-ASKS for confirmation ("진행할까요?",
#     "proceed?"). Redundant because the user already said "proceed".
#   - affirmative-form: the option restates the directive as "do exactly what
#     you said" ("그대로 진행", "Execute now"). Redundant because the option's
#     very presence proves the agent already knows the answer.
MANUFACTURED_MARKERS_KO = (
    "진행할까요",
    "계속할까요",
    "다음 액션",
    "머지할까요",
    "push할까요",
)
MANUFACTURED_MARKERS_EN = (
    "proceed",
    "continue",
    "go ahead",
)

# Affirmative-form markers — high-precision phrases only. Each must restate
# "do exactly what the user just instructed". They must NOT collide with
# "leave as-is" labels (`그대로 둬`, `keep as-is`) or genuine alternative-path
# labels, so only multi-word phrases anchored on an execute verb are listed.
AFFIRMATIVE_MARKERS_KO = (
    "그대로 진행",
    "그대로 생성",
    "그대로 실행",
    "말씀하신 대로",
    "지시대로",
)
AFFIRMATIVE_MARKERS_EN = (
    "execute now",
    "as instructed",
    "as directed",
    "as requested",
)

# Command-intent signals in the most recent user message. Case-insensitive.
#
# Korean entries are substring-matched (CJK has low collision risk for these
# specific action tokens). English entries are phrase- or token-matched to
# avoid false positives from substrings (e.g. "continuing" in an explanation
# vs "continue" as a command).
COMMAND_SIGNALS_KO = (
    "진행",
    "실행",
    "머지",
    "커밋",
    "push",
    "푸시",
    "계속",
)

# Negation markers following a Korean signal token that turn the directive
# into "do not <action>" — must NOT register as command-intent (issue #515).
# Three shapes covered: prohibitive "...하지 마/말", conditional "...하면 안 ...",
# declarative "...하지 않 ..." / "...안 됩니다/돼/된다".
NEGATION_FOLLOWUP_KO = (
    "하지 마",
    "하지 말",
    "하지마",
    "하지말",
    "하면 안",
    "하면안",
    "하지 않",
    "하지않",
    "안 됩니다",
    "안됩니다",
    "안 돼",
    "안돼",
    "안 된다",
    "안된다",
)

# English negation window: # of chars to scan before an EN command token.
# If any negation marker appears in that window, the match is rejected.
NEGATION_WINDOW_EN = 30
NEGATION_MARKERS_EN = (
    "don't",
    "do not",
    "won't",
    "will not",
    "cannot",
    "can't",
    "should not",
    "shouldn't",
    "must not",
    "mustn't",
    "never",
    " not ",
)
COMMAND_SIGNALS_EN_TOKENS = (
    "go",
    "go ahead",
    "proceed",
    "continue",
    "merge",
    "commit",
    "push",
    # execute-family — the directives that naturally pair with the
    # affirmative-form option markers ("execute now", "as instructed").
    # Korean execute intent is already covered by `실행` in COMMAND_SIGNALS_KO.
    # `run it` is a phrase, not the bare noun `run`: bare `run` whole-word
    # matches non-directive statements ("the run failed", "dry run output").
    "execute",
    "run it",
    "implement",
)

# Destructive-confirmation labels — option labels that name an irreversible
# / shared-state action (merge, push, delete, drop, prod write, force).
# When ANY option label contains one of these tokens, the menu is treated
# as a legitimate confirmation gate even in strict mode: per the project
# CLAUDE.md "Pre-Merge Reporting" + "Executing actions with care" rules,
# destructive actions require explicit per-action confirmation that the
# user's prior command alone does not absorb. Surfacing such a menu is
# justified, not manufactured.
DESTRUCTIVE_LABEL_TOKENS_KO = (
    "머지",
    "푸시",
    "삭제",
    "지우",
    "드롭",
    "초기화",
    "force",
    "프로덕션",
)
# English destructive tokens. `production` intentionally omitted — it
# is lexically ambiguous with `production-ready` / `Product plan`-style
# adjectives. Use the short form `prod` for destructive intent (matches
# `prod deploy`, `prod rollback`).
DESTRUCTIVE_LABEL_TOKENS_EN = (
    "merge",
    "push",
    "delete",
    "drop",
    "truncate",
    "force",
    "prod",
    "destroy",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_markers() -> tuple[str, ...]:
    return (
        MANUFACTURED_MARKERS_KO
        + MANUFACTURED_MARKERS_EN
        + AFFIRMATIVE_MARKERS_KO
        + AFFIRMATIVE_MARKERS_EN
    )


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


def _has_manufactured_marker(labels: list[str]) -> bool:
    """True if any option label carries a manufactured-menu marker.

    Korean markers stay substring-matched (CJK has no ASCII word boundary,
    low collision risk). English markers use ASCII-letter lookaround so
    alternative-path labels are not swept up (`continue` must not match
    `Discontinue support`) while mixed-script labels still match (issue #515).
    """
    if not labels:
        return False
    ko_markers = MANUFACTURED_MARKERS_KO + AFFIRMATIVE_MARKERS_KO
    en_markers = MANUFACTURED_MARKERS_EN + AFFIRMATIVE_MARKERS_EN
    for label in labels:
        lower = label.lower()
        for marker in ko_markers:
            if marker in label or marker.lower() in lower:
                return True
        for marker in en_markers:
            pattern = (
                r"(?<![a-z])" + re.escape(marker.lower()) + r"(?![a-z])"
            )
            if re.search(pattern, lower):
                return True
    return False


def _has_destructive_label(labels: list[str]) -> bool:
    """True if any option label names a destructive / irreversible action.

    Per the project CLAUDE.md rules (Pre-Merge Reporting, "Executing
    actions with care"), destructive actions need explicit per-action
    confirmation that a prior generic command does not absorb. When the
    menu contains such a label, treat it as a legitimate confirmation
    gate even when other markers and a command signal coexist.

    English tokens are matched with ASCII-letter lookaround rather than
    `\\b` word boundaries: this rejects `Product plan` / `production-
    ready` (token followed by another ASCII letter) while still matching
    mixed-script labels like `push할까요` (token followed by a Korean
    character, which `\\b` would not split on because Python's
    Unicode-aware `\\w` treats Korean as a word character).
    """
    if not labels:
        return False
    for label in labels:
        lower = label.lower()
        for token in DESTRUCTIVE_LABEL_TOKENS_KO:
            if token in label or token.lower() in lower:
                return True
        for token in DESTRUCTIVE_LABEL_TOKENS_EN:
            pattern = r"(?<![a-z])" + re.escape(token.lower()) + r"(?![a-z])"
            if re.search(pattern, lower):
                return True
    return False


# Status-query / question phrasings — explicit command intent is absent
# even though an action verb appears as a substring. These exclude the
# message from command-signal detection so legitimate status checks
# ("진행 상황 알려줘", "where should we go from here?") are not treated
# as imperatives.
STATUS_QUERY_PATTERNS_KO = (
    "진행 상황",
    "진행 중",
    "진행 정도",
    "진행률",
    "어디까지",
    "어떻게 진행",
    "상황 알려",
    "상태 확인",
    "상태 알려",
)
STATUS_QUERY_PATTERNS_EN = (
    "where should we",
    "where do we",
    "where to go",
    "from here",
    "how do we",
    "what do we",
    "should we",
)


def _is_status_query(user_message: str) -> bool:
    """True if the message reads as a status query / question rather
    than an imperative command. Filters out 'progress check' /
    'where should we go' style messages that would otherwise false-
    trigger via the substring or word-boundary match on `진행` / `go`.
    """
    if not user_message:
        return False
    # Korean status-query phrases.
    for kw in STATUS_QUERY_PATTERNS_KO:
        if kw in user_message:
            return True
    # English: trailing question mark or interrogative leads.
    lower = user_message.lower().strip()
    if lower.endswith("?"):
        return True
    for kw in STATUS_QUERY_PATTERNS_EN:
        if kw in lower:
            return True
    return False


def _has_command_signal(user_message: str) -> bool:
    """True if the user message contains a command-intent directive.

    Korean tokens: substring match (CJK has low collision risk for these
    specific action verbs). English tokens: whole-word match (prevents
    "continuing" → "continue", "progress" → "go", etc.).

    Status-query / question messages are excluded up-front so that
    `진행 상황 알려줘` does not match `진행` and
    `where should we go from here?` does not match `go`.
    """
    if not user_message:
        return False
    if _is_status_query(user_message):
        return False

    # Korean: substring match, but reject when the token is followed by a
    # negation pattern ("진행하지 마", "계속하지 말아줘", ...).
    for ko in COMMAND_SIGNALS_KO:
        start = 0
        while True:
            idx = user_message.find(ko, start)
            if idx < 0:
                break
            tail = user_message[idx + len(ko): idx + len(ko) + 16]
            if not any(neg in tail for neg in NEGATION_FOLLOWUP_KO):
                return True
            start = idx + len(ko)

    # English: whole-word match (case-insensitive) with negation guard on
    # the preceding window ("don't proceed", "do not continue").
    lower = user_message.lower()
    for token in COMMAND_SIGNALS_EN_TOKENS:
        pattern = r"\b" + re.escape(token.lower()) + r"\b"
        for m in re.finditer(pattern, lower):
            prefix = lower[max(0, m.start() - NEGATION_WINDOW_EN):m.start()]
            if not any(neg in prefix for neg in NEGATION_MARKERS_EN):
                return True

    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

ADVISORY_MSG = """\
[advisory] AskUserQuestion includes a manufactured action-menu option —
question-form ("진행할까요", "proceed", "go ahead") or affirmative-form
("그대로 진행", "execute now", "as instructed") — but the most recent user
message already contains a command-intent signal ("진행", "실행", "go ahead",
"proceed", etc.).

Re-asking "shall we proceed?", or offering "do exactly what you said" as a
menu option, when the user has already given a directive is manufactured
friction — it fragments decisions and adds an unnecessary confirmation
roundtrip.

Remove the manufactured-menu option or replace with a substantive decision
point (multiple real alternatives with trade-offs, or a destructive/irreversible
action requiring explicit confirmation).

Strict mode disabled. Set PRAXIS_BLOCK_MANUFACTURED_MENU_STRICT=1 to block.
"""

BLOCK_MSG = """\
BLOCKED: AskUserQuestion includes a manufactured action-menu option without
a substantive decision point.

Manufactured-menu markers detected in options[].label:
  question-form    Korean:  "진행할까요", "계속할까요", "다음 액션", "머지할까요", "push할까요"
                   English: "proceed", "continue", "go ahead"
  affirmative-form Korean:  "그대로 진행", "그대로 생성", "그대로 실행", "말씀하신 대로", "지시대로"
                   English: "execute now", "as instructed", "as directed", "as requested"

Most recent user message already contains a command-intent signal
("진행", "실행", "go", "proceed", "continue", "merge", "commit", "push",
"execute", "run it", "implement", "머지", "커밋", "푸시").

Why:
  Re-asking confirmation when the user has already given a directive is
  manufactured friction. The user's prior "proceed" / "진행" / "go ahead"
  covers the continuation. Surface only genuine decision points: multiple
  real alternatives with trade-offs, or destructive / irreversible actions
  requiring explicit confirmation.

To opt out: unset PRAXIS_BLOCK_MANUFACTURED_MENU_STRICT (default is advisory).
"""


@fail_open
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
    if not _has_manufactured_marker(labels):
        return 0

    transcript_path = payload.get("transcript_path") or ""
    user_message = read_last_user_message(transcript_path)
    if user_message is None:
        # Fail open per project hook design contract — transcript missing
        # or unreadable, cannot verify command-signal presence.
        return 0
    if not _has_command_signal(user_message):
        # No command-intent in prior message — manufactured menu may be
        # legitimate (first interaction, genuine multiple-choice). Pass.
        return 0

    # Destructive-confirmation exception — even in strict mode, a menu
    # whose options name a destructive / irreversible action (merge, push,
    # delete, prod write, force) is a legitimate confirmation gate and
    # must not be blocked. The user's prior generic command does not
    # absorb per-action approval for shared-state mutations (see project
    # CLAUDE.md "Pre-Merge Reporting" + "Executing actions with care").
    if _has_destructive_label(labels):
        return 0

    # Mode resolution.
    strict_env = os.environ.get("PRAXIS_BLOCK_MANUFACTURED_MENU_STRICT", "")
    strict_set = strict_env not in ("", "0", "false", "False")

    if strict_set:
        sys.stderr.write(BLOCK_MSG)
        return 2

    sys.stderr.write(ADVISORY_MSG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
