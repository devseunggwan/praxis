#!/usr/bin/env bash
# test_block_guard_removal_menu.sh — coverage for the guard-removal menu gate
#
# Synthesizes PreToolUse(AskUserQuestion) payloads plus a transcript and asserts:
#   advisory → exit 0 + stderr non-empty + additionalContext on stdout (default)
#   block    → exit 2 + stderr non-empty  (PRAXIS_GUARD_REMOVAL_MENU_STRICT=1)
#   pass     → exit 0 + stderr empty + stdout empty
#
# Transcript scenarios (argument 1 of build_transcript):
#   blocked       user msg → assistant Write → hook denial (permission-rule)
#   blocked-env   same, and the denial prints `Bypass (if truly needed): GATE_BYPASS=1`
#   user-reject   same, but the denial kind is user-rejected, not a hook
#   ok-result     user msg → assistant Write → ordinary (non-error) tool_result
#   stale-block   blocked, then a NEW real user message (block is in an earlier turn)
#   none          user msg only
#
# Usage: bash tests/hooks/preflight-gate/test_block_guard_removal_menu.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/block-guard-removal-menu/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()
WORK=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
trap 'rm -rf "$WORK"' EXIT

build_transcript() {
  local scenario="$1"
  local path="$WORK/transcript-$RANDOM-$RANDOM.jsonl"
  python3 - "$scenario" "$path" <<'PY'
import json, sys

scenario, path = sys.argv[1], sys.argv[2]
events = [{"type": "user", "uuid": "u0",
           "message": {"role": "user", "content": "write the three docs files"}}]
if scenario != "none":
    events.append({"type": "assistant", "uuid": "a1", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_w1", "name": "Write",
         "input": {"file_path": "docs/a.md", "content": "x"}}]}})
if scenario in ("blocked", "blocked-env", "stale-block"):
    reason = ("PreToolUse:Write hook error: [impl.py]: protected branch: "
              "edits on main are refused. Correct path: create an issue worktree.")
    if scenario == "blocked-env":
        reason += "\nBypass (if truly needed): GATE_BYPASS=1 with a one-line reason"
    events.append({"type": "user", "uuid": "u1", "toolDenialKind": "permission-rule",
                   "sourceToolAssistantUUID": "a1",
                   "message": {"role": "user", "content": [
                       {"type": "tool_result", "tool_use_id": "toolu_w1",
                        "is_error": True, "content": reason}]}})
elif scenario == "user-reject":
    events.append({"type": "user", "uuid": "u1", "toolDenialKind": "user-rejected",
                   "sourceToolAssistantUUID": "a1",
                   "message": {"role": "user", "content": [
                       {"type": "tool_result", "tool_use_id": "toolu_w1", "is_error": True,
                        "content": "The user doesn't want to proceed with this tool use."}]}})
