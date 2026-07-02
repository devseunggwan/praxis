# Stage 3 reporting contract (retrospect)

Normative Stage 3 contract for [`../SKILL.md`](../SKILL.md). `SKILL.md` keeps
the execution spine; this file defines the canonical Stage 3 report shape, the
approval flow, and the output-level gates.

## Canonical emit order

Stage 3 output MUST emit, in this order:

1. `## Retrospect Report`
2. `retrospect:pre_scan_checklist`
3. `retrospect:dismissed_candidates`
4. Stage 1.5 Hygiene Scan Trail when hygiene scan emitted carried findings,
   concurrent cursor merge, probe falsification, or hygiene skip/size-threshold
   markers
5. transcript-derived trails / ledgers
   - `retrospect:tool_census`
   - `retrospect:user_correction`
   - `retrospect:self_correction`
   - `retrospect:transcript_receipt` in post-compaction sessions
6. Stage 2.7 audit trail or skip marker
   - `<!-- retrospect:audit_skipped: no artifacts -->`
   - `<!-- retrospect:audit_skipped: transcript unreadable -->`
   - per-artifact audit trail lines when triggers were inspected
7. `<!-- retrospect:suppression_ledger begin --> ... end -->` (mandatory on
   every path, including the 0-friction "No patterns found" path)
8. `<!-- retrospect:distribution begin --> ... end -->`
9. unified findings table
10. memory-action evidence blocks for every finding whose proposed action
   includes `memory`

The `memory` distribution count includes both finding rows whose Proposed
Actions include `memory` and `successful_patterns` rows whose
`reinforce_action` is `memory`. A success-pattern memory entry must be surfaced
for approval in the same Stage 3 approval pass before Stage 4 may write it.

## Unified findings table

The table header is fixed:

```markdown
| # | Category | Tool Layer | Pattern | Root Cause | Rule / Gap | Repeat? | Proposed Actions (1~2) | Rationale | Priority |
```

Key column rules:

- `Category` is a comma-separated subset of:
  `behavioral`, `tool`, `workflow`, `spec-gap`, `memory_hygiene`,
  `output_quality`
- `Tool Layer` must be `mcp|cli|builtin|skill|—`

## Memory-Scan Evidence

For every finding whose proposed action includes `memory`, Stage 3 MUST emit an
auditable memory-scan evidence block near the unified findings table:

```markdown
<!-- memory_scan finding #<n>:
  scanned: true
  candidates_reviewed: <concrete paths or unreachable marker>
  repeat: true|false
  repeat_count: <integer>
-->
```

These lines surface the Gate-5 producer fields from Stage 2.5. Keeping them only
in internal reasoning is a Red Flag because reviewers and downstream hooks cannot
verify that the MEMORY.md repeat scan actually ran.

- `Proposed Actions (1~2)` is a subset of:
  `memory`, `issue`, `claude_md_draft`, `skill_idea`, `hook_code`,
  `upstream_feedback`
- any row with `upstream_feedback` or `issue` in `Proposed Actions` must declare
  `backing_repo: <owner>/<repo>` in `Rationale`
- memory-only rows must use the Stage 2.5 Gate-2 rationale schema

## No-pattern audit path

The 0-friction "No patterns found" path still emits the report header, every
audit fence in the canonical emit order, and the distribution card with all
counts = 0 and verdicts = NA. These audit fences are mandatory even when no
finding survives; otherwise dismissed-candidate and transcript-derived audit
trails disappear on the path where they are hardest to reconstruct.

### Enumerate-before-empty-fence

Before emitting an empty `retrospect:dismissed_candidates` fence in the
0-friction path, scan the session transcript for codex/review-bot finding
markers:

- Codex CLI reviewer output blocks
- BugBot comment bodies
- reviewer agent result text
- Cursor review annotations

Every marker match must be inspected for rule-violation content. Any match that
is a workflow/spec/hook rule violation must appear as a ledger line:

```markdown
- {one-line candidate} | reason: {dismissal rationale} | spec_cite: {section name or file:line}
```

An empty `dismissed_candidates` fence is allowed only when this enumeration
returns zero rule-violation matches. Pure style nits or refactor suggestions do
not force a ledger entry.

## Suppression ledger

Every Stage 3 report — including the 0-friction "No patterns found" path — MUST
emit the suppression-ledger fence. It is the report-level record of the Stage 2
self-incrimination pass (see
[`stage1-2-analysis.md`](stage1-2-analysis.md)): the audit surface that makes
self-suppression visible and challengeable instead of silent. Without it, an
omitted or softened agent-caused failure is indistinguishable from a clean
session.

