#!/usr/bin/env python3
"""Stop-hook gate: cross-session blame attribution as a report's opening move.

Background (issue #1391): the ruleset forbids opening a report by attributing a
fault to another session — attribution may be stated once as a routing fact,
never as the opening move, and never as the reason an item goes unhandled. In
the motivating session that rule was broken in the first sentence of the first
report while its text sat in context the whole time. PreToolUse cannot see
in-flight assistant prose, so Stop is the only surface that sees the opening.

Detection is positional and lexical only — no judgement about meaning. The first
paragraph must carry BOTH a subject referent (session / worktree / agent) and a
non-ownership token; either alone is ordinary prose. Position is the whole
discriminator, because the rule permits the same sentence later as a routing
fact, and a whole-message scan would be the false-positive generator that the
sibling `negative-existence-verdict-gate` measured at 59.5% of sessions before
narrowing to 1.1%.

Two earlier designs were falsified against the motivating paragraph itself and
are recorded in the spec so they are not re-derived: keying on the ruleset's own
list of forbidden phrases (the printed examples are not what gets written), and
clearing on finding/fix vocabulary in the same paragraph (the violating
paragraph asserts ownership and attributes in the same breath).

Advisory by default (stdout `{"systemMessage": ...}` + exit 0, the role's
standard since issue #647 H3); `PRAXIS_JOINT_LIABILITY_STRICT=1` escalates to
`{"decision": "block", "reason": ...}`, which is the only tier whose text
reaches the model (issue #1265). Fully fail-open; bypass with
`PRAXIS_JOINT_LIABILITY_BYPASS=1`.
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
    read_last_user_message,
    resolve_stop_transcript,
    stop_last_assistant_text,
)

_HOOK_NAME = "joint-liability-attribution-gate"
_ROLE = "completion-verify"
_STRICT_ENV = "PRAXIS_JOINT_LIABILITY_STRICT"
_BYPASS_ENV = "PRAXIS_JOINT_LIABILITY_BYPASS"

# Axis 1 — who the sentence is about. Korean matches are plain substrings
# (no word boundary exists); the ASCII ones use a letter-class lookaround
# because `\b` is Unicode-aware and would not separate `agent` from adjacent
# Hangul in mixed text.
_SUBJECT = re.compile(
    r"세션|워크트리|에이전트|(?<![a-z])(?:session|worktree|agent)s?(?![a-z])",
    re.IGNORECASE,
)

# Axis 2 — the disowning move. Not a phrase list: the ruleset's printed
# examples ("제 세션에서 한 작업이 아닙니다") are illustrations, and the
# sentence that actually shipped was "이 세션에는 제가 한 작업 기록이 없어서",
# which shares no phrase with any of them.
_DISOWN = re.compile(
    r"아닙니다|아니라|아닌|없어서|없습니다|없었|담당이|"
    r"(?<![a-z])(?:not\s+mine|other|another|different|someone\s+else)(?![a-z])",
    re.IGNORECASE,
)

# The rule's own carve-out: attribution is permitted when the user asked for
# routing. Cleared from the most recent user message only, mirroring
# `block-ask-end-option`.
_ROUTING_REQUEST = re.compile(
    r"누가|어느\s*세션|어디서|누구|담당|라우팅|"
    r"(?<![a-z])(?:who|which\s+session|where|routing|attribut\w*)(?![a-z])",
    re.IGNORECASE,
)

_FENCE = re.compile(r"^\s*(?:```|~~~)")


def first_paragraph(text: str) -> str:
    """Return the opening prose block, or "" when there is none.

    Headings and fenced code are not the opening move — a report that starts
    with `### Status` makes its first claim in the block after it. Skipping
    them is what keeps the positional test aimed at prose.
    """
    if not text:
        return ""
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        stripped = block.strip()
        if not stripped or _FENCE.match(stripped):
            continue
        # A block that is nothing but ATX headings carries no claim.
        if all(ln.lstrip().startswith("#") for ln in stripped.splitlines()):
            continue
        return stripped
    return ""


def is_opening_attribution(paragraph: str) -> bool:
    """True when the opening paragraph both names a sibling and disowns it."""
    if not paragraph:
        return False
    return bool(_SUBJECT.search(paragraph)) and bool(_DISOWN.search(paragraph))


def user_asked_for_routing(user_message: str | None) -> bool:
    """True when the user's own last message asked whose work this was."""
    if not user_message:
        return False
    return bool(_ROUTING_REQUEST.search(user_message))


def _advisory() -> str:
    return (
        "Joint liability: the report opens by attributing work to another "
        "session. State attribution once as a routing fact, never as the "
        "opening move — and never as the reason an item goes unhandled.\n"
        "연대 책임 — 보고의 첫 문단이 다른 세션으로 귀속을 돌리고 있습니다. "
        "귀속은 라우팅 사실로 한 번만, 첫 수로는 쓰지 않습니다. 보고를 받은 "
        "세션이 수정을 소유합니다: 기록 → 수정 또는 라우팅 → 동일 계열 자체 "
        "점검 → 한 줄 보고.\n"
        "Reference: hooks/completion-verify/"
        "joint-liability-attribution-gate/spec.md"
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

    # load_stop_turn takes the payload, not a path: on SubagentStop it picks the
    # subagent's own transcript and drops sidechain markers itself. The path is
    # still needed separately, for the most-recent-user-message clear below.
    transcript_path, _is_agent = resolve_stop_transcript(payload)
    last_text = stop_last_assistant_text(payload, load_stop_turn(payload))
    if not is_opening_attribution(first_paragraph(last_text)):
        return 0

    if transcript_path and user_asked_for_routing(
        read_last_user_message(transcript_path)
    ):
        return 0

    if os.environ.get(_STRICT_ENV, "").strip() == "1":
        emit_stop_block(_advisory())
        decision = _fire_ledger.DECISION_BLOCK
    else:
        emit_stop_advisory(_advisory())
        decision = _fire_ledger.DECISION_ADVISE

    # Stop hooks signal via stdout while exiting 0, so @fail_open's coarse path
    # would record only "pass"; record the real decision and drop the duplicate
    # so aggregate_fires() does not count one emit twice.
    session_id = payload.get("session_id")
    if _fire_ledger.record_session_fire(
        _HOOK_NAME,
        _ROLE,
        decision,
        session_id if isinstance(session_id, str) else "",
        "Stop",
    ):
        _fire_ledger.suppress_coarse_duplicate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
