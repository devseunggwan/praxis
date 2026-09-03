#!/bin/bash
# tests/hooks/postuse-correction/test_bypass_telemetry.sh
#
# Coverage for the bypass-telemetry PostToolUse hook (issue #441 Phase 1).
#
# Tested surface variants (mandatory enumeration):
#   1.  CLAUDE_HOOK_BYPASS_* inline prefix -> 1 JSONL line with that var name
#   2.  PRAXIS_HOOK_BYPASS_* inline prefix -> logged
#   3.  Multiple bypass vars in one command -> all names in one record
#   4.  No bypass env -> NO line written, exit 0
#   5.  VAR=0 (falsey) -> NOT logged
#   6.  VAR= (empty, falsey) -> NOT logged
#   7.  Opt-out env (PRAXIS_BYPASS_TELEMETRY_DISABLE=1) -> no-op
#   8.  tool_result error -> tool_result_status:"error"
#   9.  Values never appear in JSONL (only names)
#  10.  tool_input truncated to <=200 chars
#  11.  Unwritable telemetry dir -> exit 0 silently (never block)
#  12.  Malformed JSON stdin -> exit 0
#  13.  PRAXIS_MOMENTUM_BYPASS inline -> logged (PRAXIS_*BYPASS* family)
#  14.  Non-bypass env (PRAXIS_MD_ESCAPE_SKIP) -> NOT logged
#  15.  Hook ALWAYS exits 0 (never blocks)
#  16.  os.environ backstop (bypass in env, not inline)
#  17.  All required fields present in record
#
# Run: bash tests/hooks/postuse-correction/test_bypass_telemetry.sh
# Exit: 0 on success, 1 on at least one failure

set +e

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HOOK="$REPO_ROOT/hooks/postuse-correction/bypass-telemetry/impl.py"

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

# Shared temp dir -- cleaned up on exit
TMP_DIR="$(mktemp -d)" || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
trap 'rm -rf "$TMP_DIR"' EXIT

# Scrub any bypass env vars that might be present in the developer's shell or CI.
# The hook scans os.environ, so inherited bypass vars would contaminate no-bypass
# and falsey-value tests.  Test 16 explicitly passes its own bypass var as needed.
# shellcheck disable=SC2046
unset $(env | grep -oE '^(CLAUDE_HOOK_|PRAXIS_)[A-Z0-9_]*BYPASS[A-Z0-9_]*' 2>/dev/null) 2>/dev/null || true
unset PRAXIS_BYPASS_TELEMETRY_DISABLE PRAXIS_BYPASS_TELEMETRY_FILE 2>/dev/null || true

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

make_payload() {
  # make_payload <tool_name> <command> [exit_code]
  local tool_name="$1"
  local command="$2"
  local exit_code="${3:-0}"
  python3 -c '
import json, sys
tool_name, command, exit_code = sys.argv[1], sys.argv[2], int(sys.argv[3])
print(json.dumps({
    "session_id": "test-session-123",
    "tool_name": tool_name,
    "tool_input": {"command": command},
    "tool_response": {"exit": exit_code, "output": "some output"},
}))' "$tool_name" "$command" "$exit_code"
}

