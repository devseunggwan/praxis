#!/usr/bin/env python3
"""PreToolUse(AskUserQuestion) gate: re-fetch live PR state before a
PR-state-contingent next-step question.

Issue #719. A next-step `AskUserQuestion` menu whose text depends on a PR's
merge/close state can go stale mid-turn: the PR gets merged/closed by another
actor (or by an earlier step in the same turn) and the menu still surfaces
"merge PR #N?"-style options against that outdated premise. The user then has
to notice and correct the agent rather than the agent noticing itself.

No existing hook covers this:
  - `merge-state-claim-gate` (Stop) only checks the *final assistant message*
    for a completed-state claim, post-hoc — it never sees a mid-turn
    AskUserQuestion, and it fires after the question has already been shown.
  - `output-block-falsify-advisory` (PreToolUse/AskUserQuestion) emits a
    static text reminder ("may already be resolved by a merged PR") but never
    calls `gh` — it cannot tell whether the premise is actually stale.
  - `pre-merge-approval-gate` (PreToolUse/Bash) gates the `gh pr merge`
    command itself, not the *question* that precedes the decision to run it.

This hook is the live-refetch analogue of `pre-merge-approval-gate`'s
lock-boundary pattern applied to a different surface: when a question's text
names a specific PR number alongside a merge-intent keyword, it re-fetches
that PR's live `state`/`mergeStateStatus` via `gh pr view` BEFORE the menu is
allowed to surface, and warns (or, in strict mode, blocks) when the live state
is already MERGED or CLOSED.

Detection signal (co-occurrence, scoped per-question — see spec.md "False
positive boundary" for the full rationale):
  1. A PR-number token (`#123`, `PR #123`) anywhere in the question text or
     any of its options' label/description.
  2. A merge-intent keyword (`merge`, `squash`, Korean `머지`) anywhere in
     that same combined per-question text.

Both conditions must hold for the SAME `questions[]` entry — a merge keyword
in one question does not pair with a PR number in an unrelated question in
the same payload.

Default mode: advisory (exit 0 + stderr warning).
Strict mode (PRAXIS_PR_STATE_REFETCH_STRICT=1): block (exit 2).

Fail-open conditions (never block/advise):
  1. tool_name != "AskUserQuestion"
  2. No question carries the PR-number + merge-keyword co-occurrence signal
  3. `gh` binary missing, `gh pr view` errors, times out, or returns
     unparseable JSON for a candidate PR number — that number is silently
     skipped (the live state genuinely could not be determined)
  4. Live state is neither MERGED nor CLOSED (still OPEN — the premise holds)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from ask_option_text import collect_option_texts  # type: ignore[import-not-found]  # noqa: E402
from block_message import format_block  # type: ignore[import-not-found]  # noqa: E402

_STRICT_ENV = "PRAXIS_PR_STATE_REFETCH_STRICT"
_GH_TIMEOUT_SEC = 2
_MAX_PR_NUMBERS = 3

# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

# PR-number token: `#123` or `PR #123` both match on the bare `#\d+` — the
# optional "PR" prefix carries no extra information once the `#` + digits are
# present, so it is not tokenized separately (see spec.md design rationale).
_PR_NUM_RE = re.compile(r"#(\d+)")

# Merge-intent keyword. Mirrors `merge-menu-review-options-advisory`'s
# `_KO_MERGE_RE` / `MERGE_TOKENS_EN` (issue #560) — same false-positive-tested
# precision: the Korean negative lookahead excludes the meaning-inverting
# `머지된` (already-merged, a triage label) and `머지하지` (`머지하지 말고` "do
# NOT merge") inflections; English tokens use ASCII-letter lookaround so
# `merged`/`merger` do not match while mixed-script `squash 머지` still does.
# Not extracted to `_lib`: this is the 2nd consumer of this exact pattern
# (merge-menu-review-options-advisory is the 1st) — repo convention defers
# DRY extraction to the 3rd consumer.
_KO_MERGE_RE = re.compile(r"머지(?!된|하지)")
_MERGE_TOKENS_EN = ("merge", "squash")


def _en_token_present(token: str, lower_text: str) -> bool:
    pattern = r"(?<![a-z])" + re.escape(token.lower()) + r"(?![a-z])"
    return re.search(pattern, lower_text) is not None


def _has_merge_keyword(text: str) -> bool:
    if _KO_MERGE_RE.search(text):
        return True
    lower = text.lower()
    return any(_en_token_present(tok, lower) for tok in _MERGE_TOKENS_EN)


# ---------------------------------------------------------------------------
# Payload parsing
# ---------------------------------------------------------------------------


def _question_text_units(tool_input: dict) -> list[str]:
    """Return one combined text string per `questions[]` entry.

    Each unit joins the question's `header` and `question` text with every
    option's label + description (via the shared `collect_option_texts`
    helper) so the co-occurrence check scans the whole decision context, not
    just the field the number or the keyword happens to sit in — a real
    PR-state menu typically states the number in the question and the merge
    verb in an option label (e.g. question: "PR #714에 대해 어떻게 할까요?",
    option: "Merge"), but the short `header` chip (max 12 chars) can also
    carry the PR number on its own (e.g. header: "PR #714", option: "Merge").
    Tolerant of partial/malformed schemas — any missing field yields an empty
    unit rather than an exception.
    """
    units: list[str] = []
    questions = tool_input.get("questions") or []
    if not isinstance(questions, list):
        return units
    for q in questions:
        if not isinstance(q, dict):
            continue
        parts: list[str] = []
        header = q.get("header")
        if isinstance(header, str):
            parts.append(header)
        qtext = q.get("question")
        if isinstance(qtext, str):
            parts.append(qtext)
        options = q.get("options") or []
        if isinstance(options, list):
            parts.extend(collect_option_texts(options))
        units.append(" ".join(parts))
    return units


def _extract_pr_numbers(text: str) -> list[str]:
    numbers: list[str] = []
    for m in _PR_NUM_RE.finditer(text):
        n = m.group(1)
        if n not in numbers:
            numbers.append(n)
        if len(numbers) >= _MAX_PR_NUMBERS:
            break
    return numbers


def _candidate_pr_numbers(tool_input: dict) -> list[str]:
    """Return PR numbers from questions carrying BOTH signals (co-occurrence).

    A question unit without a merge keyword is skipped even if it contains a
    `#123` token — a bare PR/issue-number mention with no merge intent is
    exactly the false-positive case the co-occurrence design excludes.
    """
    numbers: list[str] = []
    for text in _question_text_units(tool_input):
        if not text or not _has_merge_keyword(text):
            continue
        for n in _extract_pr_numbers(text):
            if n not in numbers:
                numbers.append(n)
    return numbers[:_MAX_PR_NUMBERS]


# ---------------------------------------------------------------------------
# Live gh pr view re-fetch
# ---------------------------------------------------------------------------


def _gh_pr_state(cwd: str | None, number: str) -> tuple[str, object] | None:
    """Run `gh pr view <N> --json state,mergeStateStatus`.

    Returns (state, mergeStateStatus) on success, or None on ANY failure
    (binary missing, non-zero exit, timeout, unparseable JSON, non-dict
    payload, missing/non-string `state`) — the caller treats None as
    "cannot determine live state", which fail-opens that single PR number
    rather than blocking on an infrastructure error.
    """
    try:
        proc = subprocess.run(
            ["gh", "pr", "view", number, "--json", "state,mergeStateStatus"],
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT_SEC,
            cwd=cwd or None,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    state = data.get("state")
    if not isinstance(state, str) or not state:
        return None
    return state.upper(), data.get("mergeStateStatus")


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def _stale_lines(stale: list[tuple[str, str, object]]) -> str:
    lines = []
    for number, state, merge_status in stale:
        suffix = f" (mergeStateStatus={merge_status})" if merge_status else ""
        lines.append(f"  - PR #{number}: live state = {state}{suffix}")
    return "\n".join(lines)


def _advisory_msg(stale: list[tuple[str, str, object]]) -> str:
    return (
        "[advisory] This AskUserQuestion names a PR number alongside a "
        "merge-intent keyword (merge/squash/머지), so its premise depends on "
        "that PR's state. A live `gh pr view` re-fetch shows it is ALREADY "
        "resolved:\n"
        f"{_stale_lines(stale)}\n"
        "\n"
        "Asking the user to decide against a stale premise (e.g. \"merge PR "
        "#N?\" when #N is already MERGED/CLOSED) forces them to notice and "
        "correct it. Re-author the question to reflect the live state, or "
        "act directly (report the PR is already resolved) instead of "
        "surfacing this menu.\n"
        "\n"
        "Strict mode disabled. Set PRAXIS_PR_STATE_REFETCH_STRICT=1 to block.\n"
    )


def _block_msg(stale: list[tuple[str, str, object]]) -> str:
    # Standard five-field block format (issue #439) — see
    # docs/hook/block-message-format.md. The dynamic per-PR live-state list
    # doesn't fit the fixed fields, so it is appended after the formatted
    # block, mirroring block-gh-issue-create-without-dup-search's pattern of
    # emit_block(...) + a following dynamic detail write.
    return (
        format_block(
            rule_name="pr state re-fetch",
            why="the question's text names this PR alongside a merge-intent "
                "keyword, but a live `gh pr view` re-fetch shows its state is "
                "already MERGED/CLOSED — the premise is stale",
            correct_path="re-issue the AskUserQuestion reflecting the live "
                "state, or act directly (report the PR is already resolved) "
                "instead of asking",
            bypass_env=None,
            reference="CLAUDE.md → External Discussion Fidelity - Lock-"
                "boundary re-fetch; hooks/preflight-gate/pr-state-refetch-"
                "gate/spec.md",
        )
        + "\n\nLive state:\n"
        + f"{_stale_lines(stale)}\n"
        + "\nTo opt out: unset PRAXIS_PR_STATE_REFETCH_STRICT (default is "
        "advisory).\n"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


@fail_open
def main() -> int:
    payload = read_payload()
    if payload is None:
        return 0

    if not isinstance(payload, dict):
        return 0
    if payload.get("tool_name") != "AskUserQuestion":
        return 0

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0

    candidates = _candidate_pr_numbers(tool_input)
    if not candidates:
        return 0

    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        cwd = None

    stale: list[tuple[str, str, object]] = []
    for number in candidates:
        result = _gh_pr_state(cwd, number)
        if result is None:
            continue  # cannot determine live state — fail-open for this PR
        state, merge_status = result
        if state in ("MERGED", "CLOSED"):
            stale.append((number, state, merge_status))

    if not stale:
        return 0

    # Strict only on the documented `=1` value, matching the dominant
    # codebase convention (merge-menu-review-options-advisory,
    # destructive-bash-guard, protected-paths-guard, push-remote-ref-verify).
    strict_set = os.environ.get(_STRICT_ENV, "").strip() == "1"

    if strict_set:
        sys.stderr.write(_block_msg(stale))
        return 2

    sys.stderr.write(_advisory_msg(stale))
    return 0


if __name__ == "__main__":
    sys.exit(main())
