"""scripts/rule-ab-eval.py: job planning, orchestration and scoring (#1415).

The model's answers cannot be a CI gate; what turns them into a verdict can.
`run` is exercised end to end against a stand-in `claude` on PATH whose
records use the field names of a real `claude -p --output-format stream-json`
run (type/message.content[].type/num_turns/total_cost_usd/result), so arm env,
hook registration and per-run ledger isolation are checked without a model.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "rule-ab-eval.py"
SUITE = Path(__file__).resolve().parent / "fixtures" / "rule-ab-eval" / "elapsed-time-signal"

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys, time
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
args = sys.argv[1:]
answer = "hello budget=%s settings=%s" % (os.environ.get("BUDGET", "unset"), "--settings" in args)
with open(os.environ["PRAXIS_FIRE_TELEMETRY_FILE"], "a") as f:
    f.write(json.dumps({"hook": "sig", "role": "advisory-nudge", "decision": "advise"}) + "\n")
for rec in (
    {"type": "system", "subtype": "init", "session_id": "s"},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]}},
    {"type": "result", "subtype": "success", "num_turns": 2, "total_cost_usd": 0.01, "result": answer},
):
    print(json.dumps(rec))
'''


def cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env)


def write_suite(path: Path, oracle: str = "echo hello") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "suite.json").write_text(json.dumps({
        "hooks": {"Stop": []},
        "signal_hook": "sig",
        "arms": {"on": {"env": {"BUDGET": "@now"}}, "off": {"env": {}, "hooks": False}},
        "tasks": {"graded": {"prompt": "p", "oracle": oracle}, "free": {"prompt": "q"}},
    }))
    return path


def fake_path(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text(FAKE_CLAUDE)
    claude.chmod(0o755)
    return dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


# --- plan -------------------------------------------------------------------

def test_plan_covers_every_task_arm_rep_once_in_a_seeded_order():
    first = cli("plan", str(SUITE), "--seed", "7").stdout.splitlines()
    again = cli("plan", str(SUITE), "--seed", "7").stdout.splitlines()
    other = cli("plan", str(SUITE), "--seed", "8").stdout.splitlines()
    assert first == again
    assert first != other
    assert len(first) == len(set(first)) == 4 * 2 * 3
    assert {line.split()[1] for line in first} == {"budget", "none"}


def test_plan_rejects_a_suite_with_one_arm(tmp_path):
    suite = write_suite(tmp_path / "s")
    data = json.loads((suite / "suite.json").read_text())
    del data["arms"]["off"]
    (suite / "suite.json").write_text(json.dumps(data))
    proc = cli("plan", str(suite))
    assert proc.returncode != 0 and "at least two arms" in proc.stderr


@pytest.mark.parametrize("where", ["tasks", "arms"])
def test_plan_rejects_a_name_that_could_collide_in_run_ids(tmp_path, where):
    # ("a-b", "c") and ("a", "b-c") would both become run directory a-b-c-1.
    suite = write_suite(tmp_path / "s")
    data = json.loads((suite / "suite.json").read_text())
    first = next(iter(data[where]))
    data[where]["a-b"] = data[where].pop(first)
    (suite / "suite.json").write_text(json.dumps(data))
    proc = cli("plan", str(suite))
    assert proc.returncode != 0 and "name 'a-b'" in proc.stderr


# --- run (stand-in claude) ----------------------------------------------------

def test_run_then_score_applies_arm_env_hooks_and_isolation(tmp_path):
    env = fake_path(tmp_path)
    results = tmp_path / "results"
    run = cli("run", str(write_suite(tmp_path / "s")), str(results), "--reps", "2", env=env)
    assert run.returncode == 0, run.stderr
    assert (results / "oracle" / "graded.txt").read_text() == "hello\n"
    assert not (results / "oracle" / "free.txt").exists()

    answers = {}
    for meta in results.glob("runs/*/meta.json"):
        m = json.loads(meta.read_text())
        answers[m["id"]] = json.loads(meta.with_name("stream.jsonl").read_text().splitlines()[-1])["result"]
        # every run wrote its own ledger, not the operator's
        assert meta.with_name("fires.jsonl").read_text().count('"sig"') == 1
    assert len(answers) == 2 * 2 * 2
    on, off = answers["graded-on-1"], answers["graded-off-1"]
    assert on.startswith("hello budget=") and on.split("=")[1].split()[0].isdigit()
    assert "settings=True" in on
    assert off == "hello budget=unset settings=False"

    score = cli("score", str(results))
    assert score.returncode == 0, score.stderr
    lines = score.stdout.splitlines()
    graded_on = next(ln for ln in lines if ln.startswith("graded     on"))
    assert graded_on.split()[2:4] == ["2", "0"]  # runs, failed
    assert "2/2" in graded_on
    free_off = next(ln for ln in lines if ln.startswith("free       off"))
    assert free_off.split()[8] == "-"  # no oracle -> ungraded
    assert "ALL on: runs=4 failed=0" in score.stdout and "signal=4" in score.stdout


def test_run_keeps_isolation_paths_over_arm_env(tmp_path):
    suite = write_suite(tmp_path / "s")
    data = json.loads((suite / "suite.json").read_text())
    shared = tmp_path / "operator-ledger.jsonl"
    data["arms"]["on"]["env"]["PRAXIS_FIRE_TELEMETRY_FILE"] = str(shared)
    (suite / "suite.json").write_text(json.dumps(data))
    results = tmp_path / "results"
    proc = cli("run", str(suite), str(results), "--reps", "1", env=fake_path(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert not shared.exists()
    assert (results / "runs" / "graded-on-1" / "fires.jsonl").exists()


def test_run_kills_a_job_past_its_timeout_and_scores_it_failed(tmp_path):
    env = dict(fake_path(tmp_path), FAKE_SLEEP="5")
    results = tmp_path / "results"
    proc = cli("run", str(write_suite(tmp_path / "s")), str(results), "--reps", "1", "--timeout", "1", env=env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.count("(timed out)") == 4
    meta = json.loads((results / "runs" / "graded-on-1" / "meta.json").read_text())
    assert meta["timed_out"] is True and meta["rc"] == -1
    score = cli("score", str(results))
    assert score.returncode == 1 and "ALL on: runs=2 failed=2" in score.stdout


def test_run_refuses_an_oracle_past_its_timeout(tmp_path):
    proc = cli("run", str(write_suite(tmp_path / "s", "sleep 5")), str(tmp_path / "r"),
               "--oracle-timeout", "1", env=fake_path(tmp_path))
    assert proc.returncode != 0 and "ran past 1s" in proc.stderr
    assert not list((tmp_path / "r").glob("runs/*"))


def test_run_refuses_without_claude_on_path(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    env = dict(os.environ, PATH=str(empty))
    proc = subprocess.run(["/bin/sh", "-c", f'"{sys.executable}" "{SCRIPT}" run "{SUITE}" "{tmp_path / "r"}"'],
                          capture_output=True, text=True, env=env)
    assert proc.returncode != 0 and "not on PATH" in proc.stderr


@pytest.mark.parametrize("oracle", ["true", "exit 3"])
def test_run_refuses_an_oracle_that_prints_nothing_or_fails(tmp_path, oracle):
    proc = cli("run", str(write_suite(tmp_path / "s", oracle)), str(tmp_path / "r"), env=fake_path(tmp_path))
    assert proc.returncode != 0 and "oracle for 'graded'" in proc.stderr
    assert not list((tmp_path / "r").glob("runs/*"))


def test_run_refuses_a_non_empty_results_dir(tmp_path):
    results = tmp_path / "r"
    results.mkdir()
    (results / "old").write_text("x")
    proc = cli("run", str(write_suite(tmp_path / "s")), str(results), env=fake_path(tmp_path))
    assert proc.returncode != 0 and "is not empty" in proc.stderr


# --- score ------------------------------------------------------------------

def make_results(tmp_path: Path, runs: list[dict], unrun: tuple[str, ...] = ()) -> Path:
    """Runs are written as rep 1; `unrun` ids are planned in run.json but left no directory."""
    results = tmp_path / "results"
    (results / "oracle").mkdir(parents=True)
    (results / "oracle" / "graded.txt").write_text("alpha\nbeta\n")
    (results / "suite.json").write_text(json.dumps({"signal_hook": "sig", "arms": {"on": {}, "off": {}}}))
    ids = [f"{r['task']}-{r['arm']}-1" for r in runs]
    (results / "run.json").write_text(json.dumps({"planned": [*ids, *unrun]}))
    for r, run_id in zip(runs, ids):
        run = results / "runs" / run_id
        run.mkdir(parents=True)
        (run / "meta.json").write_text(json.dumps(
            {"id": run_id, "task": r["task"], "arm": r["arm"], "rep": 1, "rc": r.get("rc", 0), "wall_s": 10}))
        records = [{"type": "system", "subtype": "init", "session_id": "s"}]
        if "answer" in r:
            records.append({"type": "result", "num_turns": 1, "total_cost_usd": 0.5, "result": r["answer"]})
        (run / "stream.jsonl").write_text("\n".join(json.dumps(x) for x in records) + "\n")
        (run / "fires.jsonl").write_text("\n".join(json.dumps(x) for x in r.get("fires", [])) + "\n"
                                         if r.get("fires") else "")
    return results


def row_for(stdout: str, prefix: str) -> list[str]:
    return next(ln for ln in stdout.splitlines() if ln.startswith(prefix)).split()


def test_score_grades_every_oracle_line_in_both_polarities(tmp_path):
    results = make_results(tmp_path, [
        {"id": "a", "task": "graded", "arm": "on", "answer": "found alpha and beta"},
        {"id": "b", "task": "graded", "arm": "off", "answer": "found alpha only"},
    ])
    proc = cli("score", str(results))
    assert proc.returncode == 0, proc.stderr
    assert row_for(proc.stdout, "graded     on")[8] == "1/1"
    assert row_for(proc.stdout, "graded     off")[8] == "0/1"


def test_score_prints_a_zero_median_as_zero(tmp_path):
    # make_results writes no tool_use records, so the tools median is 0.
    results = make_results(tmp_path, [{"id": "a", "task": "graded", "arm": "on", "answer": "alpha beta"}])
    assert row_for(cli("score", str(results)).stdout, "graded     on")[5] == "0"


def test_score_counts_signal_and_only_blocking_gate_rows(tmp_path):
    fires = [
        {"hook": "sig", "role": "advisory-nudge", "decision": "advise"},
        {"hook": "some-gate", "role": "completion-verify", "decision": "pass"},
        {"hook": "some-gate", "role": "completion-verify", "decision": "block"},
        {"hook": "other", "role": "advisory-nudge", "decision": "advise"},
    ]
    results = make_results(tmp_path, [{"id": "a", "task": "graded", "arm": "on", "answer": "alpha beta", "fires": fires}])
    row = row_for(cli("score", str(results)).stdout, "graded     on")
    assert row[9:11] == ["1", "1"]  # signal, gate


@pytest.mark.parametrize("run", [
    {"id": "a", "task": "graded", "arm": "on", "rc": 1, "answer": "alpha beta"},
    {"id": "a", "task": "graded", "arm": "on"},  # no result record
])
def test_score_reports_failed_runs_and_exits_nonzero(tmp_path, run):
    results = make_results(tmp_path, [run, {"id": "b", "task": "graded", "arm": "off", "answer": "alpha beta"}])
    proc = cli("score", str(results))
    assert proc.returncode == 1
    assert row_for(proc.stdout, "graded     on")[2:4] == ["1", "1"]
    assert "ALL on: runs=1 failed=1" in proc.stdout


def test_score_counts_a_planned_job_without_results_as_failed(tmp_path):
    results = make_results(tmp_path, [{"id": "b", "task": "graded", "arm": "off", "answer": "alpha beta"}],
                           unrun=("graded-on-1",))
    proc = cli("score", str(results))
    assert proc.returncode == 1
    assert row_for(proc.stdout, "graded     on")[2:4] == ["1", "1"]
    assert "ALL on: runs=1 failed=1" in proc.stdout


def test_score_with_no_runs_is_fatal(tmp_path):
    proc = cli("score", str(make_results(tmp_path, [])))
    assert proc.returncode == 1 and "no runs" in proc.stderr
