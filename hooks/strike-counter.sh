#!/bin/bash
# strike-counter.sh — session-scoped three-strike discipline
#
# Subcommand dispatcher. Single script covers both hook and slash-command
# invocations. Hook modes read session_id from stdin JSON; slash-command
# modes read from $CLAUDE_SESSION_ID env var or a latch file written by
# the SessionStart hook.
#
# Modes:
#   session-start  Hook: record session_id, emit current count as context
#   preprompt      Hook: emit 1/2-strike reminder as additional context
#   stop           Hook: if count>=3, emit {"decision":"block"} JSON
#   strike <rsn>   Slash: increment count, echo level-specific message
#   status         Slash: echo current count + reason list
#   reset          Slash: clear state for current session; at count>=3,
#                  requires a non-empty reflection file at
#                  $STATE_DIR/${SID}.reflection.md before clearing.

# Fail-safe posture: we never want this script to break a Claude Code session.
# Instead of relying on `trap ... ERR` (which is a no-op under `set +e`), we
# keep `errexit` off and guard every external call with `|| true` /
# `2>/dev/null` / conditional checks. The script only returns a non-zero exit
# intentionally — and never from the hook modes (which stay at exit 0 always,
# using JSON `decision` to signal block).
set +e

MODE="${1:-}"
shift || true

# jq guard — Claude Code session must keep booting even without jq
if ! command -v jq >/dev/null 2>&1; then
  msg="jq required — install with: brew install jq"
  echo "$msg"
  echo "$msg" >&2
  exit 0
fi

# [#126] Use a praxis-owned path. $CLAUDE_PLUGIN_DATA is set by Claude Code
# to whichever plugin's data dir matched first in the current scope, so on
# multi-plugin installs (codex/omc/laplace-dev-hub) it can resolve to a
# sibling plugin's directory and our state would leak there — silently
# vulnerable to that plugin's cleanup/uninstall.
STATE_DIR="${PRAXIS_STATE_DIR:-$HOME/.claude/state/praxis}/strikes"
mkdir -p "$STATE_DIR" 2>/dev/null || exit 0
LATCH="$STATE_DIR/.current-session"
BLOCK_LOG="$STATE_DIR/last-block.log"

# TTL cleanup — drop sessions older than 7 days (best-effort).
# Covers both state JSON and any orphan reflection markdown so stale
# reflections from abandoned cycles cannot linger indefinitely.
find "$STATE_DIR" -maxdepth 1 \( -name '*.json' -o -name '*.reflection.md' \) -mtime +7 -delete 2>/dev/null || true

# ---- session_id resolution -------------------------------------------------
# Hook modes consume stdin once (into $INPUT) so downstream code can reuse it.
INPUT=""
SID=""

resolve_from_stdin() {
  INPUT=$(cat)
  SID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
}

resolve_from_env() {
  SID="${CLAUDE_SESSION_ID:-}"
  if [ -z "$SID" ] && [ -f "$LATCH" ]; then
    SID=$(cat "$LATCH" 2>/dev/null)
  fi
}

case "$MODE" in
  session-start|preprompt|stop)
    resolve_from_stdin
    ;;
  strike|status|reset)
    resolve_from_env
    ;;
  "")
    echo "usage: strike-counter.sh {session-start|preprompt|stop|strike|status|reset} [args]" >&2
    exit 0
    ;;
  *)
    echo "unknown mode: $MODE" >&2
    exit 0
    ;;
esac

if [ -z "$SID" ]; then
  echo "strike-counter: session_id unavailable — skipping" >&2
  exit 0
fi

STATE_FILE="$STATE_DIR/${SID}.json"
REFLECTION_FILE="$STATE_DIR/${SID}.reflection.md"

# ---- helpers ---------------------------------------------------------------
load_count() {
  # 2>/dev/null suppresses jq's own error line so a corrupt state file
  # does not turn into "0\n<error>" — the `[` test downstream only tolerates
  # a single integer.
  if [ -f "$STATE_FILE" ]; then
    jq -r '.count // 0' "$STATE_FILE" 2>/dev/null || echo 0
  else
    echo 0
  fi
}

