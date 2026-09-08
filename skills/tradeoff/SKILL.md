---
name: tradeoff
description: >
  Score the options at a decision fork on four cost axes — reversibility,
  blast radius, cost-if-wrong, process cost — with a provenance grade on every
  cell so a guess never reads as a measurement. Report-only: no totals, no
  ranking arithmetic, no external-surface writes.
when_to_use: >
  Triggers on "tradeoff", "trade-off", "비용 분석", "cost of this choice".
  Do NOT activate on "기존 결정의 부채 조회" (that is `debt`) or on "이슈 착수
  가치 판정" (the Issue Review anchor's Judgement table owns that).
verified-against-runtime: true
runtime-verified-at: 2026-09-08
runtime-verified-note: "ugrep 7.8.4 (macOS 26.5.2, invoked as grep) — the Case 2 evidence block is a transcription of a live run: the sunset-field probe exits 1 over skills/ and 0 over hooks/."
---

# Tradeoff

## Overview

A decision fork produces two or more options, and the vocabulary for costing
them already exists across this repo — `Scope-risk:` and `Confidence:`
trailers, the `Blast Radius` clause, the SRP clause's "buys a review round, a
merge ordering, a tracker entry, and a reviewer's context reload — each, every
time". What is missing is a surface that puts them side by side at the moment
of choosing. `Decision-Point Briefing` covers scope, risk and reversibility
but fires only "when you stop to ask", so a fork the agent walks straight
through is costed by nothing.

This skill fills that gap and nothing else. It enumerates the options, scores
each on four axes, and marks where every score came from.

**Core principle:** a convention is not an option. When the lookup returns a
precedent, that precedent is the answer and no table is built.

## When to Use

- An implementation fork with two or more real candidates — a pair (hook vs
  skill, inline vs extracted) or a wider set (enforce in a hook / in a skill /
  in CI / in review only)
- Deciding whether to act on a review finding, and whether it blocks

Not for deciding whether a piece of work is worth starting — the Issue Review
anchor's `Judgement` table (Need / Alternative / Urgency) owns that, and a
second surface for it would only drift from the first.

## Process

### Step 1: Enumerate the options — three probes, then stop

Take the options the user named, then run exactly these three probes. Three,
then stop: this is the termination condition, not a search to be extended.

1. **Existing code** — does something already do this? `grep`, `Read`, a
   sibling implementation.
2. **Convention or precedent** — has a rule, a sibling skill, or a prior
   decision already settled this? Read `AGENTS.md`, `ETHOS.md`, the sibling
   `SKILL.md` files.
3. **Do nothing** — what happens if none of the options is taken? Add it to the
   option list; it is a real candidate, not a formality.

**Probe 2 terminates the skill.** If it returns a precedent that covers the
fork, print the precedent with its file and line, say which option it selects,
and stop. Do not build a table. A rule is a trade-off the team already made,
and re-scoring it here would be re-opening it — which `ETHOS.md` reserves to
the team, not to the runtime. Reporting what the lookup returned is not
re-opening it.

### Step 2: Score the four axes

One ordinal value per cell — `low`, `moderate`, or `high`. Nothing else, and
no numbers: a number invites addition, and an added column of self-authored
values reads as a measurement.

| Axis | The question | Where the vocabulary comes from |
| ------ | -------------- | -------------------------------- |
| Reversibility | If this is wrong, how expensive is undoing it? | "Irreversible or shared state — never proceed on your own" |
| Blast radius | How far does a failure reach, including outside the system? | The `Blast Radius` clause; `Scope-risk:` trailer |
| Cost-if-wrong | What is paid if the judgement turns out to be mistaken? | `Confidence:` trailer |
| Process cost | Review rounds, merge ordering, reviewer context reload | The SRP clause |

Score every option on every axis — one row per option, however many there
are, with the do-nothing row always among them. **Do not narrow the set to a
pair before scoring.** Dropping a candidate on the way to the table is a
judgement made with no axis behind it, and it is invisible afterwards: the
reader sees a two-row table and takes it for the whole fork. A candidate that
does not survive belongs in the table with the cell that killed it, not
outside it. The table's own cost is one row, so an option list is narrowed by
its scores, never by anticipating them.

`UNKNOWN` is for a cell you cannot call
even directionally — not for a weak one. A cell you can call, but only from
plausibility, still gets its value and is graded `assumed` in Step 3; that is
what makes the weakness readable instead of hidden behind a blank.

### Step 3: Grade where each score came from

Every cell carries one grade beside its value:

