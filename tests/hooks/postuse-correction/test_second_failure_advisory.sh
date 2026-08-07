#!/usr/bin/env bash
# tests/hooks/postuse-correction/test_second_failure_advisory.sh
#
# Coverage for PostToolUse same-tool/same-signature retry advisory (issue #944).
#
# Scenarios:
#   1) 첫 실패 1회 -> advisory 없음
#   2) 동일 시그니처 2회째 -> advisory 출력
#   3) 동일 tool이더라도 다른 시그니처 -> advisory 없음
#   4) 다른 tool + 동일 텍스트 -> tool 분기 때문에 advisory 없음(각 tool은 독립 카운트)
#   5) 경로/해시/타임스탬프 가변치 차이 -> 정규화 후 동일 시그니처로 2회째 advisory
#   6) 성공 응답은 무시
#   7) malformed / 빈 stdin은 fail-open
#   8) session_id 없음은 fail-open
#
# Run:
#   bash tests/hooks/postuse-correction/test_second_failure_advisory.sh

set +e

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HOOK="$REPO_ROOT/hooks/postuse-correction/second-failure-advisory/impl.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "SKIP: python3 not available" >&2
  exit 0
fi

PASS=0
FAIL=0
FAILED_NAMES=()

TMP_DIR="$(mktemp -d)" || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
trap 'rm -rf "$TMP_DIR"' EXIT

make_payload() {
  # make_payload <tool_name> <session_id|__omit__> <exit_code> <mode>
  # mode:
  #   default => exit + stderr
  #   output-only => output only
  #   success => output-only with exit 0
  local tool_name="$1"
  local session_id="$2"
  local exit_code="$3"
  local mode="${4:-default}"
  python3 - <<PY "$tool_name" "$session_id" "$exit_code" "$mode"
import json
import sys

tool_name, session_id, exit_code, mode = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]

if mode == "success":
    payload = {
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_input": {"file_path": "/tmp/project/src/main.py"},
        "tool_response": {"exit": exit_code, "output": "done"},
    }
elif mode == "output-only":
    payload = {
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_input": {"file_path": "/tmp/project/src/main.py"},
        "tool_response": {"output": "unexpected connection refused"},
    }
else:
    payload = {
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_input": {"file_path": "/tmp/project/src/main.py"},
        "tool_response": {
            "exit": exit_code,
            "stderr": "read /tmp/workdir/item_1.log failed request 2026-08-07T10:00:00Z hash=0a1b2c3d4e5f6a7b8",
        },
    }

if session_id == "__omit__":
    payload.pop("session_id", None)

print(json.dumps(payload))
PY
}

pipe_hook() {
  local payload="$1"
  local state_file="$2"
  shift 2

  (
    for kv in "$@"; do
      # shellcheck disable=SC2163  # kv holds a literal KEY=VALUE pair
      export "$kv"
    done
    # shellcheck disable=SC2030,SC2031  # subshell-local env var export
    export PRAXIS_SECOND_FAILURE_ADVISORY_FILE="$state_file"
    printf '%s' "$payload" | python3 "$HOOK"
  )
}

assert_match() {
  local pattern="$1" text="$2"
  if echo "$text" | grep -Fq "$pattern"; then
    return 0
  fi
  return 1
}

assert_pass() {
  local name="$1"
  PASS=$((PASS + 1))
  echo "  OK  $name"
}

assert_fail() {
  local name="$1" msg="$2"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("$name")
  echo "FAIL  [$name] $msg"
}

# ---------------------------------------------------------------------------
# Case 1: first failure has no advisory
# ---------------------------------------------------------------------------
echo "=== case 1: first failure => no advisory ==="
STATE1="$TMP_DIR/c1.json"
payload1="$(make_payload Bash sess-944 1)"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$payload1" "$STATE1" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; then
  assert_pass "1) first failure emits no advisory"
else
  assert_fail "1) first failure emits no advisory" "rc=$rc out=[$out] err=[$err]"
fi

# ---------------------------------------------------------------------------
# Case 2: second same failure emits advisory
# ---------------------------------------------------------------------------
echo "=== case 2: second same failure => advisory ==="
payload2="$(make_payload Bash sess-944 1)"
STATE2="$TMP_DIR/c2.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$payload1" "$STATE2" >/dev/null 2>/dev/null
pipe_hook "$payload2" "$STATE2" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -n "$err" ] && assert_match "2회째" "$err"; then
  assert_pass "2) second same failure emits advisory"
else
  assert_fail "2) second same failure emits advisory" "rc=$rc out=[$out] err=[$err]"
fi

# ---------------------------------------------------------------------------
# Case 3: same tool but different signature => no advisory
# ---------------------------------------------------------------------------
echo "=== case 3: same tool, different signature => no advisory ==="
STATE3="$TMP_DIR/c3.json"
payload3="$(make_payload Bash sess-944-diff 1 default)"
payload3b="$(make_payload Bash sess-944-diff 1 output-only)"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$payload3" "$STATE3" >/dev/null 2>/dev/null
pipe_hook "$payload3b" "$STATE3" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; then
  assert_pass "3) different signature (same tool) => no advisory"
