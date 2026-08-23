"""The PR-report gate reduces every tool_use on arrival (issue #1076).

This gate cannot read a bounded tail — it looks for a PR touched anywhere in
the session — so the only thing standing between it and session-sized memory
is that it never retains a block. A `tool_use` block carries its whole `input`
dict, Bash command strings included, so buffering them re-creates exactly the
cost the streaming rewrite was meant to remove.

Run: python3 -m pytest tests/test_pr_report_gate_memory.py -q
"""
from __future__ import annotations

import importlib.util
import sys
import tracemalloc
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "hooks" / "_lib"))

_spec = importlib.util.spec_from_file_location(
    "pr_report_destination_gate",
    REPO_ROOT / "hooks" / "completion-verify" / "pr-report-destination-gate" / "impl.py",
)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)  # type: ignore[union-attr]

_HUGE = "z" * 10_000


def _bash(tid: str, cmd: str) -> dict:
    return {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd}}


def _events(calls: int, pad: int = 4000):
    """Yield one event at a time — a materialised list would dominate the peak."""
    for i in range(calls):
        yield {"message": {"content": [_bash(f"t{i}", f"gh pr view {1000 + i % 7} # " + "x" * pad)]}}
    yield {"message": {"content": [{"type": "tool_use", "id": "w", "name": "Write",
                                    "input": {"file_path": "/tmp/pr-report.md"}}]}}
    yield {"message": {"content": [{"type": "tool_result", "tool_use_id": "w", "is_error": False}]}}


def test_reduction_drops_the_block_and_its_command() -> None:
    pending: list = []
    context_prs: set[str] = set()
    gate._reduce_tool_use(
        _bash("t1", f"gh pr comment 42 --body-file x.md {_HUGE}"), context_prs, pending
    )
    assert pending == [("t1", ["42"], None)]
    assert not any(isinstance(part, dict) for entry in pending for part in entry)
    assert _HUGE not in repr(pending)


def test_context_pr_resolves_without_buffering() -> None:
    """Context is success-independent, so it must not need a pending entry."""
    pending: list = []
    context_prs: set[str] = set()
    gate._reduce_tool_use(_bash("t2", "gh pr view 77 --json state"), context_prs, pending)
    assert context_prs == {"77"}
    assert pending == []


def test_failed_post_does_not_count_as_reported() -> None:
    events = [
        {"message": {"content": [_bash("c1", "gh pr comment 55 --body-file r.md")]}},
        {"message": {"content": [{"type": "tool_result", "tool_use_id": "c1", "is_error": True}]}},
        {"message": {"content": [_bash("v1", "gh pr view 55 --json state")]}},
        {"message": {"content": [{"type": "tool_use", "id": "w", "name": "Write",
                                  "input": {"file_path": "/tmp/pr-report.md"}}]}},
        {"message": {"content": [{"type": "tool_result", "tool_use_id": "w", "is_error": False}]}},
    ]
    unreported, reports = gate.find_unreported_prs(iter(events))
    assert unreported == ["55"] and reports == ["/tmp/pr-report.md"]


def test_succeeded_post_counts_as_reported() -> None:
    """Mirror of the case above — without it a blanket regression reads as a pass."""
    events = [
        {"message": {"content": [_bash("c1", "gh pr comment 55 --body-file r.md")]}},
        {"message": {"content": [{"type": "tool_result", "tool_use_id": "c1", "is_error": False}]}},
        {"message": {"content": [_bash("v1", "gh pr view 55 --json state")]}},
        {"message": {"content": [{"type": "tool_use", "id": "w", "name": "Write",
                                  "input": {"file_path": "/tmp/pr-report.md"}}]}},
        {"message": {"content": [{"type": "tool_result", "tool_use_id": "w", "is_error": False}]}},
    ]
    assert gate.find_unreported_prs(iter(events)) == ([], ["/tmp/pr-report.md"])


def test_peak_memory_does_not_track_session_size() -> None:
    """3000 calls x 4KB of command text retained whole is ~17 MiB; reduced it is ~0.3."""
    tracemalloc.start()
    try:
        gate.find_unreported_prs(_events(3000))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2 * 1024 * 1024, f"peak {peak / 1024 / 1024:.2f} MiB — a block is being retained"
