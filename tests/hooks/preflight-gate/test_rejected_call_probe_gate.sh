#!/bin/bash
# tests/hooks/preflight-gate/test_rejected_call_probe_gate.sh
#
# Coverage for hooks/preflight-gate/rejected-call-probe-gate/impl.py
# (issue #1488).
#
# Two outcomes:
#   ask  — stdout contains permissionDecision "ask", exit 0
#   pass — exit 0, stdout empty, stderr empty
#
# The first two PASS cases are the issue's replay fixture: a `git status` or a
# `gh pr list` between the rejected call and the next mutation keeps the gate
# silent; without one it asks.
#
# Usage: bash tests/hooks/preflight-gate/test_rejected_call_probe_gate.sh
# Exit:  0 = all pass, 1 = at least one failure

set +e

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HOOK="$REPO_ROOT/hooks/preflight-gate/rejected-call-probe-gate/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

TMP=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
trap 'rm -rf "$TMP"' EXIT

# ---------------------------------------------------------------------------
# Transcript fixtures
#
# mk_transcript <outfile> <steps-json>
#   steps: [{"name": tool, "input": {...}, "result": R}]
#   R: ok | reject | interrupt | block | hookask
# Record shapes follow live transcripts: a runtime refusal carries
# `toolDenialKind: "user-rejected"` and the fixed refusal sentence; an
# interrupt carries `toolDenialKind: "interrupted"`; a hook block carries
# `permission-rule`; a declined hook ask is `user-rejected` with the hook's
# own prose instead of the refusal sentence.
# ---------------------------------------------------------------------------

mk_transcript() {
  python3 - "$1" "$2" <<'PY'
import json, sys
out, steps = sys.argv[1], json.loads(sys.argv[2])
refusal = ("The user doesn't want to proceed with this tool use. The tool use "
           "was rejected (eg. if it was a file edit, the new_string was NOT "
           "written to the file). STOP what you are doing and wait for the "
           "user to tell you how to proceed.")
events = []
for i, step in enumerate(steps):
    use_id, asst = f"toolu_{i:03d}", f"asst-{i}"
    events.append({"type": "assistant", "uuid": asst, "message": {
        "role": "assistant", "content": [
            {"type": "tool_use", "id": use_id, "name": step["name"],
             "input": step["input"]}]}})
    result = {"type": "tool_result", "tool_use_id": use_id}
    record = {"type": "user", "uuid": f"res-{i}", "sourceToolAssistantUUID": asst,
              "message": {"role": "user", "content": [result]}}
    kind = step["result"]
    if kind == "reject":
        result.update(is_error=True, content=refusal)
        record["toolDenialKind"] = "user-rejected"
        record["toolUseResult"] = "User rejected tool use"
    elif kind == "interrupt":
        result.update(is_error=True, content="[Request interrupted by user for tool use]")
        record["toolDenialKind"] = "interrupted"
    elif kind == "block":
        result.update(is_error=True, content="GATE blocked: fix the title")
        record["toolDenialKind"] = "permission-rule"
    elif kind == "hookask":
        result.update(is_error=True, content="GATE: approve this push?")
        record["toolDenialKind"] = "user-rejected"
    else:
        result["content"] = "ok"
    events.append(record)
with open(out, "w", encoding="utf-8") as fh:
    for ev in events:
        fh.write(json.dumps(ev) + "\n")
PY
}

step() {  # step <tool> <input-json> <result>
  printf '{"name":"%s","input":%s,"result":"%s"}' "$1" "$2" "$3"
}
bash_input() { python3 -c 'import json,sys; print(json.dumps({"command": sys.argv[1]}))' "$1"; }

SHIP="shipcli open --branch feat-x --push --pr"
PUSH="git push -u origin feat-x"
EDIT_INPUT='{"file_path":"/repo/a.py","old_string":"a","new_string":"b"}'

