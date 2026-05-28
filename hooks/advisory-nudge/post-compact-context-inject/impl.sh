#!/bin/bash
# post-compact-context-inject.sh — UserPromptSubmit hook
#
# Fires on the FIRST user prompt after a Claude Code `/compact` operation
# and emits `additionalContext` with session-preservation info that would
# otherwise be lost across the compaction boundary:
#   - active session_id (from payload)
#   - worktree absolute path (cwd from payload)
#   - current strike count + violation reasons (praxis strike state)
#   - active branch + open PR # / URL (read-only `gh pr list`)
#
# Detection mechanism (issue #472):
#   Claude Code's `PreCompact` event does NOT support
#   `hookSpecificOutput`/`additionalContext`/`customInstructions` (verified
#   in #466 via probe), so the surviving-context handoff is done one step
#   later on `UserPromptSubmit` after the next user message lands. We tail-
#   read the last ~100 lines of the transcript JSONL pointed at by
#   `transcript_path`, locate the most recent record with
#   `isCompactSummary == true`, and fire if and only if NO subsequent user
#   message has already consumed the post-compact handoff. A session-scoped
#   marker file dedupes so subsequent prompts don't re-fire.
#
# Fail-safe posture: jq missing, malformed payload, missing transcript,
# unreadable strike state, `gh` absent, or ANY unexpected error → exit 0
# silently. The hook NEVER blocks a prompt — that would be catastrophic
# (every UserPromptSubmit, every session).

set +e

# jq guard — Claude Code session must keep functioning even without jq
if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

INPUT=$(cat)
SID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null)

# Payload-format guard — any required field missing → fail-open silent.
if [ -z "$SID" ] || [ -z "$TRANSCRIPT" ] || [ ! -r "$TRANSCRIPT" ]; then
  exit 0
fi

# --- dedup marker (session-scoped) ------------------------------------------
# Once the hook fires for a given session it must NEVER re-fire for the
# same session — otherwise every prompt for the rest of the session
# would re-inject identical context. We use a praxis-owned state dir
# (same convention as strike-counter) so the marker is collocated with
# related session state and the TTL/cleanup pattern matches.
STATE_DIR="${PRAXIS_STATE_DIR:-$HOME/.claude/state/praxis}/post-compact"
mkdir -p "$STATE_DIR" 2>/dev/null || exit 0
MARKER="$STATE_DIR/${SID}.injected"

# TTL cleanup — drop markers older than 7 days (best-effort).
find "$STATE_DIR" -maxdepth 1 -name '*.injected' -mtime +7 -delete 2>/dev/null || true

if [ -e "$MARKER" ]; then
  exit 0
fi

# --- transcript tail-scan ---------------------------------------------------
# Read the last 100 lines and look for the most recent isCompactSummary
# record. The JSONL format Claude Code writes carries fields:
#   { "type": "system"|"user"|"assistant", "isCompactSummary": true, ... }
#   { "type": "user", "message": {...}, "timestamp": "..." }
# We treat presence of any line with isCompactSummary==true within the
# tail as a signal that a compaction happened. The dedup marker ensures
# we only fire ONCE per session, so even if multiple compactions land in
# the tail window we won't re-fire after the first injection.
#
# Implementation note: a missing/empty transcript yields zero matches and
# we exit silently — the typical first-prompt-of-session path.
HAS_COMPACT=$(tail -n 100 "$TRANSCRIPT" 2>/dev/null \
  | jq -rs 'try (map(select(.isCompactSummary == true)) | length) catch 0' 2>/dev/null)

if [ -z "$HAS_COMPACT" ] || [ "$HAS_COMPACT" = "0" ] || [ "$HAS_COMPACT" = "null" ]; then
  exit 0
fi

# --- context collection (all read-only, all fail-open) -----------------------
# Each lookup is wrapped so a single failure cannot abort the whole hook.

