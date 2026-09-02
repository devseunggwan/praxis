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
SUCCESSFUL PR-surface mutation tool_use (`git push`, `gh pr comment`, `gh pr
review`, a `gh api` call with an explicit write method against a
comments/reviews/threads endpoint, a GraphQL `resolveReviewThread` mutation, or
a write-verb GitHub MCP tool). Failed calls, `--dry-run`/`--help` rehearsals and
echoed command text do not count as mutations.

Blocks by default (`{"decision": "block", ...}` + exit 0), because issue #868
requires it verbatim: "부재 시 차단 ... advisory 티어의 실측 효과가 0 이므로
advisory 로는 같은 실패가 반복된다". `PRAXIS_PR_CLAIM_ADVISORY=1` demotes it to
a `{"systemMessage": ...}` advisory (mirrors `negative-existence-verdict-gate`);
fully fail-open; bypass with `PRAXIS_PR_CLAIM_BYPASS=1`.
"""
from __future__ import annotations

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
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _transcript import (  # type: ignore[import-not-found]  # noqa: E402
    extract_last_assistant_text,
    load_current_turn,
)

_PREFIX = "[pr-claim-mutation-gate]"
_BYPASS_ENV = "PRAXIS_PR_CLAIM_BYPASS"
_ADVISORY_ENV = "PRAXIS_PR_CLAIM_ADVISORY"
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
# The endpoint segment is required, not decorative: a write-method `gh api`
# against `.../labels` or `.../milestones` mutates GitHub but never the PR
# review surface the claim is about, and accepting it would reintroduce the
# same "adjacent call clears the gate" defect this hook exists to close.
_GH_API_WRITE_RE = re.compile(
    r"\bgh\s+api\b[^\n]*?(?:comments|reviews|threads)[^\n]*?"
    r"(?:--method\s+(?:post|patch|put|delete)\b|-X\s*(?:post|patch|put|delete)\b)"
    r"|\bgh\s+api\b[^\n]*?"
    r"(?:--method\s+(?:post|patch|put|delete)\b|-X\s*(?:post|patch|put|delete)\b)"
    r"[^\n]*?(?:comments|reviews|threads)",
    re.IGNORECASE,
)
_GRAPHQL_RESOLVE_RE = re.compile(r"resolveReviewThread", re.IGNORECASE)

# A bare `comment|review|merge|thread` substring also matches consolidated
# READ tools (`get_review_comments`, `pull_request_read`, `list_..._threads`),
# so the name is matched against write verbs instead. Same principle as the
# read-only `gh api` carve-out: listing comments is not resolving them.
_MCP_GH_MUTATION_RE = re.compile(
    r"^(?:add|create|submit|update|edit|delete|reply|resolve|unresolve|dismiss|merge)_"
    r"|_(?:add|create|submit|update|edit|delete|reply|resolve|unresolve|dismiss|merge)_",
    re.IGNORECASE,
)
_MCP_GH_READ_RE = re.compile(r"^(?:get|list|search|read|fetch|view)_", re.IGNORECASE)

# Rehearsal / inspection forms mention the mutation verb without performing it.
_NON_MUTATING_FORM_RE = re.compile(
    r"--dry-run\b|(?<![A-Za-z0-9_-])-n(?![A-Za-z0-9_-])|--help\b"
    r"|(?<![A-Za-z0-9_-])-h(?![A-Za-z0-9_-])|(?<![A-Za-z0-9_])man(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
# `echo "git push"` / a heredoc quoting the command is text, not execution.
_ECHOED_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:echo|printf|cat)\b[^\n|;&]*"
    r"(?:git\s+push|gh\s+pr\s+(?:comment|review)|gh\s+api)",
    re.IGNORECASE,
)


def _is_mutation_command(cmd: str) -> bool:
    if not cmd:
        return False
    if _NON_MUTATING_FORM_RE.search(cmd) or _ECHOED_RE.search(cmd):
        return False
    return bool(
        _PUSH_RE.search(cmd)
        or _GH_PR_COMMENT_RE.search(cmd)
        or _GH_PR_REVIEW_RE.search(cmd)
        or _GH_API_WRITE_RE.search(cmd)
        or _GRAPHQL_RESOLVE_RE.search(cmd)
    )


def _is_mcp_mutation_tool(name: str) -> bool:
    """True for a write-oriented GitHub MCP tool name.

    `name` arrives fully qualified (`mcp__github__add_comment`); the verb test
    runs on the suffix, and an explicit read prefix loses regardless — a
    consolidated reader must never clear a mutation claim.
    """
    suffix = name.split("__")[-1]
    if _MCP_GH_READ_RE.search(suffix):
        return False
    return bool(_MCP_GH_MUTATION_RE.search(suffix))


def has_pr_mutation_in_turn(turn: list[dict]) -> bool:
    """True if the current turn contains a SUCCESSFUL assistant tool_use that
    mutates the PR surface (push, PR comment/review, write-method `gh api` on a
    comments/reviews/threads endpoint, GraphQL thread resolve, or a write-verb
    GitHub MCP tool).

    A failed call is not a mutation: a rejected `git push` or a 422 from
    `gh pr comment` leaves the PR exactly as it was, so it must not clear the
    claim. Correlation is tool_use `id` <-> tool_result `tool_use_id`, mirroring
    pr-report-destination-gate. A tool_use with no result yet (the turn's last
    call) counts as success — biasing to silence, since the gate is the one
    making the accusation.
    """
    candidates: list[str] = []          # tool_use ids that look like mutations
    result_is_error: dict[str, bool] = {}
    saw_untracked_mutation = False      # mutation tool_use carrying no usable id

    for ev in turn:
        msg = ev.get("message", {})
        if not isinstance(msg, dict) or ev.get("isSidechain"):
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        role = msg.get("role")
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "tool_result":
                tid = block.get("tool_use_id")
                if isinstance(tid, str):
                    result_is_error[tid] = block.get("is_error") is True
                continue
            if kind != "tool_use" or role != "assistant":
                continue
            name = block.get("name", "") or ""
            hit = False
            if name == "Bash":
                inp = block.get("input", {})
                cmd = inp.get("command", "") if isinstance(inp, dict) else ""
                hit = _is_mutation_command(cmd)
            elif name.startswith("mcp__github__"):
                hit = _is_mcp_mutation_tool(name)
            if not hit:
                continue
            tid = block.get("id")
            if isinstance(tid, str):
                candidates.append(tid)
            else:
                saw_untracked_mutation = True

    if saw_untracked_mutation:
        return True
    return any(result_is_error.get(tid) is not True for tid in candidates)


def _advisory() -> str:
    return (
        f"{_PREFIX} final message claims a PR review comment / feedback was "
        "processed (처리했/반영했/resolved/applied/handled/fixed the comments) "
        "but the CURRENT TURN contains no SUCCESSFUL PR-surface mutation "
        "(`git push`, `gh pr comment`, `gh pr review`, a write-method `gh api` "
        "call on a comments/reviews/threads endpoint, a GraphQL thread "
        "resolve, or a write-verb GitHub MCP tool). A failed call, a "
        "`--dry-run`/`--help` rehearsal and echoed command text all leave the "
        "PR untouched, so none of them clear this.\n"
        f"{_PREFIX} Rule: the claim's surface is the PR — a local edit does "
        "not back it. Push and post/resolve on the PR BEFORE reporting the "
        "findings as handled (issue #868: 3 CodeRabbit findings were fixed "
        "locally only; push/comment/resolve were all zero).\n"
        f"{_PREFIX} demote to advisory: {_ADVISORY_ENV}=1 | "
        f"bypass: {_BYPASS_ENV}=1\n"
    )


@fail_open
def main() -> int:
    if os.environ.get(_BYPASS_ENV, "").strip():
        return 0

    payload = read_payload()
    if payload is None:
        return 0
    if not isinstance(payload, dict):
        return 0

    if payload.get("stop_hook_active"):
        return 0  # avoid re-entrant loops

    transcript_path = payload.get("transcript_path") or ""
    if not transcript_path or not os.path.isfile(transcript_path):
        return 0

    turn = load_current_turn(transcript_path)
    last_text = extract_last_assistant_text(turn) if turn else ""
    if not last_text:
        return 0

    if not detect_claim(last_text):
        return 0

    if has_pr_mutation_in_turn(turn):
        return 0

    if os.environ.get(_ADVISORY_ENV, "").strip():
        emit_stop_advisory(_advisory())
        decision = _fire_ledger.DECISION_ADVISE
    else:
        emit_stop_block(_advisory())
        decision = _fire_ledger.DECISION_BLOCK
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
