---
name: codex-review-wrap
description: >
  Worktree-aware wrapper for /codex:review. Forces explicit worktree selection
  when several worktrees are active, preventing silent cwd mismatch, then
  gates every returned finding: premise verification before fact-modifying
  edits, flip detection halting A→B→A oscillation, sibling cross-checks for a
  port or parallel hotfix, a diminishing-returns advisory
  (PRAXIS_DIMINISHING_RETURNS_N), and a per-finding approval ask. It also
  reaps leaked openai-codex brokers (PRAXIS_CODEX_REAP=1).
when_to_use: >
  Triggers on "codex review", "review codex", "safe review",
  "/codex-review-wrap", "premise verification", "flip detection",
  "sibling defect", "sibling cross-check", "diminishing returns",
  "broker reap", "finding approval", "적용 승인".
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash(git worktree list *)
  - Bash(git diff *)
  - Bash(gh pr view *)
  - Bash(ps *)
verified-against-runtime: true
runtime-verified-at: 2026-08-15
runtime-verified-note: "Measured against codex@openai-codex 1.0.6 and the live AskUserQuestion runtime; latest measurement 2026-08-15 (Step 4b/liveness source pins plus live review round-trips from two worktrees). The full dated log lives in references/verification-log.md."
---

# codex-review-wrap

## Overview

`/codex:review` selects the working tree based on cwd. When multiple worktrees
are active — the common case mid-session after a merge or context switch — cwd
drifts away from the intended target without warning.

This wrapper intercepts before Codex runs:

1. Lists all active worktrees via `git worktree list`
2. If **≥ 2 worktrees** are active → `AskUserQuestion` forces explicit selection
3. If **exactly 1** → proceeds automatically (same as current `/codex:review` behaviour)
4. Delegates to `/codex:review` with the confirmed worktree as cwd

After Codex returns, a second responsibility activates: every fact-modifying
finding must pass an independent premise check before it becomes an edit, and
the wrapper maintains a session ledger that halts same-session A→B→A flips.
When the PR is a port / parallel hotfix / A/B implementation of logic in a
sibling PR or repo, Step 5d additionally cross-checks each verified finding
against the sibling and records the result. Step 5f tracks how many rounds
have touched each `{file}:{region}` pair and emits a one-time non-blocking
advisory when the count exceeds the configured threshold (default: 4 rounds,
env var `PRAXIS_DIMINISHING_RETURNS_N`). Step 5i then puts every finding
that survived 5b and 5c — fact-modifying, structural, or stylistic — in
front of the user via `AskUserQuestion` before any edit lands: the agent's
verdict is a recommendation, the user's answer is the decision.
See **Step 5** for the full gate.

A third responsibility runs at phase end: **Step 6** reaps openai-codex
app-server brokers that outlived their owning session (a process leak that
spikes `kernel_task` once accumulated RSS crosses the macOS compressor
threshold). See **Step 6** for the reaper and its safety gate.

**Reference map:**

- Step 4 runbook (4a resolve, 4b run + liveness): [`references/step4-run-review.md`](references/step4-run-review.md)
- Step 5 verification gates (5a/5b/5c/5g): [`references/step5-premise-verification.md`](references/step5-premise-verification.md)
- Step 5 cross-checks and counters (5d/5f/5h): [`references/step5-siblings-and-regions.md`](references/step5-siblings-and-regions.md)
- Step 5 decisions and round boundary (5e/5i/5j): [`references/step5-approval-and-rounds.md`](references/step5-approval-and-rounds.md)
- Error-handling table + limitations: [`references/appendices.md`](references/appendices.md)
- Worked end-to-end example: [`references/example-flow.md`](references/example-flow.md)
- Runtime verification log: [`references/verification-log.md`](references/verification-log.md)

## Invocation Model

**Cardinality**: This skill handles exactly **one PR per invocation**. For N PRs, invoke the skill N times sequentially. Batch for-loops are **not supported** — they collapse Step 5c per-round ledger emission across multiple PRs and break flip-detection guarantees.

One invocation may still run **N rounds** against that single PR: the axis the batch prohibition guards is PR cardinality, while Step 5j loops on round cardinality, and every one of its iterations passes through an explicit user decision.

## When to Use

- Before calling `/codex:review` from any multi-worktree project
- When the session cwd differs from the worktree you just finished working in

## Inputs

