#!/usr/bin/env python3
"""Score one perf-leak-review envelope against its fixture (#1429).

Called by `scripts/perf-leak-review-eval.sh score`, which owns the tree checks
(write protection, `git status --porcelain`) and hands this the two files. Paths
come from `prepared.json` — written by `prepare` — and never from the envelope,
which is the reviewer's own output and so cannot be its own oracle.

Three checks and one report:

  repo_root   the echoed value resolves to the prepared tree. Both sides are
              realpath'd: macOS hands out /var/... for a directory that reads
              back as /private/var/..., and an unnormalized compare would fail
              on the platform rather than on the answer.
  file        every findings[].file exists under the prepared tree. Relative by
              contract, so an absolute path is a contract violation, not a hint.
  class       a fixture with an expected_class needs a finding of exactly that
              class; a clean fixture (expected_class null) needs none at all.

The report also prints a precision row — how many findings were not the expected
class. That number is observation, not a gate: a run can pass with surplus
findings, and the anchor records them so mislabelling stays visible.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL read: {path}: {exc}")
        raise SystemExit(1) from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score a perf-leak-review envelope against its fixture",
    )
    parser.add_argument("--prepared-json", required=True)
    parser.add_argument("--envelope", required=True)
    args = parser.parse_args()

    prepared_meta = _load(Path(args.prepared_json))
    envelope = _load(Path(args.envelope))

    prepared_path = Path(prepared_meta["prepared_path"]).resolve()
    fixture = Path(prepared_meta["fixture"])
    meta = _load(fixture / "meta.json")
    expected_class = meta.get("expected_class")

    failures: list[str] = []

    echoed = envelope.get("repo_root")
    if not isinstance(echoed, str) or not echoed:
        failures.append("repo_root: envelope carries no repo_root string")
    else:
        try:
            resolved = Path(echoed).resolve()
        except OSError as exc:  # pragma: no cover - resolve() rarely raises here
            failures.append(f"repo_root: {echoed!r} did not resolve: {exc}")
        else:
            if resolved != prepared_path:
                failures.append(
                    f"repo_root: {resolved} is not the prepared tree {prepared_path}"
                )
            else:
                print(f"PASS repo_root: {resolved}")

    findings = envelope.get("findings")
    if not isinstance(findings, list):
        print("FAIL findings: envelope.findings is not a list")
        return 1

    for index, finding in enumerate(findings):
        rel = finding.get("file") if isinstance(finding, dict) else None
        if not isinstance(rel, str) or not rel:
            failures.append(f"findings[{index}].file: missing")
            continue
        if rel.startswith("/"):
            failures.append(
                f"findings[{index}].file: {rel!r} is absolute — must be relative "
                "to repo_root"
            )
            continue
        if not (prepared_path / rel).is_file():
            failures.append(
                f"findings[{index}].file: {rel!r} does not exist under "
                f"{prepared_path}"
            )
    if findings and not any(f.startswith("findings[") for f in failures):
        print(f"PASS file-exists: {len(findings)} finding path(s) resolve")

    classes = [
        finding.get("class")
        for finding in findings
        if isinstance(finding, dict)
    ]
    if expected_class is None:
        if classes:
            failures.append(
                f"class: clean fixture {fixture.name} drew {len(classes)} "
                f"finding(s) {classes} — expected none"
            )
        else:
            print(f"PASS clean-control: {fixture.name} drew 0 findings")
        surplus = len(classes)
    else:
        if expected_class in classes:
            print(f"PASS recall: {fixture.name} → {expected_class}")
        else:
            failures.append(
                f"class: {fixture.name} expected {expected_class}, got "
                f"{classes or 'no findings'}"
            )
        surplus = sum(1 for value in classes if value != expected_class)

    print(f"precision: {fixture.name} surplus findings = {surplus} (observation, not a gate)")

    for failure in failures:
        print(f"FAIL {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
