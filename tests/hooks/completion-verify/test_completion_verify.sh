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

TMPDIR=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
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

mk_edit_use() {
  # $1 = tool_use id, $2 = path, $3 = tool name (Edit/Write/MultiEdit/NotebookEdit)
  local id="$1" path="$2" name="${3:-Edit}"
  local key="file_path"
  [ "$name" = "NotebookEdit" ] && key="notebook_path"
  jq -nc --arg id "$id" --arg p "$path" --arg n "$name" --arg k "$key" \
    '{type: "tool_use", id: $id, name: $n, input: {($k): $p}}'
}

mk_named_use() {
  # $1 = tool_use id, $2 = tool name (e.g. an MCP browser tool)
  local id="$1" name="$2"
  jq -nc --arg id "$id" --arg n "$name" \
    '{type: "tool_use", id: $id, name: $n, input: {}}'
}

mk_tool_result() {
  # $3 = "error" marks the result is_error (a tool call that failed)
  local id="$1" content="$2" err="${3:-}"
  local is_err=false
  [ "$err" = "error" ] && is_err=true
  jq -nc --arg id "$id" --arg c "$content" --argjson e "$is_err" '{
    type: "user",
    isSidechain: false,
    message: {
      role: "user",
      content: [{type: "tool_result", tool_use_id: $id, content: $c, is_error: $e}]
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

# FE — evidence class gated by the changed surface [#943] -------------------
#
# A frontend surface edited this turn needs evidence that something observed
# the rendered page. A generic pass (pytest, curl) satisfies the generic gate
# but says nothing about what the browser shows.

FE_OUT="============= 12 passed in 0.85s ============="
FE_CITE="12 passed in 0.85s. 작업 완료."

# fe_case <name> <expected> <edited-path> <tool> <verify-cmd> <verify-out> <final-text>
fe_case() {
  local name="$1" expected="$2" path="$3" tool="$4" cmd="$5" out="$6" final="$7"
  local u a1 b a2 r a3
  u=$(mk_user_text "고쳐줘")
  a1=$(mk_assistant "편집 중..." "[$(mk_edit_use "fe-e" "$path" "$tool")]")
  b=$(mk_bash_use "fe-b" "$cmd")
  a2=$(mk_assistant "확인 중..." "[$b]")
  r=$(mk_tool_result "fe-b" "$out")
  a3=$(mk_assistant "$final")
  run_case "$name" "$expected" "$u
$a1
$a2
$r
$a3"
}

# FE1 — the motivating failure: .tsx edited, only curl/grep evidence → block
fe_case "FE1 frontend edit with curl-only evidence" block \
  "apps/web/src/Docs.tsx" Edit \
  "curl -s -o /dev/null -w '%{http_code}' https://example.test" \
  "200 — 12 passed in 0.85s" "$FE_CITE"

# FE2 — cmux browser navigation with --snapshot-after → pass
fe_case "FE2 frontend edit with cmux --snapshot-after" pass \
  "apps/web/src/Docs.tsx" Edit \
  "cmux browser goto http://localhost:3000/docs --snapshot-after" \
  "$FE_OUT" "$FE_CITE"

# FE3 — cmux browser observation subcommand (get text) → pass
fe_case "FE3 frontend edit with cmux browser get text" pass \
  "apps/web/src/Docs.tsx" Edit \
  "cmux browser get text '.doc-link'" "$FE_OUT" "$FE_CITE"

# FE4 — global flags between `cmux` and `browser` must not break the match
fe_case "FE4 cmux global flag before browser subcommand" pass \
  "apps/web/src/Docs.tsx" Edit \
  "cmux --password secret browser snapshot --compact" "$FE_OUT" "$FE_CITE"

# FE6 — echoed browser command is fabrication, not observation → block
USER_FE6=$(mk_user_text "고쳐줘")
ASST_FE6A=$(mk_assistant "편집 중..." "[$(mk_edit_use "fe6-e" "apps/web/src/Docs.tsx" Edit)]")
ASST_FE6B=$(mk_assistant "확인 중..." "[$(mk_bash_use "fe6-b0" 'echo "cmux browser snapshot"')]")
RESULT_FE6A=$(mk_tool_result "fe6-b0" "cmux browser snapshot")
ASST_FE6C=$(mk_assistant "테스트 중..." "[$(mk_bash_use "fe6-b1" "pytest -q")]")
RESULT_FE6B=$(mk_tool_result "fe6-b1" "$FE_OUT")
ASST_FE6D=$(mk_assistant "$FE_CITE")
run_case "FE6 echoed browser command is not observation" block "$USER_FE6
$ASST_FE6A
$ASST_FE6B
$RESULT_FE6A
$ASST_FE6C
$RESULT_FE6B
$ASST_FE6D"

# FE5 — a browser MCP tool never appears as a Bash command → pass
USER_FE5=$(mk_user_text "고쳐줘")
ASST_FE5A=$(mk_assistant "편집 중..." "[$(mk_edit_use "fe5-e" "apps/web/src/Docs.tsx" Edit)]")
ASST_FE5B=$(mk_assistant "스냅샷..." "[$(mk_named_use "fe5-m" "mcp__playwright__browser_snapshot")]")
RESULT_FE5M=$(mk_tool_result "fe5-m" "- link \"문서\" [ref=e12]")
ASST_FE5C=$(mk_assistant "테스트 중..." "[$(mk_bash_use "fe5-b" "pytest -q")]")
RESULT_FE5=$(mk_tool_result "fe5-b" "$FE_OUT")
ASST_FE5D=$(mk_assistant "$FE_CITE")
run_case "FE5 browser MCP tool counts as observation" pass "$USER_FE5
$ASST_FE5A
$ASST_FE5B
$RESULT_FE5M
$ASST_FE5C
$RESULT_FE5
$ASST_FE5D"

# FE7 — backend-only edit keeps the generic gate's verdict (no regression)
fe_case "FE7 backend edit unaffected" pass \
  "src/api/handler.py" Edit "pytest -q" "$FE_OUT" "$FE_CITE"

# FE8 — no edit at all (Q&A turn) → no false positive
USER_FE8=$(mk_user_text "상태 알려줘")
ASST_FE8A=$(mk_assistant "확인 중..." "[$(mk_bash_use "fe8-b" "pytest -q")]")
RESULT_FE8=$(mk_tool_result "fe8-b" "$FE_OUT")
ASST_FE8B=$(mk_assistant "$FE_CITE")
run_case "FE8 no edit this turn" pass "$USER_FE8
$ASST_FE8A
$RESULT_FE8
$ASST_FE8B"

# FE9 — extension must be terminal: a .tsx.bak backup is not a frontend surface
fe_case "FE9 .tsx.bak is not a frontend surface" pass \
  "apps/web/src/Docs.tsx.bak" Edit "pytest -q" "$FE_OUT" "$FE_CITE"

# FE10 — extension matching is case-insensitive
fe_case "FE10 uppercase .TSX still frontend" block \
  "apps/web/src/Docs.TSX" Edit "pytest -q" "$FE_OUT" "$FE_CITE"

# FE11 — Write (file creation), not just Edit, counts as a surface change
fe_case "FE11 Write of a .vue file counts" block \
  "app/Card.vue" Write "pytest -q" "$FE_OUT" "$FE_CITE"

# FE12 — NotebookEdit carries notebook_path; .ipynb is not a frontend surface
fe_case "FE12 NotebookEdit .ipynb is not frontend" pass \
  "nb/analysis.ipynb" NotebookEdit "pytest -q" "$FE_OUT" "$FE_CITE"

# FE13 — no completion claim → the gate never arms, frontend or not
fe_case "FE13 frontend edit without a completion claim" pass \
  "apps/web/src/Docs.tsx" Edit "curl -s https://example.test" "200" \
  "아직 확인 중입니다."

# FE14 — stylesheets are a frontend surface too
fe_case "FE14 scss edit counts as frontend" block \
  "apps/web/src/theme.scss" Edit "pytest -q" "$FE_OUT" "$FE_CITE"

# FE15..FE21 — codex review round 1 (PR #957) -------------------------------
#
# The first cut asked only whether a browser call was ATTEMPTED and matched its
# driver name anywhere in the string, so a failed snapshot, a package listing,
# and a `sed -i` edit all landed on the wrong side of the gate.

# FE15 — the browser MCP call failed, so nothing observed the page → block.
USER_FE15=$(mk_user_text "고쳐줘")
ASST_FE15A=$(mk_assistant "편집 중..." "[$(mk_edit_use "fe15-e" "apps/web/src/Docs.tsx" Edit)]")
RESULT_FE15E=$(mk_tool_result "fe15-e" "ok")
ASST_FE15B=$(mk_assistant "스냅샷..." "[$(mk_named_use "fe15-m" "mcp__playwright__browser_snapshot")]")
RESULT_FE15M=$(mk_tool_result "fe15-m" "Error: browser is not open" error)
ASST_FE15C=$(mk_assistant "테스트 중..." "[$(mk_bash_use "fe15-b" "pytest -q")]")
RESULT_FE15=$(mk_tool_result "fe15-b" "$FE_OUT")
ASST_FE15D=$(mk_assistant "$FE_CITE")
run_case "FE15 failed browser MCP call is not observation" block "$USER_FE15
$ASST_FE15A
$RESULT_FE15E
$ASST_FE15B
$RESULT_FE15M
$ASST_FE15C
$RESULT_FE15
$ASST_FE15D"

# FE16 — the .tsx Edit itself failed, so no surface changed → no block.
USER_FE16=$(mk_user_text "고쳐줘")
ASST_FE16A=$(mk_assistant "편집 중..." "[$(mk_edit_use "fe16-e" "apps/web/src/Docs.tsx" Edit)]")
RESULT_FE16E=$(mk_tool_result "fe16-e" "String to replace not found in file" error)
ASST_FE16B=$(mk_assistant "테스트 중..." "[$(mk_bash_use "fe16-b" "pytest -q")]")
RESULT_FE16=$(mk_tool_result "fe16-b" "$FE_OUT")
ASST_FE16C=$(mk_assistant "$FE_CITE")
run_case "FE16 failed Edit is not a surface change" pass "$USER_FE16
$ASST_FE16A
$RESULT_FE16E
$ASST_FE16B
$RESULT_FE16
$ASST_FE16C"

# FE17 — the frontend file was edited through Bash, not a file tool → block.
fe_case "FE17 sed -i on a .tsx counts as a surface change" block \
  "src/api/handler.py" Edit \
  "sed -i '' 's/old/new/' apps/web/src/Docs.tsx" "$FE_OUT" "$FE_CITE"

# FE18 — a frontend path that is only READ by the command is not a change.
fe_case "FE18 grep of a .tsx redirected elsewhere is not a change" pass \
  "src/api/handler.py" Edit \
  "grep -n href apps/web/src/Docs.tsx > /tmp/out.txt" "$FE_OUT" "$FE_CITE"

# FE19 — a package listing mentions the driver but observes nothing → block.
fe_case "FE19 npm ls puppeteer is not observation" block \
  "apps/web/src/Docs.tsx" Edit "npm ls puppeteer" "$FE_OUT" "$FE_CITE"

# FE20 — 'snapshot' inside the URL of a navigate-only command → block.
fe_case "FE20 snapshot in a goto URL is not observation" block \
  "apps/web/src/Docs.tsx" Edit \
  "cmux browser goto http://localhost:3000/snapshot" "$FE_OUT" "$FE_CITE"

# FE21 — a browser MCP tool that drives without reading back → block.
USER_FE21=$(mk_user_text "고쳐줘")
ASST_FE21A=$(mk_assistant "편집 중..." "[$(mk_edit_use "fe21-e" "apps/web/src/Docs.tsx" Edit)]")
RESULT_FE21E=$(mk_tool_result "fe21-e" "ok")
ASST_FE21B=$(mk_assistant "닫는 중..." "[$(mk_named_use "fe21-m" "mcp__playwright__browser_close")]")
RESULT_FE21M=$(mk_tool_result "fe21-m" "closed")
ASST_FE21C=$(mk_assistant "테스트 중..." "[$(mk_bash_use "fe21-b" "pytest -q")]")
RESULT_FE21=$(mk_tool_result "fe21-b" "$FE_OUT")
ASST_FE21D=$(mk_assistant "$FE_CITE")
run_case "FE21 browser_close reads nothing back" block "$USER_FE21
$ASST_FE21A
$RESULT_FE21E
$ASST_FE21B
$RESULT_FE21M
$ASST_FE21C
$RESULT_FE21
$ASST_FE21D"

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

# === SubagentStop (issue #1337) ============================================
#
# On SubagentStop `transcript_path` is the PARENT session's and
# `agent_transcript_path` is the subagent's own. Every case below is written
# so the two transcripts DISAGREE: reading the wrong one flips the verdict,
# which is what makes these tests able to fail.

# $1 name, $2 expected, $3 main-transcript JSONL, $4 agent-transcript JSONL,
# $5 optional last_assistant_message
run_subagent_case() {
  local name="$1" expected="$2" main="$3" agent="$4" last_msg="${5:-}"

  local mpath apath
  mpath="$TMPDIR/sub_main_${PASS}_${FAIL}.jsonl"
  apath="$TMPDIR/sub_agent_${PASS}_${FAIL}.jsonl"
  printf '%s\n' "$main" > "$mpath"
  printf '%s\n' "$agent" > "$apath"

  local payload
  payload=$(jq -nc --arg m "$mpath" --arg a "$apath" --arg l "$last_msg" '
    {hook_event_name: "SubagentStop", transcript_path: $m,
     agent_transcript_path: $a, stop_hook_active: false,
     agent_id: "def456", agent_type: "Explore",
     session_id: "test-subagent"}
    + (if $l == "" then {} else {last_assistant_message: $l} end)')

  local out rc
  out=$(printf '%s' "$payload" | "$HOOK" 2>/dev/null)
  rc=$?

  if [ "$rc" -ne 0 ]; then
    echo "FAIL  [$name] hook exited $rc (expected 0)"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
  fi
  case "$expected" in
    block)
      if ! echo "$out" | grep -q '"decision": "block"'; then
        echo "FAIL  [$name] expected block, got: ${out:-<empty>}"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      fi ;;
    pass)
      if [ -n "$out" ]; then
        echo "FAIL  [$name] expected pass (no output), got: $out"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      fi ;;
  esac
  echo "PASS  [$name]"
  PASS=$((PASS + 1))
}

# A parent turn that WOULD pass: real Bash evidence, pasted. The subagent's
# own turn claims completion with nothing behind it. Reading the parent's
# transcript passes; reading the subagent's blocks.
SS_USER=$(mk_user_text "delegate the check")
SS_BASH=$(mk_bash_use "p1" "pytest tests/")
SS_PARENT_A=$(mk_assistant "running..." "[$SS_BASH]")
SS_PARENT_R=$(mk_tool_result "p1" "9 tests passed in 0.20 seconds")
SS_PARENT_B=$(mk_assistant "Parent evidence: 9 tests passed in 0.20 seconds. All done.")
SS_PARENT="$SS_USER
$SS_PARENT_A
$SS_PARENT_R
$SS_PARENT_B"

SS_AGENT_USER=$(mk_user_text "check the config")
SS_AGENT_A=$(mk_assistant "Looks correct. All done.")
SS_AGENT_NOEVIDENCE="$SS_AGENT_USER
$SS_AGENT_A"

run_subagent_case "SubagentStop grades the subagent, not the parent" \
  block "$SS_PARENT" "$SS_AGENT_NOEVIDENCE"

# The mirror image: the subagent DID verify and pasted the span, while the
# parent's turn is a bare claim. Reading the parent would block a clean run.
SS_AGENT_BASH=$(mk_bash_use "a1" "pytest tests/unit")
SS_AGENT_B1=$(mk_assistant "running..." "[$SS_AGENT_BASH]")
SS_AGENT_R=$(mk_tool_result "a1" "3 tests passed in 0.05 seconds")
SS_AGENT_B2=$(mk_assistant "Subagent evidence: 3 tests passed in 0.05 seconds. All done.")
SS_AGENT_OK="$SS_AGENT_USER
$SS_AGENT_B1
$SS_AGENT_R
$SS_AGENT_B2"
SS_PARENT_BARE="$SS_USER
$(mk_assistant "Looks correct. All done.")"

run_subagent_case "SubagentStop passes on the subagent's own evidence" \
  pass "$SS_PARENT_BARE" "$SS_AGENT_OK"

# The agent transcript may carry isSidechain markers. Keeping the filter would
# empty its turn and pass every subagent silently — the failure this asserts
# against is a false PASS, so the case is built to block.
SS_AGENT_SIDECHAIN=$(printf '%s\n%s' \
  "$(echo "$SS_AGENT_USER" | jq -c '.isSidechain = true')" \
  "$(echo "$SS_AGENT_A" | jq -c '.isSidechain = true')")
run_subagent_case "sidechain-marked agent transcript is still graded" \
  block "$SS_PARENT" "$SS_AGENT_SIDECHAIN"

# last_assistant_message is the documented source for the final text; the
# transcript "may lag". Here the agent transcript's last message carries no
# claim and the payload's does, so only the payload-preferring read blocks.
SS_AGENT_NOCLAIM="$SS_AGENT_USER
$(mk_assistant "Reviewed the config.")"
run_subagent_case "payload last_assistant_message carries the claim" \
  block "$SS_PARENT" "$SS_AGENT_NOCLAIM" "Looks correct. All done."

# An agent transcript that has not been flushed resolves to NOTHING, never to
# the parent's. Two cases, because the fallback failed in both directions:
# the parent's turn would BLOCK here, so falling back emits a verdict about
# the wrong conversation...
SS_PARENT_WOULD_BLOCK="$SS_USER
$(mk_assistant "Looks correct. All done.")"
printf '%s\n' "$SS_PARENT_WOULD_BLOCK" > "$TMPDIR/ss_fallback.jsonl"
SS_MISSING_PAYLOAD=$(jq -nc --arg m "$TMPDIR/ss_fallback.jsonl" \
  --arg a "$TMPDIR/subagents/never-written.jsonl" '
  {hook_event_name: "SubagentStop", transcript_path: $m,
   agent_transcript_path: $a, stop_hook_active: false,
   session_id: "test-subagent-fallback"}')
ss_out=$(printf '%s' "$SS_MISSING_PAYLOAD" | "$HOOK" 2>/dev/null)
if [ -z "$ss_out" ]; then
  echo "PASS  [unflushed agent transcript does not grade the parent's turn]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [unflushed agent transcript does not grade the parent's turn] out=$ss_out"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("unflushed agent transcript does not grade the parent's turn")
fi

# ...and here it CLEARS: the subagent ran nothing and merely repeated a number
# from the parent's output, which satisfied the evidence and paste checks
# against evidence it never produced (CodeRabbit on #1358).
printf '%s\n' "$SS_PARENT" > "$TMPDIR/ss_fallback2.jsonl"
SS_FALSE_CLEAR=$(jq -nc --arg m "$TMPDIR/ss_fallback2.jsonl" \
  --arg a "$TMPDIR/subagents/never-written.jsonl" '
  {hook_event_name: "SubagentStop", transcript_path: $m,
   agent_transcript_path: $a, stop_hook_active: false,
   last_assistant_message: "9 tests passed in 0.20 seconds. All done.",
   session_id: "test-subagent-false-clear"}')
ss_out2=$(printf '%s' "$SS_FALSE_CLEAR" | "$HOOK" 2>/dev/null)
if [ -z "$ss_out2" ]; then
  echo "PASS  [unflushed agent transcript cannot borrow the parent's evidence]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [unflushed agent transcript cannot borrow the parent's evidence] out=$ss_out2"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("unflushed agent transcript cannot borrow the parent's evidence")
fi

# A SubagentStop that carries no agent_transcript_path at all is the same
# case: the event name alone must keep the parent's transcript out.
SS_NO_KEY=$(jq -nc --arg m "$TMPDIR/ss_fallback.jsonl" '
  {hook_event_name: "SubagentStop", transcript_path: $m,
   stop_hook_active: false, session_id: "test-subagent-no-key"}')
ss_out3=$(printf '%s' "$SS_NO_KEY" | "$HOOK" 2>/dev/null)
if [ -z "$ss_out3" ]; then
  echo "PASS  [SubagentStop without the key still refuses the parent]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [SubagentStop without the key still refuses the parent] out=$ss_out3"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("SubagentStop without the key still refuses the parent")
fi

# Control: a plain Stop payload is unchanged by any of the above.
run_case "Stop payload unchanged by the SubagentStop path" pass "$SS_PARENT"

echo
echo "=========================================="
echo "  PASS: $PASS  FAIL: $FAIL"
echo "=========================================="
if [ "$FAIL" -gt 0 ]; then
  printf '  failed: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
