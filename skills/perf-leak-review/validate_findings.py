#!/usr/bin/env python3
"""Validate a `perf-leak-review` finding envelope against the closed contract (#1429).

Reads the reviewer's JSON envelope on stdin and exits 0 only when every
finding matches the schema below. The reviewer is an LLM, so nothing upstream
of this script is deterministic — this is the one place the closed defect-class
list and the output shape are actually enforced, which is why it rejects rather
than repairs. A repaired envelope would make an out-of-list finding look like a
compliant one, and counting those is the point (AC-6).

Envelope:
  {"repo_root": "<the --repo argument, echoed>", "findings": [ ... ]}

Finding (every key required, no others accepted):
  class       one of C1..C5 — the closed list in DEFECT_CLASSES
  file        path relative to repo_root; absolute paths are rejected so the
              envelope stays portable across the prepared/real tree boundary
  line        integer >= 1
  evidence    non-empty quoted excerpt from the code under review
  confidence  one of low / med / high
  grade       the literal "candidate" — the reviewer reads, never runs, so no
              finding it produces is ever a proven defect

An empty `findings` list is valid: that is what a clean diff looks like, and
the clean control fixtures depend on it being accepted (AC-5).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

# The closed list (spec `### Defect classes`). A sixth entry is a spec change,
# not a validator change — `tests/test_perf_leak_review_skill_shape.py` reads
# this mapping and requires the reviewer brief to carry exactly these headings,
# so the two cannot drift apart silently.
DEFECT_CLASSES: dict[str, str] = {
    "C1": "N+1 / remote call inside a loop",
    "C2": "Unreleased resource",
    "C3": "Unbounded global container or cache",
    "C4": "Load-everything-then-filter-in-memory",
    "C5": "Unreleased listener, callback, or timer",
}

CONFIDENCE_VALUES = ("low", "med", "high")
GRADE = "candidate"
FINDING_KEYS = frozenset(
    {"class", "file", "line", "evidence", "confidence", "grade"}
)


def _check_finding(index: int, finding: Any, violations: list[str]) -> None:
    where = f"findings[{index}]"
    if not isinstance(finding, dict):
        violations.append(f"{where}: not an object (got {type(finding).__name__})")
        return

    keys = set(finding)
    for missing in sorted(FINDING_KEYS - keys):
        violations.append(f"{where}.{missing}: missing")
    for extra in sorted(keys - FINDING_KEYS):
        violations.append(
            f"{where}.{extra}: unexpected key — the finding schema is closed"
        )

    klass = finding.get("class")
    # The isinstance guard is load-bearing: an unhashable value (list, dict) in
    # `class` makes the dict membership test raise instead of reporting.
    if "class" in finding and (
        not isinstance(klass, str) or klass not in DEFECT_CLASSES
    ):
        violations.append(
            f"{where}.class: {klass!r} is outside the closed list "
            f"{sorted(DEFECT_CLASSES)} — report nothing outside it"
        )

    path = finding.get("file")
    if "file" in finding:
        if not isinstance(path, str) or not path:
            violations.append(f"{where}.file: must be a non-empty string")
        elif path.startswith("/"):
            violations.append(
                f"{where}.file: {path!r} is absolute — must be relative to repo_root"
            )

    line = finding.get("line")
    if "line" in finding:
        # bool is an int subclass; True would otherwise pass as line 1.
        if isinstance(line, bool) or not isinstance(line, int) or line < 1:
            violations.append(f"{where}.line: must be an integer >= 1 (got {line!r})")

    evidence = finding.get("evidence")
    if "evidence" in finding and (not isinstance(evidence, str) or not evidence.strip()):
        violations.append(f"{where}.evidence: must be a non-empty string")

    confidence = finding.get("confidence")
    if "confidence" in finding and confidence not in CONFIDENCE_VALUES:
        violations.append(
            f"{where}.confidence: {confidence!r} is not one of "
            f"{list(CONFIDENCE_VALUES)}"
        )

    grade = finding.get("grade")
    if "grade" in finding and grade != GRADE:
        violations.append(
            f"{where}.grade: must be {GRADE!r} — a read-only review never proves "
            f"a defect (got {grade!r})"
        )


def validate(envelope: Any) -> list[str]:
    """Return every violation in `envelope`; an empty list means it is valid."""
    violations: list[str] = []
    if not isinstance(envelope, dict):
        return [f"envelope: not an object (got {type(envelope).__name__})"]

    for extra in sorted(set(envelope) - {"repo_root", "findings"}):
        violations.append(
            f"envelope.{extra}: unexpected key — the envelope carries the echoed "
            "repo_root and the findings, nothing else"
        )

    repo_root = envelope.get("repo_root")
    if not isinstance(repo_root, str) or not repo_root:
        violations.append("envelope.repo_root: must be a non-empty string")

    findings = envelope.get("findings")
    if not isinstance(findings, list):
        violations.append(
            f"envelope.findings: must be a list (got {type(findings).__name__})"
        )
        return violations

    for index, finding in enumerate(findings):
        _check_finding(index, finding, violations)
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a perf-leak-review finding envelope read from stdin; "
            "exit 1 and print every violation when it does not match"
        ),
    )
    parser.parse_args()

    raw = sys.stdin.read()
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"perf-leak-review: envelope is not valid JSON: {exc}", file=sys.stderr)
        return 1

    violations = validate(envelope)
    if violations:
        for violation in violations:
            print(f"perf-leak-review: {violation}", file=sys.stderr)
        return 1

    count = len(envelope["findings"])
    print(f"perf-leak-review: envelope OK — {count} finding(s), all in C1..C5")
    return 0


if __name__ == "__main__":
    sys.exit(main())
