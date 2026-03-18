---
name: finish-branch
description: Complete a development branch lifecycle — compound context, merge PR, clean up worktree and branch. Triggers on "cleanup", "worktree cleanup", "finish branch", "branch cleanup".
---

# Finish Branch

## Overview

Handles the full branch completion lifecycle in one pass: verify → compound → merge → cleanup.

**Core principle:** Compounding, merge, and cleanup are one cycle. Never split them.

**Replaces:** `superpowers:finishing-a-development-branch`, `worktree-cleanup`

## The Iron Law

```
NO MERGE WITHOUT COMPOUNDING. NO COMPLETION WITHOUT CLEANUP.
```

## When to Use

- After a PR has been created and is ready to merge
- When the user requests cleanup ("cleanup", "worktree cleanup", etc.)
- After `create-hub-pr` flow completes

## Process

### Step 1: Verify Current State

```bash
CURRENT_BRANCH=$(git branch --show-current)
git worktree list
git status -sb
gh pr list --head "$CURRENT_BRANCH" --state open --json number,title,state,url
```

**Pre-conditions (ALL must be true):**
- [ ] All changes committed and pushed
- [ ] PR exists (open state)
- [ ] CI checks passed (if applicable)

**If no PR exists → STOP.** Create a PR first via `create-hub-pr`.
**If uncommitted changes exist → STOP.** Commit first.
**If PR is already merged → Skip to Step 4** (cleanup only).

### Step 2: Compounding (Before Merge)

Embed context at key decision points in the codebase so future sessions pick it up naturally.
The PR number is known from Step 1 — use it for inline references.

1. Identify the PR and its key changes:

```bash
PR_NUMBER=<from Step 1>
gh pr view $PR_NUMBER --json title,body,files
gh pr diff $PR_NUMBER --name-only
```

2. Analyze changes and identify key decision points:
   - Architectural choices (why this approach over alternatives)
   - Non-obvious logic (gotchas, workarounds, constraints)
   - Configuration changes with rationale

3. Add inline comments at those decision points:

```python
# [PR #42] Switched from batch INSERT to MERGE to handle duplicate keys.
# See PR for migration context and rollback plan.
```

**Compounding rules:**
- Brief summary + PR number as inline comment at the relevant code location
- Explain "why this approach" — not "what the code does"
- Do NOT create separate documentation files
- For deeper context, reference the PR: `gh pr view #N`
- Skip compounding if the change is purely mechanical (rename, version bump, typo fix)

4. Commit and push compounding changes:

```bash
git add -A
git commit -m "chore: add compounding context for PR #<number>

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
git push
```

> **If no decision points worth documenting**, skip the commit and proceed to Step 3.

### Step 3: Merge (Squash)

Merge the PR using squash merge to keep history clean.

```bash
gh pr merge $PR_NUMBER --squash --delete-branch
```

**Verify merge succeeded:**

```bash
gh pr view $PR_NUMBER --json state,mergedAt
```

**If merge fails:**
- CI check failure → fix and retry
- Merge conflicts → resolve, push, retry
- Requires approval → notify user and STOP

### Step 4: Cleanup

Present options to the user:

```
PR #<number> merged successfully. How would you like to proceed?

1. Clean up worktree + branch (recommended)
2. Clean up worktree only (keep branch)
3. Keep everything as-is (handle later)
```

#### Option 1: Full Cleanup (recommended)

```bash
cd <main-repo-path>

git worktree remove <worktree-path>
git branch -d <branch-name> 2>/dev/null  # may already be deleted by --delete-branch

DEFAULT_BRANCH=$(git remote show origin 2>/dev/null | grep 'HEAD branch' | awk '{print $NF}')
git checkout "$DEFAULT_BRANCH"
git pull origin "$DEFAULT_BRANCH"

git worktree prune
```

#### Option 2: Worktree Only

```bash
cd <main-repo-path>
git worktree remove <worktree-path>
git worktree prune
```

#### Option 3: Keep As-Is

Report: "Keeping branch `<name>`. Worktree at `<path>`."

### Step 5: Learning Capture

Review this work cycle and capture any reusable lessons:

1. **Check for patterns worth remembering:**
   - Did you discover a non-obvious workaround?
   - Did a tool/approach work better than expected?
   - Was there a gotcha that wasted time?
   - Did the user give feedback that should persist?

2. **If lessons found**, update project memory:
   ```bash
   # Check existing memories
   ls ~/.claude/projects/*/memory/*.md 2>/dev/null
   ```
   - Update existing memory file if the lesson extends a known pattern
   - Create new memory file if it's a novel insight
   - Skip if nothing new was learned (don't create noise)

3. **If no lessons**, skip this step. Not every PR teaches something new.

> **Rule:** Only capture insights that will prevent future mistakes or save future time.
> Do NOT create memory entries for routine, well-understood work.

### Step 6: Verify Cleanup

```bash
git worktree list
git branch --list "*<issue-number>*"
hubctl status 2>/dev/null  # clean up hubctl env if exists
```

## Per-Repo Default Branch Reference

| Repository | Default Branch |
|------------|---------------|
| `laplace-dev-hub` | `main` |
| `laplace-web-v2` | `main` |
| `laplace-data-platform-mcp` | `main` |
| `laplace-airflow-dags` | `dev` |
| `laplace-airflow-dags-v3` | `dev` |
| `laplace-etl` | `dev` |
| `laplace-analytics-backend` | `dev` |
| `analytics-frontend` | `dev` |
| `laplace-gitops` | `dev` |
| `laplace-ai-agent` | `dev` |

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "I'll clean up later" | Later never comes. Zombie worktrees accumulate. |
| "Compounding can be skipped this time" | Next session loses "why was this done?" context. |
| "One leftover branch is fine" | Stale branches pollute `git branch` output. |
| "Simple change, no compounding needed" | Even simple changes have a "why". Leave the PR number at minimum. |
| "I'll compound after merge" | After merge, worktree is gone — can't add code comments on the feature branch. |

## Red Flags — STOP

- Attempting cleanup with uncommitted changes
- Merging without compounding first
- Running `git worktree remove` from inside the target worktree
- Claiming "cleanup complete" without verifying merge

## Integration

**Workflow position:**
```
[create-hub-pr] → [finish-branch] → [done]
                   ├─ compound (on feature branch)
                   ├─ merge (squash)
                   └─ cleanup (worktree + branch)
```

**Previous step:** `create-hub-pr` (PR creation)
**Next step:** None (lifecycle ends)