# check_jsonl_field <file> <python_expr_that_returns_ok_or_err>
# python_expr receives 'r' as the parsed dict
check_jsonl_field() {
  local file="$1"
  local pyexpr="$2"
  python3 -c "
import json
with open('$file') as f:
    r = json.loads(f.readline())
result = $pyexpr
print('ok' if result else 'fail')
" 2>/dev/null
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
# Test 15: hook ALWAYS exits 0
# ---------------------------------------------------------------------------
echo "=== bypass-telemetry: always exits 0 ==="

_test_exit_zero() {
  local name="$1" payload="$2"
  local tel="$TMP_DIR/exit-zero-$RANDOM.jsonl"
  local rc
  PRAXIS_BYPASS_TELEMETRY_FILE="$tel" python3 "$HOOK" <<< "$payload" >/dev/null 2>&1
  rc=$?
  if [ "$rc" -ne 0 ]; then
    assert_fail "$name" "hook exited $rc (expected 0)"
  else
    assert_pass "$name"
  fi
}

_test_exit_zero "exit 0 on bypass present" \
  "$(make_payload Bash 'CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1 git commit')"
_test_exit_zero "exit 0 on no bypass" \
  "$(make_payload Bash 'git status')"
_test_exit_zero "exit 0 on malformed stdin" \
  "not-json"

# ---------------------------------------------------------------------------
# Test 1: CLAUDE_HOOK_BYPASS_* inline prefix -> 1 JSONL line
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-telemetry: inline CLAUDE_HOOK_BYPASS_* detection ==="

TEL1="$TMP_DIR/test1.jsonl"
payload1="$(make_payload Bash 'CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1 git commit -m fix')"
PRAXIS_BYPASS_TELEMETRY_FILE="$TEL1" python3 "$HOOK" <<< "$payload1" >/dev/null 2>&1

line_count=$(wc -l < "$TEL1" 2>/dev/null | tr -d ' ' || echo 0)
if [ "$line_count" -ne 1 ]; then
  assert_fail "CLAUDE_HOOK_BYPASS writes 1 line" "expected 1 line in JSONL, got $line_count"
else
  assert_pass "CLAUDE_HOOK_BYPASS writes 1 line"
fi

result=$(check_jsonl_field "$TEL1" "'CLAUDE_HOOK_BYPASS_SCIOMC_GATE' in r['bypass_env_vars']")
if [ "$result" = "ok" ]; then
  assert_pass "CLAUDE_HOOK_BYPASS_SCIOMC_GATE name in bypass_env_vars"
else
  assert_fail "CLAUDE_HOOK_BYPASS_SCIOMC_GATE name in bypass_env_vars" "var name missing from record"
fi

# ---------------------------------------------------------------------------
# Test 2: PRAXIS_HOOK_BYPASS_* inline prefix -> logged
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-telemetry: inline PRAXIS_HOOK_BYPASS_* detection ==="

TEL2="$TMP_DIR/test2.jsonl"
payload2="$(make_payload Bash 'PRAXIS_HOOK_BYPASS_WORKTREE_GATE=1 git push')"
PRAXIS_BYPASS_TELEMETRY_FILE="$TEL2" python3 "$HOOK" <<< "$payload2" >/dev/null 2>&1

result=$(check_jsonl_field "$TEL2" "'PRAXIS_HOOK_BYPASS_WORKTREE_GATE' in r['bypass_env_vars']")
if [ "$result" = "ok" ]; then
  assert_pass "PRAXIS_HOOK_BYPASS_WORKTREE_GATE detected inline"
else
  assert_fail "PRAXIS_HOOK_BYPASS_WORKTREE_GATE detected inline" "var name missing"
fi

# ---------------------------------------------------------------------------
# Test 3: Multiple bypass vars -> all names in one record
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-telemetry: multiple bypass vars -> one record, all names ==="

TEL3="$TMP_DIR/test3.jsonl"
payload3="$(make_payload Bash 'CLAUDE_HOOK_BYPASS_DUP_GATE=1 PRAXIS_MOMENTUM_BYPASS=1 git push')"
PRAXIS_BYPASS_TELEMETRY_FILE="$TEL3" python3 "$HOOK" <<< "$payload3" >/dev/null 2>&1

line_count3=$(wc -l < "$TEL3" 2>/dev/null | tr -d ' ' || echo 0)
if [ "$line_count3" -ne 1 ]; then
  assert_fail "multiple bypass vars -> one record" "expected 1 line, got $line_count3"
else
  assert_pass "multiple bypass vars -> one record"
fi

result=$(check_jsonl_field "$TEL3" "'CLAUDE_HOOK_BYPASS_DUP_GATE' in r['bypass_env_vars'] and 'PRAXIS_MOMENTUM_BYPASS' in r['bypass_env_vars']")
if [ "$result" = "ok" ]; then
  assert_pass "both bypass names in bypass_env_vars"
else
  assert_fail "both bypass names in bypass_env_vars" "one or both names missing"
fi

# ---------------------------------------------------------------------------
# Test 4: No bypass env -> NO line written, exit 0
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-telemetry: no bypass -> nothing written ==="

TEL4="$TMP_DIR/test4.jsonl"
payload4="$(make_payload Bash 'git status')"
PRAXIS_BYPASS_TELEMETRY_FILE="$TEL4" python3 "$HOOK" <<< "$payload4" >/dev/null 2>&1

if [ -f "$TEL4" ]; then
  line_count4=$(wc -l < "$TEL4" 2>/dev/null | tr -d ' ' || echo 0)
  if [ "$line_count4" -gt 0 ]; then
    assert_fail "no bypass -> nothing written" "file has $line_count4 lines"
  else
    assert_pass "no bypass -> file empty"
  fi
else
  assert_pass "no bypass -> file not created"
fi

# ---------------------------------------------------------------------------
# Test 5: VAR=0 (falsey) -> NOT logged
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-telemetry: VAR=0 falsey -> not logged ==="

TEL5="$TMP_DIR/test5.jsonl"
payload5="$(make_payload Bash 'CLAUDE_HOOK_BYPASS_SCIOMC_GATE=0 git commit')"
PRAXIS_BYPASS_TELEMETRY_FILE="$TEL5" python3 "$HOOK" <<< "$payload5" >/dev/null 2>&1

if [ -f "$TEL5" ]; then
  lc5=$(wc -l < "$TEL5" 2>/dev/null | tr -d ' ')
  if [ "${lc5:-0}" -gt 0 ]; then
    assert_fail "VAR=0 not logged" "file has content despite VAR=0"
  else
    assert_pass "VAR=0 not logged (empty file)"
  fi
else
  assert_pass "VAR=0 not logged"
fi

# ---------------------------------------------------------------------------
# Test 5b (issue #516 defect4): VAR=false / no / off -> NOT logged.
# Shell convention treats these as inactive; the old "non-empty and != 0" rule
# wrongly recorded them as active bypass events. Case-insensitive.
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-telemetry: VAR=false/no/off falsey -> not logged ==="

for falsy in false FALSE no No off OFF; do
  TEL5b="$TMP_DIR/test5b_${falsy}.jsonl"
  payload5b="$(make_payload Bash "CLAUDE_HOOK_BYPASS_SCIOMC_GATE=${falsy} git commit")"
  PRAXIS_BYPASS_TELEMETRY_FILE="$TEL5b" python3 "$HOOK" <<< "$payload5b" >/dev/null 2>&1
  if [ -f "$TEL5b" ]; then
    lc5b=$(wc -l < "$TEL5b" 2>/dev/null | tr -d ' ')
    if [ "${lc5b:-0}" -gt 0 ]; then
      assert_fail "VAR=${falsy} not logged" "file has content despite VAR=${falsy}"
    else
      assert_pass "VAR=${falsy} not logged (empty file)"
    fi
  else
    assert_pass "VAR=${falsy} not logged"
  fi
done

# Guard against over-correction: a genuinely-truthy value (e.g. `true`) must
# still be recorded.
TEL5c="$TMP_DIR/test5c.jsonl"
payload5c="$(make_payload Bash 'CLAUDE_HOOK_BYPASS_SCIOMC_GATE=true git commit')"
PRAXIS_BYPASS_TELEMETRY_FILE="$TEL5c" python3 "$HOOK" <<< "$payload5c" >/dev/null 2>&1
if [ -f "$TEL5c" ] && [ "$(wc -l < "$TEL5c" 2>/dev/null | tr -d ' ')" -gt 0 ]; then
  assert_pass "VAR=true still logged"
else
  assert_fail "VAR=true still logged" "truthy value was not recorded"
fi

# ---------------------------------------------------------------------------
# Test 6: VAR= (empty) -> NOT logged
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-telemetry: VAR= empty -> not logged ==="

TEL6="$TMP_DIR/test6.jsonl"
payload6="$(make_payload Bash 'CLAUDE_HOOK_BYPASS_SCIOMC_GATE= git commit')"
PRAXIS_BYPASS_TELEMETRY_FILE="$TEL6" python3 "$HOOK" <<< "$payload6" >/dev/null 2>&1

if [ -f "$TEL6" ]; then
  lc6=$(wc -l < "$TEL6" 2>/dev/null | tr -d ' ')
  if [ "${lc6:-0}" -gt 0 ]; then
    assert_fail "VAR= not logged" "file has content despite empty value"
  else
    assert_pass "VAR= not logged (empty file)"
  fi
else
  assert_pass "VAR= not logged"
fi

# ---------------------------------------------------------------------------
# Test 7: PRAXIS_BYPASS_TELEMETRY_DISABLE=1 -> no-op
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-telemetry: opt-out -> no-op ==="

TEL7="$TMP_DIR/test7.jsonl"
payload7="$(make_payload Bash 'CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1 git commit')"
PRAXIS_BYPASS_TELEMETRY_FILE="$TEL7" PRAXIS_BYPASS_TELEMETRY_DISABLE=1 \
  python3 "$HOOK" <<< "$payload7" >/dev/null 2>&1

if [ -f "$TEL7" ]; then
  lc7=$(wc -l < "$TEL7" 2>/dev/null | tr -d ' ')
  if [ "${lc7:-0}" -gt 0 ]; then
    assert_fail "opt-out -> no-op" "file has content despite disable=1"
  else
    assert_pass "opt-out -> no-op (empty file)"
  fi
else
  assert_pass "opt-out -> no-op"
fi

# ---------------------------------------------------------------------------
# Test 8: tool_result error -> tool_result_status:"error"
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-telemetry: tool error -> status=error ==="

TEL8="$TMP_DIR/test8.jsonl"
payload8="$(make_payload Bash 'CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1 git commit' 1)"
PRAXIS_BYPASS_TELEMETRY_FILE="$TEL8" python3 "$HOOK" <<< "$payload8" >/dev/null 2>&1

result=$(check_jsonl_field "$TEL8" "r['tool_result_status'] == 'error'")
if [ "$result" = "ok" ]; then
  assert_pass "exit_code=1 -> tool_result_status=error"
else
  assert_fail "exit_code=1 -> tool_result_status=error" "status not 'error'"
fi

TEL8b="$TMP_DIR/test8b.jsonl"
payload8b="$(make_payload Bash 'CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1 git status' 0)"
PRAXIS_BYPASS_TELEMETRY_FILE="$TEL8b" python3 "$HOOK" <<< "$payload8b" >/dev/null 2>&1

result=$(check_jsonl_field "$TEL8b" "r['tool_result_status'] == 'ok'")
if [ "$result" = "ok" ]; then
  assert_pass "exit_code=0 -> tool_result_status=ok"
else
  assert_fail "exit_code=0 -> tool_result_status=ok" "status not 'ok'"
fi

# ---------------------------------------------------------------------------
# Test 9: Bypass var VALUES never appear in bypass_env_vars list (only names)
# A secret value may appear in tool_input (it's part of the command) but must
# NOT appear inside the bypass_env_vars array — only the var name is stored there.
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-telemetry: bypass var values not in bypass_env_vars ==="

TEL9="$TMP_DIR/test9.jsonl"
secret_val="super-secret-token-9999"
payload9="$(python3 -c "
import json, sys
val = sys.argv[1]
print(json.dumps({
    'session_id': 'test-session',
    'tool_name': 'Bash',
    'tool_input': {'command': 'CLAUDE_HOOK_BYPASS_SCIOMC_GATE=' + val + ' git commit'},
    'tool_response': {'exit': 0},
}))" "$secret_val")"
PRAXIS_BYPASS_TELEMETRY_FILE="$TEL9" python3 "$HOOK" <<< "$payload9" >/dev/null 2>&1

# The secret value must NOT appear inside the bypass_env_vars list
result9=$(python3 -c "
import json, sys
val = sys.argv[1]
with open('$TEL9') as f:
    r = json.loads(f.readline())
# bypass_env_vars is a list of names only -- values must not be in it
leaked = any(val in str(v) for v in r['bypass_env_vars'])
print('fail_leaked' if leaked else 'ok')
" "$secret_val" 2>/dev/null)
if [ "$result9" = "ok" ]; then
  assert_pass "bypass var values not stored in bypass_env_vars"
else
  assert_fail "bypass var values not stored in bypass_env_vars" "secret value found in bypass_env_vars list"
fi

# Also verify tool_response content (output) is not in the record
# (tool_response.output="some output" from make_payload must not appear)
result9b=$(python3 -c "
import json
with open('$TEL9') as f:
    line = f.readline()
# 'some output' is the tool_response output -- must not appear in the record
print('fail' if 'some output' in line else 'ok')
" 2>/dev/null)
if [ "$result9b" = "ok" ]; then
  assert_pass "tool_response content not in JSONL record"
else
  assert_fail "tool_response content not in JSONL record" "tool_response output found in record"
fi

# Bypass var value must also NOT appear in tool_input (P2 fix: inline values redacted)
result9c=$(python3 -c "
import json, sys
val = sys.argv[1]
with open('$TEL9') as f:
    r = json.loads(f.readline())
# tool_input must contain '<redacted>' and must NOT contain the raw secret value
leaked = val in r.get('tool_input', '')
redacted = '<redacted>' in r.get('tool_input', '')
print('ok' if redacted and not leaked else 'fail_leaked=' + str(leaked) + ',redacted=' + str(redacted))
" "$secret_val" 2>/dev/null)
if [ "$result9c" = "ok" ]; then
  assert_pass "bypass var value redacted in tool_input"
else
  assert_fail "bypass var value redacted in tool_input" "check returned: $result9c"
fi

# Non-bypass leading env var values must ALSO be redacted in tool_input
# e.g. CLAUDE_HOOK_BYPASS_X=1 AWS_SECRET_ACCESS_KEY=secret cmd -> both values redacted
TEL9d="$TMP_DIR/test9d.jsonl"
non_bypass_secret="aws-secret-key-xyz-12345"
payload9d="$(python3 -c "
import json, sys
val = sys.argv[1]
print(json.dumps({
    'session_id': 'test-session',
    'tool_name': 'Bash',
    'tool_input': {'command': 'CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1 AWS_SECRET_ACCESS_KEY=' + val + ' aws s3 ls'},
    'tool_response': {'exit': 0},
}))" "$non_bypass_secret")"
PRAXIS_BYPASS_TELEMETRY_FILE="$TEL9d" python3 "$HOOK" <<< "$payload9d" >/dev/null 2>&1

result9d=$(python3 -c "
import json, sys
val = sys.argv[1]
with open('$TEL9d') as f:
    r = json.loads(f.readline())
leaked = val in r.get('tool_input', '')
print('ok' if not leaked else 'fail_leaked')
" "$non_bypass_secret" 2>/dev/null)
if [ "$result9d" = "ok" ]; then
  assert_pass "non-bypass inline env value also redacted in tool_input"
else
  assert_fail "non-bypass inline env value also redacted in tool_input" "non-bypass secret found in tool_input"
fi

# ---------------------------------------------------------------------------
# Test 10: tool_input truncated to <=200 chars
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-telemetry: tool_input truncated to 200 chars max ==="

TEL10="$TMP_DIR/test10.jsonl"
payload10="$(python3 -c "
import json
cmd = 'CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1 git commit -m ' + 'x' * 300
print(json.dumps({
    'session_id': 'test-session',
    'tool_name': 'Bash',
    'tool_input': {'command': cmd},
    'tool_response': {'exit': 0},
}))")"
PRAXIS_BYPASS_TELEMETRY_FILE="$TEL10" python3 "$HOOK" <<< "$payload10" >/dev/null 2>&1

_check10=$(python3 -c "
import json
with open('$TEL10') as f:
    r = json.loads(f.readline())
tlen = len(r['tool_input'])
print('ok' if tlen <= 200 else 'fail_' + str(tlen))
" 2>/dev/null)
if [ "$_check10" = "ok" ]; then
  assert_pass "tool_input truncated to 200 chars max"
else
  assert_fail "tool_input truncated to 200 chars max" "check returned: $_check10"
fi

# ---------------------------------------------------------------------------
# Test 11: Unwritable telemetry dir -> exit 0 silently
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-telemetry: unwritable dir -> silent exit 0 ==="

UNWRITABLE_DIR="$TMP_DIR/no-write"
mkdir -p "$UNWRITABLE_DIR"
chmod 000 "$UNWRITABLE_DIR"

TEL11="$UNWRITABLE_DIR/bypass.jsonl"
payload11="$(make_payload Bash 'CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1 git commit')"
rc11=0
PRAXIS_BYPASS_TELEMETRY_FILE="$TEL11" python3 "$HOOK" <<< "$payload11" >/dev/null 2>&1
rc11=$?

chmod 755 "$UNWRITABLE_DIR"  # restore so cleanup works

if [ "$rc11" -ne 0 ]; then
  assert_fail "unwritable dir -> exit 0" "hook exited $rc11"
else
  assert_pass "unwritable dir -> exit 0 silently"
fi

# ---------------------------------------------------------------------------
# Test 12: Malformed JSON stdin -> exit 0
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-telemetry: malformed stdin -> exit 0 ==="

rc12=0
PRAXIS_BYPASS_TELEMETRY_FILE="$TMP_DIR/t12.jsonl" python3 "$HOOK" <<< "not-json" >/dev/null 2>&1
rc12=$?

if [ "$rc12" -ne 0 ]; then
  assert_fail "malformed stdin -> exit 0" "hook exited $rc12"
else
  assert_pass "malformed stdin -> exit 0"
fi

rc12b=0
PRAXIS_BYPASS_TELEMETRY_FILE="$TMP_DIR/t12b.jsonl" python3 "$HOOK" <<< "" >/dev/null 2>&1
rc12b=$?

if [ "$rc12b" -ne 0 ]; then
  assert_fail "empty stdin -> exit 0" "hook exited $rc12b"
else
  assert_pass "empty stdin -> exit 0"
fi

# ---------------------------------------------------------------------------
# Test 13: PRAXIS_MOMENTUM_BYPASS inline -> logged (PRAXIS_*BYPASS* family)
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-telemetry: PRAXIS_MOMENTUM_BYPASS inline detection ==="

TEL13="$TMP_DIR/test13.jsonl"
payload13="$(make_payload Bash 'PRAXIS_MOMENTUM_BYPASS=1 gh pr merge 42 --squash')"
PRAXIS_BYPASS_TELEMETRY_FILE="$TEL13" python3 "$HOOK" <<< "$payload13" >/dev/null 2>&1

result=$(check_jsonl_field "$TEL13" "'PRAXIS_MOMENTUM_BYPASS' in r['bypass_env_vars']")
if [ "$result" = "ok" ]; then
  assert_pass "PRAXIS_MOMENTUM_BYPASS detected inline"
else
  assert_fail "PRAXIS_MOMENTUM_BYPASS detected inline" "var name missing"
fi

# ---------------------------------------------------------------------------
# Test 14: Non-bypass env (PRAXIS_MD_ESCAPE_SKIP) -> NOT logged
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-telemetry: non-bypass env -> not logged ==="

TEL14="$TMP_DIR/test14.jsonl"
payload14="$(make_payload Bash 'PRAXIS_MD_ESCAPE_SKIP=1 git commit')"
PRAXIS_BYPASS_TELEMETRY_FILE="$TEL14" python3 "$HOOK" <<< "$payload14" >/dev/null 2>&1

if [ -f "$TEL14" ]; then
  lc14=$(wc -l < "$TEL14" 2>/dev/null | tr -d ' ')
  if [ "${lc14:-0}" -gt 0 ]; then
    assert_fail "PRAXIS_MD_ESCAPE_SKIP not logged" "non-bypass var triggered logging"
  else
    assert_pass "PRAXIS_MD_ESCAPE_SKIP not logged (empty file)"
  fi
else
  assert_pass "PRAXIS_MD_ESCAPE_SKIP not logged"
fi

# ---------------------------------------------------------------------------
# Test 16: os.environ backstop (bypass in env, not inline)
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-telemetry: os.environ backstop ==="

TEL_ENV="$TMP_DIR/test-env.jsonl"
payload_env="$(make_payload Bash 'git commit -m fix')"
PRAXIS_BYPASS_TELEMETRY_FILE="$TEL_ENV" CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1 \
  python3 "$HOOK" <<< "$payload_env" >/dev/null 2>&1

result=$(check_jsonl_field "$TEL_ENV" "'CLAUDE_HOOK_BYPASS_SCIOMC_GATE' in r['bypass_env_vars']")
if [ "$result" = "ok" ]; then
  assert_pass "os.environ bypass var detected (not inline)"
else
  assert_fail "os.environ bypass var detected (not inline)" "env backstop did not fire"
fi

# ---------------------------------------------------------------------------
# Test 17: Record fields shape
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-telemetry: record fields shape ==="

TEL_SHAPE="$TMP_DIR/test-shape.jsonl"
payload_shape="$(make_payload Bash 'CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1 git commit')"
PRAXIS_BYPASS_TELEMETRY_FILE="$TEL_SHAPE" python3 "$HOOK" <<< "$payload_shape" >/dev/null 2>&1

result=$(python3 -c "
import json
with open('$TEL_SHAPE') as f:
    r = json.loads(f.readline())
required = ['timestamp', 'session_id', 'tool', 'bypass_env_vars', 'tool_input', 'tool_result_status']
missing = [k for k in required if k not in r]
ok = (
    not missing
    and r['session_id'] == 'test-session-123'
    and r['tool'] == 'Bash'
    and isinstance(r['bypass_env_vars'], list)
    and r['tool_result_status'] in ('ok', 'error')
)
print('ok' if ok else 'fail_missing=' + str(missing))
" 2>/dev/null)
if [ "$result" = "ok" ]; then
  assert_pass "all required fields present and typed correctly"
else
  assert_fail "all required fields present and typed correctly" "$result"
fi

# ---------------------------------------------------------------------------
# Test 18: FIFO telemetry target -> refused, hook returns promptly
# ---------------------------------------------------------------------------
echo ""
echo "=== bypass-telemetry: FIFO target does not stall the hook ==="

# A plain open("a") on a FIFO with no reader blocks forever; inside the
# PostToolUse(Bash) group that would stall every sibling until the host
# timeout. The fire ledger's guard refuses non-regular targets instead.
TEL_FIFO="$TMP_DIR/test-fifo.jsonl"
mkfifo "$TEL_FIFO"
payload_fifo="$(make_payload Bash 'CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1 git commit')"
PRAXIS_BYPASS_TELEMETRY_FILE="$TEL_FIFO" python3 "$HOOK" <<< "$payload_fifo" >/dev/null 2>&1 &
fifo_pid=$!
for _ in $(seq 1 50); do
  kill -0 "$fifo_pid" 2>/dev/null || break
  sleep 0.1
done
if kill -0 "$fifo_pid" 2>/dev/null; then
  kill "$fifo_pid" 2>/dev/null
  assert_fail "FIFO target: hook returns within 5 s" "still blocked on the FIFO after 5 s"
else
  wait "$fifo_pid"
  fifo_rc=$?
  if [ "$fifo_rc" -eq 0 ]; then
    assert_pass "FIFO target: hook returns within 5 s with exit 0"
  else
    assert_fail "FIFO target: hook returns within 5 s with exit 0" "exit $fifo_rc"
  fi
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
