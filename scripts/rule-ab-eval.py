#!/usr/bin/env python3
"""A/B-measure whether a hook-delivered rule changes Claude's replies (#1415).

A suite (tests/fixtures/rule-ab-eval/<name>/suite.json) names the hooks to
register, two or more arms, and a fixed task set. Every task runs in every arm
N times through headless `claude -p`, in a shuffled order so host load and
model drift hit the arms alike.

Grading is deterministic, never a model: a task's `oracle` is a shell command
run in the repository copy BEFORE any model run, and every non-empty line it
prints must appear verbatim in the reply. A task without an oracle is reported
as ungraded. A rule whose effect cannot be written as such an oracle is out of
scope for this tool.

  plan  <suite-dir> [--reps N] [--seed S]
      Print the shuffled job order. No model call.
  run   <suite-dir> <results-dir> [--reps N] [--seed S] [--parallel P] [--model M]
        [--timeout S] [--oracle-timeout S]
      Archive HEAD into <results-dir>/repo (uncommitted changes are NOT
      included), run the oracles there, then run every job. A job past its
      timeout is killed and scored as failed.
  score <results-dir>
      Per task x arm: runs, failed runs, medians (wall s, tool calls, turns,
      cost USD), oracle passes, signal-hook fires, blocking Stop-gate fires.
      A planned job that left no meta.json counts as a failed run.

Isolation: each run gets its own PRAXIS_HOME / PRAXIS_STATE_DIR /
PRAXIS_FIRE_TELEMETRY_FILE, so installed praxis hooks still fire (the user's
settings are loaded as usual) but never write the operator's ledgers. HOME is
left alone because `claude` needs its login there.

Arm fields: `env` (value "@now" becomes the job's start epoch) and `hooks`
(false = run without the suite's hook registration).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ALLOWED_TOOLS = ["Read", "Grep", "Glob", "Bash"]
NAME_RE = re.compile(r"[A-Za-z0-9_]+")


def load_suite(suite_dir: Path) -> dict:
    suite = json.loads((suite_dir / "suite.json").read_text())
    for key in ("hooks", "arms", "tasks"):
        if not suite.get(key):
            sys.exit(f"FATAL: {suite_dir}/suite.json has no '{key}'")
    if len(suite["arms"]) < 2:
        sys.exit(f"FATAL: {suite_dir}/suite.json needs at least two arms")
    # Names join into the `task-arm-rep` run directory; a '-' inside one would
    # let two different jobs share a directory.
    for name in [*suite["tasks"], *suite["arms"]]:
        if not NAME_RE.fullmatch(name):
            sys.exit(f"FATAL: {suite_dir}/suite.json name '{name}' must match {NAME_RE.pattern}")
    return suite


def job_order(suite: dict, reps: int, seed: int) -> list[tuple[str, str, int]]:
    jobs = [(t, a, r) for t in suite["tasks"] for a in suite["arms"] for r in range(1, reps + 1)]
    random.Random(seed).shuffle(jobs)
    return jobs


def job_id(job: tuple[str, str, int]) -> str:
    return "-".join(map(str, job))


def archive_head(dest: Path) -> str:
    sha = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                         check=True, capture_output=True, text=True).stdout.strip()
    blob = subprocess.run(["git", "-C", str(REPO_ROOT), "archive", "--format=tar", sha],
                          check=True, capture_output=True).stdout
    dest.mkdir(parents=True)
    subprocess.run(["tar", "-x", "-C", str(dest)], input=blob, check=True)
    return sha


def run_oracles(suite: dict, repo: Path, out: Path, timeout: int) -> None:
    out.mkdir()
    for name, task in suite["tasks"].items():
        if "oracle" not in task:
            continue
        try:
            proc = subprocess.run(["sh", "-c", task["oracle"]], cwd=repo, capture_output=True,
                                  text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            sys.exit(f"FATAL: oracle for '{name}' ran past {timeout}s")
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        # An oracle that fails or prints nothing would grade every reply as a pass.
        if proc.returncode != 0 or not lines:
            sys.exit(f"FATAL: oracle for '{name}' exited {proc.returncode} with "
                     f"{len(lines)} lines; stderr: {proc.stderr.strip()[:300]}")
        (out / f"{name}.txt").write_text("\n".join(lines) + "\n")


def run_job(results: Path, suite: dict, model: str, timeout: int, job: tuple[str, str, int]) -> str:
    task, arm, rep = job
    run_id = job_id(job)
    out = results / "runs" / run_id
    (out / "praxis").mkdir(parents=True)
    env = dict(os.environ)
    start = int(time.time())
    for key, value in suite["arms"][arm].get("env", {}).items():
        env[key] = str(start) if value == "@now" else str(value)
    # Applied after the arm so no arm can point a run at shared state or the
    # operator's ledger.
    env.update(PRAXIS_EVAL_REPO=str(results / "repo"),
               PRAXIS_HOME=str(out / "praxis"),
               PRAXIS_STATE_DIR=str(out / "praxis" / "state"),
               PRAXIS_FIRE_TELEMETRY_FILE=str(out / "fires.jsonl"))
    cmd = ["claude", "-p", suite["tasks"][task]["prompt"], "--model", model,
           "--allowedTools", *ALLOWED_TOOLS, "--output-format", "stream-json", "--verbose"]
    if suite["arms"][arm].get("hooks", True):
        cmd[3:3] = ["--settings", str(results / "settings.json")]
    timed_out = False
    with open(out / "stream.jsonl", "w") as stdout, open(out / "stderr.txt", "w") as stderr:
        try:
            rc = subprocess.run(cmd, cwd=results / "repo", env=env, stdout=stdout, stderr=stderr,
                                timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            rc, timed_out = -1, True
    wall = int(time.time()) - start
    (out / "meta.json").write_text(json.dumps(
        {"id": run_id, "task": task, "arm": arm, "rep": rep, "rc": rc, "timed_out": timed_out,
         "wall_s": wall}))
    return f"{run_id} rc={rc}{' (timed out)' if timed_out else ''} wall={wall}s"


def cmd_plan(args) -> int:
    for job in job_order(load_suite(args.suite), args.reps, args.seed):
        print(*job)
    return 0


def cmd_run(args) -> int:
    if shutil.which("claude") is None:
        sys.exit("FATAL: `claude` is not on PATH")
    suite = load_suite(args.suite)
    results = args.results.resolve()
    if results.exists() and any(results.iterdir()):
        sys.exit(f"FATAL: {results} is not empty")
    results.mkdir(parents=True, exist_ok=True)
    sha = archive_head(results / "repo")
    run_oracles(suite, results / "repo", results / "oracle", args.oracle_timeout)
    (results / "settings.json").write_text(json.dumps({"hooks": suite["hooks"]}, indent=1))
    (results / "suite.json").write_text(json.dumps(suite, indent=1))
    jobs = job_order(suite, args.reps, args.seed)
    (results / "run.json").write_text(json.dumps(
        {"head": sha, "model": args.model, "reps": args.reps, "seed": args.seed,
         "parallel": args.parallel, "planned": [job_id(j) for j in jobs]}))
    print(f"head {sha[:8]}, {len(jobs)} jobs, {args.parallel} at a time", flush=True)
    with concurrent.futures.ThreadPoolExecutor(args.parallel) as pool:
        futures = [pool.submit(run_job, results, suite, args.model, args.timeout, j) for j in jobs]
        for done in concurrent.futures.as_completed(futures):
            print(done.result(), flush=True)
    return 0


def stream_metrics(path: Path) -> dict:
    tools, result = 0, None
    for line in path.read_text().splitlines():
        record = json.loads(line)
        if record.get("type") == "assistant":
            tools += sum(1 for b in record["message"].get("content", []) if b.get("type") == "tool_use")
        elif record.get("type") == "result":
            result = record
    if result is None:
        return {"ok": False}
    return {"ok": True, "tools": tools, "turns": result.get("num_turns"),
            "cost": result.get("total_cost_usd"), "answer": result.get("result") or ""}


def ledger_counts(path: Path, signal_hook: str | None) -> tuple[int, int]:
    signals = gates = 0
    if path.exists():
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if signal_hook and row.get("hook") == signal_hook:
                signals += 1
            elif row.get("role") == "completion-verify" and row.get("decision") not in (None, "pass"):
                gates += 1
    return signals, gates


def grade(answer: str, oracle: Path) -> bool | None:
    if not oracle.exists():
        return None
    return all(line in answer for line in oracle.read_text().splitlines() if line.strip())


def cmd_score(args) -> int:
    results = args.results.resolve()
    suite = json.loads((results / "suite.json").read_text())
    planned = json.loads((results / "run.json").read_text())["planned"]
    rows = []
    for meta_path in sorted(results.glob("runs/*/meta.json")):
        meta = json.loads(meta_path.read_text())
        run = meta_path.parent
        metrics = stream_metrics(run / "stream.jsonl")
        signals, gates = ledger_counts(run / "fires.jsonl", suite.get("signal_hook"))
        row = {**meta, "ok": metrics["ok"] and meta["rc"] == 0, "signals": signals, "gates": gates}
        if row["ok"]:
            row.update(tools=metrics["tools"], turns=metrics["turns"], cost=metrics["cost"],
                       graded=grade(metrics["answer"], results / "oracle" / f"{meta['task']}.txt"))
        rows.append(row)
    # A job that died before writing meta.json would otherwise vanish from the score.
    found = {r["id"] for r in rows}
    for run_id in planned:
        if run_id not in found:
            task, arm, rep = run_id.split("-")
            rows.append({"id": run_id, "task": task, "arm": arm, "rep": int(rep), "rc": None,
                         "missing": True, "wall_s": 0, "ok": False, "signals": 0, "gates": 0})
    if not rows:
        print(f"FATAL: no runs under {results}/runs", file=sys.stderr)
        return 1
    (results / "rows.json").write_text(json.dumps(rows, indent=1))

    def med(rs, key):
        values = [r[key] for r in rs if r.get(key) is not None]
        return statistics.median(values) if values else None

    def show(value):
        # 0 is a real median (a run with no tool calls); only a missing one is '-'.
        return "-" if value is None else value

    print("task       arm      runs failed wall_s tools turns cost_usd oracle signal gate")
    groups: dict[tuple[str, str], list] = {}
    for r in rows:
        groups.setdefault((r["task"], r["arm"]), []).append(r)
    for (task, arm), rs in sorted(groups.items()):
        ok = [r for r in rs if r["ok"]]
        graded = [r["graded"] for r in ok if r["graded"] is not None]
        oracle = f"{sum(graded)}/{len(graded)}" if graded else "-"
        cost = med(ok, "cost")
        print(f"{task:10} {arm:8} {len(rs):4} {len(rs) - len(ok):6} {show(med(ok, 'wall_s')):>6} "
              f"{show(med(ok, 'tools')):>5} {show(med(ok, 'turns')):>5} "
              f"{f'{cost:.3f}' if cost is not None else '-':>8} {oracle:>6} "
              f"{sum(r['signals'] for r in rs):6} {sum(r['gates'] for r in rs):4}")
    for arm in suite["arms"]:
        rs = [r for r in rows if r["arm"] == arm]
        ok = [r for r in rs if r["ok"]]
        print(f"ALL {arm}: runs={len(rs)} failed={len(rs) - len(ok)} "
              f"wall_median={show(med(ok, 'wall_s'))} tools_median={show(med(ok, 'tools'))} "
              f"wall_sum={sum(r['wall_s'] for r in ok)} tools_sum={sum(r['tools'] for r in ok)} "
              f"signal={sum(r['signals'] for r in rs)} gate={sum(r['gates'] for r in rs)}")
    return 1 if any(not r["ok"] for r in rows) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run"):
        p = sub.add_parser(name)
        p.add_argument("suite", type=Path)
        if name == "run":
            p.add_argument("results", type=Path)
            p.add_argument("--parallel", type=int, default=3)
            p.add_argument("--model", default="sonnet")
            p.add_argument("--timeout", type=int, default=900, help="seconds per claude run")
            p.add_argument("--oracle-timeout", type=int, default=300, help="seconds per oracle")
        p.add_argument("--reps", type=int, default=3)
        p.add_argument("--seed", type=int, default=1415)
    sub.add_parser("score").add_argument("results", type=Path)
    args = parser.parse_args()
    return {"plan": cmd_plan, "run": cmd_run, "score": cmd_score}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