SHIP_REJECTED=$(step Bash "$(bash_input "$SHIP")" reject)
SHIP_INTERRUPTED=$(step Bash "$(bash_input "$SHIP")" interrupt)
SHIP_BLOCKED=$(step Bash "$(bash_input "$SHIP")" block)
SHIP_HOOKASK=$(step Bash "$(bash_input "$SHIP")" hookask)
CAT_REJECTED=$(step Bash "$(bash_input "cat /repo/a.py")" reject)
EDIT_REJECTED=$(step Edit "$EDIT_INPUT" reject)
MCP_REJECTED=$(step mcp__srv__create_item '{"title":"x"}' reject)
GIT_STATUS_OK=$(step Bash "$(bash_input "git status")" ok)
GH_PR_LIST_OK=$(step Bash "$(bash_input "gh pr list --head feat-x")" ok)
CD_GIT_LOG_OK=$(step Bash "$(bash_input "cd /repo && git log --oneline -3")" ok)
GIT_STATUS_REJECTED=$(step Bash "$(bash_input "git status")" reject)
MCP_GET_OK=$(step mcp__srv__get_item '{"id":"1"}' ok)
LS_OK=$(step Bash "$(bash_input "ls /repo")" ok)
PUSH_OK=$(step Bash "$(bash_input "$PUSH")" ok)

T_REJECT="$TMP/reject.jsonl";                 mk_transcript "$T_REJECT" "[$SHIP_REJECTED]"
T_INTERRUPT="$TMP/interrupt.jsonl";           mk_transcript "$T_INTERRUPT" "[$SHIP_INTERRUPTED]"
T_EDIT_REJECT="$TMP/edit-reject.jsonl";       mk_transcript "$T_EDIT_REJECT" "[$EDIT_REJECTED]"
T_MCP_REJECT="$TMP/mcp-reject.jsonl";         mk_transcript "$T_MCP_REJECT" "[$MCP_REJECTED]"
T_STATUS="$TMP/status.jsonl";                 mk_transcript "$T_STATUS" "[$SHIP_REJECTED,$GIT_STATUS_OK]"
T_PR_LIST="$TMP/pr-list.jsonl";               mk_transcript "$T_PR_LIST" "[$SHIP_REJECTED,$GH_PR_LIST_OK]"
T_CD_LOG="$TMP/cd-log.jsonl";                 mk_transcript "$T_CD_LOG" "[$SHIP_REJECTED,$CD_GIT_LOG_OK]"
T_MCP_GET="$TMP/mcp-get.jsonl";               mk_transcript "$T_MCP_GET" "[$MCP_REJECTED,$MCP_GET_OK]"
T_LS="$TMP/ls.jsonl";                         mk_transcript "$T_LS" "[$SHIP_REJECTED,$LS_OK]"
T_PROBE_REJECTED="$TMP/probe-rejected.jsonl"; mk_transcript "$T_PROBE_REJECTED" "[$SHIP_REJECTED,$GIT_STATUS_REJECTED]"
T_REARMED="$TMP/rearmed.jsonl";               mk_transcript "$T_REARMED" "[$SHIP_REJECTED,$GIT_STATUS_OK,$EDIT_REJECTED]"
T_MUTATION_RAN="$TMP/mutation-ran.jsonl";     mk_transcript "$T_MUTATION_RAN" "[$SHIP_REJECTED,$PUSH_OK]"
T_READ_REJECTED="$TMP/read-rejected.jsonl";   mk_transcript "$T_READ_REJECTED" "[$CAT_REJECTED]"
T_BLOCKED="$TMP/blocked.jsonl";               mk_transcript "$T_BLOCKED" "[$SHIP_BLOCKED]"
T_HOOKASK="$TMP/hookask.jsonl";               mk_transcript "$T_HOOKASK" "[$SHIP_HOOKASK]"
T_EMPTY="$TMP/empty.jsonl";                   mk_transcript "$T_EMPTY" "[]"

mk_payload() {  # mk_payload <tool> <input-json> <transcript>
  python3 -c '
import json, sys
print(json.dumps({"session_id": "test-session", "tool_name": sys.argv[1],
                  "transcript_path": sys.argv[3],
                  "tool_input": json.loads(sys.argv[2])}))' "$1" "$2" "$3"
}

run_case() {  # run_case <name> <ask|pass> <tool> <input-json> <transcript>
  local name="$1" expected="$2" tool="$3" input="$4" transcript="$5"
  local out err_file err rc ok=1
  err_file=$(mktemp)
  # The resumable scan caches its cursor under PRAXIS_HOME; a fresh home per
  # case keeps one case's scan out of the next.
  out=$(mk_payload "$tool" "$input" "$transcript" | PRAXIS_HOME="$TMP/home-$PASS-$FAIL" "$HOOK" 2>"$err_file")
  rc=$?; err=$(cat "$err_file"); rm -f "$err_file"

  case "$expected" in
    ask)
      [ "$rc" -eq 0 ] || ok=0
      echo "$out" | grep -q '"permissionDecision": "ask"' || ok=0
      ;;
    pass)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      [ -z "$err" ]   || ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$expected] $name"; PASS=$((PASS+1))
  else
    echo "FAIL  [$expected→rc=$rc,stdout=$([ -n "$out" ] && echo non-empty || echo empty),stderr=$([ -n "$err" ] && echo non-empty || echo empty)] $name"
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# ASK — a mutating call follows a refused mutating call with no probe between
# ---------------------------------------------------------------------------

