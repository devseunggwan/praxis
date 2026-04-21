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
#   reset          Slash: clear state for current session

set +e
trap 'exit 0' ERR

MODE="${1:-}"
shift 2>/dev/null || true

# jq guard — Claude Code session must keep booting even without jq
if ! command -v jq >/dev/null 2>&1; then
  msg="jq required — install with: brew install jq"
  echo "$msg"
  echo "$msg" >&2
  exit 0
fi

STATE_DIR="${CLAUDE_PLUGIN_DATA:-$HOME/.claude/state/praxis}/strikes"
mkdir -p "$STATE_DIR" 2>/dev/null || exit 0
LATCH="$STATE_DIR/.current-session"
BLOCK_LOG="$STATE_DIR/last-block.log"

# TTL cleanup — drop sessions older than 7 days (best-effort)
find "$STATE_DIR" -maxdepth 1 -name '*.json' -mtime +7 -delete 2>/dev/null || true

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

# ---- helpers ---------------------------------------------------------------
load_count() {
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

# ---- mode handlers ---------------------------------------------------------
case "$MODE" in

  session-start)
    # Write latch file so slash commands can resolve session_id
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
      REASON_MSG="🔴 Praxis strike 3/3 — response blocked. Violations this session:
$REASONS
Ask the user to run /praxis:reset-strikes and acknowledge the retrospective before continuing."
      jq -n --arg r "$REASON_MSG" '{decision: "block", reason: $r}'
    fi
    exit 0
    ;;

  strike)
    REASON="${*:-unspecified}"
    if [ ! -f "$STATE_FILE" ]; then
      echo '{"count":0,"reasons":[]}' > "$STATE_FILE"
    fi
    tmp=$(mktemp)
    jq --arg r "$REASON" '.count = (.count + 1) | .reasons = (.reasons + [$r])' \
      "$STATE_FILE" > "$tmp" && mv "$tmp" "$STATE_FILE"
    COUNT=$(load_count)
    case "$COUNT" in
      1) echo "⚠️ 1진 경고: $REASON (다음 응답부터 주의 강화)" ;;
      2) echo "🔶 2진 회고 필요: $REASON. 누적 목록:"; load_reasons_plain; echo "관련 CLAUDE.md 섹션 재독 후 응답." ;;
      *) echo "🔴 3진 block 상태: $REASON. 누적 목록:"; load_reasons_plain; echo "다음 응답은 Stop hook으로 차단됩니다. /praxis:reset-strikes 로 해제." ;;
    esac
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
    if [ -f "$STATE_FILE" ]; then
      rm -f "$STATE_FILE"
      echo "Strikes reset (session=$SID)."
    else
      echo "No active strikes to reset."
    fi
    exit 0
    ;;

esac
