#!/bin/bash
# test-strike-counter.sh — unit + integration tests for strike-counter.sh
#
# Each test runs against an isolated CLAUDE_PLUGIN_DATA dir so concurrent
# runs do not collide and there is no leakage into the real user state.
#
# Usage: bash hooks/test-strike-counter.sh
# Exit:  0 = all pass; 1 = at least one fail (per-test output shown)

set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STRIKE="$SCRIPT_DIR/strike-counter.sh"

PASS=0
FAIL=0
FAILED_TESTS=()

run() {
  local name="$1"; shift
  if "$@"; then
    PASS=$((PASS + 1))
    printf '  ✓ %s\n' "$name"
  else
    FAIL=$((FAIL + 1))
    FAILED_TESTS+=("$name")
    printf '  ✗ %s\n' "$name"
  fi
}

# Isolated sandbox per test invocation
fresh_env() {
  local dir
  dir=$(mktemp -d)
  export CLAUDE_PLUGIN_DATA="$dir"
  export CLAUDE_SESSION_ID="test-$$-${RANDOM}"
}

cleanup_env() {
  rm -rf "${CLAUDE_PLUGIN_DATA:-/tmp/nonexistent-$$}"
  unset CLAUDE_PLUGIN_DATA CLAUDE_SESSION_ID
}

# ---- AC1: /strike prints 1진 on first call ---------------------------------
test_ac1_first_strike_warning() {
  fresh_env
  local out
  out=$("$STRIKE" strike "worktree bypass" 2>&1)
  local code=$?
  cleanup_env
  [ "$code" -eq 0 ] && echo "$out" | grep -q "1진 경고"
}

# ---- AC2: second strike triggers 2진 회고 ----------------------------------
test_ac2_second_strike_review() {
  fresh_env
  "$STRIKE" strike "one" >/dev/null
  local out code
  out=$("$STRIKE" strike "two" 2>&1); code=$?
  cleanup_env
  [ "$code" -eq 0 ] && echo "$out" | grep -q "2진 회고" && echo "$out" | grep -q "CLAUDE.md"
}

# ---- AC3: third strike marks 3진 block state -------------------------------
test_ac3_third_strike_block_state() {
  fresh_env
  "$STRIKE" strike "one" >/dev/null
  "$STRIKE" strike "two" >/dev/null
  local out code
  out=$("$STRIKE" strike "three" 2>&1); code=$?
  local status_out
  status_out=$("$STRIKE" status 2>&1)
  cleanup_env
  [ "$code" -eq 0 ] \
    && echo "$out" | grep -q "3진 block" \
    && echo "$status_out" | grep -q "Strikes: 3/3" \
    && echo "$status_out" | grep -q "one" \
    && echo "$status_out" | grep -q "two" \
    && echo "$status_out" | grep -q "three"
}

# ---- AC4: stop hook emits {"decision":"block"} when count>=3 ---------------
test_ac4_stop_hook_blocks_at_3() {
  fresh_env
  "$STRIKE" strike "one" >/dev/null
  "$STRIKE" strike "two" >/dev/null
  "$STRIKE" strike "three" >/dev/null
  local json_in
  json_in=$(printf '{"session_id":"%s","stop_hook_active":false}' "$CLAUDE_SESSION_ID")
  local out
  out=$(echo "$json_in" | "$STRIKE" stop 2>&1)
  local code=$?
  local decision
  decision=$(echo "$out" | jq -r '.decision // empty' 2>/dev/null)
  local block_log
  block_log=$(cat "$CLAUDE_PLUGIN_DATA/strikes/last-block.log" 2>/dev/null)
  cleanup_env
  [ "$code" -eq 0 ] \
    && [ "$decision" = "block" ] \
    && echo "$block_log" | grep -q "block"
}

