#!/usr/bin/env python3
"""Stop hook: PR-bound verification/review report left in a local file only.

Issue #832. Origin — a /insights friction pass over a month of sessions: the
agent ran PR verification/review, wrote the results to a LOCAL report file
(/tmp, .omc/plans), and never posted them to the PR. The user had to ask "did
you share this?". A prose CLAUDE.md rule for this ("report to the PR, not just
a local file") failed the retrieval-at-execution test (Loaded != Retrieved),
so the guard is moved to an execution-time Stop hook — mirroring the sibling
completion-verify gates whose whole thesis is "prompt layer fails, hook layer
succeeds".

## Design

Detection is per-PR, not a global boolean, so it survives multi-PR sessions
(the user's common multi-clauding pattern). Scanning the WHOLE transcript
(not just the current turn) because the report write and the PR context can
be many turns apart:

  context PRs — PR numbers the session actually worked on:
    `gh pr view|create|diff|checks|edit|ready <N>` and any `…/pull/<N>` URL.
  posted PRs  — PR numbers a SUCCESSFUL post targeted:
    `gh pr comment|review <N>`, and POST `gh api …/pulls/<N>/{comments,reviews}`.

  Fires when a report-like local .md was written AND at least one context PR
  received no successful post  (contextPRs - postedPRs != {}).

## Correctness guards (all covered by functional tests)

  - GET `gh api` (no --method POST / -f / -F / --field / --input) is a read,
    NOT a post — excluded. (`gh api` defaults to GET; POST only when the
    method is overridden or a body field is added.)
  - A post command whose tool_result is is_error is a FAILED post — excluded,
    matched by tool_use `id` <-> tool_result `tool_use_id`.
  - Posting to an unrelated PR does not mark the current PR as reported.

## Tiers

  Default -> advisory (non-blocking). The signal is heuristic ("is this local
    .md a PR report that should be shared?" is a semantic judgment), so a hard
    block is not affordable; a false fire costs one ignorable stderr line.
  `PRAXIS_HOOK_BYPASS_PR_REPORT_DESTINATION_GATE=1` -> full bypass (exit 0).

## Honest limitation

  A genuinely private scratch .md (never meant for the PR) still trips the
  advisory when a context PR exists. Advisory-only, so the cost is bounded.

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
from _hook_io import emit_stop_advisory  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _transcript import load_transcript  # type: ignore[import-not-found]  # noqa: E402

_PREFIX = "[pr-report-destination-gate]"
_BYPASS_ENV = "PRAXIS_HOOK_BYPASS_PR_REPORT_DESTINATION_GATE"

# Report-like local .md: under a scratch dir, or a review/verification basename.
_REPORT_PATH_RE = re.compile(r"(^|/)(tmp|scratchpad)/|\.omc/plans/", re.IGNORECASE)
_REPORT_NAME_RE = re.compile(r"(review|verif|verdict|report|impact|검증|리뷰)[^/]*\.md$", re.IGNORECASE)

# A `gh api` call is a POST when it overrides the method or adds a body field.
_API_POST_RE = re.compile(r"--method\s+POST\b|-X\s+POST\b|(^|\s)(-f|-F|--field|--raw-field|--input)\b", re.IGNORECASE)
_API_TARGET_RE = re.compile(r"gh\s+api\s+[^\n]*?pulls/(\d+)/(?:comments|reviews)\b", re.IGNORECASE)
_PR_URL_RE = re.compile(r"github\.com/[\w.-]+/[\w.-]+/pull/(\d+)", re.IGNORECASE)


def _pr_num(cmd: str, subs: str) -> str | None:
    """Extract a PR number from `gh pr <sub> <target>` (number or /pull/N URL)."""
    m = re.search(rf"gh\s+pr\s+(?:{subs})\s+(?:\S*?/pull/(\d+)|(\d+))", cmd, re.IGNORECASE)
    if not m:
        return None
    return m.group(1) or m.group(2)


def find_unreported_prs(events: list[dict]) -> tuple[list[str], list[str]]:
    """Return (unreported_context_prs, sample_report_files)."""
    tool_uses: list[dict] = []
    result_is_error: dict[str, bool] = {}
    raw_text_parts: list[str] = []

    for ev in events:
        msg = ev.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            raw_text_parts.append(content)
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                text = block.get("text")
                if isinstance(text, str):
                    raw_text_parts.append(text)
            elif kind == "tool_use":
                tool_uses.append(block)
            elif kind == "tool_result":
                tid = block.get("tool_use_id")
                if isinstance(tid, str):
                    result_is_error[tid] = block.get("is_error") is True
                rc = block.get("content")
                if isinstance(rc, str):
                    raw_text_parts.append(rc)
                elif isinstance(rc, list):
                    for c in rc:
                        if isinstance(c, dict) and isinstance(c.get("text"), str):
                            raw_text_parts.append(c["text"])

    raw_text = "\n".join(raw_text_parts)

    def succeeded(u: dict) -> bool:
        # Missing result (e.g. the final call) counts as success — bias to silence.
        tid = u.get("id")
        if not isinstance(tid, str):
            return True
        return result_is_error.get(tid) is not True

    context_prs: set[str] = set()
    posted_prs: set[str] = set()
    report_files: list[str] = []

    for u in tool_uses:
        name = u.get("name")
        inp = u.get("input") or {}
        if name == "Bash" and isinstance(inp.get("command"), str):
            cmd = inp["command"]
            ctx = _pr_num(cmd, "view|create|diff|checks|edit|ready")
            if ctx:
                context_prs.add(ctx)
            if not succeeded(u):
                continue
            posted = _pr_num(cmd, "comment|review")
            if posted:
                posted_prs.add(posted)
            am = _API_TARGET_RE.search(cmd)
            if am and _API_POST_RE.search(cmd):
                posted_prs.add(am.group(1))
        elif name in ("Write", "Edit") and isinstance(inp.get("file_path"), str) and succeeded(u):
            fp = inp["file_path"]
            if fp.lower().endswith(".md") and (_REPORT_PATH_RE.search(fp) or _REPORT_NAME_RE.search(fp)):
                report_files.append(fp)

    for m in _PR_URL_RE.finditer(raw_text):
        context_prs.add(m.group(1))

    if not report_files:
        return ([], [])
    unreported = sorted(context_prs - posted_prs, key=lambda n: int(n))
    return (unreported, report_files)


def _build_message(unreported: list[str], report_files: list[str]) -> str:
    seen: list[str] = []
    for f in report_files:
        if f not in seen:
            seen.append(f)
        if len(seen) == 3:
            break
    sample = "\n".join("  - " + f for f in seen)
    return (
        f"{_PREFIX} PR-bound 세션(PR {', '.join(unreported)})이 review/verification "
        "로컬 리포트 파일을 작성했으나, 그 PR 에 성공적인 `gh pr comment` / `gh pr review` "
        "게시가 없습니다:\n"
        f"{sample}\n"
        f"{_PREFIX} Rule: PR-bound 검증/리뷰 결과는 로컬 파일에만 두지 말고 PR 코멘트로 "
        "게시하라 (사용자가 '공유했나?' 를 되묻지 않도록). 게시는 여전히 Layer-3 "
        "external-write falsification 게이트에 종속된다 — 가설이 아닌 증거 기반 결과만.\n"
        f"{_PREFIX} 로컬 scratch/draft 라면 이 advisory 를 무시하라. "
        f"bypass: {_BYPASS_ENV}=1"
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

    unreported, report_files = find_unreported_prs(events)
    if not unreported or not report_files:
        return 0

    emit_stop_advisory(_build_message(unreported, report_files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
