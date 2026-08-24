#!/usr/bin/env python3
"""Stop hook: `gh pr create` succeeded this session with no verification anchor posted.

Issue #1113. Origin — a session created 4 PRs back-to-back (#1083-#1086) and
left every one without a Post-PR Empirical Verification anchor comment; no
layer fired, and the user had to ask for it 25-28 minutes later. The existing
anchor-comment-gate (#947/#996) only judges an anchor once it is ABOUT to be
posted — shape, SHA freshness, per-row evidence. Of the 16 hooks that touch
`gh pr create` at all, none require that an anchor exist in the first place.
This hook closes exactly that gap: existence, not quality.

## Design

Whole-transcript scan (mirrors pr-report-destination-gate, #832): the PR
create and the anchor post are routinely many turns apart, so a bounded tail
would miss the create. Every event is reduced to (a) a non-draft `gh pr
create` success and the PR number it minted, or (b) a successful PR-comment
post and the PR number it targeted — the raw tool_use block is dropped
immediately (same rationale as #1076).

  created PRs (non-draft) := successful `gh pr create` without `--draft`/`-d`,
    PR number read from the tool_result URL.
  posted PRs := successful `gh pr comment <N>`, or a write-method `gh api`
    call against `issues/<N>/comments` or `pulls/<N>/comments`.

  Fires when createdPRs - postedPRs != {}.

## Escalation (issue #1113's explicit design choice)

No sibling gate does this — it is a deliberate deviation, not a missed
convention. A hard block on the very first Stop after `gh pr create` would
punish a legitimate reason for a between-turn gap (the create just happened;
the agent is still mid-turn on something else). The issue asks for one nudge
before the block: the FIRST Stop this session where the condition holds emits
an advisory; the SECOND and every later Stop where it still holds emits a
block. `count_session_fires` (issue #805) is reused to read "have I already
advised this session", turning a sibling suppression primitive into an
escalation one.

## Correctness guards (mirrors pr-report-destination-gate)

  - `gh pr create` draft detection reads only the flags within THAT
    invocation's segment of a compound command, not the whole command string
    (`gh pr create --draft && gh pr comment 9 -b hi` must not let `--draft`
    leak into the comment call's segment, and a second, non-draft `gh pr
    create` later in the same compound command must not inherit it either).
  - A failed `gh pr create` / `gh pr comment` (tool_result `is_error`) does
    not count, matched by tool_use `id` <-> tool_result `tool_use_id`.
  - `gh pr create` output is expected to carry exactly ONE PR URL (a create
    success); multiple or zero URLs leave the create unresolved (skipped, not
    guessed).
  - `gh api` POST-ness reuses pr-report-destination-gate's explicit
    --method/-X-wins rule: `--method GET … -f page=1` is a documented GET.

## Fail-open contract
  - Malformed / missing stdin JSON -> exit 0
  - Missing / unreadable / empty transcript -> exit 0
  - stop_hook_active=true -> exit 0 (re-entrancy guard)
  - Any uncaught exception -> exit 0 (@fail_open)
"""
from __future__ import annotations

import os
import json
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
from _transcript import iter_transcript  # type: ignore[import-not-found]  # noqa: E402

_HOOK_NAME = "pr-anchor-existence-gate"
_ROLE = "completion-verify"
_PREFIX = "[pr-anchor-existence-gate]"
_BYPASS_ENV = "PRAXIS_PR_ANCHOR_BYPASS"
_ADVISORY_ENV = "PRAXIS_PR_ANCHOR_ADVISORY"

# A `gh pr create` invocation's own segment of a (possibly compound) command —
# up to the next `&&`/`;`/`|` or end of string — so a draft flag on one call
# never leaks into a sibling call in the same compound command.
_PR_CREATE_SEGMENT_RE = re.compile(r"gh\s+pr\s+create\b([^\n]*?)(?=&&|;|\||$)", re.IGNORECASE)
_DRAFT_FLAG_RE = re.compile(r"(?:^|\s)(?:--draft|-d)(?:\s|$)", re.IGNORECASE)

_PR_URL_RE = re.compile(r"github\.com/[\w.-]+/[\w.-]+/pull/(\d+)", re.IGNORECASE)

# Posted-PR detection — same shape as pr-report-destination-gate's `_pr_nums`:
# the target must be the token directly after the subcommand (a flag before
# the positional, `gh pr comment -b "…" 123`, is an accepted advisory-miss —
# scanning the whole command would false-match a number inside a flag value).
_PR_COMMENT_RE = re.compile(r"gh\s+pr\s+comment\s+(?:\S*?/pull/(\d+)|(\d+))", re.IGNORECASE)

_API_METHOD_RE = re.compile(r"(?:--method|-X)\s+([A-Za-z]+)", re.IGNORECASE)
_API_BODY_RE = re.compile(r"(^|\s)(-f|-F|--field|--raw-field|--input)\b", re.IGNORECASE)
_API_TARGET_RE = re.compile(r"gh\s+api\s+[^\n]*?(?:issues|pulls)/(\d+)/comments\b", re.IGNORECASE)
# A `gh api` invocation's own segment — same segmentation technique as
# `_PR_CREATE_SEGMENT_RE`, applied here so a compound command mixing methods
# and targets (`gh api GET .../issues/178/comments && gh api --method POST
# .../issues/179/comments`) pairs each method with ITS OWN target instead of
# a whole-command scan letting one invocation's POST clear another's GET, or
# vice versa (Codex review finding).
_GH_API_SEGMENT_RE = re.compile(r"gh\s+api\b[^\n]*?(?=&&|;|\||$)", re.IGNORECASE)


