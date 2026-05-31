#!/usr/bin/env bash
# test-block-manufactured-action-menu.sh — coverage for the manufactured action-menu gate
#
# Synthesizes Claude Code PreToolUse(AskUserQuestion) payloads and asserts:
#   advisory → exit 0 + stderr non-empty  (default mode)
#   block    → exit 2 + stderr non-empty  (PRAXIS_BLOCK_MANUFACTURED_MENU_STRICT=1)
#   pass     → exit 0 + stderr empty
#
# Usage: bash hooks/test-block-manufactured-action-menu.sh
# Exit:  0 = all pass; 1 = at least one fail
#
# Hook is ADVISORY by default — "manufactured marker present + command signal
# in prior message" cases expect exit 0 + non-empty stderr.
# Strict cases (PRAXIS_BLOCK_MANUFACTURED_MENU_STRICT=1) expect exit 2.

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/block-manufactured-action-menu/impl.py"

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
# $2 = options JSON array (e.g., '["Plan A", "진행할까요"]')
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
      echo "$payload" | PRAXIS_BLOCK_MANUFACTURED_MENU_STRICT=1 "$HOOK" >/dev/null 2>"$err_file"
      ;;
    advisory|default|*)
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
# (a) ADVISORY cases — default mode, manufactured marker + command signal
# ---------------------------------------------------------------------------

T1=$(build_transcript "진행해주세요")
P1=$(build_payload "$T1" '["Plan A", "Plan B", "진행할까요"]')
run_case "korean command signal + korean manufactured marker → advisory" advisory default "$P1"

T2=$(build_transcript "go ahead and implement it")
P2=$(build_payload "$T2" '["Step 1", "Step 2", "proceed"]')
run_case "english command signal + proceed marker → advisory" advisory default "$P2"

T3=$(build_transcript "실행해줘")
P3=$(build_payload "$T3" '["Option A", "Option B", "계속할까요"]')
run_case "korean command + 계속할까요 → advisory" advisory default "$P3"

T4=$(build_transcript "merge it now")
P4=$(build_payload "$T4" '["Plan A", "머지할까요"]')
# Destructive label "머지할까요" triggers the destructive-confirmation
# exception even though command + manufactured marker both match.
run_case "english merge command + 머지할까요 → pass (destructive-exempt)" pass default "$P4"

T5=$(build_transcript "proceed with the implementation")
P5=$(build_payload "$T5" '["Plan A", "go ahead"]')
run_case "'proceed' in user message + 'go ahead' marker → advisory" advisory default "$P5"

T6=$(build_transcript "continue please")
P6=$(build_payload "$T6" '["Step A", "Step B", "continue"]')
run_case "'continue' command + 'continue' marker → advisory" advisory default "$P6"

T7=$(build_transcript "push the changes")
P7=$(build_payload "$T7" '["Plan A", "push할까요"]')
# Destructive label "push할까요" triggers the destructive-confirmation
# exception even though command + manufactured marker both match.
run_case "push command + push할까요 → pass (destructive-exempt)" pass default "$P7"

T8=$(build_transcript "다음 액션 진행해")
P8=$(build_payload "$T8" '["Step 1", "다음 액션"]')
run_case "진행 in message + 다음 액션 marker → advisory" advisory default "$P8"

# ---------------------------------------------------------------------------
# (b) BLOCK cases — strict mode, manufactured marker + command signal
# ---------------------------------------------------------------------------

T_s1=$(build_transcript "진행해주세요")
P_s1=$(build_payload "$T_s1" '["Plan A", "진행할까요"]')
run_case "strict mode + korean command + manufactured marker → block" block strict "$P_s1"

T_s2=$(build_transcript "go ahead")
P_s2=$(build_payload "$T_s2" '["Step 1", "proceed"]')
run_case "strict mode + go ahead + proceed marker → block" block strict "$P_s2"

