---
name: retrospect
description: >
  Session retrospect — analyze current Claude Code session against global `~/.claude/CLAUDE.md` rules,
  identify friction patterns and root causes, propose context-appropriate improvement
  actions, then execute after user approval.
  Triggers on "retrospect", "what went wrong", "session review",
  "session improvement", "what was the issue", "improve".
verified-against-runtime: true
runtime-verified-at: 2026-06-16
runtime-verified-note: "tests/test_retrospect_falsify_recommended.sh + test_retrospect_routing.sh + retrospect hook suites — Stage 3 AskUserQuestion recommendations require falsification traces, and the active-marker/report-fence contract stays aligned."
---

# Retrospect

## Overview

Repeated friction wastes cycles across sessions. Unexamined pain stays unresolved.

**Core principle:** ALWAYS analyze root cause before proposing any action.
Symptom-level fixes miss the underlying pattern.

**Pipeline:** `Load -> Hygiene -> Analyze -> Audit -> Report/Approve -> Execute`

**Delegates to:** `oh-my-claudecode:tracer` agent (causal pattern analysis),
`oh-my-claudecode:analyst` agent (pattern clustering) — invoked via
`Agent(subagent_type="oh-my-claudecode:...")`

**Reference map:**

- Stage 1 / 1.5 / 2 / 2.7 details: [`references/stage1-2-analysis.md`](references/stage1-2-analysis.md)
- Stage 2.5 gate procedures: [`references/stage2.5-audit.md`](references/stage2.5-audit.md)
- Stage 3 output contract and approval flow: [`references/stage3-reporting.md`](references/stage3-reporting.md)
- Stage 3 worked examples: [`references/report-template.md`](references/report-template.md)
- Stage 4 execution procedures: [`references/stage4-execution.md`](references/stage4-execution.md)
- Rationalization catalog / red flags / full failure matrix: [`references/appendices.md`](references/appendices.md)

## The Iron Law

```
NO ACTION WITHOUT ROOT CAUSE ANALYSIS FIRST.
PATTERN ≠ ROOT CAUSE. SYMPTOM ≠ ROOT CAUSE.
REPEATED PATTERN + MEMORY = FAILED REMEDY. ESCALATE.
TRACER + ANALYST CALLS ARE MANDATORY, NOT OPTIONAL.
```

If you have not completed Stage 2 (Analyze), you cannot propose actions.

## When to Use

Use at the END of a working session to extract learnings:

- Session had repeated tool retries or direction changes
- User gave corrections mid-session
- A task took significantly longer than expected
- Workflow steps were skipped or out of order
- User expressed frustration or redirected multiple times

Use this especially when the same mistake happened more than once, when you
feel "I should have done that differently", or when a new workflow pattern
emerged that is not captured anywhere.

## The Stages

You MUST complete each stage before proceeding to the next.

1. **Stage 1: Load Calibration Standard**
Read the governing rule files and turn them into concrete scan questions.

2. **Stage 1.5: MEMORY.md Hygiene Pass (Detect-only)**
Run the bounded hygiene scan before the conversation analysis. It is
unconditional.

3. **Stage 2: Analyze Conversation**
Run the symmetric pre-scan lanes, derive root causes, and assign preliminary
actions.

4. **Stage 2.5: Action Distribution Audit**
Run the gate suite before any Stage 3 output.

5. **Stage 3: Report + Approval**
Emit the canonical report shape, carry caveats forward, run the pre-output
falsification gate, then ask for per-finding approval.

6. **Stage 4: Execute**
Run only approved actions, then verify the resulting artifacts.

### Stage 1: Load Calibration Standard

Before scanning the conversation:

1. Read global and project rule files — load large files (a packaged `SKILL.md`
   can exceed ~1500 lines) in bounded section/line-range chunks, never a single
   broad read that can truncate and silently drop tail rules (see
   [`references/stage1-2-analysis.md`](references/stage1-2-analysis.md)).
2. Identify the rule categories you will scan against.
3. Turn each category into a calibration question.

