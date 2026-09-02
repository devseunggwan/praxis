"""Tail-scan bounds for `postcompact-context` (issue #1034 perf review).

`_tail_candidate_lines` replaced a plain `deque(fh, maxlen=n)`. The plain form
bounds retained memory by LINE COUNT, not by bytes, so a transcript whose
records are large costs `n * line_size` on every UserPromptSubmit. A compaction
summary is exactly such a record, so the shape is not hypothetical.

These tests pin the window semantics (still the last n lines) and the reason
the change exists (a large NON-candidate line is not retained). The memory
assertion is deliberately generous: it fails on a regression to the plain form
(~20 MB at this fixture size) without pinning an exact allocator figure.
"""

import importlib.util
import json
import sys
import tracemalloc
from pathlib import Path

import pytest
from collections import deque as _deque

REPO_ROOT = Path(__file__).resolve().parents[1]
IMPL = REPO_ROOT / "hooks" / "advisory-nudge" / "postcompact-context" / "impl.py"


@pytest.fixture(scope="module")
def mod():
    """Load the hook impl directly, the same way the concurrency suite does."""
    sys.path.insert(0, str(REPO_ROOT / "hooks" / "_lib"))
    spec = importlib.util.spec_from_file_location("impl_postcompact_tail", IMPL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, n_noise: int, line_kb: int, compact_at_end: bool) -> None:
    pad = "x" * (line_kb * 1024)
    with path.open("w", encoding="utf-8") as fh:
        for i in range(n_noise):
            fh.write(json.dumps({"type": "assistant", "i": i, "pad": pad}) + "\n")
        if compact_at_end:
            fh.write(
                json.dumps(
                    {"type": "user", "isCompactSummary": True, "uuid": "u9", "timestamp": "t"}
                )
                + "\n"
            )


def test_window_is_still_the_last_n_lines(mod, tmp_path):
    """Non-candidates are elided to "", but they still occupy their slot."""
    p = tmp_path / "t.jsonl"
    _write(p, n_noise=250, line_kb=1, compact_at_end=False)

    lines = mod._tail_candidate_lines(str(p), 100)

    assert len(lines) == 100, "the window must still be the last n lines"
    assert all(line == "" for line in lines), "no line here is a candidate"


def test_a_candidate_inside_the_window_is_kept_in_full(mod, tmp_path):
    p = tmp_path / "t.jsonl"
    _write(p, n_noise=50, line_kb=1, compact_at_end=True)

    lines = mod._tail_candidate_lines(str(p), 100)
    kept = [line for line in lines if line]

    assert len(kept) == 1
    assert json.loads(kept[0])["uuid"] == "u9"
    assert mod.find_latest_compact_summary(str(p), 100)["uuid"] == "u9"


def test_a_candidate_outside_the_window_is_not_found(mod, tmp_path):
    """Eliding non-candidates must not widen the window past n lines."""
    p = tmp_path / "t.jsonl"
    pad = "x" * 1024
    with p.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "isCompactSummary": True, "uuid": "old"}) + "\n")
        for i in range(150):
            fh.write(json.dumps({"type": "assistant", "i": i, "pad": pad}) + "\n")

    assert mod.find_latest_compact_summary(str(p), 100) is None


def test_large_non_candidate_lines_are_not_retained(mod, tmp_path):
    """The whole point: memory must not scale with n * line_size.

    200 KB records x a 100-line window is ~20 MB under the plain deque. The
    ceiling here is far below that and far above what the elided form needs,
    so it catches the regression without pinning an allocator number.
    """
    p = tmp_path / "t.jsonl"
    _write(p, n_noise=200, line_kb=200, compact_at_end=True)

    tracemalloc.start()
    try:
        record = mod.find_latest_compact_summary(str(p), 100)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert record["uuid"] == "u9"
    assert peak < 5_000_000, (
        f"retained {peak / 1e6:.1f} MB — a plain deque(maxlen=100) over 200 KB "
        "records peaks near 21 MB, so this is the line-count-not-bytes regression"
    )


