#!/bin/bash
# tests/hooks/postuse-correction/test_askuserquestion_loop_signal.sh
#
# Coverage for the askuserquestion-loop-signal PostToolUse hook (issue #740).
#
# Tested surface variants (mandatory enumeration):
#   1.  tool_name == AskUserQuestion -> 1 RICH fire-ledger record written
#   2.  tool_name != AskUserQuestion (e.g. Bash)  -> nothing written
#   3.  Missing session_id -> nothing written (cannot attribute)
#   4.  Empty-string session_id -> nothing written
#   5.  Opt-out (PRAXIS_FIRE_TELEMETRY_DISABLE=1) -> no-op
#   6.  Malformed JSON stdin -> exit 0, nothing written
#   7.  Empty stdin -> exit 0, nothing written
#   8.  Hook ALWAYS exits 0 (never blocks)
#   9.  2 calls in the same session -> 2 RICH records, same session_id
#   10. RICH record field shape (timestamp/session_id/tool/hook/role/decision/granularity)
#   11. @fail_open's automatic COARSE duplicate is also present (session_id="")
#       alongside the RICH record -- documents the dedup contract the reader
#       side (bypass-review's compute_reclarification_loop_counts) relies on.
#
# Run: bash tests/hooks/postuse-correction/test_askuserquestion_loop_signal.sh
# Exit: 0 on success, 1 on at least one failure

set +e

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HOOK="$REPO_ROOT/hooks/postuse-correction/askuserquestion-loop-signal/impl.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  # stdout marker so run-tests.sh folds this skip into strict mode (#1170).
  echo "PRAXIS_SUBSKIP: python3 $0"
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

make_payload() {
  # make_payload <tool_name> <session_id>
  local tool_name="$1"
  local session_id="$2"
  python3 -c '
import json, sys
tool_name, session_id = sys.argv[1], sys.argv[2]
payload = {"tool_name": tool_name, "tool_input": {"questions": [
    {"header": "h", "question": "q?", "options": [{"label": "a"}, {"label": "b"}]}
]}}
if session_id != "__omit__":
    payload["session_id"] = session_id
print(json.dumps(payload))' "$tool_name" "$session_id"
}

rich_line_count() {
  local file="$1"
  # No early return on a missing override: the rows are in the counter
  # siblings now, and the override itself may never be created.
  # No stderr suppression / error fallback here on purpose: a json.loads()
  # or file-read failure means malformed telemetry, which must fail the
  # test loudly rather than silently masquerade as a count of 0
  # (CodeRabbit PR #743 finding).
  # A `pass` is counted rather than appended (issue #1238), so the rich rows
  # this hook writes live in the counter siblings and each carries a `count`.
  python3 -c "
import glob, json, os
n = 0
paths = ['$file'] + sorted(glob.glob(os.path.join(os.path.dirname('$file'), 'fire-counts-*.jsonl')))
for path in paths:
    if not os.path.exists(path):
        continue
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get('granularity') == 'rich':
                c = r.get('count')
                n += c if isinstance(c, int) and not isinstance(c, bool) else 1
print(n)
"
}

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

# ---------------------------------------------------------------------------
# Test 8: hook ALWAYS exits 0
# ---------------------------------------------------------------------------
echo "=== askuserquestion-loop-signal: always exits 0 ==="

_test_exit_zero() {
  local name="$1" payload="$2"
  local tel="$TMP_DIR/exit-zero-$RANDOM/ledger.jsonl"
  mkdir -p "$(dirname "$tel")"
  local rc
  if [ -z "$payload" ]; then
    # True zero-byte stdin (CodeRabbit PR #743 finding: a `<<< ""`
    # here-string still writes a trailing newline, not real EOF).
    PRAXIS_FIRE_TELEMETRY_FILE="$tel" python3 "$HOOK" < /dev/null >/dev/null 2>&1
  else
    PRAXIS_FIRE_TELEMETRY_FILE="$tel" python3 "$HOOK" <<< "$payload" >/dev/null 2>&1
  fi
  rc=$?
  if [ "$rc" -ne 0 ]; then
    assert_fail "$name" "hook exited $rc (expected 0)"
  else
    assert_pass "$name"
  fi
}

_test_exit_zero "exit 0 on AskUserQuestion" "$(make_payload AskUserQuestion sess-exit0)"
_test_exit_zero "exit 0 on non-AskUserQuestion tool" "$(make_payload Bash sess-exit0)"
_test_exit_zero "exit 0 on malformed stdin" "not-json"
_test_exit_zero "exit 0 on empty stdin" ""

