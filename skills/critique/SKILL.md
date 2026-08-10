---
name: critique
description: >
  Adversarial review of a plan, design, or proposal before resources are
  committed — surfaces defects, gaps, unfounded premises, and internal
  contradictions via an independent reviewer.
  Triggers on "plan critique", "critique this plan", "review my plan",
  "design review", "poke holes", "계획 검토", "설계 반증", "계획 비판".
  Do NOT activate on reviewing a code diff (that is a code review), on judging
  whether finished work is proven (use audit), or on merely summarizing
  a plan.
verified-against-runtime: true
runtime-verified-at: 2026-08-10
runtime-verified-note: "claude Agent(subagent_type=claude, model=opus) — live run against a plan fixture carrying 6 seeded defects: all 6 surfaced plus 5 unseeded, verdict REJECT; the reviewer's findings reached the caller only when the prompt named SendMessage(to='main')."
---

# critique

## Overview

A plan is approved by the person who wrote it far more often than it deserves,
because the author's own reading of it is the reading that produced it. False
approval costs many times more than false rejection: the work gets built, the
missing premise surfaces at integration, and the rewrite carries sunk cost. This
skill dispatches an **independent** reviewer whose job is to find what the plan
does not say — absent oracles, references that do not resolve, phases that
contradict each other.

**Core principle:** the plan is judged by an agent that did not write it, and a
claim the plan cannot support is a defect even when nothing in the plan is wrong.

## When to Use

- A plan, design doc, RFC, or migration proposal is about to be approved.
- You authored the plan yourself — self-review here is structurally compromised,
  so delegation is not optional.
- A proposal cites metrics, file paths, or symbols as premises for its phases.
- Before committing a team to a multi-phase sequence where phase N depends on
  phase N-1's output.

Skip for a single-step mechanical change, or when the question is "is the
finished work proven" rather than "is the plan sound" — that is `audit`.

## Process

### Step 1: Fix the target

Name the artifact by path and the codebase root it refers to. A critique with no
codebase root cannot check whether the plan's file and symbol references resolve,
which is where the highest-severity findings usually live.

### Step 2: Dispatch an independent reviewer

Spawn a fresh agent and inject the Role Block below verbatim. Do not perform the
critique in the current session — an agent holding the plan's authoring context
rationalizes rather than reviews.

```text
Agent(subagent_type="claude", model="opus", name="critique",
      prompt=<Role Block> + <artifact path> + <codebase root> + <Output Contract>)
```

The prompt **must** instruct the reviewer to return results via
`SendMessage(to="main")`. Plain text output from a spawned agent is not delivered
to the caller; without this line the run completes and reports nothing.

### Step 3: Require reference resolution, not just reading

The Role Block directs the reviewer to resolve every concrete reference the plan
makes — file paths, symbols, table names, metrics — against the real codebase
before judging the phase that depends on it. A plan phase built on a symbol that
does not exist is not a MEDIUM style note; it invalidates the phase.

### Step 4: Collect and report

Relay findings in severity order. Do not soften the verdict, and do not drop
findings that contradict the plan you are shepherding.

## Role Block

```text
You are reviewing a plan for approval. The author is presenting to you; you are
not a helpful assistant offering suggestions. A false approval costs far more
than a false rejection.

Standard review evaluates what is present. You also evaluate what is absent.

Protocol:
1. Read the artifact end to end before forming any judgement.
2. Resolve every concrete reference it makes — file paths, symbols, tables,
   commands — against the real codebase. Cite the probe (`grep -n`, `ls`, a
   read) inline. An unresolvable reference invalidates the phase that needs it.
3. Check the numbers. If the plan states a baseline or a target, verify the
   target is reachable from the baseline using the plan's own figures.
4. Cross-check phases against each other. A phase that consumes what an earlier
   phase removes is a contradiction, not an ordering detail.
5. Check that the completion criterion has an oracle. If the criterion is a
   wall-clock time, a rate, or a user-visible outcome, a unit-test pass does not
   measure it.
6. Name scope creep: work in the plan that does not serve its stated goal.
7. Flag every unsourced premise. A number with no measurement method is a guess
   the rest of the plan is stacked on.

You are not responsible for gathering requirements, writing the plan, or
implementing it. Do not propose a replacement design — report defects.

Any claim you make that something is absent must carry the scope you searched.
End with one observation that would falsify your own verdict.
```

## Output Contract

| Field | Vocabulary |
| ----- | ---------- |
| Finding severity | `CRITICAL` \| `HIGH` \| `MEDIUM` \| `LOW` |
| Finding line | `N. [severity] <one-line claim> — evidence: <probe and result>` |
| Verdict | `APPROVE` \| `REVISE` \| `REJECT` |
| Falsification | one observation that, if made, would overturn the verdict |

`CRITICAL` is reserved for findings that invalidate a phase or the plan's goal:
an unresolvable reference a phase is built on, a phase contradiction, a target
unreachable by the plan's own arithmetic, or a completion criterion with no
oracle.

## Limitations

- Judges the plan as written. A plan that omits a constraint the reviewer cannot
  see from the codebase will pass on that axis.
- Costs one agent per invocation. For a two-line change the dispatch overhead
  exceeds the value.
- Not a code review — it reads code only to resolve the plan's references.
- Severity calibration drifts across runs. Treat the ranking as advisory and the
  findings themselves as the product.

Idea lineage: the reviewer role is adapted from the `critic` agent in
[oh-my-claudecode](https://github.com/Yeachan-Heo/oh-my-claudecode) (MIT),
rewritten for this repo's skill conventions.
