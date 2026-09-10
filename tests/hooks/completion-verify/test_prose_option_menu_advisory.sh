#!/bin/bash
# Tests for completion-verify/prose-option-menu-advisory (Stop hook, issue #1405).
set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/completion-verify/prose-option-menu-advisory/impl.py"

unset PRAXIS_PROSE_OPTION_MENU_BYPASS

PASS=0
FAIL=0

# build_transcript <final_text> [tool_name] — an assistant tool_use block is
# inserted before the final text when tool_name is given, so the AskUserQuestion
# carve-out can be exercised against a real this-turn tool call.
build_transcript() {
  local final_text="$1" tool_name="${2:-}"
  TRANSCRIPT="$(mktemp)"
  python3 - "$TRANSCRIPT" "$final_text" "$tool_name" <<'PY'
import json, sys
path, final_text, tool_name = sys.argv[1:4]
events = [{"message": {"role": "user", "content": "please wrap up"}}]
if tool_name:
    events.append({
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": tool_name, "input": {}}],
        },
    })
    events.append({
        "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
    })
events.append({
    "message": {"role": "assistant",
                "content": [{"type": "text", "text": final_text}]},
})
with open(path, "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
}

# run_case <advisory|silent> <name> <stop_payload_extra_json> [ENV=v ...]
run_case() {
  local expected="$1" name="$2" extra="$3"
  shift 3
  local payload out err rc ok=1
  payload=$(python3 -c 'import json,sys
p={"transcript_path":sys.argv[1]}
p.update(json.loads(sys.argv[2]))
print(json.dumps(p))' "$TRANSCRIPT" "$extra")
  local err_file
  err_file=$(mktemp)
  out=$(printf '%s' "$payload" | env "$@" python3 "$HOOK" 2>"$err_file")
  rc=$?
  err=$(cat "$err_file"); rm -f "$err_file"
  case "$expected" in
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert "[prose-option-menu-advisory]" in d["systemMessage"], d
assert "decision" not in d, d
' || ok=0
      ;;
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      [ -z "$out" ] || ok=0
      ;;
  esac
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected rc=$rc out=<$out> err=<$err>"; FAIL=$((FAIL + 1))
  fi
}

MENU='### 선택지

(a) 기존 커넥터를 그대로 두고 ingestion 만 붙인다
(b) 커넥터를 폐지하고 ingestion 으로 일원화한다

어느 쪽으로 갈지 정해 주시면 그대로 작업하겠습니다.'

# =====================================================================
# CORE
# =====================================================================

build_transcript "$MENU"
run_case advisory "adjacent (a)/(b) + choice demand, no tool call" '{}'

build_transcript "$MENU" "AskUserQuestion"
run_case silent "same menu routed through AskUserQuestion" '{}'

build_transcript "$MENU"
run_case silent "bypass env" '{}' PRAXIS_PROSE_OPTION_MENU_BYPASS=1

build_transcript "$MENU"
run_case silent "stop_hook_active guard" '{"stop_hook_active": true}'

# =====================================================================
# Predicate boundaries
# =====================================================================

build_transcript "### 정리

(a) 기존 커넥터는 그대로 둡니다
(b) ingestion 은 SOLUTION 버킷에 합류합니다

두 항목 모두 반영했습니다."
run_case silent "option labels with no choice demand" '{}'

build_transcript "커넥터 폐지 없이 진행했습니다. 어느 쪽 로그를 볼지 알려주세요."
run_case silent "choice demand with no option labels" '{}'

build_transcript "(a) 첫 항목입니다.
줄1
줄2
줄3
줄4
줄5
줄6
줄7
줄8
줄9
줄10
줄11
(b) 한참 뒤의 다른 항목입니다.

어느 쪽인지 알려주세요."
run_case silent "labels farther apart than the menu window" '{}'

build_transcript "- **(a)** 원가만 push 로 받는다
- **(b)** 매칭까지 push 로 받는다

Which one do you want?"
run_case advisory "bulleted, bolded labels with an English demand" '{}'

# =====================================================================
# Fail-open
# =====================================================================

TRANSCRIPT="$(mktemp)"
python3 -c '
import json, sys
with open(sys.argv[1], "w") as f:
    f.write(json.dumps({"message": {"role": "user", "content": "wrap up"}}) + "\n")
' "$TRANSCRIPT"
run_case silent "no last assistant text" '{}'

TRANSCRIPT="/nonexistent/transcript-$$.jsonl"
run_case silent "missing transcript" '{}'

echo ""
echo "== $PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
