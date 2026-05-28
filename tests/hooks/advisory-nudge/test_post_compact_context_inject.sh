#!/bin/bash
# test_post_compact_context_inject.sh — coverage for
# hooks/advisory-nudge/post-compact-context-inject/impl.sh (issue #472)
#
# Asserts the hook's fail-open + dedup + tail-scan invariants:
#   - fires once when transcript tail contains isCompactSummary==true
#   - silent on second invocation (session-scoped marker dedup)
#   - silent when transcript has no compact-summary record
#   - silent on missing/unreadable transcript_path
#   - silent on missing session_id
#   - injection survives missing strike state / missing branch / missing gh
#
# Each test uses an isolated PRAXIS_STATE_DIR so the dedup marker doesn't
# leak between cases or contaminate the user's real state directory.
#
# Usage: bash tests/hooks/advisory-nudge/test_post_compact_context_inject.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/post-compact-context-inject/impl.sh"

if [ ! -x "$HOOK" ]; then
  chmod +x "$HOOK" 2>/dev/null
fi
if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "SKIP: jq not installed — hook silently no-ops without it" >&2
  exit 0
fi

PASS=0
FAIL=0
FAILED_NAMES=()

assert_pass() { PASS=$((PASS+1)); echo "  PASS: $1"; }
assert_fail() { FAIL=$((FAIL+1)); FAILED_NAMES+=("$1"); echo "  FAIL: $1"; [ -n "$2" ] && echo "    $2" >&2; }

# --- transcript fixtures ----------------------------------------------------
make_transcript_with_compact() {
  local path="$1"
  cat > "$path" <<'EOF'
{"type":"user","timestamp":"2026-05-28T09:00:00Z","message":{"role":"user","content":"first"}}
{"type":"assistant","timestamp":"2026-05-28T09:00:01Z","message":{"role":"assistant","content":"reply"}}
{"type":"system","timestamp":"2026-05-28T09:30:00Z","isCompactSummary":true,"summary":"compacted summary text"}
EOF
}

make_transcript_no_compact() {
  local path="$1"
  cat > "$path" <<'EOF'
{"type":"user","timestamp":"2026-05-28T09:00:00Z","message":{"role":"user","content":"first"}}
{"type":"assistant","timestamp":"2026-05-28T09:00:01Z","message":{"role":"assistant","content":"reply"}}
EOF
}

# --- harness ----------------------------------------------------------------
# Each call creates a fresh state dir + transcript + cwd; the hook should
# read PRAXIS_STATE_DIR from env (its own fallback path is HOME-rooted so
# overriding the env is what isolates the marker).
run_hook() {
  local sid="$1" cwd="$2" transcript="$3" state_dir="$4"
  local payload
  payload=$(jq -nc \
    --arg sid "$sid" \
    --arg cwd "$cwd" \
    --arg t "$transcript" \
    '{session_id:$sid, cwd:$cwd, transcript_path:$t}')
  PRAXIS_STATE_DIR="$state_dir" bash "$HOOK" <<< "$payload"
}

# --- test cases -------------------------------------------------------------

echo ""
echo "== Test 1: fires when transcript has isCompactSummary =="
TMP=$(mktemp -d)
make_transcript_with_compact "$TMP/transcript.jsonl"
OUT=$(run_hook "sid-1" "$TMP" "$TMP/transcript.jsonl" "$TMP/state")
RC=$?
if [ "$RC" -eq 0 ] \
  && echo "$OUT" | jq -e '.hookSpecificOutput.hookEventName == "UserPromptSubmit"' >/dev/null 2>&1 \
  && echo "$OUT" | jq -e '.hookSpecificOutput.additionalContext | contains("Post-compact")' >/dev/null 2>&1; then
  assert_pass "fires once with additionalContext block"
else
  assert_fail "fires once with additionalContext block" "rc=$RC, out=$OUT"
fi

echo ""
echo "== Test 2: silent on second invocation (marker dedup) =="
OUT2=$(run_hook "sid-1" "$TMP" "$TMP/transcript.jsonl" "$TMP/state")
RC2=$?
if [ "$RC2" -eq 0 ] && [ -z "$OUT2" ]; then
  assert_pass "second invocation produces no output"
else
  assert_fail "second invocation produces no output" "rc=$RC2, out=$OUT2"
fi
rm -rf "$TMP"

echo ""
echo "== Test 3: silent when transcript has no compact summary =="
TMP=$(mktemp -d)
make_transcript_no_compact "$TMP/transcript.jsonl"
OUT=$(run_hook "sid-2" "$TMP" "$TMP/transcript.jsonl" "$TMP/state")
RC=$?
if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then
  assert_pass "no isCompactSummary → no output"
else
  assert_fail "no isCompactSummary → no output" "rc=$RC, out=$OUT"
fi
rm -rf "$TMP"

echo ""
echo "== Test 4: silent when transcript_path is missing =="
TMP=$(mktemp -d)
OUT=$(run_hook "sid-3" "$TMP" "$TMP/does-not-exist.jsonl" "$TMP/state")
RC=$?
if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then
  assert_pass "missing transcript file → fail-open silent"
else
  assert_fail "missing transcript file → fail-open silent" "rc=$RC, out=$OUT"
fi
rm -rf "$TMP"

echo ""
echo "== Test 5: silent when session_id is missing =="
TMP=$(mktemp -d)
make_transcript_with_compact "$TMP/transcript.jsonl"
OUT=$(run_hook "" "$TMP" "$TMP/transcript.jsonl" "$TMP/state")
RC=$?
if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then
  assert_pass "empty session_id → fail-open silent"
else
  assert_fail "empty session_id → fail-open silent" "rc=$RC, out=$OUT"
fi
rm -rf "$TMP"

echo ""
echo "== Test 6: injection still fires with missing strike state =="
TMP=$(mktemp -d)
make_transcript_with_compact "$TMP/transcript.jsonl"
# No strike file under $TMP/state/strikes/ — should still inject, just with 0/3.
OUT=$(run_hook "sid-6" "$TMP" "$TMP/transcript.jsonl" "$TMP/state")
RC=$?
if [ "$RC" -eq 0 ] \
  && echo "$OUT" | jq -e '.hookSpecificOutput.additionalContext | contains("0/3")' >/dev/null 2>&1; then
  assert_pass "missing strike state reports 0/3 without aborting"
else
  assert_fail "missing strike state reports 0/3 without aborting" "rc=$RC, out=$OUT"
fi
rm -rf "$TMP"

echo ""
echo "== Test 7: malformed JSON payload → fail-open silent =="
OUT=$(echo 'not-json' | bash "$HOOK")
RC=$?
if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then
  assert_pass "garbage payload → no output, rc 0"
else
  assert_fail "garbage payload → no output, rc 0" "rc=$RC, out=$OUT"
fi

# --- summary ----------------------------------------------------------------
echo ""
echo "================================================================"
echo "Tests passed: $PASS"
echo "Tests failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed: ${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
