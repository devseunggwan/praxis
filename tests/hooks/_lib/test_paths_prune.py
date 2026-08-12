"""Tests for hooks/_lib/_paths.py prune_stale session ownership (#920).

`prune_stale` deleted on mtime alone. A session-keyed marker is written once at
the start of a session and then only read, so its mtime stops advancing while
the session is alive; past the TTL the sweep removed it and the gate reading it
went silently quiet — the #666 failure mode. The sweep now excludes entries
keyed on the calling session.

Coverage:
  - the calling session's entries survive a sweep that would otherwise delete
    them (both file and directory entries)
  - other sessions' stale entries are still swept (the guard is not an off
    switch)
  - fresh entries survive regardless of session
  - token matching is boundary-anchored: a PPID-shaped key like `123` must not
    protect an unrelated `...-1234.json`
  - resolve_cache_file threads its session_id through to the sweep
  - a `.lock` held right now survives another session's sweep, and an
    abandoned one does not — the one entry age cannot judge (#951)
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "hooks" / "_lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import _paths  # noqa: E402
import _state_lock  # noqa: E402

SID = "abc123-session"
OTHER = "zzz999-other"
DAY = 86400.0


def _aged(path: Path, days: float) -> Path:
    """Backdate `path` (a file, or a directory and its contents) by `days`."""
    stamp = time.time() - days * DAY
    targets = [path]
    if path.is_dir():
        targets += [Path(r) / f for r, _d, fs in os.walk(path) for f in fs]
        targets += [Path(r) for r, _d, _f in os.walk(path)]
    for t in targets:
        os.utime(t, (stamp, stamp))
    return path


def _file(cache: Path, name: str, days: float) -> Path:
    p = cache / name
    _ = p.write_text("{}", encoding="utf-8")
    return _aged(p, days)


def test_current_session_entries_survive_a_sweep(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    mine = _file(cache, f"retrospect-active-{SID}.json", days=30)
    mine_too = _file(cache, f"session-intent-{SID}.json", days=30)

    removed = _paths.prune_stale(str(cache), ttl_days=7.0, session_id=SID)

    assert removed == 0
    assert mine.exists()
    assert mine_too.exists()


def test_other_sessions_are_still_swept(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    mine = _file(cache, f"session-intent-{SID}.json", days=30)
    theirs = _file(cache, f"session-intent-{OTHER}.json", days=30)

    removed = _paths.prune_stale(str(cache), ttl_days=7.0, session_id=SID)

    assert removed == 1
    assert mine.exists()
    assert not theirs.exists()


def test_fresh_entries_survive_regardless_of_session(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    theirs = _file(cache, f"session-intent-{OTHER}.json", days=1)

    removed = _paths.prune_stale(str(cache), ttl_days=7.0, session_id=SID)

    assert removed == 0
    assert theirs.exists()


def test_session_directory_trees_are_protected(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    tree = cache / f"path-probe-gate-{SID}"
    (tree / "inner").mkdir(parents=True)
    _ = (tree / "inner" / "k.json").write_text("{}", encoding="utf-8")
    _ = _aged(tree, days=30)

    removed = _paths.prune_stale(str(cache), ttl_days=7.0, session_id=SID)

    assert removed == 0
    assert (tree / "inner" / "k.json").exists()


def test_token_match_is_boundary_anchored(tmp_path: Path) -> None:
    """A PPID fallback key is short; substring matching would over-protect."""
    cache = tmp_path / "cache"
    cache.mkdir()
    mine = _file(cache, "md-read-history-123.json", days=30)
    lookalike = _file(cache, "md-read-history-1234.json", days=30)
    prefixed = _file(cache, "md-read-history-x123.json", days=30)

    removed = _paths.prune_stale(str(cache), ttl_days=7.0, session_id="123")

    assert mine.exists()
    assert not lookalike.exists()
    assert not prefixed.exists()
    assert removed == 2


def test_a_held_lock_survives_another_sessions_sweep(tmp_path: Path) -> None:
    """Age cannot judge a lock file (#951).

    Its mtime freezes at creation — `O_CREAT` leaves an existing file alone and
    `flock` never touches it — so a lock held for longer than the TTL is
    indistinguishable by age from one abandoned that long ago. Unlinking a held
    one is not merely premature: the next caller creates a fresh inode at the
    same name and takes a *second* concurrent lock, which is the race this
    whole PR removes. The sweeping session is a different one on purpose, so
    the #920 session-ownership guard cannot be what saves the file.
    """
    cache = tmp_path / "cache"
    cache.mkdir()
    state = cache / f"session-intent-{OTHER}.json"
    _ = state.write_text("{}", encoding="utf-8")
    lock = Path(_state_lock.lock_path_for(str(state)))

    with _state_lock.state_lock(str(state)) as acquired:
        assert acquired is True
        _ = _aged(state, days=30)
        _ = _aged(lock, days=30)

        removed = _paths.prune_stale(str(cache), ttl_days=7.0, session_id=SID)

        assert lock.exists(), "a held lock was swept"
        assert _state_lock.lock_is_held(str(lock)) is True, "the probe took it"
    # The state file itself is not protected — only the lock is.
    assert removed == 1
    assert not state.exists()


def test_an_abandoned_lock_is_still_swept(tmp_path: Path) -> None:
    """The guard is a probe, not a blanket exemption for `.lock` names."""
    cache = tmp_path / "cache"
    cache.mkdir()
    stale = _file(cache, f"session-intent-{OTHER}.json.lock", days=30)

    removed = _paths.prune_stale(str(cache), ttl_days=7.0, session_id=SID)

    assert removed == 1
    assert not stale.exists()


def test_no_session_id_sweeps_everything_stale(tmp_path: Path) -> None:
    """Back-compat: callers that pass nothing get the pre-#920 behaviour."""
    cache = tmp_path / "cache"
    cache.mkdir()
    mine = _file(cache, f"session-intent-{SID}.json", days=30)

    removed = _paths.prune_stale(str(cache), ttl_days=7.0)

    assert removed == 1
    assert not mine.exists()


def test_resolve_cache_file_threads_session_id(tmp_path: Path) -> None:
    """The end-to-end path: resolving one entry must not sweep its siblings."""
    home = tmp_path / "praxis-home"
    cache = home / "cache"
    cache.mkdir(parents=True)
    os.environ["PRAXIS_HOME"] = str(home)
    os.environ["PRAXIS_CACHE_TTL_DAYS"] = "7"
    try:
        sibling = _file(cache, f"retrospect-active-{SID}.json", days=30)
        theirs = _file(cache, f"retrospect-active-{OTHER}.json", days=30)

        resolved = _paths.resolve_cache_file(
            f"session-intent-{SID}.json", session_id=SID
        )

        assert resolved == str(cache / f"session-intent-{SID}.json")
        assert sibling.exists()
        assert not theirs.exists()
    finally:
        del os.environ["PRAXIS_HOME"]
        del os.environ["PRAXIS_CACHE_TTL_DAYS"]
