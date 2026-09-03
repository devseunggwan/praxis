"""Tests for hooks/_lib/_paths.py sweep_stale and the phantom-path TTL (#1241).

`~/.praxis/state/phantom-path/` kept every 0-byte dedup marker forever (2,475
of them) because `prune_stale` was only ever called for `cache/` entries.
`sweep_stale` is the same daily, once-per-interval sweep exposed for any
directory; the external-write-path-existence-check hook calls it on each
marker write.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_LIB = _REPO / "hooks" / "_lib"
sys.path.insert(0, str(_LIB))

import _paths  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# sweep_stale: the daily prune, reusable outside cache/
# ---------------------------------------------------------------------------

def _aged(path: Path, days: float) -> Path:
    path.write_text("")
    old = time.time() - days * 86400
    os.utime(path, (old, old))
    return path


def test_sweep_stale_removes_expired_entries_and_keeps_fresh_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXIS_CACHE_TTL_DAYS", "7")
    expired = _aged(tmp_path / "abc123", 8)
    fresh = _aged(tmp_path / "def456", 1)
    assert _paths.sweep_stale(str(tmp_path)) == 1
    assert not expired.exists() and fresh.exists()


def test_sweep_stale_runs_once_per_interval(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXIS_CACHE_TTL_DAYS", "7")
    # An inherited interval of 0 would make the second call sweep again.
    monkeypatch.delenv("PRAXIS_CACHE_PRUNE_INTERVAL_HOURS", raising=False)
    _aged(tmp_path / "first", 8)
    assert _paths.sweep_stale(str(tmp_path)) == 1
    _aged(tmp_path / "second", 8)
    assert _paths.sweep_stale(str(tmp_path)) == 0  # stamp says: swept today
    assert (tmp_path / "second").exists()


def test_sweep_stale_never_raises(tmp_path):
    assert _paths.sweep_stale(str(tmp_path / "missing")) == 0


# ---------------------------------------------------------------------------
# phantom-path markers age out through the hook's own write path
# ---------------------------------------------------------------------------

def test_phantom_path_markers_past_the_ttl_are_swept_on_the_next_mark(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXIS_CACHE_TTL_DAYS", "7")
    hook = _load(
        "external_write_path_existence_check",
        _REPO / "hooks" / "advisory-nudge" / "external-write-path-existence-check" / "impl.py",
    )
    state = tmp_path / "phantom-path"
    state.mkdir()
    monkeypatch.setattr(hook, "_STATE_DIR", state)
    stale = _aged(state / ("0" * 16), 30)
    live = _aged(state / ("1" * 16), 1)
    hook._mark_reported("2" * 16)
    assert not stale.exists()
    assert live.exists()
    assert hook._already_reported("2" * 16)