Use [`references/stage1-2-analysis.md`](references/stage1-2-analysis.md)
before doing the actual scan. That reference is the full procedure for Stage 1,
Stage 1.5, Stage 2, and Stage 2.7.

### Stage 1.5: MEMORY.md Hygiene Pass (Detect-only)

Stage 1.5 runs unconditionally between Stage 1 and Stage 2. It surfaces latent
MEMORY.md defects that the session-scoped friction scan cannot see.

**Mandatory shape:**

- Scan up to **5 feedback files per invocation**
- Persist progress at `.omc/state/retrospect-hygiene-cursor.json`
- Emit findings with `category: memory_hygiene`
- Never mutate MEMORY.md here — Stage 1.5 is detect-only

**Five detection signals:**

1. stale reference
2. contradiction
3. merge candidate
4. size threshold
5. missing oracle annotation

**Cursor read mandate (entry — MUST run first):**

- Use `next_batch_pointer` to choose the batch start
- Exclude `scanned_recent_batch`
- Carry forward the cursor `note` into Stage 3 input

**Cursor write mandate (exit — read-modify-write union under concurrency, MUST).**
This is the cross-session complement of the single-session falsification gate.

- **re-read** the on-disk cursor before persisting
- If **Concurrent advance detected**, do not overwrite
- **UNION-merge the on-disk cursor** instead of plain write
- **concatenate the on-disk `note` lines** with this cycle's lines
- Advance `next_batch_pointer` to the **FURTHER of the two pointers**
- Record the Stage 3 trail line:
  `concurrent advance detected — union-merged note`
- **Silent overwrite of a concurrently-advanced cursor** is a Red Flag

If Stage 1.5 finds hygiene defects but Stage 2 finds zero friction events, take
the hygiene-only path rather than the "No patterns found" early exit.

Read the full rules, signal definitions, failure modes, and carry-forward logic
in [`references/stage1-2-analysis.md`](references/stage1-2-analysis.md).

### Stage 2: Analyze Conversation

Run the symmetric pre-scan before agent calls. The full mechanics live in
[`references/stage1-2-analysis.md`](references/stage1-2-analysis.md).

**Pre-scan lanes (all deterministic inputs, then judgment where required):**

- `friction_events`
- `successful_patterns`
- `tool_census`
- `user_correction`
- `self_correction`

**Mandatory categorization:**

Every emitted finding must carry at least one of:

- `behavioral`
- `tool`
- `workflow`
- `spec-gap`
- `memory_hygiene`
- `output_quality`

If `tool` is present, `Tool Layer` must be one of `mcp`, `cli`, `builtin`, or
`skill`.

