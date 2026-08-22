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

# ============================================================================
# Verdict-restatement gate (issue #1062) — PR #1058 anchor rev4 verbatim
# shape: a verdict number ("FAIL 0") measured once with an accurate scope
# qualifier ("348줄 중"), then restated bare across later turns while an
# unrelated progress number (log line count) kept changing beside it.
# build_transcript_verdict <prior_text> <prior_ts> <final_text> <final_ts> <evidence>
# -> writes path to $TRANSCRIPT. `prior_text`/`prior_ts` seed an EARLIER turn
# (separated by a real user message) so the claim is a cross-turn restatement,
# not a same-message repeat.
# ============================================================================
build_transcript_verdict() {
  local prior_text="$1" prior_ts="$2" final_text="$3" final_ts="$4" evidence="$5"
  TRANSCRIPT="$(mktemp)"
  python3 - "$TRANSCRIPT" "$prior_text" "$prior_ts" "$final_text" "$final_ts" "$evidence" <<'PY'
import json, sys
path, prior_text, prior_ts, final_text, final_ts, evidence = sys.argv[1:7]
events = [
    {"message": {"role": "user", "content": "run the suite"}},
    {"message": {"role": "assistant", "content": [{"type": "text", "text": prior_text}]},
     "timestamp": prior_ts},
    {"message": {"role": "user", "content": "keep going"}},
]
if evidence == "bash":
    events.append({"message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Bash",
         "input": {"command": "tail -n 50 /tmp/suite.log"}}]}})
events.append({"message": {"role": "assistant", "content": [
    {"type": "text", "text": final_text}]}, "timestamp": final_ts})
with open(path, "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY
}

# --- gap reproduction: qualified measurement, then bare restatement, no probe
build_transcript_verdict \
  "348줄 중 FAIL 0" "2026-08-20T09:19:36.000Z" \
  "1110줄까지 실패 0 으로 진행 중" "2026-08-20T09:21:48.000Z" \
  none
run_case advisory "verdict-restatement-no-qualifier" '{}'

# --- false-positive control: restatement KEEPS the qualifier -> silent ------
build_transcript_verdict \
  "348줄 중 FAIL 0" "2026-08-20T09:19:36.000Z" \
  "1110줄 중 FAIL 0" "2026-08-20T09:21:48.000Z" \
  none
run_case silent "verdict-restatement-qualified-stays-silent" '{}'

# --- restatement WITH a fresh probe in the current turn -> silent -----------
build_transcript_verdict \
  "348줄 중 FAIL 0" "2026-08-20T09:19:36.000Z" \
  "실패 0 으로 진행 중" "2026-08-20T09:21:48.000Z" \
  bash
run_case silent "verdict-restatement-with-probe" '{}'

# --- first-ever mention (no prior occurrence) -> silent ---------------------
build_transcript_verdict \
  "빌드를 시작합니다" "2026-08-20T09:19:36.000Z" \
  "FAILs: 0 / SKIPPED: 0" "2026-08-20T09:21:48.000Z" \
  none
run_case silent "verdict-first-mention-not-restatement" '{}'

# --- a DIFFERENT number is not a restatement (re-measured, not repeated) ----
build_transcript_verdict \
  "348줄 중 FAIL 0" "2026-08-20T09:19:36.000Z" \
  "1110줄까지 실패 2" "2026-08-20T09:21:48.000Z" \
  none
run_case silent "verdict-different-number-not-restatement" '{}'

# --- bare 통과 (pass, no number) restated without qualifier ------------------
build_transcript_verdict \
  "12개 중 통과" "2026-08-20T09:19:36.000Z" \
  "여전히 통과 상태로 진행 중입니다" "2026-08-20T09:21:48.000Z" \
  none
run_case advisory "verdict-bare-pass-restatement" '{}'

# --- range denominator must not pre-empt the verdict count -----------------
# "120/348 중 FAIL 0" once bound 348 as the fail count and consumed the real
# "FAIL 0", so the later bare "실패 0" found no prior mention and stayed silent.
build_transcript_verdict \
  "120/348 중 FAIL 0" "2026-08-20T09:19:36.000Z" \
  "실패 0 으로 진행 중" "2026-08-20T09:21:48.000Z" \
  none
run_case advisory "verdict-range-denominator-not-the-count" '{}'

# --- EN vocabulary needs word boundaries -----------------------------------
# "pass" inside "bypass" once recorded a bogus pass:0 prior mention, so this
# GENUINE first "PASS 0" fired an advisory (a block, under strict mode).
build_transcript_verdict \
  "bypass 0 으로 우회했습니다" "2026-08-20T09:19:36.000Z" \
  "PASS 0 입니다" "2026-08-20T09:21:48.000Z" \
  none
run_case silent "verdict-en-substring-not-a-verdict-word" '{}'