# Strike state — same path scheme as hooks/completion-verify/strike-counter/impl.sh.
STRIKE_DIR="${PRAXIS_STATE_DIR:-$HOME/.claude/state/praxis}/strikes"
STRIKE_FILE="$STRIKE_DIR/${SID}.json"
STRIKE_COUNT=0
STRIKE_REASONS=""
if [ -r "$STRIKE_FILE" ]; then
  STRIKE_COUNT=$(jq -r '.count // 0' "$STRIKE_FILE" 2>/dev/null || echo 0)
  STRIKE_REASONS=$(jq -r '.reasons // [] | to_entries | map("  \(.key + 1). \(.value)") | join("\n")' "$STRIKE_FILE" 2>/dev/null)
fi

# Branch + PR — read-only gh call. Hook MUST NOT mutate anything.
BRANCH=""
PR_LINE=""
if [ -n "$CWD" ] && [ -d "$CWD" ]; then
  BRANCH=$(git -C "$CWD" branch --show-current 2>/dev/null)
  if [ -n "$BRANCH" ] && command -v gh >/dev/null 2>&1; then
    # --limit 1 keeps the call cheap; --json restricts to needed fields.
    # gh has no `-C`; we cd via subshell so the call is scoped and side-effect-free.
    PR_JSON=$(cd "$CWD" 2>/dev/null && gh pr list --state open --head "$BRANCH" --json number,url --limit 1 2>/dev/null)
    if [ -n "$PR_JSON" ]; then
      PR_NUM=$(echo "$PR_JSON" | jq -r '.[0].number // empty' 2>/dev/null)
      PR_URL=$(echo "$PR_JSON" | jq -r '.[0].url // empty' 2>/dev/null)
      if [ -n "$PR_NUM" ] && [ -n "$PR_URL" ]; then
        PR_LINE="- Active PR: #${PR_NUM} (${PR_URL})"
      fi
    fi
  fi
fi

# --- compose context message ------------------------------------------------
# Keep the block compact — Claude reads it alongside the new prompt and
# overlong context dilutes the signal.
{
  echo "🧷 Post-compact session context (auto-injected once after /compact):"
  echo ""
  echo "- Session ID: ${SID}"
  if [ -n "$CWD" ]; then
    echo "- Worktree: ${CWD}"
  fi
  if [ -n "$BRANCH" ]; then
    echo "- Branch: ${BRANCH}"
  fi
  if [ -n "$PR_LINE" ]; then
    echo "$PR_LINE"
  fi
  if [ "$STRIKE_COUNT" != "0" ] 2>/dev/null && [ -n "$STRIKE_COUNT" ]; then
    echo "- Praxis strikes: ${STRIKE_COUNT}/3"
    if [ -n "$STRIKE_REASONS" ]; then
      echo "  Reasons:"
      echo "$STRIKE_REASONS"
    fi
  else
    echo "- Praxis strikes: 0/3"
  fi
  echo ""
  echo "(This block fires once per session — the FIRST prompt after a /compact. "
  echo "Use the IDs above to re-anchor work that spans the compaction boundary; "
  echo "re-read CLAUDE.md / the active issue if needed.)"
} > /tmp/post-compact-ctx.$$ 2>/dev/null

CONTEXT_TEXT=""
if [ -r /tmp/post-compact-ctx.$$ ]; then
  CONTEXT_TEXT=$(cat /tmp/post-compact-ctx.$$ 2>/dev/null)
  rm -f /tmp/post-compact-ctx.$$ 2>/dev/null
fi

if [ -z "$CONTEXT_TEXT" ]; then
  exit 0
fi

# --- emit + latch -----------------------------------------------------------
# Touch the marker FIRST so a downstream jq failure cannot cause a re-fire.
# The marker is itself best-effort (mkdir failure already caused exit above).
: > "$MARKER" 2>/dev/null || true

jq -n --arg ctx "$CONTEXT_TEXT" \
  '{hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: $ctx}}' 2>/dev/null

exit 0
