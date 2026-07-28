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

# --- Marker lifecycle: pre-existing marker cleared by unrelated prompt ---
run_case "preexisting_cleared_by_other"   clear "$(mk_prompt 'do something else')" set
# Pre-existing marker preserved (re-SET) by a slash invocation.
run_case "preexisting_kept_by_slash"      set   "$(mk_prompt '/retrospect')" set

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