```
/codex-review-wrap
/codex-review-wrap --model opus
```

Optional `--model` is forwarded to `/codex:review` unchanged.

## Process

### Step 1: Enumerate Active Worktrees

```bash
git worktree list --porcelain
```

Parse output into a list of `{path, branch, HEAD, detached}` entries.
Filter out entries with the explicit `bare` marker — they have no working tree.
Keep detached worktrees (no `branch` line but no `bare` marker) as valid review targets.

Expected output shape per entry:
```
worktree /path/to/repo
HEAD <sha>
branch refs/heads/<branch-name>

worktree /path/to/repo-wt/feature-xyz
HEAD <sha>
branch refs/heads/feature-xyz

worktree /path/to/repo-wt/detached-xyz
HEAD <sha>
detached
```

### Step 2: Disambiguation Gate

**Case A — exactly 1 non-bare worktree:**

Skip selection. Proceed directly to Step 3 using cwd.

**Case B — 2 or more non-bare worktrees:**

Call `AskUserQuestion` with at most **3** worktree options + `"취소"` to
respect the `AskUserQuestion.options` `maxItems: 4` runtime cap (see
`RUNTIME_CONSTRAINTS.md`). When more than 3 worktrees are active, rank
by recency (most recent HEAD commit time first) and surface the top 3;
the runtime's automatic "Other" slot lets the user type any worktree
path not in the list.

```
title: "어느 worktree 를 review 할까요?"
question: "현재 활성 worktrees:\n{numbered list of ALL worktrees}\n\n번호를 선택하거나 'Other' 에 경로를 직접 입력하세요."
options: [{path}: ({branch}) for top 3 most-recently-updated worktrees] + ["취소"]
```

The full worktree list still appears in the `question` body so the user
can read every path even when only the top 3 are surfaced as options.
If the user picks `"Other"` and types a path, validate it against the
full `git worktree list` output before proceeding.

Wait for user response. If `"취소"` or no selection → abort with message:
"Review 취소됨. 대상을 선택하지 않았습니다."

### Step 3: Confirm Selected Target

Show a one-line summary before delegating:

```
Review target: {selected_path} (branch: {branch})
```

If the selected path differs from cwd, note it explicitly:
```
⚠ cwd ({cwd}) ≠ review target ({selected_path}) — codex:review 를 선택된 경로에서 실행합니다.
```

### Step 4: Run codex-companion against the selected worktree

Before delegating to codex-companion, verify the PR is not already closed. Using the branch resolved in Steps 1–2:

```bash
gh pr view "{branch}" --json state --jq '.state' 2>/dev/null
```

- If the command exits non-zero or returns empty (no PR exists yet): continue — pre-PR review is a valid use case.
- If the returned state is `"CLOSED"` or `"MERGED"`: abort immediately:

```
ABORT: "PR is {state} — review aborted. Re-open or target a different PR."
```

**MUST NOT call `Skill("codex:review")`.** `/codex:review` declares
`disable-model-invocation: true`, so the Skill tool always returns the
following error and the call wastes a turn every time:

```
Skill codex:review cannot be used with Skill tool due to disable-model-invocation
```

This is a constant property of `/codex:review` — not session-dependent,
not retry-able, not environment-gated. Do **not** probe it as a pre-check;
do **not** attempt it as a "primary path before fallback"; do **not**
re-attempt it on a later round in the same session. Route straight to the
companion script in 4a/4b on every invocation, including the first.

The only `Skill(...)` call legitimately reachable from Step 4 is the
`oh-my-claudecode:code-reviewer` fallback in 4a — and only when the
codex-companion.mjs path does not resolve.

#### 4a. Resolve the codex-companion.mjs path — summary

Read the install path from the canonical `installed_plugins.json`; when it
does not resolve, offer the `oh-my-claudecode:code-reviewer` / `Manual` /
`Cancel` alternatives via `AskUserQuestion`. Either way, record a
`review-path:` ledger row on every first round of a target — a 5j re-entry
reads that row instead of re-resolving or re-asking. Full procedure:
[`references/step4-run-review.md`](references/step4-run-review.md).

#### 4b. Run the review — summary