elif scenario == "ok-result":
    events.append({"type": "user", "uuid": "u1", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_w1", "content": "File created"}]}})
if scenario == "stale-block":
    events.append({"type": "user", "uuid": "u2",
                   "message": {"role": "user", "content": "ok, what are the options now?"}})
with open(path, "w", encoding="utf-8") as f:
    for ev in events:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
PY
  echo "$path"
}

# $1 = transcript_path, $2 = options JSON array of [label, description] pairs
build_payload() {
  python3 - "$1" "$2" <<'PY'
import json, sys
transcript, options = sys.argv[1], json.loads(sys.argv[2])
print(json.dumps({
    "session_id": "test-session",
    "transcript_path": transcript,
    "tool_name": "AskUserQuestion",
    "tool_input": {"questions": [{
        "question": "How should we continue?", "header": "Next", "multiSelect": False,
        "options": [{"label": lab, "description": desc} for lab, desc in options],
    }]},
    "cwd": "/tmp",
}, ensure_ascii=False))
PY
}

run_case() {
  local name="$1" expected="$2" mode="$3" payload="$4"
  local err_file out_file rc err_content out_content ok=1
  err_file=$(mktemp); out_file=$(mktemp)
  if [ "$mode" = strict ]; then
    printf '%s' "$payload" | PRAXIS_GUARD_REMOVAL_MENU_STRICT=1 "$HOOK" >"$out_file" 2>"$err_file"
  elif [ "${mode#strict=}" != "$mode" ]; then
    printf '%s' "$payload" | PRAXIS_GUARD_REMOVAL_MENU_STRICT="${mode#strict=}" "$HOOK" >"$out_file" 2>"$err_file"
  else
    printf '%s' "$payload" | env -u PRAXIS_GUARD_REMOVAL_MENU_STRICT "$HOOK" >"$out_file" 2>"$err_file"
  fi
  rc=$?
  err_content=$(cat "$err_file"); out_content=$(cat "$out_file"); rm -f "$err_file" "$out_file"
  case "$expected" in
    advisory)
      [ "$rc" -eq 0 ] && [ -n "$err_content" ] \
        && printf '%s' "$out_content" | grep -q '"additionalContext"' || ok=0 ;;
    block) [ "$rc" -eq 2 ] && [ -n "$err_content" ] || ok=0 ;;
    pass) [ "$rc" -eq 0 ] && [ -z "$err_content" ] && [ -z "$out_content" ] || ok=0 ;;
    *) ok=0 ;;
  esac
  if [ "$ok" -eq 1 ]; then
    echo "PASS [$expected] $name"; PASS=$((PASS+1))
  else
    echo "FAIL [$expected] $name (rc=$rc stderr=${err_content:0:120} stdout=${out_content:0:80})"
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

T_BLOCKED=$(build_transcript blocked)
T_BLOCKED_ENV=$(build_transcript blocked-env)
T_USER_REJECT=$(build_transcript user-reject)
T_OK=$(build_transcript ok-result)
T_STALE=$(build_transcript stale-block)
T_NONE=$(build_transcript none)

KO_EXEMPTION='[["훅 예외 설정 (권장)", "settings에 예외를 추가합니다"], ["중단", "여기서 멈춥니다"]]'
EN_EXEMPTION='[["Add a hook exception (Recommended)", "Let docs writes through"], ["Stop", "Leave it"]]'
NORMAL_MENU='[["Create an issue worktree", "Cut a branch and write there"], ["Use the project CLI", "It opens the worktree for you"]]'

echo "--- positive: blocked in this turn, then a guard-removal option ---"
run_case "KO label 훅 예외 설정 after block" advisory default "$(build_payload "$T_BLOCKED" "$KO_EXEMPTION")"
run_case "KO label 훅 예외 설정 after block (strict)" block strict "$(build_payload "$T_BLOCKED" "$KO_EXEMPTION")"
run_case "EN label Add a hook exception after block" advisory default "$(build_payload "$T_BLOCKED" "$EN_EXEMPTION")"
run_case "EN label Add a hook exception after block (strict)" block strict "$(build_payload "$T_BLOCKED" "$EN_EXEMPTION")"
run_case "EN disable the hook" advisory default "$(build_payload "$T_BLOCKED" '[["Disable the hook", ""], ["Stop", ""]]')"
run_case "EN bypass the guard" advisory default "$(build_payload "$T_BLOCKED" '[["Bypass the guard for now", ""], ["Stop", ""]]')"
run_case "EN allow direct write to main" advisory default "$(build_payload "$T_BLOCKED" '[["Allow direct write to main", ""], ["Stop", ""]]')"
run_case "EN originated env SOME_BYPASS=1" advisory default "$(build_payload "$T_BLOCKED" '[["Set SOME_BYPASS=1 and retry", ""], ["Stop", ""]]')"
run_case "EN --no-verify" advisory default "$(build_payload "$T_BLOCKED" '[["Commit with --no-verify", ""], ["Stop", ""]]')"
run_case "mixed-script hook bypass하기" advisory default "$(build_payload "$T_BLOCKED" '[["hook bypass하기", ""], ["Stop", ""]]')"
run_case "KO 훅 비활성화" advisory default "$(build_payload "$T_BLOCKED" '[["훅 비활성화", ""], ["중단", ""]]')"
run_case "KO 가드 우회" advisory default "$(build_payload "$T_BLOCKED" '[["가드 우회 후 재시도", ""], ["중단", ""]]')"
run_case "KO 예외 추가 in description only" advisory default "$(build_payload "$T_BLOCKED" '[["설정 변경", "보호 브랜치 예외 추가"], ["중단", ""]]')"
run_case "KO 가드 우회하고 진행 (verb suffix is not negation)" advisory default "$(build_payload "$T_BLOCKED" '[["이 편집만 가드 우회하고 진행 (권장)", ""], ["중단", ""]]')"
run_case "KO negated marker earlier, plain marker later" advisory default "$(build_payload "$T_BLOCKED" '[["훅 우회 없음이 아니라 훅 우회 후 진행", ""], ["중단", ""]]')"
run_case "EN add an exception to the hook (guard, not code)" advisory default "$(build_payload "$T_BLOCKED" '[["Add an exception to the hook", ""], ["Stop", ""]]')"
run_case "STRICT=no stays advisory" advisory strict=no "$(build_payload "$T_BLOCKED" "$EN_EXEMPTION")"
run_case "STRICT=off stays advisory" advisory strict=off "$(build_payload "$T_BLOCKED" "$EN_EXEMPTION")"
run_case "relay + origination: gate env relayed but another var originated" advisory default "$(build_payload "$T_BLOCKED_ENV" '[["Set OTHER_SKIP=1", ""], ["Stop", ""]]')"