# ---------------------------------------------------------------------------
# Test 1: tool_name == AskUserQuestion -> 1 RICH record
# ---------------------------------------------------------------------------
echo ""
echo "=== askuserquestion-loop-signal: AskUserQuestion call -> 1 RICH record ==="

mkdir -p "$TMP_DIR/test1"
TEL1="$TMP_DIR/test1/ledger.jsonl"
payload1="$(make_payload AskUserQuestion sess-1)"
PRAXIS_FIRE_TELEMETRY_FILE="$TEL1" python3 "$HOOK" <<< "$payload1" >/dev/null 2>&1

count1="$(rich_line_count "$TEL1")"
if [ "$count1" -eq 1 ]; then
  assert_pass "AskUserQuestion call writes 1 RICH record"
else
  assert_fail "AskUserQuestion call writes 1 RICH record" "expected 1 rich line, got $count1"
fi

# ---------------------------------------------------------------------------
# Test 2: tool_name != AskUserQuestion -> nothing written
# ---------------------------------------------------------------------------
echo ""
echo "=== askuserquestion-loop-signal: non-AskUserQuestion tool -> nothing written ==="

mkdir -p "$TMP_DIR/test2"
TEL2="$TMP_DIR/test2/ledger.jsonl"
payload2="$(make_payload Bash sess-2)"
PRAXIS_FIRE_TELEMETRY_FILE="$TEL2" python3 "$HOOK" <<< "$payload2" >/dev/null 2>&1

# Note: @fail_open still appends its own COARSE record for this hook name
# regardless of tool_name (see hooks/_lib/_hook_runtime.py) -- the file may
# exist, but it must contain NO RICH record (that's the hook's own logic).
count2="$(rich_line_count "$TEL2")"
if [ "$count2" -eq 0 ]; then
  assert_pass "non-AskUserQuestion -> no RICH record"
else
  assert_fail "non-AskUserQuestion -> no RICH record" "expected 0 rich lines, got $count2"
fi

# ---------------------------------------------------------------------------
# Test 3: missing session_id -> nothing written
# ---------------------------------------------------------------------------
echo ""
echo "=== askuserquestion-loop-signal: missing session_id -> nothing written ==="

mkdir -p "$TMP_DIR/test3"
TEL3="$TMP_DIR/test3/ledger.jsonl"
payload3="$(make_payload AskUserQuestion __omit__)"
PRAXIS_FIRE_TELEMETRY_FILE="$TEL3" python3 "$HOOK" <<< "$payload3" >/dev/null 2>&1

# Note: @fail_open's COARSE record is still written regardless (see test 2's
# comment) -- only the RICH record depends on session_id being present.
count3="$(rich_line_count "$TEL3")"
if [ "$count3" -eq 0 ]; then
  assert_pass "missing session_id -> no RICH record"
else
  assert_fail "missing session_id -> no RICH record" "expected 0 rich lines, got $count3"
fi

# ---------------------------------------------------------------------------
# Test 4: empty-string session_id -> nothing written
# ---------------------------------------------------------------------------
echo ""
echo "=== askuserquestion-loop-signal: empty session_id -> nothing written ==="

mkdir -p "$TMP_DIR/test4"
TEL4="$TMP_DIR/test4/ledger.jsonl"
payload4="$(make_payload AskUserQuestion "")"
PRAXIS_FIRE_TELEMETRY_FILE="$TEL4" python3 "$HOOK" <<< "$payload4" >/dev/null 2>&1

count4="$(rich_line_count "$TEL4")"
if [ "$count4" -eq 0 ]; then
  assert_pass "empty session_id -> no RICH record"
else
  assert_fail "empty session_id -> no RICH record" "expected 0 rich lines, got $count4"
fi

# ---------------------------------------------------------------------------
# Test 5: opt-out -> no-op
# ---------------------------------------------------------------------------
echo ""
echo "=== askuserquestion-loop-signal: opt-out -> no-op ==="

mkdir -p "$TMP_DIR/test5"
TEL5="$TMP_DIR/test5/ledger.jsonl"
payload5="$(make_payload AskUserQuestion sess-5)"
PRAXIS_FIRE_TELEMETRY_FILE="$TEL5" PRAXIS_FIRE_TELEMETRY_DISABLE=1 \
  python3 "$HOOK" <<< "$payload5" >/dev/null 2>&1

if [ -f "$TEL5" ]; then
  lc5=$(wc -l < "$TEL5" 2>/dev/null | tr -d ' ')
  if [ "${lc5:-0}" -gt 0 ]; then
    assert_fail "opt-out -> no-op" "file has content despite disable=1"
  else
    assert_pass "opt-out -> no-op (empty file)"
  fi
