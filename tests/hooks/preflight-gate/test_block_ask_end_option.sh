#!/usr/bin/env bash
# test-block-ask-end-option.sh — coverage for the AskUserQuestion end-option gate
#
# Synthesizes Claude Code PreToolUse(AskUserQuestion) payloads and asserts:
#   advisory → exit 0 + stderr non-empty  (PRAXIS_ASK_END_ADVISORY=1 opt-out)
#   block    → exit 2 + stderr non-empty  (default, or PRAXIS_ASK_END_STRICT=1)
#   pass     → exit 0 + stderr empty
#
# Usage: bash hooks/test-block-ask-end-option.sh
# Exit:  0 = all pass; 1 = at least one fail
#
# Hook is STRICT by default — most "end-marker present + no stop signal"
# cases expect exit 2 + non-empty stderr. Advisory cases (with
# PRAXIS_ASK_END_ADVISORY=1) expect exit 0 + non-empty stderr.

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/block-ask-end-option/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# Build a transcript JSONL file from a single user message.
# $1 = user message text (empty string → no user entries written)
build_transcript() {
  local msg="$1"
  local path="$WORK/transcript-$$-$RANDOM.jsonl"
  if [ -n "$msg" ]; then
    python3 -c '
import json, sys
print(json.dumps({"type": "user", "message": {"role": "user", "content": sys.argv[1]}}))
' "$msg" > "$path"
  else
    : > "$path"
  fi
  echo "$path"
}

# Build a JSON payload for AskUserQuestion tool call.
# $1 = transcript_path
# $2 = options JSON array (e.g., '["Plan A", "End here"]')
build_payload() {
  local transcript="$1" options_json="$2"
  python3 - <<PY
import json, sys
options = json.loads('''$options_json''')
payload = {
    "session_id": "test-session",
    "transcript_path": "$transcript",
    "tool_name": "AskUserQuestion",
    "tool_input": {
        "questions": [
            {
                "question": "Next step?",
                "header": "Next",
                "multiSelect": False,
                "options": [{"label": opt, "description": "test desc"} for opt in options],
            }
        ]
    },
    "cwd": "/tmp",
}
print(json.dumps(payload))
PY
}

run_case() {
  local name="$1" expected="$2" mode="$3" payload="$4"
  local err_file rc

  err_file=$(mktemp)
  case "$mode" in
    strict)
      # Explicit legacy strict env var (deprecated but still honoured).
      echo "$payload" | PRAXIS_ASK_END_STRICT=1 "$HOOK" >/dev/null 2>"$err_file"
      ;;
    advisory)
      # Opt-out to advisory via new env var.
      echo "$payload" | PRAXIS_ASK_END_ADVISORY=1 "$HOOK" >/dev/null 2>"$err_file"
      ;;
    default|*)
      # Default is now strict — no env var override.
      echo "$payload" | "$HOOK" >/dev/null 2>"$err_file"
      ;;
  esac
  rc=$?
  local err_content
  err_content=$(cat "$err_file"); rm -f "$err_file"

  local ok=1
  case "$expected" in
    advisory)
      [ "$rc" -eq 0 ] && [ -n "$err_content" ] || ok=0
      ;;
    block)
      [ "$rc" -eq 2 ] && [ -n "$err_content" ] || ok=0
      ;;
    pass)
      [ "$rc" -eq 0 ] && [ -z "$err_content" ] || ok=0
      ;;
    *)
      echo "INTERNAL: unknown expected '$expected'" >&2
      ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS [$expected] $name"; PASS=$((PASS+1))
  else
    echo "FAIL [$expected→rc=$rc,stderr=$([ -n "$err_content" ] && echo non-empty || echo empty)] $name"
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# (a) BLOCK cases — default mode (strict), end marker present, no stop signal
# ---------------------------------------------------------------------------

T1=$(build_transcript "Continue with the next step")
P1=$(build_payload "$T1" '["Plan A", "Plan B", "여기서 종료"]')
run_case "korean end marker, neutral user message" block default "$P1"

