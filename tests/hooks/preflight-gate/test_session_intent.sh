#!/bin/bash
# test_session_intent.sh — coverage for hooks/preflight-gate/session-intent/impl.py
#
# Synthesizes Claude Code UserPromptSubmit + PreToolUse payloads and asserts:
#   ask    → stdout JSON permissionDecision "ask", rc=0
#   deny   → stdout JSON permissionDecision "deny", rc=0
#   silent → stdout empty, rc=0
#
# Each case gets a fresh state file via $PRAXIS_SESSION_INTENT_FILE so cases
# do not pollute each other.
#
# Usage: bash tests/test_session_intent.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/session-intent/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

WORK_DIR=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
trap 'rm -rf "$WORK_DIR"' EXIT

# run_hook state_file mode_env payload
#   mode_env: "" or "block"
run_hook() {
  local state_file="$1" mode="$2" payload="$3"
  if [ -n "$mode" ]; then
    echo "$payload" \
      | env -u PRAXIS_INTENT_PIVOT_MODE \
            PRAXIS_SESSION_INTENT_FILE="$state_file" \
            PRAXIS_INTENT_PIVOT_MODE="$mode" \
        python3 "$HOOK" 2>/dev/null
  else
    echo "$payload" \
      | env -u PRAXIS_INTENT_PIVOT_MODE \
            PRAXIS_SESSION_INTENT_FILE="$state_file" \
        python3 "$HOOK" 2>/dev/null
  fi
}

# expect_decision out expected_decision
expect_decision() {
  local out="$1" expected="$2"
  echo "$out" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    decision = d.get('hookSpecificOutput', {}).get('permissionDecision', '')
    sys.exit(0 if decision == '$expected' else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null
}

# case name script
#   The script does the full multi-step session and ends by setting:
#     FINAL_OUT  — the last command's stdout
#     FINAL_RC   — the last command's exit code
#     EXPECT     — one of: ask, deny, silent
case_run() {
  local name="$1" expect="$2" actual_out="$3" actual_rc="$4"

  local ok=1
  case "$expect" in
    ask)
      [ "$actual_rc" -eq 0 ] || ok=0
      expect_decision "$actual_out" "ask" || ok=0
      ;;
    deny)
      [ "$actual_rc" -eq 0 ] || ok=0
      expect_decision "$actual_out" "deny" || ok=0
      ;;
    silent)
      [ "$actual_rc" -eq 0 ] || ok=0
      [ -z "$actual_out" ] || ok=0
      ;;
    *)
      echo "  internal: unknown expectation '$expect'" >&2
      ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    PASS=$((PASS + 1))
    echo "  PASS  $name"
  else
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
    echo "  FAIL  $name (rc=$actual_rc, expected=$expect)"
    [ -n "$actual_out" ] && echo "        stdout: $(echo "$actual_out" | head -c 400)"
  fi
}

new_state() {
  local f="$WORK_DIR/state-$RANDOM-$$.json"
  rm -f "$f"
  echo "$f"
}

echo "test_session_intent"

