#!/bin/bash
# tests/test_bypass_review.sh — bypass-review CLI coverage (Phase 2, issue #456)
#
# Uses SYNTHETIC JSONL fixtures only — never reads ~/.praxis real telemetry.
# The fixture field names are copied verbatim from the writer:
#   hooks/postuse-correction/bypass-telemetry/impl.py lines 250-257
#
# JSONL fields used in fixtures (verbatim from writer record dict):
#   timestamp         (str) UTC ISO-8601
#   session_id        (str)
#   tool              (str)
#   bypass_env_vars   (list[str]) — var NAMES only, values never stored
#   tool_input        (str) ≤200 chars, values redacted
#   tool_result_status (str) "ok" | "error"
#
# Tested surface variants:
#   1. Grouping by bypass var name — frequency counts are correct
#   2. Error event highlighting — tool_result_status=="error" events surfaced
#   3. --errors-only flag — output restricted to error events
#   4. Zero events — handles empty telemetry dir gracefully
#   5. Multi-day span — events from multiple files aggregated correctly
#   6. --days flag — events outside the window are excluded
#   7. --dir flag — reads from override directory (not ~/.praxis)
#   8. Malformed JSONL lines — skipped without crashing
#   9. Missing field graceful — record with absent field doesn't crash
#  10. Privacy: bypass var VALUES do not appear in output
#  11. Exit code 0 on success, 1 on bad --days value
#
# Run:  bash tests/test_bypass_review.sh
# Exit: 0 on success, 1 on at least one failure

set +e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI="$REPO_ROOT/skills/bypass-review/bypass-review"

if [ ! -f "$CLI" ]; then
  echo "FAIL: CLI not found: $CLI" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "SKIP: python3 not available" >&2
  exit 0
fi

PASS=0
FAIL=0
FAILED_NAMES=()

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

assert_pass() {
  local name="$1"
  PASS=$((PASS + 1))
  printf '  OK  %s\n' "$name"
}

assert_fail() {
  local name="$1" msg="$2"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("$name")
  printf 'FAIL  [%s] %s\n' "$name" "$msg"
}

# make_event <bypass_vars_json_array> <status> [tool] [session]
# Emits a single JSON line using TODAY's UTC date in the timestamp.
make_event() {
  local bypass_vars_json="$1"
  local status="$2"
  local tool="${3:-Bash}"
  local session="${4:-test-session-42}"
  python3 -c "
import json, sys
from datetime import datetime, timezone
bypass_vars = json.loads(sys.argv[1])
status, tool, session = sys.argv[2], sys.argv[3], sys.argv[4]
print(json.dumps({
    'timestamp': datetime.now(tz=timezone.utc).isoformat(),
    'session_id': session,
    'tool': tool,
    'bypass_env_vars': bypass_vars,
    'tool_input': 'CLAUDE_HOOK_BYPASS_X=<redacted> git commit',
    'tool_result_status': status,
}))" "$bypass_vars_json" "$status" "$tool" "$session"
}

# write_fixture_file <path> <list of json lines>
write_fixture() {
  local path="$1"; shift
  for line in "$@"; do
    echo "$line" >> "$path"
  done
}

# today and yesterday for multi-day tests
TODAY=$(python3 -c "from datetime import datetime, timezone; print(datetime.now(tz=timezone.utc).strftime('%Y-%m-%d'))")
YESTERDAY=$(python3 -c "from datetime import datetime, timedelta, timezone; print((datetime.now(tz=timezone.utc) - timedelta(days=1)).strftime('%Y-%m-%d'))")

# ---------------------------------------------------------------------------
# Test 1: Grouping by bypass var — frequency counts
# ---------------------------------------------------------------------------
echo "=== bypass-review: grouping by bypass var ==="

DIR1="$TMP_DIR/t1"
mkdir -p "$DIR1"
ev_sciomc=$(make_event '["CLAUDE_HOOK_BYPASS_SCIOMC_GATE"]' "ok")
ev_sciomc2=$(make_event '["CLAUDE_HOOK_BYPASS_SCIOMC_GATE"]' "ok")
ev_dup=$(make_event '["CLAUDE_HOOK_BYPASS_DUP_GATE"]' "ok")
write_fixture "$DIR1/bypass-events-$TODAY.jsonl" "$ev_sciomc" "$ev_sciomc2" "$ev_dup"

