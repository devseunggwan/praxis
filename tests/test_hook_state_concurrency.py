"""Two-process race fixture for session state files (issue #951).

`os.replace` serializes the write, not the read-modify-write around it. These
cases run the REAL hook impls in two concurrent OS processes against one state
file and assert the state that survives, because a single-process unit test
cannot observe an interleaving that only exists between processes.

Each case runs twice against the same code:

  * `nolock` — the child replaces `state_lock` with a no-op context manager,
    reproducing the pre-#951 behaviour. These assertions pin the defect in
    place, so a regression that removes the lock fails here rather than
    silently returning the suite to green.
  * `lock` — the shipped path.

Both arms open with a start barrier, because interpreter startup skew alone
exceeded the read window often enough that the unlocked arm read as "no race".
What holds the window open after that differs by arm, and has to:

  * `nolock` — a second barrier, released once *both* children have returned
    from the state read. A fixed sleep only makes the overlap likely; on a
    loaded box the first child can complete its whole read-modify-write before
    the second is scheduled, and the serialized result that produces fails the
    arm whose entire job is to prove the race.
  * `lock` — the fixed delay. A post-read barrier is impossible here: the read
    sits inside the critical section, so a child waiting there for a sibling
    that cannot enter until it leaves would deadlock the pair. This arm needs
    no overlap anyway — it asserts the second child waits the first out, which
    is why the delay must stay well under `PRAXIS_STATE_LOCK_TIMEOUT`'s 2s
    default.

Neither arm changes hook logic; both wrap the read at the same point.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / "hooks"

# Long enough that both children are provably inside the window together,
# short enough to stay far below the lock's 2s acquisition deadline.
READ_DELAY_SECONDS = 0.4

_DRIVER = '''
import contextlib
import importlib.util
import os
import sys
import time

(
    impl_path, lock_mode, delay, read_fn, argv_mode, ready_file, go_file,
    read_file, peer_read_file,
) = sys.argv[1:10]

spec = importlib.util.spec_from_file_location("impl_under_test", impl_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

if lock_mode == "nolock":
    @contextlib.contextmanager
    def _no_lock(path, timeout=None):
        yield False
    mod.state_lock = _no_lock

_orig_read = getattr(mod, read_fn)


def _slow_read(*args, **kwargs):
    """Real read, then hold the window open so the sibling reaches it too.

    Under `nolock` the hold is a barrier on the sibling's own read rather than
    a fixed sleep: a sleep only makes the overlap likely, and on a loaded CI
    box the first child can finish its whole read-modify-write before the
    second is scheduled at all — which produces a serialized result and fails
    the arm that exists to prove the race.

    The barrier is `nolock`-only, and cannot be otherwise: under `lock` the
    read happens inside the critical section, so waiting there for a sibling
    that cannot enter until this process leaves would deadlock both children.
    That arm keeps the sleep, which is all it needs — it asserts the second
    child waits the first out, not that the two overlap.
    """
    result = _orig_read(*args, **kwargs)
    if lock_mode == "nolock":
        open(read_file, "w").close()
        _read_deadline = time.monotonic() + 30
        while not os.path.exists(peer_read_file):
            if time.monotonic() >= _read_deadline:
                sys.exit("driver: post-read barrier timed out")
            time.sleep(0.001)
    else:
        time.sleep(float(delay))
    return result


setattr(mod, read_fn, _slow_read)

# Start barrier: interpreter startup alone skewed the two children by more
# than the read delay often enough to make the unlocked arm flake, which would
# have read as "the race is gone".
open(ready_file, "w").close()
_deadline = time.monotonic() + 30
while not os.path.exists(go_file):
    if time.monotonic() >= _deadline:
        sys.exit("driver: start barrier timed out")
    time.sleep(0.001)

if argv_mode:
    sys.exit(mod.main(["impl", argv_mode]))
sys.exit(mod.main())
'''


def _release_barrier(ready_files, go_file: Path) -> None:
    """Wait for every child to report ready, then let them all through."""
    deadline = time.monotonic() + 30
    while not all(f.exists() for f in ready_files):
        if time.monotonic() >= deadline:
            raise AssertionError("children never reached the start barrier")
        time.sleep(0.005)
    go_file.write_text("go", encoding="utf-8")


def _run_pair(driver: Path, impl: Path, lock_mode: str, payloads, read_fn, argv_mode=""):
    """Launch one child per payload at the same time; return their results.

    Each payload is staged as a file and handed over as the child's stdin.
    Feeding it through `communicate` instead would serialize the pair: the
    child blocks on stdin until the parent writes, and the parent's first
    `communicate` does not return until that child has exited — so the second
    child would not start until the first was done and no race could occur.
    """
    # The post-read barrier pairs each child with exactly one peer, so a third
    # payload would leave every child waiting on a file no one else writes.
    assert len(payloads) == 2, "the post-read barrier pairs exactly two children"

    go_file = driver.parent / "go"
    read_files = [driver.parent / f"read-{i}" for i in range(len(payloads))]
    ready_files = []
    procs = []
    stdin_files = []
    for index, payload in enumerate(payloads):
        payload_file = driver.parent / f"payload-{index}.json"
        payload_file.write_text(json.dumps(payload), encoding="utf-8")
        handle = payload_file.open("r", encoding="utf-8")
        stdin_files.append(handle)
        ready_file = driver.parent / f"ready-{index}"
        ready_files.append(ready_file)
        procs.append(
            subprocess.Popen(
                [
                    sys.executable,
                    str(driver),
                    str(impl),
                    lock_mode,
                    str(READ_DELAY_SECONDS),
                    read_fn,
                    argv_mode,
                    str(ready_file),
                    str(go_file),
                    str(read_files[index]),
                    str(read_files[1 - index]),
                ],
                stdin=handle,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=os.environ.copy(),
            )
        )
    _release_barrier(ready_files, go_file)
    try:
        outputs = []
        for proc in procs:
            stdout, stderr = proc.communicate(timeout=30)
            outputs.append((proc.returncode, stdout, stderr))
        return outputs
    finally:
        for handle in stdin_files:
            handle.close()


@pytest.fixture
def driver(tmp_path: Path) -> Path:
    path = tmp_path / "race_driver.py"
    path.write_text(_DRIVER, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# second-failure-advisory — a lost increment misfires the gate twice
# ---------------------------------------------------------------------------

_SECOND_FAILURE_IMPL = HOOKS / "postuse-correction" / "second-failure-advisory" / "impl.py"


def _failure_payload(session_id: str) -> dict:
    return {
        "session_id": session_id,
        "tool_name": "Bash",
        "tool_input": {"file_path": "/tmp/project/run.sh"},
        "tool_response": {"exit": 1, "error": "connection refused"},
    }


def _seed_first_failure(state_file: Path, session_id: str) -> None:
    """Drive one ordinary failure so the pair's count sits at 1."""
    env = os.environ.copy()
    env["PRAXIS_SECOND_FAILURE_ADVISORY_FILE"] = str(state_file)
    proc = subprocess.run(
        [sys.executable, str(_SECOND_FAILURE_IMPL)],
        input=json.dumps(_failure_payload(session_id)),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "", "first failure must not advise"


def _advisory_count(outputs) -> int:
    return sum(1 for _rc, stdout, _err in outputs if "additionalContext" in stdout)


def _counts(state_file: Path) -> list[int]:
    state = json.loads(state_file.read_text(encoding="utf-8"))
    return [v for v in state["failures"].values() if isinstance(v, int)]


@pytest.mark.parametrize(
    "lock_mode,expected_count,allowed_advisories",
    [
        # Both children read count=1 and both write 2, so the third failure is
        # never recorded. What reaches the model then splits two ways, and the
        # split is a scheduling detail rather than something to pin: usually
        # both children cross the `prior_count == 1` boundary and the advisory
        # fires twice (the duplicate recorded as unverified on #950), but the
        # two also share one `<path>.tmp` staging name, so one child's
        # `os.replace` can find it already renamed away, fail, and suppress its
        # own advisory. The lost increment is the invariant violation common to
        # both, and it is deterministic.
        ("nolock", 2, (1, 2)),
        # Serialized: 1 -> 2 (advises) -> 3 (silent).
        ("lock", 3, (1,)),
    ],
)
def test_second_failure_advisory_race(
    driver, tmp_path, monkeypatch, lock_mode, expected_count, allowed_advisories
):
    state_file = tmp_path / "second-failure-advisory-race.json"
    session_id = "race-session"
    monkeypatch.setenv("PRAXIS_SECOND_FAILURE_ADVISORY_FILE", str(state_file))

    _seed_first_failure(state_file, session_id)

    outputs = _run_pair(
        driver,
        _SECOND_FAILURE_IMPL,
        lock_mode,
        [_failure_payload(session_id), _failure_payload(session_id)],
        read_fn="_load_state",
    )

    for rc, _stdout, stderr in outputs:
        assert rc == 0, stderr

    assert _advisory_count(outputs) in allowed_advisories
    assert _counts(state_file) == [expected_count]


# ---------------------------------------------------------------------------
# pre-edit-md-escape-advisory — a lost Read entry misjudges the Edit gate
# ---------------------------------------------------------------------------

_MD_ESCAPE_IMPL = (
    HOOKS / "postuse-correction" / "pre-edit-md-escape-advisory" / "impl.py"
)


def _read_payload(session_id: str, file_path: str) -> dict:
    return {
        "session_id": session_id,
        "tool_name": "Read",
        "tool_input": {"file_path": file_path},
    }


def _recorded_reads(path: Path) -> list[str] | None:
    """Paths in the history file, or None when the file no longer parses."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))["read"]
    except (json.JSONDecodeError, KeyError):
        return None


@pytest.mark.parametrize(
    "lock_mode,both_recorded",
    [
        # Both children read the empty history and append only their own path,
        # so the surviving file holds one of the two — or none of them: the two
        # also stage through one shared `<path>.tmp` name, and interleaved
        # writes there publish a file that no longer parses, which `read_state`
        # reads as an empty history. Either way the Edit gate treats a file that
        # WAS Read as unread and warns — or denies, under
        # `PRAXIS_MD_ESCAPE_MODE=block`.
        ("nolock", False),
        ("lock", True),
    ],
)
def test_md_read_history_race(driver, tmp_path, monkeypatch, lock_mode, both_recorded):
    history_file = tmp_path / "md-read-history-race.json"
    monkeypatch.setenv("PRAXIS_MD_READ_HISTORY_FILE", str(history_file))
    session_id = "race-session"
    first = str(tmp_path / "alpha.md")
    second = str(tmp_path / "beta.md")

    outputs = _run_pair(
        driver,
        _MD_ESCAPE_IMPL,
        lock_mode,
        [_read_payload(session_id, first), _read_payload(session_id, second)],
        read_fn="load_history",
        argv_mode="post",
    )

    for rc, _stdout, stderr in outputs:
        assert rc == 0, stderr

    recorded = _recorded_reads(history_file)
    if both_recorded:
        assert recorded is not None, "history file did not survive the pair"
        assert sorted(recorded) == sorted([first, second])
    else:
        assert recorded is None or sorted(recorded) != sorted([first, second])
