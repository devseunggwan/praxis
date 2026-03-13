---
name: finish-branch
description: Complete a development branch lifecycle — verify merge status, clean up worktree and branch, then compound context via inline PR comments. Triggers on "cleanup", "worktree cleanup", "finish branch", "branch cleanup".
---

# Finish Branch

## Overview

Handles the full branch completion lifecycle in one pass: verify → options → cleanup → compound.

**Core principle:** Merge, cleanup, and compounding are one cycle. Never split them.

**Replaces:** `superpowers:finishing-a-development-branch`, `worktree-cleanup`

## The Iron Law

```
NO CLEANUP WITHOUT MERGE. NO COMPLETION WITHOUT COMPOUNDING.
```

## When to Use

- After a PR has been merged
- After a local merge is complete
- When the user requests cleanup ("cleanup", "worktree cleanup", etc.)
- After `create-hub-pr` flow completes and PR is merged

## Process

### Step 1: Verify Current State

```bash
CURRENT_BRANCH=$(git branch --show-current)
git worktree list
git status -sb
gh pr list --head "$CURRENT_BRANCH" --state all --json number,title,state,mergedAt
```

**Pre-conditions (ALL must be true):**
- [ ] All changes committed and pushed
- [ ] PR is merged (or local merge is complete)
- [ ] Working directory is OUTSIDE the worktree being removed

**If PR is NOT merged → STOP.** Complete the merge first.
**If uncommitted changes exist → STOP.** Commit first.

### Step 2: Present Options

```
Branch work is complete. How would you like to proceed?

1. Clean up worktree + branch (recommended)
2. Clean up worktree only (keep branch)
3. Keep everything as-is (handle later)
4. Discard this work (delete unmerged branch)

Which option?
```

### Step 3: Execute Choice

#### Option 1: Full Cleanup (recommended)

```bash
cd <main-repo-path>

git worktree remove <worktree-path>
git branch -d <branch-name>

DEFAULT_BRANCH=$(git remote show origin 2>/dev/null | grep 'HEAD branch' | awk '{print $NF}')
git checkout "$DEFAULT_BRANCH"
git pull origin "$DEFAULT_BRANCH"

git worktree prune
```

→ Proceed to Step 4 (Compounding).

#### Option 2: Worktree Only

```bash
cd <main-repo-path>
git worktree remove <worktree-path>
git worktree prune
```

→ Proceed to Step 4 (Compounding).

#### Option 3: Keep As-Is

Report: "Keeping branch `<name>`. Worktree at `<path>`."

**Do NOT cleanup. Do NOT compound. End here.**

#### Option 4: Discard

**Require explicit confirmation:**

```
⚠️ This will permanently delete:
- Branch: <branch-name>
- Commits: <commit-list>
- Worktree: <worktree-path>

Type 'discard' to confirm.
```

Execute ONLY after 'discard' is typed:

```bash
cd <main-repo-path>
git worktree remove --force <worktree-path>
git branch -D <branch-name>
git worktree prune
```

→ Skip compounding (no PR to reference).

### Step 4: Compounding (Required for Options 1 and 2)

Embed context at key decision points in the codebase so future sessions pick it up naturally.

1. Identify the merged PR and its key changes:

```bash
gh pr view <pr-number> --json title,body,files
gh pr diff <pr-number> --name-only
```

2. Add inline comments at key decision points:

```python
# [PR #42] Switched from batch INSERT to MERGE to handle duplicate keys.
# See PR for migration context and rollback plan.
```

**Compounding rules:**
- Brief summary + PR number as inline comment at the relevant code location
- Explain "why this approach" — not "what the code does"
- Do NOT create separate documentation files
- For deeper context, reference the PR: `gh pr view #N`

### Step 4.5: Learning Capture

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

### Step 5: Verify Cleanup

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

## Red Flags — STOP

- Attempting cleanup with uncommitted changes
- Deleting a branch whose PR is not yet merged
- Running `git worktree remove` from inside the target worktree
- Claiming "cleanup complete" without compounding

## Integration

**Workflow position:**
```
[create-hub-pr] → [PR merged] → [finish-branch] → [done]
```

**Previous step:** `create-hub-pr` (PR creation and merge)
**Next step:** None (lifecycle ends)
