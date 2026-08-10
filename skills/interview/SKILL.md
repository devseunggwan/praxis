---
name: interview
description: >
  Turn a vague request into the questions that must be answered before work
  starts — ambiguities ranked by whether they block, implicit premises made
  explicit, and the acceptance criterion named.
  Triggers on "requirement interview", "clarify requirements", "what should I
  ask", "deep interview", "요구사항 정리", "모호성 도출", "인터뷰 필요".
  Do NOT activate on a request that is already specified, on designing the
  solution, or on writing the implementation plan (use critique to review
  one).
verified-against-runtime: true
runtime-verified-at: 2026-08-10
runtime-verified-note: "claude Agent(subagent_type=claude, model=opus) — live run against a four-sentence feature request: 12 ambiguities and 10 implicit premises surfaced against a pre-registered answer key of 8, verdict INTERVIEW REQUIRED."
---

# interview

## Overview

A short request reads as clear because every reader silently fills its gaps, and
each reader fills them differently. The cost lands later: the feature is built,
demoed, and rejected on a criterion nobody wrote down. This skill dispatches an
independent analyst that extracts what the request leaves undefined — the
ambiguities, the premises it assumes are true, and the acceptance criterion it
never states — and returns them as questions rather than as guesses.

**Core principle:** an ambiguity is converted into a question, never into an
assumption — and a requirement with no acceptance criterion is not ready
regardless of how many other gaps are closed.

## When to Use

- A request arrives in a few sentences and work is expected to start from it.
- The request contains soft quantifiers: "too often", "reasonably fast",
  "if it looks wrong", "naturally integrated".
- A deadline is stated before the scope is.
- Two readers of the same request would plausibly build different things.

Skip when the request already carries acceptance criteria and a closed scope, or
when the task is to design a solution to an agreed requirement.

## Process

### Step 1: Fix the target

Supply the request verbatim, plus the product context the analyst needs to tell
a real gap from a convention it could look up. A gap the codebase already
answers should be resolved by reading, not by spending a question on it.

### Step 2: Dispatch an independent analyst

```text
Agent(subagent_type="claude", model="opus", name="interview",
      prompt=<Role Block> + <request verbatim> + <product context> + <Output Contract>)
```

The prompt **must** instruct the analyst to return results via
`SendMessage(to="main")`; a spawned agent's plain text is not delivered.

Delegation matters here for a different reason than in the sibling skills: an
agent that has already begun imagining the implementation stops seeing the
ambiguities, because it has resolved them privately.

### Step 3: Separate blocking from orderable

An ambiguity is `BLOCKING` when any answer changes what gets built, not when it
is merely unanswered. Ranking everything as blocking produces a questionnaire
nobody answers; ranking nothing as blocking produces a build on assumptions.

### Step 4: Surface the questions

Put the blocking questions to the requester before work starts, in their own
words, one decision each. Carry the implicit-premise list too — a premise the
requester did not know they were making is where the schedule breaks.

## Role Block

```text
You are analyzing a request to find what must be settled before anyone builds
it. You are not designing the solution and not estimating it.

Protocol:
1. Read the request literally. For every soft quantifier — "too often", "fast",
   "ideally", "naturally", "if it looks wrong" — name the decision it hides.
2. For each gap, decide whether a different answer would change what gets built.
   If yes it is BLOCKING; if it only changes polish or ordering it is not.
3. Convert every gap into a single question the requester can answer in one
   sentence. Do not offer a menu of designs; ask what they need, not which
   implementation they prefer.
4. Do not ask what the codebase already answers. Resolve those by reading and
   say what you found.
5. List the implicit premises separately: things the request assumes are true
   without stating them — that the data exists, that an integration is already
   built, that a schema change is permitted, that the named audience is the
   real one, that the scope fits the stated deadline.
6. Name the acceptance criterion explicitly. If the request does not state how
   anyone will know the work succeeded, that absence is itself a BLOCKING
   ambiguity — record it as one rather than assuming a criterion.
7. Check the failure and edge behavior the request is silent about: missing or
   late data, the condition clearing, repeated triggering, who is excluded.

Any absence you assert must carry the scope you searched. End with one
observation that would falsify your readiness verdict.
```

## Output Contract

| Field | Vocabulary |
| ----- | ---------- |
| Ambiguity severity | `BLOCKING` \| `IMPORTANT` \| `MINOR` |
| Ambiguity line | `N. [severity] <what is undefined> — question: <one-line question>` |
| Implicit premises | numbered list of unstated assumptions the request rests on |
| Acceptance criterion | the stated criterion, or `ABSENT` recorded as a BLOCKING ambiguity |
| Verdict | `READY` \| `INTERVIEW REQUIRED` |
| Falsification | one observation that would overturn the verdict |

Any `BLOCKING` ambiguity, including an `ABSENT` acceptance criterion, forecloses
`READY`.

## Limitations

- Produces questions, not answers. The interview still has to happen with a
  person who can decide.
- Over-produces on genuinely simple requests — a two-line change will collect
  ambiguities that are not worth an exchange. Use the severity ranking to cut.
- Cannot tell a real gap from an established convention it was not shown. Give
  it the product context or it will spend questions on things the repo answers.

Idea lineage: the analyst role is adapted from the `analyst` agent and
`deep-interview` skill in
[oh-my-claudecode](https://github.com/Yeachan-Heo/oh-my-claudecode) (MIT),
rewritten for this repo's skill conventions.