```markdown
<!-- retrospect:suppression_ledger begin -->
- worst_agent_failure: <one-line, verbatim painful framing, no softening> | disposition: surface
- tempted_to_omit: <item> | reason_considered: <why> | disposition: surface|justified-drop
- tempted_to_soften: <item> | original_severity: <X> | softened_to: <Y> | restored: true|false
- self_adversarial: ran | result: <what it surfaced, or "concurred — nothing omitted or softened">
- critic_diff: <none|not-run|candidate list> | reason: <tier result or skip reason>
<!-- retrospect:suppression_ledger end -->
```

Emit the fence as **bare markdown in the report body**, not inside a fenced
code block — the Stop hook strips fenced code before parsing, so a ledger
wrapped in a ```` ```markdown ```` block is invisible to Gate-8 and blocks. The
block above is illustration only.

Rules:

- The `worst_agent_failure:` line and the `self_adversarial:` line are
  REQUIRED. The `critic_diff:` line is REQUIRED whenever the Stage 2
  externalized critic tier is evaluated, even when it did not run. The
  `tempted_to_omit` / `tempted_to_soften` rows appear only when such candidates
  existed.
- The fence is mandatory even on the clean path. When the self-incrimination
  pass found nothing to omit or soften, still emit `worst_agent_failure:` (name
  the single worst thing this session, however minor) with
  `disposition: surface`, and a `self_adversarial: ran` line stating the pass
  ran and concurred. Absence-of-suppression is itself audited.
- `critic_diff:` values:
  - `not-run | reason: tier predicate false (...)` when the friction path is
    empty or the conditional predicate did not fire
  - `not-run | reason: transcript unreadable` when the tier would fire but the
    critic cannot be briefed from readable evidence
  - `none | checked: <N> candidate(s)` when the critic found no unsurfaced
    candidate
  - one or more candidate lines when the critic found valid unsurfaced
    agent-caused failures; each must be surfaced or paired with a
    `not-suppressed: <reason>` ledger row
- Painful framing is preserved verbatim; softening the wording inside the ledger
  defeats its purpose.
- The Stop hook (Gate-8) blocks a Stage 3 report that omits this fence or either
  required line.
- The Stop hook also re-derives cheap deterministic adverse signals from the
  live transcript (`is_error:true`, content-error syntax, and documented
  `user_correction` markers on user turns). When the ledger claims a
  clean/no-failure path while those signals exceed tolerance, Gate-8 blocks as
  ledger laundering.

## Transcript receipt

When the transcript contains a compaction marker, the report must include the
`retrospect:transcript_receipt` fence with real command output.

When `is_error_count > 0`, the receipt must also include a nested
`retrospect:is_error_enum` block enumerating each error with a disposition:

- `promote`
- `note`
- `dismiss`

**Row count must equal `is_error_count` (MUST — issue #720).** Gate-7 only
checks that the block is non-empty; it does not check that every counted
error has its own row. A category-collapsed row ("these N are all the same
kind of timeout, dismiss") passes Gate-7's structural check while still
under-enumerating — read each `is_error` body individually (see
[`stage1-2-analysis.md` → Transcript-derived trail
requirements](stage1-2-analysis.md#transcript-derived-trail-requirements))
and give it its own row rather than assuming its category from a sibling row.

The skipped variant is allowed only when the transcript is genuinely
unreachable.

## Per-finding plan contract

For every non-note-only finding, Stage 3 must explain:

1. what will be created
2. why this action type was chosen
3. how it will be verified
4. `Stage 2 caveats: ...` when caveats apply
5. `Falsification: ...`

### Trigger Conditions (Gate-3 (b) demotions)

If Stage 2.5 Gate-3 detected sibling decision-coupling and demoted the weaker
action, emit one bullet per demoted action after the unified findings table.
This section is for human review only and is not parsed by the Stop hook.

Format: literal `^- Finding #N: file <action> when <observation predicate>$`.

```markdown
- Finding #N: file `<demoted_action>` when `<observation predicate>` (originally proposed alongside `<kept_action>`; demoted because <coupling reason>)
```

If no Gate-3 (b) demotions occurred, omit the section entirely.

### Action selection ladder

Action type baseline comes from the Stage 2 escalation ladder, but Stage 3 must
explicitly evaluate all six action types per non-note-only finding and select
one or two actions.

| Action Type | When to Choose | Skip If |
| ------------- | --------------- | --------- |
| `memory` | New pattern, first occurrence, individual learning | `repeat=true`; memory is blocked |
| `issue` | Systemic fix needed; repeat pattern at 1-2 prior occurrences | One-off mistake, purely local insight |
| `claude_md_draft` | Explicit cross-project rule gap exists | Existing rule already covers this pattern |
| `skill_idea` | Repeat pattern needs enforcement and manual recall is insufficient | Single memo is sufficient |
| `hook_code` | Repeat pattern at 3+ occurrences requiring automated enforcement | Fewer than 3 repeats |
| `upstream_feedback` | Tool or feature-level defect belongs upstream | Pure rule violation with no tool-level root cause |

