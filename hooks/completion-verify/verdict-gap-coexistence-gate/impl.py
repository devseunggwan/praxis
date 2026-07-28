#!/usr/bin/env python3
"""Stop-hook advisory: gate a GO-verdict phrase coexisting with an unresolved-gap
marker in the SAME assistant output.

Background (issue #845, retrospect-promoted 2026-07-23). In a PR review
session, the assistant self-flagged "⚠️ 미해소 갭 — 데이터 동일성 검증 증거
부재" as a headline in the SAME response that also asserted "판단: 리뷰어에게
보내도 됩니다" (a GO verdict). The user corrected it twice before the actual
verification (full template sweep + prod data parity) was run — result: PASS.
So the conclusion itself was not wrong; the DEFECT was locking a verdict
before running the verification the response itself said was missing.

tracer root-cause (confidence HIGH): verdict-scope conflation — "procedural
sendability" (CI green, no conflicts) and "semantic soundness" were collapsed
into one GO phrase, and the disclosed gap never got promoted into a
conditional clause on the verdict. This is a same-output logical-linkage
failure, not a search failure — always-loaded rules (Author-exempt trap,
Verification Before Completion, PR Review Protocol) and 4 memory entries were
already present and did not prevent the repeat.

Probe-first result (this PR, via session_search over locally accessible
transcripts): only the ORIGINATING incident was found — the other hits
returned by the search are the same issue body being re-quoted (issue
creation echo, this task's own reading of it), not independent repeat
occurrences. The issue's own gate ("반복 패턴 확정 시에만 hook 구현") is
therefore NOT met at the 3-5 case bar. Given the hook is advisory-only (never
blocks; the strict/bypass envs below exist only for convention-consistency
with sibling hooks in this family and are not required by the issue), the
false-positive cost of shipping now is bounded — this is documented as an
explicit, accepted deviation from the issue's stated precondition, not a
silent skip. See `spec.md` "Probe-first result" for the full account.

Detection: the last assistant turn's text (not scoped to one line, per the
issue — "동일 assistant 출력에") contains a non-negated GO-verdict phrase AND
an unresolved-gap marker that is not itself immediately followed by
resolution language ("해소되었습니다", "resolved") — UNLESS a conditional
connector ("갭 해소 시", "once the gap is resolved") already links the two,
which is exactly the remedy the advisory recommends, so it is treated as
already-applied and silenced. Advisory by default (stdout `{"systemMessage":
...}` JSON + exit 0); `PRAXIS_VERDICT_GAP_STRICT=1` escalates to
`{"decision": "block", ...}`. Fully fail-open; bypass with
`PRAXIS_VERDICT_GAP_BYPASS=1`.
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

_PREFIX = "[verdict-gap-coexistence-gate]"
_BYPASS_ENV = "PRAXIS_VERDICT_GAP_BYPASS"
_STRICT_ENV = "PRAXIS_VERDICT_GAP_STRICT"
_HOOK_NAME = "verdict-gap-coexistence-gate"
_ROLE = "completion-verify"

# ---------------------------------------------------------------------------
# GO-verdict phrases (issue #845 trigger list, verbatim + "all clear" from the
# issue title). EN uses ASCII word-boundary lookarounds (Python \b misfires
# adjacent to Hangul — same trap documented in the sibling hooks).
# ---------------------------------------------------------------------------

_GO_EN_RE = re.compile(
    r"(?<![A-Za-z])ready\s+to\s+merge(?![A-Za-z])"
    r"|(?<![A-Za-z])all\s+clear(?![A-Za-z])",
    re.IGNORECASE,
)
_GO_KO_RE = re.compile(
    r"보내도\s*(?:됩니다|됨|되겠)|머지\s*가능|approve\s*가능|문제\s*없습니다|이상\s*없습니다",
    re.IGNORECASE,
)

# Negation windows — same trailing/leading-window strategy as
# completion-signal-gate's `_is_negated_en` / `_is_negated_ko`. A GO phrase
# under negation ("머지 가능하지 않습니다", "not ready to merge") is NOT a
# verdict and must not fire.
_NEG_WINDOW_EN = 24
_NEG_MARKERS_EN = ("not ", "n't ", "no ", "never ", "isn't", "aren't", "won't", "can't", "cannot")
_NEG_WINDOW_KO = 12
_NEG_MARKERS_KO = ("지 않", "지않", "안 됩", "안됩", "못 ", "아니")


def _is_negated_en(text: str, start: int) -> bool:
    prefix = text[max(0, start - _NEG_WINDOW_EN):start].lower()
    return any(neg in prefix for neg in _NEG_MARKERS_EN)


def _is_negated_ko(text: str, end: int) -> bool:
    suffix = text[end:end + _NEG_WINDOW_KO]
    return any(neg in suffix for neg in _NEG_MARKERS_KO)


def _has_go_verdict(text: str) -> bool:
    """True if `text` contains a non-negated, non-quoted GO-verdict phrase."""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(">"):
            continue
        for m in _GO_EN_RE.finditer(line):
            if not _is_negated_en(line, m.start()):
                return True
        for m in _GO_KO_RE.finditer(line):
            if not _is_negated_ko(line, m.end()):
                return True
    return False


# ---------------------------------------------------------------------------
# Unresolved-gap markers (issue #845 trigger list). A marker immediately
# followed by resolution language ("갭... 해소되었습니다", "... resolved")
# within a short window is NOT unresolved and does not count — this is a
# deliberate, documented trade-off (mirrors the `_is_negated_*` window style)
# rather than full discourse parsing.
# ---------------------------------------------------------------------------

_GAP_RE = re.compile(
    r"⚠️?|미해소|검증\s*증거\s*부재|갭(?![A-Za-z])"
    r"|(?<![A-Za-z])unverified(?![A-Za-z])"
    r"|(?<![A-Za-z])not\s+verified(?![A-Za-z])"
    r"|(?<![A-Za-z])TODO(?![A-Za-z])"
    r"|(?<![A-Za-z])pending(?![A-Za-z])",
    re.IGNORECASE,
)
_GAP_RESOLVED_WINDOW = 32
_GAP_RESOLVED_MARKERS = (
    "해소되었", "해소함", "해소했", "해소됨", "해결되었", "해결했",
    "resolved", "closed", "addressed",
)


def _has_unresolved_gap_marker(text: str) -> bool:
    """True if `text` (outside quoted `>` lines) contains a gap marker not
    immediately followed by resolution language."""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(">"):
            continue
        for m in _GAP_RE.finditer(line):
            suffix = line[m.end():m.end() + _GAP_RESOLVED_WINDOW]
            if any(marker in suffix for marker in _GAP_RESOLVED_MARKERS):
                continue
            return True
    return False


# Conditional connector — the advisory's own recommended remedy ("갭 X 해소
# 시 보내도 됨"). If already present anywhere in the output, the verdict is
# already properly conditioned on the gap and the advisory would be noise.
_CONDITIONAL_RE = re.compile(
    r"해소\s*(?:되면|시|하면)|해결\s*(?:되면|시|하면)|조건부(?:로)?"
    r"|once\s+(?:the\s+)?gap\s+is\s+(?:resolved|addressed|closed)"
    r"|after\s+(?:resolving|addressing|closing)\s+(?:the\s+)?gap"
    r"|conditional\s+on\s+(?:the\s+)?gap",
    re.IGNORECASE,
)


def has_verdict_gap_coexistence(text: str) -> bool:
    """True if `text` asserts a GO verdict and discloses an unresolved gap in
    the SAME output, with no conditional connector already linking them."""
    if _CONDITIONAL_RE.search(text):
        return False
    return _has_go_verdict(text) and _has_unresolved_gap_marker(text)


def _advisory() -> str:
    return (
        f"{_PREFIX} final message asserts a GO verdict (보내도 됩니다/머지 "
        "가능/ready to merge/all clear/...) while ALSO disclosing an "
        "unresolved gap (⚠/미해소/검증 증거 부재/unverified/TODO/pending) in "
        "the same output.\n"
        f"{_PREFIX} Rule: a disclosed gap is a condition on the verdict, not "
        "an aside — rewrite the verdict as conditional on the gap (\"갭 X "
        "해소 시 보내도 됨\") or resolve the gap FIRST, before locking the "
        "verdict (issue #845: a self-flagged \"⚠️ 미해소 갭\" coexisted with "
        "\"판단: 리뷰어에게 보내도 됩니다\" — the eventual result was PASS, "
        "but only after 2 user corrections forced the verification that "
        "should have run before the verdict).\n"
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

    if not has_verdict_gap_coexistence(last_text):
        return 0

    if os.environ.get(_STRICT_ENV, "").strip() == "1":
        emit_stop_block(_advisory())
        decision = _fire_ledger.DECISION_BLOCK
    else:
        emit_stop_advisory(_advisory())
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
