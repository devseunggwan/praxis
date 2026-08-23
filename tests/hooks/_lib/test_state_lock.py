"""Unit coverage for the shared state lock (issue #951).

The cross-process behaviour that motivates the helper is pinned in
`tests/test_hook_state_concurrency.py`, which runs the real hooks in two
processes. What is checked here is the contract those call sites depend on:
the lock is exclusive across processes, it is released on every exit path, and
every failure mode yields False instead of raising — a hook that cannot lock
must degrade to the racy behaviour, never to a blocked tool call.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "hooks" / "_lib"))

import _state_lock  # noqa: E402


# Holds the lock for a fixed span so the parent can observe contention.
_HOLDER = """
import sys, time
sys.path.insert(0, sys.argv[1])
import _state_lock

with _state_lock.state_lock(sys.argv[2]) as acquired:
    if not acquired:
        sys.exit("holder failed to acquire")
    open(sys.argv[3], "w").close()
    time.sleep(float(sys.argv[4]))
"""


def test_lock_path_is_a_sibling_of_the_state_file(tmp_path):
    state = tmp_path / "session-state.json"
    assert _state_lock.lock_path_for(str(state)) == str(state) + ".lock"


def test_lock_is_held_answers_only_while_someone_holds_it(tmp_path):
    """The probe the cache sweep uses instead of an age check.

    A lock file's mtime freezes at creation, so age cannot separate a lock held
    right now from one abandoned a month ago. Taking it can: the attempt fails
    precisely when someone else has it. Two `open()` calls contend even inside
    one process, because `flock` is held per open file description rather than
    per process — so no child is needed to observe the held case.
    """
    state = str(tmp_path / "state.json")
    lock = _state_lock.lock_path_for(state)

    assert _state_lock.lock_is_held(lock) is False, "no file yet"

    with _state_lock.state_lock(state) as acquired:
        assert acquired is True
        assert _state_lock.lock_is_held(lock) is True

    # Released — and the probe must not leave the lock taken on its way out,
    # or the next holder would be locked out by the sweep that asked.
    assert _state_lock.lock_is_held(lock) is False
    with _state_lock.state_lock(state, timeout=0.05) as acquired:
        assert acquired is True


def test_timeout_default_and_override(monkeypatch):
    monkeypatch.delenv("PRAXIS_STATE_LOCK_TIMEOUT", raising=False)
    assert _state_lock.lock_timeout() == 2.0
    monkeypatch.setenv("PRAXIS_STATE_LOCK_TIMEOUT", "0.25")
    assert _state_lock.lock_timeout() == 0.25


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "soon",
        "-1",
        # `float()` accepts all four of these and none is caught by a `< 0`
        # check — `nan < 0` and `inf < 0` are both False. Each would reach the
        # acquisition loop as a `time.monotonic() >= deadline` that can never
        # be True, so a contended hook spins until its tool call times out.
        # That is a hang rather than an exception, which is exactly what
        # `@fail_open` cannot catch.
        "nan",
        "NaN",
        "inf",
        "Infinity",
    ],
)
def test_malformed_timeout_falls_back_to_the_default(monkeypatch, raw):
    monkeypatch.setenv("PRAXIS_STATE_LOCK_TIMEOUT", raw)
    assert _state_lock.lock_timeout() == 2.0


def test_acquires_and_releases(tmp_path):
    state = str(tmp_path / "state.json")
    with _state_lock.state_lock(state) as acquired:
        assert acquired is True
    # Released: the same path locks again without waiting out any deadline.
    started = time.monotonic()
    with _state_lock.state_lock(state, timeout=0.05) as acquired:
        assert acquired is True
    assert time.monotonic() - started < 0.05


def test_released_even_when_the_body_raises(tmp_path):
    state = str(tmp_path / "state.json")
    with pytest.raises(ValueError):
        with _state_lock.state_lock(state):
            raise ValueError("body failed")
    with _state_lock.state_lock(state, timeout=0.05) as acquired:
        assert acquired is True


def test_creates_the_parent_directory(tmp_path):
    state = str(tmp_path / "nested" / "dir" / "state.json")
    with _state_lock.state_lock(state) as acquired:
        assert acquired is True
    assert os.path.isdir(os.path.dirname(state))


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="chmod 0o500 does not restrict root, so the unwritable-location "
    "premise cannot hold when the suite runs as root (#1100)",
)
def test_unwritable_location_yields_false_without_raising(tmp_path):
    denied = tmp_path / "denied"
    denied.mkdir()
    denied.chmod(0o500)
    try:
        with _state_lock.state_lock(str(denied / "state.json"), timeout=0.05) as acquired:
            assert acquired is False
    finally:
        denied.chmod(0o700)


def test_excludes_a_second_process_then_admits_it(tmp_path):
    state = str(tmp_path / "state.json")
    held_marker = tmp_path / "held"
    holder_script = tmp_path / "holder.py"
    holder_script.write_text(_HOLDER, encoding="utf-8")

    holder = subprocess.Popen(
        [
            sys.executable,
            str(holder_script),
            str(REPO_ROOT / "hooks" / "_lib"),
            state,
            str(held_marker),
            "1.0",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while not held_marker.exists():
            assert holder.poll() is None, "holder exited before taking the lock"
            assert time.monotonic() < deadline, "holder never took the lock"
            time.sleep(0.005)

        # Contended: a deadline shorter than the hold yields False rather than
        # waiting for the holder or raising — and it yields at the deadline,
        # not when the holder happens to finish. That elapsed bound is the
        # property, not the return value: a hook whose sibling was killed while
        # holding the lock must not sit on the tool call it was only observing,
        # and a blocking `flock` returning False after the full hold would
        # satisfy every other assertion here.
        started = time.monotonic()
        with _state_lock.state_lock(state, timeout=0.05) as acquired:
            assert acquired is False
        waited = time.monotonic() - started
        assert waited < 0.5, f"waited {waited:.3f}s on a 0.05s deadline"
    finally:
        holder.wait(timeout=15)

    # Uncontended again once the holder exits — an exited process releases the
    # flock even though nothing unlinked the lock file.
    with _state_lock.state_lock(state, timeout=0.5) as acquired:
        assert acquired is True
