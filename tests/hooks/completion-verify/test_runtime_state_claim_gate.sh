#!/bin/bash
# Tests for completion-verify/runtime-state-claim-gate (Stop hook).
set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/completion-verify/runtime-state-claim-gate/impl.py"

unset PRAXIS_RUNTIME_CLAIM_BYPASS PRAXIS_RUNTIME_CLAIM_STRICT PRAXIS_HOOK_ERROR_STDERR

PASS=0
FAIL=0

# build_transcript <final_text> <evidence: none|bash|mcp|write|prev-turn-bash>
# -> writes path to $TRANSCRIPT
build_transcript() {
  local final_text="$1" evidence="$2"
  TRANSCRIPT="$(mktemp)"
  python3 - "$TRANSCRIPT" "$final_text" "$evidence" <<'PY'
import json, sys
path, final_text, evidence = sys.argv[1], sys.argv[2], sys.argv[3]
events = [{"message": {"role": "user", "content": "what is running right now?"}}]
if evidence == "prev-turn-bash":
    # Probe happened BEFORE the latest real user input -> outside current turn.
    events = [
        {"message": {"role": "user", "content": "launch it"}},
        {"message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "git status --porcelain"}}]}},
        {"message": {"role": "user", "content": "what is running right now?"}},
    ]
elif evidence == "bash":
    events.append({"message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Bash",
         "input": {"command": "git status --porcelain ../wt-4024"}}]}})
elif evidence == "mcp":
    events.append({"message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "mcp__laplace-airflow__airflow_dag_runs",
         "input": {"dag_id": "x"}}]}})
elif evidence == "write":
    events.append({"message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": "/tmp/x", "content": "y"}}]}})
events.append({"message": {"role": "assistant",
                           "content": [{"type": "text", "text": final_text}]}})
with open(path, "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
}

# run_case <advisory|advisory-strict|silent> <name> <stop_payload_extra_json> [ENV=v ...]
run_case() {
  local expected="$1" name="$2" extra="$3"
  shift 3
  local payload err rc ok=1
  payload=$(python3 -c 'import json,sys
p={"transcript_path":sys.argv[1]}
p.update(json.loads(sys.argv[2]))
print(json.dumps(p))' "$TRANSCRIPT" "$extra")
  # issue #647 H3: advisory/block both arrive as stdout JSON (exit always 0);
  # stderr must stay empty in every case.
  local out
  out=$(printf '%s' "$payload" | env "$@" python3 "$HOOK" 2>/tmp/rsc-stderr.$$)
  rc=$?
  err=$(cat /tmp/rsc-stderr.$$ 2>/dev/null; rm -f /tmp/rsc-stderr.$$)
  case "$expected" in
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert "[runtime-state-claim-gate]" in d["systemMessage"]
assert "decision" not in d
' || ok=0
      ;;
    advisory-strict)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["decision"] == "block"
assert "[runtime-state-claim-gate]" in d["reason"]
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

# --- motivating incident verbatim (issue #809): isolation claim, zero probes
build_transcript "클라우드에서 별도의 신선한 클론으로 돌아갑니다. 제가 만든 로컬 워크트리는 remote 에이전트가 사용하지 않습니다." none
run_case advisory "incident-verbatim-isolation" '{}'

# --- EN running claim, no probe -> advisory --------------------------------
build_transcript "The subagent is running in a cloud sandbox on a fresh clone." none
run_case advisory "en-running-no-probe" '{}'

# --- KR running claim with Hangul particle after EN subject ----------------
# Lookaround guard: \bagent\b would miss "agent가" (no boundary between
# Hangul and ASCII word chars).
build_transcript "agent가 로컬에서 실행 중입니다." none
run_case advisory "mixed-particle-running" '{}'

# --- claim WITH Bash probe in the current turn -> silent -------------------
build_transcript "서브에이전트가 로컬 워크트리에서 실행 중입니다 (git status 실측)." bash
run_case silent "claim-with-bash-probe" '{}'

# --- claim WITH MCP read probe -> silent -----------------------------------
build_transcript "The DAG is running on prod right now." mcp
run_case silent "claim-with-mcp-probe" '{}'

# --- probe in PREVIOUS turn only -> advisory (turn-scoped evidence) --------
build_transcript "에이전트는 여전히 백그라운드에서 실행 중입니다." prev-turn-bash
run_case advisory "prev-turn-probe-not-evidence" '{}'

# --- Write tool_use is not a probe -> advisory -----------------------------
build_transcript "The worker is running in the background." write
run_case advisory "write-is-not-probe" '{}'

# --- no runtime claim -> silent --------------------------------------------
build_transcript "I updated the README and the tests are green." none
run_case silent "no-claim" '{}'

# --- subject-less state phrase -> silent (no runtime subject) --------------
build_transcript "테스트가 실행 중입니다." none
run_case silent "subjectless-state" '{}'

# --- question form -> silent ------------------------------------------------
build_transcript "에이전트가 아직 돌고 있나요?" none
run_case silent "question-form" '{}'

# --- future intent suppresses "running" only --------------------------------
build_transcript "이제 에이전트를 백그라운드에서 실행하겠습니다." none
run_case silent "future-intent-kr" '{}'

build_transcript "The agent will be running in the cloud after dispatch." none
run_case silent "future-intent-en" '{}'

# --- isolation claim in future form is STILL gated --------------------------
# "건드리지 않을 겁니다" projects an unverified isolation guarantee.
build_transcript "remote 에이전트는 로컬 워크트리를 건드리지 않을 겁니다." none
run_case advisory "isolation-future-still-fires" '{}'

# --- suppressor is segment-scoped, not line-scoped (codex P1) ----------------
# "will" in the second clause must not suppress the state claim in the first.
build_transcript "The agent is running locally; I will stop it now." none
run_case advisory "mixed-clause-future-not-suppressing" '{}'

# --- progressive isolation negation is a claim (codex P1) --------------------
build_transcript "The subagent is not touching the local worktree." none
run_case advisory "isolation-progressive-negation" '{}'

# --- trailing question segment must not suppress a preceding claim segment ---
build_transcript "에이전트는 로컬을 건드리지 않습니다. 괜찮나요?" none
run_case advisory "claim-then-question-segment" '{}'

# --- quoted line -> silent ---------------------------------------------------
build_transcript "> 에이전트가 클라우드에서 실행 중입니다 (사용자 인용)" none
run_case silent "quoted-line" '{}'

# --- strict mode -> decision:block JSON --------------------------------------
build_transcript "The subagent is running in a cloud sandbox." none
run_case advisory-strict "strict-mode" '{}' PRAXIS_RUNTIME_CLAIM_STRICT=1

# --- bypass env -> silent -----------------------------------------------------
build_transcript "The subagent is running in a cloud sandbox." none
run_case silent "bypass" '{}' PRAXIS_RUNTIME_CLAIM_BYPASS=1

# --- stop_hook_active -> silent (loop guard) ----------------------------------
build_transcript "에이전트가 백그라운드에서 돌고 있습니다." none
run_case silent "stop-hook-active" '{"stop_hook_active": true}'

# --- missing transcript -> silent (fail-open) ---------------------------------
TRANSCRIPT="/nonexistent/transcript.jsonl"
run_case silent "missing-transcript" '{}'

echo ""
echo "runtime-state-claim-gate: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
exit 0