else
  assert_pass "opt-out -> no-op"
fi

# ---------------------------------------------------------------------------
# Test 9: 2 calls in the same session -> 2 RICH records, same session_id
# ---------------------------------------------------------------------------
echo ""
echo "=== askuserquestion-loop-signal: 2 calls same session -> 2 RICH records ==="

mkdir -p "$TMP_DIR/test9"
TEL9="$TMP_DIR/test9/ledger.jsonl"
payload9="$(make_payload AskUserQuestion sess-9)"
PRAXIS_FIRE_TELEMETRY_FILE="$TEL9" python3 "$HOOK" <<< "$payload9" >/dev/null 2>&1
PRAXIS_FIRE_TELEMETRY_FILE="$TEL9" python3 "$HOOK" <<< "$payload9" >/dev/null 2>&1

count9="$(rich_line_count "$TEL9")"
if [ "$count9" -eq 2 ]; then
  assert_pass "2 AskUserQuestion calls -> 2 RICH records"
else
  assert_fail "2 AskUserQuestion calls -> 2 RICH records" "expected 2 rich lines, got $count9"
fi

same_session9=$(python3 -c "
import glob, json, os
sids = set()
for path in ['$TEL9'] + sorted(glob.glob(os.path.join(os.path.dirname('$TEL9'), 'fire-counts-*.jsonl'))):
    if not os.path.exists(path):
        continue
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get('granularity') == 'rich':
                sids.add(r.get('session_id'))
print('ok' if sids == {'sess-9'} else 'fail_' + str(sids))
" 2>/dev/null)
if [ "$same_session9" = "ok" ]; then
  assert_pass "both RICH records carry the same session_id"
else
  assert_fail "both RICH records carry the same session_id" "$same_session9"
fi

# ---------------------------------------------------------------------------
# Test 10: RICH record field shape
# ---------------------------------------------------------------------------
echo ""
echo "=== askuserquestion-loop-signal: RICH record field shape ==="

mkdir -p "$TMP_DIR/test10"
TEL10="$TMP_DIR/test10/ledger.jsonl"
payload10="$(make_payload AskUserQuestion sess-10)"
PRAXIS_FIRE_TELEMETRY_FILE="$TEL10" python3 "$HOOK" <<< "$payload10" >/dev/null 2>&1

result10=$(python3 -c "
import glob, json, os
rich = None
for path in ['$TEL10'] + sorted(glob.glob(os.path.join(os.path.dirname('$TEL10'), 'fire-counts-*.jsonl'))):
    if not os.path.exists(path):
        continue
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get('granularity') == 'rich':
                rich = r
required = ['timestamp', 'session_id', 'tool', 'hook', 'role', 'decision', 'granularity']
missing = [k for k in required if rich is None or k not in rich]
ok = (
    rich is not None
    and not missing
    and rich['session_id'] == 'sess-10'
    and rich['tool'] == 'AskUserQuestion'
    and rich['hook'] == 'askuserquestion-loop-signal'
    and rich['role'] == 'postuse-correction'
    and rich['decision'] == 'pass'
)
print('ok' if ok else 'fail_missing=' + str(missing))
" 2>/dev/null)
if [ "$result10" = "ok" ]; then
  assert_pass "RICH record has all required fields, correctly typed"
else
  assert_fail "RICH record has all required fields, correctly typed" "$result10"
fi

# ---------------------------------------------------------------------------
# Test 11: @fail_open's automatic COARSE duplicate is also present
# ---------------------------------------------------------------------------
echo ""
echo "=== askuserquestion-loop-signal: coarse duplicate alongside RICH record ==="

mkdir -p "$TMP_DIR/test11"
TEL11="$TMP_DIR/test11/ledger.jsonl"
payload11="$(make_payload AskUserQuestion sess-11)"
PRAXIS_FIRE_TELEMETRY_FILE="$TEL11" python3 "$HOOK" <<< "$payload11" >/dev/null 2>&1

result11=$(python3 -c "
import glob, json, os
gran = []
for path in ['$TEL11'] + sorted(glob.glob(os.path.join(os.path.dirname('$TEL11'), 'fire-counts-*.jsonl'))):
    if not os.path.exists(path):
        continue
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            gran.append(json.loads(line).get('granularity'))
print('ok' if sorted(gran) == ['coarse', 'rich'] else 'fail_' + str(gran))
" 2>/dev/null)
if [ "$result11" = "ok" ]; then
  assert_pass "1 RICH + 1 COARSE record per call (fail_open dedup contract)"
else
  assert_fail "1 RICH + 1 COARSE record per call (fail_open dedup contract)" "$result11"
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
