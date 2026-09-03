#!/bin/bash
# test_retrospect_active_marker.sh — coverage for
# hooks/preflight-gate/retrospect-active-marker/impl.py
#
# The hook never blocks; it only SETs/CLEARs a session-scoped marker file. Each
# case asserts (a) rc=0, (b) empty stdout, (c) marker file present/absent after.
# A fresh state file via $PRAXIS_RETROSPECT_ACTIVE_FILE isolates cases.
#
# Usage: bash tests/hooks/preflight-gate/test_retrospect_active_marker.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/retrospect-active-marker/impl.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi
if [ ! -r "$HOOK" ]; then
  echo "FAIL: hook not readable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

WORK_DIR=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
trap 'rm -rf "$WORK_DIR"' EXIT

CASE_N=0

# run_case name expected(set|clear) payload [preexisting(set|empty)]
run_case() {
  local name="$1" expected="$2" payload="$3" pre="${4:-empty}"
  CASE_N=$((CASE_N + 1))
  local sf="$WORK_DIR/marker_${CASE_N}.json"
  if [ "$pre" = "set" ]; then
    printf '%s' '{"retrospect_active": true, "source": "test"}' > "$sf"
  fi

  local out rc
  out=$(printf '%s' "$payload" | env PRAXIS_RETROSPECT_ACTIVE_FILE="$sf" python3 "$HOOK" 2>/dev/null)
  rc=$?

  if [ "$rc" -ne 0 ]; then
    echo "FAIL  [$name] rc=$rc (expected 0)"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
  fi
  if [ -n "$out" ]; then
    echo "FAIL  [$name] expected empty stdout, got: $out"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
  fi

  case "$expected" in
    set)
      if [ ! -f "$sf" ]; then
        echo "FAIL  [$name] expected marker SET (file present), but absent"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      fi
      ;;
    clear)
      if [ -f "$sf" ]; then
        echo "FAIL  [$name] expected marker CLEAR (file absent), but present"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      fi
      ;;
    *)
      echo "FAIL  [$name] unknown expected: $expected"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      ;;
  esac
  echo "PASS  [$name]"
  PASS=$((PASS + 1))
}

mk_skill() {  # $1 = skill name
  jq -nc --arg s "$1" '{tool_name: "Skill", tool_input: {skill: $s}, session_id: "test"}'
}
mk_prompt() {  # $1 = prompt
  jq -nc --arg p "$1" '{prompt: $p, session_id: "test"}'
}

# --- PreToolUse(Skill) SET cases ---
run_case "skill_praxis_retrospect_sets"   set "$(mk_skill 'praxis:retrospect')"
run_case "skill_bare_retrospect_sets"     set "$(mk_skill 'retrospect')"
run_case "skill_mixed_case_sets"          set "$(mk_skill 'praxis:Retrospect')"

# --- PreToolUse(Skill) non-retrospect: must NOT set ---
run_case "skill_other_does_not_set"       clear "$(mk_skill 'praxis:create-hub-issue')"
run_case "skill_substring_guard"          clear "$(mk_skill 'praxis:retro-spectrum')"

# --- PreToolUse non-Skill tool: must NOT set ---
run_case "pretooluse_bash_does_not_set"   clear \
  "$(jq -nc '{tool_name: "Bash", tool_input: {command: "echo retrospect"}, session_id: "test"}')"

# --- UserPromptSubmit slash invocation SET ---
run_case "slash_retrospect_sets"          set "$(mk_prompt '/retrospect')"
run_case "slash_praxis_retrospect_sets"   set "$(mk_prompt '/praxis:retrospect now')"
run_case "slash_leading_ws_sets"          set "$(mk_prompt '   /retrospect')"

