# Stop Verdict-Gap Coexistence Gate

Supported hosts: all

`hooks/completion-verify/verdict-gap-coexistence-gate/impl.py` runs on the
`Stop` event. It scans the **final assistant message** for a GO-verdict
phrase ("보내도 됩니다", "머지 가능", "ready to merge") that coexists with an
unresolved-gap marker ("⚠", "미해소", "TODO", "unverified") **in the same
output**, and emits a stdout `{"systemMessage": ...}` JSON advisory.

## Why this exists

Retrospect-promoted 2026-07-23. In a PR review session, the assistant
self-flagged "⚠️ 미해소 갭 — 데이터 동일성 검증 증거 부재" as a headline in
the SAME response that also asserted "판단: 리뷰어에게 보내도 됩니다" (a GO
verdict). The user corrected it twice before the actual verification (full
template sweep + prod data parity) ran — result: PASS. The conclusion turned
out correct; the defect was the **order**: a verdict was locked before the
verification the response itself said was missing.

tracer root-cause (confidence HIGH): verdict-scope conflation — "procedural
sendability" (CI green, no conflicts) and "semantic soundness" were collapsed
into one GO phrase, and the disclosed gap never got promoted into a
conditional clause on the verdict. Same-output logical-linkage failure, not a
search failure: always-loaded rules (Author-exempt trap, Verification Before
Completion, PR Review Protocol) and 4 memory entries were already present and
did not prevent the repeat.

## Probe-first result (issue's own precondition)

The issue requires running a discriminating probe (`session_search` for 3-5
independent co-occurrence cases) BEFORE building, to avoid a keyword hook on
a single observation. This PR ran that probe over the locally accessible
session history (`mcp__plugin_oh-my-claudecode_t__session_search`, queries:
`머지 가능`, `미해소 갭`, `보내도 됩니다`, `ready to merge`, `문제 없습니다`).
Result: **only the originating incident was found.** The other hits are the
same issue body being re-quoted (issue creation, this task reading it back) —
not independent repeat occurrences. The 3-5 case bar the issue sets is
**not met**.

This is disclosed here as a headline, not silently absorbed: the hook below
is still built, on the judgment that (a) it is advisory-only — it never
blocks, so the false-positive cost of shipping on a single confirmed
incident is bounded — and (b) the accessible `session_search` corpus is a
local, single-machine slice of the actual multi-session history the pattern
could recur across, so "not found in this slice" is weaker evidence of
"does not recur" than a full-corpus negative would be. Flagged explicitly
for whoever reviews this PR to weigh; not a substitute for the issue's own
gate.

## What is detected

**GO-verdict phrases** (issue's trigger list + "all clear" from the issue
title), non-negated, non-quoted (`>` lines skipped):

| Pattern | Example |
| ------- | ------- |
| `보내도 됩니다/됨/되겠` | "판단: 리뷰어에게 보내도 됩니다." |
| `머지 가능` | "머지 가능합니다." |
| `approve 가능` | "approve 가능합니다." |
| `문제 없습니다` / `이상 없습니다` | "문제 없습니다." |
| `ready to merge` (EN) | "This is ready to merge." |
| `all clear` (EN) | "All clear." |

Negation (`지 않`, `안 됩`, `못␣`, `아니` trailing the KR match within 12
chars; `not␣`, `n't␣`, `no␣`, `never␣`, `isn't`, … preceding the EN match
within 24 chars; `␣` marks a literal trailing space) disqualifies the
match — "머지 가능하지 않습니다" and "not ready to merge" are not verdicts.

**Unresolved-gap markers**: `⚠`/`⚠️`, `미해소`, `검증 증거 부재`, bare `갭`,
EN `unverified`, `not verified`, `TODO`, `pending`. A marker immediately
followed by resolution language within 32 chars (`해소되었`, `해소함`,
`해소했`, `해소됨`, `해결되었`, `해결했`, `resolved`, `closed`, `addressed`) is
treated as already-resolved and does not count — e.g. "갭은 이미
해소되었습니다" or "The TODO items were addressed before this commit."

**Conditional-linkage suppression**: if the output already links the gap to
the verdict conditionally (`갭 해소 시`, `해소되면`, `조건부로`, `once the gap
is resolved`, `after resolving the gap`) — i.e. the exact remedy this
advisory recommends — the hook stays silent. Firing on an already-applied
remedy would be pure noise.

## Response

Advisory by default — exit 0 + stdout `{"systemMessage": ...}` JSON.
`PRAXIS_VERDICT_GAP_STRICT=1` escalates to `{"decision": "block", "reason":
...}` (convention-consistency with sibling `completion-verify` hooks; not
requested by the issue body, which specifies advisory only).
`PRAXIS_VERDICT_GAP_BYPASS=1` silences the hook entirely.

```text
[verdict-gap-coexistence-gate] final message asserts a GO verdict (보내도 됩니다/머지 가능/ready to merge/all clear/...) while ALSO disclosing an unresolved gap (⚠/미해소/검증 증거 부재/unverified/TODO/pending) in the same output.
[verdict-gap-coexistence-gate] Rule: a disclosed gap is a condition on the verdict, not an aside — rewrite the verdict as conditional on the gap ("갭 X 해소 시 보내도 됨") or resolve the gap FIRST, before locking the verdict (issue #845: ...).
[verdict-gap-coexistence-gate] bypass: PRAXIS_VERDICT_GAP_BYPASS=1
```

## Relationship to sibling hooks

| Hook | Scope | Overlap |
| ---- | ----- | ------- |
| `completion-signal-gate` | Stop advisory, generic completion vocabulary vs. any Bash/Read evidence | None — that hook checks for evidence anywhere in the turn; this hook checks for a SECOND signal (a self-disclosed gap) coexisting in the SAME output, regardless of tool calls |
| `output-block-falsify-advisory` / `pre-output-falsification-gate` | PreToolUse advisory on `(Recommended)` proposals before an external write | Different event (PreToolUse cannot see in-flight assistant text — issue #487 finding A3); this is the Stop-hook complement for the same conflation failure, scoped narrowly to GO-verdict + gap-marker co-occurrence |
| `merge-state-claim-gate` / `pr-claim-mutation-gate` | Stop advisory on completed-state / processed assertions | Different claim shape — those gate "X happened" claims; this gates "X is fine to ship" verdicts that self-contradict a disclosed gap |

## Parsing guarantees (fail-open)

Returns exit 0 on every infrastructure error — malformed stdin, missing/
unreadable transcript, empty transcript, no assistant text in the current
turn, and any uncaught exception (via the shared `@fail_open` decorator in
`hooks/_lib/_hook_runtime.py`). `stop_hook_active: true` short-circuits to
exit 0 (re-entry loop guard). It never blocks in the default (advisory) mode.

## Tests

```bash
bash tests/hooks/completion-verify/test_verdict_gap_coexistence_gate.sh
```

19 cases: the motivating incident verbatim (advisory), EN/KR GO+gap
co-occurrence (4 variants), GO verdict alone with no gap marker (silent),
gap marker alone with no verdict (silent), negated verdict EN/KR (silent, 2
cases), a gap marker immediately followed by resolution language EN/KR
(silent — already-resolved guard, 2 cases), conditional linkage already
present EN/KR (silent — remedy-already-applied guard, 2 cases), a quoted
line reporting a past mistake rather than asserting one now (silent),
neutral message with neither category (silent), strict mode (`decision:
block`), bypass env (silent), `stop_hook_active` loop guard (silent),
missing transcript (fail-open).
