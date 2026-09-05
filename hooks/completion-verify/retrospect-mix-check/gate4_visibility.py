#!/usr/bin/env python3
"""Resolve the facts Gate-4b needs, so the Stop hook never has to trust the
distribution card for them (issue #1244).

The card is written by the agent the gate constrains, so `gate_4_verdict:
PASS` and `repo_visibility: private` are both self-declarations. This helper
answers the two questions the hook cannot otherwise settle:

  own_orgs  — which owner handles count as own-org
  vis       — what GitHub actually reports for a backing repo

Resolution order mirrors `skills/retrospect/audit-distribution-gates.py`
(`resolve_own_orgs` / `resolve_repo_visibility`, the SoT for Gate-4 semantics):
`PRAXIS_OWN_ORGS` → `gh api user`; live API → `PRAXIS_REPO_VISIBILITY`. The API
outranks the env var deliberately — the env var is another value the same
author controls, and letting it win would rebuild the hole one layer down
(#1150).

Every failure cause collapses to the single value `UNRESOLVED`: gh missing,
unauthenticated, 404, rate-limited, offline, timed out, over the lookup cap,
or below the budget floor. The hook demotes on it rather than blocking — see
the Gate-4b block in `impl.sh` for why an unresolved lookup may not block a
Stop.

Usage:
    printf 'owner/repo\\n' | gate4_visibility.py --deadline-epoch <unix-float>
                                                 [--max-lookups N]

Output (tab-separated, one record per line):
    own_orgs\\t<comma-joined handles>      or  own_orgs\\tUNRESOLVED
    vis\\t<owner/repo>\\t<public|private|internal|NOT_OWN_ORG|UNRESOLVED>

Only an own-org repo is looked up. The exemption needs BOTH halves — own-org
AND private/internal — so no visibility answer can change the verdict for a
repo whose owner already fails the first half. Those resolve to NOT_OWN_ORG
without a round trip, and they do not consume the lookup cap: a batch of
third-party rows must not spend the budget that a genuine own-org row needs.
This mirrors `audit-distribution-gates.py` (`actual = resolve_repo_visibility(
repo) if own else None`), the SoT for Gate-4 semantics.

NOT_OWN_ORG is a settled fact, not a missing answer — a caller must escalate on
it, never demote. When the allowlist itself is UNRESOLVED ownness is unknown
rather than refuted, so every repo comes back UNRESOLVED and none is looked up:
the own-org half can no longer be established either way.

Exit status is always 0: an unusable answer is expressed as UNRESOLVED, never
as a crash the caller would have to interpret.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import (  # type: ignore[import-not-found]  # noqa: E402
    MIN_SUBPROC_BUDGET_SEC,
)

VALID_VISIBILITY = {"public", "private", "internal"}
UNRESOLVED = "UNRESOLVED"
NOT_OWN_ORG = "NOT_OWN_ORG"
# Past ~15 repos the 10s manifest budget breaks even at the measured 0.45s
# median round trip. A retrospect routing more repos than this gets the
# remainder demoted, not a hook that overruns the Stop group's shared budget.
DEFAULT_MAX_LOOKUPS = 8


def _remaining(deadline: float) -> float:
    return deadline - time.time()


def _gh(args: list[str], deadline: float) -> str | None:
    """One `gh` call bounded by the shared deadline, or None.

    Below MIN_SUBPROC_BUDGET_SEC nothing is spawned: the fork/exec is dead on
    arrival and only burns what little budget remains (`_hook_runtime`,
    issue #1167). 12 more hooks queue behind this one in the Stop group.
    """
    budget = _remaining(deadline)
    if budget < MIN_SUBPROC_BUDGET_SEC:
        return None
    try:
        out = subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=budget,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    value = out.stdout.strip()
    return value or None


def resolve_own_orgs(deadline: float) -> str:
    env = os.environ.get("PRAXIS_OWN_ORGS", "").strip()
    if env:
        handles = [h.strip().lower() for h in env.split(",") if h.strip()]
        if handles:
            return ",".join(handles)
    login = _gh(["api", "user", "--jq", ".login"], deadline)
    return login.lower() if login else UNRESOLVED


def _env_visibility(repo_key: str) -> str | None:
    for entry in os.environ.get("PRAXIS_REPO_VISIBILITY", "").split(","):
        name, _, value = entry.strip().partition("=")
        if name.strip().lower() == repo_key and value.strip() in VALID_VISIBILITY:
            return value.strip()
    return None


def resolve_repo_visibility(repo: str, deadline: float) -> str:
    value = _gh(["api", f"repos/{repo}", "--jq", ".visibility"], deadline)
    if value in VALID_VISIBILITY:
        return value
    return _env_visibility(repo.lower()) or UNRESOLVED


def main(argv: list[str]) -> int:
    deadline = 0.0
    max_lookups = DEFAULT_MAX_LOOKUPS
    i = 0
    while i < len(argv):
        if argv[i] == "--deadline-epoch" and i + 1 < len(argv):
            try:
                deadline = float(argv[i + 1])
            except ValueError:
                deadline = 0.0
            i += 2
        elif argv[i] == "--max-lookups" and i + 1 < len(argv):
            try:
                max_lookups = max(0, int(argv[i + 1]))
            except ValueError:
                pass
            i += 2
        else:
            i += 1

    # Dedupe while preserving order: one call per repo per run, so a report
    # routing the same repo from five findings still costs one round trip.
    seen: dict[str, str] = {}
    for line in sys.stdin.read().splitlines():
        repo = line.strip()
        if not repo:
            continue
        seen.setdefault(repo.lower(), repo)

    own_orgs = resolve_own_orgs(deadline) if seen else UNRESOLVED
    own_known = own_orgs != UNRESOLVED
    allowlist = set(own_orgs.split(",")) if own_known else set()

    out = [f"own_orgs\t{own_orgs}"]
    # Counts round trips actually spent, so a third-party repo cannot push a
    # genuine own-org one past the cap.
    lookups = 0
    for key, repo in seen.items():
        if not own_known:
            out.append(f"vis\t{key}\t{UNRESOLVED}")
            continue
        owner, slash, _ = key.partition("/")
        # No slash means no owner to match, so the own-org half cannot hold —
        # the caller only ever emits `owner/repo`, but a bare handle must not
        # collide with an allowlist entry of the same name and buy a lookup.
        if not slash or owner not in allowlist:
            out.append(f"vis\t{key}\t{NOT_OWN_ORG}")
            continue
        if lookups >= max_lookups:
            out.append(f"vis\t{key}\t{UNRESOLVED}")
            continue
        lookups += 1
        out.append(f"vis\t{key}\t{resolve_repo_visibility(repo, deadline)}")
    sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
