---
name: debug
description: Systematic 4-phase debugging — root cause investigation before any fix attempt. Use for any bug, test failure, or unexpected behavior. Triggers on "debug", "debugging", "why failing", "why broken", "root cause".
---

# Systematic Debug

## Overview

Random fixes waste time and create new bugs. Quick patches mask underlying issues.

**Core principle:** ALWAYS find root cause before attempting fixes. Symptom fixes are failure.

**Delegates to:** OMC `debugger` agent (stack trace analysis, root cause isolation)

## The Iron Law

```
NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST
```

If you haven't completed Phase 1, you cannot propose fixes.

## When to Use

Use for ANY technical issue:
- Test failures
- Production bugs
- Unexpected behavior
- Performance problems
- Build failures
- Integration issues

**Use this ESPECIALLY when:**
- Under time pressure (emergencies make guessing tempting)
- "Just one quick fix" seems obvious
- You've already tried multiple fixes
- Previous fix didn't work
- You don't fully understand the issue

## The Four Phases

You MUST complete each phase before proceeding to the next.

### Phase 1: Root Cause Investigation

**BEFORE attempting ANY fix:**

1. **Read Error Messages Carefully**
   - Don't skip past errors or warnings
   - Read stack traces completely
   - Note line numbers, file paths, error codes

2. **Reproduce Consistently**
   - Can you trigger it reliably?
   - What are the exact steps?
   - If not reproducible → gather more data, don't guess

3. **Check Recent Changes**
   - What changed that could cause this?
   - `git diff`, recent commits
   - New dependencies, config changes, environmental differences

4. **Gather Evidence in Multi-Component Systems**

   ```
   For EACH component boundary:
     - Log what data enters the component
     - Log what data exits the component
     - Verify environment/config propagation
     - Check state at each layer

   Run once to gather evidence showing WHERE it breaks
   → Identify the failing component
   → Investigate that specific component
   ```

5. **Trace Data Flow**
   - Where does the bad value originate?
   - What called this with the bad value?
   - Keep tracing upstream until you find the source
   - Fix at source, not at symptom

### Phase 2: Pattern Analysis

1. **Find Working Examples**
   - Locate similar working code in the same codebase
   - What works that's similar to what's broken?

2. **Compare Against References**
   - If implementing a pattern, read reference implementation COMPLETELY
   - Don't skim — read every line

3. **Identify Differences**
   - What's different between working and broken?
   - List every difference, however small
   - Don't assume "that can't matter"

4. **Understand Dependencies**
   - What other components does this need?
   - What settings, config, environment?
   - What assumptions does it make?

### Phase 3: Hypothesis and Testing

1. **Form Single Hypothesis**
   - State clearly: "I think X is the root cause because Y"
   - Write it down — be specific, not vague

2. **Test Minimally**
   - Make the SMALLEST possible change to test hypothesis
   - One variable at a time
   - Don't fix multiple things at once

3. **Verify Before Continuing**
   - Did it work? YES → Phase 4
   - Didn't work? → Form NEW hypothesis
   - DON'T add more fixes on top

### Phase 4: Implementation

1. **Create Failing Test Case**
   - Simplest possible reproduction
   - Automated test if possible
   - MUST exist before fixing

2. **Implement Single Fix**
   - Address the root cause identified
   - ONE change at a time
   - No "while I'm here" improvements
   - No bundled refactoring

3. **Verify Fix**
   - Test passes now?
   - No other tests broken?
   - Issue actually resolved?

4. **If Fix Doesn't Work**
   - **STOP**
   - Count: How many fixes have you tried?
   - If < 3: Return to Phase 1, re-analyze with new information
   - **If ≥ 3: STOP and question the architecture (step 5 below)**
   - DON'T attempt Fix #4 without architectural discussion

5. **If 3+ Fixes Failed: Question Architecture**

   **Signals of an architectural problem:**
   - Each fix reveals new shared state / coupling / problems in different places
   - Fixes require "massive refactoring" to implement
   - Each fix creates new symptoms elsewhere

   **STOP and question fundamentals:**
   - Is this pattern fundamentally sound?
   - Are we sticking with it through sheer inertia?
   - Should we refactor architecture vs. continue fixing symptoms?

   **Discuss with user before attempting more fixes.**

   This is NOT a failed hypothesis — this is a wrong architecture.

## OMC debugger Agent Delegation

Use the OMC `debugger` agent during Phases 1–3:

- Stack trace analysis
- Root cause isolation
- Regression isolation
- Build/compilation error resolution

**But:** Do NOT blindly trust agent results. Verify the reported cause independently.

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "Issue is simple, don't need process" | Simple bugs have root causes too. Process is fast for simple bugs. |
| "Emergency, no time for process" | Systematic debugging is FASTER than guess-and-check thrashing. |
| "Just try this first, then investigate" | First fix sets the pattern. Do it right from the start. |
| "I'll write test after confirming fix works" | Untested fixes don't stick. Test first proves it. |
| "Multiple fixes at once saves time" | Can't isolate what worked. Causes new bugs. |
| "Reference too long, I'll adapt the pattern" | Partial understanding guarantees bugs. Read it completely. |
| "I see the problem, let me fix it" | Seeing symptoms ≠ understanding root cause. |
| "One more fix attempt" (after 2+ failures) | 3+ failures = architectural problem. Question the pattern, don't fix again. |

## Red Flags — STOP and Return to Phase 1

If you catch yourself thinking:

- "Quick fix for now, investigate later"
- "Just try changing X and see if it works"
- "Add multiple changes, run tests"
- "Skip the test, I'll manually verify"
- "It's probably X, let me fix that"
- "I don't fully understand but this might work"
- Proposing solutions before tracing data flow
- **"One more fix attempt" (when already tried 2+)**
- **Each fix reveals new problem in different place**

**ALL of these mean: STOP. Return to Phase 1.**

**If 3+ fixes failed:** Question the architecture (see Phase 4.5).

## Quick Reference

| Phase | Key Activities | Success Criteria |
|-------|---------------|------------------|
| **1. Root Cause** | Read errors, reproduce, check changes, gather evidence | Understand WHAT and WHY |
| **2. Pattern** | Find working examples, compare | Identify differences |
| **3. Hypothesis** | Form theory, test minimally | Confirmed or new hypothesis |
| **4. Implementation** | Create test, fix, verify | Bug resolved, tests pass |

## Error Handling

| Phase | Failure | Action |
|-------|---------|--------|
| Phase 1 (root cause) | Cannot reproduce the bug locally | Gather more evidence (logs, env diffs) before hypothesizing |
| Phase 1 (root cause) | Stack trace missing or unclear | Add logging and re-run — never guess from incomplete traces |
| Phase 2 (pattern) | No working reference found | Widen the search (sibling modules, git history, related projects) |
| Phase 3 (hypothesis) | Minimal test fails to distinguish hypotheses | Split the test further; one test per hypothesis |
| Phase 4 (implementation) | Fix passes test but breaks another | Back out, return to Phase 1 — root cause is broader than thought |
| Any phase | 3+ fix attempts failed | STOP — escalate to Phase 4.5 (architecture review) |

## Integration

**Entry point:** Triggered by any bug, test failure, or unexpected behavior during work
**Exit point:** Fix verified → continue with `turbo-completion`

**OMC delegation:**
- `debugger` agent: root cause analysis, stack trace analysis
- `test-engineer` agent: failing test case creation (Phase 4)
- `ultraqa`: post-fix verification cycle
