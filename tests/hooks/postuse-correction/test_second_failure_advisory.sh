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
#   7) malformed / 빈 stdin / session_id 없음은 fail-open
#   8) 사이에 성공·다른 실패가 끼어도 같은 쌍의 2회째에 advisory
#   9) 상태 저장 실패 시 advisory 무음
#  10) stdout/output만 있는 성공 응답은 반복돼도 무음
#  11) 실패 텍스트의 Reference 경로가 advisory에 포함
#  12) 3회째 이상도 계속 advisory (회차 번호 포함) — issue #1012
#  12b) 첫 회는 여전히 무음 (반대 방향 control)
#  13) interrupted 응답은 실패로 판정
#  14) exit-0 Bash 호출(stderr가 harness cwd-reset 안내뿐) -> advisory 없음 (issue #1042)
#  15) 진짜 실패(interrupted:true) 반복은 harness noise가 섞여도 여전히 advisory (positive control, issue #1096)
#  16) stderr가 noise뿐인 서로 다른 실패는 서로 다른 signature (issue #1042)
#  17) tool_name이 Bash가 아니면 noise strip 자체를 건너뛰어 진짜 실패로 판정 (PR #1071 리뷰)
#  18) 성공한 Bash 호출이 stderr에 진행 로그를 써도(git fetch 등, exit 0) 반복돼도 advisory 없음 (issue #1096)
#  19) tool_response 가 dict 가 아니라 문자열로 오는 실패(실제 harness 형상) 처리 (issue #1265)
#      a) 동일 문자열 실패 2회 -> advisory  b) 서로 다른 문자열 실패 -> 무음
#      c) is_error:false 인 oversized-output 안내 -> 무음(must-fail)  d) User rejected tool use -> advisory
#      e) 공백뿐인 문자열 -> 무음  f) hook block 문자열 반복 -> 회차 포함 advisory
#      g) 출력 없는 `Error: Exit code 1` 이 서로 다른 명령에서 나오면 무음(signature 충돌 방지)
#      h) 같은 명령이 같은 형태로 2회 실패하면 여전히 advisory (g의 대조군)
#      i) 경로만 다른 두 명령(`cat /tmp/a` / `cat /tmp/b`) -> 무음
#         (정규화가 구분자를 흡수하던 결함; 명령은 별도 digest 로 키에 들어간다)
#      j) 같은 경로 명령 2회 -> advisory (i 의 대조군)
#      k) 같은 명령 + 다른 exit code -> 무음
#      l) tool_input 에 command 자체가 없으면 기존 키 그대로 -> 2회째 advisory
#      m) 공백뿐인 command 는 command 부재와 같은 키
#      n) 앞뒤 공백만 다른 같은 명령 -> 여전히 advisory / 내부 공백이 다르면 무음
#      o) 4096 경계: 경계 안에서 다르면 무음 / 경계 밖에서만 다르면 같은 키
#      p) 유니코드만 다른 명령 -> 무음
#      q) bare 가 아닌 실패는 정규화가 그대로 동작 (normalizer 약화 안 됨 대조군)
#      r) command 키가 없는 non-Bash·non-MCP 도구는 동작 불변
#      s) 셸에서 유의미한 내부 공백 차이(개행 / 탭 / 따옴표 안 연속 공백 /
#         NBSP)는 서로 다른 키 -> 무음, 같은 명령 2회는 여전히 advisory (대조군)
#      t) MCP 도구의 문자열은 harness 가 쓴 것만 실패 — `Error: ` 접두사만 있는
#         *성공* 텍스트는 무음, hook-error envelope·거부 문장은 여전히 advisory
#  20) PostToolUseFailure 이벤트 (issue #1337)
#      a) Bash `Exit code 1\nnpm ERR! ...` 2회 -> advisory, hookEventName 은 수신 이벤트
#      b) is_interrupt:true -> 무음, state 파일 없음
#      c) 같은 tool_use_id 가 PostToolUse 후 PostToolUseFailure 로 오면 1회만 계수 (역순도)
#      d) non-Bash MCP 도구의 error 문자열 2회 -> advisory (allowlist 없음)
#      e) PostToolUse 성공 payload 는 hook_event_name 이 있어도 여전히 무음 (대조군)
#      f) PostToolUse 문자열 실패 + PostToolUseFailure 가 같은 실패를 서로 다른 id 로
#         전달하면 하나의 pair 키 -> 2회째 advisory (`Error: ` envelope 정규화)
#      g) error 가 문자열이 아니면 fail-open 무음
#      h) 출력 없는 `Exit code 1` 은 명령이 다르면 무음 / 같으면 advisory (command digest)
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
  # stdout marker so run-tests.sh folds this skip into strict mode (#1170).
  echo "PRAXIS_SUBSKIP: python3 $0"
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

if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] && assert_match "2회째" "$out" \
    && assert_match "Failure #2" "$out"; then
  assert_pass "2) second same failure emits advisory (bilingual — #1160)"
else
  assert_fail "2) second same failure emits advisory (bilingual — #1160)" "rc=$rc out=[$out] err=[$err]"
fi

# ---------------------------------------------------------------------------
# Case 3: same tool but different signature => no advisory
# ---------------------------------------------------------------------------
echo "=== case 3: same tool, different signature => no advisory ==="
STATE3="$TMP_DIR/c3.json"
payload3="$(make_payload Bash sess-944-diff 1 default)"
# A genuinely different error signature — not an output-only payload, which
# would be classified as a success and so would pass this case for the wrong
# reason.
payload3b="$(python3 - <<'PY'
import json
print(json.dumps({
    'session_id':'sess-944-diff',
    'tool_name':'Bash',
    'tool_input':{'file_path':'/tmp/project/src/main.py'},
    'tool_response':{'exit':1,'stderr':'permission denied while opening socket'},
}))
PY
)"
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

if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] && assert_match "2회째" "$out"; then
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

# ---------------------------------------------------------------------------
# Case 8: the counter is session-cumulative, not consecutive (issue #944)
# ---------------------------------------------------------------------------
echo "=== case 8: success/other failure in between still counts ==="
STATE12="$TMP_DIR/c12.json"
pipe_hook "$(make_payload Bash sess-944-gap 1)" "$STATE12" >/dev/null 2>/dev/null
pipe_hook "$(make_payload Bash sess-944-gap 0 success)" "$STATE12" >/dev/null 2>/dev/null
pipe_hook '{"session_id":"sess-944-gap","tool_name":"Bash","tool_input":{},"tool_response":{"exit":1,"stderr":"a totally different failure"}}' \
  "$STATE12" >/dev/null 2>/dev/null
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(make_payload Bash sess-944-gap 1)" "$STATE12" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$err" ] && assert_match "2회째" "$out"; then
  assert_pass "8) non-consecutive repeat still advises on the 2nd occurrence"
else
  assert_fail "8) non-consecutive repeat still advises on the 2nd occurrence" "rc=$rc out=[$out] err=[$err]"
fi

# ---------------------------------------------------------------------------
# Case 9: a failed state write suppresses the advisory (persist-before-advise)
# ---------------------------------------------------------------------------
echo "=== case 9: unwritable state path => silent ==="
# The first failure is counted normally, then the scratch file `_save_state`
# writes before renaming is replaced by a directory: open() for write fails with
# EISDIR for every user including root, while the state file itself stays
# readable so the count is still loaded. Mode bits would not do this — container
# CI commonly runs as root, which ignores them.
STATE9="$TMP_DIR/c9.json"
pipe_hook "$(make_payload Bash sess-944-ro 1)" "$STATE9" >/dev/null 2>/dev/null
mkdir -p "$STATE9.tmp"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(make_payload Bash sess-944-ro 1)" "$STATE9" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; then
  assert_pass "9) unwritable state suppresses the advisory"