# ---- AC5: stop hook silent when count<3 ------------------------------------
test_ac5_stop_hook_silent_under_3() {
  fresh_env
  "$STRIKE" strike "one" >/dev/null
  local json_in out code
  json_in=$(printf '{"session_id":"%s","stop_hook_active":false}' "$CLAUDE_SESSION_ID")
  out=$(echo "$json_in" | "$STRIKE" stop 2>&1); code=$?
  cleanup_env
  [ "$code" -eq 0 ] && [ -z "$out" ]
}

# ---- AC6: stop_hook_active=true short-circuits -----------------------------
test_ac6_stop_active_short_circuit() {
  fresh_env
  "$STRIKE" strike "one" >/dev/null
  "$STRIKE" strike "two" >/dev/null
  "$STRIKE" strike "three" >/dev/null
  local json_in out code
  json_in=$(printf '{"session_id":"%s","stop_hook_active":true}' "$CLAUDE_SESSION_ID")
  out=$(echo "$json_in" | "$STRIKE" stop 2>&1); code=$?
  cleanup_env
  [ "$code" -eq 0 ] && [ -z "$out" ]
}

# ---- AC7: reset clears state -----------------------------------------------
test_ac7_reset_clears() {
  fresh_env
  "$STRIKE" strike "one" >/dev/null
  "$STRIKE" reset >/dev/null
  local status_out
  status_out=$("$STRIKE" status 2>&1)
  cleanup_env
  echo "$status_out" | grep -q "Strikes: 0/3"
}

# ---- AC8: preprompt emits 1/2/3 reminder context ---------------------------
test_ac8_preprompt_contexts() {
  fresh_env
  local json_in out_1 ctx_1 out_2 ctx_2
  json_in=$(printf '{"session_id":"%s"}' "$CLAUDE_SESSION_ID")

  "$STRIKE" strike "alpha" >/dev/null
  out_1=$(echo "$json_in" | "$STRIKE" preprompt 2>&1)
  ctx_1=$(echo "$out_1" | jq -r '.hookSpecificOutput.additionalContext // empty' 2>/dev/null)

  "$STRIKE" strike "beta" >/dev/null
  out_2=$(echo "$json_in" | "$STRIKE" preprompt 2>&1)
  ctx_2=$(echo "$out_2" | jq -r '.hookSpecificOutput.additionalContext // empty' 2>/dev/null)

  cleanup_env
  echo "$ctx_1" | grep -q "strike 1/3" \
    && echo "$ctx_2" | grep -q "strike 2/3" \
    && echo "$ctx_2" | grep -q "CLAUDE.md"
}

# ---- AC9: session-start writes latch + emits context when count>0 ----------
test_ac9_session_start_latch() {
  fresh_env
  local sid="$CLAUDE_SESSION_ID"
  local json_in
  json_in=$(printf '{"session_id":"%s"}' "$sid")

  # First call — no prior strikes, nothing on stdout
  local out code
  out=$(echo "$json_in" | "$STRIKE" session-start 2>&1); code=$?
  local latch_has_sid
  latch_has_sid=$(cat "$CLAUDE_PLUGIN_DATA/strikes/.current-session" 2>/dev/null)

  # Add a strike then rerun session-start — should emit context
  "$STRIKE" strike "existing" >/dev/null
  local out2
  out2=$(echo "$json_in" | "$STRIKE" session-start 2>&1)
  local ctx
  ctx=$(echo "$out2" | jq -r '.hookSpecificOutput.additionalContext // empty' 2>/dev/null)

  cleanup_env
  [ "$code" -eq 0 ] \
    && [ "$latch_has_sid" = "$sid" ] \
    && echo "$ctx" | grep -q "1/3"
}

# ---- AC10: slash command uses latch when env var is unset ------------------
test_ac10_latch_fallback() {
  fresh_env
  # Seed via hook (stdin JSON) — strike happens inside resolved session
  local sid="$CLAUDE_SESSION_ID"
  local json_in
  json_in=$(printf '{"session_id":"%s"}' "$sid")
  echo "$json_in" | "$STRIKE" session-start >/dev/null

  # Unset env var — rely on latch
  unset CLAUDE_SESSION_ID
  local out code
  out=$("$STRIKE" strike "via latch" 2>&1); code=$?
  cleanup_env
  [ "$code" -eq 0 ] && echo "$out" | grep -q "1진 경고"
}

