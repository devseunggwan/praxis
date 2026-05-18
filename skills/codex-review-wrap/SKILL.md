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
  Triggers on "codex review", "review codex", "safe review", "/codex-review-wrap",
  "premise verification", "flip detection", "sibling defect", "sibling cross-check",
  "diminishing returns".
verified-against-runtime: true
runtime-verified-at: 2026-05-13
runtime-verified-note: "codex-companion 1.0.4 — ARGUMENTS rejected for non-flag string; AskUserQuestion maxItems:4 blocks worktree list >3 items; Skill() cannot delegate to disable-model-invocation skill. Step 4 hardened to a MUST NOT directive in issue #237 (2026-05-16) — directive-only change, no new runtime claim. Step 5f (PR #329) adds procedural prose only (rounds_per_region ledger + advisory text); no runtime hook code changed — existing verification evidence remains valid."
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
env var `PRAXIS_DIMINISHING_RETURNS_N`).
See **Step 5** for the full gate.

## Invocation Model

**Cardinality**: This skill handles exactly **one PR per invocation**. For N PRs, invoke the skill N times sequentially. Batch for-loops are **not supported** — they collapse Step 5c per-round ledger emission across multiple PRs and break flip-detection guarantees.

## When to Use

- Before calling `/codex:review` from any multi-worktree project
- When the session cwd differs from the worktree you just finished working in
- Triggers: "codex review", "review codex", "safe review", "/codex-review-wrap"

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

The script derives its own ROOT_DIR via `import.meta.url`, so passing the
absolute script path to `node` is sufficient — `CLAUDE_PLUGIN_ROOT` does
not need to be set.

#### 4b. Run the review

Change working directory to the selected worktree, then invoke the
companion. `{{ARGUMENTS}}` passes any flags (e.g. `--model opus`,
`--wait`, `--background`) through unchanged.

```bash
cd {selected_path}
node "{resolved_companion_path}" review "{{ARGUMENTS}}"
```

Return the script's stdout **verbatim** — do not paraphrase, summarize, or
add commentary. This matches `/codex:review`'s contract.

If `{{ARGUMENTS}}` includes `--background`, run via `Bash(..., run_in_background: true)`
and tell the user: "Codex review started in the background. Check `/codex:status` for progress."

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

1. **5f counter update + advisory check** — increment `rounds_per_region:`
   ledger for each region touched; emit the diminishing-returns advisory if
   `cumulative = N + 1`.
2. **5d-i sibling-identification question** — `AskUserQuestion` to confirm
   whether the PR is a port / parallel hotfix / A/B implementation.
3. **5a classify findings** — fact-modifying vs structural vs stylistic.
4. **5b verify premises** — falsify each fact-modifying finding's premise
   before applying.
5. **5c flip detection during apply** — scan ledger for `applied:` /
   `rejected:` collisions before each edit.
6. **5d-ii / 5d-iii sibling cross-check + propose** — only when 5d-i
   identified a sibling.
7. **5e commit-message trailer** — `Premise-Verified:` trailer on the
   committed edit.

#### 5a. Classify each finding

| Type | Examples | Premise check required |
|------|----------|------------------------|
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
|--------------|---------------------|
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
global CLAUDE.md "External-Surface Write Requires Falsification".

#### 5c. Flip detection — halt A→B→A oscillation

Maintain a per-session ledger across all rounds in the same session.
The ledger has **four record shapes** — `applied`/`rejected` (flip
detection input), `sibling-applied` (Step 5d cross-check),
`rounds_per_region` (Step 5f diminishing-returns) — all must be tracked
because a finding rejected in round N can re-appear in round N+M and
would otherwise look novel:

```
applied:          {file}:{line-or-region} | round={N} | {value-before} → {value-after}
rejected:         {file}:{line-or-region} | round={N} | {value-before} → {value-after} | reason: {falsifying evidence}
sibling-applied:  {sibling-repo}#{PR-or-branch} | round={N} | finding={brief-label} | result={same defect | different | does not apply}
rounds_per_region: {file}:{region} | round={N} | cumulative={C}
```

