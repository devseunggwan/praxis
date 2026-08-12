"""Serialize read-modify-write on a session state file (issue #951).

`os.replace` makes the *write* atomic; it does nothing about the window
between a hook reading the state and replacing it. Two hook processes sharing
a `session_id` — parallel sub-agent tool calls are the ordinary source — read
the same value, each increments its own copy, and the later `os.replace`
discards the earlier increment.

Whether that loss is a defect depends on what the state means, so this helper
is applied selectively rather than to every consumer of `resolve_cache_file`.
The criterion and the per-hook verdicts live in
[`DESIGN.md`](../../DESIGN.md#session-state-concurrency) — read it before
adding a call site.

    with state_lock(path):
        state = load(path)
        state["count"] += 1
        save(path, state)

`state_lock` never raises and never blocks past its deadline: acquisition
failure yields False and the caller proceeds exactly as it did before the
lock existed. That is the `@fail_open` contract (DESIGN.md) — a hook that
cannot take a lock must degrade to the racy behaviour, never to a blocked
tool call. Readers need no lock: `os.replace` guarantees they see either the
whole previous file or the whole next one.
"""
from __future__ import annotations

import contextlib
import math
import os
import time
from typing import Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX host
    # Imported at module scope by hook impls, so an unguarded ImportError here
    # would take the hook down *outside* `@fail_open`'s reach (it wraps main(),
    # not the import). praxis targets POSIX hosts; on anything else the lock
    # degrades to the same no-op as a failed acquisition.
    fcntl = None  # type: ignore[assignment]

_TIMEOUT_ENV = "PRAXIS_STATE_LOCK_TIMEOUT"
_DEFAULT_TIMEOUT_SECONDS = 2.0
_POLL_INTERVAL_SECONDS = 0.005


LOCK_SUFFIX = ".lock"


def lock_path_for(path: str) -> str:
    """Companion lock file for a state file.

    A sibling rather than the state file itself: the writers this serializes
    finish with `os.replace`, which swaps the inode out from under any lock
    held on it, so the next process would lock a file no longer at that name.
    The `.lock` suffix keeps `_paths._belongs_to_session` matching, so the
    calling session's own cache sweep treats it as its own entry.

    That match protects the lock from *its owner's* sweep only. Another
    session's sweep sees a name keyed on someone else and judges the file by
    age — and a lock file's mtime freezes at creation, because `O_CREAT`
    leaves an existing file alone and `flock` never touches it. `_paths`
    therefore probes the lock before unlinking a `.lock` entry; see
    `_paths._lock_is_held`.
    """
    return path + LOCK_SUFFIX


def lock_is_held(lock_path: str) -> bool:
    """True when some process currently holds the lock on `lock_path`.

    The cache sweep judges every other entry by age, which cannot work here:
    a lock file's mtime stops advancing the moment it is created, so a lock
    held for longer than the TTL looks exactly like one abandoned that long
    ago. Taking the lock is the only way to tell them apart — it succeeds
    precisely when no one else holds it.

    Answers False when it cannot tell (no `fcntl`, file already gone), which
    hands the decision back to the caller's age check rather than pinning an
    entry in the cache forever on an inconclusive probe.
    """
    if fcntl is None:
        return False
    try:
        fd = os.open(lock_path, os.O_RDWR)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        return False
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def lock_timeout() -> float:
    """Seconds to wait for the lock. `PRAXIS_STATE_LOCK_TIMEOUT` overrides.

    A malformed or negative value falls back to the default rather than
    disabling the wait — a typo in the override must not silently reintroduce
    the race it was set to tune.

    `float()` accepts `nan` and `inf`, and neither is caught by the negative
    check: `nan < 0` and `inf < 0` are both False. Either one reaches the
    acquisition loop's `time.monotonic() >= deadline` as a comparison that can
    never be True, so a contended hook spins until the tool call times out.
    That failure is a hang rather than an exception, so `@fail_open` cannot
    reach it — the guard has to be here.
    """
    raw = os.environ.get(_TIMEOUT_ENV)
    if raw is None:
        return _DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT_SECONDS
    if value < 0 or not math.isfinite(value):
        return _DEFAULT_TIMEOUT_SECONDS
    return value


@contextlib.contextmanager
def state_lock(path: str, timeout: float | None = None) -> Iterator[bool]:
    """Hold an exclusive advisory lock over `path` for the block's duration.

    Yields True when the lock was taken, False when it was not (unwritable
    directory, non-POSIX host, or `timeout` seconds of contention). The body
    runs either way — a caller that wants to know checks the yielded value.

    Non-blocking retries rather than a blocking `flock`: a blocking call has
    no deadline, and a hook process wedged behind a lock left by a killed
    sibling would stall the tool call it was meant to observe.
    """
    if fcntl is None:
        yield False
        return

    deadline = time.monotonic() + (lock_timeout() if timeout is None else timeout)
    fd = None
    try:
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        fd = os.open(lock_path_for(path), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        if fd is not None:
            os.close(fd)
        yield False
        return

    acquired = False
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(_POLL_INTERVAL_SECONDS)
        yield acquired
    finally:
        try:
            if acquired:
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass
