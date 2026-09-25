#!/bin/sh
# UserPromptSubmit / PostToolUse / PostToolUseFailure hook: elapsed-time signal
# for delegated workers (issue #1501).
#
# The Opus 5.5 prompting guide ("Time signals for multiagent harnesses") has
# the harness append a short line to each message it sends back to the model —
# `elapsed 340s / 1200s` against a budget, or the elapsed time alone when no
# sensible budget exists. `cmux-delegate --time-budget <seconds>` opts a
# worker in by exporting two variables on the `claude` launch; hook processes
# inherit the claude process environment, so this hook reads them directly:
#
#   PRAXIS_TIME_START_EPOCH  launch time, epoch seconds (the on/off switch)
#   PRAXIS_TIME_BUDGET_S     budget in seconds; unset or 0 = elapsed-only
#
# Advisory only. Nothing here stops the worker at the limit — a hard timeout
# belongs to the orchestrator. Exit code is always 0.
#
# Written in POSIX sh, not Python, on purpose: the registration covers every
# tool call of every session, and every session that was not launched with a
# budget must pay nothing. The first test below exits before stdin is read and
# before any process is spawned.
#
# argv[1] is the event name the manifest registration passes (`args`), echoed
# as `hookEventName`, so no JSON parse is needed to answer the right event.

[ -n "${PRAXIS_TIME_START_EPOCH:-}" ] || exit 0

EVENT="${1:-PostToolUse}"
case "$EVENT" in
  UserPromptSubmit|PostToolUse|PostToolUseFailure) ;;
  *) exit 0 ;;
esac

# A decimal integer with no leading zero (sh arithmetic reads `010` as octal)
# and at most 12 digits (far inside 64-bit arithmetic). Anything else is
# malformed, and malformed is silent: a wrong number is worse than none.
_is_uint() {
  case "$1" in
    ''|*[!0-9]*) return 1 ;;
    0) return 0 ;;
    0*) return 1 ;;
  esac
  [ "${#1}" -le 12 ]
}

START="$PRAXIS_TIME_START_EPOCH"
BUDGET="${PRAXIS_TIME_BUDGET_S:-0}"
_is_uint "$START" || exit 0
_is_uint "$BUDGET" || exit 0

NOW=$(date +%s 2>/dev/null)
_is_uint "$NOW" || exit 0
# A start in the future is not clock skew between processes — the same host
# wrote it moments ago — so treat it as malformed rather than print 0.
[ "$NOW" -ge "$START" ] || exit 0
ELAPSED=$((NOW - START))

# Fire ledger (issue #848, Rule 18): armed only on the opted-in path, after
# session_id is known, so a session without the env var writes no record and
# spawns nothing. jq missing -> no session id -> no record; the signal itself
# does not depend on it.
INPUT=$(cat 2>/dev/null)
SID=""
if command -v jq >/dev/null 2>&1; then
  SID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
fi
if [ -n "$SID" ]; then
  # shellcheck source=../../_lib/record_fire.sh
  . "$(dirname "$0")/../../_lib/record_fire.sh" 2>/dev/null || true
  command -v praxis_fire_arm >/dev/null 2>&1 && \
praxis_fire_arm elapsed-time-signal advisory-nudge "$SID" ""
fi

if [ "$BUDGET" -gt 0 ]; then
  LINE="elapsed ${ELAPSED}s / ${BUDGET}s"
else
  LINE="elapsed ${ELAPSED}s"
fi

# Every interpolated value is digits or a fixed event name, so no JSON
# escaping is needed.
# shellcheck disable=SC2034  # read by the EXIT trap installed in sourced record_fire.sh
PRAXIS_FIRE_DECISION=advise
printf '{"hookSpecificOutput":{"hookEventName":"%s","additionalContext":"%s"}}\n' \
  "$EVENT" "$LINE"
exit 0
