#!/bin/bash
# test_question_turn_mutation_gate.sh -- coverage for question-turn-mutation-gate
#
# The gate emits permissionDecision "ask" on stdout at exit 0; a call it does not
# care about produces no stdout. Both branches are asserted, because a gate that
# silently stops firing looks exactly like a quiet session.
#
# The replay fixture follows issue #1486: a challenge with no question mark, a
# one-line acknowledgement, then state-changing calls in the same turn.
#
# Usage: bash tests/hooks/preflight-gate/test_question_turn_mutation_gate.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/question-turn-mutation-gate/impl.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

run_case() {
  local name="$1" result="$2" expected="$3"
  if [ "$result" = "$expected" ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected got=$result"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

TMP="$(mktemp -d)" || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
trap 'rm -rf "$TMP"' EXIT

# Write a transcript from records given as `kind:text` arguments.
#   human:<text>   a typed user message
#   meta:<text>    an isMeta user record (skill body)
#   notice:<text>  a task-notification user record
#   reply:<text>   an assistant text block
#   call:<cmd>     an assistant Bash tool_use plus its tool_result
transcript() {
  local path="$TMP/t$RANDOM$RANDOM.jsonl"
  python3 - "$path" "$@" <<'EOF'
import json, sys
path, records = sys.argv[1], sys.argv[2:]
out = []
for i, rec in enumerate(records):
    kind, _, text = rec.partition(":")
    if kind in ("human", "meta", "notice"):
        ev = {"type": "user", "message": {"role": "user", "content": text}}
        if kind == "meta":
            ev["isMeta"] = True
        if kind == "notice":
            ev["origin"] = {"kind": "task-notification"}
        out.append(ev)
    elif kind == "reply":
        out.append({"type": "assistant", "message": {"role": "assistant",
                    "content": [{"type": "text", "text": text}]}})
    elif kind == "call":
        tid = f"toolu_{i}"
        out.append({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": text}}]}})
        out.append({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}})
with open(path, "w", encoding="utf-8") as fh:
    fh.write("\n".join(json.dumps(e, ensure_ascii=False) for e in out) + "\n")
EOF
  printf '%s' "$path"
}

# payload <transcript> <tool_name> <tool_input_json> [agent_id]
payload() {
  python3 -c '
import json, sys
p = {"session_id": "s", "transcript_path": sys.argv[1], "tool_name": sys.argv[2],
     "tool_input": json.loads(sys.argv[3])}
if len(sys.argv) > 4:
    p["agent_id"] = sys.argv[4]
print(json.dumps(p))' "$@"
}

bash_input() {
  python3 -c 'import json,sys; print(json.dumps({"command": sys.argv[1]}))' "$1"
}

verdict() {
  local out status
  out=$(printf '%s' "$1" | python3 "$HOOK" 2>/dev/null)
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "exit${status}"
  elif printf '%s' "$out" | grep -q '"permissionDecision": "ask"'; then
    echo "ask"
  else
    echo "quiet"
  fi
}

# verdict for a Bash call made after the given transcript records
bash_after() {
  local cmd="$1"; shift
  verdict "$(payload "$(transcript "$@")" Bash "$(bash_input "$cmd")")"
}

echo "test_question_turn_mutation_gate"

# --- Replay of issue #1486 --------------------------------------------------
CHALLENGE='you are supposed to follow these rules, so what is this'
CHALLENGE_KO='아니 넌 이걸 지켜서 처리해야 하는 에이전트인데 이러면 어쩌자는건데'
ACK='reply:following the rules from now on'

run_case "incident_first_mutation_worktree_add" \
  "$(bash_after 'git worktree add -b fix-x ../wt origin/main' "human:$CHALLENGE" "$ACK")" "ask"
run_case "incident_ko_first_mutation" \
  "$(bash_after 'git worktree add -b fix-x ../wt origin/main' "human:$CHALLENGE_KO" "$ACK")" "ask"
# Later mutations in the same turn keep asking: the ack text and the earlier
# tool results do not open a new turn.
run_case "incident_later_mutation_pr_create" \
  "$(bash_after 'gh pr create --title x --body y' "human:$CHALLENGE" "$ACK" \
     'call:git worktree add -b fix-x ../wt origin/main' 'call:poetry install')" "ask"
run_case "incident_readonly_probe_quiet" \
  "$(bash_after 'git status' "human:$CHALLENGE" "$ACK")" "quiet"
run_case "incident_readonly_pipeline_quiet" \
  "$(bash_after 'git log --oneline -5 | head -3' "human:$CHALLENGE" "$ACK")" "quiet"

# --- The next human message opens a new turn -------------------------------
run_case "instruction_turn_quiet" \
  "$(bash_after 'git worktree add -b fix-x ../wt origin/main' 'human:start the fix in a new worktree')" "quiet"
run_case "lifted_by_next_instruction" \
  "$(bash_after 'git push origin fix-x' "human:$CHALLENGE" "$ACK" 'human:ok, go ahead and push')" "quiet"

# --- Injected user records do not open a turn ------------------------------
run_case "meta_record_does_not_lift" \
  "$(bash_after 'git commit -m x' 'human:why did the build fail?' 'meta:skill body text. do it.')" "ask"
run_case "task_notification_does_not_lift" \
  "$(bash_after 'git commit -m x' 'human:why did the build fail?' 'notice:task finished. run it.')" "ask"

# --- Classifier: the last sentence decides ---------------------------------
run_case "question_mark"          "$(bash_after 'rm -rf build' 'human:is the cache stale?')"                "ask"
run_case "fullwidth_question"     "$(bash_after 'rm -rf build' 'human:캐시가 낡았나요？')"                 "ask"
run_case "en_opener_no_mark"      "$(bash_after 'rm -rf build' 'human:why is the cache stale')"             "ask"
run_case "ko_ending_no_mark"      "$(bash_after 'rm -rf build' 'human:왜 캐시가 낡았는지')"                  "ask"
run_case "ko_challenge_no_mark"   "$(bash_after 'rm -rf build' 'human:이게 뭐야 진짜')"                      "ask"
run_case "question_then_order"    "$(bash_after 'rm -rf build' 'human:why did it fail? fix it.')"           "quiet"
run_case "order_then_question"    "$(bash_after 'rm -rf build' 'human:fix it. why did it fail?')"           "ask"
run_case "en_request_with_mark"   "$(bash_after 'rm -rf build' 'human:can you clear the cache?')"           "quiet"
run_case "ko_request_with_mark"   "$(bash_after 'rm -rf build' 'human:캐시 지워줄래?')"                     "quiet"
run_case "ko_insist_ninkka_quiet" "$(bash_after 'rm -rf build' 'human:빨리 지우라니까')"                     "quiet"
run_case "do_opener_is_order"     "$(bash_after 'rm -rf build' 'human:do the migration now')"               "quiet"
run_case "when_opener_is_order"   "$(bash_after 'rm -rf build' 'human:when done, print OK')"                "quiet"
run_case "challenge_beats_request" \
  "$(bash_after 'rm -rf build' 'human:please, why did you delete it')"                                      "ask"
# A question mark inside a system reminder or a code fence is not the user's.
run_case "reminder_mark_ignored" \
  "$(bash_after 'rm -rf build' 'human:clear the cache <system-reminder>is it stale?</system-reminder>')"  "quiet"
run_case "fence_mark_ignored" \
  "$(bash_after 'rm -rf build' 'human:clear the cache
```
why?
```')"                                                                                                      "quiet"

# --- Extra markers from the environment ------------------------------------
run_case "extra_marker_env" \
  "$(PRAXIS_QUESTION_TURN_MARKERS='wie bitte, warum' bash_after 'rm -rf build' 'human:warum ist das so')" "ask"
run_case "extra_marker_env_unset" \
  "$(bash_after 'rm -rf build' 'human:warum ist das so')"                                                  "quiet"

# --- Other tool families ---------------------------------------------------
Q="$(transcript 'human:why did the build fail?')"
run_case "edit_asks"       "$(verdict "$(payload "$Q" Edit '{"file_path":"/repo/a.py","old_string":"a","new_string":"b"}')")" "ask"
run_case "write_asks"      "$(verdict "$(payload "$Q" Write '{"file_path":"/repo/a.py","content":"x"}')")"                    "ask"
run_case "mcp_write_asks"  "$(verdict "$(payload "$Q" mcp__slack__slack_send_message '{"channel":"c","text":"t"}')")"         "ask"
run_case "mcp_read_quiet"  "$(verdict "$(payload "$Q" mcp__trino__trino_query '{"sql":"select 1"}')")"                        "quiet"
run_case "read_tool_quiet" "$(verdict "$(payload "$Q" Read '{"file_path":"/repo/a.py"}')")"                                   "quiet"

# --- A subagent's turn was not opened by the human -------------------------
run_case "subagent_quiet" \
  "$(verdict "$(payload "$Q" Bash "$(bash_input 'rm -rf build')" agent-1)")" "quiet"

# --- Fail open -------------------------------------------------------------
run_case "missing_transcript" \
  "$(verdict "$(payload "$TMP/none.jsonl" Bash "$(bash_input 'rm -rf build')")")" "quiet"
run_case "malformed_payload"  "$(verdict 'not json at all')"                     "quiet"
run_case "null_tool_input"    "$(verdict '{"tool_name":"Bash","tool_input":null}')" "quiet"
run_case "empty_object"       "$(verdict '{}')"                                  "quiet"

echo "----"
echo "PASS: $PASS / FAIL: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  printf 'failed: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
