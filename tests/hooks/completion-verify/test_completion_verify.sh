#!/bin/bash
# tests/test_completion_verify.sh — Stop hook coverage for completion-verify
#
# Synthesizes JSONL transcripts and runs the hook with stop_hook_active=false
# to assert: same-turn Bash + EVIDENCE_PATTERNS in tool_result + paste in
# assistant message → pass; any of those missing → block.
#
# Run:  ./tests/test_completion_verify.sh
# Exit: 0 on success, 1 on first failure (after summary).

set +e

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HOOK="$REPO_ROOT/hooks/completion-verify/completion-verify/impl.sh"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# Build a JSONL transcript file from $3 (multi-line content) and run the hook.
# Args:
#   $1 = case name
#   $2 = expected: "block" or "pass"
#   $3 = transcript JSONL content (each line is one event)
run_case() {
  local name="$1" expected="$2" transcript="$3"

  local tpath="$TMPDIR/transcript_${PASS}_${FAIL}.jsonl"
  printf '%s\n' "$transcript" > "$tpath"

  local payload
  payload=$(jq -nc --arg path "$tpath" \
    '{transcript_path: $path, stop_hook_active: false, session_id: "test-session"}')

  local out
  out=$(printf '%s' "$payload" | "$HOOK" 2>/dev/null)
  local rc=$?

  if [ "$rc" -ne 0 ]; then
    echo "FAIL  [$name] hook exited $rc (expected 0)"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
  fi

  case "$expected" in
    block)
      if ! echo "$out" | grep -q '"decision": "block"'; then
        echo "FAIL  [$name] expected block, got: ${out:-<empty>}"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      fi
      ;;
    pass)
      if [ -n "$out" ]; then
        echo "FAIL  [$name] expected pass (no output), got: $out"
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

# JSONL builders -------------------------------------------------------------

mk_user_text() {
  local text="$1"
  jq -nc --arg t "$text" '{
    type: "user",
    isSidechain: false,
    message: {role: "user", content: $t}
  }'
}

mk_assistant() {
  # $1 = text content
  # $2 = JSON array string of additional content blocks (e.g. tool_use entries)
  local text="$1" extra="${2:-[]}"
  jq -nc --arg t "$text" --argjson x "$extra" '{
    type: "assistant",
    isSidechain: false,
    message: {
      role: "assistant",
      content: ([{type: "text", text: $t}] + $x)
    }
  }'
}

mk_bash_use() {
  local id="$1" cmd="$2"
  jq -nc --arg id "$id" --arg c "$cmd" \
    '{type: "tool_use", id: $id, name: "Bash", input: {command: $c}}'
}

mk_tool_result() {
  local id="$1" content="$2"
  jq -nc --arg id "$id" --arg c "$content" '{
    type: "user",
    isSidechain: false,
    message: {
      role: "user",
      content: [{type: "tool_result", tool_use_id: $id, content: $c}]
    }
  }'
}

# AC1 — same-turn Bash + EVIDENCE in tool_result + paste in body → pass ------
USER1=$(mk_user_text "implement feature X")
BASH1=$(mk_bash_use "abc" "pytest tests/")
ASST1A=$(mk_assistant "Running tests..." "[$BASH1]")
RESULT1=$(mk_tool_result "abc" "5 tests passed in 0.12 seconds (no failures)")
ASST1B=$(mk_assistant "Pytest output: 5 tests passed in 0.12 seconds (no failures). All done.")
run_case "AC1 same-turn Bash+evidence+paste" pass "$USER1
$ASST1A
$RESULT1
$ASST1B"

# AC2 — claim with no Bash tool calls → block --------------------------------
USER2=$(mk_user_text "is this OK?")
ASST2=$(mk_assistant "Looks correct. All done.")
run_case "AC2 no-Bash claim" block "$USER2
$ASST2"

# AC3 — Bash present but tool_result lacks EVIDENCE_PATTERNS → block ---------
USER3=$(mk_user_text "check status")
BASH3=$(mk_bash_use "xyz" "git status")
ASST3A=$(mk_assistant "checking..." "[$BASH3]")
RESULT3=$(mk_tool_result "xyz" "On branch main\nworking tree is tidy")
ASST3B=$(mk_assistant "Status looks fine. All done.")
run_case "AC3 Bash without evidence signal" block "$USER3
$ASST3A
$RESULT3
$ASST3B"

# AC4 — Bash + EVIDENCE in tool_result but no paste in body → block ----------
USER4=$(mk_user_text "run the tests")
BASH4=$(mk_bash_use "qrs" "pytest")
ASST4A=$(mk_assistant "running..." "[$BASH4]")
RESULT4=$(mk_tool_result "qrs" "12 tests passed in 0.85 seconds (verification details A B C)")
ASST4B=$(mk_assistant "Yep all good. All done.")
run_case "AC4 evidence without paste" block "$USER4
$ASST4A
$RESULT4
$ASST4B"