Always run the companion via `Bash(..., run_in_background: true)` — a
foreground tool timeout kills a review mid-run and the round then looks
clean while having verified nothing. Return the script's stdout verbatim.
Judge liveness on `ps` + log mtime, never on `status` or `elapsed` (both
report a dead job as a healthy one). A completed background round re-enters
Step 5 from the top; findings are never applied from the completion
notification alone. Full procedure — measured against the companion version
pinned there:
[`references/step4-run-review.md`](references/step4-run-review.md).

### Step 5: Apply Findings — Premise Verification Gate

Codex review output is advisory, not authoritative. Findings whose rationale
depends on assumed facts (table contents, column names, CLI flag shapes,
filter semantics) must be verified against the actual system before any
edit is applied. Skipping this gate is the cause of A→B→A flip oscillation
across consecutive Codex rounds.

This step runs once Codex has returned its findings and the agent is about
to translate them into edits. It applies to every round in the same
session, not just the first. Terminology used below:

- **round** — one invocation of Codex review (Step 4 produces one round of findings)
- **session** — the assistant's working-memory lifetime; the Step 5c ledger lives here

##### Execution order

Sub-sections below are numbered for cross-reference, not execution order.
The execution order each round is:

0. **Interactivity check** — if the run cannot reach a user
   (`claude -p`, background worker, any context where `AskUserQuestion` has
   no recipient), skip every question step (5d-i and 5i) and take the
   non-interactive path in *Error Handling*: classify and verify findings
   (5a–5c) but apply nothing, deferring the survivors. Doing this first is
   what keeps the unconditional 5d-i question from stalling the round before
   classification happens.
1. **`round-started:` ledger row** — write it unconditionally, before any
   finding is known, so a round that ends up empty still advances the
   round number (5c → *Round number*).
2. **5f counter update + advisory check** — increment `rounds_per_region:`
   ledger for each region touched; emit the diminishing-returns advisory if
   `cumulative = threshold + 1`.
3. **5d-i sibling-identification question** — `AskUserQuestion` to confirm
   whether the PR is a port / parallel hotfix / A/B implementation
   (interactive runs only, per step 0).
4. **5a classify findings** — fact-modifying vs structural vs stylistic.
5. **5b verify premises** — falsify each fact-modifying finding's premise
   before applying.
6. **5c flip detection during apply** — scan ledger for `applied:` /
   `rejected:` collisions before each edit.
7. **5g critic pre-lock probe check** — before any critic finding that
   contains a negative claim is surfaced to the user, verify the claim
   with a live probe and cite it inline. Runs **before** 5i, because 5i
   surfaces every finding — including structural and stylistic ones,
   whose 5i question body is allowed to carry `Probe: n/a`.
8. **5i per-finding user approval gate** — ask the user, in batches of 4,
   whether to apply each finding; edits are applied only for `적용`
   answers. Runs **after** classification/verification/flip-scan/probe
   check (so the question carries evidence) and **before** the first edit
   of the round.
9. **5d-ii / 5d-iii sibling cross-check + propose** — only when 5d-i
   identified a sibling.
10. **5e commit-message trailer** — `Premise-Verified:` trailer on the
    committed fact-modifying edit.
11. **5h parent-truncates-child SoT audit** — after all approved findings
    are applied, scan the parent doc for inline transcriptions of sibling
    SoT enumerations and emit synthesized findings for any truncation
    detected. Synthesized findings re-enter 5g and 5i as their own
    approval batch before their edits are applied.
12. **[round boundary] 5j round-continuation gate** — when at least one
    edit was applied this round and the round is interactive, ask via
    `AskUserQuestion` whether to run another Codex round. On `continue`,
    re-enter **Step 4** for round N+1; otherwise proceed to Step 6.
    Re-entry skips Steps 1–3 and item 3 (5d-i) above.

#### Where each sub-step lives

The full sub-step procedures live in three reference files — open the file at
the point of use. Every normative rule of 5a–5j is stated there, not here;
the lines below only say what each sub-step guarantees.

[`references/step5-premise-verification.md`](references/step5-premise-verification.md)
— from findings to verified findings:

- **5a. Classify each finding** — fact-modifying vs structural vs stylistic;
  when in doubt, treat as fact-modifying.
- **5b. Verify the premise** — one independent falsifying check per
  fact-modifying finding before it may become an edit, with the
  verification-method table per finding type.
- **5c. Flip detection** — the session ledger's record shapes (enumerated
  there, not here — 5c is the SoT), round number derivation, and the halt
  rule for A→B→A flips and re-proposals of rejected or user-declined
  findings.
