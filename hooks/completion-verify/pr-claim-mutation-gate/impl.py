#!/usr/bin/env python3
"""Stop-hook advisory: gate PR-surface completion claims lacking a same-turn mutation.

Background (issue #868, 2026-07-27 retrospect finding #1, repeat 3x). CodeRabbit
raised 3 review findings; the assistant applied the fixes to the LOCAL worktree
only and reported to the user that the findings were handled. At that moment
push / `gh pr comment` / a review-comment API call / thread-resolve were all
ZERO — the user caught it by asking "처리한게 맞는지?". This is not an evidence-
quality problem, it is a completion-CLAIM violation: the claim's surface is the
PR (push, comment, resolved thread), but the evidence backing the claim was a
local edit — a different surface entirely.

`completion-signal-gate` (#392) already fires on generic completion vocabulary,
but it is advisory-only against a 42-fire / 0-behavior-change ledger (2026-07-27
retrospect finding #6) and it does not check whether the claimed surface (PR)
actually saw a mutation — a Read/Bash call anywhere in the turn (e.g. `git
status`) is enough to suppress it, even if the PR itself was never touched.

This hook is narrower and stricter: it fires ONLY when a PR/review-context
SUBJECT (PR, review comment, 피드백, CodeRabbit/BugBot/reviewdog, ...) and a
processed-CLAIM verb (처리했, 반영했, resolved, applied, handled, fixed the
comments, ...) co-occur on the same line, AND the CURRENT TURN contains no
PR-surface mutation tool_use (`git push`, `gh pr comment`, `gh pr review`, a
`gh api` call with an explicit write method against a comments/reviews/threads
endpoint, a GraphQL `resolveReviewThread` mutation, or a GitHub MCP
comment/review/merge tool). Advisory by default (stdout `{"systemMessage":
...}` JSON + exit 0); `PRAXIS_PR_CLAIM_STRICT=1` escalates to `{"decision":
"block", ...}`. Fully fail-open; bypass with `PRAXIS_PR_CLAIM_BYPASS=1`.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
import _fire_ledger  # type: ignore[import-not-found]  # noqa: E402
from _hook_io import (  # type: ignore[import-not-found]  # noqa: E402
    emit_stop_advisory,
    emit_stop_block,
)
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _transcript import (  # type: ignore[import-not-found]  # noqa: E402
    extract_last_assistant_text,
    get_current_turn,
    load_transcript,
)

_PREFIX = "[pr-claim-mutation-gate]"
_BYPASS_ENV = "PRAXIS_PR_CLAIM_BYPASS"
_STRICT_ENV = "PRAXIS_PR_CLAIM_STRICT"
_HOOK_NAME = "pr-claim-mutation-gate"
_ROLE = "completion-verify"

# ---------------------------------------------------------------------------
# Claim detection — a PR/review-context SUBJECT and a processed-state CLAIM on
# the same line (line-localization mirrors merge-state-claim-gate). EN tokens
# use lookarounds, not \b: Python \b puts no boundary between Hangul and ASCII
# word chars, so "\bPR\b" fails on "PR를" (same trap the sibling hooks fixed).
# ---------------------------------------------------------------------------

_SUBJECT_RE = re.compile(
    r"(?<![A-Za-z0-9_])PR(?![A-Za-z0-9_])|\bpull request\b"
    r"|리뷰\s*코멘트|리뷰\s*댓글|코멘트|댓글|피드백|지적\s*사항"
    r"|(?<![A-Za-z0-9_])reviews?(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])comments?(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])feedback(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])findings?(?![A-Za-z0-9_])"
    r"|coderabbit|bugbot|reviewdog|codex\s*review",
    re.IGNORECASE,
)

# Processed-state CLAIM verbs. Past/perfective only — future intent
# ("처리하겠습니다", "I'll fix the comments") must not match.
_CLAIM_RE = re.compile(
    r"처리\s*(했|됐|함|되었|완료)|반영\s*(했|됐|함|되었|완료)"
    r"|적용\s*(했|됐|함|되었|완료)|답변\s*(했|됨)|댓글\s*(달았|남겼)"
    r"|(?<![A-Za-z0-9_])resolved(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])applied(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])handled(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])addressed(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])fixed(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])replied(?![A-Za-z0-9_])",
    re.IGNORECASE,
)

# Negation — same line suppresses the claim (conservative; mirrors
# merge-state-claim-gate's `_NEGATION_RE`). Double negation ("처리하지 않은 게
# 아닙니다") is a KNOWN, ACCEPTED gap: the inner negation token still matches
# and suppresses the line, silencing what is semantically an affirmative
# claim. Reliable double-negation parsing is out of scope for a line-level
# regex gate; documented trade-off, same style as the sibling hooks.
_NEGATION_RE = re.compile(
    r"\bnot\b|n't\b|\bwithout\b|\byet\b|아직|않|못\s|안\s|없|실패|fail",
    re.IGNORECASE,
)

# Hedged / uncertain forms are NOT a completion claim — the assistant is
# disclosing uncertainty, not asserting done. Suppress rather than fire.
_HEDGE_RE = re.compile(
    r"것\s*같|듯\s*합니다|일\s*수도"
    r"|\bmay\s+have\b|\bmight\s+have\b|\bshould\s+have\b|\bprobably\b|\bappears?\s+to\s+have\b",
    re.IGNORECASE,
)

# Question form — "리뷰 코멘트 처리했나요?" is the user's surface, not an
# assistant assertion, and must not fire.
_QUESTION_RE = re.compile(r"[?？]\s*$|했나요|됐나요|했습니까")


def detect_claim(text: str) -> bool:
    """True if `text` asserts a PR-surface processed claim (subject + claim on
    the same line, not negated, not hedged, not a question). Quoted lines
    (`>` — reporting a claim rather than making one) are skipped."""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(">"):
            continue
        if not _SUBJECT_RE.search(line):
            continue
        if not _CLAIM_RE.search(line):
            continue
        if _NEGATION_RE.search(line):
            continue
        if _HEDGE_RE.search(line):
            continue
        if _QUESTION_RE.search(line):
            continue
        return True
    return False


# ---------------------------------------------------------------------------
# Mutation evidence — a PR-surface mutation tool_use in the CURRENT turn
# (turn-scoped, not the sibling's 80-event window: the claim is about what
# THIS turn did, and the motivating incident had zero mutations anywhere).
# A read-only `gh api ... /comments` (no write method) does NOT count —
# listing comments is not resolving them.
# ---------------------------------------------------------------------------

_PUSH_RE = re.compile(r"(?<![A-Za-z0-9_])git\s+push(?![A-Za-z0-9_])")
_GH_PR_COMMENT_RE = re.compile(r"\bgh\s+pr\s+comment\b", re.IGNORECASE)
_GH_PR_REVIEW_RE = re.compile(r"\bgh\s+pr\s+review\b", re.IGNORECASE)
_GH_API_WRITE_RE = re.compile(
    r"\bgh\s+api\b[^\n]*"
    r"(?:--method\s+(?:post|patch|put|delete)\b|-X\s*(?:post|patch|put|delete)\b)",
    re.IGNORECASE,
)
_GRAPHQL_RESOLVE_RE = re.compile(r"resolveReviewThread", re.IGNORECASE)
_MCP_GH_MUTATION_RE = re.compile(r"comment|review|merge|thread", re.IGNORECASE)


def _is_mutation_command(cmd: str) -> bool:
    if not cmd:
        return False
    return bool(
        _PUSH_RE.search(cmd)
        or _GH_PR_COMMENT_RE.search(cmd)
        or _GH_PR_REVIEW_RE.search(cmd)
        or _GH_API_WRITE_RE.search(cmd)
        or _GRAPHQL_RESOLVE_RE.search(cmd)
    )


def has_pr_mutation_in_turn(turn: list[dict]) -> bool:
    """True if the current turn contains an assistant tool_use that mutates
    the PR surface (push, PR comment/review, write-method `gh api`, GraphQL
    thread resolve, or a GitHub MCP comment/review/merge/thread tool)."""
    for ev in turn:
        msg = ev.get("message", {})
        if not isinstance(msg, dict) or msg.get("role") != "assistant" \
                or ev.get("isSidechain"):
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name", "") or ""
            if name == "Bash":
                inp = block.get("input", {})
                cmd = inp.get("command", "") if isinstance(inp, dict) else ""
                if _is_mutation_command(cmd):
                    return True
            elif name.startswith("mcp__github__") and _MCP_GH_MUTATION_RE.search(name):
                return True
    return False


def _advisory() -> str:
    return (
        f"{_PREFIX} final message claims a PR review comment / feedback was "
        "processed (처리했/반영했/resolved/applied/handled/fixed the comments) "
        "but the CURRENT TURN contains no PR-surface mutation (`git push`, "
        "`gh pr comment`, `gh pr review`, a write-method `gh api` call, a "
        "GraphQL thread resolve, or a GitHub MCP comment/review tool).\n"
        f"{_PREFIX} Rule: the claim's surface is the PR — a local edit does "
        "not back it. Push and post/resolve on the PR BEFORE reporting the "
        "findings as handled (issue #868: 3 CodeRabbit findings were fixed "
        "locally only; push/comment/resolve were all zero).\n"
        f"{_PREFIX} bypass: {_BYPASS_ENV}=1\n"
    )


@fail_open
def main() -> int:
    if os.environ.get(_BYPASS_ENV, "").strip():
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0

    if payload.get("stop_hook_active"):
        return 0  # avoid re-entrant loops

    transcript_path = payload.get("transcript_path") or ""
    if not transcript_path or not os.path.isfile(transcript_path):
        return 0

    events = load_transcript(transcript_path)
    if not events:
        return 0

    turn = get_current_turn(events)
    last_text = extract_last_assistant_text(turn) if turn else ""
    if not last_text:
        return 0

    if not detect_claim(last_text):
        return 0

    if has_pr_mutation_in_turn(turn):
        return 0

    if os.environ.get(_STRICT_ENV, "").strip() == "1":
        emit_stop_block(_advisory())
        decision = _fire_ledger.DECISION_BLOCK
    else:
        emit_stop_advisory(_advisory())
        decision = _fire_ledger.DECISION_ADVISE
    # Rich fire record (issue #847): Stop hooks signal block/advise via a stdout
    # decision while exiting 0, so @fail_open's coarse path records only "pass".
    # Record the real decision (one rich record per genuine emit; stop_hook_active
    # already guards re-entrant re-fires), keeping session attribution when present
    # and forgoing it otherwise. suppress_coarse_duplicate() drops the redundant
    # coarse "pass" so aggregate_fires() does not count one emit as fires=2.
    session_id = payload.get("session_id")
    if _fire_ledger.record_session_fire(
        _HOOK_NAME, _ROLE, decision,
        session_id if isinstance(session_id, str) else "", "Stop",
    ):
        # Suppress the coarse fallback ONLY when the rich record actually
        # landed — else a failed rich append would drop the fire from both
        # streams (coderabbit finding on PR #855).
        _fire_ledger.suppress_coarse_duplicate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
