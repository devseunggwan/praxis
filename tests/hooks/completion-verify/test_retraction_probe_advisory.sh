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

# The same exemption without the mark. `건가요` is how a question ABOUT a
# retraction ends, so the line carries the vocabulary while asserting nothing.
build_transcript "제가 앞에서 틀렸습니다라고 한 건가요" "$OUTPUT"
run_case silent "a 건가요 question carrying the vocabulary" '{}'

# Control for the row above: the same sentence as a statement still advises,
# so the silence is the ending and not the words around it.
build_transcript "제가 앞에서 틀렸습니다" "$OUTPUT"
run_case advisory "the same words as a statement still advise" '{}'

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
# Sidechain isolation — a delegated agent's events are not the main
# chain's evidence. A main-session turn carries them inline, marked
# `isSidechain`, and both counts the hook makes are claims about the
# retracting agent.
# =====================================================================

# build_sidechain_transcript <final_text> <sidechain_output> [main_output]
# The tool_use/tool_result pair carrying <sidechain_output> is marked
# isSidechain; <main_output>, when given, gets an unmarked pair as well.
build_sidechain_transcript() {
  local final_text="$1" side_output="$2" main_output="${3-__none__}"
  TRANSCRIPT="$(mktemp)"
  python3 - "$TRANSCRIPT" "$final_text" "$side_output" "$main_output" <<'PY'
import json, sys
path, final_text, side_output, main_output = sys.argv[1:5]
events = [{"message": {"role": "user", "content": "the command ran fine"}}]


def pair(tuid, output, sidechain):
    use = {"message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tuid, "name": "Bash",
         "input": {"command": "gh pr view 1 --json state"}}]}}
    res = {"message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tuid, "content": output}]}}
    if sidechain:
        use["isSidechain"] = True
        res["isSidechain"] = True
    return [use, res]


if main_output != "__none__":
    events += pair("t_main", main_output, False)
events += pair("t_side", side_output, True)
events.append({"message": {"role": "assistant",
                           "content": [{"type": "text", "text": final_text}]}})
with open(path, "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
}

SIDE_OUTPUT="state=MERGED  mergedAt=2026-09-20T01:28:38Z"
MAIN_OUTPUT="headRefOid=1b11401eb32e33f0aca69b641754941c49ccadf5"

# False positive: the main chain ran nothing. Without the filter the
# subagent's tool_use is counted and the advisory fires on a turn whose
# retracting agent never ran a probe at all — the "no tool call this
# turn" case above is the control that says this must stay silent.
build_sidechain_transcript "$RETRACTION" "$SIDE_OUTPUT"
run_case silent "a sidechain tool call is not the main chain's probe" '{}'

# False negative: the main chain did run a probe, and the retraction
# quotes the SUBAGENT's output instead. Quoting someone else's
# measurement is not evidence the retracting agent measured anything.
build_sidechain_transcript "$RETRACTION
$SIDE_OUTPUT" "$SIDE_OUTPUT" "$MAIN_OUTPUT"
run_case advisory "quoting a sidechain output does not clear the advisory" '{}'

# Positive control for the pair above: same transcript, the main
# chain's own output quoted, and the advisory correctly goes silent.
build_sidechain_transcript "$RETRACTION
$MAIN_OUTPUT" "$SIDE_OUTPUT" "$MAIN_OUTPUT"
run_case silent "quoting the main chain's own output still clears it" '{}'

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
