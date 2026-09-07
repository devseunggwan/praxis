#!/usr/bin/env python3
"""Stop hook: negative-existence verdict without an `Enumerated:` line.

Issue #804. `#346` added a critic pre-lock probe gate to codex-review-wrap
Step 5g — a critic surfacing "X does not exist / is fabricated / is unused"
must cite a live `Probe:` first. The blind spot is the *critic role only*:
the identical failure in the agent's OWN verdict is not gated at all.

Motivating case (Hub #3981). An acceptance criterion read (verbatim):

    - [ ] SQL 파일 → 영향 테스트 매핑 규칙 실재 여부 확인 (없으면 후보 A 재설계 또는 중단)

The agent surfaced this verdict to the user:

    게이트 결과가 나왔습니다 — 매핑 규칙이 없습니다.

The evidence was a read of `dags/tests/laplace/templates/sql` (PLURAL, 1
test). The real asset lives at `dags/tests/laplace/template/sql` (SINGULAR,
37 tests) — a one-character directory-name difference. Accepted as-is, the
verdict would have discarded approach A entirely (the AC's kill branch). It
was retracted only by the agent's own later probe.

The whole prompt layer was loaded and still failed
(feedback_no_premature_negative_lock_on_corpus_slice, the "Enumerate ≠
Exhaustive" CLAUDE.md rule, three more memory entries). The control group in
the same session — `output-block-falsify-advisory` — blocked 4/4 on a missing
`Falsified:` line. Prompt layer fails, hook layer succeeds, both observed in
one session.

## Design (narrowed on 200-session simulation, see spec.md)

The risk is NOT a negative claim in isolation ("column `cost` is missing"
surfaces its own error on the next query). It is a negative verdict that
*answers a pre-registered decision question with a kill branch*. So the
trigger requires BOTH, in the same paragraph:

  (a) a negative-existence marker  (없습니다 / 존재하지 않 / does not exist / ...)
  (b) a registered decision/verdict framing  (게이트 결과 / 게이트 판정 / AC # / ...)

Requirement — presence enforcement, NOT adequacy verification. A probe-
existence check is unusable: the motivating case HAD a probe (it read the
wrong directory). So, mirroring `output-block-falsify-advisory`'s `Falsified:`
line, this hook forces an `Enumerated:` line to exist in the verdict
paragraph — it does not judge whether the enumeration was adequate:

    Enumerated: <candidates actually covered> → <result of each>

The single-candidate fact ("only read templates/sql, 1 test") then sits
visibly next to the kill-branch verdict. That visibility is the whole
mechanism; it is explicitly not a guarantee the typo-class error is caught
(see spec.md "Honest limitation").

## Tiers

  Default → block (`{"decision": "block"}`). The 0.10-fires/session rate
    from the simulation is what makes a hard block affordable; the
    `Falsified:` precedent blocks too.
  `PRAXIS_NEGATIVE_EXISTENCE_ADVISORY=1` → demote to non-blocking advisory.
  `PRAXIS_HOOK_BYPASS_NEGATIVE_EXISTENCE_GATE=1` → full bypass (exit 0).

## Fail-open contract
  - Malformed / missing stdin JSON → exit 0
  - Missing / unreadable / empty transcript → exit 0
  - No last assistant text → exit 0
  - stop_hook_active=true → exit 0 (re-entrancy guard)
  - Any uncaught exception → exit 0 (@fail_open)
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_io import (  # type: ignore[import-not-found]  # noqa: E402
    emit_stop_advisory,
    emit_stop_block,
)
import _fire_ledger  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _transcript import (  # type: ignore[import-not-found]  # noqa: E402
    extract_last_assistant_text,
    load_current_turn,
)

_PREFIX = "[negative-existence-verdict-gate]"
_HOOK_NAME = "negative-existence-verdict-gate"
_ROLE = "completion-verify"
_BYPASS_ENV = "PRAXIS_HOOK_BYPASS_NEGATIVE_EXISTENCE_GATE"
_ADVISORY_ENV = "PRAXIS_NEGATIVE_EXISTENCE_ADVISORY"

# ---------------------------------------------------------------------------
# Trigger tokens (200-session simulation, issue #804 proposal v3).
#
# Korean tokens are matched as plain substrings — Hangul has no ASCII
# word-boundary hazard. English tokens are matched case-insensitively as
# substrings; each still requires a co-occurring marker/framing counterpart
# in the same paragraph, so a bare positive "Acceptance criteria met" cannot
# fire on its own.
# ---------------------------------------------------------------------------

# (a) Negative-existence markers.
_MARKERS_KO = ("없습니다", "존재하지 않", "미구현", "찾지 못했")
_MARKERS_EN = ("does not exist",)

# (b) Registered decision / verdict framing. Issue #901 removed `확인 결과`,
# `검증 결과` and `완료 조건`: a 30-day transcript census attributed all 11
# observed fires to those three and none to the gate-tier tokens below. They
# are ordinary Korean investigative prose ("upon checking …"), not a
# registered decision framing, so the marker co-occurrence requirement was
# not a sufficient discriminator on its own.
_FRAMINGS_KO = (
    "게이트 결과",
    "게이트 판정",
    "판정이 나왔",
)
_FRAMINGS_EN = ("acceptance", "ac #")

# The satisfying line: an `Enumerated:` line at column 0 with real content
# after the colon. Presence-only — content quality is intentionally NOT judged
# (see module docstring / spec.md).
_ENUMERATED_RE = re.compile(r"^Enumerated:\s*\S", re.MULTILINE)


def _contains_any(haystack_lower: str, needles: tuple[str, ...]) -> bool:
    return any(n in haystack_lower for n in needles)


def _has_marker(paragraph: str, paragraph_lower: str) -> bool:
    if _contains_any(paragraph, _MARKERS_KO):
        return True
    return _contains_any(paragraph_lower, _MARKERS_EN)


def _has_framing(paragraph: str, paragraph_lower: str) -> bool:
    if _contains_any(paragraph, _FRAMINGS_KO):
        return True
    return _contains_any(paragraph_lower, _FRAMINGS_EN)


def _paragraph_triggers(paragraph: str) -> bool:
    """A verdict paragraph fires when it carries BOTH a negative-existence
    marker and a registered decision framing, yet has no `Enumerated:` line."""
    lower = paragraph.lower()
    if not _has_marker(paragraph, lower):
        return False
    if not _has_framing(paragraph, lower):
        return False
    return not _ENUMERATED_RE.search(paragraph)


def has_unenumerated_verdict(text: str) -> bool:
    """True if any `\\n\\n`-delimited paragraph is a triggering verdict.

    Paragraph scoping mirrors the proposal's simulation (`\\n\\n` split): the
    marker, the framing, and the satisfying `Enumerated:` line must all live
    in the same verdict paragraph — an `Enumerated:` line in an unrelated
    paragraph does not clear the verdict.
    """
    for paragraph in text.split("\n\n"):
        if _paragraph_triggers(paragraph):
            return True
    return False


_MESSAGE = (
    f"{_PREFIX} the last assistant message surfaces a negative-existence verdict "
    "(없습니다 / 존재하지 않 / does not exist ...) under a registered decision/gate "
    "framing (게이트 결과 / 게이트 판정 / 판정이 나왔 / AC # / Acceptance), but the "
    "same paragraph has no 'Enumerated:' line starting in the first column.\n"
    f"{_PREFIX} 마지막 assistant 메시지가 등록된 결정/게이트 프레이밍 "
    "(게이트 결과 / 게이트 판정 / 판정이 나왔 / AC # / Acceptance) 아래에서 부정존재 판정 "
    "(없습니다 / 존재하지 않 / does not exist ...) 을 surface 했으나, 같은 문단에 "
    "첫 칼럼에서 시작하는 'Enumerated:' 줄이 없습니다.\n"
    f"{_PREFIX} Rule: kill-branch 가 걸린 사전 등록 결정 질문에 부정 판정으로 "
    "답할 때는, 실제로 커버한 후보와 각각의 결과를 판정 옆에 명시하라 "
    "(hook 은 열거의 적정성을 판정하지 않고 줄의 존재만 강제한다). Hub #3981: "
    "단수/복수 디렉터리 한 글자 차이로 접근 전체가 폐기될 뻔함.\n"
    f"{_PREFIX} 판정 문단에 첫 칼럼 시작 줄을 추가하라: "
    "'Enumerated: <실제로 커버한 후보 목록> → <각각의 결과>'. "
    "'Enumerated:' 는 자기 줄 첫 칼럼에서 시작해야 한다 (startswith 검사).\n"
    f"{_PREFIX} bypass: {_BYPASS_ENV}=1 / advisory 강등: {_ADVISORY_ENV}=1"
)


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

    transcript_path = payload.get("transcript_path") or ""
    if not transcript_path or not os.path.isfile(transcript_path):
        return 0

    turn = load_current_turn(transcript_path)
    last_text = extract_last_assistant_text(turn) if turn else ""
    if not last_text:
        return 0

    if not has_unenumerated_verdict(last_text):
        return 0

    # Advisory-demote follows the sibling advisory-env convention
    # (block-ask-end-option's PRAXIS_ASK_END_ADVISORY): any set, non-falsey
    # value opts out of the block; `0` / `false` / empty keep the default
    # block. Broader than a bare `== "1"` so `=true` / `=yes` also work,
    # while `=0` / `=false` correctly stay in block mode.
    advisory_env = os.environ.get(_ADVISORY_ENV, "")
    if advisory_env not in ("", "0", "false", "False"):
        emit_stop_advisory(_MESSAGE)
        decision = _fire_ledger.DECISION_ADVISE
    else:
        emit_stop_block(_MESSAGE)
        decision = _fire_ledger.DECISION_BLOCK
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
