---
name: turbo-setup
description: Compound setup — issue + plan + branch + worktree + deps in one step. Triggers on "setup", "turbo-setup", "quick start".
---

# Turbo Setup

## Overview

Compresses workflow steps 1-6 (issue → plan → branch → worktree → cd → deps) into a single automated pass.

**Core principle:** Ceremony is overhead. Setup should take 3 minutes, not 12.

**Chains to:** `turbo-completion` (after execution phase completes)

## The Iron Law

```
ALL 6 STEPS EXECUTE OR NONE. NO PARTIAL SETUP.
```

If any step fails, roll back completed steps and report.

## When to Use

- Starting any new task (feature, bugfix, refactoring)
- Triggers: "setup", "turbo-setup", "quick start"
- User provides: task description (title or URL)

## Pluggable Steps — Fallback Defaults

When a project's `CLAUDE.md` does not provide routing, these built-in defaults are used:

| Step | Pluggable Via | Fallback Default |
|------|--------------|------------------|
| Issue creation | Project CLAUDE.md issue-creation skill routing | `gh issue create` in the current repo |
| Planning | Project CLAUDE.md planning skill routing | Skip (no built-in planner) |
| Branch creation | — (built-in) | `issue-<N>-<type>-<desc>` on base branch |
| Worktree creation | — (built-in) | `git worktree add ../<branch> -b <branch>` |
| Dependency install | — (built-in auto-detect) | `npm install` / `pip install -r requirements.txt` / `go mod download` / `pip install -e .` |

Projects override these by declaring routing in their `CLAUDE.md`.

## Inputs

The user provides ONE of:
1. **Task description**: `"feat(dashboard): add chart filtering"`
2. **Slack/GitHub URL**: `https://your-org.slack.com/archives/...`
3. **Issue reference**: `"Hub #700 start work"`

## Outputs (for chaining)

After successful setup, report these values (used by `turbo-completion`):

```
═══════════════════════════════════════════════
 ✅ Turbo Setup Complete
═══════════════════════════════════════════════

 Issue:     #<number> (<title>)
 Branch:    issue-<N>-<type>-<desc>
 Worktree:  <path>
 Repo:      <target-repo>
 Deps:      <installed|skipped>

 Next steps (choose one):
 • Same session:  implement inline → /turbo-completion
 • Auto mode:     /turbo-implement (ralph/autopilot)
 • New session:   /cmux-delegate --cwd <worktree> --model <provider>
═══════════════════════════════════════════════
```

## Process

### Step 1: Parse Input

Determine task type and target repo from user input.

**If URL provided:**
- Slack URL → fetch message content via Slack MCP (if configured)
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

### Step 2: Create Issue (pluggable)

Delegate to the project's issue creation skill (defined in project CLAUDE.md routing).
Default: `gh issue create` in the current repo.

```bash
TITLE="<type>(<scope>): <description>"  # Conventional Commits format
BODY="<description with task list>"

gh issue create \
  --title "$TITLE" \
  --label "type:<type>" \
  --body "$BODY"
```

**Capture:** `ISSUE_NUMBER` from output.

**Validation:**
- Title is Conventional Commits format
- No duplicate issues (search first)

### Step 3: Create Branch

```bash
BRANCH="issue-${ISSUE_NUMBER}-${TYPE}-${SHORT_DESC}"
# Example: issue-42-feat-chart-filtering
# Legacy prefix `hub-` is still recognized by turbo-implement/turbo-completion for backward compatibility.
```

**Branch base rules (from CLAUDE.md):**

| Type | Base Branch |
|------|-------------|
| Feature / Refactor / Docs | `dev` (or `main` if no `dev`) |
| Hotfix | `prod` (or `main`) |

### Step 4: Create Worktree

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

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "I'll just create the branch, skip the issue" | No issue = no audit trail. turbo-completion relies on issue number to close the loop. |
| "Worktree is overhead, I'll branch in-place" | In-place branching blocks other work in the main checkout. Worktrees are free. |
| "Deps will install later when I need them" | First `npm test` fails and you lose 2 minutes debugging. Install once, upfront. |
| "Skip the rollback on failure, I'll clean up manually" | Manual cleanup gets forgotten. Partial state confuses future sessions. |
| "I know the scope, no need to ask the clarifying question" | 1 question costs 1 round-trip. Wrong scope costs an entire re-do. |

## Chaining Interface

```
turbo-setup → [choose execution path]
               │
               ├─ same session  → implement inline → turbo-completion
               ├─ auto mode     → turbo-implement  → turbo-completion
               └─ new session   → cmux-delegate --cwd <worktree> --model <provider>
                                    ↓ receives:
                                    - ISSUE_NUMBER (from git branch name)
                                    - WORKTREE_PATH (from cwd)
                                    - TARGET_REPO (from git remote)
```

`turbo-completion` auto-detects these from the current git state — no explicit handoff needed.
`cmux-delegate` spawns an independent session in the worktree; handoff is via `--cwd` flag.

## Integration

**Workflow position:**
```
[user request] → [turbo-setup] → [choose path] ─┬─ inline impl    → [turbo-completion] → [done]
                                                  ├─ turbo-implement → [turbo-completion] → [done]
                                                  └─ cmux-delegate  → [new session]       → [done]
```

**Previous step:** User request (task description or URL)
**Next step (options):**
- Inline: implement in same session, then `/turbo-completion`
- Auto: `/turbo-implement` (selects ralph or autopilot)
- Delegated: `/cmux-delegate --cwd <worktree> --model <provider>`
