#!/bin/bash
# Coverage for hooks/advisory-nudge/elapsed-time-signal/impl.sh (issue #1501).
#
# The clock is pinned with a `date` shim on PATH that answers `+%s` with a
# fixed epoch and hands every other invocation (the fire ledger's
# `date -u +%Y-%m-%d`) to the real binary — the hook carries no clock-override
# env var of its own.
#
# Every silent case is paired with an emitting case that differs only in the
# field under test, so silence cannot come from a dead fixture.
#
# Usage: bash tests/hooks/advisory-nudge/test_elapsed_time_signal.sh
# Exit: 0 = all pass; 1 = at least one failure

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/elapsed-time-signal/impl.sh"
WRAPPER="$ROOT_DIR/hooks/elapsed-time-signal.sh"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

WORK_DIR=$(mktemp -d) || {
  echo "FATAL: mktemp -d failed" >&2
  exit 1
}
trap 'rm -rf "$WORK_DIR"' EXIT

REAL_DATE=$(command -v date)
FAKE_NOW=1700001340
mkdir -p "$WORK_DIR/bin"
cat >"$WORK_DIR/bin/date" <<EOF
#!/bin/sh
if [ "\$*" = "+%s" ]; then
  echo "\${FAKE_NOW:-$FAKE_NOW}"
  exit 0
fi
exec "$REAL_DATE" "\$@"
EOF
chmod +x "$WORK_DIR/bin/date"

LEDGER="$WORK_DIR/ledger.jsonl"
PAYLOAD='{"session_id":"sess-1501","hook_event_name":"PostToolUse","tool_name":"Bash","tool_input":{"command":"ls"}}'

PASS=0
FAIL=0
FAILED_NAMES=()

pass() { PASS=$((PASS + 1)); echo "PASS  [$1]"; }
fail() {
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("$1")
  echo "FAIL  [$1] $2"
}

