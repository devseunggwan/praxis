#!/usr/bin/env python3
"""Invariant canary: workflow dependencies must stay pinned (issue #1171).

Every ``uses:`` in ``.github/workflows/*.yml`` must reference an action by an
immutable ref — a full 40-char commit SHA for repository actions, a
``@sha256:<64-hex digest>`` for ``docker://`` container actions. Tag refs
(``@v4``, ``:3.18``) are mutable, so an upstream re-tag can silently swap the
code CI runs (the tj-actions/changed-files supply-chain incident is the
canonical example). The convention is ``owner/repo@<sha> # <tag>``: the SHA is
what runs, the trailing comment documents the human-readable version for
reviewers and dependabot.

Every ``runs-on:`` must name a pinned runner image label (``ubuntu-24.04``),
never a floating ``*-latest`` alias — ``ubuntu-latest`` retargets to a new
image on GitHub's schedule, changing the preinstalled toolchain under CI
without a commit. GitHub runner labels are case-insensitive, so the match is
too, and literal values in ``matrix:`` blocks are checked as well —
``runs-on: ${{ matrix.os }}`` over ``os: [ubuntu-latest]`` is the same float
one indirection away.

Inline tool installs are held to the same discipline (PR #1194): an
``npm install`` of markdownlint-cli2 must carry ``@<exact version>`` and a
``pip install`` of ruff must carry ``==<exact version>``, wherever either
appears in a workflow. The pin's *presence* is the invariant; the version
itself is bumped freely.

The check is line-based on purpose: the repo's Python is stdlib-only (no
PyYAML), and every key the canary cares about is a single-line scalar in the
GitHub-workflow schema shapes that matter. Quoted scalars, flow sequences
(``os: [a, b]``), block list items (``- a``), and trailing comments are
handled; lines inside a block scalar (a ``run: |`` body) are free text, never
workflow keys, so they are exempt from the ``uses:``/``runs-on:`` checks —
only the tool-pin discipline still applies there, because that is exactly
where installs live.

Run standalone or via ``scripts/run-tests.sh``. Exit 0 + a verified count on
a clean tree; exit 1 listing each offending ``file:line`` on drift. Unit
tests: ``tests/test_check_workflow_pins.py``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DOCKER_DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")

# Floating runner aliases GitHub retargets over time. Matches the bare aliases
# (ubuntu-latest) and their sized variants (windows-latest-8-cores). Runner
# labels are case-insensitive on GitHub, so `Ubuntu-Latest` floats identically.
FLOATING_RUNNER_RE = re.compile(r"^(ubuntu|macos|windows)-latest(-|$)", re.IGNORECASE)

USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*(.+?)\s*$")
RUNS_ON_RE = re.compile(r"^\s*runs-on:\s*(.+?)\s*$")

# `key: |` / `key: >-` / `- run: |2+` … — the line that opens a block scalar.
BLOCK_SCALAR_RE = re.compile(
    r"^\s*(?:-\s+)?[A-Za-z_][\w.-]*:\s*[|>][0-9+-]*\s*(?:#.*)?$"
)
MATRIX_RE = re.compile(r"^\s*matrix:\s*(?:#.*)?$")

# Inline tool installs that must carry an exact pin wherever they appear.
NPM_INSTALL_RE = re.compile(r"\bnpm\s+(?:install|i|add)\b")
NPM_PINNED_RE = re.compile(r"markdownlint-cli2@\d[\w.-]*")
PIP_INSTALL_RE = re.compile(r"\bpip\s+install\b")
PIP_RUFF_RE = re.compile(r"\bruff\b")
PIP_PINNED_RE = re.compile(r"\bruff==\d[\w.-]*")


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
    if ref.startswith("docker://"):
        # Container action: the immutable form is a sha256 digest, not a git SHA.
        if DOCKER_DIGEST_RE.search(ref):
            return None
        return (
            f"{where}: docker action '{ref}' is not pinned to an immutable "
            f"digest (use docker://image@sha256:<64-hex digest>)"
        )
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
        # Expression label — the literal candidates live in a matrix: block,
        # which is scanned separately below.
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


def check_matrix_line(line: str, where: str) -> list[str]:
    """Flag floating runner aliases among a matrix line's literal scalars.

    Handles the shapes a matrix block actually contains: ``os: [a, b]``,
    ``os: a``, block list items ``- a``, and ``include:`` entries
    ``- os: a``. Non-runner values (python versions, feature flags) simply
    never match the alias regex.
    """
    text = _strip_comment(line.strip())
    if text.startswith("- "):
        text = text[2:].strip()
    m = re.match(r"^[A-Za-z_][\w.-]*:\s*(.*)$", text)
    if m:
        text = m.group(1).strip()
    if not text:
        return []
    items = (
        [item.strip() for item in text[1:-1].split(",")]
        if text.startswith("[") and text.endswith("]")
        else [text]
    )
    drifts = []
    for item in items:
        item = _unquote(item)
        if FLOATING_RUNNER_RE.match(item):
            drifts.append(
                f"{where}: matrix value '{item}' floats with GitHub's image "
                f"rollout — pin an explicit image label (e.g. ubuntu-24.04)"
            )
    return drifts


def check_tool_pins(line: str, where: str) -> tuple[list[str], int]:
    """Assert inline installs of the pinned tools carry an exact version.

    Returns (drift messages, number of install lines examined).
    """
    drifts: list[str] = []
    examined = 0
    if NPM_INSTALL_RE.search(line) and "markdownlint-cli2" in line:
        examined += 1
        if not NPM_PINNED_RE.search(line):
            drifts.append(
                f"{where}: npm install of markdownlint-cli2 is unpinned — "
                f"use markdownlint-cli2@<exact version>"
            )
    if PIP_INSTALL_RE.search(line) and PIP_RUFF_RE.search(line):
        examined += 1
        if not PIP_PINNED_RE.search(line):
            drifts.append(
                f"{where}: pip install of ruff is unpinned — "
                f"use \"ruff==<exact version>\""
            )
    return drifts, examined


def scan_file(path: Path, rel: str) -> tuple[list[str], int, int]:
    """Scan one workflow; return (drifts, uses/runs-on lines, install lines)."""
    drifts: list[str] = []
    checked = 0
    pins = 0
    block_indent: int | None = None  # indent of the key that opened a run: | body
    matrix_indent: int | None = None  # indent of an open matrix: key
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        where = f"{rel}:{lineno}"

        if block_indent is not None:
            if indent > block_indent:
                # Inside a block scalar: free text, never workflow keys — a
                # heredoc'd "uses: foo@v1" here is prose. Installs, though,
                # live exactly here, so the tool-pin discipline still applies.
                pin_drifts, examined = check_tool_pins(line, where)
                drifts.extend(pin_drifts)
                pins += examined
                continue
            block_indent = None

        if matrix_indent is not None:
            if indent > matrix_indent:
                drifts.extend(check_matrix_line(line, where))
                continue
            matrix_indent = None

        if BLOCK_SCALAR_RE.match(line):
            block_indent = indent
            continue
        if MATRIX_RE.match(line):
            matrix_indent = indent
            continue

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
            continue

        pin_drifts, examined = check_tool_pins(line, where)
        drifts.extend(pin_drifts)
        pins += examined
    return drifts, checked, pins


def check(workflows_dir: Path | None = None) -> tuple[list[str], int]:
    """Scan every workflow; return (drift messages, count of checked lines)."""
    if workflows_dir is None:
        workflows_dir = WORKFLOWS
    drifts: list[str] = []
    checked = 0
    pins = 0
    for path in sorted(
        list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml"))
    ):
        try:
            rel = str(path.relative_to(workflows_dir.parents[1]))
        except ValueError:
            rel = str(path)
        file_drifts, file_checked, file_pins = scan_file(path, rel)
        drifts.extend(file_drifts)
        checked += file_checked
        pins += file_pins
    if checked == 0:
        drifts.append(
            f"no uses:/runs-on: lines found under {workflows_dir} — "
            f"canary is scanning the wrong place"
        )
    return drifts, checked + pins


def main() -> int:
    drifts, checked = check()
    if drifts:
        print("workflow-pin check FAILED:")
        for d in drifts:
            print(f"  - {d}")
        return 1
    print(f"workflow-pin check OK ({checked} uses:/runs-on:/install lines verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
