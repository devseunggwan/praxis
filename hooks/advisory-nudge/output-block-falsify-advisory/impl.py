#!/usr/bin/env python3
"""PreToolUse advisory + ask-escalation: output-block falsification gate.

Issue #221 (advisory), #290 (ask escalation), #369 (confidence-anchoring
extension). Recurring failure mode: the "Output-Block-Level Falsification
Gate" rule in CLAUDE.md (4+ memory entries accumulated 2026-05-03 through
2026-05-21) fails to fire at output time because rule retrieval is not
structural — the rule is loaded but not re-triggered at the moment the
proposal block is authored.

This hook adds a structural enforcement point at two surfaces:

  1. AskUserQuestion — escalates the question by tier when the body
     lacks a `Falsified:` line (exact prefix at line start):

     T1. Label contains exact `(Recommended)` or `(추천)` (case-sensitive)
         — original literal-marker path. **Emits `permissionDecision: deny`
         (hard block)** — issue #393 upgrade. Rationale: the explicit
         marker is a high-confidence false-positive-free signal; soft
         `ask` permitted acknowledge-and-proceed and within-session
         recurrence was observed (retrospect 2026-05-23, 3 calls in
         a single codex-review-wrap chain). False-positive rate of
         a literal `(Recommended)` label without `Falsified:` is low
         enough to justify hard block.

     T2. Label OR description contains a confidence-anchoring framing
         token (issue #369). EN tokens (case-insensitive, ASCII word
         boundary): `safer`, `safest`, `clearly`, `obvious choice`,
         `natural fit/choice`, `default to/choice`, `prefer this`,
         bare `recommend(ed|s)?`. KO substrings: `안전한`, `가장 안전`,
         `자연스러운`, `당연히`, `분명히`, `추천`, `기본값`.
         **Emits `permissionDecision: ask` (soft gate)** — kept at ask
         for ergonomics: framing-token detection is broader and carries
         higher false-positive risk than the literal T1 marker.

     When `Falsified:` is present in the question body, both tiers
     silent-pass.

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

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402

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

ANCHORING_ASK_MSG = (
    "옵션 라벨/설명에 confidence-anchoring framing 토큰 (safer/safest/"
    "natural/obvious/clearly/default/prefer/recommend/안전한/자연스러운/"
    "당연히/분명히/추천/기본값) 이 있으나 question body 에 "
    "'Falsified: <disconfirming test 결과>' 가 없음. "
    "CLAUDE.md Output-Block-Level Falsification Gate. 추가 후 재시도."
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

# Confidence-anchoring framing tokens (issue #369). Scanned on BOTH option
# label and description, not just label, because in-vivo evidence showed
# anchoring framing placed in description ("가장 안전한 1회 변경") bypassed
# the literal marker check. Same Falsified: satisfaction as the marker tier.
#
# EN: ASCII lookaround instead of `\b`. Python's `\b` is Unicode-aware
# and would misfire when Korean text sits adjacent to ASCII (no
# boundary between Hangul and ASCII word chars). Mirrors the
# _BULK_PATTERN_EN strategy below.
#
# `recommend(?:ed|s)?` is intentionally bare (no parens) to catch
# label/description usage without literal "(Recommended)" markers.
# When `(Recommended)` IS present in a label, T1 (exact marker) fires
# first and short-circuits before this pattern is consulted.
_ANCHORING_EN_PATTERN = re.compile(
    r"(?<![A-Za-z])"
    r"(?:"
    r"safer|safest|clearly|"
    r"natural\s+(?:fit|choice)|"
    r"obvious\s+choice|"
    r"default\s+(?:to|choice)|"
    r"prefer\s+this|"
    r"recommend(?:ed|s)?"
    r")"
    r"(?![A-Za-z])",
    re.IGNORECASE,
)

# KO substrings. Hangul has no ASCII word-boundary issue — these tokens
# don't appear as substrings of unrelated words in option-label / desc
# context. "가장 안전" is listed separately from bare "안전한" to keep
# the recall for the in-vivo "가장 안전한 1회 변경" phrasing explicit,
# even though "안전한" alone is sufficient — second entry is a no-op
# under substring match but documents the canonical trigger phrase.
_ANCHORING_KO_SUBSTRINGS = (
    "안전한",
    "가장 안전",
    "자연스러운",
    "당연히",
    "분명히",
    "추천",
    "기본값",
)


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
    """True if any option label contains a (Recommended) / (추천) marker.

    Reachable only when T1 (exact marker) and T2 (confidence-anchoring,
    including bare ``recommend``) both miss. Since T2 matches any
    case-insensitive `recommend(ed|s)?` form, an option whose label
    contains `(recommended)` (lowercase) is now caught by T2 (ask)
    before this fallback advisory runs — keep this function for
    behavior continuity and clarity of the original three-tier intent,
    but it is effectively dead under the new precedence.
    """
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


def _has_confidence_anchoring(texts: list[str]) -> bool:
    """True if any text contains a confidence-anchoring framing token.

    Scans both EN regex (ASCII word-boundary lookarounds) and KO
    substring patterns. See module docstring for token sets.
    """
    if not texts:
        return False
    for text in texts:
        if not isinstance(text, str):
            continue
        if _ANCHORING_EN_PATTERN.search(text):
            return True
        for kw in _ANCHORING_KO_SUBSTRINGS:
            if kw in text:
                return True
    return False


def _collect_option_texts(options: list) -> list[str]:
    """Collect option.label + option.description into a single text list.

    Used by T2 (confidence-anchoring) detection only — T1 (exact marker)
    still scans labels only via _has_exact_recommended_marker().
    """
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


def _emit_ask(message: str) -> None:
    """T2 path: soft gate. (decision must be "ask"/"deny" — "allow" is never
    emitted; silent-pass is signaled by no JSON output.)"""
    emit_decision("ask", message)


def _emit_deny(message: str) -> None:
    """T1 path: hard block (issue #393 upgrade)."""
    emit_decision("deny", message)


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

@fail_open
def main() -> int:
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
        questions = tool_input.get("questions") or []
        if not isinstance(questions, list):
            questions = []
        # tier_decision: None | "deny" (T1) | "ask" (T2)
        tier_decision: str | None = None
        msg_to_emit: str = ASK_MSG
        advisory_needed = False
        for q in questions:
            if not isinstance(q, dict):
                continue
            options = q.get("options") or []
            if not isinstance(options, list):
                continue
            q_labels: list[str] = []
            for o in options:
                if isinstance(o, dict):
                    label = o.get("label")
                    if isinstance(label, str):
                        q_labels.append(label)
            # Per-question check: only this question's text counts.
            q_text = q.get("question")
            q_texts = [q_text] if isinstance(q_text, str) else []
            falsified_present = _has_falsified_line(q_texts)

            if _has_exact_recommended_marker(q_labels):
                # T1: literal (Recommended) / (추천) marker in label.
                # Issue #393: upgraded from ask to deny (hard block).
                if not falsified_present:
                    tier_decision = "deny"
                    msg_to_emit = ASK_MSG
                    break  # T1 deny is final — overrides any prior T2 ask
            elif _has_confidence_anchoring(_collect_option_texts(options)):
                # T2 (issue #369): confidence-anchoring framing token in
                # label OR description. Soft gate — false-positive risk
                # higher than T1's literal marker. Record but keep scanning
                # so a later T1 violation can upgrade the decision to deny.
                if not falsified_present and tier_decision is None:
                    tier_decision = "ask"
                    msg_to_emit = ANCHORING_ASK_MSG
            elif _has_recommended_marker(q_labels):
                # T3 (dead under new precedence — kept for clarity).
                advisory_needed = True

        if tier_decision == "deny":
            _emit_deny(msg_to_emit)
        elif tier_decision == "ask":
            _emit_ask(msg_to_emit)
        elif advisory_needed:
            sys.stderr.write(ADVISORY_MSG + "\n")

    elif tool_name == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str) and _is_bulk_action_command(command):
            sys.stderr.write(ADVISORY_MSG + "\n")

    return 0




if __name__ == "__main__":
    sys.exit(main())