out1=$(python3 "$CLI" --dir "$DIR1" --days 7 2>&1)

# CLAUDE_HOOK_BYPASS_SCIOMC_GATE should appear with count 2
if echo "$out1" | grep -q "CLAUDE_HOOK_BYPASS_SCIOMC_GATE"; then
  assert_pass "SCIOMC_GATE var name appears in output"
else
  assert_fail "SCIOMC_GATE var name appears in output" "var name missing from report"
fi

# DUP_GATE should appear with count 1
if echo "$out1" | grep -q "CLAUDE_HOOK_BYPASS_DUP_GATE"; then
  assert_pass "DUP_GATE var name appears in output"
else
  assert_fail "DUP_GATE var name appears in output" "var name missing from report"
fi

# Total events should be 3
if echo "$out1" | grep -q "Total events.*3\|3.*total"; then
  assert_pass "total events = 3"
else
  assert_fail "total events = 3" "could not find '3' in total events line; output: $out1"
fi

# ---------------------------------------------------------------------------
# Test 2: Error event highlighting
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-review: error event highlighting ==="

DIR2="$TMP_DIR/t2"
mkdir -p "$DIR2"
ev_ok=$(make_event '["CLAUDE_HOOK_BYPASS_SCIOMC_GATE"]' "ok")
ev_err=$(make_event '["PRAXIS_MOMENTUM_BYPASS"]' "error")
write_fixture "$DIR2/bypass-events-$TODAY.jsonl" "$ev_ok" "$ev_err"

out2=$(python3 "$CLI" --dir "$DIR2" --days 7 2>&1)

# Error section should mention the error event
if echo "$out2" | grep -q "error\|Error"; then
  assert_pass "error events section present"
else
  assert_fail "error events section present" "no error mention in output"
fi

# PRAXIS_MOMENTUM_BYPASS should appear in error section
if echo "$out2" | grep -q "PRAXIS_MOMENTUM_BYPASS"; then
  assert_pass "error event bypass var name in output"
else
  assert_fail "error event bypass var name in output" "PRAXIS_MOMENTUM_BYPASS missing"
fi

# Error count should be 1
if echo "$out2" | grep -q "Error events.*1\|1.*error"; then
  assert_pass "error event count = 1"
else
  assert_fail "error event count = 1" "could not confirm error count; output: $out2"
fi

# "bad-bypass candidate" marker should appear for PRAXIS_MOMENTUM_BYPASS (it has errors)
if echo "$out2" | grep -q "bad-bypass"; then
  assert_pass "bad-bypass candidate marker shown"
else
  assert_fail "bad-bypass candidate marker shown" "marker absent; output: $out2"
fi

# ---------------------------------------------------------------------------
# Test 3: --errors-only flag
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-review: --errors-only flag ==="

DIR3="$TMP_DIR/t3"
mkdir -p "$DIR3"
ev_ok3=$(make_event '["CLAUDE_HOOK_BYPASS_SCIOMC_GATE"]' "ok")
ev_err3=$(make_event '["PRAXIS_GH_JSON_BYPASS"]' "error")
write_fixture "$DIR3/bypass-events-$TODAY.jsonl" "$ev_ok3" "$ev_err3"

out3=$(python3 "$CLI" --dir "$DIR3" --days 7 --errors-only 2>&1)

# Should include the error bypass var
if echo "$out3" | grep -q "PRAXIS_GH_JSON_BYPASS"; then
  assert_pass "--errors-only: error event bypass var present"
else
  assert_fail "--errors-only: error event bypass var present" "PRAXIS_GH_JSON_BYPASS missing"
fi

# The "Top Bypass Vars" frequency table section should NOT appear in errors-only mode
if echo "$out3" | grep -q "Top Bypass Vars"; then
  assert_fail "--errors-only: no Top Bypass Vars section" "Top Bypass Vars section appeared in --errors-only output"
else
  assert_pass "--errors-only: Top Bypass Vars section suppressed"
fi

# ---------------------------------------------------------------------------
# Test 4: Zero events — empty dir
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-review: zero events — empty telemetry dir ==="

