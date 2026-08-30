#!/usr/bin/env python3
"""Invariant canary: workflow dependencies must stay pinned (issue #1171).

Every ``uses:`` in ``.github/workflows/*.yml`` must reference an action by a
full 40-char commit SHA — tag refs (``@v4``) are mutable, so an upstream
re-tag can silently swap the code CI runs (the tj-actions/changed-files
supply-chain incident is the canonical example). The convention is
``owner/repo@<sha> # <tag>``: the SHA is what runs, the trailing comment
documents the human-readable version for reviewers and dependabot.

Every ``runs-on:`` must name a pinned runner image label (``ubuntu-24.04``),
never a floating ``*-latest`` alias — ``ubuntu-latest`` retargets to a new
image on GitHub's schedule, changing the preinstalled toolchain under CI
without a commit. ci.yml already pins ``ubuntu-24.04`` and documents jobs'
reliance on the image's shipped tool versions (jq, shellcheck); this canary
keeps every workflow on that discipline.

The check is line-based on purpose: the repo's Python is stdlib-only (no
PyYAML), and both keys the canary cares about are single-line scalars in
every GitHub-workflow schema shape that matters. A quoted scalar, flow
sequence (``runs-on: [self-hosted, linux]``), or trailing comment is
handled; a matrix expression (``${{ ... }}``) is skipped as unpinnable here
— the matrix values themselves appear elsewhere in the file and are checked
wherever they are literal.

Run standalone or via ``scripts/run-tests.sh``. Exit 0 + a verified count on
a clean tree; exit 1 listing each offending ``file:line`` on drift.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"

SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# Floating runner aliases GitHub retargets over time. Matches the bare
# aliases (ubuntu-latest) and their sized variants (windows-latest-8-cores).
FLOATING_RUNNER_RE = re.compile(r"^(ubuntu|macos|windows)-latest(-|$)")

USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*(.+?)\s*$")
RUNS_ON_RE = re.compile(r"^\s*runs-on:\s*(.+?)\s*$")


def _strip_comment(value: str) -> str:
    """Drop a trailing ``# ...`` YAML comment (needs preceding whitespace)."""
    for i, ch in enumerate(value):
        if ch == "#" and (i == 0 or value[i - 1] in " \t"):
            return value[:i].rstrip()
    return value.strip()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def check_uses(value: str, where: str) -> str | None:
    """Return a drift message for an unpinned ``uses:`` value, else None."""
    ref = _unquote(_strip_comment(value))
    if ref.startswith("./"):
        # Local action: versioned by the enclosing commit itself — no ref to pin.
        return None
    if "@" not in ref:
        return f"{where}: uses '{ref}' carries no @ref at all — pin a 40-char commit SHA"
    sha = ref.rsplit("@", 1)[1]
    if not SHA_RE.fullmatch(sha):
        return (
            f"{where}: uses '{ref}' is not pinned to a 40-char commit SHA "
            f"(got ref '{sha}'; use owner/repo@<sha> # <tag>)"
        )
    return None


def check_runs_on(value: str, where: str) -> list[str]:
    """Return drift messages for floating ``runs-on:`` labels (possibly [])."""
    raw = _strip_comment(value)
    if "${{" in raw:
        # Matrix/expression label — the literal candidates live elsewhere in
        # the file and are checked where they appear.
        return []
    labels = (
        [item.strip() for item in raw[1:-1].split(",")]
        if raw.startswith("[") and raw.endswith("]")
        else [raw]
    )
    drifts = []
    for label in labels:
        label = _unquote(label)
        if FLOATING_RUNNER_RE.match(label):
            drifts.append(
                f"{where}: runs-on '{label}' floats with GitHub's image rollout — "
                f"pin an explicit image label (e.g. ubuntu-24.04)"
            )
    return drifts


def check(workflows_dir: Path = WORKFLOWS) -> tuple[list[str], int]:
    """Scan every workflow; return (drift messages, count of checked lines)."""
    drifts: list[str] = []
    checked = 0
    for path in sorted(
        list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml"))
    ):
        rel = path.relative_to(workflows_dir.parents[1])
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if line.lstrip().startswith("#"):
                continue
            where = f"{rel}:{lineno}"
            m = USES_RE.match(line)
            if m:
                checked += 1
                drift = check_uses(m.group(1), where)
                if drift:
                    drifts.append(drift)
                continue
            m = RUNS_ON_RE.match(line)
            if m:
                checked += 1
                drifts.extend(check_runs_on(m.group(1), where))
    if checked == 0:
        drifts.append(
            f"no uses:/runs-on: lines found under {workflows_dir} — "
            f"canary is scanning the wrong place"
        )
    return drifts, checked


def main() -> int:
    drifts, checked = check()
    if drifts:
        print("workflow-pin check FAILED:")
        for d in drifts:
            print(f"  - {d}")
        return 1
    print(f"workflow-pin check OK ({checked} uses:/runs-on: lines verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