echo "--- negatives ---"
run_case "exemption option, no prior block (none)" pass default "$(build_payload "$T_NONE" "$EN_EXEMPTION")"
run_case "exemption option, no prior block (KO)" pass default "$(build_payload "$T_NONE" "$KO_EXEMPTION")"
run_case "exemption option, tool ran fine" pass default "$(build_payload "$T_OK" "$EN_EXEMPTION")"
run_case "exemption option, user-rejected (not a hook block)" pass default "$(build_payload "$T_USER_REJECT" "$EN_EXEMPTION")"
run_case "exemption option, block was in an earlier turn" pass strict "$(build_payload "$T_STALE" "$EN_EXEMPTION")"
run_case "prior block, normal menu (satisfying paths)" pass strict "$(build_payload "$T_BLOCKED" "$NORMAL_MENU")"
run_case "prior block, KO normal menu" pass strict "$(build_payload "$T_BLOCKED" '[["이슈 워크트리 생성", "브랜치를 따서 작성"], ["훅 설정 확인", "메시지를 다시 읽기"]]')"
run_case "prior block, relay of the gate's own GATE_BYPASS=1" pass strict "$(build_payload "$T_BLOCKED_ENV" '[["Use the worktree", ""], ["GATE_BYPASS=1 (the gate offered this)", ""]]')"
run_case "prior block, KO negated 훅 우회 없음" pass strict "$(build_payload "$T_BLOCKED" '[["직전 배치와 동일한 전례, 훅 우회 없음", ""], ["중단", ""]]')"
run_case "prior block, KO negated 가드 우회하지 않고" pass strict "$(build_payload "$T_BLOCKED" '[["가드 우회하지 않고 워크트리로 진행", ""], ["중단", ""]]')"
run_case "prior block, add an exception handler (code)" pass strict "$(build_payload "$T_BLOCKED" '[["Add an exception handler", ""], ["Refactor", ""]]')"
run_case "prior block, add an exception to the error handler (code)" pass strict "$(build_payload "$T_BLOCKED" '[["Add an exception to the error handler", ""], ["Refactor", ""]]')"
run_case "prior block, skip tests (no guard noun)" pass strict "$(build_payload "$T_BLOCKED" '[["Skip the slow tests", ""], ["Run all", ""]]')"
run_case "non-AskUserQuestion tool" pass strict '{"tool_name":"Bash","tool_input":{"command":"ls"}}'
run_case "missing transcript" pass strict "$(build_payload "$WORK/nope.jsonl" "$EN_EXEMPTION")"
run_case "malformed payload" pass strict 'not json'

echo
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  printf '  - %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