Before applying any new edit, scan records whose prefix token is exactly
**`applied:`** or **`rejected:`** (NOT `sibling-applied:` or
`rounds_per_region:`) in the ledger.
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
|---|---|
| PR body keywords | `Companion`, `Refs #N`, `Mirror of #M`, `Port of`, `Parallel` |
| Commit message citations | References to a sibling PR number (`#N`) in the commit body |
| `git worktree list` | Two conceptually-paired branches (e.g., same issue prefix, `*-shell` / `*-python`) |

If no sibling is identified (user confirms "No", no auto-detect signal fires), skip 5d entirely.

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
|---|---|
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

#### 5f. Diminishing-returns advisory — rounds-per-region counter

Repeated rounds on the same file region are a signal that the upstream
surface enumeration is incomplete. This sub-step tracks how many rounds
have touched each region and surfaces a non-blocking advisory when the
count reaches the configured threshold.

##### Region label

For each finding, derive a **region label** using the nearest enclosing
context in the source file:

| File type | Region label |
|-----------|-------------|
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

Read the threshold `N` from the environment at the **start of each round**
(during the counter-update step above). The read mechanism follows the same
convention as other `PRAXIS_*` env vars in this codebase — a Bash
parameter expansion with a default:

```bash
N=${PRAXIS_DIMINISHING_RETURNS_N:-4}
```

In Python hook contexts, the equivalent is
`int(os.environ.get("PRAXIS_DIMINISHING_RETURNS_N", "4"))` (consistent with
the `os.environ.get("PRAXIS_EXTERNAL_WRITE_STRICT")` pattern at
`hooks/external-write-falsify-check.py:579` and the
`os.environ.get("PRAXIS_AUTHOR_EXEMPT_STRICT")` pattern at
`hooks/external-write-falsify-check.py:592`).

**Mid-session change semantics**: the env var is read fresh at each round's
counter-update step; it is not cached at session start. If
`PRAXIS_DIMINISHING_RETURNS_N` changes mid-session (e.g., changed from 4 to 2
between round 3 and round 4), the new value takes effect from round 4 onward.
Prior rounds use the value that was in effect at their own round start — there
is no retroactive adjustment to already-recorded ledger entries.

When `cumulative` reaches `N + 1` (i.e., the session is starting its
`(N+1)`-th round on the same region), surface the following advisory
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
`N+3`, …) for the same region — emit it exactly once at `cumulative = N+1`.

##### Interaction with flip detection (5c)

The rounds-per-region counter increments independently of flip detection.
A flip halt (5c) does not reset or suppress the counter. If a flip is
halted mid-round, the `rounds_per_region:` entry for that round is still
recorded (counter increments) but the advisory suppression rule still
applies (emit only at `cumulative = N+1`, not again on later rounds).

## Error Handling

| Situation | Action |
|-----------|--------|
| PR state is CLOSED or MERGED | ABORT: "PR is {state} — review aborted. Re-open or target a different PR." |
| `git worktree list` fails (not a git repo) | Abort: "git worktree list 실패 — git 저장소인지 확인하세요." |
| All worktrees are bare | Treat as Case A (single effective target) using cwd |
| User selects "취소" | Abort silently with one-line message |
| `installed_plugins.json` missing or codex entry absent | Offer alternatives via `AskUserQuestion` (Step 4a) |
| Resolved `codex-companion.mjs` path does not exist | Offer alternatives via `AskUserQuestion` (Step 4a) |
| Premise check (Step 5b) disproves a finding | Skip the edit; reply to Codex with the falsifying evidence |
| Flip detected (Step 5c) | Halt; surface both rounds to the user; do not apply either side without explicit direction |
| Sibling identified but branch/repo not accessible locally | Skip 5d for that sibling; record `sibling-applied: ... \| result=inaccessible` in ledger; warn user to check out the branch |
| Sibling auto-detected but user confirms "not a port" | Skip 5d entirely; no ledger entry needed |
| `PRAXIS_DIMINISHING_RETURNS_N` is set but not a positive integer | Use default (4); do not error |
| Region label cannot be determined (binary file, empty file) | Use the file path alone as the region label |

