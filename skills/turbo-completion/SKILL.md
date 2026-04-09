---
name: turbo-completion
description: >
  Compound completion — verify + review + PR + merge + cleanup + compounding in one step.
  Auto-detects PR state to choose full pipeline, merge-only, or verify-only mode.
  Triggers on "deliver", "turbo-deliver", "turbo-completion", "finish up", "cleanup",
  "finish branch", "branch cleanup", "verify", "verification", "done check", "completion check".
---

# Turbo Completion

## Overview

Completes the full delivery lifecycle in a single automated pass.
Auto-detects whether a PR exists to choose the right mode.
Also serves as the standalone verification gate via `--verify-only`.

**Core principle:** Evidence before claims. Each stage gates the next. No shortcuts.

**Chains from:** `turbo-implement` (auto-detects issue/branch from git state)
**Delegates to:** Project's code review skill, project's PR creation skill

## The Iron Law

```
NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE.
EACH STAGE MUST PASS BEFORE THE NEXT BEGINS.
NO SKIPPING. NO REORDERING.
```

If you haven't run the verification command in this message, you cannot claim it passes.

## When to Use

- After implementation is complete (full pipeline)
- When a PR is ready to merge (merge-only mode)
- Before ANY completion claim — "done", "fixed", "passes" (`--verify-only`)
- Triggers: "deliver", "turbo-completion", "finish up", "cleanup", "verify", "done check"
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
VERIFY_ONLY=false  # set true if --verify-only flag or "verify"/"done check" trigger
```

| Priority | Condition | Mode | Pipeline |
|----------|-----------|------|----------|
| 1st | `--verify-only` flag or verify trigger | **Verify-only** | verify (Stage 1 only) |
| 2nd | No open PR for current branch | **Full** | verify → review → PR → merge → compound → cleanup |
| 3rd | Open PR exists | **Merge-only** | compound → merge → cleanup |

> Conditions are evaluated top-to-bottom; first match wins.

Present the detected mode and ask for confirmation:

```
Detected: <state description>
1. Verify-only — run tests/lint/build, report evidence
2. Full pipeline — verify → review → PR → merge → cleanup
3. Merge-only — compound + merge + cleanup (PR exists)
4. Cancel
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

**Validation (STOP if any fails) — full/merge-only modes:**
- [ ] Currently in a worktree (not main repo)
- [ ] Branch name contains issue number
- [ ] Changes are committed (no dirty state)
- [ ] Branch is pushed to remote

**Validation — verify-only mode:**
- [ ] None required (runs anywhere)

## Full Pipeline

### Stage 1: Verify (The Gate)

**The Gate Function — run BEFORE claiming any status:**

```
1. IDENTIFY: What command proves this claim?
2. RUN: Execute the FULL command (fresh, complete)
3. READ: Full output, check exit code, count failures
4. VERIFY: Does output confirm the claim?
   - If NO: State actual status with evidence
   - If YES: State claim WITH evidence
5. ONLY THEN: Make the claim

Skip any step = lying, not verifying
```

**Verification Targets (auto-detect):**

| Target | Typical Command | Required? |
|--------|----------------|-----------|
| Unit tests | `pytest -v`, `npm test`, `go test ./...` | **Always** |
| Lint | `ruff check .`, `eslint .`, `golangci-lint run` | **Always** |
| Build | `npm run build`, `cargo build`, `go build ./...` | If build system exists |
| Type check | `mypy .`, `tsc --noEmit` | If type system exists |
| Functional test | DAG trigger, API call, CLI execution | If external systems changed |