else
  assert_fail "9) unwritable state suppresses the advisory" "rc=$rc out=[$out] err=[$err]"
fi

# ---------------------------------------------------------------------------
# Case 10: stdout/output-only responses are successes, not failures
# ---------------------------------------------------------------------------
echo "=== case 10: repeated stdout-only success => silent ==="
STATE14="$TMP_DIR/c14.json"
SUCCESS_PAYLOAD='{"session_id":"sess-944-ok","tool_name":"Bash","tool_input":{},"tool_response":{"stdout":"hello world"}}'
pipe_hook "$SUCCESS_PAYLOAD" "$STATE14" >/dev/null 2>/dev/null
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$SUCCESS_PAYLOAD" "$STATE14" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; then
  assert_pass "10) repeated stdout-only success stays silent"
else
  assert_fail "10) repeated stdout-only success stays silent" "rc=$rc out=[$out] err=[$err]"
fi

# ---------------------------------------------------------------------------
# Case 11: the advisory carries the Reference parsed out of the failure text
# ---------------------------------------------------------------------------
echo "=== case 11: Reference extracted from failure text ==="
STATE15="$TMP_DIR/c15.json"
REF_PAYLOAD='{"session_id":"sess-944-ref","tool_name":"Bash","tool_input":{},"tool_response":{"exit":1,"stderr":"BLOCKED: gate fired.\nReference: hooks/preflight-gate/foo/spec.md"}}'
pipe_hook "$REF_PAYLOAD" "$STATE15" >/dev/null 2>/dev/null
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$REF_PAYLOAD" "$STATE15" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$err" ] \
  && assert_match "hooks/preflight-gate/foo/spec.md" "$out" \
  && assert_match "재진술" "$out"; then
  assert_pass "11) advisory carries the failure-text Reference and restate order"
else
  assert_fail "11) advisory carries the failure-text Reference and restate order" "rc=$rc out=[$out] err=[$err]"
fi

# ---------------------------------------------------------------------------
# Case 12: occurrences 3..N keep advising, numbered (issue #1012)
#
# The old contract stopped at the `prior_count == 1` boundary, so a session that
# kept replaying the same failure went silent exactly where the loop was worst.
# Own state file (`c12b`) — case 8 above already writes `c12.json`, and sharing
# it made the occurrence number here depend on an unrelated case.
# ---------------------------------------------------------------------------
echo "=== case 12: occurrences 3..N keep advising, numbered ==="
STATE12B="$TMP_DIR/c12b.json"
payload12="$(make_payload Bash sess-944-third 1)"
pipe_hook "$payload12" "$STATE12B" >/dev/null 2>/dev/null   # 1st — silent
pipe_hook "$payload12" "$STATE12B" >/dev/null 2>/dev/null   # 2nd — advises

for occurrence in 3 4 5; do
  out_file="$(mktemp)" err_file="$(mktemp)"
  pipe_hook "$payload12" "$STATE12B" >"$out_file" 2>"$err_file"
  rc=$?
  out=$(cat "$out_file"); err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] \
    && assert_match "${occurrence}회째" "$out"; then
    assert_pass "12) occurrence ${occurrence} advises and names its count"
  else
    assert_fail "12) occurrence ${occurrence} advises and names its count" \
      "rc=$rc out=[$out] err=[$err]"
  fi
done

# Control for the same boundary in the other direction: occurrence 1 of a FRESH
# pair must still be silent. Without it, "always advise" would pass case 12 too.
echo "=== case 12b: first occurrence still silent (control) ==="
STATE12C="$TMP_DIR/c12c.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(make_payload Bash sess-944-first-only 1)" "$STATE12C" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; then
  assert_pass "12b) first occurrence stays silent"
else
  assert_fail "12b) first occurrence stays silent" "rc=$rc out=[$out] err=[$err]"
fi

# ---------------------------------------------------------------------------
# Case 13: an interrupted response counts as a failure
# ---------------------------------------------------------------------------
echo "=== case 13: interrupted => failure ==="
STATE13="$TMP_DIR/c13.json"
INTERRUPTED='{"session_id":"sess-944-int","tool_name":"Bash","tool_input":{},"tool_response":{"interrupted":true,"stderr":"user cancelled the run"}}'
pipe_hook "$INTERRUPTED" "$STATE13" >/dev/null 2>/dev/null
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$INTERRUPTED" "$STATE13" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$err" ] && assert_match "2회째" "$out"; then
  assert_pass "13) interrupted response counts as a failure"
else
  assert_fail "13) interrupted response counts as a failure" "rc=$rc out=[$out] err=[$err]"
fi

# ---------------------------------------------------------------------------
# Case 14: exit-0 Bash calls whose only `stderr` is the harness's own
# cwd-reset notice must never advise (issue #1042).
#
# `{stdout, stderr, interrupted, isImage, noOutputExpected}` — no `exit`, no
# `isError` — is the real `tool_response` shape for a Bash call in this
# harness (verified against live session `toolUseResult` transcripts); every
# one of those calls carries `"stderr": "\nShell cwd was reset to <cwd>"`
# regardless of success. Before the fix, five structurally unrelated exit-0
# commands in a row (a heredoc, `grep -l`, `head`, a heredoc `cat >`, another
# heredoc) fired the advisory 4 times running, all under the identical
# signature `ede370078f51` — reproduced byte-for-byte against the pre-fix
# code in this exact payload shape.
# ---------------------------------------------------------------------------
echo "=== case 14: exit-0 Bash call, harness-noise-only stderr => no advisory ==="
noise_payload() {
  # noise_payload <session_id> <stdout>
  python3 - "$1" "$2" <<'PY'
import json, sys
session_id, stdout = sys.argv[1], sys.argv[2]
print(json.dumps({
    "session_id": session_id,
    "tool_name": "Bash",
    "tool_input": {"command": "irrelevant"},
    "tool_response": {
        "stdout": stdout,
        "stderr": "\nShell cwd was reset to /Users/x/projects/praxis",
        "interrupted": False,
        "isImage": False,
        "noOutputExpected": False,
    },
}))
PY
}

STATE16="$TMP_DIR/c16.json"
out_file="$(mktemp)" err_file="$(mktemp)"
for stdout_text in "OK\n" "README.md\n" "line1\nline2\n" "-rw-r--r-- 1 f\n" "index updated\n"; do
  pipe_hook "$(noise_payload sess-1042-exit0 "$stdout_text")" "$STATE16" >"$out_file" 2>"$err_file"
done
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ] && [ ! -s "$STATE16" ]; then
  assert_pass "14) five exit-0 calls with only harness-noise stderr stay silent"
else
  assert_fail "14) five exit-0 calls with only harness-noise stderr stay silent" \
    "rc=$rc out=[$out] err=[$err] state=[$(cat "$STATE16" 2>/dev/null)]"
fi