T_s3=$(build_transcript "실행해줘")
P_s3=$(build_payload "$T_s3" '["Option A", "계속할까요"]')
run_case "strict mode + 실행 + 계속할까요 → block" block strict "$P_s3"

# ---------------------------------------------------------------------------
# (c) PASS cases — manufactured marker present but NO command signal in prior msg
# ---------------------------------------------------------------------------

# When there is no command signal in the prior user message, the manufactured
# menu may be legitimate (genuine first-time decision point).

T_p1=$(build_transcript "어떤 방식으로 구현할까요?")
P_p1=$(build_payload "$T_p1" '["Plan A", "Plan B", "진행할까요"]')
run_case "question user msg, no command → pass (legitimate menu)" pass default "$T_p1 $P_p1" && true
# Re-run with correct payload passing
run_case "question user msg, no command → pass" pass default "$P_p1"

T_p2=$(build_transcript "what options do we have?")
P_p2=$(build_payload "$T_p2" '["Option A", "Option B", "proceed"]')
run_case "query user msg, no command → pass" pass default "$P_p2"

T_p3=$(build_transcript "어떻게 처리하면 좋을까요")
P_p3=$(build_payload "$T_p3" '["방법 A", "방법 B", "계속할까요"]')
run_case "open-ended question, no command → pass" pass default "$P_p3"

# Empty transcript: no user message found → fail open
T_p4=$(build_transcript "")
P_p4=$(build_payload "$T_p4" '["Plan A", "진행할까요"]')
run_case "empty transcript + manufactured marker → pass (fail-open)" pass default "$P_p4"

# ---------------------------------------------------------------------------
# (d) PASS cases — no manufactured marker in options
# ---------------------------------------------------------------------------

T_nm1=$(build_transcript "진행해주세요")
P_nm1=$(build_payload "$T_nm1" '["이슈 생성", "PR 생성", "테스트 실행"]')
run_case "command signal but no manufactured marker → pass" pass default "$P_nm1"

T_nm2=$(build_transcript "go ahead")
P_nm2=$(build_payload "$T_nm2" '["Plan A", "Plan B", "Plan C"]')
run_case "go ahead but normal options only → pass" pass default "$P_nm2"

# ---------------------------------------------------------------------------
# (e) PASS cases — not AskUserQuestion tool
# ---------------------------------------------------------------------------

P_t1=$(python3 -c '
import json
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": "echo proceed"},
}))')
run_case "Bash tool passes through" pass default "$P_t1"