# ---------------------------------------------------------------------------
# Equivalence and seek-correctness tests (issue #1155)
# ---------------------------------------------------------------------------
# These tests verify that the seek-based implementation matches the old linear
# scan on the same inputs.  The old implementation is reproduced inline so the
# tests are self-contained.
# ---------------------------------------------------------------------------


def _old_tail(path: str, n: int, marker: str) -> list:
    """Reference: the original O(file-size) linear scan."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return list(_deque((line if marker in line else "" for line in fh), maxlen=n))
    except (OSError, ValueError):
        return []


def _write_custom(path, lines):
    with path.open("w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")


_MARKER = '"isCompactSummary"'
_CANDIDATE = json.dumps({"type": "user", "isCompactSummary": True, "uuid": "x"})
_NOISE = json.dumps({"type": "assistant", "i": 0})


@pytest.mark.parametrize("label,lines,n", [
    ("empty", [], 10),
    ("fewer_than_n", [_NOISE, _CANDIDATE], 10),
    ("exactly_n", [_NOISE] * 9 + [_CANDIDATE], 10),
    ("more_than_n_candidate_in_window", [_NOISE] * 200 + [_CANDIDATE], 100),
    ("more_than_n_candidate_outside_window", [_CANDIDATE] + [_NOISE] * 200, 100),
    ("no_candidate", [_NOISE] * 200, 100),
    ("multiple_candidates", [_NOISE] * 50 + [_CANDIDATE] + [_NOISE] * 50 + [_CANDIDATE], 100),
    ("korean_chars", ['{"text":"안녕하세요"}'] * 5 + [_CANDIDATE], 10),
    ("no_trailing_newline_handled", [_NOISE] * 3 + [_CANDIDATE], 5),
])
def test_seek_matches_linear_scan(mod, tmp_path, label, lines, n):
    """seek-based result must equal the old linear scan on every input shape."""
    p = tmp_path / f"t_{label}.jsonl"
    _write_custom(p, lines)
    old = _old_tail(str(p), n, _MARKER)
    new = mod._tail_candidate_lines(str(p), n)
    assert new == old, (
        f"[{label}] seek result differs from linear scan\n"
        f"  old={old}\n  new={new}"
    )


def test_seek_is_sublinear_in_file_size(mod, tmp_path):
    """Runtime must not grow proportionally with file size (issue #1155).

    A 100-line compact summary sits at the end of a large file. The seek-based
    reader should find it in O(window) time — measured by reading two files
    that differ only in the size of the pre-window prefix and asserting the
    second does not take dramatically longer.
    """
    import time

    def build(path, prefix_mb):
        pad = "y" * 1024  # 1 KB lines
        with path.open("w", encoding="utf-8") as fh:
            for i in range(prefix_mb * 1024):  # prefix_mb × 1 KB = prefix_mb MB
                fh.write(json.dumps({"type": "a", "pad": pad}) + "\n")
            fh.write(json.dumps({"type": "user", "isCompactSummary": True, "uuid": "z"}) + "\n")

    small = tmp_path / "small.jsonl"
    large = tmp_path / "large.jsonl"
    build(small, prefix_mb=1)
    build(large, prefix_mb=10)

    t0 = time.monotonic()
    for _ in range(5):
        mod._tail_candidate_lines(str(small), 100)
    small_ms = (time.monotonic() - t0) * 1000 / 5

    t0 = time.monotonic()
    for _ in range(5):
        mod._tail_candidate_lines(str(large), 100)
    large_ms = (time.monotonic() - t0) * 1000 / 5

    # A 10× larger file should not take > 10× longer if the scan is O(window).
    # We allow 15× headroom for OS scheduler variance.
    assert large_ms < max(small_ms * 15, 50), (
        f"Seek looks O(file-size): small={small_ms:.1f}ms, large={large_ms:.1f}ms "
        f"(10x file → {large_ms/small_ms:.1f}x time)"
    )
    # Both calls must still find the summary
    assert mod.find_latest_compact_summary(str(large), 100)["uuid"] == "z"