# ---------------------------------------------------------------------------
# Case 15: regression / positive control — two genuine repeats of the SAME
# real failure pattern must still advise on the 2nd occurrence, even with
# the harness-noise line appended to `stderr` (the defect-1 fix must not
# disable the hook outright).
#
# Issue #1096: a real Bash `tool_response` carries no exit/isError, so stderr
# text alone can no longer classify a Bash call as failed (a success-with-stderr
# command is byte-for-byte indistinguishable by its stderr). This genuine
# failure is therefore marked the only way a real Bash failure can be — with
# `interrupted: True` (a killed/timed-out run). The harness-noise line is still
# appended to `stderr`, so this remains the positive control that noise
# stripping in signature derivation does not break a genuine repeat.
# ---------------------------------------------------------------------------
echo "=== case 15: genuine repeated failure (harness-noise stderr suffix) still advises ==="
real_failure_payload() {
  # real_failure_payload <session_id>
  python3 - "$1" <<'PY'
import json, sys
session_id = sys.argv[1]
print(json.dumps({
    "session_id": session_id,
    "tool_name": "Bash",
    "tool_input": {"command": "python3 script.py"},
    "tool_response": {
        "stdout": "",
        "stderr": "TypeError: unsupported operand type(s)\nShell cwd was reset to /Users/x/projects/praxis",
        "interrupted": True,
        "isImage": False,
        "noOutputExpected": False,
    },
}))
PY
}

STATE17="$TMP_DIR/c17.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(real_failure_payload sess-1042-real-fail)" "$STATE17" >/dev/null 2>/dev/null
pipe_hook "$(real_failure_payload sess-1042-real-fail)" "$STATE17" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] && assert_match "2회째" "$out"; then
  assert_pass "15) genuine repeated failure still advises past harness-noise stripping"
else
  assert_fail "15) genuine repeated failure still advises past harness-noise stripping" \
    "rc=$rc out=[$out] err=[$err]"
fi

# ---------------------------------------------------------------------------
# Case 16: two structurally unrelated FAILING commands whose `stderr` is
# ONLY the harness noise (distinguishing text lives in `stdout` instead, as
# with an `interrupted:true` timeout) must get DIFFERENT signatures and not
# accumulate into one counter (issue #1042 defect 2).
# ---------------------------------------------------------------------------
echo "=== case 16: unrelated failures with noise-only stderr get distinct signatures ==="
interrupted_noise_payload() {
  # interrupted_noise_payload <session_id> <stdout>
  python3 - "$1" "$2" <<'PY'
import json, sys
session_id, stdout = sys.argv[1], sys.argv[2]
print(json.dumps({
    "session_id": session_id,
    "tool_name": "Bash",
    "tool_input": {"command": "irrelevant"},
    "tool_response": {
        "stdout": stdout,
        "stderr": "\nShell cwd was reset to /Users/x/projects/praxis",
        "interrupted": True,
        "isImage": False,
        "noOutputExpected": False,
    },
}))
PY
}

STATE18="$TMP_DIR/c18.json"
pipe_hook "$(interrupted_noise_payload sess-1042-sig "waiting on lock A ...\n")" "$STATE18" >/dev/null 2>/dev/null
pipe_hook "$(interrupted_noise_payload sess-1042-sig "waiting on lock B, unrelated command ...\n")" "$STATE18" >/dev/null 2>/dev/null

distinct_sigs="$(python3 -c "
import json
state = json.load(open('$STATE18'))
print(len(state.get('failures', {})))
")"

if [ "$distinct_sigs" = "2" ]; then
  assert_pass "16) unrelated noise-only-stderr failures get distinct signatures"
else
  assert_fail "16) unrelated noise-only-stderr failures get distinct signatures" \
    "expected 2 distinct signature keys, got $distinct_sigs: $(cat "$STATE18")"
fi

# ---------------------------------------------------------------------------
# Case 17: a NON-Bash tool whose `stderr` legitimately matches the harness
# cwd-reset line shape must still be treated as a failure — the strip is
# gated on `tool_name == "Bash"` (PR #1071 review finding). Without the
# gate, this genuine error text would be deleted and misread as a success.
# ---------------------------------------------------------------------------
echo "=== case 17: non-Bash tool with harness-noise-shaped stderr is still a failure ==="
non_bash_noise_shaped_payload() {
  # non_bash_noise_shaped_payload <session_id>
  python3 - "$1" <<'PY'
import json, sys
session_id = sys.argv[1]
print(json.dumps({
    "session_id": session_id,
    "tool_name": "Read",
    "tool_input": {"file_path": "/tmp/whatever"},
    "tool_response": {
        "stderr": "Shell cwd was reset to /Users/x/projects/praxis",
    },
}))
PY
}

STATE19="$TMP_DIR/c19.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(non_bash_noise_shaped_payload sess-1042-non-bash)" "$STATE19" >/dev/null 2>/dev/null
pipe_hook "$(non_bash_noise_shaped_payload sess-1042-non-bash)" "$STATE19" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] && assert_match "2회째" "$out"; then
  assert_pass "17) non-Bash tool with harness-noise-shaped stderr still advises"
else
  assert_fail "17) non-Bash tool with harness-noise-shaped stderr still advises" \
    "rc=$rc out=[$out] err=[$err]"
fi

# ---------------------------------------------------------------------------
# Case 18: a *successful* Bash command that writes progress to `stderr`
# (git fetch/clone/checkout, curl meters, deprecation warnings; all exit 0)
# must NEVER advise, even when the identical call repeats (issue #1096).
#
# Real Bash `tool_response` is `{stdout, stderr, interrupted, isImage,
# noOutputExpected}` — no `exit`, no `isError` (verified against live session
# `toolUseResult` transcripts). #1042 stripped only the harness cwd-reset line;
# any OTHER stderr content (here, `git fetch` branch-progress output) still fell
# through the back-compat `stderr`-non-empty check and was mislabelled a
# failure, so the second identical `git fetch origin` injected a false
# "2회째 실패" advisory. The fix stops treating stderr text alone as failure for
# Bash: these succeed, so nothing is counted and nothing is emitted — the state
# file is never even created.
# ---------------------------------------------------------------------------
echo "=== case 18: repeated success-with-stderr Bash call => no advisory (issue #1096) ==="
success_with_stderr_payload() {
  # success_with_stderr_payload <session_id> <command> <stderr>
  python3 - "$1" "$2" "$3" <<'PY'
import json, sys
session_id, command, stderr = sys.argv[1], sys.argv[2], sys.argv[3]
print(json.dumps({
    "session_id": session_id,
    "tool_name": "Bash",
    "tool_input": {"command": command},
    "tool_response": {
        "stdout": "",
        # Real progress output on stderr + the harness cwd-reset suffix. No
        # `exit`, no `isError` — exactly the real exit-0 Bash payload shape.
        "stderr": stderr + "\nShell cwd was reset to /Users/x/projects/praxis",
        "interrupted": False,
        "isImage": False,
        "noOutputExpected": False,
    },
}))
PY
}

STATE20="$TMP_DIR/c20.json"
GIT_FETCH_STDERR="From github.com:acme/repo
   abc1234..def5678  main       -> origin/main"
out_file="$(mktemp)" err_file="$(mktemp)"
# Two identical successful `git fetch origin` calls — the exact "second
# identical call" scenario the false advisory fired on.
pipe_hook "$(success_with_stderr_payload sess-1096-git 'git fetch origin' "$GIT_FETCH_STDERR")" "$STATE20" >/dev/null 2>/dev/null
pipe_hook "$(success_with_stderr_payload sess-1096-git 'git fetch origin' "$GIT_FETCH_STDERR")" "$STATE20" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ] && [ ! -s "$STATE20" ]; then
  assert_pass "18a) two identical git-fetch successes with stderr stay silent"
