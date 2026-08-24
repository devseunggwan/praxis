"""The anchor-existence gate must not retain tool_result text (issue #1113,
memory/perf finding on PR #1115).

The whole-transcript scan is unavoidable for this gate (the create and the
anchor post can be many turns apart), so the only thing standing between it
and session-sized memory is that it never buffers a tool_result's raw text —
only a `gh pr create` result is ever read at all, and only the PR-URL set
extracted from it is kept. A session with large unrelated tool outputs (file
reads, CI logs) must not cost memory/CPU proportional to their size on every
Stop event.

Run: python3 -m pytest tests/test_pr_anchor_gate_memory.py -q
"""
from __future__ import annotations

import importlib.util
import sys
import tracemalloc
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "hooks" / "_lib"))

_spec = importlib.util.spec_from_file_location(
    "pr_anchor_existence_gate",
    REPO_ROOT / "hooks" / "completion-verify" / "pr-anchor-existence-gate" / "impl.py",
)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)  # type: ignore[union-attr]

_HUGE = "z" * (200 * 1024)  # 200KB, mirrors a large file read / CI log dump


def _unrelated_call(tid: str):
    yield {"message": {"content": [
        {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": f"cat somefile-{tid}.log"}},
    ]}}
    yield {"message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": False, "content": _HUGE},
    ]}}


def _events(unrelated_calls: int):
    """Yield one event at a time — a materialised list would dominate the peak."""
    for i in range(unrelated_calls):
        yield from _unrelated_call(f"u{i}")
    yield {"message": {"content": [
        {"type": "tool_use", "id": "create1", "name": "Bash", "input": {"command": "gh pr create --title x --body y"}},
    ]}}
    yield {"message": {"content": [
        {"type": "tool_result", "tool_use_id": "create1", "is_error": False, "content": "https://github.com/o/r/pull/178"},
    ]}}


def test_non_create_tool_result_text_is_never_retained() -> None:
    pending_creates: list = []
    pending_posts: list = []
    create_tids: set[str] = set()
    gate._reduce_tool_use(
        {"name": "Bash", "id": "t1", "input": {"command": "cat somefile.log"}},
        pending_creates, pending_posts, create_tids,
    )
    # A plain `cat` is neither a `gh pr create` nor a `gh pr comment`/`gh api`
    # post — nothing about it is worth tracking at all.
    assert pending_creates == []
    assert pending_posts == []
    assert create_tids == set()


def test_create_tid_is_tracked_for_the_result_pass() -> None:
    pending_creates: list = []
    pending_posts: list = []
    create_tids: set[str] = set()
    gate._reduce_tool_use(
        {"name": "Bash", "id": "c1", "input": {"command": "gh pr create --title x --body y"}},
        pending_creates, pending_posts, create_tids,
    )
    assert pending_creates == [("c1", False)]
    assert create_tids == {"c1"}


def test_functional_result_unchanged_with_unrelated_large_outputs() -> None:
    assert gate.find_unanchored_prs(_events(5)) == ["178"]


def test_peak_memory_does_not_track_unrelated_tool_result_size() -> None:
    """300 unrelated 200KB tool_results (~60MB on disk/in-transit) must not
    show up as retained RSS — only the create result's PR-URL set is kept."""
    tracemalloc.start()
    try:
        result = gate.find_unanchored_prs(_events(300))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result == ["178"]
    assert peak < 2 * 1024 * 1024, (
        f"peak {peak / 1024 / 1024:.2f} MiB — an unrelated tool_result's text is being retained"
    )
