# Stage 2.5 action-distribution audit (retrospect)

Detailed gate procedures for [`../SKILL.md`](../SKILL.md) Stage 2.5.
`SKILL.md` names the gates; this file defines how each gate passes, fails, and
hands off to Stage 3.

## Gate sequence

Run the gates in this order for the current finding set:

1. Gate-1 — categorical integrity
2. Gate-2 — memory-only rationale schema
3. Gate-3 — evidence robustness for compound actions
4. Gate-4 — external-repo pre-check for `upstream_feedback`
5. Gate-5 — `memory_scan` completeness
6. Gate-6 — oracle-match completeness

Each finding gets at most 2 gate-driven re-entries before you must surface the
documented override prompt to the user.

### Override prompt after 2 re-entries

When any gate still fails after the per-finding re-entry cap, stop the loop and
surface the gate-specific 3-way prompt. Do not invent a fourth "continue
looping" path.

- Gate-1 / Gate-2 / Gate-3:
  `"Finding #N의 Gate-X가 2회 재진입 후에도 통과되지 않습니다. 어떻게 진행할까요? [a] rationale 직접 입력 / [b] action 직접 지정 / [c] note only 강등."`
- Gate-5:
  `"Finding #N의 Gate-5 (memory_scan 필드 누락)가 통과되지 않습니다. 어떻게 진행할까요? [a] MEMORY.md index를 직접 읽고 memory_scan 필드를 입력 / [b] action을 직접 지정 / [c] 이 finding은 note only로 강등."`
- Gate-6:
  `"Finding #N의 Gate-6 (oracle_match 누락 또는 불일치)가 통과되지 않습니다. 어떻게 진행할까요? [a] 같은 oracle로 재측정 / [b] 별도 cohort-shift finding으로 전환하고 stored value 유지 / [c] 이 finding은 note only로 강등."`

Record the selected option and the user's supplied rationale in the Stage 3
trail before proceeding.

## Gate-1: categorical integrity

Purpose: block label backsliding that hides a tool/workflow/spec-gap defect as
`behavioral` or `memory` only.

Pass when:

- every finding has a valid `category[]`
- any finding with `tool` has `Tool Layer`
- the chosen categories match the actual root cause

Fail when:

- a required category is missing
- a `tool` finding has `Tool Layer = —`
- a behavioral-only label contradicts the finding's own root-cause evidence
- any finding whose `category[]` intersects `tool`, `workflow`, or `spec-gap`
  has `Proposed Actions = memory` as a single, non-compound action

Behavioral-only safeguard (all findings): if all findings are labeled
`behavioral` only, run a final keyword sanity check on the original pre-scan
signal text. If any signal contains tool/workflow indicators such as `gh`,
`kubectl`, `MCP`, `--state`, `permission denied`, `timeout`, `--help`, or
`flag`, surface the keyword set to the user and require explicit confirmation
before Stage 3. If the user confirms, log the confirmation with the keyword set.

Per-finding behavioral-label falsification: for each finding labeled
`behavioral` only, scan its root-cause text for spec-gap/tool/workflow signal
keywords: `probe`, `scan scope`, `skill`, `hook`, `missing`, `gap`, `scope`,
`미구현`, `누락`, `flag`, `--<opt>`, `spec`, `rule absent`. If any signal is
present, the finding must either add the honest second category or include
`behavioral-label-justify: <why this is NOT spec-gap/tool/workflow despite the keyword>`.
If neither is present, return the finding to Gate-1 / Stage 2 categorization for
label re-evaluation; do not let action choice drive category narrowing.

## Gate-2: memory-only rationale schema

Purpose: prevent unsupported `memory`-only resolutions.

This gate applies only to findings whose `Proposed Actions` is exactly
`memory`.

Accept either:

- **Schema A**: exactly 5 lines of `not <action>: <reason>` covering every
  non-memory action type
- **Schema B**: 1-2 lines of `not-others: <dimension tags>`

Generic one-line rationales do not pass Gate-2.

## Gate-3: evidence robustness for compound actions

Purpose: make sure dual-action plans are actually justified. For each finding
with exactly two `Proposed Actions`, evaluate all three sub-conditions below.
Single-action findings skip Gate-3.

### Gate-3(a): per-action evidence pointer

Each action must cite at least one explicit friction-event observation that
supports that action. Citations live in the Rationale cell as a sub-bullet or
inline `(observed: <event-id or one-line>)` reference. If an action is present
only because a category default filled the table, return it to Stage 2 step 7
(action assignment) and add observation citations before re-entering Gate-3.

### Gate-3(b): sibling decision-coupling

If action B's outcome decides a question that action A already presupposes, the
pair is decision-coupled and both actions cannot be executed together. Keep the
stronger-evidence action and demote the weaker action to a Stage 3 trigger
condition line: `Finding #N: file <action> when <observation predicate>`.

Example: a `claude_md_draft` that asserts "behavior X is intentional" coupled
with `upstream_feedback` asking whether X is intentional is contradictory. Keep
one; demote the other to a trigger.

### Gate-3(c): single-observation downgrade

For each action backed by exactly one observation and `repeat=false`, downgrade
one tier:

