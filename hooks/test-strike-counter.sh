#!/bin/bash
# test-strike-counter.sh — unit + integration tests for strike-counter.sh
#
# Each test runs against an isolated PRAXIS_STATE_DIR so concurrent
# runs do not collide and there is no leakage into the real user state.
#
# Usage: bash hooks/test-strike-counter.sh
# Exit:  0 = all pass; 1 = at least one fail (per-test output shown)

# shellcheck disable=SC2329
# All test_* and helper functions are invoked indirectly via `run "<name>" test_fn`
# at the bottom of this file. Shellcheck can't trace indirect dispatch, so its
# "never invoked" warning is a false positive for this test harness.

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
  export PRAXIS_STATE_DIR="$dir"
  export CLAUDE_SESSION_ID="test-$$-${RANDOM}"
}

cleanup_env() {
  rm -rf "${PRAXIS_STATE_DIR:-/tmp/nonexistent-$$}"
  unset PRAXIS_STATE_DIR CLAUDE_SESSION_ID
}

# ---- AC1: /strike prints strike 1 warning on first call --------------------
test_ac1_first_strike_warning() {
  fresh_env
  local out
  out=$("$STRIKE" strike "worktree bypass" 2>&1)
  local code=$?
  cleanup_env
  [ "$code" -eq 0 ] && echo "$out" | grep -q "Strike 1 warning"
}

# ---- AC2: second strike triggers strike 2 review ---------------------------
test_ac2_second_strike_review() {
  fresh_env
  "$STRIKE" strike "one" >/dev/null
  local out code
  out=$("$STRIKE" strike "two" 2>&1); code=$?
  cleanup_env
  [ "$code" -eq 0 ] \
    && echo "$out" | grep -q "Strike 2 — review required" \
    && echo "$out" | grep -q "CLAUDE.md"
}

# ---- AC3: third strike marks strike 3 blocked state ------------------------
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
    && echo "$out" | grep -q "Strike 3 — blocked" \
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
  block_log=$(cat "$PRAXIS_STATE_DIR/strikes/last-block.log" 2>/dev/null)
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
  latch_has_sid=$(cat "$PRAXIS_STATE_DIR/strikes/.current-session" 2>/dev/null)

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
  [ "$code" -eq 0 ] && echo "$out" | grep -q "Strike 1 warning"
}

