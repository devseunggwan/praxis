---
name: retrospect
description: >
  Session retrospect — analyze current Claude Code session against CLAUDE.md rules,
  identify friction patterns and root causes, propose context-appropriate improvement
  actions, then execute after user approval.
  Triggers on "retrospect", "회고", "삽질 정리", "세션 개선", "what went wrong",
  "이번 세션", "불편했던 거", "개선해", "뭐가 문제였어".
---

# Retrospect

## Overview

Repeated friction wastes cycles across sessions. Unexamined pain stays unresolved.

**Core principle:** ALWAYS analyze root cause before proposing any action.
Symptom-level fixes (e.g., "remember to do X") miss the underlying pattern.

**Pipeline:** `Load → Analyze → Report/Approve → Execute` (4 stages)

**Delegates to:** OMC `tracer` agent (causal pattern analysis), `analyst` agent (pattern clustering)

## The Iron Law

```
NO ACTION WITHOUT ROOT CAUSE ANALYSIS FIRST.
PATTERN ≠ ROOT CAUSE. SYMPTOM ≠ ROOT CAUSE.
```

If you haven't completed Stage 2 (Analyze), you cannot propose actions.
"It happened because X" is a symptom. "X happened because of missing rule Y / unclear trigger Z / absent skill W" is a root cause.

## When to Use

Use at the END of a working session to extract learnings:

- Session had repeated tool retries or direction changes
- User gave corrections mid-session ("no, don't do that")
- A task took significantly longer than expected
- Workflow steps were skipped or out of order
- User expressed frustration or redirected multiple times

**Use this ESPECIALLY when:**
- The same mistake happened more than once in the session
- You feel "I should have done that differently"
- A rule in CLAUDE.md was violated — even once
- A new workflow pattern emerged that isn't captured anywhere

## The Four Stages

You MUST complete each stage before proceeding to the next.

### Stage 1: Load Calibration Standard

**Before scanning the conversation:**

1. **Read CLAUDE.md** — load all rules, behavioral guidelines, and workflow requirements
   - Global: `~/.claude-5x/CLAUDE.md`
   - Project: `CLAUDE.md` in cwd (if exists)
   - Key sections: Mandatory Rules, Behavioral Rules, Workflow rules

2. **Identify rule categories** to scan against:
   - Workflow discipline (Issue-Driven Workflow, Planning Before Implementation)
   - Evidence-Based Delivery (No "Trust Me" completions)
   - Atomic Commits + PR Lifecycle
   - Mandatory Testing (unit + functional)
   - Code Review Before Commit
   - Error Recovery Before Asking
   - Communication conventions

3. **Set the calibration frame**: For each rule category, form a question — e.g.,
   "Did the session violate 'Planning Before Implementation'? Were there 3+ step tasks that skipped plan mode?"

### Stage 2: Analyze Conversation

**Scan the current session's conversation history:**

> **Scope:** Scan the most recent 50 turns, or back to the last session boundary.
> Stop after identifying 5 distinct friction events — clustering (step 4) handles de-duplication.
> If session history is not accessible, use the user's verbal summary as input to steps 2–4.

1. **Identify friction events** — moments where:
   - User corrected Claude's direction
   - Tool calls were retried unnecessarily
   - A workflow step was skipped or out of order
   - The session stalled, looped, or backtracked

2. **Map each event to a CLAUDE.md rule** (or gap):
   - Which rule was applicable?
   - Was it followed, violated, or simply absent?
   - Quote or paraphrase the specific moment

3. **Find root cause** for each pattern:

   ```
   Symptom:   "Claude retried the same tool 3 times"
   Pattern:   "Error recovery loop"
   Root cause: "No diagnostic step between retries — violated Error Recovery Before Asking rule"

   Symptom:   "Implementation started before plan was approved"
   Pattern:   "Premature execution"
   Root cause: "Task had 4 steps but plan mode was not entered — violated Planning Before Implementation"
   ```

4. **Cluster patterns** — are multiple events the same root cause?
   If 3+ events share a root cause → HIGH priority

### Stage 3: Report + Approval

**Present findings in a structured table:**