# -----------------------------------------------------------------------
# Case 1: Read-intent opener → state file written, read_intent_anchored=true
# -----------------------------------------------------------------------
SF=$(new_state)
OUT=$(run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","session_id":"test-session-1","prompt":"compare pros/cons of issue 178"}')
RC=$?
# Verify state file
if [ "$RC" -eq 0 ] && [ -f "$SF" ] && python3 -c "
import json, sys
d = json.load(open('$SF'))
sys.exit(0 if d.get('read_intent_anchored') is True and d.get('read_intent_marker') == 'compare' else 1)
" 2>/dev/null; then
  case_run "1. read-intent opener writes anchored state" "silent" "$OUT" "$RC"
else
  case_run "1. read-intent opener writes anchored state" "silent" "BAD-STATE" "$RC"
fi

# -----------------------------------------------------------------------
# Case 2: Mutation verb from user → mutation_verb_seen flagged
# -----------------------------------------------------------------------
SF=$(new_state)
# First prompt: read-intent
run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","session_id":"test-session-2","prompt":"review this design"}' >/dev/null
# Second prompt: mutation verb
OUT=$(run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","session_id":"test-session-2","prompt":"go ahead and merge it"}')
RC=$?
if python3 -c "
import json, sys
d = json.load(open('$SF'))
sys.exit(0 if d.get('mutation_verb_seen') is True else 1)
" 2>/dev/null; then
  case_run "2. mutation verb in later prompt flags mutation_verb_seen" "silent" "$OUT" "$RC"
else
  case_run "2. mutation verb in later prompt flags mutation_verb_seen" "silent" "BAD-STATE" "$RC"
fi

# -----------------------------------------------------------------------
# Case 3: Mutation tool call after read-intent open, no mutation verb → ask
# -----------------------------------------------------------------------
SF=$(new_state)
run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","session_id":"test-session-3","prompt":"analyze the codebase"}' >/dev/null
OUT=$(run_hook "$SF" "" \
  '{"hookEventName":"PreToolUse","session_id":"test-session-3","tool_name":"Bash","tool_input":{"command":"gh issue comment 178 --body foo"}}')
RC=$?
case_run "3. gh issue comment after read-intent open → ask" "ask" "$OUT" "$RC"

# -----------------------------------------------------------------------
# Case 4: Mutation tool call after read-intent open WITH later mutation
# verb → silent pass
# -----------------------------------------------------------------------
SF=$(new_state)
run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","prompt":"review this PR"}' >/dev/null
run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","prompt":"now merge it"}' >/dev/null
OUT=$(run_hook "$SF" "" \
  '{"hookEventName":"PreToolUse","tool_name":"Bash","tool_input":{"command":"gh pr merge 5 --squash"}}')
RC=$?
case_run "4. mutation tool after explicit mutation verb → silent" "silent" "$OUT" "$RC"

# -----------------------------------------------------------------------
# Case 5: Mutation tool call without read-intent open → silent pass
# -----------------------------------------------------------------------
SF=$(new_state)
run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","prompt":"close issue 99 please"}' >/dev/null
OUT=$(run_hook "$SF" "" \
  '{"hookEventName":"PreToolUse","tool_name":"Bash","tool_input":{"command":"gh issue close 99"}}')
RC=$?
case_run "5. session opened with mutation intent → no gate → silent" "silent" "$OUT" "$RC"

# -----------------------------------------------------------------------
# Case 6: Block mode env → deny instead of ask
# -----------------------------------------------------------------------
SF=$(new_state)
run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","prompt":"compare two approaches"}' >/dev/null
OUT=$(run_hook "$SF" "block" \
  '{"hookEventName":"PreToolUse","tool_name":"Bash","tool_input":{"command":"gh pr create --title foo --body bar"}}')
RC=$?
case_run "6. PRAXIS_INTENT_PIVOT_MODE=block → deny" "deny" "$OUT" "$RC"

# -----------------------------------------------------------------------
# Case 7: Non-mutation gh tool call → silent pass (read-only)
# -----------------------------------------------------------------------
SF=$(new_state)
run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","prompt":"analyze the issues"}' >/dev/null
OUT=$(run_hook "$SF" "" \
  '{"hookEventName":"PreToolUse","tool_name":"Bash","tool_input":{"command":"gh issue list --state open"}}')
RC=$?
case_run "7. gh issue list (read) after read-intent → silent" "silent" "$OUT" "$RC"

SF=$(new_state)
run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","prompt":"review the PRs"}' >/dev/null
OUT=$(run_hook "$SF" "" \
  '{"hookEventName":"PreToolUse","tool_name":"Bash","tool_input":{"command":"gh pr view 42"}}')
RC=$?
case_run "7b. gh pr view (read) after read-intent → silent" "silent" "$OUT" "$RC"

# -----------------------------------------------------------------------
# Case 8: Korean read-intent + Korean mutation verbs both detected
# -----------------------------------------------------------------------
SF=$(new_state)
run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","session_id":"test-session-8a","prompt":"이슈 178의 장단점을 비교해줘"}' >/dev/null
OUT=$(run_hook "$SF" "" \
  '{"hookEventName":"PreToolUse","session_id":"test-session-8a","tool_name":"Bash","tool_input":{"command":"gh issue comment 178 --body test"}}')
RC=$?
case_run "8a. Korean read-intent (비교/장단점) → ask on mutation" "ask" "$OUT" "$RC"

# Korean mutation verb releases the gate
SF=$(new_state)
run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","prompt":"PR을 검토해줘"}' >/dev/null
run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","prompt":"이제 머지해도 좋아"}' >/dev/null
OUT=$(run_hook "$SF" "" \
  '{"hookEventName":"PreToolUse","tool_name":"Bash","tool_input":{"command":"gh pr merge 5 --squash"}}')
RC=$?
case_run "8b. Korean mutation verb (머지) releases gate → silent" "silent" "$OUT" "$RC"

# -----------------------------------------------------------------------
# Case 9: Malformed JSON → fail-open (silent)
# -----------------------------------------------------------------------
SF=$(new_state)
OUT=$(run_hook "$SF" "" 'not-json-at-all')
RC=$?
case_run "9. malformed JSON → silent fail-open" "silent" "$OUT" "$RC"

# -----------------------------------------------------------------------
# Case 10: Non-recognized event → silent pass
# -----------------------------------------------------------------------
SF=$(new_state)
OUT=$(run_hook "$SF" "" \
  '{"hookEventName":"SessionStart"}')
RC=$?
case_run "10. unknown hookEventName → silent" "silent" "$OUT" "$RC"

# -----------------------------------------------------------------------
# Case 11: Bash command tokenization respects quoted strings
# A quoted body containing "gh pr merge" text must NOT trigger the gate.
# -----------------------------------------------------------------------
SF=$(new_state)
run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","prompt":"analyze this"}' >/dev/null
OUT=$(run_hook "$SF" "" \
  '{"hookEventName":"PreToolUse","tool_name":"Bash","tool_input":{"command":"echo \"note: gh pr merge later\""}}')
RC=$?
case_run "11a. quoted gh pr merge inside echo → silent (not a real cmd)" "silent" "$OUT" "$RC"

# But a chained mutation should still be caught
SF=$(new_state)
run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","prompt":"analyze this"}' >/dev/null
OUT=$(run_hook "$SF" "" \
  '{"hookEventName":"PreToolUse","tool_name":"Bash","tool_input":{"command":"echo prep && gh issue create --title foo --body bar"}}')
RC=$?
case_run "11b. chained gh issue create after && → ask" "ask" "$OUT" "$RC"

# -----------------------------------------------------------------------
# Case 12: First-call state-file-empty → silent (don't ask on session opener)
# Mutation tool fires BEFORE any UserPromptSubmit → state file empty.
# -----------------------------------------------------------------------
SF=$(new_state)
OUT=$(run_hook "$SF" "" \
  '{"hookEventName":"PreToolUse","tool_name":"Bash","tool_input":{"command":"gh issue close 99"}}')
RC=$?
case_run "12. PreToolUse before any UserPromptSubmit → silent" "silent" "$OUT" "$RC"

# -----------------------------------------------------------------------
# Case 13: Same-utterance read + mutation verb both recorded simultaneously
# "review this PR and merge if good" — gate must NOT fire.
# -----------------------------------------------------------------------
SF=$(new_state)
run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","session_id":"test-session-13","prompt":"review this PR and merge if good"}' >/dev/null
OUT=$(run_hook "$SF" "" \
  '{"hookEventName":"PreToolUse","session_id":"test-session-13","tool_name":"Bash","tool_input":{"command":"gh pr merge 5 --squash"}}')
RC=$?
case_run "13. same-utterance read + mutation verb → silent on later merge" "silent" "$OUT" "$RC"

# -----------------------------------------------------------------------
# Case 14: gh api --method POST after read-intent → ask
# -----------------------------------------------------------------------
SF=$(new_state)
run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","prompt":"check the API behavior"}' >/dev/null
OUT=$(run_hook "$SF" "" \
  '{"hookEventName":"PreToolUse","tool_name":"Bash","tool_input":{"command":"gh api repos/foo/bar/issues --method POST --field title=test"}}')
RC=$?
case_run "14a. gh api --method POST after read-intent → ask" "ask" "$OUT" "$RC"

# gh api default (GET) → silent
SF=$(new_state)
run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","prompt":"check the API behavior"}' >/dev/null
OUT=$(run_hook "$SF" "" \
  '{"hookEventName":"PreToolUse","tool_name":"Bash","tool_input":{"command":"gh api repos/foo/bar"}}')
RC=$?
case_run "14b. gh api (default GET) after read-intent → silent" "silent" "$OUT" "$RC"

# -----------------------------------------------------------------------
# Case 15: Non-Bash tool → silent pass
# -----------------------------------------------------------------------
SF=$(new_state)
run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","prompt":"analyze"}' >/dev/null
OUT=$(run_hook "$SF" "" \
  '{"hookEventName":"PreToolUse","tool_name":"Read","tool_input":{"file_path":"/tmp/x"}}')
RC=$?
case_run "15. non-Bash tool → silent" "silent" "$OUT" "$RC"

# -----------------------------------------------------------------------
# Case 16: Anchor sticks — second prompt without read intent does NOT
# overwrite the original anchor.
# -----------------------------------------------------------------------
SF=$(new_state)
run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","prompt":"compare two options"}' >/dev/null
run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","prompt":"1"}' >/dev/null
OUT=$(run_hook "$SF" "" \
  '{"hookEventName":"PreToolUse","tool_name":"Bash","tool_input":{"command":"gh issue create --title foo --body bar"}}')
RC=$?
case_run "16. anchor sticks across non-mutation follow-ups → ask" "ask" "$OUT" "$RC"

# -----------------------------------------------------------------------
# Case 17: gh -R owner/repo issue create (global flag) → ask
# -----------------------------------------------------------------------
SF=$(new_state)
run_hook "$SF" "" \
  '{"hookEventName":"UserPromptSubmit","prompt":"review existing labels"}' >/dev/null
OUT=$(run_hook "$SF" "" \
  '{"hookEventName":"PreToolUse","tool_name":"Bash","tool_input":{"command":"gh -R owner/repo issue create --title foo --body bar"}}')
RC=$?
case_run "17. gh -R owner/repo issue create after read-intent → ask" "ask" "$OUT" "$RC"

# -----------------------------------------------------------------------
# Case 18: resolve_state_path() does NOT honor $CLAUDE_PROJECT_DIR
# (codex P1 regression on PR #190). A previous session's project-rooted
# state file MUST NOT leak `mutation_verb_seen` into a new session in
# the same project.
# -----------------------------------------------------------------------
PROJECT_DIR="$WORK_DIR/proj-18"
mkdir -p "$PROJECT_DIR"
# Pre-seed a state file at the project-dir path that the OLD code would
# have used. The fixed code must ignore this entirely.
echo '{"read_intent_anchored":true,"read_intent_marker":"compare","first_prompt_snippet":"prior session","mutation_verb_seen":true,"mutation_verb_seen_at":"merge"}' \
  > "$PROJECT_DIR/.praxis-session-intent.json"
# New session: same CLAUDE_PROJECT_DIR, read-intent opener, then mutation
# tool call. If the project-dir branch were still honored, the gate
# would silent-pass (leaked mutation_verb_seen). With the fix, the gate
# must fire (ask).
RESOLVED_PATH=$(
  env -u PRAXIS_SESSION_INTENT_FILE CLAUDE_PROJECT_DIR="$PROJECT_DIR" \
    python3 -c "
import os, sys
sys.path.insert(0, '$ROOT_DIR/hooks')
spec_path = os.path.join('$ROOT_DIR', 'hooks', 'preflight-gate', 'session-intent', 'impl.py')
import importlib.util
spec = importlib.util.spec_from_file_location('session_intent', spec_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.resolve_state_path())
")
case "$RESOLVED_PATH" in
  "$PROJECT_DIR/.praxis-session-intent.json")
    case_run "18. CLAUDE_PROJECT_DIR not used by resolve_state_path()" "silent" "BAD: $RESOLVED_PATH" "1"
    ;;
  *)
    # Path correctly falls through to $TMPDIR/praxis-session-intent-<PPID>.json
    case_run "18. CLAUDE_PROJECT_DIR not used by resolve_state_path()" "silent" "" "0"
    ;;
esac

# -----------------------------------------------------------------------
# Case 19: With CLAUDE_PROJECT_DIR set + a pre-seeded conflicting state
# at the old project-dir path, an explicit PRAXIS_SESSION_INTENT_FILE
# still wins. Confirms the env override remains the authoritative path
# resolution tier after the project-dir tier was removed.
# -----------------------------------------------------------------------
SF=$(new_state)
# Pre-seed a conflicting project-dir state with mutation_verb_seen=true
# (would silently pass if honored).
PROJECT_DIR_19="$WORK_DIR/proj-19"
mkdir -p "$PROJECT_DIR_19"
echo '{"read_intent_anchored":true,"mutation_verb_seen":true}' \
  > "$PROJECT_DIR_19/.praxis-session-intent.json"
# Open the session with read-intent in the explicit state file.
echo '{"hookEventName":"UserPromptSubmit","session_id":"test-session-19","prompt":"analyze the design"}' \
  | env -u PRAXIS_INTENT_PIVOT_MODE \
        PRAXIS_SESSION_INTENT_FILE="$SF" \
        CLAUDE_PROJECT_DIR="$PROJECT_DIR_19" \
    python3 "$HOOK" >/dev/null 2>&1
# Now trigger a mutation tool. With the fix, the explicit state file
# (no mutation_verb_seen) wins → gate fires → ask. If the project-dir
# branch were still active, the seeded mutation_verb_seen=true would
# silent-pass.
OUT=$(echo '{"hookEventName":"PreToolUse","session_id":"test-session-19","tool_name":"Bash","tool_input":{"command":"gh issue comment 1 --body x"}}' \
  | env -u PRAXIS_INTENT_PIVOT_MODE \
        PRAXIS_SESSION_INTENT_FILE="$SF" \
        CLAUDE_PROJECT_DIR="$PROJECT_DIR_19" \
    python3 "$HOOK" 2>/dev/null)
RC=$?
case_run "19. explicit env wins over CLAUDE_PROJECT_DIR seeded state → ask" "ask" "$OUT" "$RC"

# -----------------------------------------------------------------------
# Case 20: Two payloads with different session_id route to different
# state files (codex R2 P1 on PR #190 — session_id is the primary key).
# Without PRAXIS_SESSION_INTENT_FILE override, the resolver must derive
# the path from session_id and produce distinct paths for distinct ids.
# -----------------------------------------------------------------------
PATH_A=$(
  env -u PRAXIS_SESSION_INTENT_FILE -u CLAUDE_PROJECT_DIR \
    python3 -c "
import os, sys
sys.path.insert(0, '$ROOT_DIR/hooks')
import importlib.util
spec = importlib.util.spec_from_file_location('session_intent', os.path.join('$ROOT_DIR', 'hooks', 'preflight-gate', 'session-intent', 'impl.py'))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.resolve_state_path('sess-A'))
")
PATH_B=$(
  env -u PRAXIS_SESSION_INTENT_FILE -u CLAUDE_PROJECT_DIR \
    python3 -c "
import os, sys
sys.path.insert(0, '$ROOT_DIR/hooks')
import importlib.util
spec = importlib.util.spec_from_file_location('session_intent', os.path.join('$ROOT_DIR', 'hooks', 'preflight-gate', 'session-intent', 'impl.py'))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.resolve_state_path('sess-B'))
")
if [ -n "$PATH_A" ] && [ -n "$PATH_B" ] && [ "$PATH_A" != "$PATH_B" ] \
  && echo "$PATH_A" | grep -q "praxis-session-intent-sess-A.json" \
  && echo "$PATH_B" | grep -q "praxis-session-intent-sess-B.json"; then
  case_run "20. different session_id routes to different state files" "silent" "" "0"
else
  case_run "20. different session_id routes to different state files" "silent" "PATH_A=$PATH_A PATH_B=$PATH_B" "1"
fi

# -----------------------------------------------------------------------
# Case 21: session_id from payload takes priority over PPID. When a
# session_id is supplied, the resolved path must NOT contain the parent
# process PID — it must derive from session_id only.
# -----------------------------------------------------------------------
CURRENT_PPID=$$
PATH_SID=$(
  env -u PRAXIS_SESSION_INTENT_FILE -u CLAUDE_PROJECT_DIR \
    python3 -c "
import os, sys
sys.path.insert(0, '$ROOT_DIR/hooks')
import importlib.util
spec = importlib.util.spec_from_file_location('session_intent', os.path.join('$ROOT_DIR', 'hooks', 'preflight-gate', 'session-intent', 'impl.py'))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.resolve_state_path('my-session-key'))
")
# The resolved path should contain 'my-session-key' (not the PPID).
if echo "$PATH_SID" | grep -q "praxis-session-intent-my-session-key.json" \
  && ! echo "$PATH_SID" | grep -q "praxis-session-intent-${CURRENT_PPID}.json"; then
  case_run "21. session_id present → state file derived from session_id, not PPID" "silent" "" "0"
else
  case_run "21. session_id present → state file derived from session_id, not PPID" "silent" "PATH_SID=$PATH_SID PPID=$CURRENT_PPID" "1"
fi

# -----------------------------------------------------------------------
# Case 22: Missing session_id falls back to PPID (back-compat path).
# When the payload does not include session_id, the resolved path must
# still be produced via the PPID fallback so direct CLI / test usage
# without a payload remains functional.
# -----------------------------------------------------------------------
PATH_FALLBACK=$(
  env -u PRAXIS_SESSION_INTENT_FILE -u CLAUDE_PROJECT_DIR \
    python3 -c "
import os, sys
sys.path.insert(0, '$ROOT_DIR/hooks')
import importlib.util
spec = importlib.util.spec_from_file_location('session_intent', os.path.join('$ROOT_DIR', 'hooks', 'preflight-gate', 'session-intent', 'impl.py'))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
# Call with no argument — exercises the PPID fallback branch.
print(mod.resolve_state_path())
")
# Should look like /tmp/praxis-session-intent-<ppid>.json — extract numeric ppid
PPID_PATTERN=$(echo "$PATH_FALLBACK" | sed -n 's/.*praxis-session-intent-\([0-9][0-9]*\)\.json$/\1/p')
if [ -n "$PPID_PATTERN" ]; then
  case_run "22. missing session_id → PPID fallback path" "silent" "" "0"
else
  case_run "22. missing session_id → PPID fallback path" "silent" "PATH_FALLBACK=$PATH_FALLBACK" "1"
fi

echo ""
echo "Result: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed:"
  for name in "${FAILED_NAMES[@]}"; do
    echo "  - $name"
  done
  exit 1
fi
exit 0
