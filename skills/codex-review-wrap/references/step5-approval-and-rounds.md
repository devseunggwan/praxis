# Step 5 decisions and the round boundary: 5e / 5i / 5j (codex-review-wrap)

Detailed procedure for [`../SKILL.md`](../SKILL.md) Step 5 sub-steps 5e
(commit-message trailer), 5i (per-finding user approval gate), and 5j
(round-continuation gate). Execution order is owned by the spine's
*Execution order* list. Sibling references:
[`step5-premise-verification.md`](step5-premise-verification.md) (5a/5b/5c/5g),
[`step5-siblings-and-regions.md`](step5-siblings-and-regions.md) (5d/5f/5h).

## 5e. Record verification in the commit message

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

## 5i. Per-finding user approval gate <!-- [#861] -->

Steps 5a–5c decide whether a finding is *correct*. Whether it should be
applied **in this PR, now** is a separate judgement that belongs to the
user. This sub-step surfaces that decision explicitly.

**No finding may be edited on the agent's judgement alone.**
There is no auto-apply path — not for stylistic findings, not for
"obviously right" one-liners, not for findings the agent already verified
in 5b. Applying an edit without a recorded `적용` answer is a violation of
this gate, and the briefing-style disclosure ("I applied these, tell me if
wrong") is not a substitute for asking first.

### Scope and ordering

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

### Batching

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

### Question body — required elements

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
  option's `description` (*Self-Falsify Before Recommendation Lock*,
  [`ETHOS.md`](../../../ETHOS.md#rules-praxis-carries); enforced by
  `hooks/advisory-nudge/output-block-falsify-advisory/impl.py`, which does a
  `startswith` check, so a mid-sentence or fenced `Falsified:` does not
  count). The 5b probe usually supplies that line; when it does not, run one
  and cite it.

### Options

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

### Follow-up issues (`후속이슈` answers)

Do **not** create an issue per answer. Collect all `후속이슈` findings of the
round and, once the batch is complete, surface a single implementation-approach
review — scope, target repo(s), expected PR count, verification plan — and
create issues only after the user approves that approach
(*Implementation-approach review before issue creation*,
[`ETHOS.md`](../../../ETHOS.md#rules-praxis-carries)). Append the resulting
URL to each `deferred:` row; leave `issue=pending` if the user declines.

### Relationship to 5d-iii (sibling PRs)

An `적용` answer authorizes the edit **on the current PR only**. The sibling
fix in 5d-iii keeps its own separate approval — approval never transfers
across PRs.

### Cancellation

If the user cancels a batch or gives no answer, stop applying findings for
the round. Report which findings were already applied, which remain
undecided, and end the round without further edits.

## 5j. Round-continuation gate

This sub-step alone governs the round **boundary** rather than the round
interior, and it is the only one that draws an edge back to Step 4.
5a–5i each act on one round's findings; 5j decides whether there is
another round at all.

### Fire condition

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

### Decision table

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

### The question

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

### Re-entry

1. Return to **Step 4**; skip Steps 1–3 — the review target is already
   fixed. The re-entered round is backgrounded like any other, because
   Step 4b is unconditional. That is not a loop this gate has to hold
   open: *When the review completes* already routes a finished background
   round back into Step 5 from the top.
2. **Normalize** the original `{{ARGUMENTS}}` before reuse — strip any
   existing `--scope` / `--base <ref>`. Then append at most **one**
   target-selecting flag, the one condition (b) settled above. Stripping
   first is what keeps the two rules from colliding:
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

### No round cap

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
