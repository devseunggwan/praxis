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
#   2. Hook/rule-family + command-family summaries — grouped safely
#   3. Error event highlighting — tool_result_status=="error" events surfaced
#   4. --errors-only flag — output restricted to error events
#   5. Zero events — handles empty telemetry dir gracefully
#   6. Multi-day span — events from multiple files aggregated correctly
#   7. --days flag — events outside the window are excluded
#   8. --dir flag — reads from override directory (not ~/.praxis)
#   9. Malformed JSONL lines — skipped without crashing
#  10. Missing field graceful — record with absent field doesn't crash
#  11. Privacy: bypass var VALUES do not appear in output
#  12. Privacy: path-like command families do not expose full paths
#  13. Exit code 0 on success, 1 on bad --days value
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

TMP_DIR="$(mktemp -d)" || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
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

# make_event <bypass_vars_json_array> <status> [tool] [session] [tool_input]
# Emits a single JSON line using TODAY's UTC date in the timestamp.
make_event() {
  local bypass_vars_json="$1"
  local status="$2"
  local tool="${3:-Bash}"
  local session="${4:-test-session-42}"
  local tool_input="${5:-CLAUDE_HOOK_BYPASS_X=<redacted> git commit}"
  python3 -c "
import json, sys
from datetime import datetime, timezone
bypass_vars = json.loads(sys.argv[1])
status, tool, session, tool_input = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
print(json.dumps({
    'timestamp': datetime.now(tz=timezone.utc).isoformat(),
    'session_id': session,
    'tool': tool,
    'bypass_env_vars': bypass_vars,
    'tool_input': tool_input,
    'tool_result_status': status,
}))" "$bypass_vars_json" "$status" "$tool" "$session" "$tool_input"
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
# Test 2: Hook/rule-family + command-family summaries
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-review: hook and command family summaries ==="

DIR1B="$TMP_DIR/t1b"
mkdir -p "$DIR1B"
ev_git_family=$(make_event '["CLAUDE_HOOK_BYPASS_SCIOMC_GATE"]' "ok" "Bash" "sess-git" \
  'CLAUDE_HOOK_BYPASS_SCIOMC_GATE=<redacted> git commit -m fix')
ev_gh_family=$(make_event '["PRAXIS_HOOK_BYPASS_WORKTREE_GATE"]' "error" "Bash" "sess-gh1" \
  'PRAXIS_HOOK_BYPASS_WORKTREE_GATE=<redacted> gh pr create')
ev_gh_json=$(make_event '["PRAXIS_GH_JSON_BYPASS"]' "error" "Bash" "sess-gh2" \
  'PRAXIS_GH_JSON_BYPASS=<redacted> gh issue view 1 --json state')
ev_lower_env=$(make_event '["PRAXIS_GH_JSON_BYPASS"]' "ok" "Bash" "sess-gh3" \
  'foo=<redacted> gh issue list')
ev_env_ignore=$(make_event '["PRAXIS_GH_JSON_BYPASS"]' "ok" "Bash" "sess-gh4" \
  'env -i PRAXIS_GH_JSON_BYPASS=<redacted> gh pr list')
ev_env_chdir=$(make_event '["PRAXIS_GH_JSON_BYPASS"]' "ok" "Bash" "sess-gh5" \
  'env -C /tmp PRAXIS_GH_JSON_BYPASS=<redacted> gh pr view 1')
ev_env_separator=$(make_event '["PRAXIS_GH_JSON_BYPASS"]' "ok" "Bash" "sess-gh6" \
  'env -- PRAXIS_GH_JSON_BYPASS=<redacted> gh pr status')
ev_env_dash_separator=$(make_event '["PRAXIS_GH_JSON_BYPASS"]' "ok" "Bash" "sess-gh7" \
  'env - PRAXIS_GH_JSON_BYPASS=<redacted> gh pr checks 1')
ev_env_split_string=$(make_event '["PRAXIS_GH_JSON_BYPASS"]' "ok" "Bash" "sess-gh8" \
  'env -S "PRAXIS_GH_JSON_BYPASS=<redacted> gh pr diff 1"')
ev_env_split_string_equals=$(make_event '["PRAXIS_GH_JSON_BYPASS"]' "ok" "Bash" "sess-gh9" \
  'env --split-string="PRAXIS_GH_JSON_BYPASS=<redacted> gh issue list"')
write_fixture "$DIR1B/bypass-events-$TODAY.jsonl" "$ev_git_family" "$ev_gh_family" "$ev_gh_json" "$ev_lower_env" "$ev_env_ignore" "$ev_env_chdir" "$ev_env_separator" "$ev_env_dash_separator" "$ev_env_split_string" "$ev_env_split_string_equals"

out1b=$(python3 "$CLI" --dir "$DIR1B" --days 7 2>&1)

if echo "$out1b" | grep -q "Per-Hook Aggregation"; then
  assert_pass "hook/rule family section present"
else
  assert_fail "hook/rule family section present" "section missing; output: $out1b"
fi

if echo "$out1b" | grep -q "Bypass by Command Family"; then
  assert_pass "command family section present"
else
  assert_fail "command family section present" "section missing; output: $out1b"
fi

if echo "$out1b" | grep -q "Error-Linked Bypass Signals"; then
  assert_pass "error-linked summary section present"
else
  assert_fail "error-linked summary section present" "section missing; output: $out1b"
fi

if echo "$out1b" | grep -q "SCIOMC_GATE" && echo "$out1b" | grep -q "WORKTREE_GATE" && echo "$out1b" | grep -q "GH_JSON"; then
  assert_pass "normalized hook/rule families shown"
else
  assert_fail "normalized hook/rule families shown" "expected normalized families missing; output: $out1b"
fi

if echo "$out1b" | grep -q "gh" && echo "$out1b" | grep -q "git"; then
  assert_pass "command families shown"
else
  assert_fail "command families shown" "expected command families missing; output: $out1b"
fi

if printf '%s\n' "$out1b" | grep -Fq "gh (2)" && printf '%s\n' "$out1b" | grep -Fq "WORKTREE_GATE (1)"; then
  assert_pass "error-linked summary groups failed bypasses"
else
  assert_fail "error-linked summary groups failed bypasses" "expected error-linked summary entries missing; output: $out1b"
fi

if printf '%s\n' "$out1b" | grep -Fq "foo=<redacted>" || printf '%s\n' "$out1b" | grep -Fq -- "--ignore-environment" || printf '%s\n' "$out1b" | grep -Fq " -C " || printf '%s\n' "$out1b" | grep -Fq "(unknown)"; then
  assert_fail "command family skips env assignments and env options" "env assignment/option leaked into command family; output: $out1b"
else
  assert_pass "command family skips env assignments and env options"
fi

DIR1C="$TMP_DIR/t1c"
mkdir -p "$DIR1C"
ev_env_split_string_malformed=$(make_event '["PRAXIS_GH_JSON_BYPASS"]' "ok" "Bash" "sess-gh10" \
  'env -S "PRAXIS_GH_JSON_BYPASS=<redacted> gh pr diff 1')
ev_env_split_string_equals_malformed=$(make_event '["PRAXIS_GH_JSON_BYPASS"]' "ok" "Bash" "sess-gh11" \
  'env --split-string="PRAXIS_GH_JSON_BYPASS=<redacted> gh issue list')
write_fixture "$DIR1C/bypass-events-$TODAY.jsonl" "$ev_env_split_string_malformed" "$ev_env_split_string_equals_malformed"

out1c=$(python3 "$CLI" --dir "$DIR1C" --days 7 2>&1)
rc1c=$?

if [ "$rc1c" -eq 0 ]; then
  assert_pass "malformed env split-string: exit code 0"
else
  assert_fail "malformed env split-string: exit code 0" "exited with code $rc1c; output: $out1c"
fi

if printf '%s\n' "$out1c" | grep -Fq "(unknown)"; then
  assert_pass "malformed env split-string: command family unknown"
else
  assert_fail "malformed env split-string: command family unknown" "expected unknown family; output: $out1c"
fi

# ---------------------------------------------------------------------------
# Test 3: Error event highlighting
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
# Test 4: --errors-only flag
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
# Test 5: Zero events — empty dir
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
# Test 6: Multi-day aggregation
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
# Test 7: --days flag — old events outside window excluded
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
# Test 8: --dir flag
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
# Test 9: Malformed JSONL lines — skipped without crash
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
# Test 10: Missing fields in record — no crash
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
# Test 11: Privacy — bypass var VALUES do not appear in output
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
# Test 12: Privacy — command family should not expose full path
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-review: command family path sanitization ==="

DIR10B="$TMP_DIR/t10b"
mkdir -p "$DIR10B"
abs_path="/Users/tester/private/internal/run-internal.sh"
ev_path=$(make_event '["PRAXIS_VERSION_BUMP_BYPASS"]' "ok" "Bash" "test-path" \
  "PRAXIS_VERSION_BUMP_BYPASS=<redacted> $abs_path --mode verify")
write_fixture "$DIR10B/bypass-events-$TODAY.jsonl" "$ev_path"

out10b=$(python3 "$CLI" --dir "$DIR10B" --days 1 2>&1)

if printf '%s\n' "$out10b" | grep -Fq "path:run-internal.sh"; then
  assert_pass "privacy: path-like command family normalized"
else
  assert_fail "privacy: path-like command family normalized" "sanitized path family missing; output: $out10b"
fi

if printf '%s\n' "$out10b" | grep -Fq "$abs_path"; then
  assert_fail "privacy: full command path not exposed" "absolute path appeared in output"
else
  assert_pass "privacy: full command path not exposed"
fi

# ---------------------------------------------------------------------------
# Test 13: Exit code 1 on --days < 1
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
# Test 14: Malformed bypass_env_vars type — non-list / non-string items
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-review: malformed bypass_env_vars type ==="

DIR12="$TMP_DIR/t12"
mkdir -p "$DIR12"
# Records whose bypass_env_vars is the wrong shape: a bare string, an int,
# an object, and a list mixing a valid string with non-string items.
# A well-formed event is appended so the report still has content to render.
python3 -c "
import json
from datetime import datetime, timezone
ts = datetime.now(tz=timezone.utc).isoformat()
def rec(bypass):
    return json.dumps({
        'timestamp': ts,
        'session_id': 'sess-malformed',
        'tool': 'Bash',
        'bypass_env_vars': bypass,
        'tool_input': 'X=<redacted> git status',
        'tool_result_status': 'error',
    })
print(rec('CLAUDE_HOOK_BYPASS_SCIOMC_GATE'))   # bare string, not a list
print(rec(42))                                  # int
print(rec({'k': 'v'}))                          # object
print(rec(['PRAXIS_GH_JSON_BYPASS', 7, None]))  # list with non-string items
" > "$DIR12/bypass-events-$TODAY.jsonl"

out12=$(python3 "$CLI" --dir "$DIR12" --days 1 2>&1)
rc12=$?

if [ "$rc12" -eq 0 ]; then
  assert_pass "malformed bypass_env_vars type: exit 0 (no crash)"
else
  assert_fail "malformed bypass_env_vars type: exit 0 (no crash)" "exited $rc12; output: $out12"
fi

# The one valid string item inside the mixed list must still be counted.
if echo "$out12" | grep -q "PRAXIS_GH_JSON_BYPASS"; then
  assert_pass "malformed bypass_env_vars type: valid string item retained"
else
  assert_fail "malformed bypass_env_vars type: valid string item retained" "valid item missing; output: $out12"
fi

# A bare-string value must NOT be iterated character-by-character: no
# single-character var names should leak into the report.
if printf '%s\n' "$out12" | grep -Eq "^\s+[A-Z] \([0-9]+\)$"; then
  assert_fail "malformed bypass_env_vars type: string not char-iterated" "single-char var leaked; output: $out12"
else
  assert_pass "malformed bypass_env_vars type: string not char-iterated"
fi

# ---------------------------------------------------------------------------
# Test 15: Per-Hook Aggregation section (#689)
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-review: per-hook aggregation section (issue #689) ==="

DIR13="$TMP_DIR/t13"
mkdir -p "$DIR13"
ev13_a=$(make_event '["CLAUDE_HOOK_BYPASS_SCIOMC_GATE"]' "ok" "Bash" "sess-a" \
  'CLAUDE_HOOK_BYPASS_SCIOMC_GATE=<redacted> git commit')
ev13_b=$(make_event '["CLAUDE_HOOK_BYPASS_SCIOMC_GATE"]' "error" "Bash" "sess-b" \
  'CLAUDE_HOOK_BYPASS_SCIOMC_GATE=<redacted> git push')
ev13_c=$(make_event '["PRAXIS_HOOK_BYPASS_WORKTREE_GATE"]' "ok" "Bash" "sess-c" \
  'PRAXIS_HOOK_BYPASS_WORKTREE_GATE=<redacted> gh pr create')
write_fixture "$DIR13/bypass-events-$TODAY.jsonl" "$ev13_a" "$ev13_b" "$ev13_c"

out13=$(python3 "$CLI" --dir "$DIR13" --days 1 2>&1)

# Per-Hook Aggregation section header must be present
if echo "$out13" | grep -q "Per-Hook Aggregation"; then
  assert_pass "per-hook aggregation: section header present"
else
  assert_fail "per-hook aggregation: section header present" "section missing; output: $out13"
fi

# Both hook families must appear as rows
if echo "$out13" | grep -q "SCIOMC_GATE" && echo "$out13" | grep -q "WORKTREE_GATE"; then
  assert_pass "per-hook aggregation: hook families as rows"
else
  assert_fail "per-hook aggregation: hook families as rows" "families missing; output: $out13"
fi

# Err% column must be present (header row check)
if echo "$out13" | grep -q "Err%"; then
  assert_pass "per-hook aggregation: Err% column header present"
else
  assert_fail "per-hook aggregation: Err% column header present" "Err% missing; output: $out13"
fi

# Sessions column must be present
if echo "$out13" | grep -q "Sessions"; then
  assert_pass "per-hook aggregation: Sessions column header present"
else
  assert_fail "per-hook aggregation: Sessions column header present" "Sessions missing; output: $out13"
fi

# Last Seen column must be present
if echo "$out13" | grep -q "Last Seen"; then
  assert_pass "per-hook aggregation: Last Seen column header present"
else
  assert_fail "per-hook aggregation: Last Seen column header present" "Last Seen missing; output: $out13"
fi

# SCIOMC_GATE has 1 error out of 2 bypass events → 50% error rate
if printf '%s\n' "$out13" | grep -q "SCIOMC_GATE" && printf '%s\n' "$out13" | grep "SCIOMC_GATE" | grep -q "50%"; then
  assert_pass "per-hook aggregation: SCIOMC_GATE error rate 50%"
else
  assert_fail "per-hook aggregation: SCIOMC_GATE error rate 50%" "rate missing; output: $out13"
fi

# WORKTREE_GATE has 0 errors → 0% error rate
if printf '%s\n' "$out13" | grep -q "WORKTREE_GATE" && printf '%s\n' "$out13" | grep "WORKTREE_GATE" | grep -q "0%"; then
  assert_pass "per-hook aggregation: WORKTREE_GATE error rate 0%"
else
  assert_fail "per-hook aggregation: WORKTREE_GATE error rate 0%" "rate missing; output: $out13"
fi

# Distinct sessions: SCIOMC_GATE has 2 sessions (sess-a, sess-b)
if printf '%s\n' "$out13" | grep "SCIOMC_GATE" | grep -qE "[[:space:]]2[[:space:]]"; then
  assert_pass "per-hook aggregation: SCIOMC_GATE distinct sessions = 2"
else
  assert_fail "per-hook aggregation: SCIOMC_GATE distinct sessions = 2" "session count wrong; output: $out13"
fi

# Limitation note about derived co-occurrence must appear
if echo "$out13" | grep -q "DERIVED"; then
  assert_pass "per-hook aggregation: derived co-occurrence limitation note present"
else
  assert_fail "per-hook aggregation: derived co-occurrence limitation note present" "limitation note missing; output: $out13"
fi

# ---------------------------------------------------------------------------
# Test 16: --per-hook FAMILY flag (#689)
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-review: --per-hook flag (issue #689) ==="

DIR14="$TMP_DIR/t14"
mkdir -p "$DIR14"
ev14_a=$(make_event '["CLAUDE_HOOK_BYPASS_SCIOMC_GATE"]' "ok" "Bash" "sess-ph-a" \
  'CLAUDE_HOOK_BYPASS_SCIOMC_GATE=<redacted> git log')
ev14_b=$(make_event '["CLAUDE_HOOK_BYPASS_SCIOMC_GATE"]' "error" "Bash" "sess-ph-b" \
  'CLAUDE_HOOK_BYPASS_SCIOMC_GATE=<redacted> git push')
ev14_c=$(make_event '["PRAXIS_HOOK_BYPASS_WORKTREE_GATE"]' "ok" "Bash" "sess-ph-c" \
  'PRAXIS_HOOK_BYPASS_WORKTREE_GATE=<redacted> gh pr list')
write_fixture "$DIR14/bypass-events-$TODAY.jsonl" "$ev14_a" "$ev14_b" "$ev14_c"

out14=$(python3 "$CLI" --dir "$DIR14" --days 1 --per-hook SCIOMC_GATE 2>&1)

# Per-Hook Detail section must appear with the correct family name
if echo "$out14" | grep -q "Per-Hook Detail: SCIOMC_GATE"; then
  assert_pass "--per-hook: detail section with correct family"
else
  assert_fail "--per-hook: detail section with correct family" "section missing; output: $out14"
fi

# Should show 2 events (only SCIOMC_GATE events, not WORKTREE_GATE)
if echo "$out14" | grep -q "\[2 event(s)\]"; then
  assert_pass "--per-hook: correct event count (2)"
else
  assert_fail "--per-hook: correct event count (2)" "event count wrong; output: $out14"
fi

# WORKTREE_GATE events must NOT appear in the output
if echo "$out14" | grep -q "WORKTREE_GATE"; then
  assert_fail "--per-hook: other families filtered out" "WORKTREE_GATE leaked into --per-hook output"
else
  assert_pass "--per-hook: other families filtered out"
fi

# Error events must be marked with ⚠ ERROR
if echo "$out14" | grep -q "ERROR"; then
  assert_pass "--per-hook: error events marked"
else
  assert_fail "--per-hook: error events marked" "ERROR marker missing; output: $out14"
fi

# Distinct sessions summary must appear
if echo "$out14" | grep -q "Distinct sessions"; then
  assert_pass "--per-hook: distinct sessions line present"
else
  assert_fail "--per-hook: distinct sessions line present" "sessions line missing; output: $out14"
fi

# Test --per-hook with non-existent family: graceful no-match message
out14b=$(python3 "$CLI" --dir "$DIR14" --days 1 --per-hook DOES_NOT_EXIST 2>&1)
rc14b=$?

if [ "$rc14b" -eq 0 ]; then
  assert_pass "--per-hook nonexistent family: exit code 0"
else
  assert_fail "--per-hook nonexistent family: exit code 0" "exited $rc14b; output: $out14b"
fi

if echo "$out14b" | grep -q "no events found for family"; then
  assert_pass "--per-hook nonexistent family: helpful no-match message"
else
  assert_fail "--per-hook nonexistent family: helpful no-match message" "message missing; output: $out14b"
fi

# Top Bypass Vars and Per-Hook Aggregation sections should NOT appear in --per-hook mode
if echo "$out14" | grep -q "Top Bypass Vars"; then
  assert_fail "--per-hook: top bypass vars section suppressed" "Top Bypass Vars appeared in --per-hook output"
else
  assert_pass "--per-hook: top bypass vars section suppressed"
fi

# ---------------------------------------------------------------------------
# Test 17: --per-hook FAMILY + --errors-only honors the error filter (#689, codex P2)
# DIR14 SCIOMC_GATE = ev14_a(ok) + ev14_b(error); --errors-only must drop the ok event.
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-review: --per-hook + --errors-only (issue #689) ==="
out17=$(python3 "$CLI" --dir "$DIR14" --days 1 --per-hook SCIOMC_GATE --errors-only 2>&1)

if echo "$out17" | grep -q "(errors only)"; then
  assert_pass "--per-hook+--errors-only: filter note shown in header"
else
  assert_fail "--per-hook+--errors-only: filter note shown in header" "note missing; output: $out17"
fi

if echo "$out17" | grep -q "\[1 event(s)\]"; then
  assert_pass "--per-hook+--errors-only: ok event filtered (1 of 2 shown)"
else
  assert_fail "--per-hook+--errors-only: ok event filtered (1 of 2 shown)" "expected 1 event; output: $out17"
fi

# Every listed event must be an error (no ok event leaks through the combination).
listed=$(echo "$out17" | grep -cE '^[[:space:]]+\[[0-9]+\] ' || true)
err_marked=$(echo "$out17" | grep -c "⚠ ERROR" || true)
if [ "$listed" = "1" ] && [ "$err_marked" = "1" ]; then
  assert_pass "--per-hook+--errors-only: every listed event is an error"
else
  assert_fail "--per-hook+--errors-only: every listed event is an error" "listed=$listed err=$err_marked; output: $out17"
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