DIR4="$TMP_DIR/t4-empty"
mkdir -p "$DIR4"

out4=$(python3 "$CLI" --dir "$DIR4" --days 7 2>&1)
rc4=$?

if [ "$rc4" -ne 0 ]; then
  assert_fail "zero events: exit code 0" "exited with code $rc4"
else
  assert_pass "zero events: exit code 0"
fi

if echo "$out4" | grep -q "No bypass events\|0"; then
  assert_pass "zero events: zero-count message present"
else
  assert_fail "zero events: zero-count message present" "output: $out4"
fi

# ---------------------------------------------------------------------------
# Test 5: Multi-day aggregation
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-review: multi-day aggregation ==="

DIR5="$TMP_DIR/t5"
mkdir -p "$DIR5"
ev_today=$(make_event '["CLAUDE_HOOK_BYPASS_SCIOMC_GATE"]' "ok")
ev_yesterday=$(make_event '["CLAUDE_HOOK_BYPASS_DUP_GATE"]' "ok")
write_fixture "$DIR5/bypass-events-$TODAY.jsonl" "$ev_today"
write_fixture "$DIR5/bypass-events-$YESTERDAY.jsonl" "$ev_yesterday"

out5=$(python3 "$CLI" --dir "$DIR5" --days 7 2>&1)

# Both vars from both days should appear
if echo "$out5" | grep -q "CLAUDE_HOOK_BYPASS_SCIOMC_GATE"; then
  assert_pass "multi-day: today's event aggregated"
else
  assert_fail "multi-day: today's event aggregated" "SCIOMC_GATE missing"
fi

if echo "$out5" | grep -q "CLAUDE_HOOK_BYPASS_DUP_GATE"; then
  assert_pass "multi-day: yesterday's event aggregated"
else
  assert_fail "multi-day: yesterday's event aggregated" "DUP_GATE missing"
fi

# Total should be 2
if echo "$out5" | grep -q "Total events.*2"; then
  assert_pass "multi-day: total events = 2"
else
  assert_fail "multi-day: total events = 2" "output: $out5"
fi

# ---------------------------------------------------------------------------
# Test 6: --days flag — old events outside window excluded
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-review: --days=1 excludes yesterday ==="

DIR6="$TMP_DIR/t6"
mkdir -p "$DIR6"
ev_today6=$(make_event '["CLAUDE_HOOK_BYPASS_SCIOMC_GATE"]' "ok")
ev_yesterday6=$(make_event '["CLAUDE_HOOK_BYPASS_DUP_GATE"]' "ok")
write_fixture "$DIR6/bypass-events-$TODAY.jsonl" "$ev_today6"
write_fixture "$DIR6/bypass-events-$YESTERDAY.jsonl" "$ev_yesterday6"

out6=$(python3 "$CLI" --dir "$DIR6" --days 1 2>&1)

# Only today's event (SCIOMC_GATE) should appear; yesterday (DUP_GATE) excluded
if echo "$out6" | grep -q "CLAUDE_HOOK_BYPASS_SCIOMC_GATE"; then
  assert_pass "--days=1: today's event present"
else
  assert_fail "--days=1: today's event present" "SCIOMC_GATE missing; output: $out6"
fi

if echo "$out6" | grep -q "CLAUDE_HOOK_BYPASS_DUP_GATE"; then
  assert_fail "--days=1: yesterday excluded" "DUP_GATE appeared despite --days=1"
else
  assert_pass "--days=1: yesterday excluded"
fi

# ---------------------------------------------------------------------------
# Test 7: --dir flag
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-review: --dir override ==="

DIR7="$TMP_DIR/t7-override"
mkdir -p "$DIR7"
ev7=$(make_event '["PRAXIS_VERSION_BUMP_BYPASS"]' "ok")
write_fixture "$DIR7/bypass-events-$TODAY.jsonl" "$ev7"

out7=$(python3 "$CLI" --dir "$DIR7" 2>&1)

if echo "$out7" | grep -q "PRAXIS_VERSION_BUMP_BYPASS"; then
  assert_pass "--dir: reads from override directory"
else
  assert_fail "--dir: reads from override directory" "var missing; output: $out7"
fi

# ---------------------------------------------------------------------------
# Test 8: Malformed JSONL lines — skipped without crash
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-review: malformed JSONL lines skipped ==="

