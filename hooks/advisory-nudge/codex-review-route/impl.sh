#!/bin/bash
# codex-review-route.sh — UserPromptSubmit hook
#
# Fires when the user invokes /codex:review (bare) or codex-review-wrap.
# Emits additionalContext advisories for:
#   1. Multi-worktree detected (>= 2 non-bare worktrees) — bare /codex:review only.
#      codex-review-wrap is suppressed here because the skill itself handles
#      worktree disambiguation (Step 2).
#   2. Current branch's PR is CLOSED or MERGED — stale-state guard for both triggers.
#      Fix commits pushed after a closed PR create orphan branches or reopen
#      closed PRs unintentionally.
#
# Fail-safe: jq missing, malformed JSON, no git repo, gh absent, or any
# unexpected error all exit 0 silently. The hook never blocks a prompt.

set +e

# jq guard — Claude Code session must keep functioning even without jq
if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty' 2>/dev/null)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // ""' 2>/dev/null)

# shellcheck source=../../_lib/record_fire.sh
. "$(dirname "$0")/../../_lib/record_fire.sh" 2>/dev/null || true
command -v praxis_fire_arm >/dev/null 2>&1 && \
  praxis_fire_arm codex-review-route advisory-nudge "$SESSION_ID" ""

if [ -z "$PROMPT" ]; then
  exit 0
fi

# Detect trigger type.
# IS_BARE_CODEX_REVIEW — prompt starts with /codex:review or /codex-review.
# IS_CODEX_REVIEW_WRAP — prompt mentions codex-review-wrap (skill invocation).
IS_BARE_CODEX_REVIEW=0
IS_CODEX_REVIEW_WRAP=0

if [[ "$PROMPT" =~ ^/codex(:|-)review([[:space:]]|$) ]]; then
  IS_BARE_CODEX_REVIEW=1
fi
if [[ "$PROMPT" =~ ^(/praxis:)?codex-review-wrap([[:space:]]|$) ]]; then
  IS_CODEX_REVIEW_WRAP=1
fi

if [ "$IS_BARE_CODEX_REVIEW" -eq 0 ] && [ "$IS_CODEX_REVIEW_WRAP" -eq 0 ]; then
  exit 0
fi

# --- Advisory 1: multi-worktree (bare /codex:review only) -----------------------
# Count active non-bare, non-prunable worktrees. `git worktree list
# --porcelain` emits one block per entry (blank-line separated), each
# starting with `worktree <path>`. Bare repos have a `bare` line; stale
# worktrees have a `prunable` line. Both must be excluded — counting raw
# `worktree` lines would over-count bare-repo + linked-worktree setups
# and trigger false-positive warnings on single-target reviews.

WORKTREE_MSG=""
if [ "$IS_BARE_CODEX_REVIEW" -eq 1 ]; then
  WT_COUNT=$(git worktree list --porcelain 2>/dev/null | awk '
    /^$/ {
      if (in_block && !is_bare && !is_prunable) count++
      in_block = 0; is_bare = 0; is_prunable = 0
      next
    }
    /^worktree / { in_block = 1 }
    /^bare$/ { is_bare = 1 }
    /^prunable( |$)/ { is_prunable = 1 }
    END {
      if (in_block && !is_bare && !is_prunable) count++
      print count + 0
    }
  ')

  if [[ "$WT_COUNT" =~ ^[0-9]+$ ]] && [ "$WT_COUNT" -ge 2 ] 2>/dev/null; then
    read -r -d '' WORKTREE_MSG <<EOF
⚠️ Multi-worktree detected (${WT_COUNT} active worktrees) for a /codex:review invocation.

Bare /codex:review uses the Bash tool's session-default cwd, which often differs from the worktree the user actually wants to review — producing an empty or wrong-target diff.

Recommended action: instead of dispatching /codex:review directly, ask the user to run /praxis:codex-review-wrap. The wrapper enumerates worktrees, prompts for explicit selection, and delegates to /codex:review with the correct cwd.

If the user explicitly confirms the target worktree in this turn, you may proceed — but state the resolved cwd in your reply so the choice is visible.
EOF
  fi
fi

# --- Advisory 2: PR-state guard (all matched triggers) --------------------------
# Warns when the current worktree's branch maps to a CLOSED or MERGED PR.
# Requires `gh` CLI; fail-open when absent, when not in a git repo, or when
# no PR exists for the current branch.

PR_STATE_MSG=""
if command -v gh >/dev/null 2>&1; then
  CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
  if [ -n "$CURRENT_BRANCH" ] && [ "$CURRENT_BRANCH" != "HEAD" ]; then
    PR_STATE=$(gh pr view "$CURRENT_BRANCH" --json state --jq '.state' 2>/dev/null)
    if [ "$PR_STATE" = "CLOSED" ] || [ "$PR_STATE" = "MERGED" ]; then
      PR_STATE_MSG="⚠ Current worktree's branch (${CURRENT_BRANCH}) maps to a PR which is ${PR_STATE}. Review is unlikely to be actionable — fix commits pushed after review will target a ${PR_STATE} branch, which may create an orphan branch or reopen a closed PR unintentionally."
    fi
  fi
fi

# --- Combine and emit ------------------------------------------------------------
if [ -z "$WORKTREE_MSG" ] && [ -z "$PR_STATE_MSG" ]; then
  exit 0
fi

COMBINED_MSG="$WORKTREE_MSG"
if [ -n "$PR_STATE_MSG" ]; then
  if [ -n "$COMBINED_MSG" ]; then
    COMBINED_MSG="${COMBINED_MSG}

${PR_STATE_MSG}"
  else
    COMBINED_MSG="$PR_STATE_MSG"
  fi
fi

# shellcheck disable=SC2034  # read by the EXIT trap installed in sourced record_fire.sh
PRAXIS_FIRE_DECISION=advise
jq -n --arg ctx "$COMBINED_MSG" \
  '{hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: $ctx}}'

exit 0
