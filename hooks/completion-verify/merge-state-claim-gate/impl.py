#!/usr/bin/env python3
"""Stop-hook advisory: gate unverified merge / PR / issue / worktree state claims.

Background (issue #503): a praxis ultrawork session hallucinated review/merge
state four times in a row — "PR #495/#497 created, merged, issue closed,
worktree cleaned" — none of which had happened (the PR create was hook-blocked
and the cited numbers were unrelated worktrees). The behavioural remedy lived in
memory only, and "REPEATED PATTERN + MEMORY = FAILED REMEDY -> ESCALATE" (Iron
Law) was crossed. PreToolUse cannot see in-flight assistant text (issue #487
A3), but a Stop-hook sees the final assistant output — the exact complement.

This hook scans the final assistant message for a *completed* merge / PR / issue
/ worktree state assertion and, if no fresh state query
(`gh pr|issue view/list/merge`, or a GitHub MCP pull_request/issue/merge read)
appears in the recent transcript, emits an advisory. It is advisory by default
(stdout `{"systemMessage": ...}` JSON + exit 0 — shown to the user in the
transcript; issue #647 H3 standardized the completion-verify role on stdout
JSON, replacing the old stderr form that only reached the debug log);
`PRAXIS_MERGE_CLAIM_STRICT=1` escalates to `{"decision": "block", "reason":
...}` (re-prompts the model to verify). Fully fail-open; bypass with
`PRAXIS_MERGE_CLAIM_BYPASS=1`.
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

_PREFIX = "[merge-state-claim-gate]"
_BYPASS_ENV = "PRAXIS_MERGE_CLAIM_BYPASS"
_STRICT_ENV = "PRAXIS_MERGE_CLAIM_STRICT"
_EVIDENCE_WINDOW = 80  # how many recent transcript events to scan for evidence
_HOOK_NAME = "merge-state-claim-gate"
_ROLE = "completion-verify"

# ---------------------------------------------------------------------------
# Claim detection — a claim needs a SUBJECT token and a COMPLETED-state token
# on the same line (localizes the match, cutting false positives on long
# final messages). Completion tokens are past/perfective so future intent
# ("I'll create a PR", "ready to merge") does not trigger.
# ---------------------------------------------------------------------------

_SUBJECT_RE = re.compile(
    r"(?<![A-Za-z0-9_])PR(?![A-Za-z0-9_])|\bpull request\b|\bMR\b|이슈|\bissue\b|worktree|워크트리|#\d+",
    re.IGNORECASE,
)

# Branch/ref subject — accepted ONLY for the "applied" claim kind (issue #656).
# "dev에 적용됨" / "deployed to prod" carries no PR/#N token, so the standard
# subject misses it. Lookarounds (not \b) so Korean particles ("dev에") match —
# same trap as the \bPR\b fix above.
_BRANCH_SUBJECT_RE = re.compile(
    r"(?<![A-Za-z0-9_])(dev|prod|main|master|release)(?![A-Za-z0-9_])"
    r"|브랜치|(?<![A-Za-z0-9_])branch(?![A-Za-z0-9_])",
    re.IGNORECASE,
)

_CLAIM_KINDS: list[tuple[str, re.Pattern[str]]] = [
    ("merged", re.compile(r"\b(squash[- ]?)?merged\b|머지\s*(됐|했|됨|되었|완료|함)", re.IGNORECASE)),
    ("created", re.compile(r"\bcreated\b|\bopened\b|생성\s*(했|됨|완료|함)|만들었|올렸|작성했", re.IGNORECASE)),
    ("closed", re.compile(r"\bclosed\b|닫(았|힘|았습|혔)|종료\s*(했|됨)", re.IGNORECASE)),
    ("cleaned", re.compile(r"\b(removed|cleaned|deleted)\b|정리\s*(했|됨|완료)|삭제\s*(했|됨)|제거\s*(했|됨)", re.IGNORECASE)),
    # "applied" needs REACHABILITY evidence, not just any state query (#656):
    # PR state=MERGED is not proof the change reached the target branch.
    # `released` is deliberately absent — it false-positives on lock/memory
    # release prose ("released the lock on the main thread") and double-counts
    # with the `release` branch token; applied/deployed/landed + 배포/반영
    # cover the deploy sense.
    ("applied", re.compile(
        r"\b(applied|deployed|landed)\b|\bblocked\s+since\b"
        r"|적용\s*(됐|했|됨|되었|완료)|배포\s*(됐|됨|되었|완료)|반영\s*(됐|됨|되었|완료)|차단\s*(됐|됨|되었)",
        re.IGNORECASE,
    )),
]

# Negation present on the line -> skip (conservative; avoids noisy advisories).
# `\bno\b` intentionally omitted: it over-suppresses realistic lines like
# "PR #543 merged — no conflicts" and "Issue closed — no further action needed".
# `\bwill\b` intentionally omitted: it is too blunt at the line level — it would
# suppress mixed-tense lines such as "PR #543 merged — this will close the issue",
# where a real completion claim co-occurs with a future clause. The narrow
# false-positive it would have fixed (future-passive "the PR will be merged") is
# accepted as tolerable advisory noise; silencing a real merged-claim is worse than
# a noisy advisory on a genuine future statement (this is an advisory-only hook).
_NEGATION_RE = re.compile(
    r"\bnot\b|n't\b|\bwithout\b|\byet\b|아직|않|못\s|안\s|없|실패|fail",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Evidence detection — a fresh state query in the recent transcript.
# ---------------------------------------------------------------------------

_GH_EVIDENCE_RE = re.compile(r"\bgh\b[^|;&\n]*\b(pr|issue)\b\s+[a-z]", re.IGNORECASE)
_MCP_GH_EVIDENCE_RE = re.compile(r"pull_request|issue|merge|pr_", re.IGNORECASE)

# Reachability evidence for "applied" claims (#656). A generic state query
# (`gh pr view --json state`) is NOT sufficient — the 2026-05-15 incident ran
# exactly that probe and still mis-released 3 changes because the merged PR's
# base was a feature branch (stacked PR). Only commands that test ancestry /
# base resolution count. Kept CLI-only deliberately: an MCP pull_request_read
# *returns* baseRefName but does not prove the field was consulted.
# The baseRefName arm requires the `--json` query context AND a `state` field
# in the same command (either order): a bare-token match would let
# `grep baseRefName impl.py` (this hook's own source contains the literal)
# silently clear a genuine claim, and a baseRefName-only query never confirms
# the PR actually merged — the canonical probe is `--json state,baseRefName`.
# `--contains` tolerates both short (-r) and long (--merged) intervening flags.
# Mirrored in hooks/advisory-nudge/external-write-falsify-check (2 copies —
# DRY extraction deferred to a 3rd consumer per repo convention).
_REACHABILITY_EVIDENCE_RE = re.compile(
    r"merge-base\s+--is-ancestor"
    r"|--json[^|;&\n]*(?:state[^|;&\n]*baseRefName|baseRefName[^|;&\n]*state)"
    r"|\bbranch\b\s+(?:--?\w[\w-]*\s+)*--contains",
    re.IGNORECASE,
)

# Narrower negation for the "applied" kind only: `\bwithout\b` is dropped —
# genuine deploy claims routinely carry it ("Deployed to prod without
# incident") and would be silently suppressed. `fail` stays: a line like
# "prod 배포 실패" is a failure report, not an applied claim; the cost (an
# applied claim co-occurring with "failing" prose is missed) is accepted and
# documented, same trade-off style as the `no`/`will` notes above.
# Mirrored in hooks/advisory-nudge/external-write-falsify-check.
_APPLIED_NEGATION_RE = re.compile(
    r"\bnot\b|n't\b|\byet\b|아직|않|못\s|안\s|없|실패|fail",
    re.IGNORECASE,
)


def detect_claims(text: str) -> list[str]:
    """Return the distinct claim kinds asserted in `text` (subject + completed
    state on the same line, not negated). The "applied" kind additionally
    accepts a branch/ref subject (dev/prod/main/브랜치) — applied-on-branch
    claims often name only the target ref, never a PR number (#656)."""
    kinds: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        negated_std = bool(_NEGATION_RE.search(line))
        has_std_subject = bool(_SUBJECT_RE.search(line))
        for kind, pat in _CLAIM_KINDS:
            if kind in kinds:
                continue
            if kind == "applied":
                if _APPLIED_NEGATION_RE.search(line):
                    continue
                if not (has_std_subject or _BRANCH_SUBJECT_RE.search(line)):
                    continue
            else:
                if negated_std or not has_std_subject:
                    continue
            if pat.search(line):
                kinds.append(kind)
    return kinds


def has_fresh_state_query(events: list[dict]) -> bool:
    """True if a recent assistant tool_use is a gh pr|issue query or a GitHub
    MCP pull_request/issue/merge call."""
    for ev in events[-_EVIDENCE_WINDOW:]:
        msg = ev.get("message", {})
        if msg.get("role") != "assistant" or ev.get("isSidechain"):
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name", "") or ""
            if name.startswith("mcp__github__"):
                if _MCP_GH_EVIDENCE_RE.search(name):
                    return True
            elif name == "Bash":
                inp = block.get("input", {})
                cmd = inp.get("command", "") if isinstance(inp, dict) else ""
                if cmd and _GH_EVIDENCE_RE.search(cmd):
                    return True
    return False


def has_reachability_evidence(events: list[dict]) -> bool:
    """True if a recent assistant Bash command tests ref reachability —
    `git merge-base --is-ancestor`, a `baseRefName` field query, or
    `git branch --contains` (#656)."""
    for ev in events[-_EVIDENCE_WINDOW:]:
        msg = ev.get("message", {})
        if msg.get("role") != "assistant" or ev.get("isSidechain"):
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if (block.get("name", "") or "") != "Bash":
                continue
            inp = block.get("input", {})
            cmd = inp.get("command", "") if isinstance(inp, dict) else ""
            if cmd and _REACHABILITY_EVIDENCE_RE.search(cmd):
                return True
    return False


def _advisory(kinds: list[str]) -> str:
    # The "no fresh state query" sentence is only true for the non-applied
    # kinds — an "applied" claim can be unbacked even when a state query
    # exists (that is exactly the incident shape), so the generic sentence
    # is emitted only when a non-applied kind is actually unbacked.
    msg = ""
    other = [k for k in kinds if k != "applied"]
    if other:
        msg += (
            f"{_PREFIX} final message asserts a {'/'.join(other)} state change but no "
            "fresh state query (`gh pr|issue view/list/merge` or a GitHub MCP "
            "pull_request/issue read) appears in the recent transcript.\n"
            f"{_PREFIX} Rule: re-read the state you are about to assert "
            "(gh pr view / gh issue view / mcp__github__pull_request_read) and cite "
            "the result BEFORE declaring it — merge/close/create claims from memory "
            "have repeatedly hallucinated (issue #503).\n"
        )
    if "applied" in kinds:
        msg += (
            f"{_PREFIX} final message asserts an applied-on-branch state without "
            "REACHABILITY evidence in the recent transcript. A state query is "
            "not sufficient: PR state=MERGED does NOT prove the change "
            "reached the target branch (stacked-PR base; issue #656). Run "
            "`gh pr view <N> --json state,baseRefName` or "
            "`git merge-base --is-ancestor <sha> origin/<target>` and cite the "
            "output before asserting applied/deployed/blocked-since.\n"
        )
    return msg + f"{_PREFIX} bypass: {_BYPASS_ENV}=1\n"


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

    kinds = detect_claims(last_text)
    if not kinds:
        return 0

    # "applied" claims are cleared only by reachability evidence; the other
    # kinds are cleared by any fresh state query. A state-only query that
    # clears "merged" must NOT clear "applied" — that conflation is the exact
    # incident this kind exists for (#656).
    unbacked = [k for k in kinds if k != "applied"]
    if unbacked and has_fresh_state_query(events):
        unbacked = []
    if "applied" in kinds and not has_reachability_evidence(events):
        unbacked.append("applied")
    if not unbacked:
        return 0

    if os.environ.get(_STRICT_ENV, "").strip() == "1":
        emit_stop_block(_advisory(unbacked))
        decision = _fire_ledger.DECISION_BLOCK
    else:
        emit_stop_advisory(_advisory(unbacked))
        decision = _fire_ledger.DECISION_ADVISE
    # Rich fire record (issue #847): Stop hooks signal block/advise via a stdout
    # decision while exiting 0, so @fail_open's coarse path records only "pass".
    # Record the real decision (one rich record per genuine emit; stop_hook_active
    # already guards re-entrant re-fires), keeping session attribution when present
    # and forgoing it otherwise. suppress_coarse_duplicate() drops the redundant
    # coarse "pass" so aggregate_fires() does not count one emit as fires=2.
    session_id = payload.get("session_id")
    _fire_ledger.record_session_fire(
        _HOOK_NAME, _ROLE, decision,
        session_id if isinstance(session_id, str) else "", "Stop",
    )
    _fire_ledger.suppress_coarse_duplicate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
