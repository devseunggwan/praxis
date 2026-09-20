#!/bin/bash
# test_negation_answer_quote_advisory.sh — coverage for
# hooks/advisory-nudge/negation-answer-quote-advisory/impl.py (issue #1441)
#
# Synthesizes a transcript plus a PreToolUse payload and asserts:
#   ask:<marker>  — exit 0, stdout JSON permissionDecision=ask, contains <marker>
#   silent        — exit 0, stdout empty
#
# Usage: bash tests/hooks/advisory-nudge/test_negation_answer_quote_advisory.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/negation-answer-quote-advisory/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

unset PRAXIS_NEGATION_ANSWER_BYPASS

PASS=0
FAIL=0
FAILED_NAMES=()

FREE_PREFIX='The user answered:'
ANSWER='아니지 bump 만 언급해요 다른거도 있는데'
OPTION_PREFIX='Your questions have been answered:'

# build_transcript <answer_result_text> [follow_up_json]
#   answer_result_text — the AskUserQuestion tool_result content, verbatim as
#     the runtime writes it. "__none__" leaves the question unanswered.
#   follow_up_json — assistant content blocks appended after the answer, as a
#     JSON list; defaults to one unrelated text block.
build_transcript() {
  local result="$1" follow="${2-__default__}"
  TRANSCRIPT="$(mktemp)"
  python3 - "$TRANSCRIPT" "$result" "$follow" <<'PY'
import json, sys
path, result, follow = sys.argv[1:4]
question = {
    "question": "이 문구로 올릴까요?",
    "options": [{"label": "올린다"}, {"label": "고친다"}],
}
ask_block = {
    "type": "tool_use",
    "id": "q1",
    "name": "AskUserQuestion",
    "input": {"questions": [question]},
}
events = [
    {"message": {"role": "user", "content": "요약 문구 정리해줘"}},
    {"message": {"role": "assistant", "content": [ask_block]}},
]
if result != "__none__":
    events.append({"message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "q1", "content": result}]}})
if follow == "__default__":
    blocks = [{"type": "text", "text": "문구를 다시 썼습니다. 올리겠습니다."}]
else:
    blocks = json.loads(follow)
events.append({"message": {"role": "assistant", "content": blocks}})
with open(path, "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
}

# run_case <expectation> <name> <tool_name> <tool_input_json> [ENV=v ...]
run_case() {
  local expectation="$1" name="$2" tool="$3" tool_input="$4"
  shift 4

  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({"session_id": "t-1441", "transcript_path": sys.argv[1],
                  "hook_event_name": "PreToolUse", "tool_name": sys.argv[2],
                  "tool_input": json.loads(sys.argv[3])}))
' "$TRANSCRIPT" "$tool" "$tool_input")

  local out rc
  out=$(echo "$payload" | env "$@" python3 "$HOOK" 2>/dev/null)
  rc=$?

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      ;;
    ask:*)
      # The emitted JSON escapes non-ASCII, so the marker is matched against
      # the DECODED reason — a raw grep would miss every Korean marker.
      local marker="${expectation#ask:}"
      [ "$rc" -eq 0 ] || ok=0
      local decoded
      decoded=$(printf '%s' "$out" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)["hookSpecificOutput"]
except Exception:
    sys.exit(0)
print(d.get("permissionDecision", ""))
print(d.get("permissionDecisionReason", ""))
') || ok=0
      case "$decoded" in
        ask*) ;;
        *) ok=0 ;;
      esac
      case "$decoded" in
        *"$marker"*) ;;
        *) ok=0 ;;
      esac
      ;;
    *)
      echo "FAIL  [$name] unknown expectation: $expectation"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expectation=$expectation rc=$rc out=${out:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# Any MCP server whose tool name matches the shared Slack-write pattern;
# the server segment is a placeholder, not a real deployment's name.
SLACK='mcp__chat-server__slack_send_message'
SLACK_INPUT='{"channel": "C0", "text": "두 소비자 레포의 bump PR 입니다"}'
GH_INPUT='{"command": "gh pr comment 1 --repo o/r --body 요약"}'

# =====================================================================
# Issue acceptance
# =====================================================================

# negation-marker answer → external write with no quote → advisory
build_transcript "$FREE_PREFIX \"이 문구로 올릴까요?\"=\"$ANSWER\". Read the answers carefully."
run_case "ask:$ANSWER" "negation answer, write, nothing quoted" "$SLACK" "$SLACK_INPUT"

# same answer, quoted in the following AskUserQuestion question → silent
build_transcript "$FREE_PREFIX \"이 문구로 올릴까요?\"=\"$ANSWER\". Read the answers carefully." \
  "[{\"type\": \"tool_use\", \"id\": \"q2\", \"name\": \"AskUserQuestion\", \"input\": {\"questions\": [{\"question\": \"\\\"$ANSWER\\\" 라고 하셨는데, bump 외 PR 을 빼는 쪽으로 읽었습니다. 맞나요?\"}]}}]"
run_case silent "answer quoted back in the next question" "$SLACK" "$SLACK_INPUT"

# no negation marker → silent
build_transcript "$FREE_PREFIX \"이 문구로 올릴까요?\"=\"그대로 올려주세요 좋습니다\". Read the answers carefully."
run_case silent "free-text answer without a negation marker" "$SLACK" "$SLACK_INPUT"

# quoted in assistant prose → silent
build_transcript "$FREE_PREFIX \"이 문구로 올릴까요?\"=\"$ANSWER\". Read the answers carefully." \
  "[{\"type\": \"text\", \"text\": \"\\\"$ANSWER\\\" 라고 하셔서 bump 외 줄을 뺐습니다.\"}]"
run_case silent "answer quoted in assistant prose" "$SLACK" "$SLACK_INPUT"

# =====================================================================
# Answer-shape surface
# =====================================================================

# an option pick carries the other prefix — the label is the agent's own text
build_transcript "$OPTION_PREFIX \"이 문구로 올릴까요?\"=\"아니오, 독립적인 버그픽스 (권장)\". You can now continue with these answers in mind."
run_case silent "option-label answer is not free text" "$SLACK" "$SLACK_INPUT"

# `아니면` is a disjunction, not a refusal
build_transcript "$FREE_PREFIX \"어느 채널에 올릴까요?\"=\"아니면 다른 채널로 정해줘\". Read the answers carefully."
run_case silent "아니면 is a disjunction" "$SLACK" "$SLACK_INPUT"

# a question asks the agent something; it corrects no proposal
build_transcript "$FREE_PREFIX \"진행할까요?\"=\"아니 로컬에서 랙이 걸리는거 너때문인지?\". Read the answers carefully."
run_case silent "negation-marker answer phrased as a question" "$SLACK" "$SLACK_INPUT"

# English forms
build_transcript "$FREE_PREFIX \"post this?\"=\"no, only the bump one please\". Read the answers carefully."
run_case "ask:no, only the bump one please" "English negation answer" "$SLACK" "$SLACK_INPUT"

# `note` / `nobody` must not read as `no`
build_transcript "$FREE_PREFIX \"post this?\"=\"note that the graph is stale\". Read the answers carefully."
run_case silent "note is not a negation marker" "$SLACK" "$SLACK_INPUT"

# too short to carry a distinctive quote
build_transcript "$FREE_PREFIX \"진행할까요?\"=\"아니\". Read the answers carefully."
run_case silent "answer shorter than the quote floor" "$SLACK" "$SLACK_INPUT"

# multi-question result: the negation is in the second pair
build_transcript "$FREE_PREFIX \"채널은?\"=\"backend\", \"문구는?\"=\"$ANSWER\". Read the answers carefully."
run_case "ask:$ANSWER" "negation in the second answer of a multi-question result" "$SLACK" "$SLACK_INPUT"

# escaped quotes inside the answer must not end the pair early
build_transcript "$FREE_PREFIX \"문구는?\"=\"아니지 \\\"bump\\\" 만 언급해요 다른거도\". Read the answers carefully."
run_case 'ask:bump' "escaped quotes inside the answer" "$SLACK" "$SLACK_INPUT"

# a rejection carries no pair at all
build_transcript "The user doesn't want to proceed with this tool use. The tool use was rejected."
run_case silent "rejected question carries no answer" "$SLACK" "$SLACK_INPUT"

# an InputValidationError result is not an answer
build_transcript "<tool_use_error>InputValidationError: AskUserQuestion was called with input that could not be parsed as JSON.</tool_use_error>"
run_case silent "InputValidationError result" "$SLACK" "$SLACK_INPUT"

# unanswered question
build_transcript "__none__"
run_case silent "question with no answer yet" "$SLACK" "$SLACK_INPUT"

# =====================================================================
# Sidechain isolation — a delegated agent's events ride inline in the
# main transcript and are not the main chain's own correction.
# =====================================================================

# build_scoped_transcript <answer> <tail_kind>
#   Ask + free-text <answer>, then a tail chosen by <tail_kind>:
#     human-turn      — a person's next message, starting a new turn
#     quoted-question — a follow-up AskUserQuestion quoting <answer> verbatim
#     sidechain-only  — the Ask/answer pair itself is a subagent's
#     sidechain-quote — a subagent's prose quoting <answer>
#     none            — nothing after the answer (the armed control)
build_scoped_transcript() {
  local answer="$1" tail_kind="$2"
  TRANSCRIPT="$(mktemp)"
  python3 - "$TRANSCRIPT" "$answer" "$tail_kind" <<'PY'
import json, sys
path, answer, tail_kind = sys.argv[1:4]
escaped = answer.replace("\\", "\\\\").replace('"', '\\"')
result = f'The user answered: "이 문구로 올릴까요?"="{escaped}". Read the answers carefully.'
side = tail_kind == "sidechain-only"


def mark(event):
    if side:
        event["isSidechain"] = True
    return event


ask = mark({"message": {"role": "assistant", "content": [
    {"type": "tool_use", "id": "q1", "name": "AskUserQuestion",
     "input": {"questions": [{"question": "이 문구로 올릴까요?",
                              "options": [{"label": "올린다"}]}]}}]}})
reply = mark({"message": {"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "q1", "content": result}]}})
events = [{"message": {"role": "user", "content": "요약 문구 정리해줘"}}, ask, reply]

if tail_kind == "human-turn":
    events.append({"message": {"role": "user", "content": "이제 다른 PR 상태를 확인해줘"}})
elif tail_kind == "quoted-question":
    events.append({"message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "q2", "name": "AskUserQuestion",
         "input": {"questions": [{"question": f"앞서 {answer} 라고 하셨는데 맞나요?",
                                  "options": [{"label": "맞다"}]}]}}]}})
elif tail_kind == "sidechain-quote":
    events.append({"isSidechain": True,
                   "message": {"role": "assistant",
                               "content": [{"type": "text", "text": answer}]}})
with open(path, "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
}

# A correction is consumed by the work asked for next: the person's own next
# message ends its turn, and a write in the turn after it was never corrected.
# The `none` control below is this row's positive control too — same fixture,
# that message removed, and the answer still arms.
build_scoped_transcript "$ANSWER" human-turn
run_case silent "a new user turn ends the correction's life" "$SLACK" "$SLACK_INPUT"

# An answer holding a quote character is still quoted verbatim in the
# follow-up question. Comparing it against a JSON-serialised input compares it
# against `\"` instead, and the quotation is never recognised.
QUOTED_ANSWER='아니지 "bump" 만 언급해요 다른거도 있는데'
build_scoped_transcript "$QUOTED_ANSWER" quoted-question
run_case silent "a quoted answer is recognised in the follow-up question" "$SLACK" "$SLACK_INPUT"

# Same shape with no quote character — this one passed before the fix too, and
# is what says the row above is about the escaping and not about the quoting.
build_scoped_transcript "$ANSWER" quoted-question
run_case silent "a plain answer is recognised in the follow-up question" "$SLACK" "$SLACK_INPUT"

# A subagent's Ask and answer are its own conversation; the main agent was
# never corrected, so its write must not be stopped.
build_scoped_transcript "$ANSWER" sidechain-only
run_case silent "a sidechain answer does not arm the main chain" "$SLACK" "$SLACK_INPUT"

# Positive control for the row above: the same transcript with the marker
# gone still arms, so the silence is the filter and not a broken fixture.
build_scoped_transcript "$ANSWER" none
run_case "ask:$ANSWER" "the same pair on the main chain still arms" "$SLACK" "$SLACK_INPUT"

# The mirror direction: a subagent quoting the answer is not the main agent
# showing its reading, so it must not clear the main chain's state.
build_scoped_transcript "$ANSWER" sidechain-quote
run_case "ask:$ANSWER" "a sidechain quote does not disarm the main chain" "$SLACK" "$SLACK_INPUT"

# =====================================================================
# Write-surface and guards
# =====================================================================

build_transcript "$FREE_PREFIX \"이 문구로 올릴까요?\"=\"$ANSWER\". Read the answers carefully."
run_case "ask:$ANSWER" "gh pr comment is an external write" Bash "$GH_INPUT"

build_transcript "$FREE_PREFIX \"이 문구로 올릴까요?\"=\"$ANSWER\". Read the answers carefully."
run_case silent "a local read is not an external write" Bash '{"command": "gh pr view 1 --repo o/r"}'

build_transcript "$FREE_PREFIX \"이 문구로 올릴까요?\"=\"$ANSWER\". Read the answers carefully."
run_case silent "a local file edit is not an external write" Write '{"file_path": "/tmp/x.md", "content": "x"}'

build_transcript "$FREE_PREFIX \"이 문구로 올릴까요?\"=\"$ANSWER\". Read the answers carefully."
run_case silent "bypass env" "$SLACK" "$SLACK_INPUT" PRAXIS_NEGATION_ANSWER_BYPASS=1

TRANSCRIPT="/nonexistent/$$.jsonl"
run_case silent "missing transcript" "$SLACK" "$SLACK_INPUT"

echo
echo "== $PASS passed, $FAIL failed =="
if [ "$FAIL" -gt 0 ]; then
  printf 'failed: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