Selection matrix:

| Axis | Signal -> Action |
| ------ | ------------------ |
| Repeat count | 0x -> `memory`; 1-2x -> `issue`; 3x+ -> `skill_idea` or `hook_code` |
| Scope | Cross-project -> `claude_md_draft`; single-project -> `memory` |
| Gap type | Rule violated -> reinforce; rule absent -> draft; no enforcement -> `skill_idea` |

Repeat-count is the highest-priority axis. When `repeat=true`, scope and gap
type cannot override back to `memory`; use them only to choose additional
actions alongside the repeat-count result.

Compound action is the default for HIGH-priority findings. A single `memory`
action is acceptable only when the `Rationale` column explicitly explains why
all other action types were skipped.

## Reinforced patterns

Emit the reinforced-patterns section only when there are
`successful_patterns` rows with `reinforce_action: visualize_only`.

For `successful_patterns` rows with `reinforce_action: memory`, emit an approval
row or prompt item that names the reinforced pattern, its evidence, and the
MEMORY.md entry that Stage 4 would create. Do not write reinforced memory entries
from Stage 4 unless that item was explicitly approved in Stage 3.

Do not emit a placeholder "None." section.

## Pre-Output Falsification Gate (AskUserQuestion)

This gate fires immediately before each `AskUserQuestion`.

### Trigger detection

Any of the following triggers the gate:

- Literal `(Recommended)` suffix
- confidence-anchoring phrasing such as `safer`, `safest`, `natural fit`,
  `natural choice`, `obvious choice`, `clearly`, `recommend`, `default choice`,
  `default to`, `prefer this`, `안전한`, `자연스러운`, `추천`, `당연히`, `분명히`

### Mandatory question

Ask internally:

> If this proposal's premise is wrong, what observation should be missing from
> the current evidence? Is that observation actually missing?

### Caveats that must be read before ranking

- `tracer confidence: LOW|MED`
- `single observation`
- `alternative root cause not ruled out`
- `Gate-3 (c) downgrade applied`
- `Gate-3 (b) sibling demoted to trigger`
- `repeat=true, resolved=true (escape hatch)`
- `analyst clustered with #N`

### Outcomes

- **Premise survives** -> `(Recommended)` allowed
- **Premise fails** -> `(Recommended)` disallowed; surface unranked
- **Falsification step not run** -> `(Recommended)` DISALLOWED; ESCALATE to user with open premise

The final Stage 3 output must include a `Falsification:` line for every ranked
option.

## Red Flags

Stop and return to Stage 2 / Stage 2.5 when any of these are true:

- Emitting `AskUserQuestion` with ranking language without an accompanying `Falsification:` trace line.
- Stage 3 ranking that contradicts Stage 2 caveats.
- Omitting the `Stage 2 caveats:` line when caveats apply.
- Omitting the `retrospect:suppression_ledger` fence, or emitting it without the
  required `worst_agent_failure:`, `self_adversarial:`, and `critic_diff:`
  lines.
- Silently dropping an agent-caused failure the self-incrimination pass surfaced
  (a `justified-drop` needs an explicit reason in the ledger).

## Quick Reference

Pre-Output Falsification Gate before each `AskUserQuestion`; every ranked
option needs `Falsification:` evidence.

## Error Handling

| Failure | Action |
| --- | --- |
| Pre-Output Falsification Gate triggered but premise cannot be falsified | Drop ranking and ask with open premise |
| Finding lacks Stage 2 caveats line despite required caveats | Block Stage 3 emission and return to Stage 2 / 2.5 |

## Approval flow

After the report, ask per finding:

- `✅ Execute now`
- `⏭ Skip`
- `🕐 Defer (create note only)`

Do not run Stage 4 until the user explicitly approves the finding.

## Co-update note

Any schema drift here — including the `retrospect:suppression_ledger` fence
(Gate-8) — must be co-updated with:

- `hooks/completion-verify/retrospect-mix-check/impl.sh`
- `tests/hooks/completion-verify/test_retrospect_mix_check.sh`
- `tests/fixtures/retrospect-synth-*.jsonl` + `.expected.json`

## Examples

Use [`report-template.md`](report-template.md) for the consolidated template and
worked examples when composing the final Stage 3 output.

Example classification: `gate-suppressed` means the `(Recommended)` label was
removed because the disconfirming observation was present or the premise was not
falsified.