```
## Retrospect Report — {session_date}

| # | Pattern | Root Cause | Rule Violated / Gap | Proposed Action | Priority |
|---|---------|------------|---------------------|-----------------|----------|
| 1 | {pattern} | {root_cause} | {rule_ref or "missing rule"} | {action} | HIGH/MED/LOW |
...

No patterns found: "This session followed all CLAUDE.md rules. ✅"
```

**Action types (context-dependent — pick what fits):**

| Pattern Type | Likely Action |
|-------------|---------------|
| Repeated rule violation | Add feedback entry to MEMORY.md |
| Missing workflow step | Create GitHub issue to add skill / hook |
| Absent rule for new pattern | Draft CLAUDE.md rule addition |
| One-off mistake | Note only — no persistent action needed |
| Systemic friction in tooling | Create GitHub issue for improvement |

**Then ask for approval per item:**

```
For each finding, user selects:
  ✅ Execute now  |  ⏭ Skip  |  🕐 Defer (create note only)
```

Do NOT execute any action until user approves.

### Stage 4: Execute

For each approved action:

1. **MEMORY.md feedback** → Write to `~/.claude-5x/projects/.../memory/` with proper frontmatter
   - Type: `feedback`
   - Include: rule, why, how to apply
   - Update `MEMORY.md` index

2. **GitHub issue** → Use `laplace-dev-hub:create-hub-issue` (Hub) or `gh issue create` (other repos)
   - Title: Conventional Commits format, English
   - Body: Korean, with background + task list

3. **CLAUDE.md draft** → Write proposed rule addition as a markdown block
   - ⚠️ `~/.claude-5x/CLAUDE.md` is **global scope** — changes affect every project
   - Present the draft to user for review BEFORE any edit
   - Apply only with explicit approval ("yes, add this rule")

4. **Skill idea note** → Write to `{current_project}/.omc/plans/retrospect-skill-idea-{slug}.md`
   - `{current_project}` = `$CLAUDE_PROJECT_DIR` or `git rev-parse --show-toplevel`
   - Include: problem, proposed skill trigger, pipeline sketch

5. **Completion report:**

```
## Actions Executed

| # | Action | Result |
|---|--------|--------|
| 1 | MEMORY.md feedback added | ✅ {file_path} |
| 2 | GitHub issue created | ✅ {url} |
...

Session learnings captured. Next session will benefit from these improvements.
```

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "It was a one-off mistake, not worth capturing" | If it happened once, it can happen again. Capture it. |
| "I know the root cause, I'll just note the symptom" | Symptoms recur. Root causes get fixed. Write the root cause. |
| "MEMORY.md is already long, skip this" | Length doesn't matter. Missing the pattern is the cost. |
| "The session was mostly fine, nothing to retrospect" | Even 1 friction event is worth 2 minutes to capture. |
| "I'll do this later" | Later never comes. Do it at session end while context is fresh. |
| "This is a tool issue, not a Claude issue" | Tool + Claude interaction is within scope. Both can be improved. |

## Red Flags — STOP

If you catch yourself:

- Proposing actions before completing Stage 2 analysis
- Writing "root cause: Claude forgot to X" without tracing WHY the forgetting happened
- Adding a MEMORY.md entry that just repeats the CLAUDE.md rule verbatim (no new insight)
- Creating a GitHub issue for every minor friction (low-ROI noise)
- Skipping the approval step and executing actions directly
- Editing `~/.claude-5x/CLAUDE.md` without presenting the draft first — this is global config, affects every project

**ALL of these mean: STOP. Return to Stage 2.**

## Quick Reference

| Stage | Key Activity | Success Criteria |
|-------|-------------|-----------------|
| **1. Load** | Read CLAUDE.md, form scan questions | Rule categories identified |
| **2. Analyze** | Scan conversation, map to rules, find root cause | Root cause (not symptom) for each pattern |
| **3. Report** | Present table, collect approval per item | User approved at least 1 item (or confirmed 0 findings) |
| **4. Execute** | Run approved actions, show evidence | Completion report with links/paths |

## Integration

**Entry point:** End of a working session, or after a particularly rough workflow experience
**Exit point:** Completion report shown → optionally chain to next session's `turbo-setup`

**OMC delegation:**
- `tracer` agent: causal chain analysis for complex friction patterns
- `analyst` agent: cluster multiple friction events into root causes
- `create-hub-issue` skill: GitHub issue creation in Stage 4
