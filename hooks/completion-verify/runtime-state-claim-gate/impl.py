#!/usr/bin/env python3
"""Stop-hook advisory: gate unprobed runtime/execution state claims.

Background (issue #809): an Agent launch with `isolation: "remote"` silently
fell back to local execution. When the user asked "remote로 처리한다며?" and
"지금 서브에이전트 돌아가는 건 뭔데?", the assistant asserted TWICE — with zero
probes — that the subagent was "running in a cloud sandbox on a fresh clone,
not touching the local worktree". It was writing files into the local worktree
at that moment; the user caught it first via editor diagnostics. The probe
cost was one `git status`.

The prompt layer had already failed: the fabricated-rationale strike memory,
the full-code-path family, and CLAUDE.md "runtime behavior claims only after
actual execution + evidence" were all loaded. The gap is structural — every
existing falsification gate anchors on an *action* event (issue create,
recommendation surface, merge claim), while a conversational answer describing
runtime state is also a claim-emission point. This is the runtime-state sibling
of merge-state-claim-gate (#503): a Stop hook sees the final assistant text —
the exact surface PreToolUse hooks cannot see.

This hook scans the final assistant message for a present-state runtime
assertion (a runtime SUBJECT and a state CLAIM on the same line) and, if the
current turn contains no probe tool_use (Bash / Read / Grep / Glob / Task* /
MCP read), emits an advisory. Advisory by default (stdout
`{"systemMessage": ...}` JSON + exit 0 — issue #647 H3 standard);
`PRAXIS_RUNTIME_CLAIM_STRICT=1` escalates to `{"decision": "block", ...}`
(re-prompts the model to probe). Fully fail-open; bypass with
`PRAXIS_RUNTIME_CLAIM_BYPASS=1`.

Second, unrelated gap closed by issue #1062: the trigger above fires only when
the USER asks about runtime state. The actual path a partial measurement took
to a public PR anchor (issue #1062, PR #1058 rev4) was voluntary
RE-STATEMENT of an already-uttered verdict number ("FAIL 0") with its scope
qualifier ("348줄 중") silently dropped over several turns, while an unrelated
progress number (log line count) kept changing beside it — making the
sentence look fresh. This hook additionally scans the final message for a
verdict-count claim (`실패 N` / `FAIL N` / bare `통과`) that (a) was already
stated earlier THIS session, (b) carries no scope qualifier on its line this
time, and (c) has no fresh probe in the current turn — same evidence gate as
the running/isolation claim above.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import (  # type: ignore[import-not-found]  # noqa: E402
    emit_stop_advisory,
    emit_stop_block,
)
import _fire_ledger  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _transcript import (  # type: ignore[import-not-found]  # noqa: E402
    extract_last_assistant_text,
    get_current_turn,
    load_transcript,
)

_PREFIX = "[runtime-state-claim-gate]"
_BYPASS_ENV = "PRAXIS_RUNTIME_CLAIM_BYPASS"
_STRICT_ENV = "PRAXIS_RUNTIME_CLAIM_STRICT"
_HOOK_NAME = "runtime-state-claim-gate"
_ROLE = "completion-verify"

# ---------------------------------------------------------------------------
# Claim detection — a claim needs a runtime SUBJECT token and a present-state
# CLAIM token on the same line (line-localization mirrors
# merge-state-claim-gate; cuts false positives on long final messages).
# EN tokens use lookarounds, not \b: Python \b puts no boundary between Hangul
# and ASCII word chars, so "\bagent\b" fails on "agent가" (same trap as the
# sibling's \bPR\b fix).
# ---------------------------------------------------------------------------

_SUBJECT_RE = re.compile(
    r"에이전트|서브에이전트|프로세스|컨테이너|클라우드|원격|로컬|샌드박스|백그라운드|워커"
    r"|(?<![A-Za-z0-9_])(?:sub)?agents?(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])workers?(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])process(?:es)?(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])containers?(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])pods?(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])jobs?(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])cloud(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])remote(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])local(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])sandbox(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])background(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])DAG(?![A-Za-z0-9_])",
    re.IGNORECASE,
)

# Two claim kinds. Unlike the sibling, negative-form sentences are NOT a
# negation-skip here: "로컬은 건드리지 않습니다" IS the claim (an isolation
# assertion about what the process does not do) — the motivating incident is
# exactly that sentence. So there is no generic negation suppressor; instead
# "running" has a future/question suppressor and "isolation" is matched
# directly on its negative surface forms.
_CLAIM_KINDS: list[tuple[str, re.Pattern[str]]] = [
    # Present-state "it is running / it runs on X" assertions.
    ("running", re.compile(
        r"돌고 있|돌아가|에서 돈다|에서 도는|실행 중|실행되고|동작 중|작동 중"
        r"|(?<![A-Za-z0-9_])running(?![A-Za-z0-9_])"
        r"|(?<![A-Za-z0-9_])executing(?![A-Za-z0-9_])"
        r"|(?<![A-Za-z0-9_])runs? on(?![A-Za-z0-9_])"
        r"|is live",
        re.IGNORECASE,
    )),
    # Isolation/non-interaction assertions — negative surface forms that are
    # themselves claims about runtime behavior.
    ("isolation", re.compile(
        r"건드리지 (않|못|말)|사용하지 않|쓰지 않|접근할 수 없|접근하지 못|접근 불가"
        r"|영향(을|이)?\s*(주지 않|없)|미접촉|격리"
        r"|(?:does not|doesn't|cannot|can't|won't|will not)\s+(?:touch|use|access|see|modify)"
        r"|(?:is|are)(?:n['’]t|\s+not)\s+(?:touch|us|access|see|modify)\w*"
        r"|no access to|isolated from|untouched",
        re.IGNORECASE,
    )),
]

# Future intent / question suppressors — apply to "running" only. A future
# sentence ("will run", "실행하겠습니다") is intent, not a state claim; a
# question ("돌고 있나요?") is the user's surface, not an assertion. They must
# NOT suppress "isolation": "건드리지 않을 겁니다" projects an unverified
# isolation guarantee into the future and still needs a probe.
_FUTURE_RE = re.compile(
    r"하겠습|할게요|할 예정|시작하겠|(?<![A-Za-z0-9_])will(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])going to(?![A-Za-z0-9_])|I'll",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(r"[?？]\s*$|할까요|인가요|습니까|나요\s*[?？]")

# Suppressors are scoped per sentence-segment, not per line: "The agent is
# running locally; I will stop it now." carries a real state claim in the
# first clause — the "will" in the second must not suppress it (codex P1).
_SEGMENT_SPLIT_RE = re.compile(r"(?<=[.!?;。？！])\s+")


def detect_claims(text: str) -> list[str]:
    """Return the distinct runtime-claim kinds asserted in `text` (subject +
    state claim in the same sentence segment). Quoted lines (`>`) are skipped;
    future intent and questions suppress "running" but not "isolation"."""
    kinds: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(">"):
            continue
        for segment in _SEGMENT_SPLIT_RE.split(line):
            if not segment or not _SUBJECT_RE.search(segment):
                continue
            is_question = bool(_QUESTION_RE.search(segment))
            is_future = bool(_FUTURE_RE.search(segment))
            for kind, pat in _CLAIM_KINDS:
                if kind in kinds:
                    continue
                if kind == "running" and (is_question or is_future):
                    continue
                if kind == "isolation" and is_question:
                    continue
                if pat.search(segment):
                    kinds.append(kind)
    return kinds


# ---------------------------------------------------------------------------
# Verdict-restatement detection (issue #1062) — a DIFFERENT claim shape from
# the running/isolation claims above: not "is X currently happening" but "the
# number I already measured, said again". A claim key is (kind, number),
# e.g. "fail:0"; a bare pass/fail word with no number ("통과") keys as
# "pass:bare". Only FAIL/PASS-adjacent vocabulary counts — a standalone "N건"
# with no fail/pass word nearby is deliberately NOT matched (too broad: "이슈
# 3건" is not a verdict count) — narrowest change that closes the gap named in
# the issue, no invented "generic count" abstraction.
# ---------------------------------------------------------------------------

_FAIL_WORD = r"(?:FAILs?|fail(?:ed)?|실패|오류|에러|error)"
_PASS_WORD = r"(?:PASS(?:ED)?|통과|성공|success)"

# Reversed order ("0 FAILED", "0건 실패"): only whitespace and a Korean
# counter suffix may bridge the number to the word. A permissive `\D{0,3}`
# lets a range denominator's trailing "중" bridge instead — on "120/348 중
# FAIL 0" the reversed alternative matches "348 중 FAIL" FIRST, stores
# `fail:348`, and consumes the real "FAIL 0", so a later unqualified "실패 0"
# finds no prior mention and the gate never fires.
_REV_GAP = r"\s*(?:건|개)?\s*"

# A count attached to a fail/pass word, either order, small gap allowed
# ("FAILs: 0", "0 FAILED", "실패 0", "0건 실패").
_VERDICT_NUM_RE = re.compile(
    rf"{_FAIL_WORD}\D{{0,6}}?(\d+)|(\d+){_REV_GAP}{_FAIL_WORD}"
    rf"|{_PASS_WORD}\D{{0,6}}?(\d+)|(\d+){_REV_GAP}{_PASS_WORD}",
    re.IGNORECASE,
)
# A bare pass claim carrying no adjacent number ("통과", "PASSED").
_VERDICT_BARE_RE = re.compile(rf"{_PASS_WORD}(?!\D{{0,3}}\d)", re.IGNORECASE)

# Scope-qualifier phrases naming the measured artifact's coverage — "348줄
# 중", "of 348 lines", "120/348", "34%". A line carrying one of these is
# transparent about what was actually covered and stays silent even when the
# same number was already stated (the motivating incident's own first
# restatement, "348줄 중 FAIL 0", must stay silent per the issue's own
# false-positive check).
_QUALIFIER_RE = re.compile(
    r"\d+\s*(?:줄|line|lines)\s*(?:중|of)"
    r"|of\s+\d+\s*(?:lines?|줄)?"
    r"|\d+\s*/\s*\d+"
    r"|\d+\s*%",
    re.IGNORECASE,
)


def _claim_key_from_match(m: "re.Match[str]") -> str | None:
    if m.group(1) is not None:
        return f"fail:{m.group(1)}"
    if m.group(2) is not None:
        return f"fail:{m.group(2)}"
    if m.group(3) is not None:
        return f"pass:{m.group(3)}"
    if m.group(4) is not None:
        return f"pass:{m.group(4)}"
    return None


def _humanize_key(key: str) -> str:
    kind, _, num = key.partition(":")
    word = "FAIL" if kind == "fail" else "PASS"
    return word if num == "bare" else f"{word} {num}"


def extract_verdict_claims(text: str) -> list[dict]:
    """Return `{"key", "qualified"}` for each verdict-count claim in `text`,
    scanned per line (a scope qualifier is read from the same line the
    number appears on). Quoted lines (`>`) are skipped, matching
    `detect_claims`."""
    claims: list[dict] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(">"):
            continue
        qualified = bool(_QUALIFIER_RE.search(line))
        seen: set[str] = set()
        for m in _VERDICT_NUM_RE.finditer(line):
            key = _claim_key_from_match(m)
            if key and key not in seen:
                seen.add(key)
                claims.append({"key": key, "qualified": qualified})
        if _VERDICT_BARE_RE.search(line) and "pass:bare" not in seen:
            claims.append({"key": "pass:bare", "qualified": qualified})
    return claims


def collect_prior_verdict_mentions(prior_events: list[dict]) -> dict[str, str | None]:
    """Return claim key -> earliest ISO timestamp (or None) it was stated in
    `prior_events` (assistant text, any turn before the current one,
    qualified or not — a qualified prior mention still means the number was
    already said). ISO8601 `Z`-suffixed timestamps sort correctly as strings,
    so no datetime parsing is needed to track the minimum."""
    mentions: dict[str, str | None] = {}
    for ev in prior_events:
        msg = ev.get("message", {})
        if not isinstance(msg, dict) or msg.get("role") != "assistant" or ev.get("isSidechain"):
            continue
        content = msg.get("content", [])
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "\n".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        if not text:
            continue
        ts = ev.get("timestamp")
        ts = ts if isinstance(ts, str) and ts else None
        for claim in extract_verdict_claims(text):
            key = claim["key"]
            if key not in mentions:
                mentions[key] = ts
            elif ts and (mentions[key] is None or ts < mentions[key]):
                mentions[key] = ts
    return mentions


def _last_assistant_timestamp(turn: list[dict]) -> str | None:
    ts: str | None = None
    for ev in turn:
        msg = ev.get("message", {})
        if isinstance(msg, dict) and msg.get("role") == "assistant" and not ev.get("isSidechain"):
            t = ev.get("timestamp")
            if isinstance(t, str) and t:
                ts = t
    return ts


def _elapsed_minutes(ts_from: str | None, ts_to: str | None) -> str:
    """Minutes between two ISO8601 timestamps, or "unknown" if either is
    missing or unparseable — the message degrades gracefully rather than
    fabricating a number (transcripts predating this hook carry no
    `timestamp` gap this can't cover)."""
    if not ts_from or not ts_to:
        return "unknown"
    try:
        a = datetime.fromisoformat(ts_from.replace("Z", "+00:00"))
        b = datetime.fromisoformat(ts_to.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    return f"{abs((b - a).total_seconds()) / 60:.1f}"


def detect_verdict_restatement(
    last_text: str, prior_events: list[dict]
) -> tuple[list[str], dict[str, str | None]]:
    """Return (restated claim keys, prior-mention timestamps) for verdict
    numbers in `last_text` that are (a) unqualified here and (b) were already
    stated somewhere earlier this session."""
    mentions = collect_prior_verdict_mentions(prior_events)
    if not mentions:
        return [], mentions
    restated: list[str] = []
    for claim in extract_verdict_claims(last_text):
        if claim["qualified"]:
            continue
        key = claim["key"]
        if key in mentions and key not in restated:
            restated.append(key)
    return restated, mentions


# ---------------------------------------------------------------------------
# Evidence detection — a probe tool_use in the CURRENT turn. Turn-scoped (not
# the sibling's 80-event window): runtime state goes stale across turns, and
# the incident turns contained zero tool calls of any kind. Any read-class
# tool counts (advisory hook; classifying Bash commands by probe-ness would
# over-engineer a fail-open gate). Write/Edit/Agent/Skill/AskUserQuestion are
# deliberately excluded — launching or mutating is not observing.
# ---------------------------------------------------------------------------

_PROBE_TOOLS = frozenset(
    {"Bash", "Read", "Grep", "Glob", "LSP", "TaskList", "TaskGet", "TaskOutput", "Monitor"}
)


def has_probe_in_turn(turn: list[dict]) -> bool:
    """True if the current turn contains a probe-class assistant tool_use."""
    for ev in turn:
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
            if name in _PROBE_TOOLS or name.startswith("mcp__"):
                return True
    return False


def _advisory_running(kinds: list[str]) -> str:
    return (
        f"{_PREFIX} final message asserts a runtime/execution state "
        f"({'/'.join(kinds)}: e.g. \"X is running in Y\" / \"로컬은 건드리지 "
        "않습니다\") but the current turn contains NO probe tool call.\n"
        f"{_PREFIX} Rule: observe the state you are about to assert (git "
        "status on the worktree, task output, ps/docker ps/kubectl get, an "
        "MCP read) and cite the result — or label the sentence as unverified "
        "intent (\"요청 기준으로는 X입니다. 실측은 하지 않았습니다\"). "
        "Launch success does not reveal WHERE something runs: remote/isolation "
        "modes fall back silently (issue #809).\n"
    )


def _advisory_restatement(restated: list[str], mentions: dict[str, str | None], now: str | None) -> str:
    lines = [
        f"{_PREFIX} the final message restates a verdict number already "
        "stated earlier this session, WITHOUT a scope qualifier (\"N줄 중\" "
        "/ \"of N lines\"), and the current turn contains NO probe tool call:"
    ]
    for key in restated:
        elapsed = _elapsed_minutes(mentions.get(key), now)
        lines.append(
            f"{_PREFIX}   {_humanize_key(key)} — last stated {elapsed} min "
            "ago. Re-measure against the CURRENT artifact before restating, "
            "or keep the scope qualifier every time (\"X줄 중 Y\")."
        )
    lines.append(
        f"{_PREFIX} Rule: a changing progress number beside a frozen verdict "
        "number (log length growing, FAIL count fixed) makes the sentence "
        "look fresh even when only the frozen half is being repeated "
        "(issue #1062).\n"
    )
    return "\n".join(lines)


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
    prior_events = events[: len(events) - len(turn)] if turn else events
    restated, mentions = detect_verdict_restatement(last_text, prior_events)
    if not kinds and not restated:
        return 0

    if has_probe_in_turn(turn):
        return 0

    message = ""
    if kinds:
        message += _advisory_running(kinds)
    if restated:
        message += _advisory_restatement(restated, mentions, _last_assistant_timestamp(turn))
    message += f"{_PREFIX} bypass: {_BYPASS_ENV}=1\n"

    if os.environ.get(_STRICT_ENV, "").strip() == "1":
        emit_stop_block(message)
        decision = _fire_ledger.DECISION_BLOCK
    else:
        emit_stop_advisory(message)
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