P_t2=$(python3 -c '
import json
print(json.dumps({
    "tool_name": "Edit",
    "tool_input": {"old_string": "진행할까요", "new_string": "proceed"},
}))')
run_case "Edit tool with marker in args passes through" pass default "$P_t2"

# ---------------------------------------------------------------------------
# (f) PASS cases — missing / unreadable transcript → fail-open
# ---------------------------------------------------------------------------

P_missing=$(build_payload "/nonexistent/transcript-$$.jsonl" '["Plan A", "진행할까요"]')
run_case "missing transcript file + manufactured marker → pass (fail-open)" pass default "$P_missing"

# ---------------------------------------------------------------------------
# (g) Graceful degrade — malformed payload pieces
# ---------------------------------------------------------------------------

run_case "malformed JSON payload → graceful exit 0" pass default "not even json"

P_noq=$(python3 -c '
import json
print(json.dumps({
    "tool_name": "AskUserQuestion",
    "tool_input": {},
}))')
run_case "AskUserQuestion with no questions → pass" pass default "$P_noq"

P_badopts=$(python3 -c '
import json
print(json.dumps({
    "tool_name": "AskUserQuestion",
    "tool_input": {"questions": [{"options": "not-a-list"}]},
}))')
run_case "questions with non-list options → pass" pass default "$P_badopts"

# ---------------------------------------------------------------------------
# (h) tool_result-only user entry must be skipped (same pattern as sibling)
# ---------------------------------------------------------------------------

build_tool_result_transcript() {
  local human_text="$1"
  local path="$WORK/transcript-tr-$$-$RANDOM.jsonl"
  python3 - "$human_text" > "$path" <<'PY'
import json, sys
human_text = sys.argv[1]
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

# tool_result-only most-recent entry, prior human message has command signal
T_tr1=$(build_tool_result_transcript "진행해주세요")
P_tr1=$(build_payload "$T_tr1" '["Step 1", "진행할까요"]')
run_case "[tool-result] skip tool_result entry, prior '진행해주세요' → advisory" advisory default "$P_tr1"

# tool_result-only most-recent, prior message has NO command signal
T_tr2=$(build_tool_result_transcript "어떤 방법이 좋을까요?")
P_tr2=$(build_payload "$T_tr2" '["Plan A", "진행할까요"]')
run_case "[tool-result] skip tool_result entry, prior msg has no command → pass" pass default "$P_tr2"

# ---------------------------------------------------------------------------
# (i) False-positive avoidance — legitimate work options must NOT trigger
# ---------------------------------------------------------------------------

# "continue" appearing inside a longer label phrase that is not a simple
# menu continuation option — substring check should still catch it, but
# let's verify that work options with unrelated text don't false-trigger.

T_fp1=$(build_transcript "진행")
P_fp1=$(build_payload "$T_fp1" '["이슈 생성", "PR 검토", "배포"]')
run_case "[false-pos] normal work options only, no manufactured marker → pass" pass default "$P_fp1"

# "progress" does NOT contain "proceed" as whole word
T_fp2=$(build_transcript "check progress")
P_fp2=$(build_payload "$T_fp2" '["Plan A", "continue monitoring"]')
# "continue monitoring" contains "continue" → this WILL trigger advisory
# This is expected behavior: "continue" is a manufactured marker
run_case "[false-pos] 'continue monitoring' label + 'check progress' user msg → pass (no command signal)" pass default "$P_fp2"

# Multi-question: marker only in second question
T_mq1=$(build_transcript "진행해줘")
P_mq1=$(python3 - <<PY
import json
print(json.dumps({
    "session_id": "test-session",
    "transcript_path": "$T_mq1",
    "tool_name": "AskUserQuestion",
    "tool_input": {
        "questions": [
            {
                "question": "A?",
                "options": [{"label": "yes"}, {"label": "no"}],
            },
            {
                "question": "B?",
                "options": [{"label": "Plan A"}, {"label": "진행할까요"}],
            },
        ]
    },
}))
PY
)
run_case "multi-question payload, marker in second question + command signal → advisory" advisory default "$P_mq1"

# ---------------------------------------------------------------------------
# (e) Destructive-confirmation exception — strict mode must pass when any
#     option label names a destructive / irreversible action (merge, push,
#     delete, drop, prod, force). The user's prior command does not absorb
#     per-action approval for shared-state mutations.
# ---------------------------------------------------------------------------

T_de1=$(build_transcript "머지해줘")
P_de1=$(build_payload "$T_de1" '["진행할까요", "머지할까요"]')
run_case "[destructive-exempt-KO] '머지할까요' label + cmd + strict → pass" pass strict "$P_de1"

T_de2=$(build_transcript "push the changes")
P_de2=$(build_payload "$T_de2" '["proceed", "push to main"]')
run_case "[destructive-exempt-EN] 'push to main' label + cmd + strict → pass" pass strict "$P_de2"

T_de3=$(build_transcript "삭제 진행")
P_de3=$(build_payload "$T_de3" '["진행할까요", "데이터 삭제 확정"]')
run_case "[destructive-exempt-KO] '삭제' label + cmd + strict → pass" pass strict "$P_de3"

T_de4=$(build_transcript "go ahead")
P_de4=$(build_payload "$T_de4" '["proceed", "force-push the rebase"]')
run_case "[destructive-exempt-EN] 'force-push' label + cmd + strict → pass" pass strict "$P_de4"

T_de5=$(build_transcript "prod 배포")
P_de5=$(build_payload "$T_de5" '["proceed", "prod deploy 확정"]')
run_case "[destructive-exempt-EN] 'prod' label + cmd + strict → pass" pass strict "$P_de5"

# Advisory mode also passes for destructive labels — the exception applies
# at the marker stage, before mode resolution.
T_de6=$(build_transcript "머지해줘")
P_de6=$(build_payload "$T_de6" '["진행할까요", "머지할까요"]')
run_case "[destructive-exempt-KO] '머지할까요' label + cmd + advisory → pass" pass default "$P_de6"

# ---------------------------------------------------------------------------
# (f) Status-query / question filter — messages that look like progress
#     checks or questions must NOT trigger command-signal detection,
#     even when an action verb appears as a substring.
# ---------------------------------------------------------------------------

T_sq1=$(build_transcript "진행 상황 알려줘")
P_sq1=$(build_payload "$T_sq1" '["Plan A", "진행할까요"]')
run_case "[status-query-KO] '진행 상황 알려줘' + marker → pass" pass default "$P_sq1"

T_sq2=$(build_transcript "where should we go from here?")
P_sq2=$(build_payload "$T_sq2" '["Plan A", "proceed"]')
run_case "[status-query-EN] 'where should we go from here?' + marker → pass" pass default "$P_sq2"

T_sq3=$(build_transcript "어디까지 진행됐어?")
P_sq3=$(build_payload "$T_sq3" '["Plan A", "계속할까요"]')
run_case "[status-query-KO] '어디까지 진행됐어?' + marker → pass" pass default "$P_sq3"

T_sq4=$(build_transcript "should we continue?")
P_sq4=$(build_payload "$T_sq4" '["Plan A", "continue"]')
run_case "[status-query-EN] 'should we continue?' + marker → pass" pass default "$P_sq4"

# ---------------------------------------------------------------------------
# (g) Destructive-token word-boundary — `prod` substring must NOT
#     match `Product plan` / `production-ready docs`.
# ---------------------------------------------------------------------------

T_wb1=$(build_transcript "go ahead")
P_wb1=$(build_payload "$T_wb1" '["proceed", "Product plan"]')
run_case "[wb] 'Product plan' should NOT trigger prod-exempt + cmd + strict → block" block strict "$P_wb1"

T_wb2=$(build_transcript "go ahead")
P_wb2=$(build_payload "$T_wb2" '["proceed", "production-ready docs"]')
run_case "[wb] 'production-ready docs' should NOT trigger prod-exempt + cmd + strict → block" block strict "$P_wb2"

# Sanity: standalone `prod` word still triggers exempt.
T_wb3=$(build_transcript "go ahead")
P_wb3=$(build_payload "$T_wb3" '["proceed", "prod deploy"]')
run_case "[wb] standalone 'prod deploy' triggers exempt + cmd + strict → pass" pass strict "$P_wb3"

# ---------------------------------------------------------------------------
# (h) Negation guard — explicit "don't / 진행하지 마" must NOT register
#     as command-intent even though the action verb appears.
# ---------------------------------------------------------------------------

T_ng1=$(build_transcript "don't proceed yet")
P_ng1=$(build_payload "$T_ng1" '["Plan A", "proceed"]')
run_case "[negation-EN] \"don't proceed yet\" + 'proceed' → pass" pass default "$P_ng1"

T_ng2=$(build_transcript "do not continue")
P_ng2=$(build_payload "$T_ng2" '["Plan A", "continue"]')
run_case "[negation-EN] 'do not continue' + 'continue' → pass" pass default "$P_ng2"

T_ng3=$(build_transcript "진행하지 마")
P_ng3=$(build_payload "$T_ng3" '["Plan A", "진행할까요"]')
run_case "[negation-KO] '진행하지 마' + '진행할까요' → pass" pass default "$P_ng3"

T_ng4=$(build_transcript "계속하지 말아줘")
P_ng4=$(build_payload "$T_ng4" '["Plan A", "계속할까요"]')
run_case "[negation-KO] '계속하지 말아줘' + '계속할까요' → pass" pass default "$P_ng4"

# ---------------------------------------------------------------------------
# (i) Korean signal `계속` — must be recognized as command-intent so
#     `계속할까요` manufactured menu is detected.
# ---------------------------------------------------------------------------

T_ks1=$(build_transcript "계속해")
P_ks1=$(build_payload "$T_ks1" '["Plan A", "계속할까요"]')
run_case "[KO-signal] '계속해' + '계속할까요' → advisory" advisory default "$P_ks1"

T_ks2=$(build_transcript "계속 진행")
P_ks2=$(build_payload "$T_ks2" '["Plan A", "계속할까요"]')
run_case "[KO-signal] '계속 진행' + '계속할까요' → advisory" advisory default "$P_ks2"

# ---------------------------------------------------------------------------
# (j) Affirmative-form markers — option labels that restate the directive as
#     "do exactly what you said" ("그대로 진행", "execute now"). Same anti-
#     pattern as question-form, detected from the option-label side.
# ---------------------------------------------------------------------------

T_af1=$(build_transcript "진행해줘")
P_af1=$(build_payload "$T_af1" '["다른 방식", "그대로 진행"]')
run_case "[affirmative-KO] '진행해줘' + '그대로 진행' label → advisory" advisory default "$P_af1"

T_af2=$(build_transcript "실행해줘")
P_af2=$(build_payload "$T_af2" '["방식 변경", "그대로 생성"]')
run_case "[affirmative-KO] '실행해줘' + '그대로 생성' label → advisory" advisory default "$P_af2"

T_af3=$(build_transcript "go ahead and do it")
P_af3=$(build_payload "$T_af3" '["Revise approach", "execute now"]')
run_case "[affirmative-EN] 'go ahead' + 'execute now' label → advisory" advisory default "$P_af3"

T_af4=$(build_transcript "proceed with the plan")
P_af4=$(build_payload "$T_af4" '["Alternative", "implement as instructed"]')
run_case "[affirmative-EN] 'proceed' + 'as instructed' label → advisory" advisory default "$P_af4"

# Strict mode — affirmative marker blocks like the question-form markers.
T_af5=$(build_transcript "진행해줘")
P_af5=$(build_payload "$T_af5" '["다른 방식", "그대로 진행"]')
run_case "[affirmative-KO] strict + '진행해줘' + '그대로 진행' → block" block strict "$P_af5"

# No command signal — affirmative marker alone is a legitimate menu.
T_af6=$(build_transcript "어떤 방식이 좋을까요?")
P_af6=$(build_payload "$T_af6" '["방식 A", "그대로 진행"]')
run_case "[affirmative-KO] no command signal + '그대로 진행' → pass" pass default "$P_af6"

# Status-query message — affirmative marker must not fire on a progress check.
T_af7=$(build_transcript "진행 상황 알려줘")
P_af7=$(build_payload "$T_af7" '["방식 A", "그대로 진행"]')
run_case "[affirmative-KO] status query + '그대로 진행' → pass" pass default "$P_af7"

# Negated directive — affirmative marker must not fire.
T_af8=$(build_transcript "진행하지 마")
P_af8=$(build_payload "$T_af8" '["방식 A", "그대로 진행"]')
run_case "[affirmative-KO] negated '진행하지 마' + '그대로 진행' → pass" pass default "$P_af8"

# Destructive affirmative label — destructive-confirmation exception applies.
# Label carries both the affirmative marker ("그대로 진행") and a destructive
# token ("머지"), so the marker matches AND the destructive exception fires.
T_af9=$(build_transcript "머지해줘")
P_af9=$(build_payload "$T_af9" '["검토 더 하기", "그대로 진행 (머지 확정)"]')
run_case "[affirmative-KO] '그대로 진행 (머지 확정)' label (destructive) + cmd → pass" pass default "$P_af9"

# False-positive avoidance — "그대로 둬" (leave as-is) is NOT an affirmative
# marker; "그대로 진행" must not match it.
T_af10=$(build_transcript "진행해줘")
P_af10=$(build_payload "$T_af10" '["그대로 둬", "방식 변경"]')
run_case "[affirmative-KO] 'leave as-is' label, no marker → pass" pass default "$P_af10"

# ---------------------------------------------------------------------------
# (k) Execute-family command signals — directives that naturally pair with the
#     affirmative-form markers ("execute now", "as instructed"). Without these
#     in COMMAND_SIGNALS_EN_TOKENS the affirmative markers stay half-wired.
# ---------------------------------------------------------------------------

T_ef1=$(build_transcript "execute it")
P_ef1=$(build_payload "$T_ef1" '["revise", "execute now"]')
run_case "[execute-family] 'execute it' + 'execute now' label → advisory" advisory default "$P_ef1"

T_ef2=$(build_transcript "run it")
P_ef2=$(build_payload "$T_ef2" '["alternative", "do it as instructed"]')
run_case "[execute-family] 'run it' + 'as instructed' label → advisory" advisory default "$P_ef2"

T_ef3=$(build_transcript "implement the change")
P_ef3=$(build_payload "$T_ef3" '["방식 변경", "그대로 진행"]')
run_case "[execute-family] 'implement' + '그대로 진행' label → advisory" advisory default "$P_ef3"

# Strict mode — execute-family signal blocks like other command signals.
T_ef4=$(build_transcript "execute it")
P_ef4=$(build_payload "$T_ef4" '["revise", "execute now"]')
run_case "[execute-family] strict + 'execute it' + 'execute now' → block" block strict "$P_ef4"

# Negation guard — "don't execute" must not register as command-intent.
T_ef5=$(build_transcript "don't execute that yet")
P_ef5=$(build_payload "$T_ef5" '["revise", "execute now"]')
run_case "[execute-family] negated \"don't execute\" + 'execute now' → pass" pass default "$P_ef5"

# False-positive guard — bare noun "run" in a non-directive status statement
# must NOT register as command-intent. `run it` is a phrase, so "test run
# completed" / "the run failed" do not match. Strict mode would otherwise
# block a legitimate follow-up menu.
T_ef6=$(build_transcript "test run completed")
P_ef6=$(build_payload "$T_ef6" '["revise", "execute now"]')
run_case "[execute-family] 'test run completed' (non-directive) + marker + strict → block-free pass" pass strict "$P_ef6"

T_ef7=$(build_transcript "the run failed")
P_ef7=$(build_payload "$T_ef7" '["revise", "as instructed"]')
run_case "[execute-family] 'the run failed' (non-directive) + marker → pass" pass default "$P_ef7"

# ---------------------------------------------------------------------------
# (l) Defect 2 (issue #515) — extended Korean negation forms (conditional /
#     declarative, beyond "하지 마/말") must NOT register as command-intent.
# ---------------------------------------------------------------------------

T_d2a=$(build_transcript "진행하면 안 됩니다")
P_d2a=$(build_payload "$T_d2a" '["Plan A", "진행할까요"]')
run_case "[#515-d2] '진행하면 안 됩니다' (conditional negation) → pass" pass default "$P_d2a"

T_d2b=$(build_transcript "진행하지 않습니다")
P_d2b=$(build_payload "$T_d2b" '["Plan A", "진행할까요"]')
run_case "[#515-d2] '진행하지 않습니다' (declarative negation) → pass" pass default "$P_d2b"

T_d2c=$(build_transcript "진행 안 됩니다")
P_d2c=$(build_payload "$T_d2c" '["Plan A", "진행할까요"]')
run_case "[#515-d2] '진행 안 됩니다' (안 됩니다 negation) → pass" pass default "$P_d2c"

T_d2d=$(build_transcript "계속하면 안 돼")
P_d2d=$(build_payload "$T_d2d" '["Plan A", "계속할까요"]')
run_case "[#515-d2] '계속하면 안 돼' (안 돼 negation) → pass" pass default "$P_d2d"

T_d2e=$(build_transcript "머지하지 않습니다")
P_d2e=$(build_payload "$T_d2e" '["Plan A", "진행할까요"]')
run_case "[#515-d2] '머지하지 않습니다' (declarative negation) → pass" pass default "$P_d2e"

# Genuine positive directive must STILL advisory (negation guard didn't
# swallow legitimate command-intent).
T_d2f=$(build_transcript "진행하면 됩니다")
P_d2f=$(build_payload "$T_d2f" '["Plan A", "진행할까요"]')
run_case "[#515-d2+] '진행하면 됩니다' (affirmative) → advisory" advisory default "$P_d2f"

# Strict-mode variant of the negation guard.
T_d2g=$(build_transcript "진행하면 안 됩니다")
P_d2g=$(build_payload "$T_d2g" '["Plan A", "진행할까요"]')
run_case "[#515-d2] strict + '진행하면 안 됩니다' → pass" pass strict "$P_d2g"

# ---------------------------------------------------------------------------
# (m) Defect 2 (issue #515) — English marker word-boundary: substring matches
#     ("Discontinue" → "continue") are rejected; real markers still match.
# ---------------------------------------------------------------------------

# "Discontinue support" contains "continue" as a substring only — NOT a
# manufactured marker. No marker → pass regardless of command signal.
T_d2h=$(build_transcript "go ahead")
P_d2h=$(build_payload "$T_d2h" '["Discontinue support for v1", "Keep v1"]')
run_case "[#515-d2] 'Discontinue support' is NOT a 'continue' marker → pass" pass default "$P_d2h"

# "unprocessed" embeds "proceed"-ish letters but not the token; sanity check
# that an alternative label naming a real noun does not false-match.
T_d2i=$(build_transcript "proceed")
P_d2i=$(build_payload "$T_d2i" '["Reprocess the queue", "Skip the queue"]')
run_case "[#515-d2] 'Reprocess'/'process' label is NOT a 'proceed' marker → pass" pass default "$P_d2i"

# Genuine English markers must STILL match (word-boundary positive).
T_d2j=$(build_transcript "go ahead and implement it")
P_d2j=$(build_payload "$T_d2j" '["Step 1", "proceed"]')
run_case "[#515-d2+] standalone 'proceed' marker still matches → advisory" advisory default "$P_d2j"

T_d2k=$(build_transcript "continue please")
P_d2k=$(build_payload "$T_d2k" '["Step A", "continue"]')
run_case "[#515-d2+] standalone 'continue' marker still matches → advisory" advisory default "$P_d2k"

# Mixed-script label — "proceed" followed by Korean must still match
# (lookaround rejects only an ASCII-letter neighbour).
T_d2l=$(build_transcript "진행해주세요")
P_d2l=$(build_payload "$T_d2l" '["다른 방식", "proceed 합니다"]')
run_case "[#515-d2+] mixed-script 'proceed 합니다' marker still matches → advisory" advisory default "$P_d2l"

# ---------------------------------------------------------------------------
# Summary
# Fail-open guard opt-in (issue #498): main() must be @fail_open-wrapped;
# guard behavior is tested centrally in tests/test_hook_runtime.sh.
_failopen_out=$(python3 - << PYEOF 2>&1
import importlib.util
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
assert getattr(mod.main, "__wrapped__", None) is not None, "main is not @fail_open-wrapped"
print("OK")
PYEOF
)
_failopen_rc=$?
if [ "$_failopen_rc" -eq 0 ] && [ "$_failopen_out" = "OK" ]; then
  echo "PASS  [fail-open] main() is wrapped by the shared @fail_open guard"
  PASS=$((PASS+1))
else
  echo "FAIL  [fail-open] main() not @fail_open-wrapped (rc=$_failopen_rc out=$_failopen_out)"
  FAIL=$((FAIL+1)); FAILED_NAMES+=("fail-open guard wrapping")
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
