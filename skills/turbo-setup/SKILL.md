---
name: turbo-setup
description: Compound setup — issue + plan + branch + worktree + deps in one step. Triggers on "setup", "착수", "turbo-setup", "quick start".
---

# Turbo Setup

## Overview

Compresses workflow steps 1-6 (issue → plan → branch → worktree → cd → deps) into a single automated pass.

**Core principle:** Ceremony is overhead. Setup should take 3 minutes, not 12.

**Chains to:** `turbo-deliver` (after execution phase completes)

## The Iron Law

```
ALL 6 STEPS EXECUTE OR NONE. NO PARTIAL SETUP.
```

If any step fails, roll back completed steps and report.

## When to Use

- Starting any new task (feature, bugfix, refactoring)
- Triggers: "setup", "착수", "turbo-setup", "quick start"
- User provides: task description (title or URL)

## Inputs

The user provides ONE of:
1. **Task description**: `"feat(dashboard): add chart filtering"`
2. **Slack/GitHub URL**: `https://laplacetec.slack.com/archives/...`
3. **Issue reference**: `"Hub #700 작업 시작"`

## Outputs (for chaining)

After successful setup, report these values (used by `turbo-deliver`):

```
═══════════════════════════════════════════════
 ✅ Turbo Setup Complete
═══════════════════════════════════════════════

 Issue:     #<number> (<title>)
 Branch:    hub-<N>-<type>-<desc>
 Worktree:  <path>
 Repo:      <target-repo>
 Deps:      <installed|skipped>

 Next: Implement the feature, then run /turbo-deliver
═══════════════════════════════════════════════
```

## Process

### Step 1: Parse Input

Determine task type and target repo from user input.

**If URL provided:**
- Slack URL → fetch message content via `mcp__laplace-slack__slack_get_channel_history`
- GitHub URL → parse repo/issue/PR reference
- Extract task description from content

**If description provided:**
- Parse conventional commit format: `type(scope): description`
- Map scope to target repo using Feature-Repo Mapping (from project CLAUDE.md)

**If ambiguous → ask ONE clarifying question** (max 1 round-trip):

```
What type of task is this?
1. Feature (new functionality)
2. Bug fix
3. Refactoring
4. Documentation
5. Other
```

### Step 2: Create Issue

Invoke `laplace-dev-hub:create-hub-issue` skill logic:

```bash
# Determine if Hub issue or repo-local issue
# Hub issue: cross-repo features → laplace-dev-hub
# Repo issue: repo-specific fixes → target repo

TITLE="<type>(<scope>): <description>"  # English, Conventional Commits
BODY="<Korean description with task list>"

gh issue create \
  --repo laplacetec/laplace-dev-hub \
  --title "$TITLE" \
  --label "type:<type>" \
  --body "$BODY"
```

**Capture:** `ISSUE_NUMBER` from output.

**Validation:**
- Title is Conventional Commits format
- Body contains Korean text
- No duplicate issues (search first)

### Step 3: Create Branch

```bash
BRANCH="hub-${ISSUE_NUMBER}-${TYPE}-${SHORT_DESC}"
# Example: hub-700-feat-chart-filtering
```

**Branch base rules (from CLAUDE.md):**

| Type | Base Branch |
|------|-------------|
| Feature / Refactor / Docs | `dev` (or `main` if no `dev`) |
| Hotfix | `prod` (or `main`) |

### Step 4: Create Worktree

Invoke `oh-my-claudecode:project-session-manager` logic:

```bash
# Determine target repo path
TARGET_REPO=$(resolve_repo_path "<scope>")

cd "$TARGET_REPO"
git fetch origin
git worktree add "../${BRANCH}" -b "$BRANCH" "origin/${BASE_BRANCH}"
WORKTREE_PATH=$(realpath "../${BRANCH}")
```

### Step 5: Install Dependencies

Auto-detect and install:

```bash
cd "$WORKTREE_PATH"

# Detect package manager
if [ -f "package.json" ]; then
  npm install
elif [ -f "requirements.txt" ]; then
  pip install -r requirements.txt
elif [ -f "go.mod" ]; then
  go mod download
elif [ -f "pyproject.toml" ]; then
  pip install -e .
fi

# Airflow-specific: init submodules
if [ -f ".gitmodules" ]; then
  git submodule update --init --recursive
fi

# Copy .env if exists in main repo
MAIN_REPO=$(git worktree list | head -1 | awk '{print $1}')
if [ -f "$MAIN_REPO/.env" ] && [ ! -f ".env" ]; then
  cp "$MAIN_REPO/.env" .env
fi
```

### Step 6: Verify Setup

```bash
# Verify all pieces are in place
echo "Issue: $(gh issue view $ISSUE_NUMBER --json number,title --jq '.number')"
echo "Branch: $(git branch --show-current)"
echo "Worktree: $(pwd)"
echo "Deps: $(ls node_modules 2>/dev/null && echo 'npm' || ls .venv 2>/dev/null && echo 'pip' || echo 'none')"
```

## Error Handling

| Step | Failure | Action |
|------|---------|--------|
| Issue creation | Duplicate found | Link existing issue, skip creation |
| Issue creation | Label missing | Auto-create or use closest match |
| Branch creation | Already exists | Reuse if same issue, error if different |
| Worktree creation | Path conflict | Suggest alternative path |
| Deps install | Package error | Report and continue (non-blocking) |
| Any step | Unknown error | Roll back completed steps, report |

**Rollback order (reverse):**
```
deps → skip (no rollback needed)
worktree → git worktree remove
branch → git branch -D
issue → gh issue close (with /cancel comment)
```

## Feature-Repo Mapping (Quick Reference)

| Keyword | Target Repo | Default Branch |
|---------|-------------|----------------|
| dag, pipeline, etl | laplace-airflow-dags | dev |
| dag v3, airflow v3 | laplace-airflow-dags-v3 | dev |
| api, endpoint, backend | laplace-analytics-backend | dev |
| ui, component, chart | laplace-web-v2 | main |
| hub, tooling | laplace-dev-hub | main |

## Chaining Interface

```
turbo-setup → [user implements] → turbo-deliver
                                   ↓ receives:
                                   - ISSUE_NUMBER (from git branch name)
                                   - WORKTREE_PATH (from cwd)
                                   - TARGET_REPO (from git remote)
```

`turbo-deliver` auto-detects these from the current git state — no explicit handoff needed.

## Integration

**Workflow position:**
```
[user request] → [turbo-setup] → [EXECUTE] → [turbo-deliver] → [done]
```

**Previous step:** User request (task description or URL)
**Next step:** Implementation (manual or via `ralph`/`executor`)
