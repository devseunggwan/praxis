#!/usr/bin/env python3
"""PreToolUse(AskUserQuestion) nudge: merge-decision menu missing review options.

A merge-decision AskUserQuestion menu (an option whose label names a merge /
squash action) is the last gate before an irreversible shared-state mutation.
At that gate the user often wants a *quality* lever — re-run codex-review-wrap,
re-run code-reviewer, or open a critic debate — before committing to the merge.
But the option set is authored ad-hoc by the agent each turn, so those levers
are routinely absent and the user must request them manually.

A PreToolUse hook cannot inject options into the rendered menu (it can only
allow / block / advise). So this hook nudges: when a merge-decision menu carries
NO review/debate option, it emits an advisory asking the agent to re-author the
menu with those options before surfacing it.

This is the sibling of `block-manufactured-action-menu` (which catches a merge
menu that is *redundant* — surfaced after the user already said "merge"). This
hook catches a merge menu that is *incomplete* — missing the review/debate
levers a pre-merge decision should offer.

Default mode: advisory (exit 0 + stderr nudge).
Strict mode (PRAXIS_MERGE_MENU_REVIEW_STRICT=1): block (exit 2).

Allow conditions (no nudge/block emitted):
  1. tool_name != "AskUserQuestion"
  2. No option label names a merge-decision action
  3. A review/debate option is already present (the menu already offers the lever)
  4. Malformed / partial payload (fail-open per project hook design contract)
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402

# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

# Merge-decision trigger tokens in an option label. These identify the menu as
# a go/no-go gate on a merge. Precision matters here (we do not want to nudge on
# unrelated menus), so English tokens use ASCII-letter lookaround: `merge`
# matches but `merged` / `merger` do not (followed by an ASCII letter), while
# the mixed-script label `squash 머지` still matches via the Korean token.
MERGE_TOKENS_KO = (
    "머지",
)
MERGE_TOKENS_EN = (
    "merge",
    "squash",
)

# Review / debate option tokens. Presence of any of these in a label means the
# menu already offers a quality lever — suppress the nudge. Detection uses
# SUBSTRING matching (not lookaround) on purpose: a false positive here only
# *suppresses* the advisory, which is the safe direction (never nag when a
# review option might exist). So `reviewer` inside `code-reviewer` correctly
# counts, as does `review` inside `codex-review-wrap`.
REVIEW_TOKENS_KO = (
    "리뷰",
    "검토",
)
REVIEW_TOKENS_EN = (
    "codex",
    "review",
    "reviewer",
    "critic",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_option_labels(tool_input: dict) -> list[str]:
    """Walk questions[].options[].label and return all label strings.

    Tolerant of partial schemas — any missing field yields an empty list
    rather than an exception. The hook must never crash on a malformed payload.
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


def _en_token_present(token: str, lower_label: str) -> bool:
    """ASCII-letter lookaround match: token not flanked by ASCII letters.

    Rejects `merged` / `merger` for token `merge` while matching mixed-script
    labels like `squash 머지` (token followed by a Korean char, which `\\b`
    would not split because Python's Unicode-aware `\\w` treats Hangul as a
    word character).
    """
    pattern = r"(?<![a-z])" + re.escape(token.lower()) + r"(?![a-z])"
    return re.search(pattern, lower_label) is not None


def _has_merge_decision_option(labels: list[str]) -> bool:
    """True if any option label names a merge-decision action.

    Korean tokens use substring match (CJK has no ASCII word boundary, low
    collision risk). English tokens use ASCII-letter lookaround for precision.
    """
    for label in labels:
        lower = label.lower()
        for token in MERGE_TOKENS_KO:
            if token in label:
                return True
        for token in MERGE_TOKENS_EN:
            if _en_token_present(token, lower):
                return True
    return False


def _has_review_option(labels: list[str]) -> bool:
    """True if any option label already offers a review / debate lever.

    Uses substring matching for both KO and EN: a false positive only
    suppresses the advisory (safe direction — never nag when a review option
    may exist), so the broader match is intentional.
    """
    for label in labels:
        lower = label.lower()
        for token in REVIEW_TOKENS_KO:
            if token in label:
                return True
        for token in REVIEW_TOKENS_EN:
            if token in lower:
                return True
    return False


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

ADVISORY_MSG = """\
[advisory] This AskUserQuestion is a merge-decision menu (an option names a
merge / squash action) but offers NO review / debate option.

A pre-merge gate is the last point before an irreversible shared-state
mutation. Before surfacing merge vs hold, consider re-authoring the menu to
also offer the quality levers the user may want at this gate:
  - re-run codex-review-wrap (independent Codex pass)
  - re-run oh-my-claudecode:code-reviewer
  - open a critic debate (oh-my-claudecode:critic)

A PreToolUse hook cannot inject options into the rendered menu — re-issue the
AskUserQuestion with these options added if a fresh review pass is appropriate.

Strict mode disabled. Set PRAXIS_MERGE_MENU_REVIEW_STRICT=1 to block.
"""

BLOCK_MSG = """\
BLOCKED: merge-decision AskUserQuestion menu offers no review / debate option.

A merge / squash option is present, but none of the options offers a quality
lever (codex-review-wrap, code-reviewer, critic). A pre-merge gate is the last
point before an irreversible shared-state mutation — surface the review/debate
levers as menu options so the user can choose a fresh review before merging.

Re-issue the AskUserQuestion with options for:
  - re-run codex-review-wrap
  - re-run oh-my-claudecode:code-reviewer
  - open a critic debate (oh-my-claudecode:critic)

To opt out: unset PRAXIS_MERGE_MENU_REVIEW_STRICT (default is advisory).
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


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
    if not labels:
        return 0

    # Trigger only on a merge-decision menu.
    if not _has_merge_decision_option(labels):
        return 0

    # If a review/debate lever is already offered, the menu is complete — pass.
    if _has_review_option(labels):
        return 0

    # Strict only on the documented `=1` value (spec mode table + ADVISORY_MSG
    # both contract `PRAXIS_MERGE_MENU_REVIEW_STRICT=1`). `.strip() == "1"`
    # matches the dominant codebase convention (destructive-bash-guard,
    # protected-paths-guard, push-remote-ref-verify, path-probe-gate) and avoids
    # the broader `not in (...)` form that would treat a disable-intent value
    # like `=no` as strict — contradicting the documented contract.
    strict_set = os.environ.get("PRAXIS_MERGE_MENU_REVIEW_STRICT", "").strip() == "1"

    if strict_set:
        sys.stderr.write(BLOCK_MSG)
        return 2

    sys.stderr.write(ADVISORY_MSG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