def _is_api_post(segment: str) -> bool:
    """True when a `gh api` call actually POSTs (explicit --method/-X wins;
    absent one, a body field implies POST — same rule as pr-report-destination-gate).
    Caller passes ONE invocation's own segment, never a whole compound command."""
    m = _API_METHOD_RE.search(segment)
    if m:
        return m.group(1).upper() == "POST"
    return bool(_API_BODY_RE.search(segment))


def _api_post_targets(cmd: str) -> list[str]:
    """PR numbers targeted by a write-method `gh api` call, resolved per
    invocation segment so method and target are never cross-matched across
    two different `gh api` calls in the same compound command."""
    targets: list[str] = []
    for segment in _GH_API_SEGMENT_RE.finditer(cmd):
        seg = segment.group(0)
        if _is_api_post(seg):
            targets.extend(m.group(1) for m in _API_TARGET_RE.finditer(seg))
    return targets


def _create_segments(cmd: str) -> list[str]:
    """Every `gh pr create` invocation's own argument segment in `cmd`."""
    return [m.group(1) for m in _PR_CREATE_SEGMENT_RE.finditer(cmd)]


def _comment_targets(cmd: str) -> list[str]:
    return [m.group(1) or m.group(2) for m in _PR_COMMENT_RE.finditer(cmd)]


def _reduce_tool_use(block: dict, pending_creates: list, pending_posts: list) -> None:
    """Reduce a tool_use block to what the resolver pass needs; drop the block itself
    (retaining whole Bash command strings for the session length is the cost #1076
    already paid down for this gate's whole-transcript-scan sibling)."""
    name = block.get("name")
    inp = block.get("input") or {}
    tid = block.get("id")
    tid = tid if isinstance(tid, str) else None
    if name != "Bash" or not isinstance(inp.get("command"), str):
        return
    cmd = inp["command"]
    for segment in _create_segments(cmd):
        is_draft = bool(_DRAFT_FLAG_RE.search(" " + segment))
        pending_creates.append((tid, is_draft))
    posted = list(_comment_targets(cmd))
    posted.extend(_api_post_targets(cmd))
    if posted:
        pending_posts.append((tid, posted))


def find_unanchored_prs(events) -> list[str]:
    """Return the sorted list of non-draft PR numbers created (successfully) this
    session that have not received a successful comment post."""
    # (tool_use id, is_draft) — one entry per `gh pr create` invocation.
    pending_creates: list[tuple[str | None, bool]] = []
    # (tool_use id, [PR numbers posted to])
    pending_posts: list[tuple[str | None, list[str]]] = []
    result_is_error: dict[str, bool] = {}
    result_text: dict[str, str] = {}

    for ev in events:
        msg = ev.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "tool_use":
                _reduce_tool_use(block, pending_creates, pending_posts)
            elif kind == "tool_result":
                tid = block.get("tool_use_id")
                if not isinstance(tid, str):
                    continue
                result_is_error[tid] = block.get("is_error") is True
                rc = block.get("content")
                parts: list[str] = []
                if isinstance(rc, str):
                    parts.append(rc)
                elif isinstance(rc, list):
                    for c in rc:
                        if isinstance(c, dict) and isinstance(c.get("text"), str):
                            parts.append(c["text"])
                if parts:
                    result_text[tid] = "\n".join(parts)

    created: set[str] = set()
    for tid, is_draft in pending_creates:
        if is_draft:
            continue
        if tid is None or result_is_error.get(tid) is True:
            continue
        text = result_text.get(tid, "") if tid is not None else ""
        urls = {m.group(1) for m in _PR_URL_RE.finditer(text)}
        if len(urls) == 1:
            created.add(next(iter(urls)))

    posted: set[str] = set()
    for tid, nums in pending_posts:
        if tid is not None and result_is_error.get(tid) is True:
            continue
        posted.update(nums)

    return sorted(created - posted, key=int)


def _build_message(unanchored: list[str], blocking: bool) -> str:
    verb = "차단" if blocking else "안내"
    return (
        f"{_PREFIX} 이번 세션에서 PR {', '.join('#' + n for n in unanchored)} 을(를) "
        "생성했지만, 그 PR 에 대한 검증 앵커 코멘트(`gh pr comment` / `gh api ... "
        "issues/comments`)가 아직 게시되지 않았습니다 ({verb}).\n".format(verb=verb)
        + f"{_PREFIX} Rule: Post-PR Empirical Verification 앵커를 남기세요. 요구하는 "
        "것은 존재이지 PASS 가 아닙니다 — 환경 불통/자격증명 부재로 검증이 막혔다면 "
        "`BLOCKED` 행을 담은 앵커도 유효합니다.\n"
        f"{_PREFIX} 드래프트 PR 은 이 게이트 대상이 아닙니다.\n"
        f"{_PREFIX} 항상 advisory 로: {_ADVISORY_ENV}=1 | bypass: {_BYPASS_ENV}=1\n"
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

    unanchored = find_unanchored_prs(iter_transcript(transcript_path))
    if not unanchored:
        return 0

    session_id = payload.get("session_id")
    has_session = isinstance(session_id, str) and bool(session_id)
    force_advisory = bool(os.environ.get(_ADVISORY_ENV, "").strip())

    already_advised = (
        has_session
        and _fire_ledger.count_session_fires(_HOOK_NAME, session_id, _fire_ledger.DECISION_ADVISE) > 0
    )

    if force_advisory or not already_advised:
        emit_stop_advisory(_build_message(unanchored, blocking=False))
        decision = _fire_ledger.DECISION_ADVISE
    else:
        emit_stop_block(_build_message(unanchored, blocking=True))
        decision = _fire_ledger.DECISION_BLOCK

    if has_session:
        if _fire_ledger.record_session_fire(_HOOK_NAME, _ROLE, decision, session_id, "Stop"):
            _fire_ledger.suppress_coarse_duplicate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
