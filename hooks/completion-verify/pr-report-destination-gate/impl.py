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
    `gh pr view|create|diff|checks|checkout|edit|ready|merge <N>` and any
    `…/pull/<N>` URL.
  posted PRs  — PR numbers a SUCCESSFUL post targeted:
    `gh pr comment|review <N>`, and POST `gh api …/pulls/<N>/{comments,reviews}`.

  Fires when a report-like local .md was written AND at least one context PR
  received no successful post  (contextPRs - postedPRs != {}).

## Correctness guards (all covered by functional tests)

  - `gh api` POST detection honors an explicit --method/-X value first, so
    `--method GET … -f page=1` is a documented GET (query params), NOT a post;
    only when no method is given does a body field (-f/-F/--field/--raw-field/
    --input) imply POST. (`gh api` defaults to GET.)
  - A post command whose tool_result is is_error is a FAILED post — excluded,
    matched by tool_use `id` <-> tool_result `tool_use_id`.
  - Posting to an unrelated PR does not mark the current PR as reported.
  - Compound commands (`gh pr view 10 && gh pr view 11`) contribute EVERY PR
    target (finditer), not just the first.
  - A PR URL in tool output counts as context only when the result carries
    exactly ONE distinct PR (a create/view success), never a multi-PR listing
    (`gh pr list`); narrative (assistant/user) prose contributes all URLs.
  - Fires once per session: the whole-transcript scan re-derives the same
    condition each Stop, so a session-fire dedup (issue #805) suppresses the
    repeat while an unreported context PR persists.

## Tiers

  Default -> advisory (non-blocking). The signal is heuristic ("is this local
    .md a PR report that should be shared?" is a semantic judgment), so a hard
    block is not affordable; a false fire costs one ignorable advisory line
    (emitted as a `systemMessage` JSON on stdout, not stderr).
  `PRAXIS_HOOK_BYPASS_PR_REPORT_DESTINATION_GATE=1` -> full bypass (exit 0).

## Honest limitations (advisory-only, so each is a bounded false signal)

  - A genuinely private scratch .md (never meant for the PR) still trips the
    advisory when a context PR exists.
  - A post is counted whenever a successful comment/review targets the PR — the
    post's CONTENT is not correlated with the report file, so posting a
    "review starting" comment then leaving the final report unposted still
    suppresses the advisory (false negative).
  - PRs are keyed by number only, so two repos sharing a number
    (`org/a#178` vs `org/b#178`) collide in one session — bare `gh pr view 178`
    carries no owner/repo, so a full owner/repo key is not recoverable.

## Fail-open contract
  - Malformed / missing stdin JSON -> exit 0
  - Missing / unreadable / empty transcript -> exit 0
  - stop_hook_active=true -> exit 0 (re-entrancy guard)
  - Any uncaught exception -> exit 0 (@fail_open)
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
import _fire_ledger  # type: ignore[import-not-found]  # noqa: E402
from _hook_io import emit_stop_advisory  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _transcript import (  # type: ignore[import-not-found]  # noqa: E402
    reduce_transcript_resumable,
    stop_scan_cursor_path,
)

_HOOK_NAME = "pr-report-destination-gate"
_ROLE = "completion-verify"
_PREFIX = "[pr-report-destination-gate]"
_BYPASS_ENV = "PRAXIS_HOOK_BYPASS_PR_REPORT_DESTINATION_GATE"

# Report-like local .md: under a scratch dir, or a review/verification basename.
_REPORT_PATH_RE = re.compile(r"(^|/)(tmp|scratchpad)/|\.omc/plans/", re.IGNORECASE)
_REPORT_NAME_RE = re.compile(r"(review|verif|verdict|report|impact|검증|리뷰)[^/]*\.md$", re.IGNORECASE)

# A `gh api` POST: an explicit --method/-X value wins; absent one, a body field implies POST.
_API_METHOD_RE = re.compile(r"(?:--method|-X)\s+([A-Za-z]+)", re.IGNORECASE)
_API_BODY_RE = re.compile(r"(^|\s)(-f|-F|--field|--raw-field|--input)\b", re.IGNORECASE)
_API_TARGET_RE = re.compile(r"gh\s+api\s+[^\n]*?pulls/(\d+)/(?:comments|reviews)\b", re.IGNORECASE)
_PR_URL_RE = re.compile(r"github\.com/[\w.-]+/[\w.-]+/pull/(\d+)", re.IGNORECASE)


def _is_api_post(cmd: str) -> bool:
    """True when a `gh api` call actually POSTs.

    An explicit `--method`/`-X` value wins, so `--method GET … -f page=1` is a
    documented GET (query params), not a post; only when no method is given
    does a body field (`-f`/`-F`/`--field`/`--raw-field`/`--input`) imply POST.
    """
    m = _API_METHOD_RE.search(cmd)
    if m:
        return m.group(1).upper() == "POST"
    return bool(_API_BODY_RE.search(cmd))


def _pr_nums(cmd: str, subs: str) -> list[str]:
    """All PR numbers from `gh pr <sub> <target>` occurrences (number or /pull/N URL).

    finditer (not search) so a compound command (`gh pr view 10 && gh pr view 11`)
    yields every target, not just the first. The target is read as the token
    directly after the subcommand, so a flag before the positional
    (`gh pr comment -b "…" 123`) is not matched — an accepted advisory-only miss;
    scanning the whole command would false-match a number inside a flag value
    (`--body "closes 999"`).
    """
    return [
        m.group(1) or m.group(2)
        for m in re.finditer(rf"gh\s+pr\s+(?:{subs})\s+(?:\S*?/pull/(\d+)|(\d+))", cmd, re.IGNORECASE)
    ]


def _add_narrative_prs(acc: set[str], text: str) -> None:
    """Narrative prose (assistant/user text) referencing a PR is intentional context."""
    for m in _PR_URL_RE.finditer(text):
        acc.add(m.group(1))


def _add_tool_result_prs(acc: set[str], text: str) -> None:
    """Count a PR URL from tool output only when the result carries exactly ONE
    distinct PR (a `gh pr create`/`view` success), never a multi-PR listing
    (`gh pr list`) which would flood unrelated PRs into context."""
    urls = {m.group(1) for m in _PR_URL_RE.finditer(text)}
    if len(urls) == 1:
        acc.add(next(iter(urls)))


def _reduce_tool_use(block: dict, context_prs: set[str], pending: list) -> None:
    """Keep only what the success-resolving pass needs, never the block itself."""
    name = block.get("name")
    inp = block.get("input") or {}
    tid = block.get("id")
    tid = tid if isinstance(tid, str) else None
    if name == "Bash" and isinstance(inp.get("command"), str):
        cmd = inp["command"]
        # Context is success-independent, so it resolves here and needs no buffer.
        context_prs.update(_pr_nums(cmd, "view|create|diff|checks|checkout|edit|ready|merge"))
        posted = list(_pr_nums(cmd, "comment|review"))
        if _is_api_post(cmd):
            posted.extend(m.group(1) for m in _API_TARGET_RE.finditer(cmd))
        if posted:
            pending.append((tid, posted, None))
    elif name in ("Write", "Edit") and isinstance(inp.get("file_path"), str):
        fp = inp["file_path"]
        if fp.lower().endswith(".md") and (_REPORT_PATH_RE.search(fp) or _REPORT_NAME_RE.search(fp)):
            pending.append((tid, [], fp))


def find_unreported_prs(events) -> tuple[list[str], list[str]]:
    """Return (unreported_context_prs, sample_report_files).

    `events` is any iterable of event dicts — pass `iter_transcript(path)` to
    stream. This scan genuinely needs the whole session (it looks for a PR
    touched anywhere in it), so it cannot read a bounded tail like its sibling
    gates. Every block is reduced on arrival to the PR numbers and report path
    it carries, and the block itself is dropped: retaining `tool_use` blocks
    would keep whole Bash command strings alive for the length of the session,
    which is the cost this gate exists to avoid (issue #1076).
    """
    state = _new_state()
    for ev in events:
        _reduce_event(state, ev)
    return _resolve(state)


def scan_unreported_prs(transcript_path: str, session_id) -> tuple[list[str], list[str]]:
    """`find_unreported_prs` over the whole file, resuming from the previous
    Stop's cursor when the session has one (issue #1237)."""
    state = reduce_transcript_resumable(
        transcript_path,
        stop_scan_cursor_path(_HOOK_NAME, session_id),
        _new_state,
        _reduce_event,
        encode=_encode_state,
        decode=_decode_state,
    )
    return _resolve(state)


def _new_state() -> dict:
    return {
        # (tool_use id, PR numbers this call posted to, report file written)
        "pending": [],
        "result_is_error": {},
        "context_prs": set(),
        "interesting_tids": set(),
    }


def _encode_state(state: dict) -> dict:
    return {
        "pending": [[tid, list(posted), report] for tid, posted, report in state["pending"]],
        "result_is_error": state["result_is_error"],
        "context_prs": sorted(state["context_prs"]),
        "interesting_tids": sorted(state["interesting_tids"]),
    }


def _decode_state(data: dict) -> dict:
    return {
        "pending": [(tid, list(posted), report) for tid, posted, report in data["pending"]],
        "result_is_error": dict(data["result_is_error"]),
        "context_prs": set(data["context_prs"]),
        "interesting_tids": set(data["interesting_tids"]),
    }


def _reduce_event(state: dict, ev: dict) -> None:
    pending = state["pending"]
    result_is_error = state["result_is_error"]
    context_prs = state["context_prs"]
    msg = ev.get("message")
    if not isinstance(msg, dict):
        return
    content = msg.get("content")
    if isinstance(content, str):
        _add_narrative_prs(context_prs, content)
        return
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text = block.get("text")
            if isinstance(text, str):
                _add_narrative_prs(context_prs, text)
        elif kind == "tool_use":
            before = len(pending)
            _reduce_tool_use(block, context_prs, pending)
            for tid, _posted, _report in pending[before:]:
                if tid is not None:
                    state["interesting_tids"].add(tid)
        elif kind == "tool_result":
            tid = block.get("tool_use_id")
            # Only a pending post/report call is ever looked up, so only those
            # tids are kept: the state crosses a JSON file between Stops
            # (issue #1237), and one entry per tool_result would grow it with
            # the session (583KB on a 143k-event transcript, 6KB with this).
            if isinstance(tid, str) and tid in state["interesting_tids"]:
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
                _add_tool_result_prs(context_prs, "\n".join(parts))


def _resolve(state: dict) -> tuple[list[str], list[str]]:
    pending = state["pending"]
    result_is_error = state["result_is_error"]
    context_prs = state["context_prs"]

    posted_prs: set[str] = set()
    report_files: list[str] = []

    for tid, posted, report_file in pending:
        # Missing result (e.g. the final call) counts as success — bias to silence.
        if tid is not None and result_is_error.get(tid) is True:
            continue
        posted_prs.update(posted)
        if report_file is not None:
            report_files.append(report_file)

    if not report_files:
        return ([], [])
    unreported = sorted(context_prs - posted_prs, key=lambda n: int(n))
    return (unreported, report_files)


def _build_message(unreported: list[str], report_files: list[str]) -> str:
    """Compose the Stop advisory naming the unreported PR(s) and up to 3 files."""
    seen: list[str] = []
    for f in report_files:
        if f not in seen:
            seen.append(f)
        if len(seen) == 3:
            break
    sample = "\n".join("  - " + f for f in seen)
    return (
        f"{_PREFIX} this PR-bound session (PR {', '.join(unreported)}) wrote a local "
        "review/verification report file, but no successful `gh pr comment` / "
        "`gh pr review` was posted on that PR:\n"
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
    """Stop-hook entrypoint: emit the advisory when a context PR went unreported."""
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

    session_id = payload.get("session_id")
    has_session = isinstance(session_id, str) and bool(session_id)

    unreported, report_files = scan_unreported_prs(transcript_path, session_id)
    if not unreported or not report_files:
        return 0

    # Fire once per session: the whole-transcript scan re-derives the same
    # condition on every Stop, so without this the advisory would repeat each
    # turn while an unreported context PR persists (sanctioned dedup, issue #805).
    if has_session and _fire_ledger.count_session_fires(_HOOK_NAME, session_id, _fire_ledger.DECISION_ADVISE) > 0:
        return 0

    emit_stop_advisory(_build_message(unreported, report_files))
    if has_session:
        _fire_ledger.record_session_fire(_HOOK_NAME, _ROLE, _fire_ledger.DECISION_ADVISE, session_id, "Stop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