# ---- AC11: missing session_id fails cleanly --------------------------------
test_ac11_missing_session_id() {
  local dir
  dir=$(mktemp -d)
  CLAUDE_PLUGIN_DATA="$dir" env -u CLAUDE_SESSION_ID "$STRIKE" strike "x" >/tmp/out.$$ 2>/tmp/err.$$
  local code=$?
  local out err
  out=$(cat /tmp/out.$$)
  err=$(cat /tmp/err.$$)
  rm -f /tmp/out.$$ /tmp/err.$$
  rm -rf "$dir"
  # script should exit 0 (fail-safe) and announce missing session_id on stderr
  [ "$code" -eq 0 ] && [ -z "$out" ] && echo "$err" | grep -q "session_id unavailable"
}

# ---- AC12: status prints Strikes: N/3 header -------------------------------
test_ac12_status_header() {
  fresh_env
  local out
  out=$("$STRIKE" status 2>&1)
  cleanup_env
  echo "$out" | grep -q "Strikes: 0/3"
}

# ---- AC13: hooks.json is valid JSON ----------------------------------------
test_ac13_hooks_json_valid() {
  jq . "$SCRIPT_DIR/hooks.json" >/dev/null 2>&1
}

# ---- AC14: session-start exports session_id via $CLAUDE_ENV_FILE -----------
test_ac14_env_file_export() {
  fresh_env
  local envfile
  envfile=$(mktemp)
  local json_in
  json_in=$(printf '{"session_id":"%s"}' "$CLAUDE_SESSION_ID")
  local expected_sid="$CLAUDE_SESSION_ID"

  # Simulate Claude Code's SessionStart hook environment
  CLAUDE_ENV_FILE="$envfile" bash -c \
    "echo '$json_in' | \"$STRIKE\" session-start >/dev/null"

  local ok=1
  grep -q "export CLAUDE_SESSION_ID=" "$envfile" || ok=0
  grep -q "$expected_sid" "$envfile" || ok=0
  rm -f "$envfile"
  cleanup_env
  [ "$ok" -eq 1 ]
}

# ---------- runner ----------------------------------------------------------
echo "strike-counter.sh tests"
echo "------------------------"
run "AC1  first strike → 1진 warning" test_ac1_first_strike_warning
run "AC2  second strike → 2진 review + CLAUDE.md" test_ac2_second_strike_review
run "AC3  third strike → 3진 block state + status" test_ac3_third_strike_block_state
run "AC4  stop hook blocks at count≥3 (JSON decision + log)" test_ac4_stop_hook_blocks_at_3
run "AC5  stop hook silent at count<3" test_ac5_stop_hook_silent_under_3
run "AC6  stop_hook_active=true short-circuits" test_ac6_stop_active_short_circuit
run "AC7  reset clears state" test_ac7_reset_clears
run "AC8  preprompt emits 1/2진 additionalContext" test_ac8_preprompt_contexts
run "AC9  session-start writes latch + emits context" test_ac9_session_start_latch
run "AC10 slash command uses latch when env var unset" test_ac10_latch_fallback
run "AC11 missing session_id → silent fail-safe exit 0" test_ac11_missing_session_id
run "AC12 status prints Strikes: N/3" test_ac12_status_header
run "AC13 hooks.json is valid JSON" test_ac13_hooks_json_valid
run "AC14 session-start exports CLAUDE_SESSION_ID via \$CLAUDE_ENV_FILE" test_ac14_env_file_export

echo "------------------------"
echo "Passed: $PASS  Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed tests:"
  for t in "${FAILED_TESTS[@]}"; do
    echo "  - $t"
  done
  exit 1
fi
exit 0
