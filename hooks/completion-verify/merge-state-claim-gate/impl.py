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
    get_current_turn,
    load_recent_events,
    resolve_stop_transcript,
    stop_last_assistant_text,
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
# Negative-polarity state claims (issue #869). "PR #864 는 여전히 OPEN, 커밋
# 유실 없음" is a genuine state claim, but it is neither caught by
# `_CLAIM_KINDS` (no merged/created/closed/cleaned/applied verb — "OPEN" and
# "no loss" are not in that vocabulary at all) nor rescued by `_NEGATION_RE`
# (which would silence it anyway, since "없음" matches). The original
# negative-polarity design intentionally treated any negated line as safe —
# but "여전히 OPEN" and "유실 없음" are exactly the persistence-of-state /
# no-change claims that go stale across turns and were never re-verified in
# the #869 incident (pre-compaction snapshot repeated across several turns;
# the PR had actually merged 9ac8fa3 in the interim).
#
# Scope is deliberately narrow to avoid widening false positives (mirrors the
# issue's own scoping note): this path fires ONLY when a PR/issue NUMBER
# (`#123`) co-occurs with unchanged/still-open/no-loss vocabulary on the SAME
# LINE. A numberless line ("the branch still has no open PR") is left to the
# existing (silent) negation path — same trade-off style as the `applied`
# kind's narrower negation set above.
_UNCHANGED_STATE_RE = re.compile(
    r"여전히\s*(?:OPEN|open|열려\s*있)|그대로|유실\s*(?:없|없음|없다)"
    r"|남아있|변경\s*(?:없|되지\s*않)"
    r"|(?<![A-Za-z0-9_])still\s+open(?![A-Za-z0-9_])"
    r"|no\s+commits?\s+(?:were\s+)?lost|nothing\s+(?:was\s+)?lost"
    r"|(?:hasn't|has\s+not|wasn't|was\s+not)\s+been\s+merged"
    r"|remains?\s+open",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"#(\d+)")


def detect_unchanged_claims(text: str) -> list[str]:
    """Return distinct `#N` tokens asserted as unchanged/still-open/no-loss,
    ON THE SAME LINE as the number (co-occurrence narrowing per #869 scope).
    Unlike `detect_claims`, this path is NOT skipped by `_NEGATION_RE` — the
    negative surface form (없음/not merged/still open) IS the claim, mirroring
    `runtime-state-claim-gate`'s "isolation" kind, which matches negative
    surface forms directly instead of treating negation as a generic
    suppressor."""
    numbers: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not _UNCHANGED_STATE_RE.search(line):
            continue
        for m in _NUMBER_RE.finditer(line):
            tag = f"#{m.group(1)}"
            if tag not in numbers:
                numbers.append(tag)
    return numbers

# ---------------------------------------------------------------------------
# Evidence detection — a fresh state query in the recent transcript.
# ---------------------------------------------------------------------------

_GH_EVIDENCE_RE = re.compile(r"\bgh\b[^|;&\n]*\b(pr|issue)\b\s+[a-z]", re.IGNORECASE)
_MCP_GH_EVIDENCE_RE = re.compile(r"pull_request|issue|merge|pr_", re.IGNORECASE)
# The queried number must sit at the TARGET position of a READ subcommand.
# Whole-command scanning cleared `#864` off `gh pr view 111 --repo org/p-864`
# (number in an unrelated slug) and off `gh pr merge 864` (a mutation, which
# tells you nothing about the post-merge state the claim asserts).
_GH_READ_VERBS = frozenset({"view", "list", "status", "checks", "diff"})
_GH_TARGET_RE = re.compile(
    r"\bgh\b(?P<mid>[^|;&\n]*?)\b(?:pr|issue)\s+(?P<verb>[a-z-]+)(?P<rest>[^|;&\n]*)",
    re.IGNORECASE,
)
_TARGET_NUM_RE = re.compile(r"(?:^|/)(?P<n>[0-9]+)$")
# GitHub MCP reads only — a merge/create/close tool is a mutation, same
# exclusion as the CLI verb allow-list above.
_MCP_GH_READ_RE = re.compile(r"^mcp__github__(?:get|list|search|read)_|_read$", re.IGNORECASE)
_MCP_NUMBER_KEY_RE = re.compile(
    r"^(?:number|pull_?number|issue_?number|pr_?number)$", re.IGNORECASE
)

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