else
  assert_fail "3) different signature (same tool) => no advisory" "rc=$rc out=[$out] err=[$err]"
fi

# ---------------------------------------------------------------------------
# Case 4: different tool with same text => independent counters
# ---------------------------------------------------------------------------
echo "=== case 4: different tool => independent counters ==="
STATE4="$TMP_DIR/c4.json"
payload4="$(make_payload Bash sess-944-tool 1)"
payload4b="$(make_payload Edit sess-944-tool 1)"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$payload4" "$STATE4" >/dev/null 2>/dev/null
pipe_hook "$payload4b" "$STATE4" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; then
  assert_pass "4) different tool => independent counting"
else
  assert_fail "4) different tool => independent counting" "rc=$rc out=[$out] err=[$err]"
fi

# ---------------------------------------------------------------------------
# Case 5: path/hash/timestamp normalizing keeps signature same on second failure
# ---------------------------------------------------------------------------
echo "=== case 5: variable-only differences normalized ==="
STATE5="$TMP_DIR/c5.json"
payload5="$(make_payload Bash sess-944-var 1)"
payload5b="$(python3 - <<'PY'
import json
print(json.dumps({
    'session_id':'sess-944-var',
    'tool_name':'Bash',
    'tool_input':{'file_path':'/tmp/other/main.py'},
    'tool_response':{
      'exit':1,
      'stderr':'read /var/log/item_99.log failed request 2026-08-07T10:05:00Z hash=1b2c3d4e5f6a7b80',
    },
}))
PY
)"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$payload5" "$STATE5" >/dev/null 2>/dev/null
pipe_hook "$payload5b" "$STATE5" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -n "$err" ] && assert_match "2회째" "$err"; then
  assert_pass "5) path/hash/timestamp normalization preserves signature"
else
  assert_fail "5) path/hash/timestamp normalization preserves signature" "rc=$rc out=[$out] err=[$err]"
fi

# ---------------------------------------------------------------------------
# Case 6: success path -> no advisory
# ---------------------------------------------------------------------------
echo "=== case 6: success -> no advisory ==="
STATE6="$TMP_DIR/c6.json"
payload6="$(make_payload Bash sess-944-success 0 success)"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$payload6" "$STATE6" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; then
  assert_pass "6) success response emits no advisory"
else
  assert_fail "6) success response emits no advisory" "rc=$rc out=[$out] err=[$err]"
fi

# ---------------------------------------------------------------------------
# Case 7: malformed / empty input / missing session_id -> fail-open
# ---------------------------------------------------------------------------
echo "=== case 7: malformed and empty stdin and missing session_id -> fail-open ==="

STATE7="$TMP_DIR/c7.json"
out_file="$(mktemp)" err_file="$(mktemp)"
PRAXIS_SECOND_FAILURE_ADVISORY_FILE="$STATE7" python3 "$HOOK" <<< not-json >/dev/null 2>"$err_file"
rc=$?
if [ "$rc" -ne 0 ]; then
  assert_fail "7-1) malformed json -> exit 0" "rc=$rc"
else
  echo "  OK  7-1) malformed json -> exit 0"
  PASS=$((PASS + 1))
fi
if [ -s "$err_file" ]; then
  assert_fail "7-1) malformed json -> no stderr" "stderr=[$(cat "$err_file")]"
fi
rm -f "$err_file"
rm -f "$out_file"

STATE7_EMPTY="$TMP_DIR/c7-empty.err"
err_file="$(mktemp)"
(
  # shellcheck disable=SC2030,SC2031  # subshell-local env var export
  export PRAXIS_SECOND_FAILURE_ADVISORY_FILE="$STATE7"
  python3 "$HOOK" < /dev/null >/dev/null 2>"$err_file"
)
rc_empty=$?
if [ "$rc_empty" -ne 0 ]; then
  assert_fail "7-2) empty stdin -> exit 0" "rc=$rc_empty"
else
  echo "  OK  7-2) empty stdin -> exit 0"
  PASS=$((PASS + 1))
fi
if [ -s "$err_file" ]; then
  assert_fail "7-2) empty stdin -> no stderr" "stderr=[$(cat "$err_file")]"
fi
rm -f "$err_file"

echo "=== case 7-3: missing session_id -> fail-open ==="
STATE7b="$TMP_DIR/c7b.json"
payload7c="$(make_payload Bash __omit__ 1)"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$payload7c" "$STATE7b" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; then
  assert_pass "7-3) missing session_id -> no advisory"
else
  assert_fail "7-3) missing session_id -> no advisory" "rc=$rc out=[$out] err=[$err]"
fi

echo
if [ "$FAIL" -eq 0 ]; then
  echo "PASS: $PASS"
  exit 0
fi

echo "FAIL: $FAIL"
for name in "${FAILED_NAMES[@]}"; do
  echo "  - $name"
done
exit 1