else
  assert_fail "18a) two identical git-fetch successes with stderr stay silent" \
    "rc=$rc out=[$out] err=[$err] state=[$(cat "$STATE20" 2>/dev/null)]"
fi

# A second flavour of success-with-stderr: a deprecation warning on exit 0,
# repeated. Same expectation — no advisory, no state.
STATE21="$TMP_DIR/c21.json"
DEPRECATION_STDERR="DeprecationWarning: 'foo' is deprecated and will be removed"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(success_with_stderr_payload sess-1096-dep 'python3 build.py' "$DEPRECATION_STDERR")" "$STATE21" >/dev/null 2>/dev/null
pipe_hook "$(success_with_stderr_payload sess-1096-dep 'python3 build.py' "$DEPRECATION_STDERR")" "$STATE21" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ] && [ ! -s "$STATE21" ]; then
  assert_pass "18b) repeated deprecation-warning-on-stderr success stays silent"
else
  assert_fail "18b) repeated deprecation-warning-on-stderr success stays silent" \
    "rc=$rc out=[$out] err=[$err] state=[$(cat "$STATE21" 2>/dev/null)]"
fi

# ---------------------------------------------------------------------------
# Case 19: string-shaped `tool_response` (issue #1265).
#
# A FAILED tool call does not reach this hook as a dict at all — it arrives as a
# plain string, so `_is_failed`'s `isinstance(tool_response, dict)` guard
# rejected it before any failure marker was ever consulted. Both roads were
# closed: the dict road by the `tool_name == "Bash"` guard (#1096), the string
# road by the isinstance check. The hook fired 135,030 times and recorded
# `decision: pass` on every one.
#
# Every fixture below is a VERBATIM string captured from a real session
# transcript (`toolUseResult` for a `Bash`/MCP `tool_use`), not composed:
# a census of 10,467 unique Bash results across 120 transcripts found 388
# string payloads, all carrying `tool_result.is_error == True`, and zero
# successful Bash call that arrives as a string.
# ---------------------------------------------------------------------------
echo "=== case 19: string-shaped failed tool_response (issue #1265) ==="
string_payload() {
  # string_payload <session_id> <tool_name> <tool_response-string> [command]
  python3 - "$1" "$2" "$3" "${4:-true}" <<'PY'
import json, sys
session_id, tool_name, text, command = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
print(json.dumps({
    "session_id": session_id,
    "tool_name": tool_name,
    "tool_input": {"command": command},
    # The whole payload — a bare string, exactly as the harness delivers it.
    "tool_response": text,
}))
PY
}

# Captured verbatim: a shell parse error, exit 1.
STR_FAIL_A=$'Error: Exit code 1\n(eval):1: == not found'
# Captured verbatim: a different failure — a 2-minute timeout, exit 143.
STR_FAIL_B=$'Error: Exit code 143\nCommand timed out after 2m 0s'
# Captured verbatim: a PreToolUse hook block (the family #1265 exists to catch).
STR_BLOCKED='Error: Blocked: sleep 60 followed by: echo done. To wait for a condition, use Monitor with an until-loop (e.g. `until <check>; do sleep 2; done`).'
# Captured verbatim: a user-denied call. No `Error: ` prefix at all, so a
# prefix-only matcher would miss it.
STR_REJECTED='User rejected tool use'
# Captured verbatim from an MCP tool_result whose `is_error` was FALSE: the
# harness's oversized-output notice for a SUCCESSFUL call whose result was
# spilled to a file. It opens with `Error: ` and is not a failure — the one
# must-fail case in the string surface. (Absolute path shortened; the notice
# text itself is unmodified.)
STR_OVERSIZED='Error: result (104,870 characters across 2,772 lines) exceeds maximum allowed tokens. Output has been saved to /tmp/x/out.txt'

# 19a) positive: the same real string failure twice => advisory on the 2nd.
STATE22="$TMP_DIR/c22.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(string_payload sess-1265-a Bash "$STR_FAIL_A")" "$STATE22" >/dev/null 2>/dev/null
pipe_hook "$(string_payload sess-1265-a Bash "$STR_FAIL_A")" "$STATE22" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] && assert_match "2회째" "$out"; then
  assert_pass "19a) repeated string-shaped failure advises on the 2nd occurrence"
else
  assert_fail "19a) repeated string-shaped failure advises on the 2nd occurrence" \
    "rc=$rc out=[$out] err=[$err]"
fi

# 19b) negative control: two DIFFERENT string failures must not advise — this
# hook is about repetition, not about failure. Without distinct signatures both
# would collapse onto one pair and the 2nd would fire (issue #1042 defect 2).
STATE23="$TMP_DIR/c23.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(string_payload sess-1265-b Bash "$STR_FAIL_A")" "$STATE23" >/dev/null 2>/dev/null
pipe_hook "$(string_payload sess-1265-b Bash "$STR_FAIL_B")" "$STATE23" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; then
  assert_pass "19b) two different string failures stay silent (distinct signatures)"
else
  assert_fail "19b) two different string failures stay silent (distinct signatures)" \
    "rc=$rc out=[$out] err=[$err]"
fi

# 19c) must-fail case: the oversized-output notice is a SUCCESS (`is_error:
# false`) that happens to open with `Error: `. Repeating it must stay silent and
# must not even create state — otherwise every large successful result reads as
# a failure.
STATE24="$TMP_DIR/c24.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(string_payload sess-1265-c mcp__x__query "$STR_OVERSIZED")" "$STATE24" >/dev/null 2>/dev/null
pipe_hook "$(string_payload sess-1265-c mcp__x__query "$STR_OVERSIZED")" "$STATE24" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ] && [ ! -s "$STATE24" ]; then
  assert_pass "19c) repeated oversized-output notice (is_error:false) stays silent"
else
  assert_fail "19c) repeated oversized-output notice (is_error:false) stays silent" \
    "rc=$rc out=[$out] err=[$err] state=[$(cat "$STATE24" 2>/dev/null)]"
fi

# 19d) `User rejected tool use` carries no `Error: ` prefix, so it is matched by
# name. A repeated denial is exactly the blind-retry loop this hook exists for.
STATE25="$TMP_DIR/c25.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(string_payload sess-1265-d Bash "$STR_REJECTED")" "$STATE25" >/dev/null 2>/dev/null
pipe_hook "$(string_payload sess-1265-d Bash "$STR_REJECTED")" "$STATE25" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] && assert_match "2회째" "$out"; then
  assert_pass "19d) repeated user-rejection string advises"
else
  assert_fail "19d) repeated user-rejection string advises" "rc=$rc out=[$out] err=[$err]"
fi

# 19e) an empty / whitespace-only string is not failure evidence -> fail-open.
STATE26="$TMP_DIR/c26.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(string_payload sess-1265-e Bash "   ")" "$STATE26" >/dev/null 2>/dev/null
pipe_hook "$(string_payload sess-1265-e Bash "   ")" "$STATE26" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ] && [ ! -s "$STATE26" ]; then
  assert_pass "19e) whitespace-only string payload stays silent"
else
  assert_fail "19e) whitespace-only string payload stays silent" \
    "rc=$rc out=[$out] err=[$err] state=[$(cat "$STATE26" 2>/dev/null)]"
fi