def _bash_reads_number(cmd: str, digits: str) -> bool:
    """True if `cmd` is a gh READ of exactly `digits`.

    Positional, not substring: the number must appear as a bare argument or
    a PR/issue URL ending in `/864`, and the subcommand must be a read verb.
    `gh pr view 1864`, `gh pr view 111 --repo org/project-864` (digits inside
    a slug never follow a `/`) and `gh pr merge 864 --squash` all fail.
    Flag *values* are scanned too rather than assuming the target is the
    first positional — `gh pr view --json state 864` is a real shape and a
    value-taking-flag table would be its own drift surface.
    """
    for m in _GH_TARGET_RE.finditer(cmd):
        if m.group("verb").lower() not in _GH_READ_VERBS:
            continue
        for token in m.group("rest").split():
            if token.startswith("-"):
                continue
            hit = _TARGET_NUM_RE.search(token.rstrip("/"))
            if hit and hit.group("n") == digits:
                return True
    return False


def _mcp_reads_number(name: str, inp: dict, digits: str) -> bool:
    """True if a GitHub MCP READ tool was called with `digits` in a
    PR/issue *number* field. An owner/repo slug carrying the digits, and any
    mutation tool (merge/create/close), do not count."""
    if not _MCP_GH_READ_RE.search(name):
        return False
    for key, value in inp.items():
        if _MCP_NUMBER_KEY_RE.match(str(key)) and str(value) == digits:
            return True
    return False


def has_fresh_query_for_number(events: list[dict], number: str) -> bool:
    """True if a recent assistant `gh pr|issue` Bash command or GitHub MCP
    call explicitly references `number` (e.g. `gh pr view 864 --json state`
    for claim `#864`). Narrower than `has_fresh_state_query`: a query for a
    DIFFERENT PR/issue does not back a negative-polarity claim about THIS
    number (#869) — a blanket "any gh query fires recently" check would have
    let the incident's stale re-assertion pass if any unrelated `gh pr view`
    call happened to appear in the window."""
    digits = number.lstrip("#")
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
            if name == "Bash":
                inp = block.get("input", {})
                cmd = inp.get("command", "") if isinstance(inp, dict) else ""
                if cmd and _bash_reads_number(cmd, digits):
                    return True
            elif name.startswith("mcp__github__"):
                inp = block.get("input", {})
                if isinstance(inp, dict) and _mcp_reads_number(name, inp, digits):
                    return True
    return False


def _advisory(kinds: list[str], stale_unchanged: list[str] | None = None) -> str:
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
    if stale_unchanged:
        msg += (
            f"{_PREFIX} final message asserts {', '.join(stale_unchanged)} is "
            "still-open / unchanged / no-loss (여전히 OPEN / 유실 없음 / not "
            "merged) but no fresh `gh pr|issue` query FOR THAT NUMBER appears "
            "in the recent transcript.\n"
            f"{_PREFIX} Rule: a persistence claim goes stale exactly like a "
            "change claim — re-fetch the specific number "
            "(`gh pr view <N> --json state` / `gh issue view <N>`) before "
            "repeating a pre-compaction snapshot (issue #869: \"PR #864 는 "
            "여전히 OPEN\" was repeated across turns while the PR had already "
            "merged).\n"
        )
    return msg + f"{_PREFIX} bypass: {_BYPASS_ENV}=1\n"


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

    # Stop reads the session transcript; SubagentStop reads the subagent's own
    # (`agent_transcript_path`), whose sidechain markers are dropped so the
    # shared readers do not treat a file that is entirely the subagent's as
    # somebody else's events — see `_transcript.resolve_stop_transcript`.
    transcript_path, is_agent = resolve_stop_transcript(payload)
    if not transcript_path or not os.path.isfile(transcript_path):
        return 0

    # Bounded tail rather than the whole transcript (#1076). min_events
    # covers the _EVIDENCE_WINDOW slices below, which read past the turn.
    events = load_recent_events(
        transcript_path, min_events=_EVIDENCE_WINDOW, drop_sidechain=is_agent
    )
    if not events:
        return 0

    turn = get_current_turn(events)
    last_text = stop_last_assistant_text(payload, turn) if turn else ""
    if not last_text:
        return 0

    kinds = detect_claims(last_text)
    unchanged_numbers = detect_unchanged_claims(last_text)
    if not kinds and not unchanged_numbers:
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

    # Negative-polarity persistence claims (#869): cleared per-NUMBER, not by
    # any generic gh query — a query about a different PR/issue must not
    # clear a stale claim about this one.
    stale_unchanged = [
        n for n in unchanged_numbers if not has_fresh_query_for_number(events, n)
    ]

    if not unbacked and not stale_unchanged:
        return 0

    if os.environ.get(_STRICT_ENV, "").strip() == "1":
        emit_stop_block(_advisory(unbacked, stale_unchanged))
        decision = _fire_ledger.DECISION_BLOCK
    else:
        emit_stop_advisory(_advisory(unbacked, stale_unchanged))
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