load_reasons_plain() {
  if [ -f "$STATE_FILE" ]; then
    jq -r '.reasons // [] | to_entries | map("  \(.key + 1). \(.value)") | join("\n")' "$STATE_FILE" 2>/dev/null
  fi
}

# [PR #105] Reflection gating helpers. A reflection is valid only if the
# file exists AND is non-empty — empty markers would let the user bypass
# the gate by just `touch`ing the path. `[ -s file ]` covers both
# conditions atomically. Gate applies only at count>=3 in `reset` mode.
reflection_valid() {
  [ -s "$REFLECTION_FILE" ]
}

reflection_requirement_msg() {
  cat <<MSG
Recovery is a two-step trust process — write, then persuade.

Step 1 — Write a reflection document at the path below.
Path: $REFLECTION_FILE

Reflection must cover:
1. Summary of the three recorded violations this session
2. Root cause of each violation (which CLAUDE.md rule/section was breached)
3. Concrete behavioral changes to prevent recurrence (checklist form)

Step 2 — Present the reflection to the user and make an explicit appeal:
- Quote or summarize the reflection in-chat (do not just point at the file)
- Acknowledge the specific harm each violation caused
- Commit to the preventive checklist in concrete terms
- Explicitly ask the user to run /praxis:reset-strikes as a trust decision

/praxis:reset-strikes will be refused if the reflection file is missing or empty, and should only be invoked by the user after your appeal — never implicitly.
MSG
}

# ---- mode handlers ---------------------------------------------------------
case "$MODE" in

  session-start)
    # Primary: export CLAUDE_SESSION_ID via $CLAUDE_ENV_FILE. Claude Code
    # sources this file into every subsequent Bash tool invocation, so
    # slash commands receive the authoritative session_id directly from
    # the environment. This avoids cross-session latch contamination when
    # multiple Claude sessions share the same $STATE_DIR.
    if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
      printf 'export CLAUDE_SESSION_ID=%q\n' "$SID" >> "$CLAUDE_ENV_FILE" 2>/dev/null || true
    fi
    # Backstop: latch file for environments where $CLAUDE_ENV_FILE is
    # unavailable. Only consulted when the env var is truly absent, so
    # concurrent session overwrites are harmless under normal operation.
    echo "$SID" > "$LATCH"
    COUNT=$(load_count)
    if [ "$COUNT" -gt 0 ] 2>/dev/null; then
      # Emit as JSON additionalContext so Claude sees current strike state
      jq -n --arg ctx "Praxis strikes carried from prior activity this session: $COUNT/3" \
        '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}'
    fi
    exit 0
    ;;

  preprompt)
    COUNT=$(load_count)
    case "$COUNT" in
      0)
        exit 0
        ;;
      1)
        REASONS=$(load_reasons_plain)
        MSG="⚠️ Praxis strike 1/3 — warning. Recorded violation:
$REASONS
Stay extra careful with the rules this session."
        jq -n --arg ctx "$MSG" \
          '{hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: $ctx}}'
        ;;
      2)
        REASONS=$(load_reasons_plain)
        MSG="🔶 Praxis strike 2/3 — review required. Cumulative violations:
$REASONS
Before your next action, re-read the relevant sections of ~/.claude/CLAUDE.md and explicitly state how you will avoid another violation. One more strike triggers a hard block."
        jq -n --arg ctx "$MSG" \
          '{hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: $ctx}}'
        ;;
      *)
        # 3+ — the Stop hook will block; preprompt stays quiet
        exit 0
        ;;
    esac
    exit 0
    ;;

  stop)
    # Honor infinite-loop guard: if a previous Stop hook already blocked
    # and Claude is retrying, let it through.
    STOP_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null)
    if [ "$STOP_ACTIVE" = "true" ]; then
      exit 0
    fi
    COUNT=$(load_count)
    if [ "$COUNT" -ge 3 ] 2>/dev/null; then
      REASONS=$(load_reasons_plain)
      ts=$(date -u +%FT%TZ)
      echo "[$ts] session=$SID count=$COUNT block" >> "$BLOCK_LOG" 2>/dev/null || true
      REQUIREMENT=$(reflection_requirement_msg)
      REASON_MSG="🔴 Praxis strike 3/3 — response blocked. Violations this session:
