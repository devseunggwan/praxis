---
name: turbo-deliver
description: >
  Compound delivery — verify + review + PR + merge + cleanup + compounding in one step.
  Auto-detects PR state to choose full pipeline or merge-only mode.
  Triggers on "deliver", "turbo-deliver", "finish up", "cleanup", "finish branch", "branch cleanup".
---

# Turbo Deliver

## Overview

Compresses the full delivery lifecycle into a single automated pass.
Auto-detects whether a PR exists to choose the right mode.

**Core principle:** Delivery is a pipeline, not a checklist. Each stage gates the next.

**Chains from:** `turbo-setup` (auto-detects issue/branch from git state)
**Delegates to:** `verify-completion`, project's code review skill, project's PR creation skill

## The Iron Law

```
EACH STAGE MUST PASS BEFORE THE NEXT BEGINS.
NO SKIPPING. NO REORDERING.
```

## When to Use

- After implementation is complete
- When a PR is ready to merge (merge-only mode)
- Triggers: "deliver", "turbo-deliver", "finish up", "cleanup", "finish branch", "branch cleanup"
- No input required — auto-detects everything from current git state

## Pluggable Steps — Fallback Defaults

When a project's `CLAUDE.md` does not provide routing, these built-in defaults are used:

| Stage | Pluggable Via | Fallback Default |
|-------|--------------|------------------|
| Verify | — (built-in) | Auto-detect: `pytest`, `npm test`, `ruff check`, etc. |
| Code review | Project CLAUDE.md review skill routing | `oh-my-claudecode:code-reviewer` agent |
| PR creation | Project CLAUDE.md PR-creation skill routing | `gh pr create` with auto-detected repo |
| Merge | — (built-in) | `gh pr merge --squash --delete-branch` |
| Compound | — (built-in) | Inline code comments with PR reference |
| Cleanup | — (built-in) | `git worktree remove` + `git branch -d` |

Projects override these by declaring routing in their `CLAUDE.md`.

## Mode Detection (Step 0)

On start, detect the current state and choose mode automatically:

```bash
BRANCH=$(git branch --show-current)
PR_JSON=$(gh pr list --head "$BRANCH" --state open --json number,title,url --jq '.[0]')
```

| Condition | Mode | Pipeline |
|-----------|------|----------|
| No open PR for current branch | **Full** | verify → review → PR → merge → compound → cleanup |
| Open PR exists | **Merge-only** | compound → merge → cleanup |

Present the detected mode and ask for confirmation:

```
Detected: PR #<N> exists for branch <branch>.
1. Merge-only — compound + merge + cleanup
2. Full pipeline — re-run verify + review before merge
3. Cancel
```

## Inputs (Auto-Detected)

All inputs are derived from the current working directory:

```bash
BRANCH=$(git branch --show-current)
ISSUE_NUMBER=$(echo "$BRANCH" | grep -oP '(?<=issue-)\d+|(?<=hub-)\d+')
WORKTREE_PATH=$(pwd)
TARGET_REPO=$(basename $(git remote get-url origin) .git)
DEFAULT_BRANCH=$(git remote show origin 2>/dev/null | grep 'HEAD branch' | awk '{print $NF}')
MAIN_REPO=$(git worktree list | head -1 | awk '{print $1}')
```

**Validation (STOP if any fails):**
- [ ] Currently in a worktree (not main repo)
- [ ] Branch name contains issue number
- [ ] Changes are committed (no dirty state)
- [ ] Branch is pushed to remote

## Full Pipeline

### Stage 1: Verify (delegates to `verify-completion`)

Run all verification targets. **MUST pass before proceeding.**

```bash
# Auto-detect verification targets
if [ -f "pytest.ini" ] || [ -f "setup.py" ] || [ -d "tests" ]; then
  pytest -v
fi
if [ -f "package.json" ]; then
  npm test 2>/dev/null
  npm run lint 2>/dev/null
fi
if command -v ruff &>/dev/null; then
  ruff check . && ruff format --check .
fi
```

**On failure:**
1. Auto-fix attempt (ruff format, eslint --fix)
2. Re-run verification
3. If still failing after 2 attempts → **STOP and report to user**

### Stage 2: Code Review (pluggable)

Delegate to the project's code review skill (defined in project CLAUDE.md routing).
Default: `oh-my-claudecode:code-reviewer` agent.

1. Review diff against base branch
2. Check for security vulnerabilities, logic defects, SOLID violations

**Severity gates:**
| Severity | Action |
|----------|--------|
| Critical | **STOP** — report to user, do NOT proceed |
| High | **STOP** — report to user |
| Medium | Log as PR comment, proceed |
| Low/Recommended | Log as PR comment, proceed |

### Stage 3: Create PR (pluggable)

Delegate to the project's PR creation skill (defined in project CLAUDE.md routing).
Default: `gh pr create` with auto-detected repo.

```bash
gh pr create \
  --title "${TITLE}" \
  --label "${PR_LABEL}" \
  --assignee "@me" \
  --body "$(generate_pr_body)"
```

**Capture:** `PR_NUMBER`, `PR_URL`

## Merge-Only Pipeline

Starts here when an open PR is detected. Also reached after Stage 3 in full pipeline.

### Stage 4: Compound (inline context)

Embed context at key decision points before merge.

```bash
PR_NUMBER=<detected or created>
gh pr view $PR_NUMBER --json title,body,files
gh pr diff $PR_NUMBER --name-only
```

