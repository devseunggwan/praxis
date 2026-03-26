---
name: turbo-deliver
description: Compound delivery — verify + review + PR + merge + cleanup + compounding in one step. Triggers on "deliver", "turbo-deliver", "마무리", "배달".
---

# Turbo Deliver

## Overview

Compresses workflow steps 9-14 (verify → review → PR → merge → cleanup → compounding) into a single automated pass.

**Core principle:** Delivery is a pipeline, not a checklist. Each stage gates the next.

**Chains from:** `turbo-setup` (auto-detects issue/branch from git state)
**Delegates to:** `verify-completion`, `code-review`, `create-hub-pr`, `finish-branch`

## The Iron Law

```
VERIFY → REVIEW → PR → MERGE → COMPOUND → CLEANUP
EACH STAGE MUST PASS BEFORE THE NEXT BEGINS.
NO SKIPPING. NO REORDERING.
```

## When to Use

- After implementation is complete
- Triggers: "deliver", "turbo-deliver", "마무리", "배달"
- No input required — auto-detects everything from current git state

## Inputs (Auto-Detected)

All inputs are derived from the current working directory:

```bash
BRANCH=$(git branch --show-current)
ISSUE_NUMBER=$(echo "$BRANCH" | grep -oP '(?<=hub-)\d+|(?<=issue-)\d+')
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

## Outputs

```
═══════════════════════════════════════════════
 ✅ Turbo Deliver Complete
═══════════════════════════════════════════════

 PR:        #<number> (<title>) — MERGED
 Issue:     #<issue> — CLOSED
 Worktree:  <path> — REMOVED
 Branch:    <name> — DELETED

 Pipeline:  verify ✅ → review ✅ → PR ✅ → merge ✅ → compound ✅ → cleanup ✅
═══════════════════════════════════════════════
```

## Process

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
if [ -f "go.mod" ]; then
  go test ./...
  golangci-lint run 2>/dev/null
fi
if command -v ruff &>/dev/null; then
  ruff check . && ruff format --check .
fi
```

**On failure:**
1. Auto-fix attempt (ruff format, eslint --fix)
2. Re-run verification
3. If still failing after 2 attempts → **STOP and report to user**

### Stage 2: Code Review (delegates to `code-review`)

Invoke `laplace-dev-hub:code-review` logic:

1. Review diff against base branch
2. Check for:
   - Security vulnerabilities
   - Logic defects
   - SOLID principle violations
   - Python 3.8 compatibility (Airflow containers)
   - Atomic commit compliance

**Severity gates:**
| Severity | Action |
|----------|--------|
| Critical | **STOP** — report to user, do NOT proceed |
| High | **STOP** — report to user |
| Medium | Log as PR comment, proceed |
| Low/Recommended | Log as PR comment, proceed |

### Stage 3: Create PR (delegates to `create-hub-pr`)

```bash
# Find distributed issue in target repo (if Hub issue)
DISTRIBUTED_ISSUE=$(gh issue list --repo "laplacetec/${TARGET_REPO}" \
  --search "hub-${ISSUE_NUMBER}" --json number --jq '.[0].number' 2>/dev/null)

# Determine labels
LABELS=$(gh label list --repo "laplacetec/${TARGET_REPO}" --json name --jq '.[].name' | head -20)
PR_LABEL=$(select_label "$LABELS" "$TYPE")

gh pr create \
  --repo "laplacetec/${TARGET_REPO}" \
  --title "${TITLE}" \
  --label "${PR_LABEL}" \
  --assignee "@me" \
  --body "$(generate_pr_body)"
```

**PR body includes:**
- Summary (from commit messages)
- `Closes #<distributed_issue>` (if exists)
- Test plan
- Review checklist items from Stage 2

**Capture:** `PR_NUMBER`, `PR_URL`

### Stage 4: Merge (delegates to `finish-branch`)

Wait for CI, then squash merge:

```bash
# Wait for CI (poll with timeout)
MAX_WAIT=300  # 5 minutes
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
  STATUS=$(gh pr checks "$PR_NUMBER" --repo "laplacetec/${TARGET_REPO}" 2>/dev/null)
  if echo "$STATUS" | grep -q "All checks were successful"; then
    break
  elif echo "$STATUS" | grep -q "fail"; then
    echo "CI failed. Analyzing..."
    # Auto-fix attempt
    break
  fi
  sleep 10
  ELAPSED=$((ELAPSED + 10))
done
```

### Stage 5: Compound (inline context)

Before merge, add compounding comments:

```bash
# Analyze key decision points in the diff
gh pr diff "$PR_NUMBER" --repo "laplacetec/${TARGET_REPO}"

# Add inline comments at decision points
# Format: # [PR #N] <why this approach>
```

Then merge:

```bash
gh pr merge "$PR_NUMBER" --repo "laplacetec/${TARGET_REPO}" --squash --delete-branch
```

### Stage 6: Cleanup

```bash
cd "$MAIN_REPO"
git worktree remove "$WORKTREE_PATH"
git branch -d "$BRANCH" 2>/dev/null
git checkout "$DEFAULT_BRANCH"
git pull origin "$DEFAULT_BRANCH"
git worktree prune
```

## Error Handling

| Stage | Failure | Action |
|-------|---------|--------|
| Verify | Test failure | Auto-fix (2x), then STOP |
| Verify | Lint failure | Auto-format, re-check |
| Review | Critical finding | **STOP** — report to user |
| PR creation | Label missing | Query available labels, select closest |
| PR creation | Body validation | Auto-fix format |
| CI | Check failure | Analyze logs, auto-fix (1x), then STOP |
| Merge | Conflict | **STOP** — report to user |
| Merge | Approval required | **STOP** — report to user |
| Cleanup | Worktree busy | Report, suggest manual cleanup |

**Escalation pattern:**
```
Auto-fix attempt (silent) → 2nd attempt (silent) → STOP + report to user
```

Never silently swallow errors. Never proceed past a failed gate.

## Pipeline Visualization

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌──────────┐    ┌─────────┐
│ VERIFY  │───▶│ REVIEW  │───▶│   PR    │───▶│  MERGE  │───▶│COMPOUND  │───▶│ CLEANUP │
│         │    │         │    │         │    │         │    │          │    │         │
│ test    │    │ diff    │    │ create  │    │ CI wait │    │ inline   │    │worktree │
│ lint    │    │ security│    │ labels  │    │ squash  │    │ comments │    │ branch  │
│ build   │    │ quality │    │ body    │    │ delete  │    │ PR refs  │    │ prune   │
└────┬────┘    └────┬────┘    └────┬────┘    └────┬────┘    └────┬─────┘    └────┬────┘
     │              │              │              │              │              │
   FAIL→fix      CRIT→STOP     FAIL→fix       FAIL→STOP     skip if         FAIL→
   2x→STOP                     format          conflict      mechanical      report
```

## Chaining Interface

**From turbo-setup:**
```
turbo-setup outputs → git state (branch, worktree, issue#)
turbo-deliver reads → git state (auto-detect, no explicit handoff)
```

**To cmux-orchestrator:**
```
orchestrator dispatches: "turbo-setup $task && implement && turbo-deliver"
turbo-deliver signals completion via exit code + result file
```

## Integration

**Workflow position:**
```
[turbo-setup] → [EXECUTE] → [turbo-deliver] → [done]
                               ├─ verify-completion
                               ├─ code-review
                               ├─ create-hub-pr
                               └─ finish-branch
```

**Previous step:** Implementation (manual or via `ralph`/`executor`)
**Next step:** None (lifecycle ends) or next task from orchestrator queue