# 19f) a repeated PreToolUse hook block — the motivating family. The advisory
# must carry the running count so the loop gets a stronger signal, not silence.
STATE27="$TMP_DIR/c27.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(string_payload sess-1265-f Bash "$STR_BLOCKED")" "$STATE27" >/dev/null 2>/dev/null
pipe_hook "$(string_payload sess-1265-f Bash "$STR_BLOCKED")" "$STATE27" >/dev/null 2>/dev/null
pipe_hook "$(string_payload sess-1265-f Bash "$STR_BLOCKED")" "$STATE27" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] && assert_match "3회째" "$out"; then
  assert_pass "19f) repeated hook-block string keeps advising with its count"
else
  assert_fail "19f) repeated hook-block string keeps advising with its count" \
    "rc=$rc out=[$out] err=[$err]"
fi

# 19g/19h) the bare `Error: Exit code 1` — no output under it — is byte-identical
# whatever command died (6 of the 388 observed). Two DIFFERENT commands failing
# that way must stay silent; the control that makes that meaningful is 19h,
# where the SAME command failing twice still advises. Without 19h, "collision
# fixed" is indistinguishable from "this shape stopped firing entirely".
STR_BARE_EXIT='Error: Exit code 1'

STATE28="$TMP_DIR/c28.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(string_payload sess-1265-g Bash "$STR_BARE_EXIT" 'grep -q needle haystack.txt')" "$STATE28" >/dev/null 2>/dev/null
pipe_hook "$(string_payload sess-1265-g Bash "$STR_BARE_EXIT" 'git diff --quiet HEAD~1')" "$STATE28" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; then
  assert_pass "19g) two different commands with bare exit-code text stay silent"
else
  assert_fail "19g) two different commands with bare exit-code text stay silent" \
    "rc=$rc out=[$out] err=[$err]"
fi

STATE29="$TMP_DIR/c29.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(string_payload sess-1265-h Bash "$STR_BARE_EXIT" 'grep -q needle haystack.txt')" "$STATE29" >/dev/null 2>/dev/null
pipe_hook "$(string_payload sess-1265-h Bash "$STR_BARE_EXIT" 'grep -q needle haystack.txt')" "$STATE29" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] && assert_match "2회째" "$out"; then
  assert_pass "19h) the same command repeating a bare exit-code failure advises"
else
  assert_fail "19h) the same command repeating a bare exit-code failure advises" \
    "rc=$rc out=[$out] err=[$err]"
fi

# ---------------------------------------------------------------------------
# Cases 19i..19q: the command discriminator must survive `_normalize_signature`.
#
# 19g/19h above pass with the command appended to the signature TEXT, because
# `grep -q needle haystack.txt` and `git diff --quiet HEAD~1` differ in tokens
# the normalizer leaves alone. Two commands that differ ONLY in a path do not:
# `_PATH_RE` turns both into `cat <path>`, so they collapsed onto one pair and
# the second, unrelated failure fired a false advisory. The command is therefore
# hashed separately (`_command_discriminator`) and mixed into the key beside the
# normalized signature, so normalization cannot reach it.
# ---------------------------------------------------------------------------
echo "=== case 19i: bare exit code, commands differing only by path => silent ==="
STATE30="$TMP_DIR/c30.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(string_payload sess-1265-i Bash "$STR_BARE_EXIT" 'cat /tmp/a')" "$STATE30" >/dev/null 2>/dev/null
pipe_hook "$(string_payload sess-1265-i Bash "$STR_BARE_EXIT" 'cat /tmp/b')" "$STATE30" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; then
  assert_pass "19i) path-only-differing commands stay silent (normalizer cannot eat the discriminator)"
else
  assert_fail "19i) path-only-differing commands stay silent (normalizer cannot eat the discriminator)" \
    "rc=$rc out=[$out] err=[$err]"
fi

echo "=== case 19j: same path-carrying command twice => advisory (control for 19i) ==="
STATE31="$TMP_DIR/c31.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(string_payload sess-1265-j Bash "$STR_BARE_EXIT" 'cat /tmp/a')" "$STATE31" >/dev/null 2>/dev/null
pipe_hook "$(string_payload sess-1265-j Bash "$STR_BARE_EXIT" 'cat /tmp/a')" "$STATE31" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] && assert_match "2회째" "$out"; then
  assert_pass "19j) the same path-carrying command repeating still advises"
else
  assert_fail "19j) the same path-carrying command repeating still advises" \
    "rc=$rc out=[$out] err=[$err]"
fi

echo "=== case 19k: same command, different exit codes => silent ==="
STATE32="$TMP_DIR/c32.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(string_payload sess-1265-k Bash 'Error: Exit code 1' 'cat /tmp/a')" "$STATE32" >/dev/null 2>/dev/null
pipe_hook "$(string_payload sess-1265-k Bash 'Error: Exit code 2' 'cat /tmp/a')" "$STATE32" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; then
  assert_pass "19k) different exit codes on the same command stay silent"
else
  assert_fail "19k) different exit codes on the same command stay silent" "rc=$rc out=[$out] err=[$err]"
fi

echo "=== case 19l: bare exit code with NO command in tool_input => unchanged (merges) ==="
# Nothing in the payload can tell the two apart, so the pre-existing behaviour
# stands: they share a pair and the 2nd advises. Asserted so the fallback path is
# pinned rather than left to chance.
no_command_payload() {
  python3 - "$1" <<'PY'
import json, sys
print(json.dumps({
    "session_id": sys.argv[1],
    "tool_name": "Bash",
    "tool_input": {},
    "tool_response": "Error: Exit code 1",
}))
PY
}
STATE33="$TMP_DIR/c33.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(no_command_payload sess-1265-l)" "$STATE33" >/dev/null 2>/dev/null
pipe_hook "$(no_command_payload sess-1265-l)" "$STATE33" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] && assert_match "2회째" "$out"; then
  assert_pass "19l) absent command falls back to the undiscriminated key"
else
  assert_fail "19l) absent command falls back to the undiscriminated key" "rc=$rc out=[$out] err=[$err]"
fi

echo "=== case 19m: whitespace-only command behaves like an absent one ==="
STATE34="$TMP_DIR/c34.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(string_payload sess-1265-m Bash "$STR_BARE_EXIT" '   ')" "$STATE34" >/dev/null 2>/dev/null
pipe_hook "$(no_command_payload sess-1265-m)" "$STATE34" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] && assert_match "2회째" "$out"; then
  assert_pass "19m) whitespace-only command shares the absent-command key"
else
  assert_fail "19m) whitespace-only command shares the absent-command key" "rc=$rc out=[$out] err=[$err]"
fi

echo "=== case 19n: leading/trailing whitespace only => same key ==="
# Whitespace outside the command is shell-insignificant, so stripping it keeps
# the key. This is the half of the old "collapse all whitespace" behaviour that
# survives; the internal half is what 19s removes.
STATE35="$TMP_DIR/c35.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(string_payload sess-1265-n Bash "$STR_BARE_EXIT" 'cat /tmp/a')" "$STATE35" >/dev/null 2>/dev/null
pipe_hook "$(string_payload sess-1265-n Bash "$STR_BARE_EXIT" '  cat /tmp/a  ')" "$STATE35" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] && assert_match "2회째" "$out"; then
  assert_pass "19n) leading/trailing whitespace differences still match"
else
  assert_fail "19n) leading/trailing whitespace differences still match" "rc=$rc out=[$out] err=[$err]"