# --- UserPromptSubmit non-invocation CLEAR ---
run_case "prompt_other_clears"            clear "$(mk_prompt 'fix the failing test in foo.py')"
run_case "prompt_casual_mention_no_set"   clear "$(mk_prompt 'can you explain the retrospect 회고 flow?')"
run_case "slash_substring_guard"          clear "$(mk_prompt '/retrospects-are-cool')"

# --- Marker lifecycle: an unrelated prompt DECAYS rather than clears (#1098) ---
# The documented flow "skill invoked -> clarification -> user answers -> Stage 3
# report" puts one ordinary prompt between SET and the report. Clearing on it
# disarmed the #666 gate for the report that followed, so the marker now spends
# a turn of MARKER_TURN_BUDGET instead. Full decay is covered below.
run_case "preexisting_survives_one_other"  set   "$(mk_prompt 'do something else')" set
# Pre-existing marker preserved (re-SET) by a slash invocation.
run_case "preexisting_kept_by_slash"      set   "$(mk_prompt '/retrospect')" set

# --- Budget decay (#1098): the armed window is bounded, not permanent ---
# Feeds N ordinary prompts into one marker file and reports the state after each.
# run_decay name budget expected_after_each("set set clear ...")
run_decay() {
  local name="$1" budget="$2"; shift 2
  CASE_N=$((CASE_N + 1))
  local sf="$WORK_DIR/decay_${CASE_N}.json"
  if [ "$budget" = "legacy" ]; then
    # A marker written before #1098 carries no turns_remaining; it must count as
    # a full budget rather than decaying instantly or never.
    printf '%s' '{"retrospect_active": true, "source": "skill"}' > "$sf"
  else
    printf '{"retrospect_active": true, "source": "skill", "turns_remaining": %s}' "$budget" > "$sf"
  fi
  local turn=0 expected ok=true
  for expected in "$@"; do
    turn=$((turn + 1))
    printf '%s' "$(mk_prompt 'unrelated work')" \
      | env PRAXIS_RETROSPECT_ACTIVE_FILE="$sf" python3 "$HOOK" >/dev/null 2>&1
    local actual="clear"; [ -f "$sf" ] && actual="set"
    if [ "$actual" != "$expected" ]; then
      echo "FAIL  [$name] after turn $turn: expected $expected, got $actual"
      ok=false; break
    fi
  done
  if $ok; then echo "PASS  [$name]"; PASS=$((PASS + 1))
  else FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); fi
}

run_decay "decay_full_budget_disarms_on_third" 3 set set clear clear
run_decay "decay_legacy_body_counts_as_full"   legacy set set clear
run_decay "decay_last_turn_clears"             1 clear
run_decay "decay_corrupt_body_counts_as_full"  '"not-a-number"' set set clear

# A slash invocation mid-decay re-arms the full budget.
CASE_N=$((CASE_N + 1))
DECAY_SF="$WORK_DIR/rearm_${CASE_N}.json"
printf '%s' '{"retrospect_active": true, "source": "skill", "turns_remaining": 1}' > "$DECAY_SF"
printf '%s' "$(mk_prompt '/retrospect')" \
  | env PRAXIS_RETROSPECT_ACTIVE_FILE="$DECAY_SF" python3 "$HOOK" >/dev/null 2>&1
printf '%s' "$(mk_prompt 'unrelated work')" \
  | env PRAXIS_RETROSPECT_ACTIVE_FILE="$DECAY_SF" python3 "$HOOK" >/dev/null 2>&1
if [ -f "$DECAY_SF" ]; then
  echo "PASS  [slash_rearms_full_budget]"; PASS=$((PASS + 1))
else
  echo "FAIL  [slash_rearms_full_budget] marker cleared — budget was not re-armed"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("slash_rearms_full_budget")
fi

# --- Fail-open ---
run_case "malformed_json_failopen"        clear '{not valid json{{'
run_case "empty_stdin_failopen"           clear ''

echo
echo "================================"
echo "retrospect-active-marker: $PASS passed, $FAIL failed"
echo "================================"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed:"; for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