$REASONS

$REQUIREMENT"
      jq -n --arg r "$REASON_MSG" '{decision: "block", reason: $r}'
    fi
    exit 0
    ;;

  strike)
    REASON="${*:-unspecified}"
    if [ ! -f "$STATE_FILE" ]; then
      echo '{"count":0,"reasons":[]}' > "$STATE_FILE"
    fi
    # Keep the temp file on the same filesystem as STATE_FILE so `mv`
    # always resolves to rename(2) (atomic) rather than copy+delete.
    tmp=$(mktemp "$STATE_DIR/.tmp.XXXXXX")
    jq --arg r "$REASON" '.count = (.count + 1) | .reasons = (.reasons + [$r])' \
      "$STATE_FILE" > "$tmp" && mv "$tmp" "$STATE_FILE"
    COUNT=$(load_count)
    # Distinguish expected counts (1/2/>=3) from unexpected values (0,
    # non-numeric, empty). COUNT=0 after a strike usually means the write
    # failed silently (jq parse error on corrupt state, mv failure, etc.);
    # announcing "strike 3 — blocked" in that case is a lie since the Stop
    # hook only blocks when count>=3. Route unexpected values through a
    # distinct error branch so UX matches actual enforcement state.
    if ! [[ "$COUNT" =~ ^[0-9]+$ ]] || [ "$COUNT" -eq 0 ] 2>/dev/null; then
      echo "⚠️ Strike state error: count='$COUNT' — state file corrupt or write failed." >&2
      echo "Recovery: run /praxis:reset-strikes and retry." >&2
    elif [ "$COUNT" -eq 1 ] 2>/dev/null; then
      echo "⚠️ Strike 1 warning: $REASON (tighten rule adherence from next response)"
    elif [ "$COUNT" -eq 2 ] 2>/dev/null; then
      echo "🔶 Strike 2 — review required: $REASON. Cumulative list:"
      load_reasons_plain
      echo "Re-read the relevant CLAUDE.md section before replying."
    else
      # COUNT >= 3
      echo "🔴 Strike 3 — blocked: $REASON. Cumulative list:"
      load_reasons_plain
      echo "The next response will be blocked by the Stop hook."
      echo ""
      reflection_requirement_msg
    fi
    exit 0
    ;;

  status)
    COUNT=$(load_count)
    echo "Strikes: $COUNT/3"
    if [ "$COUNT" -gt 0 ] 2>/dev/null; then
      echo "Reasons:"
      load_reasons_plain
    fi
    exit 0
    ;;

  reset)
    if [ ! -f "$STATE_FILE" ]; then
      echo "No active strikes to reset."
      exit 0
    fi
    # Reflection gate applies only at count>=3 (the block threshold).
    # At lower counts the user may want to clear accidental or exploratory
    # strikes without ceremony, so we keep reset free in that range.
    COUNT=$(load_count)
    if [ "$COUNT" -ge 3 ] 2>/dev/null; then
      if ! reflection_valid; then
        echo "❌ Reset refused — reflection missing or empty."
        echo ""
        reflection_requirement_msg
        exit 0
      fi
      # On successful gated reset, remove the reflection too so the next
      # next strike-3 cycle starts from a clean slate and cannot reuse a stale doc.
      rm -f "$REFLECTION_FILE"
    fi
    rm -f "$STATE_FILE"
    echo "Strikes reset (session=$SID)."
    exit 0
    ;;

esac