fi

echo "=== case 19o: commands past the 4096 bound ==="
# Differing INSIDE the bound => distinct keys => silent.
LONG_PAD="$(python3 -c "print('x' * 5000)")"
STATE36="$TMP_DIR/c36.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(string_payload sess-1265-o1 Bash "$STR_BARE_EXIT" "cat /tmp/a $LONG_PAD")" "$STATE36" >/dev/null 2>/dev/null
pipe_hook "$(string_payload sess-1265-o1 Bash "$STR_BARE_EXIT" "cat /tmp/b $LONG_PAD")" "$STATE36" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; then
  assert_pass "19o-1) >4096-char commands differing inside the bound stay silent"
else
  assert_fail "19o-1) >4096-char commands differing inside the bound stay silent" "rc=$rc out=[$out] err=[$err]"
fi

# Differing only PAST the bound => same key => advises. Documents the truncation
# rather than leaving it untested.
STATE37="$TMP_DIR/c37.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(string_payload sess-1265-o2 Bash "$STR_BARE_EXIT" "cat ${LONG_PAD}A")" "$STATE37" >/dev/null 2>/dev/null
pipe_hook "$(string_payload sess-1265-o2 Bash "$STR_BARE_EXIT" "cat ${LONG_PAD}B")" "$STATE37" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] && assert_match "2회째" "$out"; then
  assert_pass "19o-2) commands differing only past the 4096 bound share a key"
else
  assert_fail "19o-2) commands differing only past the 4096 bound share a key" "rc=$rc out=[$out] err=[$err]"
fi

echo "=== case 19p: unicode commands are discriminated ==="
STATE38="$TMP_DIR/c38.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(string_payload sess-1265-p Bash "$STR_BARE_EXIT" 'grep 실패 /tmp/log')" "$STATE38" >/dev/null 2>/dev/null
pipe_hook "$(string_payload sess-1265-p Bash "$STR_BARE_EXIT" 'grep 성공 /tmp/log')" "$STATE38" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; then
  assert_pass "19p) unicode-only-differing commands stay silent"
else
  assert_fail "19p) unicode-only-differing commands stay silent" "rc=$rc out=[$out] err=[$err]"
fi

echo "=== case 19q: non-bare failures still normalize (the normalizer is untouched) ==="
# The discriminator must not have been bought by weakening `_normalize_signature`:
# two failures whose text differs only by a path must STILL merge and advise.
STATE39="$TMP_DIR/c39.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(string_payload sess-1265-q Bash $'Error: Exit code 1\ncannot open /tmp/a' 'cat /tmp/a')" "$STATE39" >/dev/null 2>/dev/null
pipe_hook "$(string_payload sess-1265-q Bash $'Error: Exit code 1\ncannot open /tmp/b' 'cat /tmp/b')" "$STATE39" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] && assert_match "2회째" "$out"; then
  assert_pass "19q) non-bare path-only-differing failures still normalize together"
else
  assert_fail "19q) non-bare path-only-differing failures still normalize together" "rc=$rc out=[$out] err=[$err]"
fi

echo "=== case 19r: a non-Bash tool with no command key is unaffected ==="
# `Edit`, not an MCP tool: MCP strings are scoped separately by 19t, and Edit's
# successful results are dicts (1,135/1,135 built-in-tool successes in the
# census), so its string payload is failure evidence exactly as Bash's is. The
# text is captured verbatim from a real failed Edit.
non_bash_bare_payload() {
  python3 - "$1" <<'PY'
import json, sys
print(json.dumps({
    "session_id": sys.argv[1],
    "tool_name": "Edit",
    "tool_input": {"file_path": "/tmp/whatever"},
    "tool_response": "Error: String to replace not found in file.",
}))
PY
}
STATE40="$TMP_DIR/c40.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(non_bash_bare_payload sess-1265-r)" "$STATE40" >/dev/null 2>/dev/null
pipe_hook "$(non_bash_bare_payload sess-1265-r)" "$STATE40" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] && assert_match "2회째" "$out"; then
  assert_pass "19r) non-Bash tool with no command key keeps advising on repeat"
else
  assert_fail "19r) non-Bash tool with no command key keeps advising on repeat" "rc=$rc out=[$out] err=[$err]"
fi

