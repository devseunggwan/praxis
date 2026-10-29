#!/usr/bin/env python3
"""Generate merge-gate transcript fixtures (Claude Code JSONL).

Usage::

    python3 tests/fixtures/merge_gate_fixtures.py [--out DIR]

Helpers for the rows a ``momentum-rule-retrieval-gate`` merge fixture needs —
user text, assistant prose, a Bash ``tool_use`` and its ``tool_result``, an
``AskUserQuestion`` plus the host's ``"q"="a"`` answer row, and a six-item
briefing section — and a ``CASES`` table that ``main`` writes, one file per
case, to ``--out`` (default: this directory, ``tests/fixtures``).

Add a case by adding a ``CASES`` entry, then pin it in
``tests/hooks/advisory-nudge/test_momentum_rule_retrieval_gate.sh`` as a
pass/deny pair::

    run_merge_escalation_case "<name>" "<yes|no>" "" "<fixture>.jsonl" "<command>"

where ``yes`` expects a deny. Name each PR in ONE reference form per fixture
(``owner/repo#N`` or a ``/pull/N`` URL), so a broken regex for either form
fails a case on its own instead of being masked by the other.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def prose(text: str) -> dict:
    return {"type": "assistant", "message": {"role": "assistant",
                                             "content": [{"type": "text", "text": text}]}}


def bash(tid: str, cmd: str) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd}}]}}


def result(tid: str, text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text}]}}


def ask(tid: str, question: str, label: str, other: str = "hold") -> list[dict]:
    """AskUserQuestion with options ``label`` / ``other``, answered with ``label``."""
    return [
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tid, "name": "AskUserQuestion", "input": {"questions": [
                {"question": question, "header": "Merge", "multiSelect": False,
                 "options": [{"label": label, "description": ""},
                             {"label": other, "description": ""}]}]}}]}},
        result(tid, f'User has answered your questions: "{question}"="{label}". '
                    "You can now continue with the user's answers in mind."),
    ]


def section(heading: str, extra: str = "") -> str:
    """Six-item briefing section; ``heading`` carries the PR reference."""
    return (f"## {heading}\n- What changed: one rule row\n- Verified: CI pass, tests pass\n"
            f"- NOT verified: live routing\n- Risk: docs only\n- Open items: none{extra}\n")


def write(out: Path, name: str, events: list[dict]) -> Path:
    path = out / name
    with open(path, "w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return path


def _example() -> list[dict]:
    """Cross-repo bulk ask, an unrelated ``cd`` after the probes, first PR merged."""
    q = ("Merge briefing\n\n" + section("a/x#833") + "\n"
         + section("PR #999 https://github.com/b/y/pull/999")
         + "\nMerge both, #833 and #999?")
    return [
        user("prepare the two cross-repo PRs for merge"),
        bash("c1", "gh pr checks 999 --repo b/y"), result("c1", "all checks passing"),
        bash("c2", "gh pr checks 833 --repo a/x"), result("c2", "all checks passing"),
        bash("cd1", "cd ../elsewhere && ls"), result("cd1", ""),
        *ask("q1", q, "merge #833 and #999"),
        bash("m1", "gh pr merge 833 --repo a/x --squash"), result("m1", ""),
    ]


CASES = {
    "momentum-merge-example.jsonl": _example,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    ap.add_argument("--out", type=Path, default=HERE, help="fixture directory (default: %(default)s)")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    for name, build in CASES.items():
        print(write(a.out, name, build()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
