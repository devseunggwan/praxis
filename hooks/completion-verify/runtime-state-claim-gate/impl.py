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
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
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

_PREFIX = "[runtime-state-claim-gate]"
_BYPASS_ENV = "PRAXIS_RUNTIME_CLAIM_BYPASS"
_STRICT_ENV = "PRAXIS_RUNTIME_CLAIM_STRICT"

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


def _advisory(kinds: list[str]) -> str:
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

    kinds = detect_claims(last_text)
    if not kinds:
        return 0

    if has_probe_in_turn(turn):
        return 0

    if os.environ.get(_STRICT_ENV, "").strip() == "1":
        emit_stop_block(_advisory(kinds))
    else:
        emit_stop_advisory(_advisory(kinds))
    return 0


if __name__ == "__main__":
    sys.exit(main())
