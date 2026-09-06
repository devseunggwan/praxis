# PreToolUse AskUserQuestion PR-State Re-Fetch Gate

Supported hosts: all

`hooks/preflight-gate/pr-state-refetch-gate/impl.py` fires on every
PreToolUse(AskUserQuestion) event. When a question's text names a PR number
alongside a merge-intent keyword, the hook re-fetches that PR's **live**
`state`/`mergeStateStatus` via `gh pr view` before the menu is allowed to
surface. If the live state is already `MERGED` or `CLOSED`, it warns
(default) or blocks (strict mode) — the question's premise is stale.

## Why this exists

A next-step `AskUserQuestion` menu whose text depends on a PR's merge/close
state can go stale mid-turn: the PR gets merged or closed (by another actor,
or by an earlier step in the same turn) and the menu still surfaces
"merge PR #N?"-style options against that outdated premise. The user then has
to notice the mismatch and correct the agent themselves — exactly the kind of
silent premise drift the *External Discussion Fidelity — lock-boundary
re-fetch* rule ([`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries)) targets for other surfaces (Slack
threads, PR mergeable status at merge-ask time).

No existing hook covers this surface:

| Hook | Why it doesn't cover this |
| ------ | --------------------------- |
| `merge-state-claim-gate` (Stop) | Only scans the **final assistant message** for a completed-state claim, post-hoc. It cannot see a mid-turn `AskUserQuestion` — by the time the Stop hook runs, the stale question has already been shown to the user. |
| `output-block-falsify-advisory` (PreToolUse/AskUserQuestion) | Emits a static text reminder ("may already be resolved by a merged PR") but never calls `gh` — it cannot tell whether the premise is actually stale, only that it *might* be. |
| `pre-merge-approval-gate` (PreToolUse/Bash) | Gates the `gh pr merge` **command** itself, not the **question** that precedes the decision to run it. By the time this hook would fire, the stale menu has already round-tripped through the user. |

This hook is the live-refetch analogue of `pre-merge-approval-gate`'s
lock-boundary re-fetch pattern (`gh pr view --json mergeable,mergeStateStatus`
immediately before surfacing a merge-approval ask), extended to a different
surface: a PR-state-contingent **question**, not a merge **command**.

## Detection signal (co-occurrence)

Both conditions must hold for the **same** `questions[]` entry (question text

+ all of that question's option labels/descriptions, joined):

1. A **PR-number token** — `#\d+` (matches both bare `#123` and `PR #123`;
   the optional `PR` prefix carries no extra information once `#123` is
   present, so it is not tokenized separately).
2. A **merge-intent keyword** — `merge`, `squash` (English, ASCII-letter
   lookaround), or `머지` (Korean, with the same negative-lookahead exclusions
   as `merge-menu-review-options-advisory`'s `_KO_MERGE_RE`: `머지된`
   already-merged/triage-label and `머지하지` "do NOT merge" do not count).

A merge keyword in one question does **not** pair with a PR number in an
unrelated question in the same payload — detection is scoped per-question,
not payload-wide, so a multi-question menu with an unrelated merge-adjacent
question elsewhere does not cross-contaminate.

## False-positive boundary (explicit design decisions)

| Text | Fires? | Why |
| ------ | -------- | ----- |
| `"PR #714 merge할까요?"` | Yes | number + EN merge keyword, same question |
| `"이 변경(#714)을 머지할까요?"` | Yes | number + KO merge keyword, same question |
| `"PR #714 리뷰를 진행할까요?"` | **No** | number present, but no merge/squash/머지 keyword anywhere in the question unit — a review-only menu does not depend on merge state |
| `"머지 충돌을 해결해볼까요?"` | **No** | merge keyword present, but no `#\d+` token anywhere — nothing to re-fetch |
| `"#714 이슈를 닫을까요?"` | **No** (in practice) | number present but no merge keyword — a bare issue-close question is not merge-state-contingent |
| Question: `"PR #714에 대해 어떻게 할까요?"`, option: `"Merge"` | Yes | co-occurrence is checked across the **whole per-question text** (question + all its options), not per-line/per-option — this is the realistic shape (number in the question, verb in an option label) |
| Question A has `"#100"` only; Question B (same payload) has `"merge"` only | **No** | scoped per-question — cross-question pairing is explicitly excluded |

The design deliberately does **not** treat the bare literal `PR` as an
independent third keyword (unlike the issue's illustrative "merge"/"PR"/"머지"
list): requiring `#\d+` already implies the number is almost always adjacent
to the word "PR" in natural phrasing, so a separate `PR`-token requirement
would only widen the match surface (e.g. "PR #714의 diff 크기 확인" — not a
merge decision) without adding recall for the actual target scenario
(merge/hold decision menus).

`gh pr view <N>` on a number that is actually an **issue** number (not a PR)
fails with a non-zero exit — this naturally routes through the fail-open path
below rather than needing separate issue-vs-PR disambiguation logic.

## Live re-fetch

For each candidate PR number (deduplicated, capped at 3 per payload):

```bash
gh pr view <N> --json state,mergeStateStatus
```

Run with `cwd` set from the hook payload's `cwd` field (falls back to the
hook process's own cwd when absent) so the query targets the correct
worktree's repo, and a 2-second timeout (worst case 3 candidates × 2s = 6s,
under the 8s manifest timeout).

