# Retrospect appendices

Supplementary material for [`../SKILL.md`](../SKILL.md). This file holds the
longer rationalization catalog, red-flag list, quick-reference table, failure
matrix summary, and integration notes.

## Rationalization prevention

Do not bypass a mandatory step because the task feels small, safe, or familiar.

Invalid rationalizations include:

- "3-line fix"
- "low blast radius"
- "I already know this rule"
- "the agent or CI will catch it"

When you catch yourself thinking that way, run the step instead of arguing past
it. Only the user can grant an exemption.

## Red Flags — STOP

The following are structural stop signals:

- skipping Stage 1.5 because MEMORY.md is "small enough"
- skipping the tool census because no tool visibly failed
- silent dismissal of a `user_correction` or `self_correction` candidate
- jumping to Stage 3 without reading the linked reference for the active stage
- a behavioral-only finding whose own root-cause text points at tool/workflow
  or spec-gap causes
- silent retain / silent drop of an unrunnable carried-finding probe
- any applied-on-branch or deployed-on-branch claim without a live probe

## Quick Reference

| Stage | Key activity | Success criteria |
|-------|--------------|-----------------|
| Stage 1 | Load rule files and scan questions | categories and questions are explicit |
| Stage 1.5 | Detect-only MEMORY.md hygiene scan | cursor honored; findings emitted or documented skip trail |
| Stage 2 | Symmetric pre-scan + analysis | root causes, categories, and audit trails are complete |
| Stage 2.5 | Gate suite | gates 1-6 have explicit verdicts |
| Stage 3 | Canonical report + approval | `Stage 2 caveats:` and `Falsification:` lines are present where required |
| Stage 4 | Approved execution + verification | artifact exists and verification result is reported |

## Error handling summary

| Stage | Failure | Action |
|-------|---------|--------|
| Stage 1 | missing rule file | continue with defaults; flag it |
| Stage 1.5 | MEMORY.md index inaccessible | emit hygiene skipped trail and continue |
| Stage 1.5 | cursor corrupt | reset bounded batch and log the reset |
| Stage 2 | transcript unreachable | emit the relevant skipped trail line and use the allowed fallback |
| Stage 2.5 | gate still fails after 2 re-entries | surface the gate-specific override prompt |
| Stage 3 | falsification gate cannot clear the premise | drop ranking and ask with open premise |
| Stage 3 | required caveat line missing | block emission and return to Stage 2 / 2.5 |
| Stage 4 | write / issue / hook action fails | report the failure; never silently drop it |

## Integration notes

- The Stop hook parses the distribution card and unified findings table
- Post-compaction sessions also require the `retrospect:transcript_receipt`
  fence
- When compaction (or a chunked/bounded rule or transcript read) leaves the
  transcript or tool census partial, record that limit on the Stage 3
  `Stage 2 caveats:` line (e.g. `tool census partial: post-compaction salient
  window only`) instead of implying full coverage — a partial scan reported as
  complete is itself a concealment
- Stage 4 action procedures and artifact verification live in
  [`stage4-execution.md`](stage4-execution.md)
- Worked examples live in [`report-template.md`](report-template.md)
