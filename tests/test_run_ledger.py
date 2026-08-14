"""tests/test_run_ledger.py — Run/Task ledger for delegated work (#982).

The ledger's whole value is that it answers after the fact, so the properties
under test are the ones that decide whether an answer is trustworthy: does the
fold respect event order, does a concurrent write from a sibling delegation
survive intact, and does a host with no cmux still get a complete record.

Every case runs against a temp PRAXIS_STATE_DIR — the developer's real ledger is
never read or written.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

_HELPER = Path(__file__).resolve().parent.parent / "skills" / "cmux-orchestrate" / "run_ledger.py"


def _load(monkeypatch, state_dir: Path):
    """Import the module fresh with PRAXIS_STATE_DIR pointed at a temp root.

    Re-imported per test rather than cached: the path helpers read the
    environment at call time, but a stale module object would still hold any
    state a previous test left behind.
    """
    monkeypatch.setenv("PRAXIS_STATE_DIR", str(state_dir))
    spec = importlib.util.spec_from_file_location("run_ledger_under_test", _HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def ledger(monkeypatch, tmp_path):
    """The module with cmux stubbed out — every test here is about the record,
    and a real cmux call would make results depend on the developer's sidebar."""
    module = _load(monkeypatch, tmp_path / "state")
    monkeypatch.setattr(module, "_cmux", lambda argv: None)
    return module


# ---------------------------------------------------------------------------
# 1. The fold — later events win, in file order
# ---------------------------------------------------------------------------


def test_a_bound_task_starts_pending(ledger):
    run = ledger.create("run")["run"]
    ledger.bind(run, "WS-A", "task a")
    assert ledger.summary(run)["pending"] == 1


def test_the_last_event_for_a_workspace_decides_its_state(ledger):
    run = ledger.create("run")["run"]
    ledger.bind(run, "WS-A", "task a")
    ledger.mark(run, "WS-A", "task.started")
    ledger.mark(run, "WS-A", "task.done")
    assert ledger.summary(run)["done"] == 1
    assert ledger.summary(run)["running"] == 0


def test_a_blocked_task_that_later_finishes_is_done(ledger):
    """Blocked is a current condition, not a scar. A run whose blocker was
    cleared must read as cleared, or the state stops tracking reality."""
    run = ledger.create("run")["run"]
    ledger.bind(run, "WS-A", "task a")
    ledger.mark(run, "WS-A", "task.blocked")
    ledger.mark(run, "WS-A", "task.done")
    assert ledger.summary(run)["state"] == "complete"


def test_one_blocked_task_makes_the_whole_run_blocked(ledger):
    """The run state exists to surface what needs a human. Two of three tasks
    progressing must not paint over the third."""
    run = ledger.create("run")["run"]
    for ws in ("WS-A", "WS-B", "WS-C"):
        ledger.bind(run, ws, ws)
    ledger.mark(run, "WS-A", "task.done")
    ledger.mark(run, "WS-B", "task.started")
    ledger.mark(run, "WS-C", "task.blocked")
    assert ledger.summary(run)["state"] == "blocked"


def test_an_empty_run_says_so_rather_than_reading_complete(ledger):
    """`done == total` is true at 0 == 0. A run that has bound nothing yet must
    not report the same state as a run that finished everything."""
    run = ledger.create("run")["run"]
    assert ledger.summary(run)["state"] == "empty"


def test_a_closed_run_reports_closed_over_its_task_counts(ledger):
    run = ledger.create("run")["run"]
    ledger.bind(run, "WS-A", "task a")
    ledger.close(run)
    assert ledger.summary(run)["state"] == "closed"


def test_mark_and_summary_use_one_vocabulary(ledger):
    """`task.started` produces the state `running`. A caller reading `state=`
    from a transition and from a summary must not get two names for it."""
    run = ledger.create("run")["run"]
    ledger.bind(run, "WS-A", "task a")
    assert ledger.mark(run, "WS-A", "task.started")["state"] == "running"
    assert ledger.summary(run)["running"] == 1


# ---------------------------------------------------------------------------
# 1b. The group header is a workspace, and --from does not change that
# ---------------------------------------------------------------------------


def _group_mutations(calls: list[list[str]]) -> list[list[str]]:
    """Group calls that change something. `_group_ref` reads the group list on
    every bind, and counting that read as an action makes every assertion here
    about call ordering rather than about what was done."""
    return [c for c in calls if c[:1] == ["workspace-group"] and c[1] != "list"]


@pytest.fixture()
def spy(monkeypatch, tmp_path):
    """The module with every cmux argv captured instead of executed."""
    module = _load(monkeypatch, tmp_path / "state")
    calls: list[list[str]] = []
    monkeypatch.setattr(module, "_cmux", lambda argv: calls.append(argv))
    return module, calls


def test_the_group_is_anchor_only(spy):
    """Measured on cmux 0.64.22: `create --from <ws>` still opens a fresh anchor
    workspace and files <ws> under it — member_count 4 for three tasks, exactly
    as without --from. The extra member is the group header, not a stray tab,
    so --from buys nothing and costs the header its run name."""
    ledger, calls = spy
    ledger.create("run")
    made = _group_mutations(calls)
    assert [c[1] for c in made] == ["create"]
    assert "--from" not in made[0]


