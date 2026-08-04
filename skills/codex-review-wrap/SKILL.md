---
name: codex-review-wrap
description: >
  Worktree-aware wrapper for /codex:review. When multiple active worktrees exist,
  forces explicit selection before delegating to Codex. Prevents silent cwd mismatch
  between the current shell location and the intended review target. Also enforces
  a premise verification gate before applying fact-modifying findings, with flip
  detection that halts A→B→A oscillation across rounds within the same session.
  When the PR is a port / parallel hotfix / A/B implementation, Step 5d cross-checks
  every fact-modifying finding against the sibling implementation and records results
  in the session ledger. Step 5f tracks rounds-per-region and surfaces a non-blocking
  diminishing-returns advisory when the same file:region accumulates more than N rounds
  (default 4, configurable via PRAXIS_DIMINISHING_RETURNS_N).
  Step 5i asks the user, per finding, whether to apply it — no finding is edited
  on the agent's judgement alone.
  At phase end it reaps leaked openai-codex app-server brokers via a co-located
  idle-gated reaper (GC by default; opt-in running-broker kill via
  PRAXIS_CODEX_REAP=1) to prevent the kernel_task memory-pressure spike caused
  by brokers that outlive their owning session.
  Triggers on "codex review", "review codex", "safe review", "/codex-review-wrap",
  "premise verification", "flip detection", "sibling defect", "sibling cross-check",
  "diminishing returns", "broker reap", "finding approval", "적용 승인".
verified-against-runtime: true
runtime-verified-at: 2026-08-04
runtime-verified-note: "codex-companion 1.0.4 — ARGUMENTS rejected for non-flag string; AskUserQuestion maxItems:4 blocks worktree list >3 items; Skill() cannot delegate to disable-model-invocation skill. Step 4 hardened to a MUST NOT directive in issue #237 (2026-05-16) — directive-only change, no new runtime claim. Step 5f (PR #329) adds procedural prose only (rounds_per_region ledger + advisory text); no runtime hook code changed — existing verification evidence remains valid. Step 6 (issue #683) adds codex-broker-reaper.sh; its reap and GC decisions are backed by re-runnable behavior tests in tests/test_codex_broker_reaper.sh (Gate 3 owner-death oracle over nine equally-idle brokers, Gate 4 GC sessionDir ownership) — run them for current evidence. The prose record this field used to carry (a 2026-06-18 synthetic-broker observation) was a one-time manual run, not re-executable, and did not prevent the #919 kill of live sessions; issue #921 replaced it with the tests. Step 5i (issue #861) verified 2026-07-26 against the live AskUserQuestion runtime — 3-question and 4-question calls, each question carrying 3 explicit options, round-tripped and returned every answer keyed by question text; the runtime appended its own Other slot, confirming both the 4-question batch size and the 적용/미적용/후속이슈 shape against the 4-option cap (RUNTIME_CONSTRAINTS.md §1a). Step 5j (issue #945) verified 2026-08-04: the gate's actual option labels (추가 라운드 실행 / 현재 라운드로 충분 — Step 6 으로 진행) round-tripped through the live AskUserQuestion runtime and the answer came back keyed by question text, and the call passed the block-ask-end-option hook (labels and question body checked against all 30 markers in that hook's spec). Fire condition (b) measured the same day against codex@openai-codex 1.0.6 by executing resolveReviewTarget from scripts/lib/git.mjs: a clean worktree resolves to mode=branch with baseRef=main, and a dirty one to mode=working-tree, whose context carries staged, unstaged, and untracked diffs — so an applied edit is in the review target either way under --scope auto. The fire path was then traced end-to-end the same day by running this skill on this very change: round 1 applied 6 findings, 5j fired, the answer was 추가 라운드 실행, Step 4 was re-entered with Steps 1-3 and 5d-i skipped, round 2 returned 4 further findings against round 1's own edits, and the second gate returned Step 6 으로 진행 — so both the continue and stop branches, and the sibling-id reuse, are observed rather than asserted. The skip branches (0 applied) remain documented as a decision table rather than observed live: 5j is prose the agent follows, not hook code, so non-firing has no independent oracle."
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

#### 4a. Resolve the codex-companion.mjs path

Read the install path from the canonical Claude Code plugin manifest:

```bash
manifest="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/installed_plugins.json"
install_path=$(jq -r '.plugins["codex@openai-codex"][0].installPath // empty' "$manifest")
companion="$install_path/scripts/codex-companion.mjs"
```

If `$companion` is empty or the file does not exist:

1. Output: `"⚠ codex-companion.mjs not found — openai-codex plugin may not be installed."`
2. Offer alternatives via `AskUserQuestion`:
   - **`oh-my-claudecode:code-reviewer`** — Claude-based code review (equivalent quality)
   - **`Manual`** — output the diff for direct inspection; skip automated review
   - **`Cancel`** — abort the review
3. Act on the selection:
   - `oh-my-claudecode:code-reviewer` → `Skill("oh-my-claudecode:code-reviewer")` with cwd set to `{selected_path}`
   - `Manual` → run `git diff origin/<base-branch>..HEAD` in `{selected_path}` and exit
   - `Cancel` → abort silently with one-line message

**Record the resolved path in the ledger, on every first round of a
target** — including the ordinary case where `codex-companion.mjs`
resolved and no question was asked:

```text
review-path: target={worktree-path}#{branch} | round={N} | path={codex-companion | code-reviewer | manual}
```

A round re-entered by 5j reads this row instead of re-resolving or
re-asking (5j → *Re-entry*, item 4), so without the row the re-entry
instruction has nothing to read. Match on `target=`, same as `sibling-id:`.

The script derives its own ROOT_DIR via `import.meta.url`, so passing the
absolute script path to `node` is sufficient — `CLAUDE_PLUGIN_ROOT` does
not need to be set.

#### 4b. Run the review

Change working directory to the selected worktree, then invoke the
companion. `{{ARGUMENTS}}` passes any flags (e.g. `--model opus`,
`--wait`, `--background`) through unchanged. One exception: a round
re-entered from 5j drops `--background`, because a re-backgrounded round
breaks that gate's synchronous loop.

```bash
cd {selected_path}
node "{resolved_companion_path}" review "{{ARGUMENTS}}"
```

Return the script's stdout **verbatim** — do not paraphrase, summarize, or
add commentary. This matches `/codex:review`'s contract.

If `{{ARGUMENTS}}` includes `--background`, run via `Bash(..., run_in_background: true)`
and tell the user: "Codex review started in the background. Check `/codex:status` for progress."
Backgrounding defers Step 5, it does not skip it. Which path the findings
take depends on where the session is when the run completes:

- **User is back in an interactive foreground turn** — collect the findings
  and enter Step 5 from the top (interactivity check → 5f → …); the
  interactivity check passes and 5i can ask.
- **No user is reachable** (unattended worker, `-p` run, session already
  ended) — the interactivity check fails and the findings take the
  non-interactive path: verified, applied to nothing, deferred.

Either way, never apply a background review's findings from the completion
notification alone.

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

#### 5a. Classify each finding

| Type | Examples | Premise check required |
| ------ | ---------- | ------------------------ |
| **Fact-modifying** | WHERE / filter logic, catalog / schema / table / column names, CLI flag or option references, API endpoint / signature, version or SDK identifiers, **string literals used as identifiers** (provider keys, env names, lookup tokens) | **YES** |
| **Structural** | Code organization, function decomposition, file layout, renames of code symbols only (variables, functions, types) when no string literal is touched | No |
| **Stylistic** | Comments, formatting, lint-style suggestions | No |

A finding is **fact-modifying** if accepting it would change a value the
running system reads or matches against (filter predicate, identifier
lookup, CLI invocation, network call, string-keyed lookup). Anything
else is structural or stylistic. When in doubt, treat the finding as
fact-modifying — false positives cost one extra verification call;
false negatives cause the exact flip-oscillation this gate prevents.

#### 5b. Verify the premise before applying fact-modifying findings

For each fact-modifying finding, run one independent check that would
**falsify** the underlying premise. Capture the verification output and
keep it for 5d. If the verification disproves the premise, do NOT apply
the finding — reply to Codex (or surface to the user) with the result.

##### Verification methods by finding type

This table is the canonical reference for the AC #3 documentation
requirement; lift it when authoring related skills.

| Finding type | Verification method |
| -------------- | --------------------- |
| WHERE clause / filter logic | Run the query with and without the filter; compare row counts against the rationale |
| Catalog / schema / table name | `SHOW CATALOGS` / `SHOW SCHEMAS` / `SHOW TABLES` (or equivalent MCP / Trino / live-env query) |
| Column name | `DESCRIBE <table>` against the live env |
| CLI flag / option | `<binary> --help` and a real dry-run invocation — naming-pattern intuition is **not** verification |
| API endpoint / signature | Hit the live endpoint, read the official docs, or grep the SDK source |
| Version / SDK identifier | Resolve via Context7 or the official changelog — never trust training data |