# ---------------------------------------------------------------------------
# Case 19s: shell-significant whitespace inside the command.
#
# The digest used to collapse every whitespace run to one space so a re-typed
# command still matched. Whitespace is not decoration in shell: a newline
# separates two commands, and a run inside quotes is part of an argument. Two
# distinct commands therefore digested to one hash and the second one fired a
# false advisory — the collision this discriminator exists to prevent. 19s-e is
# the control: with the collapse gone, the same command still advises.
# ---------------------------------------------------------------------------
echo "=== case 19s: shell-significant internal whitespace => distinct keys ==="
# U+00A0 written as an escape: a literal NBSP in this file is invisible to a
# reader and to `git diff`, and the case turns on it being there.
NBSP="$(python3 -c 'import sys; sys.stdout.write("\u00a0")')"
ws_case() {
  # ws_case <label> <session> <cmd-1> <cmd-2> <expect: silent|advisory>
  local label="$1" sess="$2" cmd1="$3" cmd2="$4" expect="$5"
  local state="$TMP_DIR/ws-$sess.json"
  local o e r
  o="$(mktemp)" e="$(mktemp)"
  pipe_hook "$(string_payload "$sess" Bash "$STR_BARE_EXIT" "$cmd1")" "$state" >/dev/null 2>/dev/null
  pipe_hook "$(string_payload "$sess" Bash "$STR_BARE_EXIT" "$cmd2")" "$state" >"$o" 2>"$e"
  r=$?
  local out err
  out=$(cat "$o"); err=$(cat "$e")
  rm -f "$o" "$e"
  if [ "$expect" = "silent" ]; then
    if [ "$r" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; then
      assert_pass "$label"
    else
      assert_fail "$label" "rc=$r out=[$out] err=[$err]"
    fi
  else
    if [ "$r" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] && assert_match "2회째" "$out"; then
      assert_pass "$label"
    else
      assert_fail "$label" "rc=$r out=[$out] err=[$err]"
    fi
  fi
}

ws_case "19s-a) newline vs space are different programs" \
  sess-1265-sa $'false\nfalse' 'false false' silent
ws_case "19s-b) tab vs space stay on distinct keys" \
  sess-1265-sb $'cat\t/tmp/a' 'cat /tmp/a' silent
ws_case "19s-c) a run inside quotes is part of the argument" \
  sess-1265-sc "test 'a  b' = x" "test 'a b' = x" silent
ws_case "19s-d) NBSP is not a shell separator" \
  sess-1265-sd "cat${NBSP}/tmp/a" 'cat /tmp/a' silent
ws_case "19s-e) control: the same command still advises" \
  sess-1265-se 'cat /tmp/a' 'cat /tmp/a' advisory

# ---------------------------------------------------------------------------
# Case 19t: MCP strings are trusted only when the HARNESS wrote them.
#
# Nothing in the hook payload marks a string as an error — `is_error` lives on
# the transcript's `tool_result` block — so the classifier reads the shape.
# Censused over 14,652 `toolUseResult` entries: MCP is the only class whose
# *successful* results ever arrive as a bare string (25 of 1,152; Bash and the
# other built-ins are 12,927/12,927 dicts). On that channel a leading `Error: `
# can be the tool's own text, so a tool answering `Error: no rows found` on
# success accumulated state and advised on its second return. 19t-b/c/d are the
# controls that keep "scoped" distinguishable from "MCP stopped firing".
# ---------------------------------------------------------------------------
echo "=== case 19t: MCP string payloads are scoped to harness-authored text ==="
mcp_case() {
  # mcp_case <label> <session> <tool_response> <expect: silent|advisory>
  local label="$1" sess="$2" text="$3" expect="$4"
  local state="$TMP_DIR/mcp-$sess.json"
  local o e r out err
  o="$(mktemp)" e="$(mktemp)"
  pipe_hook "$(string_payload "$sess" mcp__db__query "$text")" "$state" >/dev/null 2>/dev/null
  pipe_hook "$(string_payload "$sess" mcp__db__query "$text")" "$state" >"$o" 2>"$e"
  r=$?
  out=$(cat "$o"); err=$(cat "$e")
  rm -f "$o" "$e"
  if [ "$expect" = "silent" ]; then
    if [ "$r" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; then
      assert_pass "$label"
    else
      assert_fail "$label" "rc=$r out=[$out] err=[$err]"
    fi
  else
    if [ "$r" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] && assert_match "2회째" "$out"; then
      assert_pass "$label"
    else
      assert_fail "$label" "rc=$r out=[$out] err=[$err]"
    fi
  fi
}

# The defect: a SUCCESSFUL MCP result whose own text opens with `Error: `.
mcp_case "19t-a) an MCP success whose text opens with 'Error: ' stays silent" \
  sess-1265-ta 'Error: no rows found' silent
# Controls — the harness's own strings still advise on the same channel.
mcp_case "19t-b) the PreToolUse hook-error envelope still advises" \
  sess-1265-tb 'Error: PreToolUse:mcp__db__query hook error: blocked by gate' advisory
mcp_case "19t-c) a repeated user rejection still advises" \
  sess-1265-tc 'User rejected tool use' advisory
# The pre-existing must-fail case, on the channel it was actually observed on.
mcp_case "19t-d) the oversized-output notice still stays silent" \
  sess-1265-td "$STR_OVERSIZED" silent

# ---------------------------------------------------------------------------
# Case 20: the PostToolUseFailure event (issue #1337).
#
# A real Bash `tool_response` carries no exit status (#1096), so the
# PostToolUse path can only see a failed Bash command when the harness happens
# to deliver it as an `Error: `-prefixed string (#1265). The harness's
# PostToolUseFailure event is the documented signal: top-level `error` whose
# first line, for Bash, is `Exit code N`; `is_interrupt: true` when the run
# reached Claude Code as an abort. The payloads below follow that contract.
# ---------------------------------------------------------------------------
echo "=== case 20: PostToolUseFailure event ==="
failure_event_payload() {
  # failure_event_payload <session> <tool_name> <tool_use_id> <error|__nonstr__> [command] [is_interrupt]
  python3 - "$1" "$2" "$3" "$4" "${5:-}" "${6:-}" <<'PY'
import json, sys
session_id, tool_name, tool_use_id, error, command, interrupt = sys.argv[1:7]
payload = {
    "session_id": session_id,
    "hook_event_name": "PostToolUseFailure",
    "tool_name": tool_name,
    "tool_use_id": tool_use_id,
    "tool_input": {"command": command} if command else {"query": "select 1"},
    # A non-string `error` is a shape the hook has never seen: fail-open.
    "error": {"code": 1} if error == "__nonstr__" else error,
    "duration_ms": 1234,
}
if interrupt == "interrupt":
    payload["is_interrupt"] = True
print(json.dumps(payload))
PY
}
posttooluse_event_payload() {
  # posttooluse_event_payload <session> <tool_use_id> <tool_response-string> [command]
  python3 - "$1" "$2" "$3" "${4:-true}" <<'PY'
import json, sys
session_id, tool_use_id, text, command = sys.argv[1:5]
print(json.dumps({
    "session_id": session_id,
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
    "tool_use_id": tool_use_id,
    "tool_input": {"command": command},
    "tool_response": text,
}))
PY
}

# Doc contract: first line `Exit code N`, then the command's interleaved output.
ERR_NPM=$'Exit code 1\nnpm ERR! missing script: test\nnpm ERR! A complete log of this run can be found in: /root/.npm/_logs/2026-09-06T10_00_00_000Z-debug-0.log'

# 20a) the same Bash failure twice -> advisory on the 2nd, under the event's own name.
STATE50="$TMP_DIR/c50.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(failure_event_payload sess-1337-a Bash toolu_a1 "$ERR_NPM" 'npm test')" "$STATE50" >/dev/null 2>/dev/null
pipe_hook "$(failure_event_payload sess-1337-a Bash toolu_a2 "$ERR_NPM" 'npm test')" "$STATE50" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] && assert_match "2회째" "$out" \
    && assert_match '"hookEventName": "PostToolUseFailure"' "$out"; then
  assert_pass "20a) repeated Bash PostToolUseFailure advises on the 2nd, named after the event"
else
  assert_fail "20a) repeated Bash PostToolUseFailure advises on the 2nd, named after the event" \
    "rc=$rc out=[$out] err=[$err]"
fi

# 20a-1) the first occurrence is still silent (two-way control).
STATE51="$TMP_DIR/c51.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(failure_event_payload sess-1337-a1 Bash toolu_a3 "$ERR_NPM" 'npm test')" "$STATE51" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ] && [ -f "$STATE51" ]; then
  assert_pass "20a-1) first PostToolUseFailure is silent but counted"
else
  assert_fail "20a-1) first PostToolUseFailure is silent but counted" \
    "rc=$rc out=[$out] err=[$err] state_exists=$([ -f "$STATE51" ] && echo yes || echo no)"
fi

# 20b) is_interrupt:true is not a failure of the command: silent, and no state
# is written — twice, so a counted interrupt could not hide as a "first".
STATE52="$TMP_DIR/c52.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(failure_event_payload sess-1337-b Bash toolu_b1 $'Exit code 130\n^C' 'sleep 99' interrupt)" "$STATE52" >/dev/null 2>/dev/null
pipe_hook "$(failure_event_payload sess-1337-b Bash toolu_b2 $'Exit code 130\n^C' 'sleep 99' interrupt)" "$STATE52" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ] && [ ! -f "$STATE52" ]; then
  assert_pass "20b) is_interrupt:true is silent and writes no state"
else
  assert_fail "20b) is_interrupt:true is silent and writes no state" \
    "rc=$rc out=[$out] err=[$err] state_exists=$([ -f "$STATE52" ] && echo yes || echo no)"
fi

# 20c) one tool call, both events: PostToolUse string first, then the failure
# event with the SAME tool_use_id. Counted once, so the second event is silent
# and the pair's count stays at 1.
STATE53="$TMP_DIR/c53.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(posttooluse_event_payload sess-1337-c toolu_c1 $'Error: Exit code 1\n(eval):1: == not found' 'if [ x == y ]; then :; fi')" "$STATE53" >/dev/null 2>/dev/null
pipe_hook "$(failure_event_payload sess-1337-c Bash toolu_c1 $'Exit code 1\n(eval):1: == not found' 'if [ x == y ]; then :; fi')" "$STATE53" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"
count_c="$(python3 -c 'import json,sys; s=json.load(open(sys.argv[1])); print(sum(s["failures"].values()))' "$STATE53" 2>/dev/null)"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ] && [ "$count_c" = "1" ]; then
  assert_pass "20c) same tool_use_id via PostToolUse then PostToolUseFailure counts once"
