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
  - `gh pr comment` target detection tolerates flag/positional interleaving
    (`gh pr comment -b "…" 178` is valid gh usage) via a shlex-tokenized walk
    that skips known flags and their values, rather than a fixed-offset regex
    (CodeRabbit finding, PR #1115).
  - Compound-command segmentation (`gh pr create`/`gh pr comment`/`gh api`) is
    shell-quote-aware: `_split_invocations` tokenizes the WHOLE command with
    `shlex.shlex(..., punctuation_chars=True)` before splitting on an
    UNQUOTED `&&`/`;`/`|`/`||`/`;;`. A prior lookahead regex split on those
    characters even inside quotes, so `gh pr comment -b "verified && ready"
    178` truncated mid-quote (the anchor read as unposted) and `gh pr create
    --body "a && b" --draft` lost its `--draft` flag the same way (CodeRabbit
    finding, PR #1115).
  - Only tids in `interesting_tids` (every `gh pr create`/`gh pr comment`/
    write-`gh api` tool_use) ever get a `result_is_error` entry at all — a
    session with tens of thousands of unrelated tool calls must not grow that
    dict by one entry per call (CodeRabbit finding, PR #1115).
  - A `gh pr comment` / write-method `gh api` call only counts as "posted"
    when its tool_use has a matching tool_result that is explicitly non-error
    — a missing/unconfirmed result (interrupted call) does not count
    (CodeRabbit finding, PR #1115).
  - Only a `gh pr create` result's text is ever read (to find its PR URL), and
    only the extracted URL set is retained, never the raw text — a session
    with large unrelated tool outputs (file reads, CI logs) must not cost
    proportional memory/CPU on every Stop (user-reported, PR #1115; mirrors
    pr-report-destination-gate's #1076 discipline, which this gate's original
    tool_result handling had NOT actually matched despite the docstring
    claiming to mirror it).

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
import shlex
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

_DRAFT_FLAGS = frozenset({"--draft", "-d"})

_PR_URL_RE = re.compile(r"github\.com/[\w.-]+/[\w.-]+/pull/(\d+)", re.IGNORECASE)

# Posted-PR detection. `gh pr comment [<number>|<url>|<branch>] [flags]` lets
# flags and the positional identifier interleave in any order (Cobra parsing;
# `gh pr comment -b "…" 178` is valid and confirmed via `gh pr comment --help`
# and a live invocation) — a fixed-offset regex would miss it and leave a
# genuinely-posted PR reading as unanchored (CodeRabbit finding, PR #1115).
# Each invocation's own tokens are walked past known flags (skipping a
# value-flag's own value) so a number *inside* a flag value (`-b "closes 999"
# 178`) is never mistaken for the positional identifier.
_PR_COMMENT_VALUE_FLAGS = frozenset({"-b", "--body", "-F", "--body-file", "-R", "--repo"})

_API_METHOD_RE = re.compile(r"(?:--method|-X)\s+([A-Za-z]+)", re.IGNORECASE)
_API_BODY_RE = re.compile(r"(^|\s)(-f|-F|--field|--raw-field|--input)\b", re.IGNORECASE)
_API_TARGET_RE = re.compile(r"gh\s+api\s+[^\n]*?(?:issues|pulls)/(\d+)/comments\b", re.IGNORECASE)

# Control operators that separate independent invocations in a compound Bash
# command. A prior regex-based segmenter (`gh\s+pr\s+create\b([^\n]*?)(?=&&|;
# |\||$)`) matched these unquoted — `-b "verified && ready" 178` truncated the
# segment mid-quote (shlex.split then raised, dropping the whole invocation)
# and `--body "a && b" --draft` lost its `--draft` flag the same way
# (CodeRabbit finding, PR #1115). shlex's `punctuation_chars` mode keeps a
# quoted operator inside its token while still splitting on an unquoted one —
# verified live: `shlex.shlex('gh pr comment -b "verified && ready" 178',
# posix=True, punctuation_chars=True)` yields ONE token for the quoted body.
_CONTROL_OPERATORS = frozenset({"&&", "||", ";", ";;", "|"})


def _split_invocations(cmd: str) -> list[list[str]]:
    """Tokenize `cmd` with shell-quoting semantics and split into one token
    list per invocation, on unquoted control operators only. `[]` on
    unbalanced/malformed quoting (fail open — nothing is guessed)."""
    try:
        lexer = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        if tok in _CONTROL_OPERATORS:
            segments.append(current)
            current = []
        else:
            current.append(tok)
    segments.append(current)
    return [seg for seg in segments if seg]


def _starts_with(seg: list[str], words: tuple[str, ...]) -> bool:
    return len(seg) >= len(words) and tuple(t.lower() for t in seg[: len(words)]) == words


def _is_api_post(tokens: list[str]) -> bool:
    """True when a `gh api` call actually POSTs (explicit --method/-X wins;
    absent one, a body field implies POST — same rule as pr-report-destination-gate).
    Caller passes ONE invocation's own tokens, never a whole compound command."""
    seg = " ".join(tokens)
    m = _API_METHOD_RE.search(seg)
    if m:
        return m.group(1).upper() == "POST"
    return bool(_API_BODY_RE.search(seg))


def _api_post_targets(segments: list[list[str]]) -> list[str]:
    """PR numbers targeted by a write-method `gh api` invocation, resolved per
    invocation so method and target are never cross-matched across two
    different `gh api` calls in the same compound command."""
    targets: list[str] = []
    for seg in segments:
        if not _starts_with(seg, ("gh", "api")):
            continue
        if _is_api_post(seg):
            seg_text = " ".join(seg)
            targets.extend(m.group(1) for m in _API_TARGET_RE.finditer(seg_text))
    return targets


def _create_segments(segments: list[list[str]]) -> list[list[str]]:
    """Every `gh pr create` invocation's own token list."""
    return [seg for seg in segments if _starts_with(seg, ("gh", "pr", "create"))]


def _comment_targets(segments: list[list[str]]) -> list[str]:
    """PR identifiers targeted by `gh pr comment` invocations, tolerant of
    flag/positional interleaving. An unrecognized `-`-prefixed token is
    treated as a boolean flag (skip one) rather than guessed as a value flag —
    the conservative default when its arity is unknown."""
    targets: list[str] = []
    for tokens in segments:
        if not _starts_with(tokens, ("gh", "pr", "comment")):
            continue
        i = 3  # tokens[0:3] == ["gh", "pr", "comment"]
        while i < len(tokens):
            tok = tokens[i]
            if tok in _PR_COMMENT_VALUE_FLAGS:
                i += 2
                continue
            if tok.startswith("-"):
                i += 1
                continue
            m = _PR_URL_RE.search(tok)
            if m:
                targets.append(m.group(1))
            elif tok.isdigit():
                targets.append(tok)
            i += 1
    return targets


def _reduce_tool_use(
    block: dict,
    pending_creates: list,
    pending_posts: list,
    create_tids: set,
    interesting_tids: set,
) -> None:
    """Reduce a tool_use block to what the resolver pass needs; drop the block itself
    (retaining whole Bash command strings for the session length is the cost #1076
    already paid down for this gate's whole-transcript-scan sibling). `create_tids`
    collects every `gh pr create` tool_use id so the tool_result pass below knows,
    without buffering anything, which results are worth reading at all; `interesting_tids`
    is the wider set (create + post) worth even an `is_error` lookup."""
    name = block.get("name")
    inp = block.get("input") or {}
    tid = block.get("id")
    tid = tid if isinstance(tid, str) else None
    if name != "Bash" or not isinstance(inp.get("command"), str):
        return
    segments = _split_invocations(inp["command"])
    for seg in _create_segments(segments):
        is_draft = any(t.lower() in _DRAFT_FLAGS for t in seg)
        pending_creates.append((tid, is_draft))
        if tid is not None:
            create_tids.add(tid)
            interesting_tids.add(tid)
    posted = list(_comment_targets(segments))
    posted.extend(_api_post_targets(segments))
    if posted:
        pending_posts.append((tid, posted))
        if tid is not None:
            interesting_tids.add(tid)


def find_unanchored_prs(events) -> list[str]:
    """Return the sorted list of non-draft PR numbers created (successfully) this
    session that have not received a successful comment post."""
    # (tool_use id, is_draft) — one entry per `gh pr create` invocation.
    pending_creates: list[tuple[str | None, bool]] = []
    # (tool_use id, [PR numbers posted to])
    pending_posts: list[tuple[str | None, list[str]]] = []
    create_tids: set[str] = set()
    interesting_tids: set[str] = set()
    result_is_error: dict[str, bool] = {}
    # Only a `gh pr create` result ever carries a PR URL this gate needs, and
    # the extracted set is a handful of short digit strings regardless of the
    # result's own size — never the raw text (a 60MB session transcript with
    # unrelated large tool outputs previously cost ~60MB of retained RSS per
    # Stop invocation for a value read from at most a few tids; user-reported
    # memory/perf concern, PR #1115).
    create_urls: dict[str, frozenset[str]] = {}

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
                _reduce_tool_use(block, pending_creates, pending_posts, create_tids, interesting_tids)
            elif kind == "tool_result":
                tid = block.get("tool_use_id")
                if not isinstance(tid, str) or tid not in interesting_tids:
                    continue
                result_is_error[tid] = block.get("is_error") is True
                if tid not in create_tids:
                    continue
                rc = block.get("content")
                parts: list[str] = []
                if isinstance(rc, str):
                    parts.append(rc)
                elif isinstance(rc, list):
                    for c in rc:
                        if isinstance(c, dict) and isinstance(c.get("text"), str):
                            parts.append(c["text"])
                if parts:
                    create_urls[tid] = frozenset(
                        m.group(1) for m in _PR_URL_RE.finditer("\n".join(parts))
                    )

    created: set[str] = set()
    for tid, is_draft in pending_creates:
        if is_draft:
            continue
        if tid is None or result_is_error.get(tid) is True:
            continue
        urls = create_urls.get(tid, frozenset())
        if len(urls) == 1:
            created.add(next(iter(urls)))

    posted: set[str] = set()
    for tid, nums in pending_posts:
        # Require a *confirmed successful* tool_result (result_is_error[tid] is
        # explicitly False) — not merely "not is_error=True". `tid is None` or
        # an interrupted/truncated call with no matching tool_result yet must
        # not count as posted (CodeRabbit finding, PR #1115): a genuinely
        # unconfirmed post would otherwise clear the anchor requirement early.
        if tid is None or result_is_error.get(tid) is not False:
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