T2=$(build_transcript "What about option B?")
P2=$(build_payload "$T2" '["Implement", "Review", "End here"]')
run_case "english end marker, neutral user message" block default "$P2"

T3=$(build_transcript "다음 단계 진행해주세요")
P3=$(build_payload "$T3" '["Step 1", "Step 2", "세션 종료"]')
run_case "korean continuation, korean end marker" block default "$P3"

# ---------------------------------------------------------------------------
# (b) BLOCK cases — explicit strict env var (deprecated, still honoured)
# ---------------------------------------------------------------------------

T4=$(build_transcript "Continue please")
P4=$(build_payload "$T4" '["Plan A", "여기서 종료"]')
run_case "explicit strict env + end marker + no signal → block" block strict "$P4"

T5=$(build_transcript "")
P5=$(build_payload "$T5" '["Plan A", "End here"]')
run_case "explicit strict env + empty transcript → block (no signal)" block strict "$P5"

# ---------------------------------------------------------------------------
# (b2) ADVISORY cases — opt-out via PRAXIS_ASK_END_ADVISORY=1
# ---------------------------------------------------------------------------

T_adv1=$(build_transcript "Continue with the next step")
P_adv1=$(build_payload "$T_adv1" '["Plan A", "Plan B", "여기서 종료"]')
run_case "advisory mode + korean end marker, neutral message" advisory advisory "$P_adv1"

T_adv2=$(build_transcript "What about option B?")
P_adv2=$(build_payload "$T_adv2" '["Implement", "Review", "End here"]')
run_case "advisory mode + english end marker, neutral message" advisory advisory "$P_adv2"

# ---------------------------------------------------------------------------
# (c) PASS cases — no end marker
# ---------------------------------------------------------------------------

T6=$(build_transcript "Continue")
P6=$(build_payload "$T6" '["Plan A", "Plan B", "Plan C"]')
run_case "no end marker present" pass default "$P6"

T7=$(build_transcript "Continue")
P7=$(build_payload "$T7" '["Plan A", "Plan B", "Plan C"]')
run_case "no end marker present (strict mode also passes)" pass strict "$P7"

# ---------------------------------------------------------------------------
# (d) PASS cases — end marker present BUT user signaled stop
# ---------------------------------------------------------------------------

T8=$(build_transcript "그만 하고 여기서 마무리하자")
P8=$(build_payload "$T8" '["Plan A", "여기서 종료"]')
run_case "korean stop signal in user message" pass default "$P8"

T9=$(build_transcript "Stop here, we're done")
P9=$(build_payload "$T9" '["Plan A", "End here"]')
run_case "english stop signal in user message" pass default "$P9"

T10=$(build_transcript "그만")
P10=$(build_payload "$T10" '["Plan A", "여기서 종료"]')
run_case "minimal korean stop signal (strict mode also passes)" pass strict "$P10"

# ---------------------------------------------------------------------------
# (e) PASS cases — not AskUserQuestion tool
# ---------------------------------------------------------------------------

P11=$(python3 -c '
import json
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": "echo end here"},
}))')
run_case "non-AskUserQuestion tool passes through" pass default "$P11"

