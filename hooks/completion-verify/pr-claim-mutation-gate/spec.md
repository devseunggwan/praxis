# Stop PR-Claim Mutation Gate

Supported hosts: all

`hooks/completion-verify/pr-claim-mutation-gate/impl.py` runs on the `Stop`
event. It scans the **final assistant message** for a PR-surface completion
claim ("리뷰 코멘트를 처리했습니다", "Fixed the review comments") and, when the
**current turn** contains no *successful* PR-surface mutation tool call,
blocks the stop with a `{"decision": "block", ...}` JSON.

## Why this exists

2026-07-27 session retrospect finding #1 (HIGH, repeat 3x). CodeRabbit raised
3 review findings on an open PR. The assistant applied the fixes to the LOCAL
worktree only and reported to the user that the findings were handled. At
that moment `git push` / `gh pr comment` / a review-comment API call /
thread-resolve were all **zero** — the user caught it by asking "처리한게
맞는지?" (did you actually process it?).

This is not an evidence-quality problem — it is a **completion-claim
violation**. The claim's surface is the PR (the comment thread, the pushed
commit), but the evidence backing the claim was a local edit, a different
surface entirely.

`completion-signal-gate` (#392) already exists but does not catch this case:

- It is **advisory-only**, and against this session's fire ledger the ADVISE
  tier fired 42 times with **zero** behavior change (retrospect finding #6).
- Its evidence check is **any** `Bash`/`Read` tool_use anywhere in the turn —
  a `git status` or `cat file` call is enough to suppress it, even though
  neither touches the PR at all.
- It does not check whether the claimed surface (the PR) actually saw a
  mutation.

This hook is narrower and stricter: the claim must combine a PR/review
SUBJECT with a processed-state CLAIM verb, and the evidence gate requires a
tool call that specifically mutates the PR surface — not any tool call.

## What is detected

A claim requires, **on the same line**:

- a **subject** token — `PR`, `pull request`, `리뷰 코멘트`, `리뷰 댓글`,
  `코멘트`, `댓글`, `피드백`, `지적사항`, `review(s)`, `comment(s)`,
  `feedback`, `finding(s)`, or a reviewer-bot name (`coderabbit`, `bugbot`,
  `reviewdog`, `codex review`); and
- a **processed-state claim** verb — `처리했/됐/함`, `반영했/됐/함`,
  `적용했/됐/함`, `답변했/됨`, `댓글 달았/남겼`, or EN `resolved`, `applied`,
  `handled`, `addressed`, `fixed`, `replied`.

Past/perfective forms only — future intent (`처리하겠습니다`, "I'll fix the
comments") does not match either verb set.

**Suppressors (any of these on the same line skips the claim):**

| Suppressor | Example | Why |
| ---------- | ------- | --- |
| Negation | `아직 처리하지 못했습니다`, `have not addressed ... yet` | reporting incompletion, not claiming done |
| Hedge | `처리한 것 같습니다`, `I might have addressed ...` | disclosed uncertainty, not a completion claim |
| Question | `처리했나요?`, `Did I fix the comments?` | the user's/assistant's own question, not an assertion |
| Quoted line (`>` prefix) | `> 처리했다고 보고했다` | reporting that a claim was made, not making one |

**Known accepted gap — double negation.** `처리하지 않은 게 아닙니다`
("it is not the case that it was not processed" — i.e. affirmative) still
matches `_NEGATION_RE`'s inner `않` token and is silently suppressed even
though the sentence is semantically a completion claim. Reliable
double-negation parsing is out of scope for a line-level regex gate; this is
a documented trade-off in the same style as `merge-state-claim-gate`'s
`without`/`fail` notes.

## Mutation evidence (current turn only)

Evidence is scoped to the **current turn** (not a wider event window): the
claim is specifically about what *this turn* did to the PR, and the
motivating incident had zero mutations of any kind, anywhere. A tool call in
a previous turn does not back a claim made in this one.

A turn clears the gate if any assistant `tool_use` matches:

| Form | Pattern |
| ---- | ------- |
| Push | `git push` |
| PR comment | `gh pr comment ...` |
| PR review | `gh pr review ...` |
| Write-method `gh api` | `gh api ... --method POST\|PATCH\|PUT\|DELETE` (or `-X ...`) **on a `comments`/`reviews`/`threads` endpoint** |
| GraphQL thread resolve | a command containing `resolveReviewThread` |
| GitHub MCP mutation | `mcp__github__*` tool whose name carries a write verb (`add`/`create`/`submit`/`update`/`edit`/`delete`/`reply`/`resolve`/`dismiss`/`merge`); a `get`/`list`/`search`/`read` prefix always loses |

Three things that look like mutations but are not, and therefore do not clear
the gate:

- a call whose `tool_result` is `is_error` — a rejected push or a 422 from
  `gh pr comment` leaves the PR exactly as it was (correlated by tool_use
  `id` ↔ tool_result `tool_use_id`, mirroring `pr-report-destination-gate`);
- a `--dry-run` / `--help` / `man` rehearsal;
- command text inside `echo` / `printf` / `cat`.

A write-method `gh api` on a non-review endpoint (`.../labels`,
`.../milestones`) mutates GitHub but not the surface the claim is about, so
the endpoint segment is required rather than decorative.

A **read-only** `gh api .../comments` call (no explicit write method — the
default HTTP verb is GET) does **not** clear the gate: listing comments is
not resolving them. `Read`/`Write`/generic `Bash` tool calls with no
PR-surface command also do not clear it — this is the key delta from
`completion-signal-gate`, which treats any `Bash`/`Read` call as sufficient
evidence.

## Response

**Blocks by default** — exit 0 + stdout `{"decision": "block", "reason":
...}`, which re-prompts the model to push/comment/resolve before stopping.
This is what issue #868 asks for verbatim ("부재 시 차단 ... advisory 티어의
실측 효과가 0 이므로 advisory 로는 같은 실패가 반복된다"), and it mirrors
`negative-existence-verdict-gate`, which also blocks by default with an
advisory-demote escape hatch. `PRAXIS_PR_CLAIM_ADVISORY=1` demotes it to a
`{"systemMessage": ...}` advisory; `PRAXIS_PR_CLAIM_BYPASS=1` silences it
entirely.

```text
[pr-claim-mutation-gate] final message claims a PR review comment / feedback was processed (처리했/반영했/resolved/applied/handled/fixed the comments) but the CURRENT TURN contains no PR-surface mutation (`git push`, `gh pr comment`, `gh pr review`, a write-method `gh api` call, a GraphQL thread resolve, or a GitHub MCP comment/review tool).
[pr-claim-mutation-gate] Rule: the claim's surface is the PR — a local edit does not back it. Push and post/resolve on the PR BEFORE reporting the findings as handled (issue #868: 3 CodeRabbit findings were fixed locally only; push/comment/resolve were all zero).
[pr-claim-mutation-gate] bypass: PRAXIS_PR_CLAIM_BYPASS=1
```

## Relationship to sibling hooks

| Hook | Scope | Overlap |
| ---- | ----- | ------- |
| `completion-signal-gate` | Stop advisory, generic completion vocabulary, any Bash/Read call clears it | Complementary — broader phrase net, much weaker evidence gate |
| `merge-state-claim-gate` | Stop advisory, merge/PR/issue/worktree **state** assertions (`merged`, `created`, `closed`), evidence = any fresh `gh pr\|issue` query in the recent 80-event window | None — that hook gates state assertions about the PR's lifecycle; this one gates "I processed the review feedback" claims and requires an actual **mutation**, not just a query, in the **current turn** only |
| `runtime-state-claim-gate` | Stop advisory, runtime/execution state claims, evidence = any probe tool in current turn | Structural sibling (same turn-scoped-evidence shape); different claim domain |

## Parsing guarantees (fail-open)

Returns exit 0 on every infrastructure error — malformed stdin, missing/
unreadable transcript, empty transcript, no assistant text in the current
turn, and any uncaught exception (via the shared `@fail_open` decorator in
`hooks/_lib/_hook_runtime.py`). `stop_hook_active: true` short-circuits to
exit 0 (re-entry loop guard). It never blocks a normal Stop in the default
(advisory) mode.

## Tests

```bash
bash tests/hooks/completion-verify/test_pr_claim_mutation_gate.sh
```

27 cases: the motivating incident verbatim (KR, zero mutation → advisory),
4 EN/KR claim variants without mutation (advisory), claim cleared by `git
push` / `gh pr comment` / `gh pr review` / write-method `gh api` / GitHub MCP
comment tool (silent, 5 cases), claim NOT cleared by a read-only `gh api`
listing call (advisory — the incident-shape guard), `Write` tool_use does not
count as PR mutation (advisory), mutation in the **previous** turn does not
back a claim in this one (advisory — turn-scoped evidence), no PR subject
(silent — out of this hook's scope), subject present but no claim verb
(silent), negated claim EN/KR (silent), the documented double-negation gap
(silent, accepted trade-off), hedged EN/KR forms (silent), question-form
EN/KR (silent), a quoted line reporting a claim rather than making one
(silent), strict mode (`decision: block`), bypass env (silent),
`stop_hook_active` loop guard (silent), missing transcript (fail-open).
