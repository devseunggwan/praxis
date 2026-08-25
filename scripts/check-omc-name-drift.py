#!/usr/bin/env python3
"""Drift guard: praxis must not reference an oh-my-claudecode name that is gone.

omc retires workflow names at major boundaries **without leaving aliases** —
v5.0.0 removed 17 of them outright, so every praxis reference to one had been
silently broken for a day before anyone looked. Prose ages out of attention;
this check makes the breakage fail CI instead.

Denylist, not allowlist. The snapshot below is refreshed by hand when omc ships
a major, so it is stale most of the time. A stale allowlist ("every name must
be in the snapshot") would fail CI every time omc *adds* a skill — a false
failure on a healthy tree. A stale denylist only misses names retired *after*
the last refresh, which is a false pass on an already-broken tree: the same
state we are in today, and one this check strictly improves on.

Invocation forms only. Matching requires the ``oh-my-claudecode:`` prefix,
which is what scopes this to real references — a ``Skill()`` call, a slash
command, a routing-table row. A comment recounting history ("a praxis ultrawork
session hallucinated a merge") writes the bare word and is left alone, because
that sentence is still true. Verified at authoring time: no comment and no
CHANGELOG entry in this repo carries the prefixed form.

Run standalone or via ``scripts/run-tests.sh``. Exit 0 + a scanned-file count
on a clean tree; exit 1 listing every ``file:line  name`` hit on drift.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Names omc 5.0.0 removed (docs/MIGRATION.md "Removed skills and commands").
# Cross-checked against the plugin's own dist/workflow/registry.js: all 17 are
# absent from WORKFLOW_ENTRIES, while plan/execute/review/verify/research/team
# are present. Note WORKFLOW_ENTRIES is a *live-name* list — it carries no
# "removed" decision — so it can confirm this snapshot but cannot generate it.
#
# Refresh procedure when omc ships a major: read its MIGRATION.md removal
# table, append the new names here, and re-run this check.
RETIRED_NAMES: dict[str, str] = {
    "ultrawork": "execute (or team for coordinated workers)",
    "ultraqa": "verify",
    "ultrapilot": "team",
    "swarm": "team",
    "pipeline": "execute",
    "merge-readiness": "review",
    "deep-dive": "research",
    "sciomc": "research",
    "ccg": "ask + team (run ask codex and ask antigravity, then synthesize)",
    "omc-teams": "team",
    "setup": "omc-setup",
    "mcp-setup": "omc-setup",
    "omc-reference": "wiki",
    "learner": "remember",
    "writer-memory": "remember",
    "local-build-reminder": "(removed; docs and CI cover the rebuild signal)",
    "understanding-gate": "review",
}

# ``(?![\w-])`` rather than ``\b``: a word boundary sits between "setup" and a
# following hyphen, so ``\b`` would report a hypothetical
# ``oh-my-claudecode:setup-wizard`` as a reference to the retired ``setup``.
# The trailing guard must reject a hyphen the way it rejects a letter.
_HIT_RE = re.compile(
    r"oh-my-claudecode:(" + "|".join(map(re.escape, sorted(RETIRED_NAMES))) + r")(?![\w-])"
)

# Two files state retired names by definition — this one as its denylist, its
# test as the input-surface fixtures that pin what counts as a reference.
# Scanning either reports hits on a clean tree, so both are exempt. Keep the set
# to files whose whole purpose is to spell the names out; anything else that
# needs to mention one should say it without the ``oh-my-claudecode:`` prefix.
_EXEMPT = frozenset(
    {
        Path(__file__).relative_to(REPO),
        Path("tests/test_check_omc_name_drift.py"),
    }
)


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [Path(p) for p in out.split("\0") if p]


def check() -> list[str]:
    hits: list[str] = []
    for rel in tracked_files():
        if rel in _EXEMPT:
            continue
        try:
            text = (REPO / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable — nothing to reference a name from
        for lineno, line in enumerate(text.splitlines(), 1):
            for name in _HIT_RE.findall(line):
                hits.append(f"{rel}:{lineno}  oh-my-claudecode:{name} -> use {RETIRED_NAMES[name]}")
    return hits


def main() -> int:
    hits = check()
    if hits:
        print("omc-name-drift check FAILED — these names no longer resolve:")
        for h in hits:
            print(f"  - {h}")
        return 1
    print(f"omc-name-drift check OK ({len(RETIRED_NAMES)} retired names, none referenced)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