| Live `state` | Result |
| --------------- | -------- |
| `MERGED` or `CLOSED` | Stale premise — advisory (default) or block (strict) |
| `OPEN` | Premise holds — silent pass |
| `gh` call fails, times out, or returns unparseable JSON | **That PR number is skipped** (fail-open) — cannot determine live state, so neither warn nor block on it |

A payload with 2+ candidate PR numbers where only some are stale still fires
— the message lists only the stale ones.

## What is advised / blocked

| Scenario | Action |
| ---------- | -------- |
| Default mode, ≥1 candidate PR's live state is MERGED/CLOSED | exit 0 + advisory stderr |
| `PRAXIS_PR_STATE_REFETCH_STRICT=1`, ≥1 candidate PR's live state is MERGED/CLOSED | exit 2 (block) |
| Any tool name other than `AskUserQuestion` | silent pass-through |
| No question carries the PR-number + merge-keyword co-occurrence signal | silent pass-through (no `gh` call made — zero subprocess cost) |
| All candidate PRs are still OPEN | silent pass-through |
| `gh` binary missing / call errors / times out / unparseable JSON, for a given PR number | that number is skipped (fail-open); if no other candidate is stale, silent pass-through |
| Malformed / missing payload | silent pass-through (fail-open) |

## Mode and env var behavior

| Env var state | Mode | Exit code on match |
| --------------- | ------ | --------------------- |
| Unset (default) | **Advisory** | 0 + stderr warning |
| `PRAXIS_PR_STATE_REFETCH_STRICT=1` | Strict | 2 (block) |

Default is advisory: the co-occurrence signal is a heuristic (a question can
legitimately mention a PR number and the word "merge" without the menu being
a go/no-go merge decision — e.g. "PR #714 merge 이력을 요약할까요?"), and the
live-state re-fetch, while authoritative once it runs, should not hard-block
until false-positive/false-negative rates are observed in practice. This
mirrors `merge-menu-review-options-advisory` and
`block-manufactured-action-menu`'s advisory-first-then-strict-opt-in
convention for `AskUserQuestion` gates.

## Parsing guarantees (fail-open)

+ Malformed JSON payload → exit 0
+ `tool_name != "AskUserQuestion"` → exit 0
+ `tool_input` absent or not a dict → exit 0
+ `questions` absent or not a list → exit 0 (no candidates)
+ `options` absent or not a list in a question → that question's options
  contribute nothing to its text unit (question text alone is still scanned)
+ No PR-number + merge-keyword co-occurrence in any question → exit 0, zero
  `gh` subprocess calls
+ `gh` binary missing, non-zero exit, timeout, or unparseable JSON for a
  candidate number → that number is dropped, not blocked
+ Any uncaught exception → exit 0 (via the shared `@fail_open` decorator in
  `hooks/_lib/_hook_runtime.py`)

## Relationship to sibling hooks

| Hook | Overlap |
| ------ | --------- |
| `merge-state-claim-gate` | None — that hook gates the **assertion** of completed state in the final message (post-hoc); this hook gates the **premise** of a mid-turn question (pre-hoc) |
| `output-block-falsify-advisory` | Complementary — that hook's static reminder covers the general "may already be resolved" case across many claim types; this hook adds a live, PR-specific re-fetch with an authoritative yes/no answer |
| `pre-merge-approval-gate` | None — different surface (`gh pr merge` Bash command vs. `AskUserQuestion` menu); same underlying pattern (live `gh pr view` re-fetch at a lock boundary) |
| `merge-menu-review-options-advisory` | None — that hook checks whether a merge-decision menu **offers a review lever**; this hook checks whether the menu's **premise about a specific PR is still true**. Both inspect `AskUserQuestion` merge-related text but for orthogonal purposes. Merge-keyword regex is mirrored from this hook (2nd consumer; see impl.py docstring) |

## Tests

```bash
bash tests/hooks/preflight-gate/test_pr_state_refetch_gate.sh
```

Covers: co-occurrence signal true positives (EN `merge`, KO `머지`, number-in-
question + verb-in-option shape), false-positive exclusions (number without
merge keyword, merge keyword without number, cross-question non-pairing),
live re-fetch MERGED → advisory, live re-fetch CLOSED → advisory, live
re-fetch OPEN → silent pass, strict mode → block, `gh` missing → fail-open,
`gh` non-zero exit → fail-open, unparseable `gh` JSON → fail-open, non-
AskUserQuestion tool → silent pass, malformed payload → fail-open, multiple
candidate PR numbers with mixed live states.