- **5g. Critic pre-lock probe check** — a negative claim ("X does not
  exist", "X is unused", …) needs a live `Probe:` citation at the assertion
  site before it may be surfaced; includes the critic prompt template block.

[`references/step5-siblings-and-regions.md`](references/step5-siblings-and-regions.md)
— cross-checks and counters:

- **5d. Sibling cross-check (5d-i/ii/iii)** — identify port / parallel
  hotfix / A/B siblings, replay each falsifiable 5b test against the
  sibling, and propose same-defect fixes under their own separate approval.
- **5f. Diminishing-returns advisory** — the `rounds_per_region:` counter
  and the once-per-region advisory at `cumulative = threshold + 1`
  (`PRAXIS_DIMINISHING_RETURNS_N`, default 4).
- **5h. Parent-truncates-child SoT audit** — sweep the parent doc for
  truncated inline transcriptions of sibling SoT enumerations; synthesized
  findings re-enter 5g and 5i as their own approval batch.

[`references/step5-approval-and-rounds.md`](references/step5-approval-and-rounds.md)
— decisions and the round boundary:

- **5e. Commit trailer** — `Premise-Verified:` trailer on every committed
  fact-modifying edit; the single rule for trailer scope.
- **5i. Per-finding user approval gate** — every finding that survives 5b
  and 5c is asked via `AskUserQuestion` (batches of at most 4;
  적용 / 미적용 / 후속이슈); there is no auto-apply path.
- **5j. Round-continuation gate** — fire conditions (at least one applied
  edit, edits inside the next round's review target, interactive), the
  decision table, re-entry rules back into Step 4, and the no-round-cap
  policy.

### Step 6: Reap leaked codex brokers (phase end)

The openai-codex plugin starts a per-session app-server broker that is
reparented to launchd (`ppid=1`) and is **not** killed when its owning Claude
session exits. Across multi-day uptime these accumulate; once cumulative RSS
crosses the macOS memory-compressor threshold, each idle broker's periodic
wakeup drives compress/decompress churn that surfaces as `kernel_task` system
CPU — a non-linear spike, not a linear one.

Run the co-located reaper at the end of every review invocation — **macOS
only**: the leak is a launchd/`/var/folders` mechanism and the script uses BSD
`stat`, so on other platforms skip this step entirely. It is the
single source of truth for safe reaping, shared with the launchd job (see
`LAUNCHD.md`). Resolve it via the plugin root, mirroring the strike-counter
convention used by the `strike` / `reset-strikes` skills:

**Default — GC only (zero risk).** Removes the stale tmp sessionDirs of brokers
whose pid is already dead. Never signals a running process.

```bash
"${CLAUDE_PLUGIN_ROOT}/skills/codex-review-wrap/codex-broker-reaper.sh" --gc
```

**Opt-in — also reap running idle brokers.** When `PRAXIS_CODEX_REAP=1` is set,
additionally kill running brokers whose `broker.log` has been idle longer than
`--max-age` minutes (default 30). A broker actively serving a review has a
freshly-touched log and is skipped by the idle gate, so this stays safe to run
from inside a session even while sibling sessions hold their own brokers.

```bash
if [ "${PRAXIS_CODEX_REAP:-0}" = "1" ]; then
  "${CLAUDE_PLUGIN_ROOT}/skills/codex-review-wrap/codex-broker-reaper.sh" --reap --max-age 30
fi
```

**Never** broad-kill (`pkill -f codex`, `pkill node`): sibling Claude sessions
share the same broker process class, and an unscoped kill aborts their
in-flight reviews. The reaper's per-broker idle gate is the only sanctioned
path. The heavy, session-independent reclaim of running orphans belongs to the
launchd job (`LAUNCHD.md`), not to a per-review phase end — phase end only
keeps the count below the compressor threshold.

## Error Handling and Limitations

The full error-handling table — per-situation actions for every step, several
of which change control flow (abort, skip, halt) rather than wording — and
the limitations list live in
[`references/appendices.md`](references/appendices.md). Consult the table the
moment a step hits an unexpected state.

## Example Flow

A worked end-to-end transcript — worktree selection (Steps 1–3), the review
run (Step 4), every Step 5 gate firing on concrete findings across multiple
rounds, flip halts, and the 5j round loop — lives in
[`references/example-flow.md`](references/example-flow.md).
