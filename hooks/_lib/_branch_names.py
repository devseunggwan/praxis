"""Work-branch naming convention shared by the hooks that read branch names.

`branch-name-check` gates a new branch name against it; `side-effect-scan`
uses it to tell a push of the session's own issue branch from a push to any
other ref. Both read the same environment, so a repo that configures its own
convention configures both.

Config:
  PRAXIS_BRANCH_NAME_REGEX     — work-branch pattern (default below)
  PRAXIS_BRANCH_NAME_WHITELIST — comma-separated base branch names
                                 (default: main,master,dev,prod,staging)
"""
from __future__ import annotations

import os
import re

DEFAULT_REGEX = (
    r"^(hub|issue)-[0-9]+-(feat|fix|docs|style|refactor|chore|test|perf|ci|build|hotfix)-[a-z0-9-]+$"
)
DEFAULT_WHITELIST = frozenset({"main", "master", "dev", "prod", "staging"})


def work_branch_regex() -> re.Pattern[str]:
    """The configured work-branch pattern; the default when unset or invalid."""
    raw = os.environ.get("PRAXIS_BRANCH_NAME_REGEX", "").strip()
    pattern = raw if raw else DEFAULT_REGEX
    try:
        return re.compile(pattern)
    except re.error:
        return re.compile(DEFAULT_REGEX)


def base_branch_names() -> frozenset[str]:
    """The configured base (always-allowed at creation) branch names."""
    raw = os.environ.get("PRAXIS_BRANCH_NAME_WHITELIST", "").strip()
    if raw:
        return frozenset(name.strip() for name in raw.split(",") if name.strip())
    return DEFAULT_WHITELIST