# ---- AC11: missing session_id fails cleanly --------------------------------
test_ac11_missing_session_id() {
  local dir
  dir=$(mktemp -d)
  PRAXIS_STATE_DIR="$dir" env -u CLAUDE_SESSION_ID "$STRIKE" strike "x" >/tmp/out.$$ 2>/tmp/err.$$
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

# ---- AC15 (plan AC7): jq missing → stdout AND stderr guidance, exit 0 ------
test_ac15_jq_missing_guidance() {
  fresh_env
  # Empty PATH hides every external binary including jq
  local out err code
  out=$(PATH="" "$STRIKE" strike "x" 2>/tmp/err.$$)
  code=$?
  err=$(cat /tmp/err.$$)
  rm -f /tmp/err.$$
  cleanup_env
  [ "$code" -eq 0 ] \
    && echo "$out" | grep -q "jq required" \
    && echo "$err" | grep -q "jq required"
}

# ---- AC16 (plan AC9): skill files exist + reference strike-counter.sh ------
test_ac16_skill_files_exist() {
  local root
  root=$(cd "$SCRIPT_DIR/.." && pwd)
  local ok=1
  for s in strike strikes reset-strikes; do
    [ -f "$root/skills/$s/SKILL.md" ] || ok=0
    grep -q "strike-counter.sh" "$root/skills/$s/SKILL.md" 2>/dev/null || ok=0
  done
  [ "$ok" -eq 1 ]
}

# ---- AC18 (codex P2): corrupt state → error branch, not false block --------
test_ac18_corrupt_state_not_false_block() {
  fresh_env
  mkdir -p "$PRAXIS_STATE_DIR/strikes"
  # Seed a non-JSON state file so jq parse fails and COUNT stays 0
  echo "not-valid-json" > "$PRAXIS_STATE_DIR/strikes/${CLAUDE_SESSION_ID}.json"

  local out err
  out=$("$STRIKE" strike "after-corrupt" 2>/tmp/err.$$)
  local code=$?
  err=$(cat /tmp/err.$$)
  rm -f /tmp/err.$$

  # Confirm stop hook does NOT block (count<3), confirming the UX/enforcement
  # asymmetry is fixed — error message on stderr, not a false block on stdout
  local stop_out
  local json_in
  json_in=$(printf '{"session_id":"%s","stop_hook_active":false}' "$CLAUDE_SESSION_ID")
  stop_out=$(echo "$json_in" | "$STRIKE" stop 2>&1)
  cleanup_env

  [ "$code" -eq 0 ] \
    && ! echo "$out" | grep -q 'Strike 3 — blocked' \
    && echo "$err" | grep -q "Strike state error" \
    && [ -z "$stop_out" ]
}

# ---- AC19: reset at count>=3 refused when reflection missing ---------------
test_ac19_reset_blocked_without_reflection() {
  fresh_env
  "$STRIKE" strike "one" >/dev/null
  "$STRIKE" strike "two" >/dev/null
  "$STRIKE" strike "three" >/dev/null
  local out code
  out=$("$STRIKE" reset 2>&1); code=$?
  # State must still exist (reset was refused)
  local state_file="$PRAXIS_STATE_DIR/strikes/${CLAUDE_SESSION_ID}.json"
  local state_kept=0
  [ -f "$state_file" ] && state_kept=1
  cleanup_env
  [ "$code" -eq 0 ] \
    && echo "$out" | grep -q "Reset refused" \
    && echo "$out" | grep -q "reflection.md" \
    && [ "$state_kept" -eq 1 ]
}

# ---- AC20: reset at count>=3 refused when reflection file is empty ---------
test_ac20_reset_blocked_when_reflection_empty() {
  fresh_env
  "$STRIKE" strike "one" >/dev/null
  "$STRIKE" strike "two" >/dev/null
  "$STRIKE" strike "three" >/dev/null
  local reflection="$PRAXIS_STATE_DIR/strikes/${CLAUDE_SESSION_ID}.reflection.md"
  : > "$reflection"  # zero-byte file
  local out code
  out=$("$STRIKE" reset 2>&1); code=$?
  cleanup_env
  [ "$code" -eq 0 ] && echo "$out" | grep -q "Reset refused"
}

# ---- AC21: reset at count>=3 succeeds when reflection is non-empty ---------
test_ac21_reset_succeeds_with_reflection() {
  fresh_env
  "$STRIKE" strike "one" >/dev/null
  "$STRIKE" strike "two" >/dev/null
  "$STRIKE" strike "three" >/dev/null
  local reflection="$PRAXIS_STATE_DIR/strikes/${CLAUDE_SESSION_ID}.reflection.md"
  local state_file="$PRAXIS_STATE_DIR/strikes/${CLAUDE_SESSION_ID}.json"
  printf '# Reflection\n\nRoot causes and prevention checklist.\n' > "$reflection"
  local out code
  out=$("$STRIKE" reset 2>&1); code=$?
  # Both state and reflection must be removed after a successful gated reset
  local state_gone=1
  [ -f "$state_file" ] && state_gone=0
  local reflection_gone=1
  [ -f "$reflection" ] && reflection_gone=0
  cleanup_env
  [ "$code" -eq 0 ] \
    && echo "$out" | grep -q "Strikes reset" \
    && [ "$state_gone" -eq 1 ] \
    && [ "$reflection_gone" -eq 1 ]
}

# ---- AC22: reset at count<3 is NOT gated (no reflection required) ----------
test_ac22_reset_not_gated_under_3() {
  fresh_env
  "$STRIKE" strike "one" >/dev/null
  local out code
  out=$("$STRIKE" reset 2>&1); code=$?
  cleanup_env
  [ "$code" -eq 0 ] && echo "$out" | grep -q "Strikes reset"
}

# ---- AC23: stop hook block message includes reflection instructions --------
test_ac23_stop_block_has_reflection_instructions() {
  fresh_env
  "$STRIKE" strike "one" >/dev/null
  "$STRIKE" strike "two" >/dev/null
  "$STRIKE" strike "three" >/dev/null
  local json_in
  json_in=$(printf '{"session_id":"%s","stop_hook_active":false}' "$CLAUDE_SESSION_ID")
  local out
  out=$(echo "$json_in" | "$STRIKE" stop 2>&1)
  local reason
  reason=$(echo "$out" | jq -r '.reason // empty' 2>/dev/null)
  cleanup_env
  echo "$reason" | grep -q "reflection" \
    && echo "$reason" | grep -q "reflection.md" \
    && echo "$reason" | grep -q "Root cause"
}

# ---- AC24: block message includes persuasion step instructions -------------
test_ac24_block_message_has_persuasion_step() {
  fresh_env
  "$STRIKE" strike "one" >/dev/null
  "$STRIKE" strike "two" >/dev/null
  local strike_out
  strike_out=$("$STRIKE" strike "three" 2>&1)
  local json_in
  json_in=$(printf '{"session_id":"%s","stop_hook_active":false}' "$CLAUDE_SESSION_ID")
  local stop_out
  stop_out=$(echo "$json_in" | "$STRIKE" stop 2>&1)
  local stop_reason
  stop_reason=$(echo "$stop_out" | jq -r '.reason // empty' 2>/dev/null)
  cleanup_env
  # Both the strike-time and stop-hook messages must name the persuasion step
  # so Claude cannot skip the user-facing appeal after writing the reflection.
  echo "$strike_out" | grep -qi "persuade\|trust\|appeal" \
    && echo "$stop_reason" | grep -qi "persuade\|trust\|appeal"
}

# ---- AC17 (plan Step 1.5): TTL cleanup removes stale state files -----------
test_ac17_ttl_cleanup() {
  fresh_env
  # Ensure strikes/ dir exists first, then drop a stale state file plus a
  # fresh one. session-start should sweep stale but leave fresh.
  mkdir -p "$PRAXIS_STATE_DIR/strikes"
  local stale_sid="ttl-stale-$$"
  local stale_file="$PRAXIS_STATE_DIR/strikes/${stale_sid}.json"
  local fresh_sid="ttl-fresh-$$"
  local fresh_file="$PRAXIS_STATE_DIR/strikes/${fresh_sid}.json"
  echo '{"count":0,"reasons":[]}' > "$stale_file"
  echo '{"count":0,"reasons":[]}' > "$fresh_file"
  # Backdate only the stale one (-v for macOS BSD, -d for GNU)
  touch -t "$(date -v-8d +%Y%m%d%H%M 2>/dev/null || date -d '8 days ago' +%Y%m%d%H%M 2>/dev/null)" \
    "$stale_file" 2>/dev/null

  # Sanity: confirm both files were created
  local precheck_ok=1
  [ -f "$stale_file" ] || precheck_ok=0
  [ -f "$fresh_file" ] || precheck_ok=0

  local json_in
  json_in=$(printf '{"session_id":"%s"}' "$CLAUDE_SESSION_ID")
  echo "$json_in" | "$STRIKE" session-start >/dev/null

  local stale_gone=1
  [ -f "$stale_file" ] && stale_gone=0
  local fresh_kept=1
  [ -f "$fresh_file" ] || fresh_kept=0
  cleanup_env
  [ "$precheck_ok" -eq 1 ] && [ "$stale_gone" -eq 1 ] && [ "$fresh_kept" -eq 1 ]
}

# ---- AC25 (issue #126): state never lands inside $CLAUDE_PLUGIN_DATA -------
# Regression guard. Before the fix, $STATE_DIR resolved to
# "${CLAUDE_PLUGIN_DATA:-...}/strikes" so on multi-plugin installs praxis
# strike state could land inside a sibling plugin's data dir (codex/omc/…).
# After the fix, the resolution honors $PRAXIS_STATE_DIR exclusively, so
# pointing $CLAUDE_PLUGIN_DATA at a sibling dir must NOT cause writes there.
test_ac25_state_isolated_from_claude_plugin_data() {
  local praxis_dir sibling_dir
  praxis_dir=$(mktemp -d)
  sibling_dir=$(mktemp -d)
  export PRAXIS_STATE_DIR="$praxis_dir"
  export CLAUDE_PLUGIN_DATA="$sibling_dir"
  export CLAUDE_SESSION_ID="ac25-$$-${RANDOM}"

  local json_in
  json_in=$(printf '{"session_id":"%s"}' "$CLAUDE_SESSION_ID")
  echo "$json_in" | "$STRIKE" session-start >/dev/null
  "$STRIKE" strike "isolation check" >/dev/null

  local praxis_has_state=0 sibling_has_state=0
  [ -f "$praxis_dir/strikes/.current-session" ] && praxis_has_state=1
  [ -d "$sibling_dir/strikes" ] && sibling_has_state=1
  [ -f "$sibling_dir/strikes/.current-session" ] && sibling_has_state=2

  rm -rf "$praxis_dir" "$sibling_dir"
  unset PRAXIS_STATE_DIR CLAUDE_PLUGIN_DATA CLAUDE_SESSION_ID
  [ "$praxis_has_state" -eq 1 ] && [ "$sibling_has_state" -eq 0 ]
}

# ---------- runner ----------------------------------------------------------
echo "strike-counter.sh tests"
echo "------------------------"
run "AC1  first strike → strike 1 warning" test_ac1_first_strike_warning
run "AC2  second strike → strike 2 review + CLAUDE.md" test_ac2_second_strike_review
run "AC3  third strike → strike 3 blocked state + status" test_ac3_third_strike_block_state
run "AC4  stop hook blocks at count≥3 (JSON decision + log)" test_ac4_stop_hook_blocks_at_3
run "AC5  stop hook silent at count<3" test_ac5_stop_hook_silent_under_3
run "AC6  stop_hook_active=true short-circuits" test_ac6_stop_active_short_circuit
run "AC7  reset clears state" test_ac7_reset_clears
run "AC8  preprompt emits strike 1/2 additionalContext" test_ac8_preprompt_contexts
run "AC9  session-start writes latch + emits context" test_ac9_session_start_latch
run "AC10 slash command uses latch when env var unset" test_ac10_latch_fallback
run "AC11 missing session_id → silent fail-safe exit 0" test_ac11_missing_session_id
run "AC12 status prints Strikes: N/3" test_ac12_status_header
run "AC13 hooks.json is valid JSON" test_ac13_hooks_json_valid
run "AC14 session-start exports CLAUDE_SESSION_ID via \$CLAUDE_ENV_FILE" test_ac14_env_file_export
run "AC15 jq missing → stdout+stderr guidance + exit 0" test_ac15_jq_missing_guidance
run "AC16 skill files exist + reference strike-counter.sh" test_ac16_skill_files_exist
run "AC17 TTL cleanup removes stale state files" test_ac17_ttl_cleanup
run "AC18 corrupt state → error branch, not false block (codex P2)" test_ac18_corrupt_state_not_false_block
run "AC19 reset at 3/3 refused without reflection file" test_ac19_reset_blocked_without_reflection
run "AC20 reset at 3/3 refused when reflection file is empty" test_ac20_reset_blocked_when_reflection_empty
run "AC21 reset at 3/3 succeeds with reflection + clears both files" test_ac21_reset_succeeds_with_reflection
run "AC22 reset at count<3 not gated by reflection" test_ac22_reset_not_gated_under_3
run "AC23 stop hook block message includes reflection instructions" test_ac23_stop_block_has_reflection_instructions
run "AC24 block message includes persuasion step instructions" test_ac24_block_message_has_persuasion_step
run "AC25 state isolated from \$CLAUDE_PLUGIN_DATA (issue #126)" test_ac25_state_isolated_from_claude_plugin_data

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