# run_hook <target> <event-arg> <env assignments...>
# Runs with a clean slate for the two PRAXIS_TIME_* vars so the developer's
# own environment cannot leak into a case; sets OUT / ERR / RC.
run_hook() {
  local target="$1" event="$2"
  shift 2
  rm -f "$LEDGER"
  OUT=$(printf '%s\n' "$PAYLOAD" | env -u PRAXIS_TIME_START_EPOCH -u PRAXIS_TIME_BUDGET_S \
    PATH="$WORK_DIR/bin:$PATH" PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" "$@" \
    "$target" "$event" 2>"$WORK_DIR/stderr")
  RC=$?
  ERR=$(<"$WORK_DIR/stderr")
}

expect_line() {
  local name="$1" want="$2" event="$3"
  local got_ctx got_event
  if [ "$RC" -ne 0 ] || [ -n "$ERR" ]; then
    fail "$name" "rc=$RC stderr=$ERR"
    return
  fi
  got_ctx=$(printf '%s' "$OUT" | jq -r '.hookSpecificOutput.additionalContext' 2>/dev/null)
  got_event=$(printf '%s' "$OUT" | jq -r '.hookSpecificOutput.hookEventName' 2>/dev/null)
  if [ "$got_ctx" = "$want" ] && [ "$got_event" = "$event" ]; then
    pass "$name"
  else
    fail "$name" "want ($event, '$want') got ($got_event, '$got_ctx') raw=$OUT"
  fi
}

expect_silent() {
  local name="$1"
  if [ "$RC" -eq 0 ] && [ -z "$OUT" ] && [ -z "$ERR" ]; then
    pass "$name"
  else
    fail "$name" "rc=$RC stdout=$OUT stderr=$ERR"
  fi
}

START=1700001000   # FAKE_NOW - 340

# --- 1. Opt-in switch: absent env is silent, present env emits -------------
run_hook "$HOOK" PostToolUse
expect_silent "env absent -> silent"
if [ ! -e "$LEDGER" ]; then
  pass "env absent -> no fire-ledger record (zero-cost path)"
else
  fail "env absent -> no fire-ledger record (zero-cost path)" "$(cat "$LEDGER")"
fi

run_hook "$HOOK" PostToolUse PRAXIS_TIME_START_EPOCH=$START PRAXIS_TIME_BUDGET_S=1200
expect_line "budget mode -> elapsed from stored start" "elapsed 340s / 1200s" PostToolUse
if [ "$(jq -r 'select(.hook == "elapsed-time-signal") | "\(.decision) \(.session_id)"' "$LEDGER" 2>/dev/null)" \
  = "advise sess-1501" ]; then
  pass "emission records an advise fire with the session id"
else
  fail "emission records an advise fire with the session id" "$(cat "$LEDGER" 2>/dev/null)"
fi

# --- 1b. The no-op path reads no stdin and spawns nothing -------------------
# The cost claim in spec.md (exit before stdin is read, before any process is
# spawned) is what every session without a budget pays on every tool call, so
# it is pinned here, not left to the prose.
#
# stdin that never closes: a hook that reads stdin before the env check blocks
# until `timeout` kills it (rc 124). Process substitution keeps the writer end
# open without the shell waiting for `sleep` to finish.
stdin_never_closes() {
  local target="$1"
  shift
  local fd holder
  exec {fd}< <(sleep 30)
  holder=$!
  env -u PRAXIS_TIME_START_EPOCH -u PRAXIS_TIME_BUDGET_S \
    PATH="$WORK_DIR/bin:$PATH" PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" "$@" \
    timeout 1 "$target" PostToolUse <&"$fd" >"$WORK_DIR/stdout" 2>"$WORK_DIR/stderr"
  RC=$?
  exec {fd}<&-
  kill "$holder" 2>/dev/null
  OUT=$(<"$WORK_DIR/stdout")
  ERR=$(<"$WORK_DIR/stderr")
}

rm -f "$LEDGER"
stdin_never_closes "$HOOK"
expect_silent "env absent + stdin never closes -> exits without reading stdin"
# Control: the opted-in path does read stdin, so the same harness must time
# out there, or the case above would pass on a harness that closes stdin.
stdin_never_closes "$HOOK" PRAXIS_TIME_START_EPOCH=$START PRAXIS_TIME_BUDGET_S=1200
if [ "$RC" -eq 124 ]; then
  pass "control: opted-in path blocks on the same never-closing stdin"
else
  fail "control: opted-in path blocks on the same never-closing stdin" "rc=$RC"
fi

# Spy shims: each external the opted-in path uses records its own name before
# handing over to the real binary. On the no-op path the spy log stays empty.
SPY_DIR="$WORK_DIR/spy"
SPY_LOG="$WORK_DIR/spy.log"
mkdir -p "$SPY_DIR"
for tool in cat jq date sleep dirname; do
  real=$(PATH="$WORK_DIR/bin:$PATH" command -v "$tool") || continue
  cat >"$SPY_DIR/$tool" <<EOF
#!/bin/sh
echo $tool >>"$SPY_LOG"
exec "$real" "\$@"
EOF
  chmod +x "$SPY_DIR/$tool"
done

rm -f "$SPY_LOG"
run_hook "$HOOK" PostToolUse PATH="$SPY_DIR:$WORK_DIR/bin:$PATH"
if [ "$RC" -eq 0 ] && [ -z "$OUT" ] && [ ! -e "$SPY_LOG" ]; then
  pass "env absent -> no external command spawned"
else
  fail "env absent -> no external command spawned" "rc=$RC spawned=$(tr '\n' ' ' <"$SPY_LOG" 2>/dev/null)"
fi
# Control: the opted-in path does reach the shims.
rm -f "$SPY_LOG"
run_hook "$HOOK" PostToolUse PATH="$SPY_DIR:$WORK_DIR/bin:$PATH" \
  PRAXIS_TIME_START_EPOCH=$START PRAXIS_TIME_BUDGET_S=1200
if grep -qx date "$SPY_LOG" 2>/dev/null && grep -qx jq "$SPY_LOG" 2>/dev/null; then
  pass "control: opted-in path spawns date and jq through the spies"
else
  fail "control: opted-in path spawns date and jq through the spies" "spawned=$(tr '\n' ' ' <"$SPY_LOG" 2>/dev/null)"
fi

# --- 2. The elapsed value follows the clock, not a constant -----------------
run_hook "$HOOK" PostToolUse PRAXIS_TIME_START_EPOCH=$START PRAXIS_TIME_BUDGET_S=1200 FAKE_NOW=1700002500
expect_line "later clock -> larger elapsed (past the budget, still advisory)" "elapsed 1500s / 1200s" PostToolUse

run_hook "$HOOK" PostToolUse PRAXIS_TIME_START_EPOCH=$START PRAXIS_TIME_BUDGET_S=1200 FAKE_NOW=$START
expect_line "start == now -> elapsed 0" "elapsed 0s / 1200s" PostToolUse

# --- 3. Elapsed-only mode ----------------------------------------------------
run_hook "$HOOK" PostToolUse PRAXIS_TIME_START_EPOCH=$START PRAXIS_TIME_BUDGET_S=0
expect_line "budget 0 -> elapsed only" "elapsed 340s" PostToolUse

run_hook "$HOOK" PostToolUse PRAXIS_TIME_START_EPOCH=$START
expect_line "budget unset -> elapsed only" "elapsed 340s" PostToolUse

# --- 4. hookEventName echoes the registration's argv -------------------------
run_hook "$HOOK" UserPromptSubmit PRAXIS_TIME_START_EPOCH=$START PRAXIS_TIME_BUDGET_S=1200
expect_line "UserPromptSubmit registration" "elapsed 340s / 1200s" UserPromptSubmit

run_hook "$HOOK" PostToolUseFailure PRAXIS_TIME_START_EPOCH=$START PRAXIS_TIME_BUDGET_S=1200
expect_line "PostToolUseFailure registration" "elapsed 340s / 1200s" PostToolUseFailure

run_hook "$HOOK" Stop PRAXIS_TIME_START_EPOCH=$START PRAXIS_TIME_BUDGET_S=1200
expect_silent "unregistered event arg -> silent"

# --- 5. Malformed env -> silent ---------------------------------------------
for bad in "abc" "-5" "17000010.5" " 1700001000" "01700001000" "1234567890123" ""; do
  run_hook "$HOOK" PostToolUse PRAXIS_TIME_START_EPOCH="$bad" PRAXIS_TIME_BUDGET_S=1200
  expect_silent "malformed start '$bad' -> silent"
done
for bad in "abc" "-1" "1200s" "012" "1e3" "1234567890123"; do
  run_hook "$HOOK" PostToolUse PRAXIS_TIME_START_EPOCH=$START PRAXIS_TIME_BUDGET_S="$bad"
  expect_silent "malformed budget '$bad' -> silent"
done

run_hook "$HOOK" PostToolUse PRAXIS_TIME_START_EPOCH=1700009999 PRAXIS_TIME_BUDGET_S=1200
expect_silent "start in the future -> silent"

# --- 6. Payload independence ------------------------------------------------
# The signal does not depend on the payload; a garbage payload still emits,
# only the ledger record (which needs session_id) is skipped.
PAYLOAD_SAVED="$PAYLOAD"
PAYLOAD='not json'
run_hook "$HOOK" PostToolUse PRAXIS_TIME_START_EPOCH=$START PRAXIS_TIME_BUDGET_S=1200
expect_line "unparseable payload -> still emits" "elapsed 340s / 1200s" PostToolUse
if [ ! -e "$LEDGER" ]; then
  pass "unparseable payload -> no ledger record without a session id"
else
  fail "unparseable payload -> no ledger record without a session id" "$(cat "$LEDGER")"
fi
PAYLOAD="$PAYLOAD_SAVED"

# --- 7. Generated wrapper forwards argv to the impl -------------------------
if [ -x "$WRAPPER" ]; then
  run_hook "$WRAPPER" UserPromptSubmit PRAXIS_TIME_START_EPOCH=$START PRAXIS_TIME_BUDGET_S=1200
  expect_line "wrapper forwards the event arg" "elapsed 340s / 1200s" UserPromptSubmit
else
  fail "wrapper forwards the event arg" "wrapper missing: $WRAPPER"
fi

echo ""
echo "== $PASS passed, $FAIL failed =="
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for name in "${FAILED_NAMES[@]}"; do
    echo "  - $name"
  done
  exit 1
fi
