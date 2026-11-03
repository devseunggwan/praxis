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
         + section("https://github.com/b/y/pull/999")
         + "\nMerge both, #833 and #999?")
    return [
        user("prepare the two cross-repo PRs for merge"),
        bash("c1", "gh pr checks 999 --repo b/y"), result("c1", "all checks passing"),
        bash("c2", "gh pr checks 833 --repo a/x"), result("c2", "all checks passing"),
        bash("cd1", "cd ../elsewhere && ls"), result("cd1", ""),
        *ask("q1", q, "merge #833 and #999"),
        bash("m1", "gh pr merge 833 --repo a/x --squash"), result("m1", ""),
    ]


def _numbered(reply: str, questions: str, pr: str = "999") -> list[dict]:
    """Prior-turn briefing of ``pr`` ending in numbered ``questions``, answered
    with a numbered ``reply`` (issue #1630); nothing merged yet."""
    return [
        user(f"prepare PR #{pr} for merge"),
        bash("c1", f"gh pr checks {pr}"), result("c1", "all checks passing"),
        prose("Merge briefing\n\n" + section(f"PR #{pr}") + "\n" + questions),
        user(reply),
    ]


_Q_MERGE_FIRST = "1. Approve merge of #999?\n2. Follow-up: option A or option B?"
_Q_MERGE_LAST = "1. Follow-up: option A or option B?\n2. Approve merge of #999?"
_Q_MERGE_FIRST_KO = "1. #999 머지할까요?\n2. 후속 작업은 A, B 중 무엇으로 할까요?"
# A request list that asks with a noun phrase and option bullets, no `?`.
_Q_REQUEST_KO = ("요청 사항\n1. #999 머지 승인 여부\n   - (추천) 승인합니다. 머지 뒤 정리까지 합니다.\n"
                 "   - 보류하고 리뷰를 먼저 돌립니다.\n2. 머지 뒤 체크아웃 갱신 순서\n"
                 "   - (추천) 재시작한 뒤 갱신합니다.\n   - 바로 갱신합니다.")


def _numbered_serial(reply: str) -> list[dict]:
    """#999 merged on a typed approval, then a briefing of #1001 ending in
    numbered questions, answered with a numbered ``reply``."""
    return [
        *_numbered("approve", "Approve merge of #999?"),
        bash("m1", "gh pr merge 999 --squash"), result("m1", "merged"),
        bash("c2", "gh pr checks 1001"), result("c2", "all checks passing"),
        prose("Merge briefing\n\n" + section("PR #1001") + "\n"
              "1. Approve merge of #1001?\n2. Follow-up: option A or option B?"),
        user(reply),
    ]


CASES = {
    "momentum-merge-example.jsonl": _example,
    # Issue #1630: a numbered reply is read at the merge question's number.
    "momentum-merge-numbered-first-item.jsonl":
        lambda: _numbered("1. approve\n2. go with your recommendation", _Q_MERGE_FIRST),
    "momentum-merge-numbered-last-item.jsonl":
        lambda: _numbered("1. go with your recommendation\n2. approve", _Q_MERGE_LAST),
    "momentum-merge-numbered-first-item-ko.jsonl":
        lambda: _numbered("1. 머지 승인\n2. 추천대로 진행해 주세요", _Q_MERGE_FIRST_KO),
    "momentum-merge-numbered-hold-at-merge-item.jsonl":
        lambda: _numbered("1. hold\n2. approve", _Q_MERGE_FIRST),
    "momentum-merge-numbered-hold-at-merge-item-ko.jsonl":
        lambda: _numbered("1. 보류\n2. 진행", _Q_MERGE_FIRST_KO),
    "momentum-merge-numbered-unmatched.jsonl":
        lambda: _numbered("3. approve\n4. ok", _Q_MERGE_FIRST),
    "momentum-merge-numbered-request-ko.jsonl":
        lambda: _numbered("1. 승인\n2. 추천방향대로", _Q_REQUEST_KO),
    "momentum-merge-numbered-request-ko-hold.jsonl":
        lambda: _numbered("1. 보류\n2. 추천방향대로", _Q_REQUEST_KO),
    "momentum-merge-numbered-serial.jsonl":
        lambda: _numbered_serial("1. approve\n2. go with your recommendation"),
    "momentum-merge-numbered-serial-hold.jsonl":
        lambda: _numbered_serial("1. hold\n2. approve"),
    # A later word on the merge in the same reply, a question about a step
    # around the merge, and another PR's unnamed ask: none approves #999.
    "momentum-merge-numbered-trailing-hold.jsonl":
        lambda: _numbered("1. approve\n2. B\n\nActually, hold off on the merge for now.",
                          _Q_MERGE_FIRST),
    "momentum-merge-numbered-prep-ask.jsonl":
        lambda: _numbered("1. yes\n2. B",
                          "1. Re-run CI before merging #999?\n2. Follow-up: option A or option B?"),
    "momentum-merge-numbered-other-pr-ask.jsonl":
        lambda: [user("prepare PR #1001 for merge"),
                 bash("c1", "gh pr checks 999"), result("c1", "all checks passing"),
                 prose("Merge briefing\n\n" + section("PR #1001") + "\n"
                       "1. Approve merge?\n2. Should I start reviewing #999 next?"),
                 user("1. ok\n2. no")],
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