## Example Flow

```
user: /codex-review-wrap

[Step 1] git worktree list result:
  0: /Users/dev/project/laplace-dev-hub       (main)
  1: /Users/dev/project-wt/windmill-hub-1539  (issue-1539-windmill-runner)

[Step 2] AskUserQuestion →
  "어느 worktree 를 review 할까요?"
  0: /Users/dev/project/laplace-dev-hub (main)
  1: /Users/dev/project-wt/windmill-hub-1539 (issue-1539-windmill-runner)

user selects: 1

[Step 3] Review target: /Users/dev/project-wt/windmill-hub-1539 (branch: issue-1539-windmill-runner)
  ⚠ cwd (/Users/dev/project/laplace-dev-hub) ≠ review target

[Step 4] cd /Users/dev/project-wt/windmill-hub-1539
         → node {install_path}/scripts/codex-companion.mjs review

[Step 5 — Round 1 — counter update (5f, first action)]:
  ledger: rounds_per_region: query.sql:filter_clause | round=1 | cumulative=1
  ledger: rounds_per_region: cli.sh:parse_prompt      | round=1 | cumulative=1
  (cumulative ≤ N=4 → no advisory emitted yet)

[Step 5 — Round 1 — sibling check (5d-i)]: AskUserQuestion fired:
  User: "이 PR은 praxis#199 (shell 버전)의 Python port입니다."
  → sibling identified: praxis#199 on branch issue-199-hook-shell

[Step 5 — Round 1 — classify + verify (5a → 5b)] Codex returned 3 findings:
  - F1: rename `query()` → `run_query()`           [structural — apply directly]
  - F2: change WHERE col_a = 1 → col_b = 1         [fact-modifying — verify column exists]
  - F3: drop the `--state all` flag                [fact-modifying — verify CLI accepts the value]
  Verify F2: DESCRIBE my_table → col_b not present
    → ledger: rejected: query.sql:L42 | round=1 | col_a → col_b | reason: col_b absent in DESCRIBE
  Verify F3: gh search issues --help → --state accepts only {open, closed}
    → apply; ledger: applied: cli.sh:L10 | round=1 | "--state all" → "--state open"
  Commit F3 with trailer:  Premise-Verified: gh search issues --help (excerpt)

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

[Step 5 — Round 2] Codex now re-suggests changing WHERE col_a = 1 → col_b = 1
  Scan ledger: rejected entry on query.sql:L42 with same A → B transition exists
  → flip fires (re-proposal of rejected); halt and surface to user

[Step 5 — Round 2 alt] Codex now suggests "--state open" → "--state all"
  Scan ledger: applied entry on cli.sh:L10 reverses → flip fires (applied flip); halt

[Step 5f — Diminishing-returns example] PRAXIS_DIMINISHING_RETURNS_N=4 (default)
  Rounds 1–4: counter increments silently
    ledger: rounds_per_region: cli.sh:parse_prompt | round=1 | cumulative=1
    ledger: rounds_per_region: cli.sh:parse_prompt | round=2 | cumulative=2
    ledger: rounds_per_region: cli.sh:parse_prompt | round=3 | cumulative=3
    ledger: rounds_per_region: cli.sh:parse_prompt | round=4 | cumulative=4
  Round 5 (cumulative = N+1 = 5): advisory emitted once, then 5a continues normally
    ledger: rounds_per_region: cli.sh:parse_prompt | round=5 | cumulative=5
    Advisory: this is round 5 on cli.sh:parse_prompt. Findings to date suggest
    the underlying surface enumeration may be incomplete. Consider pausing
    to re-enumerate cases up-front before continuing.
  Round 6+: counter still increments, advisory NOT re-emitted
    ledger: rounds_per_region: cli.sh:parse_prompt | round=6 | cumulative=6
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
