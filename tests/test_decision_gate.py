"""tests/test_decision_gate.py — delegated-worker decision gate (#984).

The gate's whole value is that it refuses on every path that is not an
explicit approval, so most of what is pinned here is the shape of NOT
proceeding: a timeout, an absent cmux, and a wake with nothing recorded are
three different reasons and none of them is `approved`.

The cmux behaviours these tests stand on were transcribed from cmux 0.64.22
before the code existed — the token latches a signal sent before anyone waits,
and the latch is one-shot. Both are quoted in the module docstring. The tests
below substitute a fake `_cmux` rather than re-measure them: what they check is
that the module's own decisions hold given those behaviours.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import threading

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_MODULE_PATH = _ROOT / "skills" / "cmux-delegate" / "decision_gate.py"
_ENTRY = _ROOT / "skills" / "cmux-delegate" / "decision-gate.sh"

_WS = "AAAAAAAA-1111-2222-3333-444444444444"


def _load():
    spec = importlib.util.spec_from_file_location("decision_gate", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["decision_gate"] = module
    spec.loader.exec_module(module)
    return module


gate = _load()


@pytest.fixture
def decision_file(tmp_path):
    return str(tmp_path / f"{_WS}.json")


def _record(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# --------------------------------------------------------------------------
# ask — every non-approval path
# --------------------------------------------------------------------------


def test_timeout_is_not_a_rejection(decision_file, monkeypatch):
    """A deadline that passed and a delegator who said no are different facts.

    Collapsing them would let a worker treat "nobody answered" as a decision
    and report the work as declined, which hides an unanswered question.
    """
    monkeypatch.setattr(gate, "_cmux", lambda args, timeout: 1)
    result = gate.ask(decision_file, _WS, "DROP TABLE 를 실행해도 됩니까", 5)
    assert result["verdict"] == "timeout"


def test_absent_cmux_is_unavailable_not_approved(decision_file, monkeypatch):
    """praxis is host-neutral everywhere else; a gate is the exception.

    `_cmux` returning None is cmux missing entirely. Degrading to "proceed"
    here would mean a worker on a host without cmux runs unsupervised — the
    exact state the gate exists to prevent.
    """
    monkeypatch.setattr(gate, "_cmux", lambda args, timeout: None)
    result = gate.ask(decision_file, _WS, "질문", 5)
    assert result["verdict"] == "unavailable"


def test_wake_without_a_recorded_verdict_is_unavailable(decision_file, monkeypatch):
    """A latched token from an earlier decision wakes the waiter with nothing
    decided. Reading that as approval consumes another question's answer."""
    monkeypatch.setattr(gate, "_cmux", lambda args, timeout: 0)
    result = gate.ask(decision_file, _WS, "질문", 5)
    assert result["verdict"] == "unavailable"


def test_unwritable_file_is_unavailable(tmp_path, monkeypatch):
    """No published question means nobody can answer it, so blocking on the
    signal would only burn the timeout."""
    monkeypatch.setattr(gate, "_cmux", lambda args, timeout: 0)
    result = gate.ask(str(tmp_path / "no-such-dir" / "x.json"), _WS, "질문", 5)
    assert result["verdict"] == "unavailable"