##### Recursive premise (one level only)

If the verification command itself depends on a fact, falsify that
prerequisite first — but cap recursion at **one level**. Example: a
verification SQL `SELECT col_a FROM t WHERE join_key = ?` assumes
`join_key` exists; run `DESCRIBE t` once before running the SELECT.
Do not recurse further (don't verify that DESCRIBE itself works) —
once is enough. Premise-falsification before public claim — see
global `~/.claude/CLAUDE.md` "External-Surface Write Requires Falsification".

#### 5c. Flip detection — halt A→B→A oscillation

Maintain a per-session ledger across all rounds in the same session.
The ledger has **nine record shapes** — `applied`/`rejected` (flip
detection input), `sibling-applied` (Step 5d cross-check),
`rounds_per_region` (Step 5f diminishing-returns), `deferred` (Step 5i
follow-up issue), `sibling-id` (Step 5d-i answer, reused across rounds),
`review-path` (Step 4a reviewer selection, reused across rounds),
`round-started` (one per round, unconditional), `round-continued`
(Step 5j gate decision) — all must be tracked because a finding rejected
in round N can re-appear in round N+M and would otherwise look novel:

```
round-started:    target={worktree-path}#{branch} | round={N}
applied:          {file}:{line-or-region} | round={N} | {value-before} → {value-after}
rejected:         {file}:{line-or-region} | round={N} | {value-before} → {value-after} | reason: {falsifying evidence}
sibling-applied:  {sibling-repo}#{PR-or-branch} | round={N} | finding={brief-label} | result={same defect | different | does not apply}
rounds_per_region: {file}:{region} | round={N} | cumulative={C}
deferred:         {file}:{line-or-region} | round={N} | finding={brief-label} | issue={URL or "pending"}
sibling-id:       target={worktree-path}#{branch} | round={N} | sibling={ref[,ref…] | none}
review-path:      target={worktree-path}#{branch} | round={N} | path={codex-companion | code-reviewer | manual}
round-continued:  target={worktree-path}#{branch} | from={N} | applied={C} | decision={continue | stop | other} | to={N+1 | —}
```

`target=` is `{worktree-path}#{branch}` — both fields confirmed in Step 3,
because neither identifies the PR on its own: one worktree can be switched
between branches, and one branch can be checked out at a different path in
a later session. `target=` is what keeps the per-invocation answers
(`sibling-id:`, `review-path:`) from leaking: the ledger is
per-**session**, and the Invocation Model above has one session run N
sequential invocations for N PRs, so "the session's first round" is not
the same thing as "this PR's first round". A row is reusable only when
both fields of its `target=` equal the current invocation's; otherwise ask
again.

`sibling-id:` is written once per **invocation target** by 5d-i —
**including when the answer is "no sibling"** (`sibling=none`), because a
missing row is what would otherwise force 5d-i to re-ask on every
re-entered round. `round-continued:` records one 5j decision; `to=` is
filled only when `decision=continue` and is `—` otherwise, so a stopped
round never leaves a round number that was never started. The `→` glyph
stays reserved for value transitions — round transitions use
`from=`/`to=`.

**Round number `N`** is derived, not stored separately:
`N = (the highest round= value in the ledger) + 1`, and `N=1` when the
ledger holds no `round=` field at all. Read only the dedicated
`| round={integer} |` field of a **recognized record shape** — never a
`round=` that happens to occur inside free-form content. Several fields
are free-form: `rejected:` carries `reason:` text (falsifying evidence, or
a `user:` sentence), `sibling-applied:` and `deferred:` carry a
`finding={brief-label}`, and any of them may quote a URL. A `round=999`
sitting in one of those would otherwise advance the round counter to 1000
without a round having started. This is monotone by
construction, so it stays correct where a `round-continued:`-anchored
formula does not: a `decision=stop` row carries `to=—` and would re-issue
its own `from=` on the next invocation in the same session, and a round
that applied nothing writes no `round-continued:` row at all. Round
numbers are session-wide and never reset per target — they order the flip
ledger, which is also session-wide.

**Write `round-started:` as the very first ledger action of every round**,
ahead of the 5f counter update, and unconditionally — before it is known
whether the round will produce a finding. Every other row is conditional:
a round where Codex returns nothing and 5h synthesizes nothing writes no
`rounds_per_region:`, no `applied:`, and no `round-continued:` row, so
without `round-started:` that round leaves the ledger's highest `round=`
untouched and the next round is handed the same `N`.

**Cumulative round count `R`** — the number of rounds run against **the
current target**, which is what a user reading the 5j question needs.
`R = count of round-started: rows whose target= matches this invocation's`.
It is not interchangeable with `N`: `N` is session-wide and never resets,
so on the second PR of a session `N` already counts the first PR's rounds.

Before applying any new edit, scan records whose prefix token is exactly
**`applied:`** or **`rejected:`** (NOT `sibling-applied:`,
`rounds_per_region:`, `deferred:`, `sibling-id:`, `review-path:`,
`round-started:`, or `round-continued:`) in the ledger.
A flip fires when:

1. **Applied flip** — the new edit would revert a previously-applied
   change (`applied: A → B` then new proposal `B → A` on the same region).
2. **Re-proposal of rejected** — a finding that was already rejected
   in an earlier round is being proposed again with the same value
   transition (`rejected: A → B` then new proposal `A → B` again).

In either case, STOP and surface to the user:

```
⚠ Flip detected: {file}:{region}
   Round N {applied|rejected}: {A} → {B}
   Round N+M now suggests:     {B} → {A}    (or same A → B for re-proposal)
Both findings cannot be simultaneously correct.
Resolve before applying further edits.
```

**Evidence rejection vs user decision.** A `rejected:` row whose reason
starts with `user:` (written by 5i for a `미적용` / `후속이슈` answer) records
a *decision*, not a disproved premise — the finding may well be correct. When
the colliding row is one of those, do not claim a factual contradiction; use
this message instead:

```text
⚠ Re-proposal of a user-declined finding: {file}:{region}
   Round N: user chose {미적용|후속이슈} — {A} → {B}
   Round N+M proposes the same change again.
Confirm before re-applying; the earlier answer was a decision, not a
disproved premise.
```

Do not apply either side of a flip without explicit user direction. The
ledger lives in the assistant's working memory for the session only —
flip detection is inherently same-session and does not require
cross-session persistence.

#### 5d. Cross-check sibling implementations (when applicable)

When the PR under review is a port / parallel hotfix / A/B implementation
of logic that exists in another PR or another repo, each fact-modifying
finding that passed Step 5b verification must additionally be tested
against the sibling implementation.

##### 5d-i. Identify sibling implementations

At the **start of Step 5** — immediately after the `rounds_per_region:` counter
update (5f counter, which runs first) and before classifying findings (5a) —
surface:

```
AskUserQuestion: "이 PR이 다른 PR/레포의 port · parallel hotfix · A/B 구현체인가요?
그렇다면 형제 구현체를 알려주세요."
```

Additionally, auto-detect sibling signals:

| Signal source | Detection |
| --- | --- |
| PR body keywords | `Companion`, `Refs #N`, `Mirror of #M`, `Port of`, `Parallel` |
| Commit message citations | References to a sibling PR number (`#N`) in the commit body |
| `git worktree list` | Two conceptually-paired branches (e.g., same issue prefix, `*-shell` / `*-python`) |

If no sibling is identified (user confirms "No", no auto-detect signal fires), skip 5d entirely.

**Record the answer either way.** Write one `sibling-id:` ledger row per
**invocation target** — `sibling={ref[,ref…]}` when siblings were
identified, `sibling=none` when they were not:

```text
sibling-id: target={worktree-path}#{branch} | round={N} | sibling={ref[,ref…] | none}
```

The "none" row matters as much as the positive one: a round re-entered by
5j reads this row instead of re-asking, and a missing row is
indistinguishable from "never asked". Ask on the first round **of this
target**; later rounds against the same target reuse the recorded answer.
A row whose `target=` differs is another PR's answer and must not be
reused — reusing it would skip 5d-i for the second PR of the session and
cross-check it against the first PR's sibling.

##### 5d-ii. Apply falsifiable tests to sibling

For each fact-modifying finding that passed Step 5b:

1. Construct the **same falsifiable test** used in 5b (identical input, invocation, or query).
2. Apply it against the sibling implementation (sibling worktree path, sibling repo branch).
3. Record the result in the session ledger (extends the 5c ledger format with `sibling-applied:` rows):

```
sibling-applied: {sibling-repo}#{PR-or-branch} | round={N} | finding={brief-label} | result={same defect | different | does not apply}
```

**Result semantics:**

| Result | Meaning |
| --- | --- |
| `same defect` | Sibling exhibits the identical root-cause failure — sibling PR also needs the fix |
| `different` | Sibling has a variant or no equivalent code path — no cross-fix needed |
| `does not apply` | The finding's context (file, function, identifier) does not exist in the sibling |
| `inaccessible` | Sibling branch/repo could not be reached locally — cross-check skipped; user warned |

##### 5d-iii. Propose sibling fix (same defect only)

When `result=same defect`:

1. Draft the equivalent edit for the sibling PR.
2. **Surface to the user before applying** — the sibling PR has its own approval scope separate from the current PR:

```
⚠ 형제 구현체 동일 결함 발견:
   현재 PR: {current-repo}#{current-PR} — finding: {brief-label}
   형제 PR:  {sibling-repo}#{sibling-PR} — 동일 결함 확인 근거: {falsifying test output}

제안된 수정: {draft-edit-summary}
형제 PR에 적용할까요? (이 PR과 별도의 승인이 필요합니다)
```

3. Record the outcome in the ledger:
   - Applied → append `fix-applied: yes` to the `sibling-applied:` row
   - User declined → append `fix-applied: declined` to the `sibling-applied:` row

Do NOT apply any sibling edit without explicit per-PR user approval. Approval for
the current PR does not transfer to the sibling PR.

#### 5g. Critic pre-lock probe check

Before a critic finding that contains any of the negative-claim forms below
is surfaced to the user, the critic **must** run an independent live probe
at the assertion site and include the result in the same message body.

##### Negative-claim trigger forms

The gate fires when the critic's output (or any finding it forwards)
contains one of the following patterns — in English or Korean:

| English form | Korean form |
| --- | --- |
| "X is fabricated" | "X 는 fabricated" |
| "X does not exist" | "X 는 존재하지 않음" / "X 는 없음" |
| "X is unused" | "X 는 사용되지 않음" |
| "X has no runtime effect" | "X 는 runtime effect 가 없음" |
| "X is missing from {file/scope}" | "X 는 {file/scope} 에 없음" |

The list is illustrative, not exhaustive. Any claim whose logical content
is "X does not exist in the codebase / in this file / in this scope" falls
within the gate — regardless of exact phrasing.

##### Mandatory probe citation format

Every negative claim that falls within the gate must include, in the same
message body at the assertion site:

```
Probe: <command> → <one-line output>
```

Examples:

```
Probe: grep -n PRAXIS_ASK_END_STRICT hooks/preflight-gate/block-ask-end-option/impl.py → 452: strict_env = os.environ.get("PRAXIS_ASK_END_STRICT", "")
Probe: grep -rn "col_b" schemas/my_table.sql → (no output — col_b absent)
Probe: grep -n "def run_query" src/client.py → (no output — symbol not defined)
```

The probe command must be the **actual command**, not a description of
what was done. "I already read this file earlier in the session" is **not**
a valid substitute — re-run at the negative-claim emit site.

##### Absence-of-evidence vs evidence-of-absence

When the probe returns non-empty output that contradicts the negative claim,
the critic must **retract** the claim before surfacing the finding:

```
Retracted: original claim "PRAXIS_ASK_END_STRICT is fabricated"
Probe: grep -n PRAXIS_ASK_END_STRICT hooks/preflight-gate/block-ask-end-option/impl.py →
  28: Deprecated: PRAXIS_ASK_END_STRICT=1 is still respected when explicitly set
  452: strict_env = os.environ.get("PRAXIS_ASK_END_STRICT", "")
Finding: PRAXIS_ASK_END_STRICT exists — claim withdrawn.
```

When the probe returns empty output (absence confirmed), cite the empty
result explicitly so readers can distinguish verified absence from unchecked:

```
Probe: grep -rn "col_b" schemas/ → (no output — col_b absent in schemas/)
```

##### Worked examples

**F1 — git boolean-flag fix (PR #344 round-2, author failure caught by round-3 critic)**

Critic finding that needed a probe before surfacing:
> "`--literal-pathspecs` and `--super-prefix` are boolean flags and cannot
> take a value argument."

Required probe citation:
```
Probe: man git | grep -A2 '\-\-literal-pathspecs' → --literal-pathspecs: Treat pathspecs literally. [no value argument]
Probe: man git | grep -A2 '\-\-super-prefix' → --super-prefix=<path>: [takes a value — NOT a boolean flag]
```

The second probe disproves the grouped claim for `--super-prefix`. Without
these probes, the critic's "both are boolean" claim would have been surfaced
unchecked and the force-push bug would not have been caught in round 3.

**F2 — `PRAXIS_ASK_END_STRICT` fabrication claim (PR #341 round-1, critic failure)**

Critic finding that was surfaced without a probe:
> "`PRAXIS_ASK_END_STRICT` is a fabricated precedent — it does not exist
> in hooks/*.py."

Required probe citation (missing in round-1):
```
Probe: grep -n PRAXIS_ASK_END_STRICT hooks/preflight-gate/block-ask-end-option/impl.py → 452: strict_env = os.environ.get("PRAXIS_ASK_END_STRICT", "")
```

The probe would have immediately falsified the claim (the variable appears
at lines 28, 30, 31, 417, 451, 452, 457). Because the probe was skipped, the round-2
fix agent had to discover and correct the critic's error inline — a
preventable extra round-trip.

##### Critic prompt template requirement

When codex-companion or the review model emits critic output, the system
prompt or review-invocation context **must** include the following
requirement block so the gate is enforced at generation time, not only
at post-processing time:

---

```
CRITIC PRE-LOCK PROBE GATE (mandatory)

Before surfacing any of the following negative claim forms, run an
independent live probe at the assertion site and include the result
inline in the same message:

  - "X is fabricated"
  - "X does not exist" / "X 는 없음" / "X 는 존재하지 않음"
  - "X is unused" / "X 는 사용되지 않음"
  - "X has no runtime effect" / "X 는 runtime effect 가 없음"
  - "X is missing from {file/scope}" / "X 는 {file/scope} 에 없음"

Required inline citation format:
  Probe: <command> → <one-line output>

Example:
  Probe: grep -n PRAXIS_ASK_END_STRICT hooks/preflight-gate/block-ask-end-option/impl.py → 452: strict_env = os.environ.get("PRAXIS_ASK_END_STRICT", "")

"I already read this file earlier" is NOT a valid substitute — re-run
the probe at the negative-claim emit point. If the probe disproves the
claim, retract the claim before surfacing the finding.
```

---

The template block above must appear verbatim (or equivalent) in any
context string passed to codex-companion's review invocation. When
`oh-my-claudecode:code-reviewer` is used as the Step 4a fallback, surface
this requirement block as the first item in the reviewer's context.

#### 5e. Record verification in the commit message

When committing a fact-modifying edit, include the verification result
as a git trailer in the commit body so future readers (and the next
Codex round) can see the premise was checked, and so `git
interpret-trailers` can parse it:

```
fix(scope): <change>

Premise-Verified: <command + output excerpt or source link>
```

Trailer key uses the canonical hyphen-and-capitalized form
(`Premise-Verified:`) — not free-form text — so trailer-aware tooling
can pick it up. Structural and stylistic edits do not need this trailer.

A commit that 5j offers to create follows this same rule. Because 5j runs
after this step, it attaches the trailer itself, one per fact-modifying
edit in that commit, using the 5b verification output from the same
round.

#### 5f. Diminishing-returns advisory — rounds-per-region counter

Repeated rounds on the same file region are a signal that the upstream
surface enumeration is incomplete. This sub-step tracks how many rounds
have touched each region and surfaces a non-blocking advisory when the
count reaches the configured threshold.

##### Region label

For each finding, derive a **region label** using the nearest enclosing
context in the source file:

| File type | Region label |
| ----------- | ------------- |
| Markdown (`.md`, `.mdx`) | Nearest enclosing heading text at any level (`#` through `######`); H4+ headings are valid region labels because this SKILL.md itself uses `####` and `#####` for sub-steps, and excluding them would leave deeply-nested findings unlabelled |
| Code (`.py`, `.ts`, `.sh`, `.js`, …) | Enclosing function / class / method symbol name |
| Plain text / unknown | The file path alone (no region suffix) |

Region label format: `{file}:{region}` — e.g.
`skills/codex-review-wrap/SKILL.md:Step 5` or
`hooks/codex-review-route.sh:parse_prompt`.

**Same-file collision tiebreaker**: when two distinct occurrences of the same
symbol name appear in one file (e.g., two functions both named `parse_prompt`),
append the 1-based occurrence index: `{file}:{region}:{occurrence}` (e.g.,
`hooks/codex-review-route.sh:parse_prompt:1` and
`hooks/codex-review-route.sh:parse_prompt:2`). Occurrence order follows
top-to-bottom source order. This suffix is added only when a collision exists —
unique names keep the plain `{file}:{region}` form.

##### Counter update (every round)

At the **start of Step 5** — this is the **first action**, before the sibling
identification question (5d-i) and before classifying findings (5a) — append
one `rounds_per_region:` entry to the session ledger for each distinct
`{file}:{region}` pair touched by this round's findings:

```
rounds_per_region: {file}:{region} | round={N} | cumulative={C}
```

`cumulative` is the total number of rounds (including this one) that
have touched `{file}:{region}` in the current session.

##### Advisory threshold

Read the threshold `threshold` from the environment at the **start of each
round** (during the counter-update step above). It is deliberately **not**
called `N`: 5c uses `N` for the session-wide round number, and reusing the
letter here would let `cumulative = threshold + 1` be misread as
`cumulative = current round + 1`. The read mechanism follows the same
convention as other `PRAXIS_*` env vars in this codebase — a Bash
parameter expansion with a default:

```bash
threshold=${PRAXIS_DIMINISHING_RETURNS_N:-4}
```

In Python hook contexts, the equivalent is
`int(os.environ.get("PRAXIS_DIMINISHING_RETURNS_N", "4"))` (consistent with
the `os.environ.get("PRAXIS_EXTERNAL_WRITE_STRICT")` pattern at
`hooks/advisory-nudge/external-write-falsify-check/impl.py:578` and the
`os.environ.get("PRAXIS_AUTHOR_EXEMPT_STRICT")` pattern at
`hooks/advisory-nudge/external-write-falsify-check/impl.py:591`).

**Mid-session change semantics**: the env var is read fresh at each round's
counter-update step; it is not cached at session start. If
`PRAXIS_DIMINISHING_RETURNS_N` changes mid-session (e.g., changed from 4 to 2
between round 3 and round 4), the new value takes effect from round 4 onward.
Prior rounds use the value that was in effect at their own round start — there
is no retroactive adjustment to already-recorded ledger entries.

When `cumulative` reaches `threshold + 1` (i.e., the session is starting
its `(threshold+1)`-th round on the same region), surface the following advisory
**once per `{file}:{region}` per session** — immediately after emitting
the `rounds_per_region:` ledger entry, before proceeding to 5a
classification:

```
Advisory: this is round {cumulative} on {file}:{region}. Findings to date suggest
the underlying surface enumeration may be incomplete. Consider pausing
to re-enumerate cases up-front before continuing.
```

The advisory is **informational only** — it does not block, does not
require user confirmation, and does not prevent edits from being applied.
Do not re-surface the advisory on subsequent rounds (round `N+2`,
`threshold+3`, …) for the same region — emit it exactly once at
`cumulative = threshold+1`.

##### Interaction with flip detection (5c)

The rounds-per-region counter increments independently of flip detection.
A flip halt (5c) does not reset or suppress the counter. If a flip is
halted mid-round, the `rounds_per_region:` entry for that round is still
recorded (counter increments) but the advisory suppression rule still
applies (emit only at `cumulative = threshold+1`, not again on later rounds).

#### 5h. Parent-truncates-child SoT enumeration audit <!-- [#395] -->

When the PR diff touches a parent document that inline-transcribes
enumerations owned by a sibling SoT (another skill body, a test definition
table, a routing matrix, a prerequisite list), truncation of that
enumeration is a systematic failure mode that round-by-round external review
catches one missing row at a time. This step collapses that N-round sequence
into a single pre-merge sweep.

Run this step **once per round, after every approved finding has been
applied** — findings the user left at `미적용` or `후속이슈` do not hold it
back — before the round ends. ("Round" is the unit defined at the top of
Step 5: one invocation of Codex review. Since 5j can re-enter Step 4
inside a single skill invocation, "per invocation" would be ambiguous.)

**Do not re-synthesize a finding the user already declined.** Before
emitting, scan the ledger for a `rejected:` row on the same
`{file}:{region}` that carries `reason: user:`, the `[#395]` marker,
**and the same cited sibling SoT** (`{sibling_file}:{sibling_section}`);
if one exists, stay silent for that one citation. Without this check a
declined synthesized finding is re-emitted every round — 5h is a
stateless sweep, so it cannot remember on its own — and 5c halts the
round as a re-proposal of a user-declined finding, every round.

The key needs all three parts because each one alone over- or
under-suppresses:

- `{file}:{region}` alone silences real truncations in that heading for
  the rest of the session — region labels are coarse (nearest enclosing
  heading), and one heading routinely cites more than one sibling SoT.
- adding `[#395]` separates a declined 5h finding from a declined Codex
  finding, since `rejected:` rows carry no provenance field — but two 5h
  findings under the same heading still collapse onto one key.
- adding the sibling SoT identity is what keeps those two apart, so
  declining the `Test definitions` truncation leaves the `Phase
  applicability` truncation under the same heading free to re-emit.

##### Trigger — does the parent doc cite a sibling SoT?

Scan the parent document for any of the following citation signals:

| Citation signal | Examples |
| --- | --- |
| Another skill name referenced | `codex-review-wrap`, `cmux-delegate`, `retrospect` |
| Enumerated test IDs | `Test 1`, `Test 7`, `Test N-M` |
| Enumerated phase IDs | `Phase 0`, `Phase 1a`, `Phase 2` |
| Named enum cited | `kind:`, `state:`, `result=` with a listed subset |
| Matrix row reference | `row of <matrix-name>`, `routing matrix`, `prerequisite rows` |
| Prerequisite claim | `after phase N`, `requires step M`, `depends on step X` |

If **no** citation signal is found, skip 5h entirely — emit nothing.

##### Audit procedure (per cited SoT)

For each citation signal found:

1. **Locate the source SoT** — find the table / enum / list / prerequisite
   block that owns the enumeration in the sibling document. Use `Read` on
   the sibling skill file or the referenced doc section.

2. **Count source rows** — record `source_count` = the number of distinct
   items in the sibling SoT.

3. **Count parent-transcribed rows** — record `parent_count` = the number
   of items the parent body cites at the same citation site.

4. **Compare** — compute the set difference: `missing = source_items − parent_items`.
   If `missing` is non-empty, the parent truncates or diverges from the sibling
   SoT. Emit a synthesized finding (see format below). If `missing` is empty,
   record match and skip to the next signal.
   If the sibling SoT cannot be located, emit an unresolved advisory (see
   below).

   > **Why set difference, not count equality**: count equality passes when
   > `parent` drops one source item and adds one stale/extra item simultaneously
   > (counts match but content diverges). Set difference catches this case.

##### Synthesized finding format

```
⚠ SoT truncation detected — [#395]:
  Parent: {parent_file}:{heading_or_line}
  Sibling SoT: {sibling_file}:{heading_or_section}
  Source rows: {source_count}  |  Parent-cited rows: {parent_count}
  Missing: {list of missing items, derived from set difference}

Proposed resolution: extend the parent citation to include all {source_count}
items, OR replace the inline transcription with a reference link to the
sibling SoT so the parent can never drift again.
```

Carry the `[#395]` marker **and the cited sibling SoT** into the ledger.
When 5i returns `미적용` or `후속이슈` for a synthesized finding, the
resulting `rejected:` row's `reason:` field starts with `user:` and the
row includes both — that triple is the key the re-synthesis check above
reads:

```text
rejected: {parent_file}:{heading} | round={N} | [#395] SoT truncation vs {sibling_file}:{sibling_section} | reason: user: declined (round N)
```

The synthesized finding is treated as a **structural** finding (not
fact-modifying): it describes an omission in documentation, not a runtime
predicate. Apply Step 5a classification accordingly — it does not require a
premise check via 5b, but **does** flow through the normal apply/commit cycle
(5c flip detection, 5i approval). Being structural, it takes no
`Premise-Verified:` trailer — 5e is the single rule for trailer scope.

##### Unresolved advisory (sibling SoT not locatable)

When the citation signal is present but the referenced sibling SoT cannot
be located (skill file not found, section heading has changed, external doc
URL):

```
Advisory — SoT reference unresolved [#395]:
  Parent: {parent_file}:{heading_or_line}
  Citation: "{verbatim citation text}"
  Could not locate sibling SoT — manual check recommended before merge.
```

This advisory is non-blocking; it does not prevent the review from completing.

##### Authoring guidance (prevention, not just detection)

When authoring or editing a parent skill body, prefer **reference links**
over **inline transcription** whenever the source of truth lives in a sibling
document:

- **Prefer**: `See [Test definitions in child-skill](./child/SKILL.md#test-definitions).`
- **Avoid**: re-listing `Test 1`, `Test 2`, ..., `Test N` verbatim in the
  parent body.

Inline transcriptions are permissible only when the cited enumeration is
stable and small (≤ 3 items). For enumerations of 4+ items, or enumerations
expected to grow, use a reference link — each transcription site becomes a
permanent drift risk.

##### Interaction with step 5b

The SoT audit is distinct from the per-finding premise verification in 5b:

- **5b** fires on Codex/BugBot findings after they are surfaced and classifies
  each finding's underlying factual claim.
- **5h** is a proactive sweep on the parent document itself, independent of
  whether Codex reported a SoT-related finding. It catches truncations that
  external review has not yet surfaced — the same root cause that would have
  produced the (threshold+1)-th round finding.

The two steps are complementary: 5b prevents bad fixes; 5h prevents missed
enumerations from reaching the next reviewer.

#### 5i. Per-finding user approval gate <!-- [#861] -->

Steps 5a–5c decide whether a finding is *correct*. Whether it should be
applied **in this PR, now** is a separate judgement that belongs to the
user. This sub-step surfaces that decision explicitly.

**No finding may be edited on the agent's judgement alone.**
There is no auto-apply path — not for stylistic findings, not for
"obviously right" one-liners, not for findings the agent already verified
in 5b. Applying an edit without a recorded `적용` answer is a violation of
this gate, and the briefing-style disclosure ("I applied these, tell me if
wrong") is not a substitute for asking first.

##### Scope and ordering

The gate covers **every finding of the round that survives 5b and 5c**:
fact-modifying, structural, and stylistic alike, plus any finding
synthesized by 5h. A finding whose premise 5b actively disproved is **not**
offered as an `적용` option — applying a value already shown to be false is
never a decision worth surfacing. Report those in one line instead
("N findings rejected on evidence: …") so the user still sees them.
Present the surviving findings in a deterministic order so the same round
always produces the same question sequence:

1. fact-modifying findings (5a row 1)
2. structural findings (5a row 2)
3. stylistic findings (5a row 3)

Ties inside a group keep Codex's own output order. Findings already halted
by 5c flip detection are **not** put through the gate in that state — a flip
is surfaced on its own per 5c first. Once the user resolves it, the surviving
side re-enters 5i as a normal finding and needs its own `적용` answer before
it is edited; the flip-resolution direction is not itself that answer.

If the round produced zero applicable findings, skip 5i entirely and say so
in one line.

##### Batching

On the Claude host, `AskUserQuestion` accepts at most **4 questions per
call** and **4 options per question** (see `RUNTIME_CONSTRAINTS.md` §1 and
§1a). Emit one question per finding, **4 findings per call**, and repeat the
call until every finding has an answer. Do not compress two findings into
one question — a single answer cannot carry two decisions.

`skills/` ships to other hosts too (see `manifests/platforms/` and
`plugins/praxis/.codex-plugin/plugin.json`), and their ask-user tools have
their own caps. Batch against **the cap of the host actually running** —
read it from that host's tool schema rather than assuming 4 — and never
above 4. If the running host exposes **no callable ask-user tool at all**
(or the one it has is unavailable in the current mode), the run counts as
non-interactive: take the step-0 path — verify, apply nothing, defer the
survivors — rather than applying findings without a recorded answer.

Each question's `question` text must start with a stable finding ID
(`F1`, `F2`, …). Answers come back keyed by question text, so two duplicate
findings on the same `{file}:{region}` with the same transition would
otherwise collide on one key and share (or overwrite) a single answer.

##### Question body — required elements

Each question body must let the user decide without re-reading the diff:

```text
{file}:{region}
변경: {value-before} → {value-after}
판정: {apply | reject} — {one-line reason}
Probe: {command} → {one-line output}
flip: none
```

- `Probe:` carries the 5b verification evidence verbatim. `Probe: n/a
  (structural)` / `Probe: n/a (stylistic)` is allowed **only** when the
  finding is not fact-modifying **and** carries no 5g negative claim; a
  structural finding that asserts "symbol X is unused / does not exist"
  still carries its 5g probe output here. The line is never omitted.
- `flip:` reports the 5c scan result for that region (`none`, or the
  colliding ledger row).
- When the agent's verdict option is labelled `(Recommended)`, a line
  starting at column 0 with the literal prefix `Falsified:` must carry the
  disconfirming-test result — either in the question body or in that
  option's `description` (global CLAUDE.md → *Self-Falsify Before
  Recommendation Lock*; enforced by
  `hooks/advisory-nudge/output-block-falsify-advisory/impl.py`, which does a
  `startswith` check, so a mid-sentence or fenced `Falsified:` does not
  count). The 5b probe usually supplies that line; when it does not, run one
  and cite it.

##### Options

Exactly three options, plus the runtime's automatic `Other` slot:

| Option | Action |
| --- | --- |
| `적용` | Apply the edit → ledger `applied:` row → 5e `Premise-Verified:` trailer **if the edit is fact-modifying** (structural and stylistic edits take no trailer, per 5e) |
| `미적용` | Do not edit → ledger `rejected: … \| reason: user: declined (round N)` |
| `후속이슈` | Do not edit → ledger `rejected: … \| reason: user: deferred to follow-up` **and** a `deferred:` row |
| `Other` (free text) | If the instruction is a plain decline or defer, record the matching row above. If it proposes a **different** edit than the finding did, that is a new proposal: re-run 5a classification, 5b premise verification, and the 5c flip scan on it, then ask again — an `Other` answer is never itself an `적용` answer for the modified edit. If it reads as approval of the **unchanged** edit ("그대로 적용해", "yes, apply this"), do not treat the paraphrase as the answer either: re-ask the same finding with the three options and apply only on a literal `적용` |

Put the agent's recommended option first and mark it `(Recommended)`.

Both `미적용` and `후속이슈` write a `rejected:` row with a `user:` reason
prefix, so a finding the user declined in round N still fires 5c if Codex
re-proposes the same transition in round N+M — but 5c reports it as a
re-proposal of a *decision*, not as a factual contradiction (see 5c →
*Evidence rejection vs user decision*).

##### Follow-up issues (`후속이슈` answers)

Do **not** create an issue per answer. Collect all `후속이슈` findings of the
round and, once the batch is complete, surface a single implementation-approach
review — scope, target repo(s), expected PR count, verification plan — and
create issues only after the user approves that approach (global CLAUDE.md →
*Implementation-approach review BEFORE issue creation*). Append the resulting
URL to each `deferred:` row; leave `issue=pending` if the user declines.

##### Relationship to 5d-iii (sibling PRs)

An `적용` answer authorizes the edit **on the current PR only**. The sibling
fix in 5d-iii keeps its own separate approval — approval never transfers
across PRs.

##### Cancellation

If the user cancels a batch or gives no answer, stop applying findings for
the round. Report which findings were already applied, which remain
undecided, and end the round without further edits.

#### 5j. Round-continuation gate

This sub-step alone governs the round **boundary** rather than the round
interior, and it is the only one that draws an edge back to Step 4.
5a–5i each act on one round's findings; 5j decides whether there is
another round at all.

##### Fire condition

Fire only when **all three** hold:

- **(a)** at least one edit was actually applied this round — `{C} ≥ 1`,
  counting 5i-approved edits and 5h synthesized-finding edits together;
- **(b)** those edits are inside the next round's review target — the
  check is membership of the **actual** next-round diff, not of the scope
  name, and it is asked of whichever reviewer the `review-path:` row names,
  not of codex-companion unconditionally;
- **(c)** the interactivity check (execution-order item 0) is re-run for
  **this** round and passes.

Measured against `codex@openai-codex 1.0.6` (`scripts/lib/git.mjs`):
`resolveReviewTarget` supports three scopes (`:141`) and returns
`explicit: true` for `--base <ref>`, `--scope working-tree`, and
`--scope branch` (`:143-160`); under `--scope auto` it picks
`working-tree` whenever the tree is dirty and `branch` otherwise
(`:176-190`). `working-tree` mode collects only `git diff --cached` and
`git diff` (`:309-323`) — both relative to HEAD — while `branch` mode
collects the base comparison. Re-measure if the plugin version changes.

Each mode therefore **excludes** the other's edits, which is what
condition (b) has to check:

| Next round resolves to | Carries | Missed if… |
| ------------------------ | --------- | ------------ |
| `working-tree` | staged + unstaged + untracked | 5e committed this round's edits |
| `branch` | commits against the base | this round's edits are still uncommitted |

`--scope auto` does **not** make this self-correcting. `isDirty` is true
when *any* path is dirty (`getWorkingTreeState`, `:122-131`), so a round
that commits its edits while an unrelated file stays dirty still resolves
to `working-tree` — and the commit is outside it. The scope name alone
never settles condition (b).

So, before firing: determine what the next round would resolve to under
the same arguments, and confirm every edit applied this round appears in
that diff. If any is missing, do not fire on a warning — take one of two
paths and say which in the question body:

- **make the target match** — commit the edits (branch-bound target) or
  leave them uncommitted (working-tree-bound target); or
- **switch the scope** for the re-entered round, naming the flag added.

If neither is possible, skip the gate and proceed to Step 6 rather than
re-entering a round that would review a diff missing this round's work —
that round reads as convergence while having verified nothing.

**The `code-reviewer` fallback path.** The table above describes
codex-companion, which is what `review-path: … | path=codex-companion`
names. When the row names `code-reviewer` instead, condition (b) asks the
same question of *that* reviewer's next-round target: the Step 4a fallback
is invoked with cwd set to the selected worktree and reviews what it is
handed, so establish what it would be handed and confirm every applied
edit is in it. If that cannot be established for the reviewer in use, **do
not fire** — conditions (a) and (c) are unaffected, but (b) is unmet, and
an unmet (b) is a skip, not a warning. `path=manual` never reaches 5j:
Step 4a's `Manual` branch exits before Step 5.

When the branch-scope path leads 5j to offer a commit and the commit
contains fact-modifying edits, 5j attaches the `Premise-Verified:` trailer
itself — one per such edit, quoting the 5b output from this round. 5e
defines the trailer's scope and format; it simply runs earlier in the
order (item 10), so it cannot cover a commit created here.

##### Decision table

`{C}` below is the round's **total** applied count — Codex findings plus
any 5h synthesized finding the user approved. Codex returning 0 findings
is therefore not its own row: 5h runs every round (see 5h → *once per
round*) and can produce an edit on a round Codex left empty.

| Round state | Gate fires? | Next |
| ------------- | ------------- | ------ |
| `{C}` = 0 for the whole round (no Codex finding applied, no 5h finding applied) | No | Step 6 |
| 5i batch cancelled, `{C}` = 0 | No | Step 6 |
| 5i batch cancelled, `{C}` ≥ 1 | **Yes** — question carries the undecided count | per answer |
| Non-interactive run (`claude -p`, background worker) | No | Step 6 |
| An applied edit is outside the next round's diff and neither realignment path is available | No | Step 6 |
| `{C}` ≥ 1, every applied edit confirmed in the next round's diff, interactive | **Yes** | per answer |

A cancelled 5i batch is not a vote against another round — *Cancellation*
above ends the round while keeping the edits already applied, so the
edits are real and the question is still live.

##### The question

```text
AskUserQuestion: "라운드 {N} 완료 — 이번 라운드 {C}건 적용, 누적 {R}라운드{,
수확 체감 region: {regions}}{, 미결정 {K}건}. Codex 리뷰를 한 번 더
실행할까요?"

  option 1  "추가 라운드 실행"
            → Step 4 재진입, 같은 worktree/PR, 라운드 N+1
  option 2  "현재 라운드로 충분 — Step 6 으로 진행"
            → broker reaper 후 마무리
```

`{N}` is the session-wide round number defined in 5c; `{R}` is the
per-target cumulative count defined alongside it (`count of
round-started: rows matching this target`). They differ from the second
PR of a session onward, and `{R}` is the one the question shows — a user
deciding whether *this* PR has had enough review is not helped by a count
that includes the previous PR's rounds. `{C}` is the applied count from
the decision table; `{K}` is the undecided count a cancelled 5i batch
left behind; `{regions}` lists the regions 5f flagged this round, and the
whole clause is omitted when it flagged none.

`{regions}` lists every region whose `rounds_per_region:` cumulative count
exceeds the 5f threshold, **recomputed every round**. 5f's own advisory
fires once per region and then goes quiet (`cumulative = threshold+1` only), so
the gate — not 5f — is what keeps the diminishing-returns signal in front
of the user as rounds accumulate. This is also why the question grows
more informative rather than less: without it, round 5's question would
carry exactly as much as round 2's.

Neither option label may contain a bare end-token followed by a heading
separator (`종료 —`, `여기까지`, `그만:`, `마무리 -`) — the
`block-ask-end-option` hook cannot see which skill is running and reads
that shape as a session-end option, blocking the call. Keep labels
phrased as the action taken ("Step 6 으로 진행"). The same restraint
applies to the question body.

The runtime appends its own `Other` slot. Route a free-text answer that
clearly maps to one of the two options accordingly; if it is ambiguous,
re-ask the same question rather than guessing (same as 5i). Record
`decision=other` when the answer resolves to neither.

##### Re-entry

1. Return to **Step 4**; skip Steps 1–3 — the review target is already
   fixed.
2. **Normalize** the original `{{ARGUMENTS}}` before reuse — strip
   `--background` (a re-backgrounded round breaks the gate's synchronous
   loop) **and** any existing `--scope` / `--base <ref>`. Then append at
   most **one** target-selecting flag, the one condition (b) settled
   above. Stripping first is what keeps the two rules from colliding:
   without it, "switch the scope for the re-entered round" stacks a second
   `--scope` on top of the caller's, and which one wins is the CLI's
   business, not this skill's. After appending, re-run the condition (b)
   membership check against the arguments actually being passed.
3. Re-run Step 4's PR-state check every time — the PR can be merged or
   closed between rounds.
4. Reuse the `sibling-id:` and `review-path:` rows whose `target=` matches
   this invocation's target; do not re-ask either.
5. If the recorded fallback was the `code-reviewer` path, the 5g critic
   template must be prepended again on every round — that requirement
   does not carry across rounds by itself.

Record the decision:

```text
round-continued: target={worktree-path}#{branch} | from={N} | applied={C} | decision={continue | stop | other} | to={N+1 | —}
```

##### No round cap

Three paths end the loop: the user chooses to proceed to Step 6, a round
applies zero edits, or the run is non-interactive so the gate never
fires. There is no
maximum round count and no new environment variable. What makes an
unbounded loop safe is that **every** iteration passes through an
explicit user decision — not 5f's advisory, which is non-blocking,
per-region, and emitted exactly once.

A 5c flip halt is **not** a fourth termination path: once the user
resolves the flip, the surviving side re-enters 5i and can be applied.
An unresolved flip converges on the zero-edits path instead.

### Step 6: Reap leaked codex brokers (phase end)

The openai-codex plugin starts a per-session app-server broker that is
reparented to launchd (`ppid=1`) and is **not** killed when its owning Claude
session exits. Across multi-day uptime these accumulate; once cumulative RSS
crosses the macOS memory-compressor threshold, each idle broker's periodic
wakeup drives compress/decompress churn that surfaces as `kernel_task` system
CPU — a non-linear spike, not a linear one.

Run the co-located reaper at the end of every review invocation. It is the
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

## Error Handling

| Situation | Action |
| ----------- | -------- |
| PR state is CLOSED or MERGED | ABORT: "PR is {state} — review aborted. Re-open or target a different PR." |
| `git worktree list` fails (not a git repo) | Abort: "git worktree list 실패 — git 저장소인지 확인하세요." |
| All worktrees are bare | Treat as Case A (single effective target) using cwd |
| User selects "취소" | Abort silently with one-line message |
| `installed_plugins.json` missing or codex entry absent | Offer alternatives via `AskUserQuestion` (Step 4a) |
| Resolved `codex-companion.mjs` path does not exist | Offer alternatives via `AskUserQuestion` (Step 4a) |
| Premise check (Step 5b) disproves a finding | Skip the edit; reply to Codex with the falsifying evidence |
| Flip detected (Step 5c) | Halt; surface both rounds to the user; do not apply either side without explicit direction |
| Sibling identified but branch/repo not accessible locally | Skip 5d for that sibling; record `sibling-applied: ... \| result=inaccessible` in ledger; warn user to check out the branch |
| Sibling auto-detected but user confirms "not a port" | Skip 5d entirely; still write `sibling-id: … \| sibling=none` so a 5j re-entry does not re-ask |
| `PRAXIS_DIMINISHING_RETURNS_N` is set but not a positive integer | Use default (4); do not error |
| Region label cannot be determined (binary file, empty file) | Use the file path alone as the region label |
| Critic negative claim emitted without `Probe:` citation (5g) | Halt the finding; prompt the critic to re-run with probe citation before surfacing |
| Probe command for 5g returns unexpected output or exits with an error code that signals a command failure (e.g. exit=2 "command not found", permission denied) — distinct from `grep` exit=1 (no match), which is the expected signal for verified absence | Surface probe failure to the user; do not auto-retract the claim — let the user decide |
| Approval gate (5i) — user cancels a batch or gives no answer | Stop applying for the round; report applied / undecided findings; no further edits |
| Approval gate (5i) — round produced zero applicable findings | Skip 5i; state "no findings to approve" in one line |
| Approval gate (5i) — user declines the follow-up issue approach review | Keep the `deferred:` rows with `issue=pending`; create nothing |
| Approval gate (5i) — non-interactive run (`claude -p`, background) where `AskUserQuestion` cannot reach a user | Apply nothing; record every finding that survived 5b/5c as `deferred:` (evidence-rejected and flip-halted findings stay out, same as the interactive path) and report the full list for a later interactive round |
| Round-continuation gate (5j) — user cancels, or `AskUserQuestion` returns no answer (it blocks, so this means cancellation or a tool error) | Do not re-enter; proceed to Step 6. Record `decision=stop` |
| Round-continuation gate (5j) — next round resolves to `branch` and the applied edits are uncommitted | Offer to commit them; if declined, skip the gate and proceed to Step 6 — a base-pinned review of round N+1 would not see the edits |
| Round-continuation gate (5j) — next round resolves to `working-tree` and 5e already committed this round's edits | Do not offer a commit. Either re-enter with `--base <ref>` so the commit is in the target, or skip the gate — never fire while telling the user the edits are invisible to round N+1 |
| SoT audit (5h) — sibling document not locatable | Emit unresolved advisory; do not block review completion |
| SoT audit (5h) — parent citation site ambiguous (multiple tables at same heading) | Use all tables at the heading as candidate SoT sources; report each comparison separately |
| Reaper (Step 6) — running broker has no readable `broker.log` | Idle is indeterminate → broker is KEPT (logged as `SKIP ... no logFile`); never reaped on a guess |
| Reaper (Step 6) — `CLAUDE_PLUGIN_ROOT` unset (skill run outside plugin context) | Resolve the script via the installed-plugins manifest, same as Step 4a; if still unresolved, skip Step 6 with a one-line note — reaping is best-effort hygiene, not a gate |
| Reaper (Step 6) — agent considers `pkill -f codex` / `pkill node` | Forbidden: aborts sibling sessions' in-flight reviews. Use only the reaper's per-broker idle gate |

## Example Flow

```
user: /codex-review-wrap

[Step 1] git worktree list result:
  0: /Users/dev/project/my-repo               (main)
  1: /Users/dev/project-wt/my-repo-feature-1  (issue-1-feature)

[Step 2] AskUserQuestion →
  "어느 worktree 를 review 할까요?"
  0: /Users/dev/project/my-repo (main)
  1: /Users/dev/project-wt/my-repo-feature-1 (issue-1-feature)

user selects: 1

[Step 3] Review target: /Users/dev/project-wt/my-repo-feature-1 (branch: issue-1-feature)
  ⚠ cwd (/Users/dev/project/my-repo) ≠ review target

[Step 4] cd /Users/dev/project-wt/my-repo-feature-1
         → node {install_path}/scripts/codex-companion.mjs review

[Step 5 — Round 1 — counter update (5f, first action)]:
  ledger: rounds_per_region: query.sql:filter_clause | round=1 | cumulative=1
  ledger: rounds_per_region: cli.sh:parse_prompt      | round=1 | cumulative=1
  (cumulative ≤ threshold=4 → no advisory emitted yet)

[Step 5 — Round 1 — sibling check (5d-i)]: AskUserQuestion fired:
  User: "이 PR은 praxis#199 (shell 버전)의 Python port입니다."
  → sibling identified: praxis#199 on branch issue-199-hook-shell

[Step 5 — Round 1 — classify + verify (5a → 5b)] Codex returned 3 findings:
  - F1: rename `query()` → `run_query()`           [structural — no premise check, still gated by 5i]
  - F2: change WHERE col_a = 1 → col_b = 1         [fact-modifying — verify column exists]
  - F3: drop the `--state all` flag                [fact-modifying — verify CLI accepts the value]
  Verify F2: DESCRIBE my_table → col_b not present
    → ledger: rejected: query.sql:L42 | round=1 | col_a → col_b | reason: col_b absent in DESCRIBE
    (evidence-rejected → not put through the 5i gate)
  Verify F3: gh search issues --help → --state accepts only {open, closed}

[Step 5i — approval gate] 2 findings survive to the gate (F3 fact-modifying, F1 structural)
  1 finding rejected on evidence: F2 (col_b absent) — reported, not offered as 적용
  AskUserQuestion (1 call, 2 questions):
    Q1 "F3 — cli.sh:L10 --state 값 수정"   (question text starts with the finding ID)
       body: cli.sh:parse_prompt
             변경: "--state all" → "--state open"
             판정: apply — 현재 값은 CLI 가 거부함
             Probe: gh search issues --help → --state {open|closed}  ("all" 미지원)
             flip: none
       options: 적용 (Recommended) / 미적용 / 후속이슈
       적용 description: "Falsified: '--state all 도 허용된다' 가설 반증 — probe 출력에 all 없음"
    Q2 "F1 — query() → run_query() 리네임"
       body: query.sql:query
             변경: query() → run_query()
             판정: apply — 호출부 3곳 동시 수정 필요
             Probe: n/a (structural)
             flip: none
       options: 적용 (Recommended) / 미적용 / 후속이슈
       적용 description: "Falsified: '호출부가 이미 run_query 를 쓴다' 가설 반증 —
         grep -n 'query(' src/ → 3곳 모두 구 이름 사용"
  User: Q1=적용, Q2=후속이슈
  → apply F3; ledger: applied: cli.sh:L10 | round=1 | "--state all" → "--state open"
  → skip F1;  ledger: rejected: query.sql:query | round=1 | query() → run_query() | reason: user: deferred to follow-up
               ledger: deferred: query.sql:query | round=1 | finding=F1(rename) | issue=pending
  (아직 커밋하지 않음 — 실행 순서상 5d-ii/iii 가 5e 앞에 온다)
  Round 종료 시 deferred 1건에 대해 구현 접근안 리뷰 surface → 승인 시에만 이슈 생성

[Step 5d] Cross-check sibling: praxis#199 (branch issue-199-hook-shell)
  Apply same test for F3 against sibling:
    cd /path/to/praxis-wt/issue-199-hook-shell
    gh search issues --help → --state accepts only {open, closed}
    sibling hook also uses "--state all" on line 8 → same defect confirmed
  ledger: sibling-applied: praxis#199 | round=1 | finding=F3(--state all) | result=same defect
  ⚠ 형제 구현체 동일 결함 발견:
     현재 PR: praxis#200 — finding: F3 (--state all)
     형제 PR:  praxis#199 — 동일 결함 확인 근거: hook.sh:L8에서 "--state all" 사용 확인
  → surface to user for separate approval before applying sibling fix

[Step 5e — commit, after 5d completes]
  Commit F3 with trailer:  Premise-Verified: gh search issues --help (excerpt)

[Step 5j — round-continuation gate] fire condition check:
  (a) applied this round = 1 (F3)                          → pass
  (b) scope=auto, worktree clean after commit → next round resolves to branch
      diff; F3's edit confirmed present in it                 → pass
  (c) interactivity check re-run for this round             → pass
  AskUserQuestion:
    "라운드 1 완료 — 이번 라운드 1건 적용, 누적 1라운드. Codex 리뷰를
     한 번 더 실행할까요?"
    옵션: 추가 라운드 실행 / 현재 라운드로 충분 — Step 6 으로 진행
  User: 추가 라운드 실행
  → ledger: round-continued: target=/Users/dev/project-wt/my-repo-feature-1#issue-1-feature | from=1 | applied=1 | decision=continue | to=2
  → re-enter Step 4 (Steps 1–3 skipped, 5d-i and 4a not re-asked — the sibling-id:
    and review-path: rows for this target exist, --background dropped if it was
    present, PR-state re-checked)

[Step 5 — Round 2] Codex now re-suggests changing WHERE col_a = 1 → col_b = 1
  Scan ledger: rejected entry on query.sql:L42 with same A → B transition exists
  → flip fires (re-proposal of rejected); halt and surface to user

[Step 5 — Round 2 alt] Codex now suggests "--state open" → "--state all"
  Scan ledger: applied entry on cli.sh:L10 reverses → flip fires (applied flip); halt

[Step 5j — Round 2, gate skipped] the flip halted before any edit landed:
  (a) applied this round = 0                                → fail
  → gate does not fire; no round-continued: row is written; proceed to Step 6.
  (Had the user resolved the flip and applied the surviving side, (a) would
   pass and the gate would fire — a flip halt is not a termination path.)

[Step 5j — alternative branch, NOT a continuation of the block above]
  The block above ended at Step 6, so no round 3 follows it. This branch shows
  what a round 3 would look like on the other path: the user resolves the round-2
  flip, applies the surviving side ({C} = 1), the gate fires, the answer is
  추가 라운드 실행, and Step 4 is re-entered.

  [Round 3, gate skipped] Codex returned 0 findings
    5h still ran this round and synthesized nothing either → {C} = 0 → gate does
    not fire → Step 6. (Had 5h synthesized a truncation and the user approved it,
    {C} would be 1 and the gate would fire — "Codex 0 findings" alone is not a
    skip condition.)

[Step 5f — Diminishing-returns example] PRAXIS_DIMINISHING_RETURNS_N=4 (default)
  Rounds 1–4: counter increments silently
    ledger: rounds_per_region: cli.sh:parse_prompt | round=1 | cumulative=1
    ledger: rounds_per_region: cli.sh:parse_prompt | round=2 | cumulative=2
    ledger: rounds_per_region: cli.sh:parse_prompt | round=3 | cumulative=3
    ledger: rounds_per_region: cli.sh:parse_prompt | round=4 | cumulative=4
  Round 5 (cumulative = threshold+1 = 5): advisory emitted once, then 5a continues normally
    ledger: rounds_per_region: cli.sh:parse_prompt | round=5 | cumulative=5
    Advisory: this is round 5 on cli.sh:parse_prompt. Findings to date suggest
    the underlying surface enumeration may be incomplete. Consider pausing
    to re-enumerate cases up-front before continuing.
  Round 6+: counter still increments, advisory NOT re-emitted
    ledger: rounds_per_region: cli.sh:parse_prompt | round=6 | cumulative=6

[Step 5g — critic pre-lock probe check, F2 scenario]
  Critic finding (round-1): "PRAXIS_ASK_END_STRICT is a fabricated precedent —
    it does not exist in hooks/*.py"
  → gate fires: negative-claim form "does not exist" detected
  → critic required to run probe before surfacing:
    Probe: grep -n PRAXIS_ASK_END_STRICT hooks/preflight-gate/block-ask-end-option/impl.py →
      28: Deprecated: PRAXIS_ASK_END_STRICT=1 is still respected when explicitly set
      452: strict_env = os.environ.get("PRAXIS_ASK_END_STRICT", "")
  → probe disproves claim → critic must retract:
    Retracted: "PRAXIS_ASK_END_STRICT is fabricated"
    Probe: grep -n PRAXIS_ASK_END_STRICT hooks/preflight-gate/block-ask-end-option/impl.py →
      line 28, 30, 31, 417, 451, 452, 457 found
    Finding: variable exists — claim withdrawn.

[Step 5g — critic pre-lock probe check, F1 scenario]
  Critic finding (round-3): "both --literal-pathspecs and --super-prefix are
    boolean flags and cannot take a value argument"
  → gate fires: negative-claim form "cannot take a value" (has no runtime effect variant)
  → critic required to run probe per flag before surfacing:
    Probe: man git | grep -A2 '\-\-literal-pathspecs' → [no value argument — boolean confirmed]
    Probe: man git | grep -A2 '\-\-super-prefix' → --super-prefix=<path> [takes a value — NOT boolean]
  → second probe partially disproves the grouped claim
  → critic surfaces retracted + refined finding:
    "--literal-pathspecs is boolean (confirmed). --super-prefix takes a value (claim retracted for this flag)."

[Step 5h — SoT enumeration audit]
  Parent doc (current PR): skills/phase-router/SKILL.md
  Scan for citation signals:
    - "Test 1-7" cited at heading "## Acceptance Criteria"  → citation signal: enumerated test IDs
    - "Phase 0, Phase 1" cited at heading "## Prerequisites" → citation signal: enumerated phase IDs
  Locate sibling SoTs:
    - Test SoT: skills/phase-router/SKILL.md#test-definitions → Read → finds Test 1–9 (conditional Tests 8, 9)
    - Phase SoT: skills/phase-router/SKILL.md#phase-applicability → Read → finds Phase 0, 1, 1a, 2
  Compare:
    - Tests: parent_count=7, source_count=9 → truncation detected (missing Test 8, Test 9)
    - Phases: parent_count=2, source_count=4 → truncation detected (missing Phase 1a, Phase 2)
  Emit synthesized findings:
    ⚠ SoT truncation detected — [#395]:
      Parent: skills/phase-router/SKILL.md:Acceptance Criteria
      Sibling SoT: skills/phase-router/SKILL.md:Test definitions
      Source rows: 9  |  Parent-cited rows: 7
      Missing: Test 8 (conditional — cache miss path), Test 9 (conditional — retry path)
    ⚠ SoT truncation detected — [#395]:
      Parent: skills/phase-router/SKILL.md:Prerequisites
      Sibling SoT: skills/phase-router/SKILL.md:Phase applicability matrix
      Source rows: 4  |  Parent-cited rows: 2
      Missing: Phase 1a, Phase 2
  → both findings flow through 5a classification (structural), 5c flip check, then apply cycle
```

## Limitations

- Does not modify `/codex:review` itself — users who call it directly still get the old behaviour
- Subshell `cd` does not persist after skill execution — cwd is not mutated in the parent session
- The Step 5 ledger is per-session only — flips that span session boundaries are not detected
- Premise classification (5a) is heuristic; when in doubt, treat the finding as fact-modifying
- Step 5d sibling cross-check requires the sibling branch to be locally accessible — remote-only PRs need a manual `git worktree add` before cross-check can run
- Sibling auto-detection from `git worktree list` uses branch-name heuristics (shared prefix, `*-shell` / `*-python` suffixes) and may produce false positives on unrelated paired branches; user confirmation at 5d-i overrides the auto-detect signal
- The rounds-per-region counter (5f) is per-session only — counts do not carry across session boundaries
- Region label extraction (5f) is heuristic: the nearest enclosing heading / symbol is determined from the finding context Codex provides; findings with no file attribution use the file path alone
- The advisory threshold `PRAXIS_DIMINISHING_RETURNS_N` applies uniformly across all regions; per-region tuning is not supported
- Step 5g negative-claim detection is pattern-based; highly paraphrased negative claims that do not match the trigger forms may slip through — when in doubt, treat a claim as negative and require a probe
- The critic prompt template (5g) is injected into codex-companion's context at invocation time; when using the `oh-my-claudecode:code-reviewer` fallback (Step 4a), the template must be manually prepended to the reviewer's context
- Step 5h SoT audit detects truncation only for inline-transcribed enumerations; reference-link citations (sibling SoT referenced but not transcribed) are inherently safe and are not audited
- Step 5h citation-signal scanning is keyword-based; enumerations that use non-standard labels (e.g., custom matrix row identifiers) may not be detected — when authoring, prefer the standard labels listed in the trigger table
- Step 5h requires the sibling SoT document to be locally readable; remote-only or external URLs are flagged as unresolved advisories and require manual verification
- Step 5i requires an interactive session — `AskUserQuestion` has no reachable user under `claude -p` or a background worker, so those runs apply nothing and defer every finding
- Step 5i costs one `AskUserQuestion` round-trip per 4 findings; a round with many stylistic findings trades throughput for control, by design
- Step 5i decisions live in the same per-session ledger as 5c — a finding declined in one session is not remembered in the next
- Step 5j imposes no maximum round count. Three paths end the loop: the user chooses Step 6, a round applies zero edits (`{C}` = 0, counting 5h synthesized edits — Codex returning zero findings is not on its own one of them), or the run is non-interactive. A 5c flip halt is not one of them — a resolved flip can still be applied; an unresolved one converges on the zero-edits path
- Step 5j never fires in a non-interactive run, but not by a rule of its own: `claude -p` and background workers apply nothing (5i), so the round reaches the gate with zero applied edits and fails the fire condition. Fire condition (c) re-runs the interactivity check anyway, so the guard does not depend on 5i keeping that behaviour
- Step 5j extends the review phase for as long as the loop runs, and Step 6 reaps brokers only at phase end — so each round's broker stays resident until the loop finishes. A long loop holds more brokers than a single-round invocation
- The ledger lives in session working memory, so the longer a 5j loop runs the more rounds compete for that context. Flip detection degrades *within* a session as round count grows — the existing cross-session caveat does not cover this
- Step 5j's fire condition (b) rests on `resolveReviewTarget` and `collectReviewContext` as measured against `codex@openai-codex 1.0.6`; a change to how either picks or assembles a scope would need re-measuring. What (b) requires is membership of the applied edits in the next round's actual diff — the scope name is not a proxy for it, since `--scope auto` resolves to `working-tree` whenever *any* path is dirty and a commit made this round then falls outside the target
- Step 6 reaper is macOS-only (launchd reparenting + `/var/folders` sessionDirs); it is a no-op on other platforms
- Step 6 idle detection uses `broker.log` mtime as an activity proxy — a broker mid-operation that stays silent longer than `--max-age` could be misjudged idle and reaped; the cost is a benign respawn on the next codex call, never a correctness break
- Step 6 phase-end reaping keeps the broker count below the compressor threshold but does not reclaim every running orphan; the session-independent launchd job (`LAUNCHD.md`) is what reaps orphans whose owning session is already gone
