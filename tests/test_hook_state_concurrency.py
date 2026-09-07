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

Not every arm pins a lost update. `jq-config-empty-dict-advisory` (#970) also
staged its write through a filename its sibling used, so an interleaving there
published bytes no reader can parse — and every reader answers unparseable
state with an empty one, which loses the whole set rather than one entry. That
arm therefore asserts what the surviving file cannot hold, and a companion
case pins the per-process staging name without needing to win a race.

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

import importlib.util
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
    read_file, peer_read_file, stage_mode,
) = sys.argv[1:11]

spec = importlib.util.spec_from_file_location("impl_under_test", impl_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

if lock_mode == "nolock":
    @contextlib.contextmanager
    def _no_lock(path, timeout=None):
        yield False
    mod.state_lock = _no_lock

if stage_mode == "shared":
    # Negative control for the Q0 re-grade (#1034). It removes exactly the
    # property those rows claim as their exemption — a staging name no
    # sibling can collide on — and nothing else: both children stage through
    # ONE name, opened without O_TRUNC and pre-filled to a longer sibling's
    # length, so the shorter write leaves that sibling's tail in place. The
    # filler is non-UTF-8 so the published file is unambiguously unreadable
    # rather than merely surprising. This is the #970 mechanism, and it is
    # what tells a real 0 apart from a harness that never reached the state
    # file at all (the trap hit during #1017 verification).
    import tempfile

    _FILLER = b"\\xff" * 4096

    def _shared_fd(path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
        os.write(fd, _FILLER)
        os.lseek(fd, 0, os.SEEK_SET)
        return fd

    def _shared_mkstemp(suffix=None, prefix=None, dir=None, text=False):
        # Signature mirrors `tempfile.mkstemp` exactly, parameter order
        # included, so a future row that calls it positionally or passes
        # `text=` exercises the control instead of crashing the child on a
        # TypeError. `text` is accepted and ignored because POSIX ignores it
        # too: tempfile's text and binary open flags differ only by
        # `os.O_BINARY`, which exists on Windows alone, and praxis hooks
        # target POSIX hosts.
        shared = os.path.join(dir or ".", "praxis-shared-stage.tmp")
        return _shared_fd(shared), shared

    tempfile.mkstemp = _shared_mkstemp

    _real_open = open

    def _shared_open(file, mode="r", *args, **kwargs):
        # A hook that stages nothing writes the final name in place, so the
        # shared name IS the state file and there is no mkstemp to replace.
        if "w" not in mode:
            return _real_open(file, mode, *args, **kwargs)
        return os.fdopen(_shared_fd(file), "w", encoding=kwargs.get("encoding"))

    mod.open = _shared_open

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
    if lock_mode != "lock":
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


def _run_pair(
    driver: Path,
    impl: Path,
    lock_mode: str,
    payloads,
    read_fn,
    argv_mode="",
    stage_mode="",
):
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
                    stage_mode,
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
        # both children advise as "2회째" (the duplicate recorded as unverified
        # on #950), but the two also share one `<path>.tmp` staging name, so one
        # child's `os.replace` can find it already renamed away, fail, and
        # suppress its own advisory. The lost increment is the invariant
        # violation common to both, and it is deterministic — it is also what
        # the count assertion below, not the advisory count, discriminates on.
        ("nolock", 2, (1, 2)),
        # Serialized: 1 -> 2 (advises) -> 3 (advises too, since #1012 widened
        # the fire condition from the `prior_count == 1` boundary to every
        # occurrence >= 2).
        ("lock", 3, (2,)),
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


# ---------------------------------------------------------------------------
# jq-config-empty-dict-advisory — a lost entry repeats one advisory, a
# corrupted state file drops the session's whole dedup set (issue #970)
# ---------------------------------------------------------------------------

_JQ_CONFIG_IMPL = (
    HOOKS / "advisory-nudge" / "jq-config-empty-dict-advisory" / "impl.py"
)


def _jq_payload(session_id: str, config_path: str) -> dict:
    return {
        "session_id": session_id,
        "tool_name": "Bash",
        "tool_input": {"command": f"jq '.' {config_path}"},
    }


def _dedup_state_file(praxis_home: Path, session_id: str) -> Path:
    """Where the hook keeps its dedup set. It has no env override — the path
    is `resolve_cache_file`'s, so `PRAXIS_HOME` is the only way to redirect it."""
    return praxis_home / "cache" / f"jq-config-advisory-{session_id}.json"


def _advised_paths(path: Path) -> list[str] | None:
    """Paths in the dedup set, or None when the file is gone or no longer parses."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, list) else None


@pytest.mark.parametrize(
    "lock_mode,both_recorded",
    [
        # Two outcomes, not one, which is why the unlocked assertion is
        # "cannot hold both" rather than "holds exactly one". Both children
        # read the absent state and publish a set containing only their own
        # path — the lost update #951 graded as a repeated advisory. But the
        # two also staged through one shared `<path>.tmp` name, and a pair
        # interleaved there publishes bytes that no longer parse, which
        # `_load_seen`'s `except ValueError` turns into an empty set: the
        # session's entire dedup set, not one entry. On the pre-fix code both
        # showed up unforced — 4 and 5 of 100 uninstrumented pairs (#970).
        ("nolock", False),
        ("lock", True),
    ],
)
def test_jq_config_dedup_race(driver, tmp_path, monkeypatch, lock_mode, both_recorded):
    praxis_home = tmp_path / "praxis-home"
    monkeypatch.setenv("PRAXIS_HOME", str(praxis_home))
    session_id = "race-session"

    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    first = config_dir / "alpha.json"
    second = config_dir / "beta.json"
    # Size 0 — the [config-empty] branch, which is the one that records a
    # dedup entry without shelling out to `jq` to validate the contents.
    first.write_bytes(b"")
    second.write_bytes(b"")

    outputs = _run_pair(
        driver,
        _JQ_CONFIG_IMPL,
        lock_mode,
        [
            _jq_payload(session_id, str(first)),
            _jq_payload(session_id, str(second)),
        ],
        read_fn="_load_seen",
    )

    for rc, _stdout, stderr in outputs:
        assert rc == 0, stderr
        assert "[config-empty]" in stderr, "child emitted no advisory to dedup"

    recorded = _advised_paths(_dedup_state_file(praxis_home, session_id))
    if both_recorded:
        assert recorded is not None, "dedup state did not survive the pair"
        assert sorted(recorded) == sorted([str(first), str(second)])
    else:
        assert recorded is None or sorted(recorded) != sorted([str(first), str(second)])


def _load_impl(path: Path):
    spec = importlib.util.spec_from_file_location(f"impl_{path.parent.name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_jq_config_staging_name_is_per_process(tmp_path, monkeypatch):
    """The staging name has to vary per process, pinned without a race.

    The race arm above proves the pair misbehaves but not *which* of the two
    defects it hit on that scheduling, so a regression to the shared `.tmp`
    name could pass it on a lucky run. This is the deterministic half: the
    staging file is what one process can overwrite under another's feet, and
    it is also the floor under `state_lock`'s documented fail-open path —
    with the lock present and never acquired, the shared name still published
    unparseable state and the per-process one only ever lost an entry (#970).
    """
    impl = _load_impl(_JQ_CONFIG_IMPL)
    staged: list[str] = []
    real_replace = os.replace

    def _recording_replace(src, dst):
        staged.append(str(src))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _recording_replace)
    state = tmp_path / "jq-config-advisory-race-session.json"

    monkeypatch.setattr(os, "getpid", lambda: 4242)
    impl._save_seen(str(state), {"/tmp/.claude/alpha.json"})
    monkeypatch.setattr(os, "getpid", lambda: 5353)
    impl._save_seen(str(state), {"/tmp/.claude/beta.json"})

    assert len(staged) == 2
    assert staged[0] != staged[1], "both processes staged through one filename"
    assert _advised_paths(state) == ["/tmp/.claude/beta.json"]


def test_jq_config_staging_file_is_unlinked_on_failure(tmp_path, monkeypatch):
    """A failed publish must not leave its staging file behind.

    Per-process names trade one leaked `<state>.tmp` for one per pid, so the
    cleanup that the shared name could skip is now load-bearing.
    """
    impl = _load_impl(_JQ_CONFIG_IMPL)

    def _failing_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", _failing_replace)
    state = tmp_path / "jq-config-advisory-race-session.json"
    impl._save_seen(str(state), {"/tmp/.claude/alpha.json"})

    assert not state.exists()
    assert list(tmp_path.iterdir()) == [], "staging file survived a failed replace"


# ---------------------------------------------------------------------------
# Q0 re-grade of the unlocked `resolve_cache_file` consumers (issue #1034)
# ---------------------------------------------------------------------------
#
# #970 re-graded only the `jq-config` row against Q0 (the corruption axis) and
# left the rest as an author assertion: "Q0 was applied to the other six only
# far enough to see that it moves no other row." Two of those six are the
# already-locked rows above, whose shipped arms assert a state file that still
# parses — that is their Q0 measurement. The remaining rows are these.
#
# `lock_mode` is not, on its own, the negative control here. These modules
# import no `state_lock` at all, so a `nolock` arm has nothing to replace and
# would run byte-identical code in both arms; they pass `unlocked` purely to
# arm the post-read barrier, which is what puts both children at the write
# together. The control is `stage_mode="shared"`, which strips the one
# property their exemption rests on — a staging name a sibling cannot collide
# on — and nothing else. Without it a 0 could not be told apart from a harness
# that never reached the state file, the exact trap hit during #1017.
#
# `postcompact-context` was the fourth row here, and the one this measurement
# moved: it staged through no name at all, 5 of 300 unforced pairs published a
# short write over a longer sibling's tail, and #1034 gave it `state_lock` plus
# `tempfile.mkstemp` staging. #1339 then moved the hook from `UserPromptSubmit`
# to `SessionStart(compact)`, where the event fires once per compaction, and
# deleted the uuid state file the row measured. The row, its race arms, and
# the deterministic staging / lock-ordering cases are gone with it; the
# measurement stays on record in docs/hook-state-concurrency-measurements.md.

_Q0_ROWS = (
    "worktree-prune-snapshot-gate",
    "retrospect-active-marker",
    "session-intent",
)

_Q0_SESSION = "race-session"


def _q0_spec(row: str, tmp_path: Path) -> dict:
    """Impl, env override, payload pair, and barrier point for one row.

    The two payloads are deliberately unequal in length wherever the hook lets
    them be: a shared staging name only corrupts when the shorter write leaves
    a longer sibling's tail behind, so equal-length payloads would make the
    negative control itself unfalsifiable.
    """
    if row == "worktree-prune-snapshot-gate":
        state = tmp_path / "worktree-prune-snapshot.json"
        payload = {
            "session_id": _Q0_SESSION,
            "tool_name": "Bash",
            "tool_input": {"command": "git worktree list --porcelain"},
        }
        return {
            "impl": HOOKS / "preflight-gate" / row / "impl.py",
            "env": {"PRAXIS_WORKTREE_PRUNE_SNAPSHOT_FILE": str(state)},
            "state": state,
            # The snapshot flag is the same literal for both siblings, so this
            # row cannot corrupt on content grounds even with one staging name
            # — the control arm's filler is what supplies the length difference
            # the hook itself never produces.
            "barrier_fn": "read_state",
            "lock_modes": ("unlocked", "unlocked"),
            "payloads": [payload, payload],
        }
    if row == "retrospect-active-marker":
        state = tmp_path / "retrospect-active.json"
        return {
            "impl": HOOKS / "preflight-gate" / row / "impl.py",
            "env": {"PRAXIS_RETROSPECT_ACTIVE_FILE": str(state)},
            "state": state,
            # No read to barrier on — the marker is a whole-file write — so
            # the barrier sits on the path resolution immediately before it.
            "barrier_fn": "resolve_state_path",
            "lock_modes": ("unlocked", "unlocked"),
            "payloads": [
                {
                    "session_id": _Q0_SESSION,
                    "hookEventName": "PreToolUse",
                    "tool_name": "Skill",
                    "tool_input": {"skill": skill},
                }
                for skill in ("praxis:retrospect", "retrospect")
            ],
        }
    if row == "session-intent":
        state = tmp_path / "session-intent.json"
        return {
            "impl": HOOKS / "preflight-gate" / row / "impl.py",
            "env": {"PRAXIS_SESSION_INTENT_FILE": str(state)},
            "state": state,
            "barrier_fn": "read_state",
            "lock_modes": ("unlocked", "unlocked"),
            "payloads": [
                {
                    "session_id": _Q0_SESSION,
                    "hookEventName": "UserPromptSubmit",
                    "prompt": prompt,
                }
                # `first_prompt_snippet` carries up to 200 prompt characters
                # into the state, so unequal prompts write unequal files.
                for prompt in ("read the diff", "review the diff " + "x" * 150)
            ],
        }
    raise ValueError(f"no Q0 spec for row {row!r} — add it to _q0_spec and _Q0_ROWS together")


def _state_parses(path: Path) -> bool:
    """Whether a reader gets the state back, rather than `except ValueError`."""
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return True


@pytest.mark.parametrize("stage_mode", ["shipped", "shared"])
@pytest.mark.parametrize("row", _Q0_ROWS)
def test_q0_staging_collision(driver, tmp_path, monkeypatch, row, stage_mode):
    spec = _q0_spec(row, tmp_path)
    for key, value in spec["env"].items():
        monkeypatch.setenv(key, value)

    outputs = _run_pair(
        driver,
        spec["impl"],
        spec["lock_modes"][0 if stage_mode == "shipped" else 1],
        spec["payloads"],
        read_fn=spec["barrier_fn"],
        stage_mode="" if stage_mode == "shipped" else "shared",
    )

    for rc, _stdout, stderr in outputs:
        assert rc == 0, stderr

    assert spec["state"].exists(), "the pair never reached the state file"
    if stage_mode == "shipped":
        assert _state_parses(spec["state"]), (
            "shipped staging published state no reader can parse — this row is "
            "not Q0-exempt and needs `state_lock`"
        )
    else:
        assert not _state_parses(spec["state"]), (
            "the negative control produced no corruption: a 0 in the shipped "
            "arm proves nothing until this arm can fail it"
        )
