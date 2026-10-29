#!/usr/bin/env python3
"""Explain a step change in a CI test-time series from the runs either side of it.

Usage: ci_durations_diff.py [--step "Run tests"] <owner/repo> <run_before> <run_after> [<before> <after> ...]

For each pair: per-leg `collected N items` and pytest summary seconds, then every
`--durations` entry of the after-run that grew by >3 s or is absent from the before-run's
top-N list (absent is not new: it may have ranked just below the cutoff; confirm new ids
from the test files or full per-test data). Works when CI runs without `-v` (dots
output), where per-file timestamp attribution is impossible. Each log is windowed to the
named step's started_at..completed_at so an earlier pytest step cannot be parsed instead.
Needs `--durations=N` in the CI pytest call. Fetches raw job logs (gh run view --log truncates).
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.request


def sh(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def window(raw: str, start: str, end: str) -> str:
    """Keep log lines whose ISO timestamp (to the second) lies within the step's bounds."""
    lo, hi = start[:19], end[:19]
    return "\n".join(ln for ln in raw.splitlines() if lo <= ln[:19] <= hi)


def job_logs(repo: str, run: str, step: str) -> dict[str, str]:
    token = sh("gh", "auth", "token").strip()
    jobs = json.loads(sh("gh", "api", f"repos/{repo}/actions/runs/{run}/jobs?per_page=50"))["jobs"]
    logs = {}
    for job in jobs:
        if not job["name"].startswith("test"):
            continue
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/jobs/{job['id']}/logs",
                                     headers={"Authorization": f"Bearer {token}"})
        raw = re.sub(r"\x1b\[[0-9;]*m", "", urllib.request.urlopen(req).read().decode(errors="ignore"))
        bounds = [(s["started_at"], s["completed_at"]) for s in job.get("steps", [])
                  if s["name"] == step and s.get("started_at") and s.get("completed_at")]
        if bounds:
            raw = window(raw, *bounds[0])
        else:
            print(f"  warning: {job['name']}: no completed step {step!r}; parsing the whole log", file=sys.stderr)
        logs[job["name"]] = raw
    return logs


def summarize(logs: dict[str, str]) -> dict[tuple[str, str], float]:
    durations = {}
    for name, txt in sorted(logs.items()):
        collected = re.search(r"collected \d+ items[^\n]*", txt)
        summary = re.findall(r"=+ \d+ passed[^\n]* in [0-9.]+s[^\n]*", txt)
        print(f"  {name}: {collected.group(0) if collected else 'no collected line'} | "
              f"{summary[-1] if summary else 'no summary line'}")
        start = txt.find("slowest ")
        if start < 0:
            continue
        for line in txt[start:].splitlines()[1:200]:
            m = re.search(r"Z ([0-9.]+)s (call|setup|teardown)\s+(\S+)", line)
            if m:
                durations[(m.group(3), m.group(2))] = float(m.group(1))
            elif durations and "=" in line and "passed" in line:
                break
    return durations


def main() -> None:
    ap = argparse.ArgumentParser(description="Diff CI --durations output across before/after run pairs.")
    ap.add_argument("--step", default="Run tests", help="name of the main pytest step (default: %(default)s)")
    ap.add_argument("repo")
    ap.add_argument("runs", nargs="+")
    args = ap.parse_args()
    runs = args.runs
    if len(runs) % 2:
        sys.exit("runs must come in before/after pairs")
    for before, after in zip(runs[::2], runs[1::2]):
        print(f"== {before} (before)")
        db = summarize(job_logs(args.repo, before, args.step))
        print(f"== {after} (after)")
        da = summarize(job_logs(args.repo, after, args.step))
        print(f"  top-durations sum {sum(db.values()):.0f}s -> {sum(da.values()):.0f}s")
        for key, sec in sorted(da.items(), key=lambda kv: -kv[1]):
            if key not in db:
                print(f"  {sec:6.1f}s (not in before top-N) {key[1]} {key[0]}")
            elif sec - db[key] > 3:
                print(f"  {sec:6.1f}s (was {db[key]}) {key[1]} {key[0]}")


if __name__ == "__main__":
    main()
