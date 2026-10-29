#!/usr/bin/env python3
"""Explain a step change in a CI test-time series from the runs either side of it.

Usage: ci_durations_diff.py <owner/repo> <run_before> <run_after> [<run_before> <run_after> ...]

For each pair: per-leg `collected N items` and pytest summary seconds, then every
`--durations` entry of the after-run that is new or grew by >3 s. Works when CI runs
without `-v` (dots output), where per-file timestamp attribution is impossible.
Needs `--durations=N` in the CI pytest call. Fetches raw job logs (gh run view --log truncates).
"""
import re
import subprocess
import sys
import urllib.request


def sh(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def job_logs(repo: str, run: str) -> dict[str, str]:
    token = sh("gh", "auth", "token").strip()
    jobs = sh("gh", "api", f"repos/{repo}/actions/runs/{run}/jobs?per_page=50",
              "-q", '.jobs[]|select(.name|startswith("test"))|"\\(.id)\\t\\(.name)"')
    logs = {}
    for line in jobs.splitlines():
        jid, name = line.split("\t")
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/jobs/{jid}/logs",
                                     headers={"Authorization": f"Bearer {token}"})
        raw = urllib.request.urlopen(req).read().decode(errors="ignore")
        logs[name] = re.sub(r"\x1b\[[0-9;]*m", "", raw)
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
    repo, runs = sys.argv[1], sys.argv[2:]
    if len(runs) % 2:
        sys.exit("runs must come in before/after pairs")
    for before, after in zip(runs[::2], runs[1::2]):
        print(f"== {before} (before)")
        db = summarize(job_logs(repo, before))
        print(f"== {after} (after)")
        da = summarize(job_logs(repo, after))
        print(f"  top-durations sum {sum(db.values()):.0f}s -> {sum(da.values()):.0f}s")
        for key, sec in sorted(da.items(), key=lambda kv: -kv[1]):
            if key not in db or sec - db[key] > 3:
                print(f"  {sec:6.1f}s (was {db.get(key, '-')}) {key[1]} {key[0]}")


if __name__ == "__main__":
    main()
