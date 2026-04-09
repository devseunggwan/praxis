---
name: turbo-implement
description: >
  Implementation orchestrator between turbo-setup and turbo-completion.
  Auto-detects git state from turbo-setup, selects execution mode, and chains to turbo-completion on completion.
  Triggers on "implement", "turbo-implement", "start coding", "build it".
---

# Turbo Implement

## Overview

Bridges the gap between setup and delivery. After turbo-setup creates the workspace, turbo-implement orchestrates the actual implementation work and chains to turbo-completion when done.

**Core principle:** The implementation phase needs structure, not just "go code." Mode selection matches task complexity to execution strategy.

**Chains from:** `turbo-setup` (auto-detects issue/branch from git state)
**Chains to:** `turbo-completion` (invoked on completion)

## The Iron Law

```
IMPLEMENTATION MODE MUST MATCH TASK COMPLEXITY.
NO DELIVERY WITHOUT VERIFICATION.
```

## When to Use

- After `turbo-setup` completes (or after manually creating a branch/worktree)
- Triggers: "implement", "turbo-implement", "start coding", "build it"
- No input required — auto-detects from git state

## Inputs (Auto-Detected)

All inputs are derived from the current working directory (same as turbo-completion):

```bash
BRANCH=$(git branch --show-current)
ISSUE_NUMBER=$(echo "$BRANCH" | grep -oP '(?<=issue-)\d+|(?<=hub-)\d+')
WORKTREE_PATH=$(pwd)
TARGET_REPO=$(basename $(git remote get-url origin) .git)
```

**Validation (STOP if any fails):**
- [ ] Branch name contains issue number
- [ ] Working directory is clean (or has only untracked files)

## Process

### Step 1: Gather Context

```bash
# Read issue details
ISSUE=$(gh issue view "$ISSUE_NUMBER" --json title,body,labels)

# Check for existing spec files
SPEC=$(ls .omc/specs/deep-interview-*.md 2>/dev/null | head -1)
PLAN=$(ls .omc/plans/*.md 2>/dev/null | head -1)
```

Present context summary:

```
═══════════════════════════════════════════════
 Turbo Implement
═══════════════════════════════════════════════

 Issue:   #<N> — <title>
 Branch:  <branch>
 Spec:    <path or "none">
 Plan:    <path or "none">
═══════════════════════════════════════════════
```

### Step 2: Select Execution Mode

Present mode options based on detected context:

```
How should this be implemented?
1. Manual — I'll implement, call /turbo-completion when done
2. Ralph — persistence loop until all acceptance criteria pass
3. Autopilot — full autonomous: plan → implement → QA → validate
4. Guided — implement step-by-step with verification after each change
```

**Mode routing heuristics (suggest but don't force):**

| Signal | Suggested Mode |
|--------|---------------|
| Spec file exists with acceptance criteria | Ralph (criteria-driven loop) |
| Plan file exists with implementation steps | Autopilot (plan-driven execution) |
| Simple task (1-2 files, clear scope) | Guided |
| No spec, no plan, complex task | Manual (needs more planning first) |

### Step 3: Execute

#### Mode: Manual

Report context and exit. User implements manually.

```
Ready to implement. When done, run /turbo-completion to complete the lifecycle.

Useful commands:
  /turbo-completion --verify-only  — check tests/lint before delivery
  /turbo-completion      — full delivery pipeline
  /debug              — if you hit a bug
```

#### Mode: Ralph (pluggable)

Delegate to the project's persistence loop skill (defined in CLAUDE.md routing).
Default: `oh-my-claudecode:ralph`.

Pass the spec or issue body as the task definition:

```
Task: Implement issue #<N> — <title>
Acceptance criteria: <from spec or issue body>
Verify: run tests + lint after each change
```

#### Mode: Autopilot (pluggable)

Delegate to the project's autonomous execution skill (defined in CLAUDE.md routing).
Default: `oh-my-claudecode:autopilot`.

If a spec file exists, pass it as Phase 0 output (autopilot skips expansion).
If a plan file exists, pass it as Phase 1 output (autopilot skips planning).

#### Mode: Guided

Interactive step-by-step implementation:

1. Break the task into small steps (from spec, plan, or issue body)
2. For each step:
   - Show what needs to change
   - Implement the change
   - Run relevant tests
   - Show results, ask to proceed
3. After all steps: run full verification

### Step 4: Chain to Delivery

After implementation completes (any mode except Manual):

```
Implementation complete. Chain to turbo-completion?
1. Yes — run /turbo-completion now (Recommended)
2. Not yet — I want to review changes first
3. Skip — I'll deliver manually later
```

If "Yes": invoke `turbo-completion` skill.
If "Not yet": show `git diff --stat` and wait.

## Error Handling

| Situation | Action |
|-----------|--------|
| No issue number in branch | Ask user for issue reference |
| Spec/plan file not found | Proceed without — suggest Manual or Guided mode |
| Ralph/autopilot not available | Fall back to Guided mode |
| Implementation fails mid-way | Save progress, report status, suggest `/debug` |
| Tests fail after implementation | Invoke `debug` skill for root cause analysis |

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "Skip mode selection, just start coding" | Mode mismatch wastes time — ralph on simple tasks loops forever, manual on complex tasks stalls. |
| "Ralph is always better, pick it by default" | Ralph without acceptance criteria loops without progress. Use it only when criteria exist. |
| "I'll chain to turbo-completion later" | Later never comes. Chain immediately or the worktree accumulates stale changes. |
| "Autopilot on a no-spec task" | Autopilot without a spec invents scope. Pick Guided or write a spec first. |
| "Skip verification, tests will run in CI" | CI finds the failure 10 minutes later, across a PR review cycle. Run locally first. |

## Pipeline Visualization

```
┌─────────────┐    ┌──────────────────┐    ┌──────────────┐
│ TURBO-SETUP │───▶│ TURBO-IMPLEMENT  │───▶│TURBO-DELIVER │
│             │    │                  │    │              │
│ issue       │    │ Step 1: context  │    │ verify       │
│ branch      │    │ Step 2: mode     │    │ review       │
│ worktree    │    │ Step 3: execute  │    │ PR           │
│ deps        │    │ Step 4: chain    │    │ merge        │
└─────────────┘    └──────────────────┘    └──────────────┘
                    modes:
                    · manual (exit)
                    · ralph (loop)
                    · autopilot (autonomous)
                    · guided (interactive)
```

## Chaining Interface

**From turbo-setup:**
```
turbo-setup outputs → git state (branch, worktree, issue#)
turbo-implement reads → git state (auto-detect, no explicit handoff)
```

**To turbo-completion:**
```
turbo-implement completion → invokes turbo-completion skill
turbo-completion reads → git state (auto-detect, no explicit handoff)
```

All three skills share the same auto-detection mechanism: read git branch, extract issue number, detect worktree. No explicit state passing needed.

## Integration

**Workflow position:**
```
[turbo-setup] → [turbo-implement] → [turbo-completion] → [done]
```

**Previous step:** `turbo-setup` (workspace ready)
**Next step:** `turbo-completion` (delivery pipeline)