P12=$(python3 -c '
import json
print(json.dumps({
    "tool_name": "Edit",
    "tool_input": {"old_string": "여기서 종료", "new_string": "stop"},
}))')
run_case "Edit tool with end-marker in args passes through" pass default "$P12"

# ---------------------------------------------------------------------------
# (f) Graceful degrade — malformed / missing payload pieces
# ---------------------------------------------------------------------------

P13='not even json'
run_case "malformed JSON payload → graceful exit 0" pass default "$P13"

P14=$(python3 -c '
import json
print(json.dumps({
    "tool_name": "AskUserQuestion",
    "tool_input": {},
}))')
run_case "AskUserQuestion with no questions → pass" pass default "$P14"

P15=$(python3 -c '
import json
print(json.dumps({
    "tool_name": "AskUserQuestion",
    "tool_input": {"questions": [{"options": "not-a-list"}]},
}))')
run_case "questions with non-list options → pass" pass default "$P15"

# missing transcript_path: per project hook design contract, unreadable
# transcripts MUST fail open — the strict-mode flip cannot retroactively
# block on an unverifiable stop-signal check.
T16=$(build_transcript "")
P16=$(build_payload "/nonexistent/path-$$.jsonl" '["Plan A", "End here"]')
run_case "missing transcript file + end marker → pass (fail-open)" pass default "$P16"

# ---------------------------------------------------------------------------
# (g) Multi-question payload — marker in any question triggers advisory
# ---------------------------------------------------------------------------

T17=$(build_transcript "Just continue")
P17=$(python3 - <<PY
import json
print(json.dumps({
    "session_id": "test-session",
    "transcript_path": "$T17",
    "tool_name": "AskUserQuestion",
    "tool_input": {
        "questions": [
            {
                "question": "A?",
                "options": [{"label": "yes"}, {"label": "no"}],
            },
            {
                "question": "B?",
                "options": [{"label": "Plan A"}, {"label": "End here"}],
            },
        ]
    },
}))
PY
)
run_case "multi-question payload, marker in second question" block default "$P17"

# ---------------------------------------------------------------------------
# (h) F1 regression — bare-word stop tokens must NOT trigger (codex #193)
# ---------------------------------------------------------------------------

# Before F1 fix, 'send' contained 'end' (substring of stop signal "end") and
# 'backend' contained 'end' / 'don't stop' contained 'stop' — all three
# false-allowed the surface. With phrase-only matching + negation guard,
# these messages must NOT register as stop signals.

T18=$(build_transcript "send the message to the team")
P18=$(build_payload "$T18" '["Plan A", "End here"]')
run_case "[F1] 'send' substring must NOT pass as stop signal" block default "$P18"

T19=$(build_transcript "the backend service is failing")
P19=$(build_payload "$T19" '["Plan A", "End here"]')
run_case "[F1] 'backend' substring must NOT pass as stop signal" block default "$P19"

T20=$(build_transcript "don't stop now, keep going")
P20=$(build_payload "$T20" '["Plan A", "End here"]')
run_case "[F1] negated 'don't stop now' must NOT pass" block default "$P20"

T21=$(build_transcript "I quit the previous job last year")
P21=$(build_payload "$T21" '["Plan A", "End here"]')
run_case "[F1] 'quit' bare word in unrelated context must NOT pass" block default "$P21"

T22=$(build_transcript "do not wrap up yet")
P22=$(build_payload "$T22" '["Plan A", "End here"]')
run_case "[F1] negated 'do not wrap up' must NOT pass" block default "$P22"

# ---------------------------------------------------------------------------
# (i) F2 regression — tool_result-only user entry must be skipped (codex #193)
# ---------------------------------------------------------------------------

# Build a transcript where the most recent user entry is tool_result-only,
# preceded by a real human user message with an explicit stop signal.
# Before F2 fix, the hook returned empty string at the tool_result entry
# and never reached the real human message → false-block in strict mode.

build_tool_result_transcript() {
  local human_text="$1"
  local path="$WORK/transcript-tr-$$-$RANDOM.jsonl"
  python3 - "$human_text" > "$path" <<'PY'
import json, sys
human_text = sys.argv[1]
# Order: oldest first (real human message), then assistant, then
# tool_result-only user entry as the most recent.
print(json.dumps({"type": "user", "message": {"role": "user", "content": human_text}}))
print(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Working on it."}]}}))
print(json.dumps({
    "type": "user",
    "message": {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "abc123", "content": "command output text"}
        ],
    },
}))
PY
  echo "$path"
}

T23=$(build_tool_result_transcript "stop here please, we are done")
P23=$(build_payload "$T23" '["Plan A", "End here"]')
run_case "[F2] tool_result-only user entry skipped, prior 'stop here' found" pass default "$P23"

T24=$(build_tool_result_transcript "그만 하고 마무리하자")
P24=$(build_payload "$T24" '["Plan A", "여기서 종료"]')
run_case "[F2] tool_result-only skipped, prior korean stop signal" pass strict "$P24"

T25=$(build_tool_result_transcript "Continue with the next step")
P25=$(build_payload "$T25" '["Plan A", "End here"]')
run_case "[F2] tool_result-only skipped, prior msg has no signal → block" block default "$P25"

# ---------------------------------------------------------------------------
# (j) F1 positive — phrase-based stop signals continue to match
# ---------------------------------------------------------------------------

# Ensure the phrase-based matching still catches legitimate stop signals.

T26=$(build_transcript "let's stop here for today")
P26=$(build_payload "$T26" '["Plan A", "End here"]')
run_case "[F1+] 'let's stop here' phrase passes" pass default "$P26"

T27=$(build_transcript "I think we are done with this discussion")
P27=$(build_payload "$T27" '["Plan A", "End here"]')
run_case "[F1+] 'we are done' phrase passes" pass default "$P27"

T28=$(build_transcript "Time to wrap up, that's all from me")
P28=$(build_payload "$T28" '["Plan A", "End here"]')
run_case "[F1+] 'wrap up' + 'that's all' phrases pass" pass default "$P28"

# ---------------------------------------------------------------------------
# (k) Indirect end-option markers — English (issue #209)
# ---------------------------------------------------------------------------

T_ie1=$(build_transcript "Continue with the plan")
P_ie1=$(build_payload "$T_ie1" '["Plan A", "Plan B", "Take a break"]')
run_case "[indirect-EN] 'take a break' in options → block" block default "$P_ie1"

T_ie2=$(build_transcript "What should we do next?")
P_ie2=$(build_payload "$T_ie2" '["Option 1", "Option 2", "Prioritize other work"]')
run_case "[indirect-EN] 'prioritize other work' → block" block default "$P_ie2"

T_ie3=$(build_transcript "Keep going please")
P_ie3=$(build_payload "$T_ie3" '["Option 1", "Pause for now"]')
run_case "[indirect-EN] 'pause for now' → block" block default "$P_ie3"

T_ie4=$(build_transcript "Continue implementation")
P_ie4=$(build_payload "$T_ie4" '["Plan A", "Plan B", "Resume in a later session"]')
run_case "[indirect-EN] 'resume in a later session' → block" block default "$P_ie4"

T_ie5=$(build_transcript "진행해 주세요")
P_ie5=$(build_payload "$T_ie5" '["Option A", "Option B", "Other work first"]')
run_case "[indirect-EN] 'other work first' → block" block default "$P_ie5"

# ---------------------------------------------------------------------------
# (l) Indirect end-option markers — Korean (issue #209)
# ---------------------------------------------------------------------------

T_ik1=$(build_transcript "계속 진행해 주세요")
P_ik1=$(build_payload "$T_ik1" '["옵션 A", "옵션 B", "잠시 멈춰"]')
run_case "[indirect-KO] '잠시 멈춰' in options → block" block default "$P_ik1"

T_ik2=$(build_transcript "다음 단계 알려주세요")
P_ik2=$(build_payload "$T_ik2" '["Plan A", "잠시 보류"]')
run_case "[indirect-KO] '잠시 보류' → block" block default "$P_ik2"

T_ik3=$(build_transcript "계속해주세요")
P_ik3=$(build_payload "$T_ik3" '["옵션 1", "옵션 2", "휴식"]')
run_case "[indirect-KO] '휴식' → block" block default "$P_ik3"

T_ik4=$(build_transcript "다음 작업 알려주세요")
P_ik4=$(build_payload "$T_ik4" '["Plan A", "다른 작업 우선"]')
run_case "[indirect-KO] '다른 작업 우선' → block" block default "$P_ik4"

T_ik5=$(build_transcript "계속 진행해")
P_ik5=$(build_payload "$T_ik5" '["Plan A", "Plan B", "다음 세션"]')
run_case "[indirect-KO] '다음 세션' → block" block default "$P_ik5"

# Bare "보류" intentionally omitted as a marker — see block-ask-end-option.py
# rationale comment. Verify false-positive cases below don't regress.

# ---------------------------------------------------------------------------
# (m) 4-option padding pattern — 4th option only carries indirect marker
# ---------------------------------------------------------------------------

T_4p1=$(build_transcript "이슈 분석 부탁해")
P_4p1=$(build_payload "$T_4p1" '["이슈 생성", "구현 계획", "워크트리 설정", "Take a break"]')
run_case "[4-pad] 4-option set, 4th only is indirect → block" block default "$P_4p1"

T_4p2=$(build_transcript "continue")
P_4p2=$(build_payload "$T_4p2" '["Step 1", "Step 2", "Step 3", "Pause for now"]')
run_case "[4-pad] 4-option set, 4th only is 'pause for now' → block" block default "$P_4p2"

T_4p3=$(build_transcript "계속 진행해주세요")
P_4p3=$(build_payload "$T_4p3" '["리뷰", "구현", "테스트", "다음 세션"]')
run_case "[4-pad] 4-option set, 4th only is '다음 세션' → block" block default "$P_4p3"

# ---------------------------------------------------------------------------
# (n-pre) Heading-separator KO end-tokens in option labels (issue #236)
# ---------------------------------------------------------------------------
#
# Before #236, END_OPTION_MARKERS_KO required full phrased forms ("여기서 종료",
# "세션 종료"). A label of shape "종료 — context" fell through _has_end_marker
# because no phrased substring matched. PR #241 review rejected the
# bare-token approach (false-blocks "종료된 이슈 목록" / "회의 마무리 방식
# 검토"); instead we list the heading-separator patterns explicitly:
# "{token} —" / "{token} -" / "{token}:" — these require a separator, so
# they catch the issue-#236 trigger without colliding with inflected nouns.

T_sep1=$(build_transcript "다음 단계 진행해주세요")
P_sep1=$(build_payload "$T_sep1" '["Plan A", "Plan B", "종료 — 여기서 끊자"]')
run_case "[#236] '종료 —' separator label → block" block default "$P_sep1"

T_sep2=$(build_transcript "계속 작업해주세요")
P_sep2=$(build_payload "$T_sep2" '["Option 1", "Option 2", "그만 — 다음에 이어서"]')
run_case "[#236] '그만 —' separator label → block" block default "$P_sep2"

T_sep3=$(build_transcript "다음 작업 알려주세요")
P_sep3=$(build_payload "$T_sep3" '["Plan A", "Plan B", "마무리 — 정리하고 종료"]')
run_case "[#236] '마무리 —' separator label → block" block default "$P_sep3"

T_sep4=$(build_transcript "다음 단계는?")
P_sep4=$(build_payload "$T_sep4" '["Plan A", "종료: 세션 닫기"]')
run_case "[#236] '종료:' colon-separator label → block" block default "$P_sep4"

T_sep5=$(build_transcript "다음 작업 알려주세요")
P_sep5=$(build_payload "$T_sep5" '["Plan A", "Plan B", "마무리: 회고 작성"]')
run_case "[#236] '마무리:' colon-separator label → block" block default "$P_sep5"

T_sep6=$(build_transcript "계속 진행해주세요")
P_sep6=$(build_payload "$T_sep6" '["Plan A", "그만 - 휴식 필요"]')
run_case "[#236] '그만 -' hyphen-separator label → block" block default "$P_sep6"

# Separator marker + stop signal in user message → pass (signal short-circuits).
T_sep7=$(build_transcript "그만하자")
P_sep7=$(build_payload "$T_sep7" '["Plan A", "종료 — 여기서 끊자"]')
run_case "[#236] '종료 —' label + stop signal → pass" pass default "$P_sep7"

# False-positive regression: PR #241 review surfaced inflected-noun forms
# that bare-token matching would have wrongly blocked. Verify the
# phrased-separator approach lets these through.

T_fp_inf1=$(build_transcript "이번 분기 완료된 작업 보여주세요")
P_fp_inf1=$(build_payload "$T_fp_inf1" '["종료된 이슈 목록", "진행 중 이슈", "신규 이슈"]')
run_case "[#236-fp] '종료된 이슈 목록' (inflected) → pass" pass default "$P_fp_inf1"

T_fp_inf2=$(build_transcript "회의 운영 개선안 알려주세요")
P_fp_inf2=$(build_payload "$T_fp_inf2" '["회의 마무리 방식 검토", "어젠다 정리", "회고 도입"]')
run_case "[#236-fp] '회의 마무리 방식 검토' (compound) → pass" pass default "$P_fp_inf2"

T_fp_inf3=$(build_transcript "이슈 정렬 기준 알려주세요")
P_fp_inf3=$(build_payload "$T_fp_inf3" '["종료 시각 기준", "생성 시각 기준", "우선순위 기준"]')
run_case "[#236-fp] '종료 시각 기준' (inflected noun) → pass" pass default "$P_fp_inf3"

# ---------------------------------------------------------------------------
# (n) False positive avoidance — legitimate work options must NOT be blocked
# ---------------------------------------------------------------------------

T_fp1=$(build_transcript "다음 단계 알려주세요")
P_fp1=$(build_payload "$T_fp1" '["이슈 생성", "PR 생성", "테스트 실행"]')
run_case "[false-pos] normal work options, no end marker → pass" pass default "$P_fp1"

T_fp2=$(build_transcript "진행해주세요")
P_fp2=$(build_payload "$T_fp2" '["Plan A", "다른 방법 먼저", "Option C"]')
run_case "[false-pos] '다른 방법 먼저' is NOT '다른 작업 우선' → pass" pass default "$P_fp2"

T_fp3=$(build_transcript "keep going")
P_fp3=$(build_payload "$T_fp3" '["break down the task", "pause and review docs", "continue"]')
run_case "[false-pos] 'break down' / 'pause and review' not end markers → pass" pass default "$P_fp3"

T_fp4=$(build_transcript "무엇을 해야 할까요")
P_fp4=$(build_payload "$T_fp4" '["작업 세션 예약", "코드 리뷰", "배포"]')
run_case "[false-pos] '세션' alone in non-end context → pass" pass default "$P_fp4"

# Bare "보류" regression — was previously a marker, now removed because it
# substring-matched legitimate labels like "보류 중인 이슈 확인".
T_fp5=$(build_transcript "이슈 정리해주세요")
P_fp5=$(build_payload "$T_fp5" '["보류 중인 이슈 확인", "보류 상태 검토", "신규 이슈 분류"]')
run_case "[false-pos] '보류 중인 이슈 확인' / '보류 상태 검토' → pass" pass default "$P_fp5"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
# Fail-open: uncaught exception in _main_inner returns 0 (issue #498)
# ---------------------------------------------------------------------------
# Inject an unexpected exception (e.g. MemoryError / RecursionError) into
# _main_inner and assert the outer try/except Exception wrapper in main()
# returns exit 0 with no stderr — the documented "a hook never breaks a
# normal session" fail-open contract.
_failopen_out=$(python3 - << PYEOF 2>&1
import sys, importlib.util, io
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
def _raise(): raise MemoryError("simulated catastrophic failure")
mod._main_inner = _raise
sys.stdin = io.StringIO('{}')
sys.exit(mod.main())
PYEOF
)
_failopen_rc=$?
if [ "$_failopen_rc" -eq 0 ] && [ -z "$_failopen_out" ]; then
  echo "PASS  [fail-open] uncaught exception in _main_inner -> exit 0, no stderr"
  PASS=$((PASS+1))
else
  echo "FAIL  [fail-open] uncaught exception in _main_inner (rc=$_failopen_rc out=$(echo "$_failopen_out" | head -c 160))"
  FAIL=$((FAIL+1)); FAILED_NAMES+=("fail-open uncaught exception in _main_inner")
fi


# ---------------------------------------------------------------------------

echo ""
echo "=========================================="
echo "PASS: $PASS"
echo "FAIL: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
fi

[ "$FAIL" -eq 0 ]