```bash
# Auto-detect and run
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

**Evidence Reporting — show actual output for each target:**

```
Verification results:
- Tests: 34/34 pass (0 failures) — `pytest -v` output confirmed
- Lint: 0 errors, 0 warnings — `ruff check .` output confirmed
- Build: exit code 0 — `npm run build` output confirmed
```

**Common Failures — what counts as evidence:**

| Claim | Requires | Not Sufficient |
|-------|----------|----------------|
| Tests pass | Test command output: 0 failures | Previous run, "should pass" |
| Linter clean | Linter output: 0 errors | Partial check, extrapolation |
| Build succeeds | Build command: exit 0 | Linter passing, "logs look good" |
| Bug fixed | Test original symptom: passes | "Code changed, assumed fixed" |
| Requirements met | Line-by-line checklist verified | "Tests pass" alone |
| API works | Response body content verified | HTTP 200 status code alone |

**OMC ultraqa delegation** — when available, delegate to `ultraqa` for the fix→retry cycle:

```
ultraqa cycle: test → verify → fix (on failure) → repeat (until pass)
```

If `ultraqa` is unavailable, run the manual retry:

**On failure:**
1. Auto-fix attempt (ruff format, eslint --fix)
2. Re-run verification
3. If still failing after 2 attempts → **STOP and report to user**

**If `--verify-only`:** Report evidence and exit. Do not proceed to Stage 2.

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

### Full / Merge-only mode

```
===================================================
 Turbo Completion Complete
===================================================

 PR:        #<number> (<title>) — MERGED
 Issue:     #<issue> — CLOSED
 Worktree:  <path> — REMOVED
 Branch:    <name> — DELETED
 Mode:      full | merge-only

 Pipeline:  verify > review > PR > merge > compound > cleanup
===================================================
```

### Verify-only mode

```
===================================================
 Verification Complete
===================================================

 Branch:    <name>
 Results:
 - Tests:   34/34 pass
 - Lint:    0 errors
 - Build:   exit 0

 Status:    PASS | FAIL (details above)
===================================================
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
  ├─ --verify-only → Verify-Only (Stage 1 only)
  ├─ No PR → Full Pipeline (Stages 1-3, then 4-7)
  └─ PR exists → Merge-Only (Stages 4-7)

┌─────────┐    ┌─────────┐    ┌─────────┐    ┌──────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│ VERIFY  │───▶│ REVIEW  │───▶│   PR    │───▶│COMPOUND  │───▶│  MERGE  │───▶│ CLEANUP │───▶│ LEARN   │
│ (1)     │    │ (2)     │    │ (3)     │    │ (4)      │    │ (5)     │    │ (6)     │    │ (7)     │
└─────────┘    └─────────┘    └─────────┘    └──────────┘    └─────────┘    └─────────┘    └─────────┘
 all modes      full only      full only      both modes      both modes     both modes     both modes
    ▲
    └─ --verify-only stops here
```

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "Should work now" | RUN the verification |
| "I'm confident" | Confidence is not evidence |
| "I'll clean up later" | Later never comes. Zombie worktrees accumulate. |
| "Compounding can be skipped this time" | Next session loses "why was this done?" context. |
| "Simple change, no verification needed" | Simple changes break too. Run it. |
| "I'll compound after merge" | After merge, worktree is gone — can't add code comments on the feature branch. |
| "HTTP 200 means it works" | Check response body content, not just status code. |
| "Agent said success" | Verify independently. |
| "Partial check is enough" | Partial proves nothing. |

## Red Flags — STOP

If you catch yourself about to:

- Use "should", "probably", "seems to"
- Express satisfaction before verification ("Great!", "Done!")
- Commit / push / create PR without verification
- Trust agent success reports at face value
- Rely on partial verification
- Skip the approval step for any stage

**ALL of these mean: STOP. Run the Gate Function (Stage 1).**

## Integration

**Workflow position:**
```
[turbo-setup] → [turbo-implement] → [turbo-completion] → [done]
                                          ├─ --verify-only: Stage 1 only
                                          ├─ Full: verify → review → PR
                                          └─ Both: compound → merge → cleanup → learn
```

**Previous step:** Implementation (manual or via `ralph`/`executor`)
**Next step:** None (lifecycle ends) or next task from orchestrator queue