| Grade | Means | Requirement |
| ------- | ------- | ------------- |
| `measured` | A command was run and its output decided this | Print the command beside the cell |
| `estimated` | Inferred from a comparable case that was actually read | Name the case |
| `assumed` | No basis beyond plausibility | Say so plainly |
| `UNKNOWN` | Not callable even directionally | Leave the value empty |

The grades exist to make the weak cells visible, not to make the table
trustworthy. A table of `assumed` cells is a table of guesses that has been
labelled honestly — it has not become evidence.

### Step 4: Report

Print the table, then one line per option saying why it was chosen or dropped.

- **No totals row.** No sum, no average, no weighted score, no ranking column.
  The axes are read side by side; they are not commensurable.
- If the decision is worth keeping, propose a `Rejected: <option> | <reason>`
  commit trailer — but only when `measured` and `estimated` cells are the
  majority. A trailer carries no provenance grade, and `debt` harvests
  `Rejected:` into its ledger, so an `assumed` judgement promoted to a trailer
  lands there as a decision with its weakest property stripped off.
- Write the trailer nowhere yourself. Proposing it is the whole action.

## Error Handling

| Error | Recovery |
| ------- | ---------- |
| Only one option after all three probes | Report that; there is no fork to cost |
| Probe 2 returns a precedent | Print it and stop — no table (Step 1) |
| Every cell on an axis is `UNKNOWN` | Report the axis as undecidable rather than dropping it; a hidden axis reads as a scored one |
| The fork is about whether to follow a rule | Not a fork. Print the rule and stop |
| More options than fit one readable table | Still score them all. Split the table by axis, never by dropping options — a table trimmed to fit hides the trim |

## Limitations

- **The scores are self-authored.** No command validates them, and the
  provenance grades are filled by the same hand that filled the values. This
  makes the weakness visible; it does not remove it. `ETHOS.md` names this
  class directly — a schema check on an evidence table confirms the shape of a
  block whose sentence is the part that lied.
- **No automatic regression.** `scripts/run-tests.sh` does not execute skills.
  What is locked is the step order and the presence of the examples below
  (`tests/test_tradeoff_skill_shape.py`), not the quality of any scoring run.
- **Report-only.** Nothing here writes to a PR, an issue, or any external
  surface.
- **Axis review.** These four axes are a starting set. Review them by
  2027-03-08 and drop any that comes back empty or identical across runs.

## Examples

### Case 1 — the lookup returns a precedent, so no table is built

Fork: *should the enforcement for this live in a hook or in a skill?*

```text
Probe 2 (convention) → ETHOS.md, Hook Ethos:
  "Spec defines, hook enforces. Each hook is the structural enforcement of a
   rule that already exists in CLAUDE.md or a memory entry. Memory-based
   feedback alone has historically failed (≥5 recurrences) — hooks replace the
   memo when the pattern proves recurrent."

Selects: skill. No recurrence record exists for this rule yet, so the hook
condition is not met. Stopping here — this fork is already settled.
```

No table. The precedent decided it.

### Case 2 — no binding precedent, so the table is built

Fork: *how should this skill's axis review be triggered — by use count, by
date, or not at all?*

| Option | Reversibility | Blast radius | Cost-if-wrong | Process cost |
| -------- | --------------- | -------------- | --------------- | -------------- |
| Use count | `low` `assumed` | `low` `assumed` | `moderate` `estimated` | `high` `measured` |
| Date-based | `low` `assumed` | `low` `assumed` | `low` `estimated` | `low` `measured` |
| Neither | `low` `assumed` | `moderate` `assumed` | `moderate` `assumed` | `low` `assumed` |

```text
measured — process cost, both rows:
  $ grep -rl "^review_by:" skills/ >/dev/null 2>&1; echo $?
  1                        # no skill declares a sunset field
  $ grep -rl "review_by" hooks/ >/dev/null 2>&1; echo $?
  0                        # hooks do — positive control for the same grep form

  The skills probe anchors on the frontmatter key (`^review_by:`), not the bare
  word: a bare-word search matches this file's own prose and would report the
  opposite. An evidence block that its own artifact falsifies is the failure
  this skill exists to make visible.

  Date-based reuses a field this repo already maintains for hooks, so the cost
  is one line. Use count has no field at all: a report-only skill writes no
  state, so counting uses means introducing persistence it does not have.

estimated — cost-if-wrong: read against the axis-pruning precedent in
  docs/retrospect-prune-audit.md, which pruned by re-scoring, not by counting.
```

Chosen: **date-based**. Use count was dropped on process cost — it needs a
state store this skill does not have. Neither was dropped because an unreviewed
axis set is how a scoring table becomes decorative.

Nine of the twelve cells are `assumed`. The table records that; it does not fix
it.
