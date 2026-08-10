---
name: trace
description: >
  Trace a symptom to its root cause through competing hypotheses — each
  hypothesis is refuted or held on observed evidence, and the surviving verdict
  ships with the probe that would falsify it.
  Triggers on "causal trace", "root cause", "why is this failing", "trace the
  cause", "근본 원인", "원인 추적", "인과 추적".
  Do NOT activate on applying a known fix, on auditing whether a fix is proven
  (use audit), or on a stack trace with a single obvious cause.
verified-against-runtime: true
runtime-verified-at: 2026-08-10
runtime-verified-note: "claude Agent(subagent_type=claude, model=opus) — live run against an intermittent-zero-row fixture: root cause (timezone boundary) identified, all three offered hypotheses correctly refuted on the error-log evidence, plus 6 unseeded defects and a falsifying probe."
---

# trace

## Overview

An investigation that starts from one hypothesis finds evidence for it. The
symptom is real, the hypothesis is plausible, and the first supporting detail
ends the search — so the fix lands on a correlate and the symptom returns. This
skill forces the shape that avoids it: hypotheses compete, each is attacked with
the evidence that would refute it, and the survivor is not a conclusion until a
probe that could still overturn it is named.

**Core principle:** a hypothesis is adopted only after the competing ones are
refuted on observed evidence — and a root cause with no falsifying probe is a
guess that happens to fit.

## When to Use

- A symptom is intermittent, environment-dependent, or time-correlated.
- Several plausible causes are on the table and the team is about to pick one.
- A previous fix did not stop the symptom, which means the earlier cause was a
  correlate.
- A failure is silent — the system reports success while producing nothing.

Skip when the stack trace names the cause outright, or when the task is to apply
a cause that is already established.

## Process

### Step 1: Fix the target

State the symptom, the observation window, and every hypothesis already on the
table. Hypotheses supplied by the team are inputs to be refuted, not a menu to
choose from — an investigation that only ranks the offered options inherits
their blind spot.

### Step 2: Dispatch an independent tracer

```text
Agent(subagent_type="claude", model="opus", name="trace",
      prompt=<Role Block> + <symptom and window> + <known hypotheses> + <Output Contract>)
```

The prompt **must** instruct the tracer to return results via
`SendMessage(to="main")`; a spawned agent's plain text is not delivered.

### Step 3: Require refutation, not ranking

Every hypothesis gets an explicit disposition with the evidence that produced
it. "Unlikely" is not a disposition. The absence of an error log is strong
evidence against an error-producing hypothesis, and that reasoning belongs in
the output where it can be checked.

### Step 4: Collect and report

Lead with the root cause and the chain that produced it. Carry the falsifying
probe through to the user — it is what makes the verdict actionable rather than
merely confident.

## Role Block

```text
You are tracing a symptom to its root cause. You are not fixing it, and you are
not choosing the most plausible option from a list.

Protocol:
1. Separate what was observed from what was inferred. List the observations
   first, and treat every hypothesis — including ones handed to you — as a
   claim to be attacked.
2. For each hypothesis, state what would have to be observable if it were true,
   then check whether that observation exists. An absent error log refutes an
   error-producing hypothesis; a symptom that correlates with wall-clock time
   rather than load refutes a capacity hypothesis. Before an absence counts as
   refutation, confirm the oracle can produce a positive result — a known-good
   entry on the same path, in the same window. An oracle that never writes
   returns empty for the wrong reason, so an unconfirmed one leaves the
   hypothesis `HELD`, not `REJECTED`.
3. Give every hypothesis an explicit disposition: REJECTED, HELD, or ADOPTED,
   each with the evidence that produced it. A hypothesis that is real but does
   not produce THIS symptom is REJECTED for this investigation and reported
   separately as its own defect.
4. Build the chain for the surviving cause: from the observation, through the
   code path, to the symptom. Cite file and line where the chain touches code.
5. Note any silent-failure path you cross — a branch that converts a failure
   into a success return, a swallowed exception, an empty result treated as
   valid. It is rarely the cause and is often why nobody noticed.
6. Report defects you find along the way that are not this cause, separately
   and marked as such.

Any absence you assert must carry the scope you searched. End with one probe:
the observation that would falsify your root cause, and what the investigation
should pivot to if that probe comes back the other way.
```

## Output Contract

| Field | Vocabulary |
| ----- | ---------- |
| Root cause | one line, stated as a mechanism |
| Chain | numbered observation → code path → symptom, with file:line citations |
| Hypothesis disposition | `REJECTED` \| `HELD` \| `ADOPTED`, one per hypothesis, each with evidence |
| Separate defects | numbered list of real defects that are not this cause |
| Verdict | `ROOT CAUSE IDENTIFIED` \| `INCONCLUSIVE` |
| Falsifying probe | the observation that would overturn the verdict, plus the pivot |

`HELD` is for a hypothesis that neither the evidence nor its absence settles —
it stays open and names what would settle it. Reporting every hypothesis as
`REJECTED` except one is a signal to re-read the evidence, not a strong result.

## Limitations

- Traces to a cause, not to a fix. The remediation is a separate decision, and
  the cheapest fix is often not at the root.
- Bounded by the evidence supplied. A tracer given no logs will hold hypotheses
  open that one observation would have closed.
- When the trace reveals that the requirement itself was never settled — the
  behavior is not a bug but an unstated expectation — continue with
  `interview` rather than forcing a cause.

Idea lineage: the tracer role is adapted from the `tracer` agent and `trace`
skill in [oh-my-claudecode](https://github.com/Yeachan-Heo/oh-my-claudecode)
(MIT), rewritten for this repo's skill conventions.
