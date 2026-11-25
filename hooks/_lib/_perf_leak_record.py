"""Where a perf-leak-review record for one PR head lives (issue #1667).

Shared by the merge-gate `perfleak` check, which reads a record, and
`skills/perf-leak-review/record_review.py`, which writes one; one module so the
two cannot disagree about the path.

A record is keyed by the PR's canonical URL parts and its head sha, so a push
leaves the previous head's record behind and the gate asks for a new review.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from _paths import praxis_state_dir  # type: ignore[import-not-found]

ENABLE_ENV = "PRAXIS_MERGE_PERF_LEAK_REVIEW"

_PR_URL = re.compile(r"^https://(?P<host>[^/\s]+)/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)/pull/(?P<number>\d+)/?$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


def enabled() -> bool:
    return os.environ.get(ENABLE_ENV) == "1"


def parse_pr_url(url: str) -> Optional[tuple[str, str, str, str]]:
    """(host, owner, repo, number) from a PR URL, else None."""
    match = _PR_URL.match(url.strip())
    if not match:
        return None
    return match["host"], match["owner"], match["repo"], match["number"]


def record_dir() -> str:
    return os.path.join(praxis_state_dir(), "perf-leak-review")


def record_path(pr_url: str, head: str, suffix: str = ".json") -> Optional[str]:
    """Path of the record for `pr_url` at `head`, or None when either is malformed."""
    parts = parse_pr_url(pr_url)
    if parts is None or not _SHA.match(head):
        return None
    host, owner, repo, number = parts
    name = f"{host}__{owner}__{repo}__{number}__{head}{suffix}"
    return os.path.join(record_dir(), name)
