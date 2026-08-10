---
name: audit
description: >
  Audit whether a completion claim is actually proven — grades each claim's
  evidence, separates measurement from inference, and names what was never
  verified.
  Triggers on "evidence audit", "is this actually verified", "audit the
  evidence", "완료 검증", "증거 심사", "검증 충분성".
  Do NOT activate on judging whether a plan is sound (use critique), on
  finding a bug's cause (use trace), or on running the tests yourself.
verified-against-runtime: true
runtime-verified-at: 2026-08-10
runtime-verified-note: "claude Agent(subagent_type=claude, model=opus) — live run against a completion-report fixture with 6 defective claims: all 6 graded correctly (mock-only, lint suppression, unscoped negative, unmeasured performance, CI aggregate), verdict RE-VERIFY."
---

# audit

## Overview

"Done" is usually asserted with evidence that does not reach the claim. Unit
tests prove logic against mocks the author wrote; a lint pass bought with a
suppression comment is greener than the code; "no other instance is affected" is
a claim about everything the author did not look at. Each of these reads as
verification and none of them is. This skill dispatches an independent auditor
that grades every claim against the evidence actually produced.

**Core principle:** evidence is graded by what it measures, not by whether it is
green — and a claim with no stated scan scope is unproven regardless of outcome.

## When to Use

- A completion report, PR description, or status update asserts work is done.
- Before a merge, a release, or any irreversible action gated on "verified".
- A report contains a negative claim — "no other X", "nothing else broke",
  "no regression" — which is only as strong as its search.
- You wrote the report yourself and want the claim checked by something that did
  not produce it.

Skip when the work has no completion claim yet, or when the question is whether
the approach is right rather than whether the result is proven.

## Process

### Step 1: Fix the target

Name the report by path or paste it, and name the artifact it claims to have
fixed — the failing run, the pod, the record. The audit's hardest question is
whether the original symptom was observed to disappear on that artifact.

### Step 2: Dispatch an independent auditor

```text
Agent(subagent_type="claude", model="opus", name="audit",
      prompt=<Role Block> + <report> + <artifact under claim> + <Output Contract>)
```

The prompt **must** instruct the auditor to return results via
`SendMessage(to="main")`; a spawned agent's plain text is not delivered.

Do not audit in the current session when the current session produced the
report. The author's memory of intent fills the evidence gaps automatically,
which is exactly the failure being audited for.

### Step 3: Grade each claim separately

One verdict for the whole report hides which link is weakest. Every enumerated
claim gets its own grade, and the report's verdict is bounded by its weakest
claim, not by the count of green ones.

### Step 4: Collect and report

Relay the per-claim grades and the missing-verification list intact. The missing
list is the actionable half — a claim graded `VOID` tells you what to redo.

## Role Block

```text
You are auditing whether a completion claim is proven. You are not re-doing the
work and not judging whether the approach was right — only whether the evidence
produced reaches the claim made.

Grade every claim independently. For each, ask what the evidence physically
measures and whether that is the thing claimed.

Protocol:
1. Unit tests prove logic against fixtures. They do not prove behavior against a
   real system. A claim about runtime, integration, or vendor behavior needs
   execution output from that system.
2. A mock whose shape was taken from the code under test is self-confirming.
   Mock fields must trace to vendor docs, a working baseline, or a recorded real
   response. Note it when they do not.
3. A lint or type pass bought with a suppression (`noqa`, `type: ignore`,
   `eslint-disable`, `any`) is not a pass. Grade it VOID unless a concrete
   reason a root fix is impossible is stated.
4. A negative claim ("no other X", "nothing else affected") with no stated scan
   scope is unfalsifiable. Grade VOID and name the scope it would need.
5. An inference from code shape ("shorter path, so faster") is not a
   measurement. Performance, capacity, and latency claims need numbers.
6. An aggregate status (CI green, a checks summary) is a rollup, not a source.
   It does not identify which artifact was tested.
7. A green checkmark with no accompanying source is decoration.
8. Separately, ask whether the ORIGINAL symptom was observed to disappear on the
   actual failing artifact. Reproducing the mechanism elsewhere proves the
   mechanism, not the fix.

Then list what is missing: the verification that would actually settle each
unproven claim, stated as a runnable check.

Any absence you assert must carry the scope you searched. End with one
observation that would falsify your verdict.
```

## Output Contract

| Field | Vocabulary |
| ----- | ---------- |
| Claim grade | `SUFFICIENT` \| `INSUFFICIENT` \| `VOID` |
| Claim line | `N. [grade] <why the evidence does or does not reach the claim>` |
| Missing verification | list of runnable checks that would settle the unproven claims |
| Verdict | `ACCEPTED` \| `RE-VERIFY` |
| Falsification | one observation that would overturn the verdict |

`INSUFFICIENT` means the evidence points at the claim but does not reach it —
more of the same kind would help. `VOID` means the evidence cannot support the
claim at all: a suppressed lint, an unscoped negative, an inference presented as
a measurement. `ACCEPTED` requires every claim to be `SUFFICIENT`; one
`INSUFFICIENT` or `VOID` anywhere makes the verdict `RE-VERIFY`. The two grades
share a verdict but are not interchangeable: `INSUFFICIENT` is closed by more
evidence of the kind already produced, `VOID` cannot be closed without evidence
of a different kind. Grade by which of those two is true, never by which
verdict it leads to.

## Limitations

- Audits the report, not the system. It cannot tell you the code is correct —
  only that the claim of correctness is or is not carried by its evidence.
- Cannot grade evidence it is not shown. A report that omits its commands looks
  worse than one that pastes weak output; that asymmetry is intentional but does
  mean thoroughness of reporting is being measured alongside thoroughness of work.
- Does not run the missing verification. It names the checks; executing them is
  the caller's job.

Idea lineage: the auditor role is adapted from the `verifier` agent in
[oh-my-claudecode](https://github.com/Yeachan-Heo/oh-my-claudecode) (MIT),
rewritten for this repo's skill conventions.
