#!/bin/bash
# Tests for completion-verify/retraction-probe-advisory (Stop hook, issue #1442).
set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/completion-verify/retraction-probe-advisory/impl.py"

unset PRAXIS_RETRACTION_PROBE_BYPASS

PASS=0
FAIL=0

# build_transcript <final_text> [tool_output] — when tool_output is given, a
# Bash tool_use and its tool_result carrying that output precede the final text.
build_transcript() {
  local final_text="$1" tool_output="${2-__none__}"
  TRANSCRIPT="$(mktemp)"
  python3 - "$TRANSCRIPT" "$final_text" "$tool_output" <<'PY'
import json, sys
path, final_text, tool_output = sys.argv[1:4]
events = [{"message": {"role": "user", "content": "the command ran fine"}}]
if tool_output != "__none__":
    events.append({"message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "Bash",
         "input": {"command": "gh issue view 1 --json state,labels"}}]}})
    events.append({"message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": tool_output}]}})
events.append({"message": {"role": "assistant",
                           "content": [{"type": "text", "text": final_text}]}})
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
assert "[retraction-probe-advisory]" in d["systemMessage"], d
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

OUTPUT='{"state":"OPEN","labels":[{"name":"release-requested"}]}
exit=0'
RETRACTION='제 판단이 틀렸습니다. 권한 경로가 하나 더 있었습니다.'

# =====================================================================
# Issue acceptance
# =====================================================================

build_transcript "$RETRACTION" "$OUTPUT"
run_case advisory "retraction, Bash ran, no output quoted" '{}'

build_transcript "제 판단이 틀렸습니다. 조회 결과는 다음과 같습니다:
{\"state\":\"OPEN\",\"labels\":[{\"name\":\"release-requested\"}]}" "$OUTPUT"
run_case silent "retraction quotes a line of this turn's output" '{}'

build_transcript "> 제 판단이 틀렸습니다

위 문장은 이전 세션의 기록입니다." "$OUTPUT"
run_case silent "retraction inside a > quote" '{}'

build_transcript "제 판단이 틀렸습니까?" "$OUTPUT"
run_case silent "retraction phrased as a question" '{}'

build_transcript "명령이 실행됐고 라벨이 붙었습니다." "$OUTPUT"
run_case silent "no retraction vocabulary" '{}'

# =====================================================================
# Predicate boundaries
# =====================================================================

build_transcript "$RETRACTION"
run_case silent "retraction with no tool call this turn" '{}'

build_transcript "My judgement was wrong: there is an allow path I did not read." "$OUTPUT"
run_case advisory "English retraction" '{}'

build_transcript "정정합니다. exit=0 이었습니다." "$OUTPUT"
run_case advisory "an output line shorter than the quote minimum does not count" '{}'

build_transcript "앵커를 rev 5 로 정정했고 공지를 올렸습니다." "$OUTPUT"
run_case silent "reporting correction work is not a retraction" '{}'

build_transcript "명령이 실행됐고 라벨이 붙었습니다." "$OUTPUT"
run_case advisory "payload last_assistant_message is the text graded" \
  "{\"last_assistant_message\": \"$RETRACTION\"}"

# =====================================================================
# Guards and fail-open
# =====================================================================

build_transcript "$RETRACTION" "$OUTPUT"
run_case silent "bypass env" '{}' PRAXIS_RETRACTION_PROBE_BYPASS=1

build_transcript "$RETRACTION" "$OUTPUT"
run_case silent "stop_hook_active guard" '{"stop_hook_active": true}'

TRANSCRIPT="/nonexistent/transcript-$$.jsonl"
run_case silent "missing transcript" '{}'

echo ""
echo "== $PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
