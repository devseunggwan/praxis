#!/usr/bin/env python3
"""Invariant canary: load-bearing hook tokens must stay in sync on BOTH sides.

praxis's known root failure mode is dual-SoT drift: a load-bearing token (a
regex a hook scans for, an env-var family it matches) lives both in the hook
source that enforces it and in the spec.md that documents it. When one copy is
reworded the two silently diverge — the doc promises a token the hook no longer
matches, or vice versa. This canary pins each such token and fails CI when it
drifts.

Each invariant pins TWO things, one per side, because the two sides carry the
token in different shapes:

  - ``scan_pattern`` — the enforcing **regex-source fragment** that must appear
    verbatim in the hook source. This is deliberately the anchored/escaped form
    (e.g. ``^Pre-commit verified:`` or ``\\[skip-codex-review\\]``) that occurs
    ONLY on the ``re.compile`` line, not the bare token that also shows up in
    docstrings, comments, and deny-message strings. Pinning the bare token on
    the scan side would let a reword of the enforcing line slip through as long
    as any comment copy survived — so we pin the regex fragment instead.
  - ``token`` — the human-facing literal the spec documents (and that users copy
    into commit messages). Pinned by presence in the doc; the doc's contract is
    "this token is documented", so substring presence is the right strength
    there.

Scope (issue #712): only tokens a hook actually scans. ``Not-tested:`` /
``Confidence:`` are commit-trailer conventions no hook scans — out of scope by
design, so they are deliberately absent from the table below.

The INVARIANTS table itself is the pin point (a third, intentional copy, the
same model as ponytail's ``check-rule-copies.js``): if a token is renamed on
both sides at once, the hardcoded literals here stop matching and force the
canary to be updated in lockstep.

Run standalone or via ``scripts/run-tests.sh``. Exit 0 + a verified count on a
clean tree; exit 1 listing the offending (token, side, file) on drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Each invariant pins one load-bearing token across the two files that must
# stay in sync. ``scan_pattern`` is checked against the hook source (the
# enforcing regex fragment); ``token`` is checked against the doc (the
# documented literal). See the module docstring for why the two differ.
INVARIANTS: list[dict] = [
    {
        "token": "Pre-commit verified:",
        "scan_pattern": "^Pre-commit verified:",
        "scan": "hooks/preflight-gate/block-pr-without-precommit-evidence/impl.py",
        "doc": "hooks/preflight-gate/block-pr-without-precommit-evidence/spec.md",
    },
    {
        "token": "Caller chain verified:",
        "scan_pattern": "^Caller chain verified:",
        "scan": "hooks/preflight-gate/block-pr-without-caller-evidence/impl.py",
        "doc": "hooks/preflight-gate/block-pr-without-caller-evidence/spec.md",
    },
    {
        "token": "[skip-codex-review]",
        "scan_pattern": r"\[skip-codex-review\]",
        "scan": "hooks/preflight-gate/block-commit-without-codex-review/impl.py",
        "doc": "hooks/preflight-gate/block-commit-without-codex-review/spec.md",
    },
    {
        "token": "[user-approved]",
        "scan_pattern": r"\[(?:user-approved|ratified-by-user|user-ratified)\]",
        "scan": "hooks/preflight-gate/block-sciomc-finding-commit/impl.py",
        "doc": "hooks/preflight-gate/block-sciomc-finding-commit/spec.md",
    },
    {
        "token": "[ratified-by-user]",
        "scan_pattern": r"\[(?:user-approved|ratified-by-user|user-ratified)\]",
        "scan": "hooks/preflight-gate/block-sciomc-finding-commit/impl.py",
        "doc": "hooks/preflight-gate/block-sciomc-finding-commit/spec.md",
    },
    {
        # The bypass-family detection regex itself — the strongest pin
        # available. bypass-telemetry's _BYPASS_NAME_RE source must equal the
        # regex the spec documents, or the telemetry silently stops counting a
        # renamed bypass family. Here scan_pattern == token: the token *is* the
        # full regex literal, which appears only at the re.compile line.
        "token": r"^(?:CLAUDE_HOOK_|PRAXIS_).*BYPASS",
        "scan_pattern": r"^(?:CLAUDE_HOOK_|PRAXIS_).*BYPASS",
        "scan": "hooks/postuse-correction/bypass-telemetry/impl.py",
        "doc": "hooks/postuse-correction/bypass-telemetry/spec.md",
    },
]


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        # FileNotFoundError and IsADirectoryError are both OSError subclasses;
        # a mistyped path is reported as drift below rather than crashing.
        return None


def check(repo: Path = REPO, invariants: list[dict] = INVARIANTS) -> list[str]:
    """Return a list of drift messages; empty list means every invariant holds."""
    drifts: list[str] = []
    for inv in invariants:
        token = inv["token"]

        # Scan side: the enforcing regex fragment must be present verbatim.
        scan_text = _read(repo / inv["scan"])
        if scan_text is None:
            drifts.append(f"{token!r}: scan file missing on disk: {inv['scan']}")
        elif inv["scan_pattern"] not in scan_text:
            drifts.append(
                f"{token!r}: enforcing pattern {inv['scan_pattern']!r} "
                f"absent from scan side {inv['scan']}"
            )

        # Doc side: the documented literal must be present verbatim.
        doc_text = _read(repo / inv["doc"])
        if doc_text is None:
            drifts.append(f"{token!r}: doc file missing on disk: {inv['doc']}")
        elif token not in doc_text:
            drifts.append(f"{token!r}: absent from doc side {inv['doc']}")
    return drifts


def main() -> int:
    drifts = check()
    if drifts:
        print("hook-token-invariant check FAILED:")
        for d in drifts:
            print(f"  - {d}")
        return 1
    print(f"hook-token-invariant check OK ({len(INVARIANTS)} invariants verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