def test_verdict_of_a_foreign_token_is_ignored(decision_file, monkeypatch):
    """The file is re-read after the wait, so a record left by a DIFFERENT
    question must not be consumed as this one's answer."""
    def resolve_someone_elses(args, timeout):
        record = _record(decision_file)
        record.update({"token": "praxis-decision-other", "status": "approved"})
        with open(decision_file, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
        return 0

    monkeypatch.setattr(gate, "_cmux", resolve_someone_elses)
    result = gate.ask(decision_file, _WS, "질문", 5)
    assert result["verdict"] == "unavailable"


def test_approval_carries_the_delegators_reason(decision_file, monkeypatch):
    """The positive control for every refusal above: the same code path does
    return `approved` when a verdict against THIS token is on disk."""
    def resolve_this_one(args, timeout):
        record = _record(decision_file)
        record.update({"status": "approved", "reason": "이 테이블은 임시본입니다"})
        with open(decision_file, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False)
        return 0

    monkeypatch.setattr(gate, "_cmux", resolve_this_one)
    result = gate.ask(decision_file, _WS, "질문", 5)
    assert result["verdict"] == "approved"
    assert result["reason"] == "이 테이블은 임시본입니다"


def test_a_verdict_written_without_a_signal_still_reaches_the_worker(
    decision_file, monkeypatch
):
    """The file is the authority, not the signal. A delegator whose signal was
    lost has still decided, and the worker must see it rather than time out."""
    def write_but_do_not_signal(args, timeout):
        record = _record(decision_file)
        record.update({"status": "rejected", "reason": "prod 는 사람이 직접"})
        with open(decision_file, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False)
        return 1  # wait-for reports a timeout

    monkeypatch.setattr(gate, "_cmux", write_but_do_not_signal)
    result = gate.ask(decision_file, _WS, "질문", 5)
    assert result["verdict"] == "rejected"


def test_each_question_mints_its_own_token(decision_file, monkeypatch):
    """A workspace-named token would hand a stale latched signal to the next
    question from the same workspace."""
    monkeypatch.setattr(gate, "_cmux", lambda args, timeout: 1)
    gate.ask(decision_file, _WS, "첫 질문", 5)
    first = _record(decision_file)["token"]
    gate.ask(decision_file, _WS, "둘째 질문", 5)
    second = _record(decision_file)["token"]
    assert first != second


# --------------------------------------------------------------------------
# resolve / status — the delegator's half
# --------------------------------------------------------------------------


def test_resolve_signals_the_token_from_the_file(decision_file, monkeypatch):
    """Never a name rebuilt here — a derived name is the same string for every
    decision from this workspace, which is what makes staleness reachable."""
    monkeypatch.setattr(gate, "_cmux", lambda args, timeout: 1)
    gate.ask(decision_file, _WS, "질문", 1)
    token = _record(decision_file)["token"]

    seen: list[list[str]] = []
    monkeypatch.setattr(gate, "_cmux", lambda args, timeout: seen.append(args) or 0)
    gate.resolve(decision_file, _WS, "approved", "됩니다")
    assert seen == [["wait-for", "--signal", token]]


def test_resolving_twice_does_not_signal_again(decision_file, monkeypatch):
    """The second signal would latch and satisfy this workspace's NEXT
    question with nobody having answered it."""
    monkeypatch.setattr(gate, "_cmux", lambda args, timeout: 1)
    gate.ask(decision_file, _WS, "질문", 1)

    calls: list[list[str]] = []
    monkeypatch.setattr(gate, "_cmux", lambda args, timeout: calls.append(args) or 0)
    gate.resolve(decision_file, _WS, "approved", "됩니다")
    second = gate.resolve(decision_file, _WS, "rejected", "역시 안 됩니다")

    assert len(calls) == 1
    assert second["verdict"] == "approved"


def test_concurrent_resolvers_produce_one_verdict_and_one_signal(
    decision_file, monkeypatch
):
    """Two delegators resolving one pending decision (CodeRabbit, PR #999).

    The read-modify-write had a window where both could pass the
    already-resolved check, both write, and both signal. The second signal is
    the damage — it latches and releases this workspace's NEXT question with
    nobody having answered it.
    """
    monkeypatch.setattr(gate, "_cmux", lambda args, timeout: 1)
    gate.ask(decision_file, _WS, "질문", 1)

    signals: list[list[str]] = []
    barrier = threading.Barrier(2)

    def counting_cmux(args, timeout):
        signals.append(args)
        return 0

    monkeypatch.setattr(gate, "_cmux", counting_cmux)

    results: list[object] = []

    def contend(verdict, reason):
        barrier.wait()
        results.append(gate.resolve(decision_file, _WS, verdict, reason))

    threads = [
        threading.Thread(target=contend, args=("approved", "됩니다")),
        threading.Thread(target=contend, args=("rejected", "안 됩니다")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(signals) == 1
    verdicts = {r["verdict"] for r in results}
    assert len(verdicts) == 1
    assert _record(decision_file)["status"] == verdicts.pop()


def test_a_failed_write_leaves_the_previous_record_readable(
    decision_file, monkeypatch
):
    """The old `open(path, "w")` truncated before writing, so a reader racing
    a writer could parse nothing (#1031). `_read` reports that as "no file"
    and `resolve` then answers as if the question had never been asked — a
    recorded verdict silently becomes an unasked question.

    Failing the write mid-flight is the deterministic stand-in for that race:
    truncate-then-fail and truncate-then-race leave the same half-file, and
    only one of them can be scheduled on demand.
    """
    gate._write(decision_file, {"status": "pending", "question": "질문"})
    before = _record(decision_file)

    def failing_dump(record, handle, **kwargs):
        handle.write('{"status": "app')
        raise OSError("disk full")

    monkeypatch.setattr(gate.json, "dump", failing_dump)
    assert gate._write(decision_file, {"status": "approved"}) is False

    monkeypatch.undo()
    assert _record(decision_file) == before
    parent = pathlib.Path(decision_file).parent
    assert [q.name for q in parent.iterdir()] == [pathlib.Path(decision_file).name]


def test_a_new_question_clears_the_previous_claim(decision_file, monkeypatch):
    """The claim's lifetime is one question. Left behind, it would make every
    later decision from this workspace unresolvable."""
    monkeypatch.setattr(gate, "_cmux", lambda args, timeout: 1)
    gate.ask(decision_file, _WS, "첫 질문", 1)
    gate.resolve(decision_file, _WS, "approved", "됩니다")

    gate.ask(decision_file, _WS, "둘째 질문", 1)
    signals: list[list[str]] = []
    monkeypatch.setattr(gate, "_cmux", lambda args, timeout: signals.append(args) or 0)
    result = gate.resolve(decision_file, _WS, "rejected", "이번엔 안 됩니다")

    # The second question must be resolvable AND signalled — a leftover claim
    # would send this down the loser path, which signals nothing.
    assert result["verdict"] == "rejected"
    assert len(signals) == 1


def test_resolve_on_a_question_nobody_asked(decision_file):
    assert gate.resolve(decision_file, _WS, "approved", "")["state"] == "none"


def test_status_reports_the_question_while_pending(decision_file, monkeypatch):
    monkeypatch.setattr(gate, "_cmux", lambda args, timeout: 1)
    gate.ask(decision_file, _WS, "DROP TABLE 를 실행해도 됩니까", 1)
    result = gate.status(decision_file, _WS)
    assert result["state"] == "pending"
    assert result["question"] == "DROP TABLE 를 실행해도 됩니까"


# --------------------------------------------------------------------------
# output contract
# --------------------------------------------------------------------------


def test_agent_text_cannot_run_in_the_delegators_shell():
    """`question` is written by the delegated agent and the delegator consumes
    it under `eval`. Unconditional single-quoting is what keeps the delegate
    from choosing what the delegator executes."""
    rendered = gate._format({"state": "pending", "question": "$(touch /tmp/pwned)"})
    assert "'$(touch /tmp/pwned)'" in rendered


def test_embedded_quote_survives_the_rendering():
    rendered = gate._format({"reason": "it's fine"})
    assert rendered == """reason='it'\\''s fine'"""


# --------------------------------------------------------------------------
# entry point argv contract
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["frobnicate", _WS],
        ["status", "../../etc/passwd"],
        ["status", _WS, "extra"],
        ["resolve", _WS],
        ["resolve", _WS, "maybe"],
        ["ask", "--timeout"],
    ],
    ids=[
        "no arguments",
        "unknown subcommand",
        "traversal-shaped workspace",
        "status with a stray positional",
        "resolve with no verdict",
        "resolve with a verdict that is neither",
        "ask with a dangling --timeout",
    ],
)
def test_malformed_argv_exits_2(argv, tmp_path):
    completed = subprocess.run(
        ["sh", str(_ENTRY), *argv],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PRAXIS_HOME": str(tmp_path)},
    )
    assert completed.returncode == 2


def test_ask_outside_a_workspace_refuses(tmp_path):
    """The worker never names itself: an explicit --workspace would let one
    worker publish a question under another's name."""
    completed = subprocess.run(
        ["sh", str(_ENTRY), "ask", "질문"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PRAXIS_HOME": str(tmp_path)},
    )
    assert completed.returncode == 2
    assert "no workspace" in completed.stderr


def test_status_of_an_unasked_workspace_exits_0(tmp_path):
    """Well-formed arguments always exit 0 — a caller branches on `state`, and
    a non-zero exit would collapse "nothing asked" into "the probe broke"."""
    completed = subprocess.run(
        ["sh", str(_ENTRY), "status", _WS],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PRAXIS_HOME": str(tmp_path)},
    )
    assert completed.returncode == 0
    assert completed.stdout.startswith("state='none'")