1. Identify key decision points:
   - Architectural choices (why this approach over alternatives)
   - Non-obvious logic (gotchas, workarounds, constraints)
   - Configuration changes with rationale

2. Add inline comments:

```python
# [PR #42] Switched from batch INSERT to MERGE to handle duplicate keys.
# See PR for migration context and rollback plan.
```

**Compounding rules:**
- Brief summary + PR number as inline comment at the relevant code location
- Explain "why this approach" — not "what the code does"
- Do NOT create separate documentation files
- Skip compounding if the change is purely mechanical (rename, version bump, typo fix)

3. Commit and push compounding changes (if any):

```bash
git add -A
git commit -m "chore: add compounding context for PR #<number>

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
git push
```

### Stage 5: Merge (squash)

Wait for CI, then squash merge:

```bash
MAX_WAIT=300  # 5 minutes
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
  STATUS=$(gh pr checks "$PR_NUMBER" --repo "${TARGET_REPO}" 2>/dev/null)
  if echo "$STATUS" | grep -q "All checks were successful"; then
    break
  elif echo "$STATUS" | grep -q "fail"; then
    echo "CI failed. Analyzing..."
    break
  fi
  sleep 10
  ELAPSED=$((ELAPSED + 10))
done

gh pr merge "$PR_NUMBER" --repo "${TARGET_REPO}" --squash --delete-branch
```

### Stage 6: Cleanup

Present options to the user:

```
PR #<number> merged. How to proceed?
1. Full cleanup — remove worktree + branch (recommended)
2. Worktree only — keep branch
3. Keep as-is — handle later
```

#### Option 1: Full Cleanup (recommended)

```bash
cd "$MAIN_REPO"
git worktree remove "$WORKTREE_PATH"
git branch -d "$BRANCH" 2>/dev/null
git checkout "$DEFAULT_BRANCH"
git pull origin "$DEFAULT_BRANCH"
git worktree prune
```

#### Option 2: Worktree Only

```bash
cd "$MAIN_REPO"
git worktree remove "$WORKTREE_PATH"
git worktree prune
```

#### Option 3: Keep As-Is

Report: "Keeping branch `<name>`. Worktree at `<path>`."

### Stage 7: Learning Capture

Review this work cycle and capture reusable lessons:

1. **Check for patterns worth remembering:**
   - Non-obvious workaround discovered?
   - Tool/approach that worked better than expected?
   - Gotcha that wasted time?
   - User feedback that should persist?

2. **If lessons found**, update project memory
3. **If no lessons**, skip. Not every PR teaches something new.

> **Rule:** Only capture insights that prevent future mistakes or save future time.

## Outputs

```
═══════════════════════════════════════════════
 ✅ Turbo Deliver Complete
═══════════════════════════════════════════════

 PR:        #<number> (<title>) — MERGED
 Issue:     #<issue> — CLOSED
 Worktree:  <path> — REMOVED
 Branch:    <name> — DELETED
 Mode:      full | merge-only

 Pipeline:  verify ✅ → review ✅ → PR ✅ → merge ✅ → compound ✅ → cleanup ✅
═══════════════════════════════════════════════
```

## Error Handling

| Stage | Failure | Action |
|-------|---------|--------|
| Verify | Test failure | Auto-fix (2x), then STOP |
| Verify | Lint failure | Auto-format, re-check |
| Review | Critical finding | **STOP** — report to user |
| PR creation | Label missing | Query available labels, select closest |
| CI | Check failure | Analyze logs, auto-fix (1x), then STOP |
| Merge | Conflict | **STOP** — report to user |
| Merge | Approval required | **STOP** — report to user |
| Cleanup | Worktree busy | Report, suggest manual cleanup |

**Escalation pattern:**
```
Auto-fix attempt (silent) → 2nd attempt (silent) → STOP + report to user
```

## Pipeline Visualization

```
Step 0: MODE DETECTION
  ├─ No PR → Full Pipeline (Stages 1-3, then 4-7)
  └─ PR exists → Merge-Only (Stages 4-7)

┌─────────┐    ┌─────────┐    ┌─────────┐    ┌──────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│ VERIFY  │───▶│ REVIEW  │───▶│   PR    │───▶│COMPOUND  │───▶│  MERGE  │───▶│ CLEANUP │───▶│ LEARN   │
│ (1)     │    │ (2)     │    │ (3)     │    │ (4)      │    │ (5)     │    │ (6)     │    │ (7)     │
└─────────┘    └─────────┘    └─────────┘    └──────────┘    └─────────┘    └─────────┘    └─────────┘
 full only      full only      full only      both modes      both modes     both modes     both modes
```

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "I'll clean up later" | Later never comes. Zombie worktrees accumulate. |
| "Compounding can be skipped this time" | Next session loses "why was this done?" context. |
| "Simple change, no compounding needed" | Even simple changes have a "why". Leave the PR number at minimum. |
| "I'll compound after merge" | After merge, worktree is gone — can't add code comments on the feature branch. |

## Integration

**Workflow position:**
```
[turbo-setup] → [EXECUTE] → [turbo-deliver] → [done]
                               ├─ Step 0: mode detection
                               ├─ Full: verify → review → PR
                               └─ Both: compound → merge → cleanup → learn
```

**Previous step:** Implementation (manual or via `ralph`/`executor`)
**Next step:** None (lifecycle ends) or next task from orchestrator queue