# --- the count must belong to the verdict word, not an intervening noun ----
build_transcript_verdict \
  "error code 0 을 확인했습니다" "2026-08-20T09:19:36.000Z" \
  "실패 0 입니다" "2026-08-20T09:21:48.000Z" \
  none
run_case silent "verdict-count-belongs-to-another-noun" '{}'

# --- EN reversed-order restatement still fires (boundary fix is not a mute) -
build_transcript_verdict \
  "348줄 중 0 FAILED" "2026-08-20T09:19:36.000Z" \
  "0 FAILED 로 진행 중" "2026-08-20T09:21:48.000Z" \
  none
run_case advisory "verdict-en-reversed-order-restatement" '{}'

# --- an unrelated progress number is not the verdict's qualifier -----------
# Scanned per line, the "80%" marked the whole line qualified and the stale
# "실패 0" beside it passed silently — the incident's own shape (a moving
# progress number beside a frozen verdict).
build_transcript_verdict \
  "348줄 중 FAIL 0" "2026-08-20T09:19:36.000Z" \
  "진행률 80%, 실패 0 으로 진행 중" "2026-08-20T09:21:48.000Z" \
  none
run_case advisory "verdict-unrelated-percent-not-a-qualifier" '{}'

build_transcript_verdict \
  "348줄 중 FAIL 0" "2026-08-20T09:19:36.000Z" \
  "작업 3/5 완료, 실패 0" "2026-08-20T09:21:48.000Z" \
  none
run_case advisory "verdict-unrelated-fraction-not-a-qualifier" '{}'

# --- a bare-PASS key must not be minted from a numerically scoped claim ----
# "0 PASS" reverses the order, so the bare-PASS pattern matched inside it and
# recorded pass:bare alongside pass:0 — a prior bare 통과 then read the scoped
# claim as its own restatement.
build_transcript_verdict \
  "12개 중 통과" "2026-08-20T09:19:36.000Z" \
  "0 PASS 입니다" "2026-08-20T09:21:48.000Z" \
  none
run_case silent "verdict-reversed-numeric-is-not-a-bare-pass" '{}'

# --- a verdict stated as a QUESTION stays silent (spec: either kind) --------
build_transcript_verdict \
  "348줄 중 FAIL 0" "2026-08-20T09:19:36.000Z" \
  "실패 0인가요?" "2026-08-20T09:21:48.000Z" \
  none
run_case silent "verdict-question-clause-stays-silent" '{}'

build_transcript_verdict \
  "348줄 중 FAIL 0" "2026-08-20T09:19:36.000Z" \
  "FAIL 0?" "2026-08-20T09:21:48.000Z" \
  none
run_case silent "verdict-en-question-clause-stays-silent" '{}'

# --- same-clause qualifier still silences (EN "of N lines" form) -----------
build_transcript_verdict \
  "348줄 중 FAIL 0" "2026-08-20T09:19:36.000Z" \
  "FAIL 0 of 1110 lines" "2026-08-20T09:21:48.000Z" \
  none
run_case silent "verdict-same-clause-qualifier-stays-silent" '{}'

# --- elapsed time belongs to the scored message, not an older one ----------
# A final event carrying no timestamp fell back to an EARLIER assistant event
# in the same turn, so the advisory reported that event's age ("1 min ago")
# instead of "unknown". Needs TWO assistant events in the turn to discriminate.
TRANSCRIPT="$(mktemp)"
python3 - "$TRANSCRIPT" <<'PY_T'
import json, sys
events = [
    {"message": {"role": "user", "content": "run the suite"}},
    {"message": {"role": "assistant", "content": [{"type": "text", "text": "348줄 중 FAIL 0"}]},
     "timestamp": "2026-08-20T09:19:36.000Z"},
    {"message": {"role": "user", "content": "keep going"}},
    {"message": {"role": "assistant", "content": [{"type": "text", "text": "계속 진행합니다"}]},
     "timestamp": "2026-08-20T09:21:00.000Z"},
    {"message": {"role": "assistant", "content": [{"type": "text", "text": "실패 0 으로 진행 중"}]}},
]
with open(sys.argv[1], "w", encoding="utf-8") as f:
    for e in events:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
PY_T
elapsed_out=$(python3 -c 'import json,sys; print(json.dumps({"transcript_path": sys.argv[1]}))' "$TRANSCRIPT" \
  | python3 "$HOOK" 2>/dev/null)
if printf '%s' "$elapsed_out" | grep -q 'last stated unknown'; then
  echo "PASS  [verdict-elapsed-unknown-when-final-event-untimed]"; PASS=$((PASS + 1))
else
  echo "FAIL  [verdict-elapsed-unknown-when-final-event-untimed] out=<$elapsed_out>"; FAIL=$((FAIL + 1))
fi

echo ""
echo "runtime-state-claim-gate: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
exit 0
