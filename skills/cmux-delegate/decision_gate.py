"""Decision gate for a delegated cmux worker (issue #984).

A delegated worker reaches points where it must not decide alone — a mutation
needing approval, a requirement with two readings. Its only channel was the
completion report, which is written once at the end, so the worker's choices
were to proceed on its own authority or stop silently.

#1054 restored the worker's stdin, so a *permission* prompt now has a channel —
a human at the pane can answer it. This gate is still the only channel for the
questions no permission prompt asks (which of two readings a requirement has,
whether a plan is the right one), and the only one that survives nobody looking
at the pane, because the question lands in a file the delegator can read later.

This module gives the worker a third option: publish the question, block, and
resume on the delegator's verdict.

WHAT THIS DOES NOT BUILD. cmux ships the blocking primitive — `cmux wait-for
[-S|--signal] <name> [--timeout <seconds>]` — so there is no wait loop and no
timeout machinery here. What the primitive cannot do is carry a payload: a
signal says "go", never "no, and here is why". So the verdict travels in a
file and the signal is only the doorbell. That split is the whole design.

NO NEW SUPERVISORY LOOP. #155 deleted a dispatch/poll/collect layer whose
stated defect was abstracting a workflow the user runs directly, and the
workflow that survived was one task per delegation, supervised by hand. So
nothing here polls: the pending question is read at Step 7, where the
delegator already stands to judge the report.

THE TOKEN IS PER DECISION, NOT PER WORKSPACE. Measured on cmux 0.64.22, the
token latches — a signal sent before anyone waits is retained and satisfies
the next waiter — and it is one-shot, consumed by that waiter:

    $ cmux wait-for -S praxis-race-probe          # nobody waiting yet
    OK
    $ cmux wait-for praxis-race-probe --timeout 5
    OK                                            # latched signal, returns at once
    $ cmux wait-for praxis-race-probe --timeout 4
    Error: wait-for timed out waiting for 'praxis-race-probe'

The latch removes the lost-wakeup race, which is why `ask` may write its file
and call `wait-for` without ordering care. But it creates the opposite hazard:
a verdict signalled to a worker that died before waiting stays latched, and a
workspace-named token would hand that stale "go" to the NEXT decision from the
same workspace. So each question mints its own token and records it in the
file; the delegator signals the token it reads there, never one it derives.

UNAVAILABLE IS NOT APPROVAL. Every failure to reach a verdict — no cmux, an
unwritable file, a timeout — resolves to a verdict the worker must treat as
"do not proceed". A gate that fails open is not a gate. This is the one place
praxis's host-neutral posture (ARCHITECTURE.md) does not mean "degrade and
continue": on a host without cmux the worker cannot ask, so it must stop and
say so in its report rather than act unsupervised.

The decision file's path is resolved by decision-gate.sh and passed in. This
module never derives it — `_paths.sh` lives under hooks/, and importing its
Python twin from a skill would invert the layering (#981).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid

_Record = dict[str, object]


def _read(path: str) -> _Record | None:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write(path: str, record: _Record) -> bool:
    """Atomic write (temp + `os.replace`): `open(path, "w")` truncates before
    writing, so a concurrent `_read` can observe a half-written file. That
    read raises `ValueError`, which `_read` cannot tell apart from "no file",
    and `resolve` then reports the decision as never having been asked
    (#1031). Same convention as `session-intent`'s `write_state` — a sibling
    on the same truncate-vs-crash hazard (issue #647 H7).

    The temp file is created in `path`'s own directory (never a fixed
    fallback like `/tmp`): `os.replace` requires both on the same filesystem,
    and a missing directory must fail here exactly as the old `open(path,
    "w")` did, not silently write elsewhere.
    """
    parent = os.path.dirname(path) or "."
    try:
        fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".decision-gate-")
    except OSError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        # Broad on purpose: json.dump can raise TypeError, not just OSError;
        # any failure before os.replace must still unlink the temp file.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return False
    return True


def _cmux(args: list[str], timeout: float) -> int | None:
    """Exit status, or None when cmux could not be run at all. The two are
    distinct: a non-zero status is cmux answering, None is cmux absent."""
    try:
        completed = subprocess.run(
            ["cmux", *args], capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.returncode


_CLAIM_SUFFIX = ".claim"
_LOSER_TIMEOUT_SECONDS = 2.0
_LOSER_POLL_SECONDS = 0.005


def _claim(path: str) -> bool:
    """True when THIS process is the one that gets to record the verdict.

    `resolve` is a read-modify-write, and two delegators resolving one pending
    decision could both read `pending`, both write, and both signal. The second
    signal is the damage: it latches, and the next question from this workspace
    is released with nobody having answered it.

    `O_CREAT|O_EXCL` is the arbiter rather than a lock file — one syscall, no
    deadline, no `fcntl` availability question, and the winner is decided by
    the kernel. The repo's `_state_lock` would do this too, but it lives under
    hooks/ and a skill importing it inverts the layering (#981).

    A non-EEXIST failure (unwritable directory) answers True: arbitration is
    impossible there, and the `_write` immediately after fails for the same
    reason and returns before anything is signalled.
    """
    try:
        handle = os.open(path + _CLAIM_SUFFIX, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    except OSError:
        return True
    os.close(handle)
    return True


def _release_claim(path: str) -> None:
    """The claim's lifetime is one question, so publishing a new one clears it."""
    try:
        os.unlink(path + _CLAIM_SUFFIX)
    except OSError:
        pass


def _await_verdict(path: str) -> _Record | None:
    """The losing resolver's read. The winner may not have written yet, so this
    waits briefly rather than reporting `pending` for a decision that is about
    to be resolved a millisecond later."""
    deadline = time.monotonic() + _LOSER_TIMEOUT_SECONDS
    while True:
        record = _read(path)
        if record is not None and record.get("status") in ("approved", "rejected"):
            return record
        if time.monotonic() >= deadline:
            return None
        time.sleep(_LOSER_POLL_SECONDS)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ask(path: str, workspace: str, question: str, timeout: int) -> _Record:
    # The random tail is what makes the token unique, not the clock: a worker
    # asking twice inside one second with one pid produced the SAME name, and
    # a one-shot latched signal for the first question would then release the
    # second. The timestamp stays only because it makes the file readable.
    token = f"praxis-decision-{workspace}-{int(time.time())}-{uuid.uuid4().hex[:12]}"
    record: _Record = {
        "workspace": workspace,
        "question": question,
        "token": token,
        "asked_at": _now(),
        "status": "pending",
    }
    _release_claim(path)
    if not _write(path, record):
        return {"verdict": "unavailable", "reason": "결정 파일을 쓸 수 없습니다"}

    # cmux owns the deadline. The subprocess timeout is a margin over it for a
    # wedged process, never a second clock racing the first.
    status = _cmux(["wait-for", token, "--timeout", str(timeout)], timeout=timeout + 30)
    if status is None:
        return {"verdict": "unavailable", "reason": "cmux 없음 — 승인을 받을 수 없습니다"}

    # Re-read whatever the deadline outcome was. A delegator that wrote the
    # verdict but failed to signal is indistinguishable from silence at the
    # `wait-for` layer, and the file is the authority on the verdict.
    resolved = _read(path) or {}
    if resolved.get("token") == token and resolved.get("status") in ("approved", "rejected"):
        return {
            "verdict": str(resolved["status"]),
            "reason": str(resolved.get("reason", "")),
        }
    if status == 0:
        # Woken with nothing decided. A latched token from an earlier decision
        # is the known way this happens, and proceeding on it would consume a
        # different question's approval.
        return {"verdict": "unavailable", "reason": "신호는 왔으나 판정이 기록되지 않았습니다"}
    return {"verdict": "timeout", "reason": f"{timeout}초 안에 판정 없음"}


def resolve(path: str, workspace: str, verdict: str, reason: str) -> _Record:
    record = _read(path)
    if record is None:
        return {"state": "none", "workspace": workspace}
    if record.get("status") in ("approved", "rejected"):
        # Idempotent, and deliberately silent on cmux: a second signal would
        # latch and satisfy this workspace's NEXT decision unasked.
        return {
            "state": "resolved",
            "workspace": workspace,
            "verdict": str(record["status"]),
            "reason": str(record.get("reason", "")),
        }

    if not _claim(path):
        # Another resolver won. It writes the verdict and signals; this one
        # reports what was actually decided and signals nothing.
        settled = _await_verdict(path)
        if settled is None:
            return {
                "state": "pending",
                "workspace": workspace,
                "reason": "다른 resolve 가 진행 중입니다",
            }
        return {
            "state": "resolved",
            "workspace": workspace,
            "verdict": str(settled["status"]),
            "reason": str(settled.get("reason", "")),
        }

    record["status"] = verdict
    record["reason"] = reason
    record["resolved_at"] = _now()
    if not _write(path, record):
        return {"state": "error", "workspace": workspace, "reason": "결정 파일을 쓸 수 없습니다"}

    token = str(record.get("token", ""))
    # Read from the file, never rebuilt here: a derived name would be the same
    # string for every decision from this workspace, which is exactly what
    # makes a stale latched signal reachable.
    signalled = _cmux(["wait-for", "--signal", token], timeout=30) if token else None
    return {
        "state": "resolved",
        "workspace": workspace,
        "verdict": verdict,
        "reason": reason,
        "signalled": "yes" if signalled == 0 else "no",
    }


def status(path: str, workspace: str) -> _Record:
    record = _read(path)
    if record is None:
        return {"state": "none", "workspace": workspace}
    state = str(record.get("status", "pending"))
    result: _Record = {
        "state": "pending" if state == "pending" else "resolved",
        "workspace": workspace,
        "question": str(record.get("question", "")),
    }
    if state != "pending":
        result["verdict"] = state
        result["reason"] = str(record.get("reason", ""))
    return result


def _shell_quote(value: str) -> str:
    """POSIX single-quoting: close, escape, reopen for an embedded quote."""
    return "'" + value.replace("'", "'\\''") + "'"


def _format(result: _Record) -> str:
    """`key='value'` pairs on one line — the shape a shell caller reads with
    `case` without a JSON parser.

    Single-quoted unconditionally. `question` and `reason` carry text written
    by the delegated agent, so under the documented `eval "$(...)"` a
    double-quoted value would run any `$(...)` in that text inside the
    DELEGATOR's shell. Same reasoning as agent_liveness.py, same remedy.
    """
    order = ("state", "verdict", "reason", "workspace", "question", "signalled")
    parts = []
    for key in order:
        if key not in result:
            continue
        value = str(result[key]).replace("\n", " ").strip()
        parts.append(f"{key}={_shell_quote(value)}")
    return " ".join(parts)


def _usage() -> int:
    print(
        "usage: decision_gate.py ask <file> <workspace> <timeout> <question>\n"
        "       decision_gate.py resolve <file> <workspace> approve|reject [reason]\n"
        "       decision_gate.py status <file> <workspace>",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        return _usage()
    command, path, workspace = argv[1], argv[2], argv[3]

    if command == "ask":
        if len(argv) != 6:
            return _usage()
        try:
            timeout = int(argv[4])
        except ValueError:
            return _usage()
        if timeout <= 0:
            return _usage()
        result = ask(path, workspace, argv[5], timeout)
    elif command == "resolve":
        if len(argv) not in (5, 6) or argv[4] not in ("approve", "reject"):
            return _usage()
        verdict = "approved" if argv[4] == "approve" else "rejected"
        result = resolve(path, workspace, verdict, argv[5] if len(argv) == 6 else "")
    elif command == "status":
        if len(argv) != 4:
            return _usage()
        result = status(path, workspace)
    else:
        return _usage()

    print(_format(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
