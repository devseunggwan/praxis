#!/usr/bin/env python3
"""Stop hook: positive-polarity artifact verdict without a `Verdict-evidence:` line.

Issue #862. `negative-existence-verdict-gate` (#804) gates the *negative*
polarity — "X does not exist" answering a pre-registered decision question.
Its mirror image is ungated: a *positive* verdict about a local artifact
("this file is a duplicate / can be deleted / was superseded") surfaced as a
candidate list, where the judgement rests on a cheap proxy instead of the
artifact's own content.

Motivating case (2026-07-26 memory-cleanup session, 5 misjudgements in one
session). The agent surfaced a 21-row table labelled verbatim
`Tier A — 삭제 후보 (근거 확정)`. The actual basis per row:

    | 판단하려던 속성            | 실제로 쓴 대리지표        |
    | 두 메모리가 같은 내용인가  | `description` 유사도      |
    | 서로 연결돼 있는가         | `grep '\\[\\['`            |
    | 섹션이 중복인가            | 섹션 제목 + 줄 간격       |
    | 위반 기록인가              | `grep '재발|strike'`      |

Ground truth was one command away in every case. Two rows were refuted by the
files' own bodies — both carried an explicit `Facet sibling (병합 아님)`
decision, and a third pair cross-referenced each other in prose rather than
`[[wikilink]]` syntax, which the chosen grep could not see. The label said
`근거 확정`; 17 of 21 rows had no executed evidence.

The prompt layer was fully loaded and still failed: `~/.claude/CLAUDE.md`
§Information Accuracy Layer 3 **Author-exempt verification trap** describes
this exact case ("own-authored content ... mapping tables ... Plausibility
from naming pattern ... ≠ verification").

## Why the existing fleet does not cover it

  `source-citation-probe-gate`      → external-write bodies only; this was
                                      in-conversation output.
  `output-block-falsify-advisory`   → AskUserQuestion / Bash surfaces; the
  `pre-output-falsification-gate`     table was plain assistant text.
  `negative-existence-verdict-gate` → negative polarity only.
  `path-probe-gate`                 → path depth, not artifact content.

## Design

Presence enforcement, NOT adequacy verification — mirroring the `Enumerated:`
precedent. Requiring "a Read of every judged path exists in the transcript"
was rejected: the motivating session did read *some* files, and per-row path
extraction from prose is unreliable. So the hook forces a single visible line
next to the verdict block:

    Verdict-evidence: <command run this session> → <output>

Writing that line is what makes the empty rows visible to the author at
authoring time. It is explicitly not a guarantee the judgement is right.

Trigger requires ALL THREE in one *unit* — a list-shaped paragraph joined with
its immediately preceding lead-in paragraph — so ordinary prose using the word
"중복" cannot fire on its own:

  (a) a positive artifact-verdict marker  (삭제 후보 / 중복 / superseded / ...)
  (b) a candidate/decision framing        (후보 / Tier / 정리 대상 / candidate)
  (c) a list shape                        (markdown table row, or >=2 `- ` items)

### Divergence from the sibling: satisfying-line scope

`negative-existence-verdict-gate` requires `Enumerated:` in the *same*
paragraph. This hook also accepts the immediately-following paragraph.
Reason: the verdict here is table-shaped, and a trailing non-table line
inside the same `\\n\\n` block breaks markdown table rendering. Adjacency is
still enforced — an evidence line elsewhere in the message does not clear it.

## Tiers

  Default → advisory (non-blocking). The trigger is text-pattern based and
    newer than the `Enumerated:` precedent, so it starts advisory; promotion
    to default-block is a separate call once fixtures accumulate.
  `PRAXIS_ARTIFACT_VERDICT_STRICT=1` → hard block.
  `PRAXIS_HOOK_BYPASS_ARTIFACT_VERDICT_GATE=1` → full bypass (exit 0).

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

_PREFIX = "[artifact-verdict-evidence-gate]"
_HOOK_NAME = "artifact-verdict-evidence-gate"
_ROLE = "completion-verify"
_BYPASS_ENV = "PRAXIS_HOOK_BYPASS_ARTIFACT_VERDICT_GATE"
_STRICT_ENV = "PRAXIS_ARTIFACT_VERDICT_STRICT"

# ---------------------------------------------------------------------------
# Trigger tokens. Korean tokens match as plain substrings (Hangul has no ASCII
# word-boundary hazard); English tokens match case-insensitively. Each of the
# three conditions must hold in the SAME paragraph.
# ---------------------------------------------------------------------------

# (a) Positive artifact-verdict markers.
_MARKERS_KO = (
    "삭제 후보",
    "삭제 가능",
    "삭제 대상",
    "중복",
    "통합 후보",
    "통합 대상",
    "병합 후보",
    "폐기 대상",
    "승계",
)
_MARKERS_EN = ("duplicate", "superseded", "redundant", "obsolete", "safe to delete")

# (b) Candidate / decision framing.
_FRAMINGS_KO = ("후보", "정리 대상", "판정", "티어")
_FRAMINGS_EN = ("candidate", "tier")

# (c) List shape — a markdown table row, or two or more `- ` bullets.
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*]\s+\S", re.MULTILINE)

# The satisfying line: `Verdict-evidence:` at column 0 with real content after
# the colon. Presence-only — content quality is intentionally NOT judged.
_EVIDENCE_RE = re.compile(r"^Verdict-evidence:\s*\S", re.MULTILINE)

# Fenced blocks are illustrations, not verdicts. Stripping them before the scan
# closes both directions of the same hole: a message *documenting* this hook
# quotes a candidate table (false trigger), and a fenced
# `Verdict-evidence: <command> → <output>` placeholder would otherwise clear a
# real adjacent verdict (false clear). Replaced with a paragraph break so the
# surrounding paragraphs stay separate.
_FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,}).*?^[ \t]*\1[ \t]*$", re.MULTILINE | re.DOTALL)


def _contains_any(haystack: str, needles: tuple[str, ...]) -> bool:
    return any(n in haystack for n in needles)


def _has_marker(paragraph: str, paragraph_lower: str) -> bool:
    if _contains_any(paragraph, _MARKERS_KO):
        return True
    return _contains_any(paragraph_lower, _MARKERS_EN)


def _has_framing(paragraph: str, paragraph_lower: str) -> bool:
    if _contains_any(paragraph, _FRAMINGS_KO):
        return True
    return _contains_any(paragraph_lower, _FRAMINGS_EN)


def _has_list_shape(paragraph: str) -> bool:
    if _TABLE_ROW_RE.search(paragraph):
        return True
    return len(_BULLET_RE.findall(paragraph)) >= 2


def _unit_triggers(unit: str) -> bool:
    """A verdict unit fires when it carries a positive artifact-verdict marker,
    a candidate framing, and a list shape — all three."""
    lower = unit.lower()
    if not _has_marker(unit, lower):
        return False
    if not _has_framing(unit, lower):
        return False
    return _has_list_shape(unit)


def has_unevidenced_verdict(text: str) -> bool:
    """True if any verdict unit lacks an adjacent `Verdict-evidence:` line.

    A *unit* is a list-shaped paragraph joined with its immediately preceding
    paragraph. The lead-in carries the framing ("다음 두 메모리는 통합
    후보입니다") far more often than the list itself does, so scoping the
    trigger to a single paragraph — as `negative-existence-verdict-gate` does
    — misses the exact shape this hook exists for (issue #862 M1/M3). One
    paragraph of lead-in is the limit: framing two or more paragraphs away is
    not treated as attached.

    The satisfying scope is the list paragraph plus the one immediately after
    it (see module docstring "Divergence from the sibling"). An evidence line
    further away does not clear the verdict.

    Fenced blocks are stripped first — they carry illustrations, and both a
    quoted candidate table and a quoted `Verdict-evidence:` placeholder would
    otherwise be read as the real thing.
    """
    paragraphs = _FENCE_RE.sub("\n\n", text).split("\n\n")
    for index, paragraph in enumerate(paragraphs):
        if not _has_list_shape(paragraph):
            continue
        unit = paragraph
        if index > 0:
            unit = paragraphs[index - 1] + "\n\n" + paragraph
        if not _unit_triggers(unit):
            continue
        scope = paragraph
        if index + 1 < len(paragraphs):
            scope = paragraph + "\n\n" + paragraphs[index + 1]
        if not _EVIDENCE_RE.search(scope):
            return True
    return False


_MESSAGE = (
    f"{_PREFIX} the last assistant message surfaces a positive verdict on local "
    "artifacts (delete candidate / duplicate / merge target / superseded ...) as a "
    "candidate list or table, but no first-column 'Verdict-evidence:' line sits "
    "next to that verdict block.\n"
    f"{_PREFIX} 마지막 assistant 메시지가 로컬 아티팩트에 대한 긍정 판정 "
    "(삭제 후보 / 중복 / 통합 대상 / superseded ...) 을 후보 목록·표 형태로 "
    "surface 했으나, 그 판정 블록에 인접한 첫 칼럼 'Verdict-evidence:' 줄이 "
    "없습니다.\n"
    f"{_PREFIX} Rule: 판정 대상의 본문을 읽지 않은 채 파일명·description 유사도 "
    "같은 대리지표로 삭제/중복/통합을 판정하지 말 것. 이번 세션에 실행한 확인 "
    "명령과 그 출력을 판정 옆에 명시하라 (hook 은 근거의 적정성을 판정하지 않고 "
    "줄의 존재만 강제한다). 2026-07-26 세션: 21행 '근거 확정' 표에서 17행이 "
    "미검증, 2건이 파일 본문의 명시적 반대 결정으로 반증됨.\n"
    f"{_PREFIX} 판정 블록 바로 뒤에 첫 칼럼 시작 줄을 추가하라: "
    "'Verdict-evidence: <이번 세션에 실행한 명령> → <출력>'. "
    "근거를 못 쓰는 행은 표에서 내려라.\n"
    f"{_PREFIX} bypass: {_BYPASS_ENV}=1 / 차단 승격: {_STRICT_ENV}=1"
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

    if not has_unevidenced_verdict(last_text):
        return 0

    # Exact `=1` matches the `_STRICT_ENV` convention of the three sibling
    # completion-verify gates; any other value keeps the default advisory.
    if os.environ.get(_STRICT_ENV, "").strip() == "1":
        emit_stop_block(_MESSAGE)
        decision = _fire_ledger.DECISION_BLOCK
    else:
        emit_stop_advisory(_MESSAGE)
        decision = _fire_ledger.DECISION_ADVISE
    # Rich fire record (issue #847): Stop hooks signal block/advise via a stdout
    # decision while exiting 0, so @fail_open's coarse path records only "pass".
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