**Self-incrimination pass (anti-suppression, MUST):** after the five lanes and
before clustering, run the self-incrimination pass — name the single worst
agent-caused friction you are tempted to omit or soften. It occupies a
guaranteed slot that does NOT consume the 5-event `friction_events` cap, and a
failure with no `user_correction` marker or no `self_correction` signature match
is still in scope. An agent-caused, user-visible-impact failure may not be
self-downgraded to `note only` (severity-honesty floor). The result is recorded
in the Stage 3 `retrospect:suppression_ledger` fence. Judging which item is most
painful is a self-feedback step and does **not** mechanize (issue #715) — the
externalized critic tier below is the enforcing external signal, not a smarter
self-check. What *is* mechanized: Gate-8c (retrospect-mix-check) blocks a
`critic_diff: not-run` when the live transcript carries user corrections, so the
external tier cannot be self-skipped in the conceal-prone case. Full rules in
[`references/stage1-2-analysis.md`](references/stage1-2-analysis.md).

**Externalized critic re-scan tier (conditional, issue #702):** after MEMORY
repeat / cluster signals are available, run a READ-ONLY external critic scan
when the friction path is non-empty and the tier predicate in
[`references/stage1-2-analysis.md`](references/stage1-2-analysis.md) fires.
Record the outcome in the Stage 3 `critic_diff:` ledger line whether the tier
ran or was skipped.

**Core flow:**

1. Pre-scan the transcript and produce the five lanes
2. Run the self-incrimination pass (anti-suppression)
3. Run tracer / analyst where the friction path requires them
4. Derive root causes rather than symptoms
5. Cluster overlaps
6. Run the MEMORY.md scan, then the conditional externalized critic re-scan
   tier when triggered
7. Assign preliminary actions
8. Run Stage 2.7 when the artifact-audit triggers are present
9. Hand findings to Stage 2.5

**Early exit only when all are true:**

- zero friction events
- zero unresolved Pre-scan Checklist violations
- zero promoted census findings
- zero Stage 1.5 hygiene findings
- zero Stage 2.7 audit findings

**Compaction rule:** post-compaction sessions require the
`retrospect:transcript_receipt` fence in Stage 3.

### Stage 2.7: Adaptive Post-hoc Artifact Audit

Stage 2.7 is part of Stage 2 and fires only when the session includes artifact
writes such as PR / issue / Slack / Notion actions.

It exists to catch silent-pass quality defects that a friction-only scan can
miss. Use the full trigger list, audit surfaces, and trail rules in
[`references/stage1-2-analysis.md`](references/stage1-2-analysis.md).

### Stage 2.5: Action Distribution Audit

Stage 2.5 is the last gate before Stage 3 output. Run the complete gate suite
from [`references/stage2.5-audit.md`](references/stage2.5-audit.md).

**Required gates:**

- Gate-1: categorical integrity
- Gate-2: memory-only rationale schema
- Gate-3: evidence robustness for compound actions
- Gate-4: external-repo authorization pre-check for `upstream_feedback`
- Gate-5: `memory_scan` completeness
- Gate-6: oracle-match completeness for stored-value corrections

**Output:** a distribution card with action counts plus gate verdicts.

If a gate still fails after 2 per-finding re-entries, surface the documented
override prompt rather than silently continuing.

### Stage 3: Report + Approval

Stage 3 must follow the canonical report shape from
[`references/stage3-reporting.md`](references/stage3-reporting.md).
Worked examples live in
[`references/report-template.md`](references/report-template.md).

**Output order:**

1. `## Retrospect Report`
2. audit fences (`pre_scan_checklist`, `dismissed_candidates`, and the required
   transcript-derived ledgers)
3. `<!-- retrospect:suppression_ledger begin --> ... end -->` (mandatory on
   every path — records the self-incrimination pass)
4. `<!-- retrospect:distribution begin --> ... end -->`
5. unified findings table

**Per-finding plan must state:**

1. what will be created
2. why this action type
3. how it will be verified
4. `Stage 2 caveats: ...` when applicable
5. `Falsification: ...`

#### Pre-Output Falsification Gate (AskUserQuestion)

This gate fires immediately before each `AskUserQuestion` emitted by Stage 3.

**Trigger detection:**

- **Literal `(Recommended)` suffix** on any option label
- confidence-anchoring phrasing such as `safer`, `natural fit`, `안전한`,
  `자연스러운`, `추천`, `default to`

**Mandatory question (internal, never omitted):**

> If this proposal's premise is wrong, what observation should be missing from
> the current evidence? Is that observation actually missing?

**Carry forward Stage 2 caveats:**

- `tracer confidence: LOW|MED`
- `single observation`
- `alternative root cause not ruled out`
- `Gate-3 (c) downgrade applied`
- `Gate-3 (b) sibling demoted to trigger`
- `repeat=true, resolved=true (escape hatch)`
- `analyst clustered with #N`

**Outcome rules:**

- **Premise survives** -> `(Recommended)` allowed
- **Premise fails** -> `(Recommended)` **DISALLOWED**
- **Falsification step not run** -> `(Recommended)` **DISALLOWED** and
  **ESCALATE to user with open premise**

Every ranked option needs a `Falsification:` trace line immediately below
`Stage 2 caveats:` (or in its place when there are no caveats).

**gate-suppressed example:** when a single observation leaves an alternative
root cause unresolved, surface the option unranked rather than marking it
`(Recommended)`.

**Approval flow:**

For each finding, ask:

- `✅ Execute now`
- `⏭ Skip`
- `🕐 Defer (create note only)`

Do not execute any action until the user approves it.

### Stage 4: Execute

`note only` items require no execution.

Before executing any approved action, read
[`references/stage4-execution.md`](references/stage4-execution.md). It holds
the full procedures, prompt variants, repo-resolution rules, and artifact
verification matrix.

**Action map:**

| # | Action | Reference-owned gate |
| --- | -------- | ---------------------- |
| 1 | MEMORY.md feedback | duplicate-check before create; `hookable` / `hookKeywords` decision |
| 2 | GitHub issue | project issue workflow |
| 3 | global `~/.claude/CLAUDE.md` draft | target detection + staging + approval |
| 4 | upstream feedback | backing-repo verification + external approval gate; Stage 3 rationale uses `backing_repo: <resolved_backing_repo>` and praxis-internal routing examples use `<resolved-praxis-repo>` placeholders until live resolution |
| 5 | skill idea note | local artifact write |
| 6 | hook code | branch/worktree guard + registration prompt |

## Rationalization Prevention

Do not rationalize past any mandatory gate.

- "small fix", "I already know", "the agent will infer it", and "CI will catch
  it" are not exemptions
- When you notice yourself trying to bypass a mandatory step, run the step
- If you truly need an exemption, ask the user explicitly

The larger rationalization catalog lives in
[`references/appendices.md`](references/appendices.md).

## Red Flags — STOP

STOP and return to the owning stage when any of these happen:

- Acting from a stage summary without reading its linked reference
- An `AskUserQuestion` recommendation without an accompanying `Falsification:` trace line
- Stage 3 ranking that contradicts Stage 2 caveats
- Omitting the `Stage 2 caveats:` line when caveats apply
- Omitting the `retrospect:suppression_ledger` fence, or silently dropping /
  softening an agent-caused failure the self-incrimination pass surfaced
- Silent overwrite of a concurrently-advanced cursor

The exhaustive Red Flag catalog is in
[`references/appendices.md`](references/appendices.md).

## Quick Reference

| Stage | Minimum check |
| ------- | --------------- |
| Stage 1 | Load rule files and calibration questions |
| Stage 1.5 | Cursor schedule honored; detect-only hygiene scan executed |
| Stage 2 | Five pre-scan lanes + self-incrimination pass + categories + early-exit carve-outs checked |
| Stage 2.5 | Gates 1-6 evaluated before any Stage 3 output |
| Stage 3 | `suppression_ledger` fence emitted; Pre-Output Falsification Gate before each `AskUserQuestion` |
| Stage 4 | Reference procedure read; artifact verified after execution |

Full tables and stage-by-stage failure handling are in
[`references/appendices.md`](references/appendices.md).

## Error Handling

| Stage | Failure | Action |
| ------- | --------- | -------- |
| Stage 1.5 | MEMORY.md index inaccessible | skip hygiene with the documented trail line; continue to Stage 2 |
| Stage 2 | transcript unreachable | emit the documented `*_skipped` trail line and continue with the allowed fallback |
| Stage 3 | Pre-Output Falsification Gate triggered but premise cannot be falsified | drop ranking, surface the option unranked, and ask with open premise |
| Stage 3 | finding lacks Stage 2 caveats line despite required caveats | block Stage 3 emission and return to Stage 2 / 2.5 |
| Stage 4 | artifact write fails | report the failure; do not silently drop the action |

The complete failure matrix is in
[`references/appendices.md`](references/appendices.md).

## Integration

- The Stop hook parses the Stage 3 distribution card and unified findings table
- Post-compaction Stage 3 output also needs the `retrospect:transcript_receipt`
  fence
- Any schema drift must be co-updated with the hook and tests called out in
  [`references/stage3-reporting.md`](references/stage3-reporting.md)