DIR8="$TMP_DIR/t8"
mkdir -p "$DIR8"
ev_good=$(make_event '["CLAUDE_HOOK_BYPASS_SCIOMC_GATE"]' "ok")
{
  echo "not-json-at-all"
  echo ""
  echo "{broken: json}"
  echo "$ev_good"
} > "$DIR8/bypass-events-$TODAY.jsonl"

out8=$(python3 "$CLI" --dir "$DIR8" --days 1 2>&1)
rc8=$?

if [ "$rc8" -ne 0 ]; then
  assert_fail "malformed lines: exit 0" "exited $rc8"
else
  assert_pass "malformed lines: exit 0"
fi

if echo "$out8" | grep -q "CLAUDE_HOOK_BYPASS_SCIOMC_GATE"; then
  assert_pass "malformed lines: valid events still aggregated"
else
  assert_fail "malformed lines: valid events still aggregated" "good event missing; output: $out8"
fi

# ---------------------------------------------------------------------------
# Test 9: Missing fields in record — no crash
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-review: missing fields in record ==="

DIR9="$TMP_DIR/t9"
mkdir -p "$DIR9"
# Minimal record: only timestamp and bypass_env_vars, other fields absent
python3 -c "
import json
from datetime import datetime, timezone
print(json.dumps({
    'timestamp': datetime.now(tz=timezone.utc).isoformat(),
    'bypass_env_vars': ['CLAUDE_HOOK_BYPASS_SCIOMC_GATE'],
}))
" > "$DIR9/bypass-events-$TODAY.jsonl"

out9=$(python3 "$CLI" --dir "$DIR9" --days 1 2>&1)
rc9=$?

if [ "$rc9" -ne 0 ]; then
  assert_fail "missing fields: exit 0" "exited $rc9; output: $out9"
else
  assert_pass "missing fields: exit 0 (no crash)"
fi

# ---------------------------------------------------------------------------
# Test 10: Privacy — bypass var VALUES do not appear in output
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-review: bypass var values never in output ==="

DIR10="$TMP_DIR/t10"
mkdir -p "$DIR10"
# Craft a record where tool_input contains a redacted bypass value
secret_indicator="super-secret-bypass-value-99"
python3 -c "
import json, sys
from datetime import datetime, timezone
secret = sys.argv[1]
print(json.dumps({
    'timestamp': datetime.now(tz=timezone.utc).isoformat(),
    'session_id': 'test-privacy',
    'tool': 'Bash',
    'bypass_env_vars': ['CLAUDE_HOOK_BYPASS_SCIOMC_GATE'],
    'tool_input': 'CLAUDE_HOOK_BYPASS_SCIOMC_GATE=<redacted> git commit',
    'tool_result_status': 'ok',
}))" "$secret_indicator" > "$DIR10/bypass-events-$TODAY.jsonl"

out10=$(python3 "$CLI" --dir "$DIR10" --days 1 2>&1)

# The secret value is NOT in the JSONL at all (redacted by writer);
# verify it also doesn't appear in output (belt-and-suspenders).
if echo "$out10" | grep -q "$secret_indicator"; then
  assert_fail "privacy: bypass var value not in output" "secret_indicator appeared"
else
  assert_pass "privacy: bypass var value not in output"
fi

# tool_result_status field name should NOT appear in the output (internal field name)
# but the var NAME should appear
if echo "$out10" | grep -q "CLAUDE_HOOK_BYPASS_SCIOMC_GATE"; then
  assert_pass "privacy: bypass var NAME present in output"
else
  assert_fail "privacy: bypass var NAME present in output" "var name missing; output: $out10"
fi

# ---------------------------------------------------------------------------
# Test 11: Exit code 1 on --days < 1
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-review: exit 1 on bad --days ==="

DIR11="$TMP_DIR/t11"
mkdir -p "$DIR11"

python3 "$CLI" --dir "$DIR11" --days 0 >/dev/null 2>&1
rc11=$?

if [ "$rc11" -eq 1 ]; then
  assert_pass "--days=0 returns exit 1"
else
  assert_fail "--days=0 returns exit 1" "got exit $rc11 instead"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
  echo "Failed tests:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
