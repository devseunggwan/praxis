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


# A prose menu of lettered choice lines, answered with the letter alone
# (issue #1652).
_MENU_MERGE_KO = "어떻게 할까요?\nA: 머지합니다\nB: 보류"
_MENU_NEGATED_KO = "어떻게 할까요?\nA: 머지하지 않기\nB: 머지"
_MENU_HOLD_FIRST_KO = "어떻게 할까요?\nA: 보류\nB: 머지"
_MENU_B_REVIEW_KO = "어떻게 할까요?\nA: 머지합니다\nB: 리뷰를 한 번 더 돌립니다"


def _lettered_serial(reply: str) -> list[dict]:
    """#999 merged on a typed approval, then a briefing of #1001 ending in a
    lettered menu with no merge question, answered with ``reply``."""
    return [
        *_numbered("approve", "Approve merge of #999?"),
        bash("m1", "gh pr merge 999 --squash"), result("m1", "merged"),
        bash("c2", "gh pr checks 1001"), result("c2", "all checks passing"),
        prose("Merge briefing\n\n" + section("PR #1001") + "\n" + _MENU_MERGE_KO),
        user(reply),
    ]


def _host_relayed(merge_result: dict | None = None) -> list[dict]:
    """Prior-turn briefing of o/r#999, then the host's own row relaying an
    answer given in another channel; the transcript path never reads it as an
    approval (issue #1639). ``merge_result`` appends an earlier
    ``gh pr merge 999 --repo o/r`` call ``m1`` with that result row."""
    rows = [
        user("prepare o/r#999 for merge"),
        bash("c1", "gh pr checks 999 --repo o/r"), result("c1", "all checks passing"),
        prose("Merge briefing\n\n" + section("o/r#999") + "\nApprove merge of #999?"),
        user("[host notice] An answer for your question arrived in another channel. "
             "Read it there before you continue."),
    ]
    if merge_result is not None:
        rows += [bash("m1", "gh pr merge 999 --repo o/r --squash"), merge_result]
    return rows


def _failed_result(tid: str, text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": True}]}}


CASES = {
    "momentum-merge-example.jsonl": _example,
    # Issue #1639: an answer relayed by the host, for the approval provider.
    "momentum-merge-host-relayed.jsonl": _host_relayed,
    "momentum-merge-host-relayed-failed-merge.jsonl":
        lambda: _host_relayed(_failed_result("m1", "blocked by another gate")),
    "momentum-merge-host-relayed-merged.jsonl":
        lambda: _host_relayed(result("m1", "merged")),
    "momentum-merge-host-relayed-gh-host.jsonl":
        lambda: [bash("e1", "export GH_HOST=ghe.example.com"), result("e1", ""), *_host_relayed()],
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
    # A numbered hold on a later re-ask withdraws a numbered approval (serial).
    "momentum-merge-numbered-serial-rehold.jsonl":
        lambda: [*_numbered_serial("1. approve\n2. B"),
                 prose("One more check before I merge.\n"
                       "1. Approve merge of #1001?\n2. Follow-up: option A or option B?"),
                 user("1. hold\n2. B"),
                 prose("Merge briefing\n\n" + section("PR #1001") + "\nApprove merge of #1001?")],
    # A merge ask naming the PR by URL is one sentence, dots and all.
    "momentum-merge-numbered-url-ask.jsonl":
        lambda: _numbered("1. approve\n2. ok",
                          "1. Approve merge of https://github.com/o/r/pull/999?\n"
                          "2. Follow-up: option A or option B?"),
    # A condition written after the ask makes `yes` a conditional yes.
    "momentum-merge-numbered-trailing-condition.jsonl":
        lambda: _numbered("1. yes\n2. B",
                          "1. Approve merge of #999? Only after the release.\n"
                          "2. Follow-up: option A or option B?"),
    # Issue #1652: a one-letter reply is the choice line it picks.
    "momentum-merge-letter-merge-ko.jsonl": lambda: _numbered("A", _MENU_MERGE_KO),
    "momentum-merge-letter-merge-lower.jsonl": lambda: _numbered("a.", _MENU_MERGE_KO),
    "momentum-merge-letter-negated.jsonl": lambda: _numbered("A", _MENU_NEGATED_KO),
    "momentum-merge-letter-no-menu.jsonl":
        lambda: _numbered("A", "Approve merge of #999?"),
    "momentum-merge-letter-hold.jsonl": lambda: _numbered("A", _MENU_HOLD_FIRST_KO),
    "momentum-merge-letter-b-not-merge.jsonl": lambda: _numbered("B", _MENU_B_REVIEW_KO),
    "momentum-merge-letter-serial.jsonl": lambda: _lettered_serial("A"),
    "momentum-merge-letter-serial-hold.jsonl": lambda: _lettered_serial("B"),
    # A later `B` (hold) to a re-asked menu withdraws the earlier `A`.
    "momentum-merge-letter-serial-rehold.jsonl":
        lambda: [*_lettered_serial("A"),
                 prose("One more check before I merge #1001.\n" + _MENU_MERGE_KO),
                 user("B"),
                 prose("Merge briefing\n\n" + section("PR #1001") + "\nApprove merge of #1001?")],
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