# AC5 — claim NOT in last 10 lines (mid-message 완료) → pass -----------------
USER5=$(mk_user_text "explain X")
ASST5=$(mk_assistant "All done with the introduction. Now the steps:
Step 1: configure
Step 2: deploy
Step 3: monitor
Step 4: scale
Step 5: optimize
Step 6: review
Step 7: iterate
Step 8: document
Step 9: handoff
Step 10: close
Step 11: end")
run_case "AC5 claim outside last 10 lines" pass "$USER5
$ASST5"

# AC6 — non-Bash tool_use only → block (Bash gating preserved) ---------------
USER6=$(mk_user_text "read this file")
READ_USE=$(jq -nc '{type: "tool_use", id: "rd1", name: "Read", input: {file_path: "/tmp/x"}}')
ASST6A=$(mk_assistant "reading..." "[$READ_USE]")
READ_RESULT=$(jq -nc '{
  type: "user",
  isSidechain: false,
  message: {
    role: "user",
    content: [{type: "tool_result", tool_use_id: "rd1", content: "5 tests passed in 0.12 seconds (no failures)"}]
  }
}')
ASST6B=$(mk_assistant "5 tests passed in 0.12 seconds (no failures). All done.")
run_case "AC6 non-Bash tool ignored as evidence" block "$USER6
$ASST6A
$READ_RESULT
$ASST6B"

# AC7b — realistic pytest output ("12 passed in 0.85s", no "tests" word) → pass
USER7B=$(mk_user_text "run pytest")
BASH7B=$(mk_bash_use "pyt1" "pytest -q")
ASST7B_A=$(mk_assistant "running pytest..." "[$BASH7B]")
RESULT7B=$(mk_tool_result "pyt1" "============= 12 passed in 0.85s =============")
ASST7B_B=$(mk_assistant "Pytest result: 12 passed in 0.85s. All done.")
run_case "AC7b realistic pytest output" pass "$USER7B
$ASST7B_A
$RESULT7B
$ASST7B_B"

# AC7 — claim followed by Korean evidence (e.g. 테스트 통과) + paste → pass --
USER7=$(mk_user_text "pytest 돌려봐")
BASH7=$(mk_bash_use "kor1" "pytest")
ASST7A=$(mk_assistant "테스트 실행 중..." "[$BASH7]")
RESULT7=$(mk_tool_result "kor1" "전체 테스트 통과 — 총 8건 (실패 0, 경고 0)")
ASST7B=$(mk_assistant "출력 결과: 전체 테스트 통과 — 총 8건 (실패 0, 경고 0). 작업 완료.")
run_case "AC7 Korean evidence with paste" pass "$USER7
$ASST7A
$RESULT7
$ASST7B"

# AG1 — echo-fabricated evidence (echo of the success token) → block [#758] --
USER_AG1=$(mk_user_text "run the tests")
BASH_AG1=$(mk_bash_use "ag1" 'echo "5 tests passed"')
ASST_AG1A=$(mk_assistant "verifying..." "[$BASH_AG1]")
RESULT_AG1=$(mk_tool_result "ag1" "5 tests passed")
ASST_AG1B=$(mk_assistant "5 tests passed. All done.")
run_case "AG1 echo-fabricated evidence blocked" block "$USER_AG1
$ASST_AG1A
$RESULT_AG1
$ASST_AG1B"

# AG2 — printf-fabricated evidence → block [#758] ---------------------------
USER_AG2=$(mk_user_text "check it")
BASH_AG2=$(mk_bash_use "ag2" 'printf "PASS\n"')
ASST_AG2A=$(mk_assistant "checking..." "[$BASH_AG2]")
RESULT_AG2=$(mk_tool_result "ag2" "PASS")
ASST_AG2B=$(mk_assistant "PASS. All done.")
run_case "AG2 printf-fabricated evidence blocked" block "$USER_AG2
$ASST_AG2A
$RESULT_AG2
$ASST_AG2B"

# AG3 — real command chained with echo (has ';') → pass [#758] --------------
USER_AG3=$(mk_user_text "run tests")
BASH_AG3=$(mk_bash_use "ag3" 'pytest -q; echo cleanup')
ASST_AG3A=$(mk_assistant "running..." "[$BASH_AG3]")
RESULT_AG3=$(mk_tool_result "ag3" "12 passed in 0.85s")
ASST_AG3B=$(mk_assistant "12 passed in 0.85s. All done.")
run_case "AG3 real command chained with echo passes" pass "$USER_AG3
$ASST_AG3A
$RESULT_AG3
$ASST_AG3B"

# AG4 — Korean echo-fabricated evidence → block [#758] ---------------------
USER_AG4=$(mk_user_text "테스트 돌려봐")
BASH_AG4=$(mk_bash_use "ag4" 'echo "테스트 통과"')
ASST_AG4A=$(mk_assistant "확인 중..." "[$BASH_AG4]")
RESULT_AG4=$(mk_tool_result "ag4" "테스트 통과")
ASST_AG4B=$(mk_assistant "테스트 통과. 작업 완료.")
run_case "AG4 Korean echo-fabricated evidence blocked" block "$USER_AG4
$ASST_AG4A
$RESULT_AG4
$ASST_AG4B"

# AG5 — echo of command-substitution (has '$(') runs a real command → pass [#758]
USER_AG5=$(mk_user_text "run pytest")
# SC2016: the $(...) is intentional literal command-string data, not for expansion.
# shellcheck disable=SC2016
BASH_AG5=$(mk_bash_use "ag5" 'echo "$(pytest -q)"')
ASST_AG5A=$(mk_assistant "running..." "[$BASH_AG5]")
RESULT_AG5=$(mk_tool_result "ag5" "3 passed in 0.10s")
ASST_AG5B=$(mk_assistant "3 passed in 0.10s. All done.")
run_case "AG5 echo of command-substitution passes" pass "$USER_AG5
$ASST_AG5A
$RESULT_AG5
$ASST_AG5B"

# Fail-safe 1 — stop_hook_active=true → pass (no block) ---------------------
fs_payload=$(jq -nc '{transcript_path: "/dev/null", stop_hook_active: true, session_id: "x"}')
fs_out=$(printf '%s' "$fs_payload" | "$HOOK" 2>/dev/null)
fs_rc=$?
if [ "$fs_rc" -eq 0 ] && [ -z "$fs_out" ]; then
  echo "PASS  [fail-safe stop_hook_active=true]"; PASS=$((PASS + 1))
else
  echo "FAIL  [fail-safe stop_hook_active=true] rc=$fs_rc out=$fs_out"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("fail-safe stop_hook_active=true")
fi

# Fail-safe 2 — missing transcript path → pass ------------------------------
ms_payload=$(jq -nc '{transcript_path: "/nonexistent/path/12345", stop_hook_active: false, session_id: "x"}')
ms_out=$(printf '%s' "$ms_payload" | "$HOOK" 2>/dev/null)
ms_rc=$?
if [ "$ms_rc" -eq 0 ] && [ -z "$ms_out" ]; then
  echo "PASS  [fail-safe missing transcript]"; PASS=$((PASS + 1))
else
  echo "FAIL  [fail-safe missing transcript] rc=$ms_rc out=$ms_out"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("fail-safe missing transcript")
fi

# Fail-safe 3 — empty transcript file → pass --------------------------------
empty_path="$TMPDIR/empty.jsonl"
: > "$empty_path"
empty_payload=$(jq -nc --arg p "$empty_path" '{transcript_path: $p, stop_hook_active: false, session_id: "x"}')
empty_out=$(printf '%s' "$empty_payload" | "$HOOK" 2>/dev/null)
empty_rc=$?
if [ "$empty_rc" -eq 0 ] && [ -z "$empty_out" ]; then
  echo "PASS  [fail-safe empty transcript]"; PASS=$((PASS + 1))
else
  echo "FAIL  [fail-safe empty transcript] rc=$empty_rc out=$empty_out"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("fail-safe empty transcript")
fi

# Fail-safe 4 — malformed JSONL line → pass ---------------------------------
bad_path="$TMPDIR/bad.jsonl"
echo 'not valid json' > "$bad_path"
bad_payload=$(jq -nc --arg p "$bad_path" '{transcript_path: $p, stop_hook_active: false, session_id: "x"}')
bad_out=$(printf '%s' "$bad_payload" | "$HOOK" 2>/dev/null)
bad_rc=$?
if [ "$bad_rc" -eq 0 ] && [ -z "$bad_out" ]; then
  echo "PASS  [fail-safe malformed JSONL]"; PASS=$((PASS + 1))
else
  echo "FAIL  [fail-safe malformed JSONL] rc=$bad_rc out=$bad_out"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("fail-safe malformed JSONL")
fi

echo
echo "=========================================="
echo "  PASS: $PASS  FAIL: $FAIL"
echo "=========================================="
if [ "$FAIL" -gt 0 ]; then
  printf '  failed: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