else
  assert_fail "20c) same tool_use_id via PostToolUse then PostToolUseFailure counts once" \
    "rc=$rc out=[$out] err=[$err] total_count=[$count_c]"
fi

# 20c-1) the reverse arrival order dedupes too, and a THIRD event with a new id
# for the same failure is the real 2nd occurrence -> advisory. This is the
# control that separates "deduped" from "the failure path stopped counting".
STATE54="$TMP_DIR/c54.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(failure_event_payload sess-1337-c1 Bash toolu_c2 $'Exit code 1\n(eval):1: == not found' 'if [ x == y ]; then :; fi')" "$STATE54" >/dev/null 2>/dev/null
pipe_hook "$(posttooluse_event_payload sess-1337-c1 toolu_c2 $'Error: Exit code 1\n(eval):1: == not found' 'if [ x == y ]; then :; fi')" "$STATE54" >/dev/null 2>/dev/null
count_c1_mid="$(python3 -c 'import json,sys; s=json.load(open(sys.argv[1])); print(sum(s["failures"].values()))' "$STATE54" 2>/dev/null)"
pipe_hook "$(failure_event_payload sess-1337-c1 Bash toolu_c3 $'Exit code 1\n(eval):1: == not found' 'if [ x == y ]; then :; fi')" "$STATE54" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ "$count_c1_mid" = "1" ] && [ -n "$out" ] \
    && assert_match "Failure #2" "$out"; then
  assert_pass "20c-1) reverse order dedupes; a new id for the same failure is occurrence #2"
else
  assert_fail "20c-1) reverse order dedupes; a new id for the same failure is occurrence #2" \
    "rc=$rc out=[$out] err=[$err] count_after_pair=[$count_c1_mid]"
fi

# 20d) a non-Bash MCP tool: the event is the verdict, so the tool's own error
# text counts without the `Error: ` allowlist the string path needs (19t).
STATE55="$TMP_DIR/c55.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(failure_event_payload sess-1337-d mcp__db__query toolu_d1 'The operation timed out.')" "$STATE55" >/dev/null 2>/dev/null
pipe_hook "$(failure_event_payload sess-1337-d mcp__db__query toolu_d2 'The operation timed out.')" "$STATE55" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] && assert_match "2회째" "$out" \
    && assert_match "mcp__db__query failure pattern recurring" "$out"; then
  assert_pass "20d) a repeated MCP tool error string advises via PostToolUseFailure"
else
  assert_fail "20d) a repeated MCP tool error string advises via PostToolUseFailure" \
    "rc=$rc out=[$out] err=[$err]"
fi

# 20e) negative control: a PostToolUse SUCCESS payload that now carries
# `hook_event_name` and a `tool_use_id` still passes — the field routes, it
# does not classify.
STATE56="$TMP_DIR/c56.json"
success_event_payload="$(python3 - <<'PY'
import json
print(json.dumps({
    "session_id": "sess-1337-e",
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
    "tool_use_id": "toolu_e1",
    "tool_input": {"command": "git fetch origin"},
    "tool_response": {"stdout": "", "stderr": "From origin\n * branch main -> FETCH_HEAD\nShell cwd was reset to /tmp/x", "interrupted": False, "isImage": False, "noOutputExpected": False},
}))
PY
)"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$success_event_payload" "$STATE56" >/dev/null 2>/dev/null
pipe_hook "$success_event_payload" "$STATE56" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ] && [ ! -f "$STATE56" ]; then
  assert_pass "20e) a PostToolUse success with hook_event_name present stays silent"
else
  assert_fail "20e) a PostToolUse success with hook_event_name present stays silent" \
    "rc=$rc out=[$out] err=[$err] state_exists=$([ -f "$STATE56" ] && echo yes || echo no)"
fi

# 20f) one pair key across the two events: the same failure reaching the hook
# once as a PostToolUse string (`Error: ` envelope) and once as a failure event
# (no envelope), under DIFFERENT ids, must be occurrence #2 — otherwise a
# session alternating between the events never advises.
STATE57="$TMP_DIR/c57.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(posttooluse_event_payload sess-1337-f toolu_f1 $'Error: Exit code 1\n(eval):1: == not found' 'if [ x == y ]; then :; fi')" "$STATE57" >/dev/null 2>/dev/null
pipe_hook "$(failure_event_payload sess-1337-f Bash toolu_f2 $'Exit code 1\n(eval):1: == not found' 'if [ x == y ]; then :; fi')" "$STATE57" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] && assert_match "Failure #2" "$out"; then
  assert_pass "20f) PostToolUse string and PostToolUseFailure error share one pair key"
else
  assert_fail "20f) PostToolUse string and PostToolUseFailure error share one pair key" \
    "rc=$rc out=[$out] err=[$err]"
fi

# 20g) a non-string `error` is an unseen shape: fail-open, silent, no state.
STATE58="$TMP_DIR/c58.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(failure_event_payload sess-1337-g Bash toolu_g1 __nonstr__ 'false')" "$STATE58" >/dev/null 2>/dev/null
pipe_hook "$(failure_event_payload sess-1337-g Bash toolu_g2 __nonstr__ 'false')" "$STATE58" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ] && [ ! -f "$STATE58" ]; then
  assert_pass "20g) a non-string error field fails open with no state"
else
  assert_fail "20g) a non-string error field fails open with no state" \
    "rc=$rc out=[$out] err=[$err] state_exists=$([ -f "$STATE58" ] && echo yes || echo no)"
fi

# 20h) a bare `Exit code 1` with no output carries nothing to tell two commands
# apart, so the command digest joins the key here exactly as it does for the
# string path (19g/19h): different commands stay silent, the same command
# advises.
STATE59="$TMP_DIR/c59.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(failure_event_payload sess-1337-h Bash toolu_h1 'Exit code 1' 'grep -q needle haystack.txt')" "$STATE59" >/dev/null 2>/dev/null
pipe_hook "$(failure_event_payload sess-1337-h Bash toolu_h2 'Exit code 1' 'git diff --quiet HEAD~1')" "$STATE59" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; then
  assert_pass "20h-1) bare Exit code from two different commands stays silent"
else
  assert_fail "20h-1) bare Exit code from two different commands stays silent" \
    "rc=$rc out=[$out] err=[$err]"
fi

STATE60="$TMP_DIR/c60.json"
out_file="$(mktemp)" err_file="$(mktemp)"
pipe_hook "$(failure_event_payload sess-1337-h1 Bash toolu_h3 'Exit code 1' 'grep -q needle haystack.txt')" "$STATE60" >/dev/null 2>/dev/null
pipe_hook "$(failure_event_payload sess-1337-h1 Bash toolu_h4 'Exit code 1' 'grep -q needle haystack.txt')" "$STATE60" >"$out_file" 2>"$err_file"
rc=$?
out=$(cat "$out_file"); err=$(cat "$err_file")
rm -f "$out_file" "$err_file"

if [ "$rc" -eq 0 ] && [ -z "$err" ] && [ -n "$out" ] && assert_match "2회째" "$out"; then
  assert_pass "20h-2) bare Exit code from the same command twice still advises"
else
  assert_fail "20h-2) bare Exit code from the same command twice still advises" \
    "rc=$rc out=[$out] err=[$err]"
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
