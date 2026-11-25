#!/usr/bin/env python3
"""Record a perf-leak-review run against one PR head, for the merge gate (issue #1667).

  record_review.py prepare --pr <PR URL>
      Fetch the PR's head sha and diff (`gh pr diff`), write the diff under the
      praxis state dir, and print its path. Review that diff with the skill.

  record_review.py commit --pr <PR URL> --envelope <file>
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
_CLASSES = ("C1", "C2", "C3", "C4", "C5", "C6")


class RecordError(Exception):
    pass


def _gh(args: list[str]) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RecordError(f"gh {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout


def _live(pr: str) -> tuple[str, str, str]:
    """(canonical url, head sha, diff) of the PR as GitHub has it now."""
    url, head = _gh(["pr", "view", pr, "--json", "url,headRefOid", "-q", '.url + " " + .headRefOid']).split()
    diff = _gh(["pr", "diff", pr])
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


def prepare(pr: str) -> str:
    url, head, diff = _live(pr)
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


def commit(pr: str, envelope_path: str) -> str:
    url, head, diff = _live(pr)
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
    p_commit = sub.add_parser("commit")
    p_commit.add_argument("--pr", required=True)
    p_commit.add_argument("--envelope", required=True)
    args = parser.parse_args(argv)
    if parse_pr_url(args.pr) is None:
        print(f"record_review: --pr must be a PR URL (https://<host>/<owner>/<repo>/pull/<N>): {args.pr}", file=sys.stderr)
        return 1
    try:
        out = prepare(args.pr) if args.step == "prepare" else commit(args.pr, args.envelope)
    except (RecordError, OSError, ValueError) as exc:
        print(f"record_review: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
