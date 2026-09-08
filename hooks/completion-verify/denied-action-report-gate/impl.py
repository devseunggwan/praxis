#!/usr/bin/env python3
"""Stop-hook gate: a refused tool call this turn that the report never mentions.

Background (issue #1392). `retrospect-mix-check` Gate-12 (#1013) already encodes
this rule and states its mechanism: a refused action "has no outcome, so it
leaves no error, no correction and no confession, and selection by ease of
recall never reaches it". But Gate-12 keys on the retrospect Stage-3 report's
structural fences, so an ordinary closing report is uncovered, and the
always-loaded rule that failures are ranked by damage has no gate behind it
there.

Nothing here judges meaning. A structurally rejected tool call is an objective
event (`_transcript.scan_user_rejections`: `toolDenialKind`, `is_error: true`
and the runtime's fixed refusal sentence, three co-agreeing markers), and
whether the report acknowledges it is a lexical test.

Scope is the **current turn**, by intersecting the session-wide scan with the
tool_use ids this turn produced results for. The cursor cannot do that job: it
resumes a byte offset while the reducer state accumulates, so a cursored scan
still answers for the whole session, and firing on a refusal from fifty turns
ago would be the false-positive shape the sibling `negative-existence-verdict-
gate` measured at 59.5% of sessions before narrowing.

Advisory by default; `PRAXIS_DENIED_ACTION_STRICT=1` escalates to block, the
only tier whose text reaches the model (#1265). Fail-open; bypass with
`PRAXIS_DENIED_ACTION_BYPASS=1`.
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
    load_stop_turn,
    resolve_stop_transcript,
    scan_cursor_path,
    scan_user_rejections,
    stop_last_assistant_text,
)

_HOOK_NAME = "denied-action-report-gate"
_ROLE = "completion-verify"
_STRICT_ENV = "PRAXIS_DENIED_ACTION_STRICT"
_BYPASS_ENV = "PRAXIS_DENIED_ACTION_BYPASS"

# A refused AskUserQuestion is the user dismissing the menu and answering in
# their own words. Nothing was prevented and nothing goes unreported, so it is
# not the class this gate is about — and it is half the corpus: 7 of the 14
# refusals across 60 recent sessions. Including it would make most fires false.
_EXCLUDED_TOOLS = frozenset({"AskUserQuestion"})

# Acknowledgement, not identity. Measured against the same corpus, matching on
# the refused call's own identifier tokens cleared rows by incidental overlap
# (a path fragment shared with unrelated prose) while missing every report that
# said `취소했습니다` in plain Korean — wrong in both directions. What a report
# that owns the refusal always carries is a word for the refusal itself.
_ACK = re.compile(
    r"거부|거절|차단|반려|미승인|승인|취소|중단|철회|보류|멈추|안\s*했|못\s*했|"
    r"(?<![a-z])(?:denied|deny|rejected|reject|blocked|refused|declined|"
    r"cancell?ed|aborted|stopped|skipped|not\s+approved|permission)(?![a-z])",
    re.IGNORECASE,
)

_TOOL_NAME = re.compile(r"[A-Za-z0-9_]{3,}")


def turn_tool_use_ids(turn: list[dict]) -> set[str]:
    """Every tool_use id this turn produced a result for.

    Rejections are recorded as tool_result blocks, so their ids are what scopes
    a session-wide scan down to the turn that is stopping.
    """
    ids: set[str] = set()
    for block in _content_blocks(turn):
        tid = block.get("tool_use_id")
        if isinstance(tid, str) and tid:
            ids.add(tid)
    return ids


def turn_has_error_result(turn: list[dict]) -> bool:
    """Whether any tool_result this turn is an error.

    A necessary condition for a refusal, and the gate on the indeterminate
    branch below — it keeps a scan that could not catch up from speaking on
    turns that plainly had nothing to refuse.
    """
    return any(b.get("is_error") is True for b in _content_blocks(turn))


def _content_blocks(turn: list[dict]):
    for ev in turn or []:
        message = ev.get("message") if isinstance(ev, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict):
                yield block


def is_acknowledged(rejection: dict, message: str, sole: bool = True) -> bool:
    """True when the report owns *this* refusal.

    Naming the refused tool always counts. A bare refusal word counts only when
    it is the turn's `sole` refusal, because a word has one referent: with two
    refusals in a turn, "the push was denied" accounts for the push and says
    nothing about the other call, so reading it as covering both would let one
    acknowledgement retire every omission beside it.
    """
    if not message:
        return False
    name = (rejection.get("tool_name") or "").strip()
    if name and name in set(_TOOL_NAME.findall(message)):
        return True
    return sole and bool(_ACK.search(message))


def unreported(rejections: list[dict], turn_ids: set[str], message: str) -> list[dict]:
    """This turn's refusals, minus the excluded class, that the report is silent on."""
    candidates = [
        r
        for r in rejections
        if r.get("tool_use_id") in turn_ids
        and r.get("tool_name") not in _EXCLUDED_TOOLS
    ]
    sole = len(candidates) == 1
    return [r for r in candidates if not is_acknowledged(r, message, sole)]


