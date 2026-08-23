"""The runtime-state gate scans prior turns only when one could matter (#1076).

`detect_verdict_restatement` answers a two-part question: is a verdict claim
unqualified *here*, and was it already stated earlier. The first half is
decided from `last_text` alone, so deciding it first keeps the whole-session
scan off the common path — a turn with no unqualified verdict claim cannot
have one restated, whatever the history holds.

Run: python3 -m pytest tests/test_runtime_state_claim_scan.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "hooks" / "_lib"))

_spec = importlib.util.spec_from_file_location(
    "runtime_state_claim_gate",
    REPO_ROOT / "hooks" / "completion-verify" / "runtime-state-claim-gate" / "impl.py",
)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)  # type: ignore[union-attr]


class _Tripwire:
    """Iterating at all is the cost this short-circuit exists to avoid."""

    def __iter__(self):
        raise AssertionError("prior_events was scanned")


def test_no_unqualified_claim_leaves_history_unscanned() -> None:
    restated, mentions = gate.detect_verdict_restatement(
        "지금 배포 스크립트를 돌리는 중입니다.", _Tripwire()
    )
    assert (restated, mentions) == ([], {})


def test_unqualified_claim_still_scans_history() -> None:
    """Mirror of the case above — without it, a gate that never scans passes."""
    drained = {"n": 0}

    def counting():
        drained["n"] += 1
        yield from ()

    text = "행 3은 PASS 입니다."
    assert any(not c["qualified"] for c in gate.extract_verdict_claims(text)), (
        "fixture no longer parses as an unqualified verdict claim"
    )
    gate.detect_verdict_restatement(text, counting())
    assert drained["n"] == 1


def test_restatement_still_detected_across_prior_turns() -> None:
    """The short-circuit must not cost the gate its actual finding."""
    text = "행 3은 PASS 입니다."
    key = gate.extract_verdict_claims(text)[0]["key"]
    prior = [
        {
            "timestamp": "2026-08-23T00:00:00Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        }
    ]
    restated, mentions = gate.detect_verdict_restatement(text, iter(prior))
    assert mentions, "prior-mention collector did not recognise the fixture text"
    assert key in restated
