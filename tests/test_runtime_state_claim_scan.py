"""The runtime-state gate reads prior turns lazily, and holds only the reduction.

Two properties, both about the same scan:

  * it is not run at all when `last_text` carries no unqualified verdict claim,
    because the first half of the question is answerable from `last_text` alone;
  * while it runs, what is buffered is the verdict keys seen so far, not the
    events carrying them — a completed turn can be most of a long session.

Run: python3 -m pytest tests/test_runtime_state_claim_scan.py -q
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tracemalloc
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

_VERDICT = "행 3은 PASS 입니다."


def _write(path: Path, events: list[dict]) -> str:
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return str(path)


def _assistant(text: str, ts: str | None = None) -> dict:
    ev: dict = {"type": "assistant", "message": {"role": "assistant",
                                                 "content": [{"type": "text", "text": text}]}}
    if ts:
        ev["timestamp"] = ts
    return ev


def _user(text: str = "다음 단계 진행해줘") -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def test_no_unqualified_claim_leaves_the_transcript_unread(monkeypatch, tmp_path) -> None:
    def tripwire(_path):
        raise AssertionError("the transcript was scanned")

    monkeypatch.setattr(gate, "prior_verdict_mentions", tripwire)
    path = _write(tmp_path / "t.jsonl", [_user(), _assistant(_VERDICT)])
    assert gate.detect_verdict_restatement("배포 스크립트를 돌리는 중입니다.", path) == ([], {})


def test_unqualified_claim_still_reads_the_transcript(monkeypatch, tmp_path) -> None:
    """Mirror of the case above — without it, a gate that never scans passes."""
    calls = {"n": 0}

    def counting(_path):
        calls["n"] += 1
        return {}

    monkeypatch.setattr(gate, "prior_verdict_mentions", counting)
    assert any(not c["qualified"] for c in gate.extract_verdict_claims(_VERDICT)), (
        "fixture no longer parses as an unqualified verdict claim"
    )
    gate.detect_verdict_restatement(_VERDICT, str(tmp_path / "t.jsonl"))
    assert calls["n"] == 1


def test_restatement_detected_across_a_prior_turn(tmp_path) -> None:
    """The laziness must not cost the gate its actual finding."""
    key = gate.extract_verdict_claims(_VERDICT)[0]["key"]
    path = _write(tmp_path / "t.jsonl", [
        _user(), _assistant(_VERDICT, "2026-08-23T00:00:00Z"),
        _user(), _assistant("작업을 이어갑니다."),
    ])
    restated, mentions = gate.detect_verdict_restatement(_VERDICT, path)
    assert mentions, "prior-mention collector did not recognise the fixture text"
    assert key in restated


def test_current_turn_does_not_count_as_its_own_prior_mention(tmp_path) -> None:
    """The trailing pending group is the current turn and must be dropped —
    otherwise the text being judged restates itself and every claim fires."""
    path = _write(tmp_path / "t.jsonl", [_user(), _assistant(_VERDICT, "2026-08-23T00:00:00Z")])
    assert gate.prior_verdict_mentions(path) == {}
    assert gate.detect_verdict_restatement(_VERDICT, path) == ([], {})


def test_peak_memory_does_not_track_the_size_of_a_completed_turn(tmp_path) -> None:
    """Buffering the events made peak follow the largest turn: 8000 events of
    4KB text peaked at ~39 MiB. Buffering the reduction keeps it flat."""
    big = "y" * 4000
    events = [_user()] + [_assistant(big, "2026-08-23T00:00:00Z") for _ in range(4000)]
    events += [_user(), _assistant("현재 턴")]
    path = _write(tmp_path / "t.jsonl", events)

    tracemalloc.start()
    try:
        gate.prior_verdict_mentions(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2 * 1024 * 1024, f"peak {peak / 1024 / 1024:.2f} MiB — a turn is being buffered"
