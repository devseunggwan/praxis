# Stage 1 / 1.5 / 2 / 2.7 procedures (retrospect)

Detailed procedures for the early stages of
[`../SKILL.md`](../SKILL.md). `SKILL.md` holds the execution spine and routing;
this file holds the full Stage 1, Stage 1.5, Stage 2, and Stage 2.7 rules.

## Stage 1: Load Calibration Standard

Before scanning the conversation:

1. Read global and project rule files.
   - Global: `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/CLAUDE.md`
   - Project: `AGENTS.md` in cwd
   - **Large rule files — chunked load (MUST).** Rule files, and on some hosts
     this skill's own packaged `SKILL.md`, can be long (a single-file `SKILL.md`
     has exceeded ~1500 lines on the Codex host). Loading one with a single broad
     `rg` or whole-file read can truncate the output, silently dropping the rules
     at the tail and calibrating against a partial standard. Instead:
     - Read by section/heading or in bounded line ranges (`sed -n`, or a `Read`
       with `offset`+`limit`) — one Stage's worth at a time, not a whole-file dump.
     - When a broad `rg` is unavoidable, cap the match count (a low output limit
       on the host's grep tool, or `--max-count` for `rg`) and page through
       sections rather than emitting every match at once.
     - If any rule read comes back truncated, re-read the missing range before
       calibrating — never proceed on a partial rule set.
2. Identify the rule categories you are scanning against.
   - workflow discipline
   - evidence-based delivery
   - atomic commits / PR lifecycle
   - mandatory testing
   - code review before commit
   - error recovery before asking
   - communication conventions
3. Turn each category into a calibration question.
   - Example: "Did this session violate Planning Before Implementation?"

If a rule file is missing, proceed with defaults and flag it in the report.

## Stage 1.5: MEMORY.md Hygiene Pass (Detect-only)

Stage 1.5 runs unconditionally between Stage 1 and Stage 2. It detects latent
MEMORY.md defects that do not show up as session friction.

### Scope and cursor

- Scan up to **5 feedback files per invocation**
- Persist progress at `.omc/state/retrospect-hygiene-cursor.json`
- Cursor fields:
  - last successful timestamp
  - `scanned_recent_batch`
  - `next_batch_pointer`
  - pairwise-proof trail for signals 2 and 3
  - carry-forward `note`

### Cursor read mandate (entry)

If the cursor exists, before scanning any feedback file:

1. Use `next_batch_pointer` as the schedule
2. Exclude `scanned_recent_batch`
3. Carry the `note` field forward into Stage 3 input

Silent skip of the cursor read is a Red Flag.

### Cursor write mandate (exit — read-modify-write union under concurrency, MUST)

Before persisting the cursor:

1. **re-read** the on-disk cursor
2. Compare it with the entry snapshot
   - the write-time timestamp is the primary concurrent-advance discriminator:
     if the on-disk timestamp differs from the entry-read timestamp, another
     session persisted, even when `next_batch_pointer` and
     `scanned_recent_batch` still match
   - do not rely on pointer/list comparison alone; same-batch concurrent
     writes can otherwise clobber sibling `note` and scan-trail updates
3. If no concurrent advance happened, write normally
4. If **Concurrent advance detected**, **UNION-merge the on-disk cursor**
   instead of overwriting:
   - **concatenate the on-disk `note` lines** with this cycle's lines, then
     de-duplicate byte-identical lines
   - union the per-batch scan trail
   - advance `next_batch_pointer` to the **FURTHER of the two pointers**
   - union `scanned_recent_batch`
5. Record the Stage 3 trail line:
   `Cursor write: concurrent advance detected — union-merged note (...)`

This is the cross-session complement of the single-session falsification gate.
**Silent overwrite of a concurrently-advanced cursor** is a Red Flag.

### Detection signals

1. **Stale reference**
   - Verify file paths, CLI flags, line citations, and skill/hook references
   - Use a second probe path before concluding stale
2. **Contradiction**
   - Pairwise compare entries with overlapping triggers
   - Record pairwise proof even when matches = 0
3. **Merge candidate**
   - Group entries that restate the same root-cause family
   - Record pairwise proof even when matches = 0
4. **Size threshold**
   - Check index line count, byte size, and observed host truncation warnings
   - Fire when line count >= `PRAXIS_RETROSPECT_INDEX_LINE_THRESHOLD`
     (default `200`)
   - Fire when byte size >= `PRAXIS_RETROSPECT_INDEX_BYTE_THRESHOLD`
     (default `24000`)
   - New writes must enforce `PRAXIS_RETROSPECT_INDEX_LINE_CAP`
     (default `150`) per index line
   - malformed threshold env vars silently fall back to the documented defaults
   - Subsignals:
     - promoted-pointer compression
     - backlink-zero archive
     - new-entry length cap

   Link-detection helper (link-convention-agnostic): promoted-pointer
   compression and backlink-zero archive both consume one shared scanner. The
   scanner recognizes a reference from index entry A to artifact B when B appears
   in A's body in any of these forms:

   - `[[wikilink]]` whose target basename equals B
   - Markdown link `[text](path)` where `path` resolves to B (relative or
     absolute), with allowed extensions `.md`, `.txt`, `.json`, `.py`, `.sh`
   - Bare basename mention: a single-token `<B>.<ext>` (minimum 4 chars, allowed
     extensions above) outside code fences

   A scan failure on one form falls through to the next. Only when all three
   forms fail to match does an entry count as backlink-zero.
5. **Missing oracle annotation**
   - Detect numeric measurements whose oracle / cohort / unit is not recorded

### Output and carry-forward rules

- Emit findings as `category: memory_hygiene`
- `Tool Layer` is `—` by default; use `skill` only when a skill/hook artifact
  is directly implicated
- Set provisional `Proposed Actions` by signal, finalized by Stage 2.5:
  - stale reference -> `memory`, or `claude_md_draft` when the stale citation
    is itself a rule line that needs updating
  - contradiction -> `memory + issue` so both entries are surfaced for human
    resolution before merge
  - merge candidate -> `memory` through the Stage 4 merge path
  - size threshold / promoted-pointer compression -> `memory`
  - backlink-zero archive -> `issue` for human-confirmed archive movement
  - new-entry length cap -> `memory` with the cap as a write-time guard
  - missing oracle annotation -> `memory`
- Stage 1.5 is detect-only: no inline MEMORY.md mutation
- If Stage 2 finds zero friction but Stage 1.5 produced findings, use the
  hygiene-only path instead of the early exit

### Hygiene Scan Trail

When Stage 1.5 runs, Stage 3 must carry:

- current-cycle summary
- carried findings from the cursor `note`
- falsification result when the current cycle contradicts a carried finding

If a carried finding's probe is unrunnable:

- record `probe=UNRUNNABLE (...)`
- retain by default
- auto-drop only for repeated rule-conflict cases per the bounded-drop rule

Bounded-drop rule:

- a rule-conflict carried finding may be auto-dropped only after
  `PRAXIS_RETROSPECT_UNRUNNABLE_DROP_CYCLES` consecutive cycles (default `3`)
  with `probe=UNRUNNABLE` and no fresh supporting evidence
- access-blocked-only findings are never auto-dropped; retain them until the
  artifact becomes readable or the user explicitly clears the finding
- every drop must leave a Stage 3 trail line with the carried finding id,
  cycle count, and reason

### Failure modes

- MEMORY.md inaccessible -> emit `<!-- retrospect:hygiene_skipped: index not accessible -->`
- cursor corrupt -> reset to a bounded batch and log the reset
- unreadable file inside batch -> continue with the remaining files
- index unreadable for size measurement -> skip signal 4 only

## Stage 2: Analyze Conversation

Stage 2 starts with a symmetric pre-scan before any agent calls.

### Pre-scan lanes

Scope window: scan the current session boundary when available; otherwise scan
the last 50 turns. Post-compaction sessions must use the available transcript
jsonl when readable and emit the Stage 3 transcript receipt trail. The same
scope window applies to `tool_census`, `user_correction`, and
`self_correction`, so their counts and early-exit decisions share one oracle.

1. **`friction_events`**
   - up to 5 friction events feeding the friction analysis path
2. **`successful_patterns`**
   - up to 3 successful patterns
   - each needs `pattern`, `evidence`, and `reinforce_action`
3. **`tool_census`**
   - deterministic inventory of every tool used in scope
   - row shape:
     - `tool_name`
     - `layer`
     - `call_count`
     - `error_count`
     - `retry_count`
     - `workaround_marker`
     - `surfaced_in_friction`
     - `signal`
   - `error_count` per row must be derived from reading each `is_error: true`
     result's own body for that tool — not inferred from the tool's typical
     failure mode or from the count of a prior row. See the `is_error` census
     cross-check under "Transcript-derived trail requirements" below.
   - `retry_count` is failure-driven only: same tool + parameters re-issued
     after a failed, errored, or unexpected-output prior call. Intentional
     polling, repeated status reads, and background-output re-reads (for example
     `write_stdin` polls, progress polling, or reading a growing log) are not
     retries and must not increment this counter even when consecutive.
4. **`user_correction`**
   - deterministic marker scan of user turns
   - candidate classes and recall-floor markers:
     - `negation`: `아니`, `그거 아니야`, `하지 마`, `그거 말고`,
       `no`, `don't`, `stop`
     - `redirect`: `~라니까`, `~하라고 했잖아`, `그게 아니라`,
       `내 말은`, `다시`, `I said X`, `not that`
     - `mismatch`: `~하라니까 ~하고 있네?`, `왜 X 안 하고 Y?`,
       `왜 ~했어`, `that's not what I asked`
   - Python `re.\w` / `\b` is Unicode-aware; for ASCII boundaries in mixed
     Korean/English text use explicit ASCII guards such as
     `(?<![a-z])foo(?![a-z])`
   - cap priority: when more than 5 friction events exist, genuine `user_correction` events rank first for the retained `friction_events` slots; a narrative-only event yields before a user correction is dropped
   - false-positive drops must be recorded in the ledger
5. **`self_correction`**
   - deterministic signature scan of agent self-correction sequences
   - signature families: `oracle-mismatch`, `wrong-target`, `basis-change`
   - a candidate requires all three:
     1. same intent
     2. changed oracle, target, or basis
     3. prior result was wrong or superseded, not merely errored
   - identical command re-runs are retries; broadening a correct-but-incomplete
     search is progressive refinement, not self-correction
   - cap priority: genuine self-corrections rank after genuine
     `user_correction` events and before narrative-only events for retained
     `friction_events` slots
   - false-positive drops must be recorded in the ledger

### Self-incrimination pass (anti-suppression, MUST)

The five pre-scan lanes are deterministic and capped, so they structurally miss
the friction the analyzing agent is most motivated to bury: its OWN failures
that left no `user_correction` marker and do not fit the `self_correction`
three-part signature. The analyzing context is the same context that committed
those failures, so selection bias suppresses them silently — this is the
"retrospect hides the painful parts" failure this pass exists to close.

Run this pass AFTER the five lanes and BEFORE root-cause clustering. It is
unconditional whenever the friction path is non-empty. Answer the adversarial
question in writing before continuing:

> What is the single most embarrassing agent-caused friction this session that I
> am tempted to leave out of `friction_events` or to soften? Why am I tempted?

Rules:

- The worst agent-caused friction this pass surfaces occupies a **guaranteed
  slot that does NOT consume the 5-event `friction_events` cap** — it is
  surfaced in addition to the capped lanes, never in competition with them. A
  full cap is not a reason to drop it.
- Signature non-match is not grounds for exclusion. A failure that fails the
  `self_correction` 3-part signature, or carries no `user_correction` marker
  (a *silent* failure the user never caught), is still in scope here; this pass
  exists precisely to catch that class.
- Preserve the painful framing verbatim. Do not relabel an agent behavioral
  failure as neutral "tool friction" or "one-off" to lower its severity, and do
  not soften the wording.
- Record every item you considered omitting or softening — with the reason — in
  the Stage 3 `retrospect:suppression_ledger` fence (see
  [`stage3-reporting.md`](stage3-reporting.md)). Considering-then-dropping is
  allowed only with an explicit `disposition: justified-drop` reason; a silent
  drop is a Red Flag.

**Limits — what is mechanizable vs what is not (issue #715):** Only the *format*
of this pass is hook-enforceable: the `retrospect:suppression_ledger` and
`retrospect:distribution` fences must be present with their required lines, and
the `## Actions Executed` marker must appear. The *semantic* judgment this pass turns
on — "is this actually the single most painful item, and did a positive learning
quietly take its slot?" — is **not** hook-enforceable; it stays a self-feedback
step even when wrapped in a hook. Four consecutive recurrences of the
"hid the painful part" failure, plus the self-correction literature (intrinsic
self-correction without an external signal does not reliably work and can degrade
output; the bottleneck is error *detection*, not correction), establish that
adding more self-check prose to this family does not enforce it. Do not keep
refining self-prompts here — the leverage is the **external** signal (the
externalized critic tier below, or the user's "you hid it?" probe), not a smarter
self-instruction. That leverage is mechanized, not just described: **Gate-8c**
in `retrospect-mix-check` blocks a `critic_diff: not-run` whenever the live
transcript carries more than one *near-unambiguous* user-correction marker (a
deliberately low-false-positive set — issue #722 — far tighter than Gate-8b's,
biased toward a false-negative so a clean retrospect is never hard-blocked;
ambiguous everyday-conversational corrections remain this prose layer's job, not
the gate's). Within that narrowed set the external tier cannot be self-skipped in
exactly the case (a genuine, clearly-stated user correction occurred) its
predicate is satisfied. The hook cannot judge *which* failure is worst; it can
and does force the external auditor to run when the transcript proves real
user-visible friction.

### Externalized critic re-scan tier (conditional, issue #702)

The self-incrimination pass still runs inside the same context that made the
mistake. That closes silent omission only as far as the analyzing context can
make itself be honest; it does not break same-context anchoring. The
externalized critic tier adds a separate READ-ONLY context only when the extra
cost is justified.

Run this tier AFTER cluster fold-back and the MEMORY repeat scan, and BEFORE
action assignment. At that point the predicate's `repeat=true` /
`effective_repeat` inputs exist, but no provisional actions have been selected
yet.

#### Tier predicate

Run the externalized critic re-scan when all of these are true:

1. the friction path is non-empty
2. the transcript or scoped session context is readable enough to brief a
   READ-ONLY critic
3. either:
   - at least one `friction_events` candidate is agent-caused and
     user-visible-impact (wasted user cycles, wrong external artifact, or user
     correction required)
   - at least one candidate has `repeat=true` or an effective repeat signal from
     the MEMORY scan / cluster fold-back
   - `PRAXIS_RETROSPECT_CRITIC=1`

Do not spawn a critic on the 0-friction path. Do not spawn it merely because a
clean ledger is required. The tier is a selection-bias breaker, not a replacement
for Gate-8b's deterministic laundering floor.

If the predicate is false, record the skipped outcome for Stage 3:

```markdown
critic_diff: not-run | reason: tier predicate false (<reason>)
```

If the transcript is unreadable, record:

```markdown
critic_diff: not-run | reason: transcript unreadable
```

#### Critic brief

When the predicate fires, invoke a separate READ-ONLY critic context. Prefer the
project's normal critic subagent when available; otherwise use the closest
available read-only review/debate agent. The brief must include:

- transcript / scope window identifier
- the five pre-scan lanes
- the self-incrimination pass output, carried **verbatim** — do not paraphrase,
  summarize, or soften the candidate #1-damage wording before it reaches the
  critic. A sanitized brief turns the critic into an echo chamber (issue #715):
  it can only ratify framing it was never allowed to see.
- the current self-selected friction set
- the instruction: "Find agent-caused failures the analyzing agent might
  minimize, omit, or soften, and judge **how bad the worst one is** — did a
  positive learning take the #1-damage slot? Record that severity judgment in
  the candidate's `why_self_serving_bias_risk` cell; do not ask 'is this a good
  learning'. Do not propose fixes. Do not mutate files or external state."

The critic's job is not to re-run Stage 2. It produces a narrow diff oracle:

```markdown
critic_candidates:
- <candidate> | evidence: <turn/tool/output> | why_self_serving_bias_risk: <reason>
```

#### Diff handling

Compare `critic_candidates` against the self-selected friction set and
suppression ledger:

- If every critic candidate is already surfaced, record:
  `critic_diff: none | checked: <N> candidate(s)`
- If a critic-only candidate is valid, surface it as a friction event or ledger
  row. A valid critic-only agent-caused, user-visible-impact failure cannot be
  downgraded to `note only`.
- If a critic-only candidate is not surfaced, record an explicit
  `not-suppressed: <reason>` line in the suppression ledger. Valid reasons are
  duplicate, non-agent-caused, evidence-insufficient, or outside-scope.

A silent drop of a critic-only candidate is a Red Flag. The Stage 3
`critic_diff:` ledger line is mandatory whenever this tier is evaluated, even
when it did not run.

### Categorization

Every emitted finding must carry at least one category:

- `behavioral`
- `tool`
- `workflow`
- `spec-gap`
- `memory_hygiene`
- `output_quality`

Rules:

- `tool` requires `Tool Layer = mcp|cli|builtin|skill`
- `behavioral`-only findings use `Tool Layer = —`
- `workflow`, `spec-gap`, and `memory_hygiene` may optionally use `skill`
  when the skill itself is part of the problem surface

### Core Stage 2 sequence

1. Run the pre-scan
2. Emit the Pre-scan Checklist and dismissed-candidate ledger inputs
3. Run the self-incrimination pass
4. Run tracer / analyst on the friction path
5. Derive root causes and cluster overlaps
6. Run the MEMORY.md repeat scan, then the conditional externalized critic
   re-scan tier when triggered
7. Assign provisional actions
8. Run Stage 2.7 when triggered

#### Action assignment (step 7)

Auto-assign provisional `Proposed Actions` from the escalation ladder below.
Use `event.effective_repeat` from the MEMORY scan as the repeat signal; for
singleton events it equals `event.repeat_count`. Apply Repeat-based rows first,
then Category-default rows when `category[]` makes memory-only inappropriate
even on first occurrence.

For clusters with the same root cause in one session, fold the cluster back
into repeat escalation before assigning actions:

- compute `cluster_repeat_count = max(repeat_count) + (cluster_event_count - 1)`
- propagate that value to every cluster member as `event.effective_repeat`
- use the propagated `event.effective_repeat` for all Repeat-based rows, so a
  new member of a repeated cluster does not stay at `repeat_count=0`

| Condition | Action Type | Rationale |
| ----------- | ------------- | ----------- |
| New pattern with structural root cause | `memory` | First occurrence; capture for future reference |
| Repeat in MEMORY.md, 1-2x | `issue` | Memory alone failed; needs systemic fix |
| Repeat 3x+ | `hook_code` or `skill_idea` | Multiple memory entries indicate an enforcement gap |
| Missing rule, new | `claude_md_draft` | No rule exists for this pattern |
| Missing rule + repeat | `claude_md_draft`, `issue` | Missing rule caused recurrence |
| Tool friction from step 4b | `upstream_feedback` | Tool improvement belongs in the tool's backing repo |
| One-off situational mistake | `note only` | No persistent action needed |
| Category default — `tool` | `upstream_feedback` (compound with `memory` only when behavioral co-label exists) | Tool defect is not a Claude behavior issue |
| Category default — `workflow` | `hook_code` or `skill_idea` | Skipped workflow steps need structural enforcement |
| Category default — `spec-gap` | `claude_md_draft` or `skill_idea` | Rule gaps are filled with rules or skill changes |
| Category default — `behavioral` | `memory` | Only category where memory-alone is acceptable |

Distinguish "new pattern" from "one-off mistake" by recurrence likelihood:
structural root causes are new patterns; typo/context-loss edge cases are
one-off. When uncertain, default to `memory`.

**Severity-honesty floor (MUST).** A friction event that is BOTH agent-caused
AND user-visible-impact (wasted user cycles, a wrong external artifact shipped,
or the user had to correct it) may NOT be self-classified as a one-off
situational mistake and routed to `note only`. It must surface as at least
`memory` with its painful framing preserved verbatim. The `note only` escape and
the "one-off" carve-out above apply only to genuinely user-invisible,
non-recurring edge cases — never as a downgrade path for the self-incrimination
pass's worst-failure slot.

If `repeat=true`, `Proposed Actions` must not be `memory` alone. The only
escape hatch is `repeat=true AND resolved=true`, where `note only` is allowed
with a report sentence confirming the existing resolution still works.

For any row whose `Proposed Actions` contains `upstream_feedback` or `issue`,
the `Rationale` cell must include a literal `backing_repo: <owner>/<repo>` line,
embedded with `<br>` separators when rendered in the Stage 3 table. The Stop
hook treats both actions as routed actions, and Stage 4 re-reads this declaration
as the routing decision before creating either upstream feedback or project issue
artifacts.

Resolve `backing_repo:` from these sources, in order:

1. Plugin manifest `repository` field
2. MCP server git remote
3. Dotfiles backing repo via symlink chain
4. Project `AGENTS.md` feature-to-repo mapping

If the layer is unresolvable (`builtin`, or no upstream reachable per Stage 4
Action 4's routing table), remove `upstream_feedback` from the action set and
re-derive the remaining action from the ladder. Do not emit a placeholder
`backing_repo`. If the layer is ambiguous, keep `upstream_feedback` only after
surfacing the repo choice to the user at Stage 2; that user-selected repo
becomes the declared `backing_repo:`.

#### Tool-friction promotion pass (step 4b)

Run this pass after the behavioral / workflow friction scan and before root
cause clustering. It promotes objective tool defects into `category: tool`
findings so they do not disappear behind behavioral memories.

Tool layers to scan:

| Layer | Examples | Friction signals |
| ------- | ---------- | ----------------- |
| `mcp` | custom or third-party MCP servers | slow response, missing field, schema mismatch, timeout |
| `cli` | `gh`, `kubectl`, `git`, project CLIs | missing flag/option, undocumented behavior, workaround needed |
| `builtin` | Read/Edit/Bash/Grep/Glob, Agent, hooks | environmental constraint, permission issue, output truncation |
| `skill` | praxis / OMC / project-specific skills and subagents | stage boundary unclear, trigger mismatch, prompt defect, wrong routing |

For each tool-friction event or promoted census row, record:

- `tool_name`
- `layer`
- `origin`: `friction` or `census`
- `friction_type`: missing feature, design defect, documentation gap,
  performance issue, or integration mismatch
- `evidence`: event citation, or census row `signal` plus call site
- `expected_behavior`
- `proposed_fix_direction`

Promotion rule: if the census row carries objective evidence (`error_count > 0`,
`retry_count >= PRAXIS_RETROSPECT_CENSUS_RETRY_THRESHOLD` (default `2`),
`workaround_marker`, or a non-empty `signal`) and it is not
already represented by an equivalent step-4 finding, promote it as a tool
finding. Cap promoted census-origin findings at
`PRAXIS_RETROSPECT_CENSUS_FINDING_CAP` (default `5`) and record non-promoted
rows in the `retrospect:tool_census` trail.

Dedup rule: if a single moment has both a rule violation and a tool defect,
record both findings. The step-4 finding addresses what the agent should have
done differently; the step-4b finding addresses what the tool should improve.
The two findings may have different action types.

Backing-repo signal: for any promoted tool finding likely to become
`upstream_feedback`, carry the source needed by Stage 2 step 7 / Stage 4 Action
4 to resolve `backing_repo:`. If the layer is unresolvable, do not fabricate a
placeholder; let step 7 remove or re-derive `upstream_feedback`.

#### Mandatory tracer / analyst calls

TRACER + ANALYST CALLS ARE MANDATORY, NOT OPTIONAL when
`friction_events` is non-empty:

- before invoking the tracer, run one cheap artifact probe when the friction
  event names a directly readable file, hook, skill, CLI, or command output
- include the probe result in the tracer brief as `probed artifact:`; if the
  artifact cannot be probed, include `probe skipped:` with the reason
- the artifact probe informs the tracer; it does not replace the mandatory
  tracer call
- invoke the tracer first on the friction path and preserve its hypothesis,
  evidence, and confidence in Stage 2 caveats
- invoke the analyst after tracer output to cluster overlap and identify
  shared root-cause families
- do not replace either call with a local summary; if an invocation is
  unavailable, record the failure explicitly and carry the caveat into Stage 3

#### MEMORY.md repeat-scan producer contract

For every finding whose provisional or final action includes `memory`, Stage 2
must produce the fields consumed by Stage 2.5 Gate-5:

- `memory_scan.scanned`
- `memory_scan.candidates_reviewed`
- `repeat`
- `repeat_count`

The scan must inspect the 2-hop candidate set before deciding repeat status:

1. identify candidate memories/rules from the finding's pattern, tool layer,
   action type, and root-cause wording
2. open the candidate files or explicitly record why they were unreachable
3. set `repeat=true` only when a prior matching pattern exists
4. set `repeat_count` to the number of matching prior occurrences reviewed
5. set `memory_scan.candidates_reviewed` to the concrete candidate list, not a
   self-authored claim that the scan happened

If the index or a candidate file is inaccessible, still emit
`memory_scan.scanned=true`, record the blocker in
`memory_scan.candidates_reviewed`, and treat the finding as a new pattern for
action assignment unless another reachable candidate proves a repeat. This
keeps Gate-5 passable while preserving the accessibility caveat. Do not
silently omit the `memory_scan` field.

### Early-exit rules

The "No patterns found" path is allowed only when all are true:

- `friction_events` is empty
- the Pre-scan Checklist has no unresolved violations
- Stage 1.5 produced no hygiene findings
- the census promoted no findings
- Stage 2.7 produced no findings

Otherwise continue through Stage 2.5 and Stage 3.

### Transcript-derived trail requirements

For post-compaction sessions with a readable JSONL transcript, enumerate the
transcript before analysis instead of relying only on the compaction summary:

- scan `is_error` tool results and record `is_error_count`
  - **Read each `is_error: true` result's own body text individually — do not
    infer its failure category from the tool name, from a preceding result in
    the same scan, or from an assumed pattern (MUST).** A retrospect analyzing
    its own source session committed exactly this failure: it category-assumed
    `is_error` bodies instead of reading each one, and under-counted as a
    result — the self-referential "recursive-retrospect anti-pattern" this
    pass exists to catch (issue #720).
  - This individual read is what populates the Stage 3
    `retrospect:is_error_enum` block (see
    [`stage3-reporting.md`](stage3-reporting.md) and
    [`report-template.md`](report-template.md)), whose presence Gate-7
    hard-blocks Stage 3 on. **Gate-7 only checks that the block is non-empty
    (>= 1 disposition row) — it does NOT check that the row count matches
    `is_error_count`.** A single category-assumed row ("these are all
    transient timeouts, dismiss") satisfies Gate-7's structural check while
    still under-enumerating. Treat row-count parity with `is_error_count` as a
    Stage 2 MUST even though the hook cannot verify it: do not close the enum
    block until every counted `is_error` has its own disposition row.
  - **is_error / tool_census cross-check (MUST).** `is_error_count` must
    equal the sum of `error_count` across all `tool_census` rows (pre-scan
    lane 3) for the same scope window. A mismatch means one of the two scans
    skipped a result — re-scan the narrower one before proceeding to Stage
    2.5. Record the reconciled `error_count` values directly in the existing
    `retrospect:tool_census` trail (no separate fence needed); do not silently
    average or split the difference between the two counts.
- scan explicit user corrections and record `user_correction_count`
- scan self-corrections / retries and record `self_correction_count`
- if the transcript is unreadable, emit
  `retrospect:transcript_receipt_skipped: transcript unreachable` and carry the
  caveat into Stage 3

Stage 3 must carry the appropriate trail/ledger fences:

- `retrospect:tool_census`
- `retrospect:user_correction`
- `retrospect:self_correction`
- `retrospect:transcript_receipt` for post-compaction sessions

These trails are audit surfaces. Silent omission is a Red Flag.

## Stage 2.7: Adaptive Post-hoc Artifact Audit

Stage 2.7 is the artifact-quality audit path.

### Trigger surfaces

Run it when the session transcript contains any of:

- PR / issue / comment writes
- Slack or Notion writes
- approved external-write events

### Audit focus

1. **PR and issue quality**
   - missing evidence
   - mergeability / blocking-comment blind spots
2. **Sub-agent substance**
   - shallow or empty downstream output being used as if it were verified work
3. **External-write evidence**
   - hypotheses or unverified claims in comments / messages / reports

### Output

- emit `category: output_quality`
- set provisional `Proposed Actions` by sub-audit, finalized by Stage 2.5:
  - PR mergeability -> `memory` for repeated approval-gate drift, or
    `claude_md_draft` when a user-facing workflow rule needs strengthening
  - sub-agent substance -> `skill_idea` for prompt/role contract gaps, or
    `memory` when the issue is a repeated local verification habit
  - external-comment evidence -> `memory` for repeated claim hygiene drift,
    `claude_md_draft` for rule gaps, or `hook_code` when an existing hook can
    enforce the missing evidence trace
- include the artifact-audit trail line when 0 triggers exist:
  `<!-- retrospect:audit_skipped: no artifacts -->`
- if the transcript is unreadable, emit the unreadable variant instead of
  inventing a result

### Required sub-audits

When at least one trigger surface exists, run all three sub-audits. A sub-audit
may emit zero findings, but it must not be silently skipped.

#### Sub-audit 1: PR mergeability

For each PR touched by `gh pr {create,edit,merge,comment}` in this session, run:

```bash
gh pr view <number-or-url> --json reviewRequests,reviews,state,mergeable,mergedAt
```

Emit an `output_quality` finding with `Tool Layer: cli` when any of these hold:

- `state == "CLOSED"` and `mergedAt == null`
- two or more `CHANGES_REQUESTED` review submissions are present
- `mergeable == "CONFLICTING"`

Filter on `CHANGES_REQUESTED`; raw review count alone does not measure revision
cost.

#### Sub-audit 2: sub-agent substance

For every sub-agent / reviewer result used as evidence downstream, inspect the
actual returned text. Emit an `output_quality` finding with `Tool Layer: skill`
when the result is stream-killed, empty, shorter than 100 meaningful characters,
or contains no concrete file / symbol / command evidence while the parent
session treated it as verified work.

#### Sub-audit 3: external-comment evidence

For each external/shared-surface comment or message write, scan the emitted body
for hypothesis markers such as `might`, `could be`, `probably`, `seems like`,
`가능성`, `추정`, or equivalent unverified framing. If the same body lacks a
verification trace, falsification trace, or cited command/API output, emit an
`output_quality` finding. This applies to create and update variants because an
edited comment can introduce or preserve the same defect.