def _advisory(items: list[dict]) -> str:
    names = ", ".join(sorted({(r.get("tool_name") or "?") for r in items})) or "?"
    return (
        f"Denied action missing from the report: {len(items)} tool call(s) were "
        f"refused this turn ({names}) and the final message never says so. Rank "
        "what you report by damage, not by what is easiest to recall — a refused "
        "action leaves no error, no correction and no confession, so it is "
        "exactly what recall misses.\n"
        f"이번 턴에 거부된 도구 호출 {len(items)}건({names})을 최종 보고가 "
        "언급하지 않았습니다. 보고 순위는 고백하기 쉬운 순이 아니라 손해 순입니다.\n"
        "Reference: hooks/completion-verify/denied-action-report-gate/spec.md"
    )


def _indeterminate() -> str:
    return (
        "Denied-action scan is indeterminate: this turn carries an errored tool "
        "result, but the rejection scan did not catch up to the end of the "
        "transcript, so it cannot say whether a refusal is among them. Zero here "
        "is what an unfinished scan returns, not a finding (issue #1231) — write "
        "it as unverified rather than as an absence.\n"
        "거부 스캔이 미완입니다 — 0건은 '없음'이 아니라 '못 읽음'입니다. "
        "부재로 보고하지 말고 미확인으로 남기십시오.\n"
        "Reference: hooks/completion-verify/denied-action-report-gate/spec.md"
    )


@fail_open
def main() -> int:
    if os.environ.get(_BYPASS_ENV, "").strip() == "1":
        return 0

    payload = read_payload()
    if not isinstance(payload, dict):
        return 0
    if payload.get("stop_hook_active"):
        return 0

    transcript_path, _is_agent = resolve_stop_transcript(payload)
    if not transcript_path or not os.path.isfile(transcript_path):
        return 0

    session_id = payload.get("session_id")
    session_id = session_id if isinstance(session_id, str) else ""
    rejections = scan_user_rejections(
        transcript_path,
        cursor_path=scan_cursor_path(_HOOK_NAME, session_id),
    )
    turn = load_stop_turn(payload)

    # None is INDETERMINATE, never "no rejections" — the scan did not reach the
    # end of the file. Folding it into [] would silence the gate exactly on the
    # long sessions where refusals accumulate.
    if rejections is None:
        if not turn_has_error_result(turn):
            return 0
        message = _indeterminate()
    else:
        items = unreported(
            rejections, turn_tool_use_ids(turn), stop_last_assistant_text(payload, turn)
        )
        if not items:
            return 0
        message = _advisory(items)

    if os.environ.get(_STRICT_ENV, "").strip() == "1":
        emit_stop_block(message)
        decision = _fire_ledger.DECISION_BLOCK
    else:
        emit_stop_advisory(message)
        decision = _fire_ledger.DECISION_ADVISE

    if _fire_ledger.record_session_fire(
        _HOOK_NAME, _ROLE, decision, session_id, "Stop"
    ):
        _fire_ledger.suppress_coarse_duplicate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