run_case "rejected mutating CLI, next mutation with no probe (issue fixture)" ask \
  Bash "$(bash_input "$PUSH")" "$T_REJECT"

run_case "interrupted mutating CLI, next mutation is a file edit" ask \
  Edit "$EDIT_INPUT" "$T_INTERRUPT"

run_case "rejected file edit, next mutation is a Bash write" ask \
  Bash "$(bash_input "$PUSH")" "$T_EDIT_REJECT"

run_case "rejected MCP write, next MCP write" ask \
  mcp__srv__update_item '{"id":"1"}' "$T_MCP_REJECT"

run_case "a read that inspects no state (ls) is not a probe" ask \
  Bash "$(bash_input "$PUSH")" "$T_LS"

run_case "a probe that was itself refused never ran" ask \
  Bash "$(bash_input "$PUSH")" "$T_PROBE_REJECTED"

run_case "a later refused mutation re-arms after an earlier probe" ask \
  Bash "$(bash_input "$PUSH")" "$T_REARMED"

run_case "a leading cd does not turn a mutation into a probe" ask \
  Bash "$(bash_input "cd /repo && $PUSH")" "$T_REJECT"

# ---------------------------------------------------------------------------
# PASS — silent controls
# ---------------------------------------------------------------------------

run_case "git status ran between (issue fixture)" pass \
  Bash "$(bash_input "$PUSH")" "$T_STATUS"

run_case "gh pr list ran between (issue fixture)" pass \
  Bash "$(bash_input "$PUSH")" "$T_PR_LIST"

run_case "cd <repo> && git log ran between" pass \
  Bash "$(bash_input "$PUSH")" "$T_CD_LOG"

run_case "a read-only MCP call ran between" pass \
  mcp__srv__update_item '{"id":"1"}' "$T_MCP_GET"

run_case "the pending call is itself a probe" pass \
  Bash "$(bash_input "gh pr list --head feat-x")" "$T_REJECT"

run_case "the pending call is a probe behind a leading cd" pass \
  Bash "$(bash_input "cd /repo && git status")" "$T_REJECT"

run_case "the pending call is a probe with an explicit repo path" pass \
  Bash "$(bash_input "git -C /repo log --oneline -1")" "$T_REJECT"

run_case "the pending call is read-only" pass \
  Bash "$(bash_input "ls /repo")" "$T_REJECT"

run_case "the pending call is a read-only MCP call" pass \
  mcp__srv__get_item '{"id":"1"}' "$T_MCP_REJECT"

run_case "a mutating call already ran after the refusal (ask approved)" pass \
  Bash "$(bash_input "$PUSH")" "$T_MUTATION_RAN"

run_case "the refused call was read-only" pass \
  Bash "$(bash_input "$PUSH")" "$T_READ_REJECTED"

run_case "a hook block is not a refusal (the call never ran)" pass \
  Bash "$(bash_input "$PUSH")" "$T_BLOCKED"

run_case "a declined hook ask is not a runtime refusal" pass \
  Bash "$(bash_input "$PUSH")" "$T_HOOKASK"

run_case "no refusal in the transcript" pass \
  Bash "$(bash_input "$PUSH")" "$T_EMPTY"

run_case "missing transcript fails open" pass \
  Bash "$(bash_input "$PUSH")" "$TMP/does-not-exist.jsonl"

# ---------------------------------------------------------------------------
# The ask names the refused call
# ---------------------------------------------------------------------------

out=$(mk_payload Bash "$(bash_input "$PUSH")" "$T_REJECT" | PRAXIS_HOME="$TMP/home-quote" "$HOOK" 2>/dev/null)
if echo "$out" | grep -q "shipcli open --branch feat-x" && echo "$out" | grep -q "user-rejected"; then
  echo "PASS  [ask] reason names the refused call and its denial kind"; PASS=$((PASS+1))
else
  echo "FAIL  [ask] reason names the refused call and its denial kind"; FAIL=$((FAIL+1))
  FAILED_NAMES+=("reason names the refused call")
fi

echo
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  printf '  - %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
