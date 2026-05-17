#!/usr/bin/env python3
"""PreToolUse advisory + ask-escalation: output-block falsification gate.

Issue #221 (advisory), #290 (ask escalation). Recurring failure mode: the
"Output-Block-Level Falsification Gate" rule in CLAUDE.md (4+ memory entries
accumulated 2026-05-03 through 2026-05-13) fails to fire at output time because
rule retrieval is not structural — the rule is loaded but not re-triggered at
the moment the proposal block is authored.

This hook adds a structural enforcement point at two surfaces:

  1. AskUserQuestion — option labels containing "(Recommended)" or "(추천)":
     - Exact case-sensitive match ("(Recommended)" / "(추천)"):
       Escalate to `permissionDecision: ask` if the question body lacks a
       `Falsified:` line (exact prefix at line start). If `Falsified:` IS
       present, silent pass.
     - Case-insensitive match only (e.g. "(recommended)" lowercase):
       Advisory stderr reminder (original behavior).

  2. Bash — bulk-action commands containing patterns like "close all",
     "delete all", "merge all" (+ Korean equivalents). Advisory only.

Fail-open contract (project hook design):
  - Malformed / missing stdin JSON → exit 0
  - Unknown tool_name → exit 0
  - Missing target field (questions / command) → exit 0
  - Any uncaught exception → exit 0
"""
from __future__ import annotations

import json
import re
import sys

# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

ADVISORY_MSG = (
    "[output-block-falsify-advisory] Surfacing a recommendation/bulk-action "
    "proposal? Run the output-block falsification gate first: is the proposal's "
    "premise already addressed by in-flight work, a merged PR, or a parallel "
    "proposal in this session? If yes — STOP and cite the invalidating link "
    "instead of surfacing the proposal."
)

ASK_MSG = (
    "(Recommended) 라벨이 있으나 question body 에 "
    "'Falsified: <disconfirming test 결과>' 가 없음. "
    "CLAUDE.md Self-Falsify Before Recommendation Lock 룰. 추가 후 재시도."
)

# ---------------------------------------------------------------------------
# AskUserQuestion: (Recommended) / (추천) marker detection
# ---------------------------------------------------------------------------

# Exact tokens for ask-escalation (case-sensitive, includes parentheses).
RECOMMENDED_MARKERS_EXACT_EN = ("(Recommended)",)
RECOMMENDED_MARKERS_EXACT_KO = ("(추천)",)

# Substrings for fallback advisory (case-insensitive for English form).
RECOMMENDED_MARKERS_EN = ("(Recommended)",)
RECOMMENDED_MARKERS_KO = ("(추천)",)


def _collect_option_labels(tool_input: dict) -> list[str]:
    """Walk questions[].options[].label and return all label strings.

    Tolerant of partial schemas — any missing field returns an empty list.
    Hook must never crash on malformed payloads.
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


def _collect_question_texts(tool_input: dict) -> list[str]:
    """Collect questions[].question text strings from all question objects."""
    texts: list[str] = []
    questions = tool_input.get("questions") or []
    if not isinstance(questions, list):
        return texts
    for q in questions:
        if not isinstance(q, dict):
            continue
        text = q.get("question")
        if isinstance(text, str):
            texts.append(text)
    return texts


def _has_exact_recommended_marker(labels: list[str]) -> bool:
    """True if any label contains exact (Recommended) or (추천) (case-sensitive)."""
    if not labels:
        return False
    for label in labels:
        for marker in RECOMMENDED_MARKERS_EXACT_EN:
            if marker in label:
                return True
        for marker in RECOMMENDED_MARKERS_EXACT_KO:
            if marker in label:
                return True
    return False


def _has_falsified_line(texts: list[str]) -> bool:
    """True if any question text has a line beginning with 'Falsified:' (exact prefix)."""
    for text in texts:
        for line in text.splitlines():
            if line.startswith("Falsified:"):
                return True
    return False


def _has_recommended_marker(labels: list[str]) -> bool:
    """True if any option label contains a (Recommended) / (추천) marker."""
    if not labels:
        return False
    for label in labels:
        lower = label.lower()
        for marker in RECOMMENDED_MARKERS_EN:
            if marker.lower() in lower:
                return True
        for marker in RECOMMENDED_MARKERS_KO:
            if marker in label:
                return True
    return False


def _emit_ask(message: str) -> None:
    """Output permissionDecision: ask JSON to stdout."""
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": message,
            }
        },
        sys.stdout,
    )
    sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# Bash: bulk-action keyword detection
# ---------------------------------------------------------------------------

# English bulk-action phrases. Matched as case-insensitive substrings after
# a word-boundary check on the verb part. The "all" / "all " suffix is kept
# as a plain substring to avoid over-matching on common non-bulk commands.
#
# Strategy: search for the verb phrase with "all" nearby. Using a regex with
# ASCII lookaround avoids `\b` issues when Korean text appears nearby.
_BULK_VERBS_EN = ("close", "delete", "merge", "reject", "approve")

# ASCII lookaround instead of `\b`: Python's `\b` is Unicode-aware and would
# misfire when Korean text sits adjacent to ASCII (no boundary between Hangul
# and ASCII word chars). Explicit ASCII boundaries prevent false matches like
# `disclose all` / `enclose all` triggering on the `close all` substring.
_BULK_PATTERN_EN = re.compile(
    r"(?<![A-Za-z])(?:" + "|".join(_BULK_VERBS_EN) + r")\s+all(?![A-Za-z])",
    re.IGNORECASE,
)

# Korean bulk-action substrings. Plain substring match is safe because Hangul
# has no ASCII word-boundary issue — these tokens don't appear as substrings
# of unrelated words.
_BULK_SUBSTRINGS_KO = (
    "전부 닫",
    "모두 닫",
    "전부 삭제",
    "모두 삭제",
    "전부 머지",
    "모두 머지",
    "다 머지",
    "전부 클로즈",
    "모두 클로즈",
)

def _is_bulk_action_command(command: str) -> bool:
    """True if the Bash command contains a bulk-action mutation keyword."""
    if not command:
        return False
    # English bulk phrases (close all / delete all / merge all / etc.)
    if _BULK_PATTERN_EN.search(command):
        return True
    # Korean substrings
    for kw in _BULK_SUBSTRINGS_KO:
        if kw in command:
            return True
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _main_inner() -> int:
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
        labels = _collect_option_labels(tool_input)
        if _has_exact_recommended_marker(labels):
            # Ask-escalation path: check for Falsified: evidence in question body.
            texts = _collect_question_texts(tool_input)
            if not _has_falsified_line(texts):
                _emit_ask(ASK_MSG)
        elif _has_recommended_marker(labels):
            # Fallback advisory: case-insensitive match (e.g. lowercase "(recommended)").
            sys.stderr.write(ADVISORY_MSG + "\n")

    elif tool_name == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str) and _is_bulk_action_command(command):
            sys.stderr.write(ADVISORY_MSG + "\n")

    return 0


def main() -> int:
    """Advisory hook — must NEVER break tool execution. Any uncaught exception
    in the inner logic is swallowed and the hook fails open (exit 0)."""
    try:
        return _main_inner()
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