def test_bind_adds_to_the_existing_group(spy, monkeypatch):
    ledger, calls = spy
    monkeypatch.setattr(ledger, "_group_ref", lambda run_id: "workspace_group:1")
    run = ledger.create("run")["run"]
    calls.clear()
    ledger.bind(run, "WS-B", "task b")
    assert [c[1] for c in _group_mutations(calls)] == ["add"]


# ---------------------------------------------------------------------------
# 2. Concurrency — N delegations write to one run
# ---------------------------------------------------------------------------


def test_concurrent_binds_all_survive_intact(ledger):
    """The reason there is no mutable state document. Sixteen writers append at
    once; every line must be whole and every task must be present."""
    run = ledger.create("run")["run"]
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda i: ledger.bind(run, f"WS-{i:02d}", f"task {i}"), range(16)))

    path = Path(ledger._events_path(run))
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    for line in lines:
        json.loads(line)  # raises if a write tore
    assert ledger.summary(run)["total"] == 16


def test_a_corrupt_line_does_not_take_the_history_with_it(ledger):
    """A partially readable history answers more than a raised exception."""
    run = ledger.create("run")["run"]
    ledger.bind(run, "WS-A", "task a")
    with open(ledger._events_path(run), "a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    ledger.bind(run, "WS-B", "task b")
    assert ledger.summary(run)["total"] == 2


# ---------------------------------------------------------------------------
# 3. Fail-open — the record survives what the view does not
# ---------------------------------------------------------------------------


def test_the_record_is_complete_when_cmux_is_absent(monkeypatch, tmp_path):
    """praxis is host-neutral (ARCHITECTURE.md). Simulated by an exec failure,
    which is what an absent binary actually produces."""
    module = _load(monkeypatch, tmp_path / "state")

    def _no_cmux(*args, **kwargs):
        raise FileNotFoundError("cmux")

    monkeypatch.setattr(subprocess, "run", _no_cmux)
    run = module.create("run")["run"]
    module.bind(run, "WS-A", "task a")
    module.mark(run, "WS-A", "task.done")
    assert module.summary(run)["done"] == 1


def test_an_unwritable_state_dir_does_not_raise(ledger, monkeypatch):
    """A ledger that fails a delegation is worse than no ledger."""
    monkeypatch.setattr(os, "open", lambda *a, **k: (_ for _ in ()).throw(PermissionError()))
    run_id = "1786600000-1"
    assert ledger._append(run_id, {"event": "task.added", "workspace": "WS-A"}) is False


def test_summary_of_an_unknown_run_answers_rather_than_failing(ledger):
    result = ledger.summary("no-such-run")
    assert result["total"] == 0
    assert result["state"] == "empty"


# ---------------------------------------------------------------------------
# 4. Path convention and the CLI contract
# ---------------------------------------------------------------------------


def test_events_live_under_the_praxis_state_dir(ledger, tmp_path):
    run = ledger.create("run")["run"]
    ledger.bind(run, "WS-A", "task a")
    expected = tmp_path / "state" / "runs" / run / "events.jsonl"
    assert expected.is_file()


def test_run_ids_sort_newest_first(ledger, monkeypatch):
    """`list` orders on the id itself, which opens with epoch seconds.

    The clock advances by 100 per call rather than per run: `create` reads it
    twice (the id, then the event's `at`), and a fixture that assumed one read
    ran dry mid-test.
    """
    clock = itertools.count(1786600000, 100)
    monkeypatch.setattr(ledger.time, "time", lambda: next(clock))
    ids = [ledger.create("r")["run"] for _ in range(3)]
    assert len(set(ids)) == 3
    assert ledger.runs() == sorted(ids, reverse=True)


def test_a_label_carrying_a_quote_survives_the_documented_eval(ledger):
    """The output is consumed with `eval "$(...)"`, and labels carry user text."""
    run = ledger.create("it's a run")["run"]
    rendered = ledger._format(ledger.summary(run))
    extracted = subprocess.run(
        ["sh", "-c", f'eval "{rendered}"; printf "%s" "$label"'],
        capture_output=True,
        text=True,
        check=False,
    )
    assert extracted.stdout == "it's a run"


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["bogus"],
        ["create"],
        ["bind", "run-1"],
        ["bind", "run-1", "WS-A", "label", "extra"],
    ],
)
def test_a_malformed_invocation_exits_2(argv):
    """The one failure the wrapper does not swallow: a caller with a bug."""
    proc = subprocess.run(
        [sys.executable, str(_HELPER), *argv],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "usage:" in proc.stderr


def test_a_well_formed_invocation_exits_0(tmp_path):
    env = {**os.environ, "PRAXIS_STATE_DIR": str(tmp_path / "state")}
    proc = subprocess.run(
        [sys.executable, str(_HELPER), "summary", "no-such-run"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 0
    assert "state='empty'" in proc.stdout
