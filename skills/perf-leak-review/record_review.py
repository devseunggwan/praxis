#!/usr/bin/env python3
"""Record a perf-leak-review run against one PR head, for the merge gate (issue #1667).

  record_review.py prepare --pr <PR URL> [--repo <local clone>]
      Fetch the PR's head sha and diff (`gh pr diff`), write the diff under the
      praxis state dir, and print its path. Review that diff with the skill.
      GitHub refuses diffs past 300 files; then --repo rebuilds the diff from a
      local clone (fetches the PR head and base into FETCH_HEAD only).

  record_review.py commit --pr <PR URL> --envelope <file> [--repo <local clone>]
      Re-read the head and the diff and refuse if either moved since
      `prepare`; validate the envelope with validate_findings.py; write the
      record the merge-gate `perfleak` check looks up; print one
      verification-anchor row.

The record says the review ran on this head. Its findings are candidates and
the gate never reads them as a verdict.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SKILL_DIR.parent.parent / "hooks" / "_lib"))
from _perf_leak_record import parse_pr_url, record_dir, record_path  # type: ignore[import-not-found]  # noqa: E402

_VALIDATOR = _SKILL_DIR / "validate_findings.py"
_FENCE = re.compile(r"^\s*```(?:json)?\s*\n(?P<body>.*?)\n\s*```\s*$", re.S)
_CLASSES = ("C1", "C2", "C3", "C4", "C5")


class RecordError(Exception):
    pass


def _gh(args: list[str]) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RecordError(f"gh {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout


def _git(repo: str, args: list[str]) -> str:
    result = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RecordError(f"git {' '.join(args[:2])} failed in {repo}: {result.stderr.strip()}")
    return result.stdout


def _fetched(repo: str, remote: str, ref: str) -> str:
    """Sha of `ref` fetched from `remote`; only FETCH_HEAD is written, no local ref moves."""
    _git(repo, ["fetch", "--quiet", "--no-tags", remote, ref])
    return _git(repo, ["rev-parse", "FETCH_HEAD"]).strip()


def _local_diff(url: str, head: str, base: str, repo: str) -> str:
    """The PR's diff rebuilt in a local clone: merge-base of base and head, to head (GitHub's three-dot view)."""
    host, owner, name, number = parse_pr_url(url) or ("", "", "", "")
    remote = f"https://{host}/{owner}/{name}.git"
    fetched_head = _fetched(repo, remote, f"refs/pull/{number}/head")
    if fetched_head != head:
        raise RecordError(f"{url} head is {head[:7]} but the fetch returned {fetched_head[:7]}; run again")
    base_sha = _fetched(repo, remote, f"refs/heads/{base}")
    merge_base = _git(repo, ["merge-base", base_sha, head]).strip()
    return _git(repo, ["diff", "--no-color", "--no-ext-diff", merge_base, head])


def _live(pr: str, repo: str | None = None) -> tuple[str, str, str]:
    """(canonical url, head sha, diff) of the PR as GitHub has it now.

    GitHub refuses `gh pr diff` past 300 files (HTTP 406, `too_large`); with `--repo` the diff is
    rebuilt from a local clone instead, so a large PR can still be reviewed and recorded.
    """
    url, head, base = _gh(["pr", "view", pr, "--json", "url,headRefOid,baseRefName",
                           "-q", '.url + " " + .headRefOid + " " + .baseRefName']).split()
    try:
        diff = _gh(["pr", "diff", pr])
    except RecordError as exc:
        if "too_large" not in str(exc):
            raise
        if repo is None:
            raise RecordError(f"{exc}\nGitHub will not serve this diff; pass --repo <local clone of the "
                              f"repository> to both prepare and commit to build it locally") from exc
        diff = _local_diff(url, head, base, repo)
    if not diff.strip():
        raise RecordError(f"{url} has an empty diff; there is nothing to review")
    return url, head, diff


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _paths(url: str, head: str) -> tuple[str, str, str]:
    record = record_path(url, head)
    if record is None:
        raise RecordError(f"not a PR URL with a 40-char head: {url} {head}")
    return record, record_path(url, head, ".diff") or "", record_path(url, head, ".pending.json") or ""


def _publish(path: str, text: str) -> None:
    """Write `text` to `path` through a same-directory temp file, so a failed write leaves no record."""
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        Path(tmp).write_text(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def prepare(pr: str, repo: str | None = None) -> str:
    url, head, diff = _live(pr, repo)
    _, diff_path, pending_path = _paths(url, head)
    os.makedirs(record_dir(), exist_ok=True)
    _publish(diff_path, diff)
    _publish(pending_path, json.dumps({"url": url, "head": head, "diff_sha256": _sha256(diff)}))
    return f"head {head}\ndiff {diff_path}\n"


def _envelope(path: str) -> dict:
    raw = Path(path).read_text()
    fenced = _FENCE.match(raw)
    text = fenced["body"] if fenced else raw
    result = subprocess.run([sys.executable, str(_VALIDATOR)], input=text, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RecordError("envelope rejected by validate_findings.py:\n" + (result.stdout + result.stderr).strip())
    return json.loads(text)


def _row(head: str, envelope: dict) -> str:
    findings = envelope.get("findings", [])
    counts = " ".join(f"{c}:{sum(1 for f in findings if f.get('class') == c)}" for c in _CLASSES)
    where = "listed under Open items" if findings else "none"
    return (f"| P | perf-leak-review on `{head[:7]}`: {len(findings)} candidate(s) ({counts}); "
            f"candidates {where}, read-only reviewer, not verified by execution | PASS(mock) |")


def commit(pr: str, envelope_path: str, repo: str | None = None) -> str:
    url, head, diff = _live(pr, repo)
    record, _, pending_path = _paths(url, head)
    if not os.path.isfile(pending_path):
        raise RecordError(f"no `prepare` for {url} at head {head[:7]}; the head may have moved, run prepare again")
    pending = json.loads(Path(pending_path).read_text())
    if pending.get("diff_sha256") != _sha256(diff):
        raise RecordError(f"the diff of {url} changed since prepare; run prepare again")
    envelope = _envelope(envelope_path)
    _publish(record, json.dumps({
        "url": url, "head": head, "diff_sha256": pending["diff_sha256"],
        "recorded_at": int(time.time()), "envelope": envelope,
    }, indent=2))
    return f"record {record}\n{_row(head, envelope)}\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="step", required=True)
    p_prepare = sub.add_parser("prepare")
    p_prepare.add_argument("--pr", required=True)
    p_prepare.add_argument("--repo", help="local clone; used only when GitHub refuses the diff as too large")
    p_commit = sub.add_parser("commit")
    p_commit.add_argument("--pr", required=True)
    p_commit.add_argument("--envelope", required=True)
    p_commit.add_argument("--repo", help="the same local clone passed to prepare")
    args = parser.parse_args(argv)
    if parse_pr_url(args.pr) is None:
        print(f"record_review: --pr must be a PR URL (https://<host>/<owner>/<repo>/pull/<N>): {args.pr}", file=sys.stderr)
        return 1
    try:
        out = (prepare(args.pr, args.repo) if args.step == "prepare"
               else commit(args.pr, args.envelope, args.repo))
    except (RecordError, OSError, ValueError) as exc:
        print(f"record_review: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