| Original action | Downgraded to | Rationale |
|-----------------|---------------|-----------|
| `upstream_feedback` | `memory` | One observation against an external party does not justify upstream-write cost |
| `issue` | `memory` | One observation without repeat does not justify systemic tracking |
| `hook_code` | `skill_idea` | One observation does not justify enforcement code |
| `claude_md_draft` | `memory` | One-observation rule changes risk over-fitting |
| `skill_idea` | `memory` | One observation does not justify a skill artifact |
| `memory` | no downgrade | Already the lowest tier |

If the resulting action set becomes memory-only while the finding still carries
`tool`, `workflow`, or `spec-gap`, re-run Gate-1. The downgrade itself does not
consume a re-entry; the resulting Gate-1 re-evaluation does.

### Gate-3 outcomes

- all applicable sub-conditions pass -> keep both actions and emit
  `gate_3_verdict: PASS`
- any sub-condition changes the action set -> apply the downgrade/demotion and
  re-enter the affected gate path
- no compound findings -> `gate_3_verdict: NA`
- unresolved violation after the per-finding re-entry cap -> surface the
  documented 3-way user override prompt

## Gate-4: external-repo authorization pre-check

Purpose: classify `upstream_feedback` findings before Stage 3 ranking so that
external writes cannot slip through as if they were ordinary internal issues.

For each `upstream_feedback` finding:

1. parse `backing_repo: <owner>/<repo>` from the Rationale cell
2. resolve the own-org allowlist
3. classify the owner as internal or external
4. annotate external findings with the literal Stage 4 warning prefix:
   `⚠ EXTERNAL: per-action approval required at Stage 4`
5. emit `gate_4_verdict`

Own-org allowlist resolution, in priority order:

1. `PRAXIS_OWN_ORGS` as comma-separated handles
2. `gh api user --jq .login` as the single own handle
3. conservative fallback: treat all `backing_repo` owners as external

Classification: extract `owner` from `backing_repo: <owner>/<repo>` and compare
case-insensitively against the resolved allowlist. If owner is absent from the
allowlist, mark the finding `external=true`. If the `backing_repo:` declaration
is absent, Gate-4 skips that finding here; Stage 4 Action 4 step 0's
missing-declaration abort is the downstream enforcement.

For external findings, the literal warning prefix must appear in the Stage 3
Rationale cell for the `upstream_feedback (external)` row. Stage 4 scans that
exact string before creating/commenting/editing in an external repository; a
paraphrase disables the per-action approval gate. The same row must also carry
`backing_repo: <owner>/<repo>` so Stage 4 can route the approval decision to the
right repository boundary.

Verdicts:

- `PASS` -> zero external findings; all `upstream_feedback` rows are internal
- `WARN` -> at least one external finding exists, or conservative fallback was
  required; Stage 4 must require per-action approval for external rows
- `NA` -> no `upstream_feedback` findings

## Gate-5: memory-scan completeness

Purpose: prove that the MEMORY.md repeat scan actually happened for any finding
that wants a `memory` action.

For every memory-action finding, require:

- `memory_scan.scanned == true`
- `memory_scan.candidates_reviewed` present
- `repeat` present
- `repeat_count` present

If the scan could not run because the index is inaccessible, record that in the
`memory_scan` field rather than silently skipping the field.

## Gate-6: oracle-match completeness

Purpose: prevent overwriting stored numeric values with a probe that measured a
different quantity.

For every stored-value correction finding, require:

- `oracle_match` field present
- `oracle_match: true` when the action would invalidate or overwrite the stored
  value

Producer procedure:

1. Read the originating entry's stored oracle: matching basis, cohort, and unit.
2. Re-probe with the same matching basis / cohort / unit before treating the
   stored value as stale or wrong.
3. Record `oracle_match: true` only when the falsification probe used that same
   oracle.
4. Treat a different-oracle probe as a cohort-shift observation, not as
   falsification of the stored value.

If the originating entry did not record its oracle, defer falsification and
route the work to an annotation update rather than fabricating a correction.

## Distribution card

On pass, Stage 2.5 hands Stage 3 the canonical distribution card:

```markdown
<!-- retrospect:distribution begin -->
- memory: {n}
- issue: {n}
- claude_md_draft: {n}
- skill_idea: {n}
- hook_code: {n}
- upstream_feedback: {n}
- memory_hygiene: {n}
- output_quality: {n}
- gate_1_verdict: {PASS|FAIL|NA}
- gate_2_verdict: {PASS|FAIL|NA}
- gate_3_verdict: {PASS|FAIL|NA}
- gate_4_verdict: {PASS|WARN|NA}
- gate_5_verdict: {PASS|FAIL|NA}
- gate_6_verdict: {PASS|FAIL|NA}
<!-- retrospect:distribution end -->
```

Semantics:

- `memory_hygiene` and `output_quality` are category counts, not action keys
- `gate_1_verdict`, `gate_2_verdict`, and `gate_4_verdict` are structurally
  relevant to the Stop-hook path
- `gate_3_verdict`, `gate_5_verdict`, and `gate_6_verdict` are emitted for
  procedural audit even when the Stop hook does not parse them

## Re-entry and override

If a finding still fails a gate after 2 re-entries:

- do not keep looping
- surface the documented override prompt for that gate
- record the user's selection in the report trail
