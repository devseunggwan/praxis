#!/usr/bin/env bash
# test_pre_output_falsification_gate.sh — coverage for the pre-output
# falsification gate (issue #487).
#
# Synthesizes Claude Code PreToolUse payloads and asserts:
#   advisory → exit 0 + stderr non-empty
#   pass     → exit 0 + stderr empty
#
# The hook is ADVISORY-only — it never blocks. There is no block verdict.
#
# Usage: bash tests/hooks/advisory-nudge/test_pre_output_falsification_gate.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/pre-output-falsification-gate/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# A dedicated TMPDIR so Lane B counters never leak into the real tmp tree and
# every test run starts from a clean counter state.
GATE_TMPDIR="$WORK/tmp"
mkdir -p "$GATE_TMPDIR"

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

# Build a transcript JSONL with a single user tool_result block.
# $1 = tool_result text content
build_transcript_tool_result() {
  local content="$1"
  local path="$WORK/transcript-tr-$$-$RANDOM.jsonl"
  python3 - "$content" > "$path" <<'PY'
import json, sys
content = sys.argv[1]
print(json.dumps({"type": "user", "message": {"role": "user", "content": "do the work"}}))
print(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "running"}]}}))
print(json.dumps({
    "type": "user",
    "message": {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "abc123", "content": content}
        ],
    },
}))
PY
  echo "$path"
}

# Build an AskUserQuestion payload.
# $1 = transcript_path
# $2 = question body text
# $3 = options JSON array of {label,description} dicts
build_ask_payload() {
  local transcript="$1" qbody="$2" options_json="$3"
  python3 - "$transcript" "$qbody" "$options_json" <<'PY'
import json, sys
transcript, qbody, options_json = sys.argv[1], sys.argv[2], sys.argv[3]
options = json.loads(options_json)
payload = {
    "session_id": "test-session-A",
    "transcript_path": transcript,
    "tool_name": "AskUserQuestion",
    "tool_input": {
        "questions": [
            {"question": qbody, "header": "Next", "multiSelect": False, "options": options}
        ]
    },
    "cwd": "/tmp",
}
print(json.dumps(payload))
PY
}

# Build a Bash payload.
# $1 = session_id
# $2 = command string
build_bash_payload() {
  local sid="$1" cmd="$2"
  python3 - "$sid" "$cmd" <<'PY'
import json, sys
sid, cmd = sys.argv[1], sys.argv[2]
print(json.dumps({
    "session_id": sid,
    "tool_name": "Bash",
    "tool_input": {"command": cmd},
    "cwd": "/tmp",
}))
PY
}

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

# run_case NAME EXPECTED EXTRA_ENV PAYLOAD
#   EXPECTED ∈ {advisory, pass}
#   EXTRA_ENV: extra env assignments (e.g. "PRAXIS_FALSIFY_GATE_BYPASS=1") or ""
run_case() {
  local name="$1" expected="$2" extra_env="$3" payload="$4"
  local err_file rc err_content

  err_file=$(mktemp)
  echo "$payload" | env TMPDIR="$GATE_TMPDIR" $extra_env "$HOOK" >/dev/null 2>"$err_file"
  rc=$?
  err_content=$(cat "$err_file"); rm -f "$err_file"

  local ok=1
  case "$expected" in
    advisory) [ "$rc" -eq 0 ] && [ -n "$err_content" ] || ok=0 ;;
    pass)     [ "$rc" -eq 0 ] && [ -z "$err_content" ] || ok=0 ;;
    *) echo "INTERNAL: unknown expected '$expected'" >&2; ok=0 ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS [$expected] $name"; PASS=$((PASS+1))
  else
    echo "FAIL [$expected→rc=$rc,stderr=$([ -n "$err_content" ] && echo non-empty || echo empty)] $name"
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
  fi
}

# ===========================================================================
# Lane A
# ===========================================================================

# --- Lane A positive: (Recommended) + negative transcript + no falsify phrase ---
TA1=$(build_transcript_tool_result "query returned 0 rows and a timeout error")
PA1=$(build_ask_payload "$TA1" "Which approach should we take next?" \
  '[{"label":"Option A (Recommended)","description":"do A"},{"label":"Option B","description":"do B"}]')
run_case "Lane A positive: (Recommended) + negative evidence + no probe → advisory" advisory "" "$PA1"

# --- Lane A false-positive #1a: same markers but KO falsify phrase '반증' in body ---
TA2=$(build_transcript_tool_result "query returned 0 rows, error present")
PA2=$(build_ask_payload "$TA2" "어떤 방식이 좋을까요? (반증: A가 틀렸다면 row 수가 늘어야 함)" \
  '[{"label":"방식 A (Recommended)","description":"do A"},{"label":"방식 B","description":"do B"}]')
run_case "Lane A false-pos: '반증' falsify phrase in body → pass" pass "" "$PA2"

# --- Lane A false-positive #1b: EN falsify phrase 'if this is wrong' in body ---
TA3=$(build_transcript_tool_result "the verification is failing with an error")
PA3=$(build_ask_payload "$TA3" "Pick one. If this is wrong, the row count should change." \
  '[{"label":"Plan A (Recommended)","description":"do A"},{"label":"Plan B","description":"do B"}]')
run_case "Lane A false-pos: 'if this is wrong' phrase in body → pass" pass "" "$PA3"

# --- Lane A false-positive #2: no evaluative marker ---
TA4=$(build_transcript_tool_result "query returned 0 rows and an error")
PA4=$(build_ask_payload "$TA4" "Which approach should we take next?" \
  '[{"label":"Option A","description":"do A"},{"label":"Option B","description":"do B"}]')
run_case "Lane A false-pos: no evaluative marker → pass" pass "" "$PA4"

# --- Lane A edge: marker present but transcript has NO negative evidence ---
TA5=$(build_transcript_tool_result "query returned 42 rows successfully, all good")
PA5=$(build_ask_payload "$TA5" "Which approach should we take next?" \
  '[{"label":"Option A (Recommended)","description":"do A"},{"label":"Option B","description":"do B"}]')
run_case "Lane A edge: marker but no negative evidence → pass" pass "" "$PA5"

# --- Lane A edge: missing transcript → fail-open pass ---
PA6=$(build_ask_payload "/nonexistent/transcript-$$.jsonl" "Which approach?" \
  '[{"label":"Option A (Recommended)","description":"do A"},{"label":"Option B","description":"do B"}]')
run_case "Lane A edge: missing transcript → pass (fail-open)" pass "" "$PA6"

# --- Lane A edge: KO evaluative marker '안전한' + negative evidence → advisory ---
TA7=$(build_transcript_tool_result "결과가 비었고 error 발생")
PA7=$(build_ask_payload "$TA7" "어떤 방식으로 진행할까요?" \
  '[{"label":"방식 A","description":"가장 안전한 선택"},{"label":"방식 B","description":"대안"}]')
run_case "Lane A edge: KO '안전한' marker + negative evidence → advisory" advisory "" "$PA7"

# --- Lane A bypass: positive payload but PRAXIS_FALSIFY_GATE_BYPASS=1 → pass ---
TA8=$(build_transcript_tool_result "query returned 0 rows and a timeout error")
PA8=$(build_ask_payload "$TA8" "Which approach should we take next?" \
  '[{"label":"Option A (Recommended)","description":"do A"},{"label":"Option B","description":"do B"}]')
run_case "Lane A bypass: BYPASS=1 on positive payload → pass" pass "PRAXIS_FALSIFY_GATE_BYPASS=1" "$PA8"

# ===========================================================================
# Lane B
# ===========================================================================

# --- Lane B positive: 3× identical 'git status' under one session ---
SID_B="lane-b-positive-session"
PB1=$(build_bash_payload "$SID_B" "git status")
run_case "Lane B: 1st git status → pass" pass "" "$PB1"
run_case "Lane B: 2nd git status → pass" pass "" "$PB1"
run_case "Lane B: 3rd git status → advisory" advisory "" "$PB1"

# --- Lane B false-positive: 'git commit' is not read-only ---
SID_BC="lane-b-commit-session"
PB2=$(build_bash_payload "$SID_BC" 'git commit -m "msg"')
run_case "Lane B false-pos: git commit ×1 → pass" pass "" "$PB2"
run_case "Lane B false-pos: git commit ×2 → pass" pass "" "$PB2"
run_case "Lane B false-pos: git commit ×3 → pass (not read-only)" pass "" "$PB2"

# --- Lane B false-positive: 3× DIFFERENT subcommands (status/get/list) ---
SID_BD="lane-b-distinct-session"
PB3a=$(build_bash_payload "$SID_BD" "git status")
PB3b=$(build_bash_payload "$SID_BD" "kubectl get pods")
PB3c=$(build_bash_payload "$SID_BD" "gh pr list")
run_case "Lane B false-pos: git status (distinct ×1) → pass" pass "" "$PB3a"
run_case "Lane B false-pos: kubectl get (distinct ×1) → pass" pass "" "$PB3b"
run_case "Lane B false-pos: gh pr list (distinct ×1) → pass" pass "" "$PB3c"

# --- Lane B bypass: 3× git status but BYPASS=1 on the 3rd → pass ---
SID_BB="lane-b-bypass-session"
PB4=$(build_bash_payload "$SID_BB" "git status")
run_case "Lane B bypass: 1st git status (bypass) → pass" pass "PRAXIS_FALSIFY_GATE_BYPASS=1" "$PB4"
run_case "Lane B bypass: 2nd git status (bypass) → pass" pass "PRAXIS_FALSIFY_GATE_BYPASS=1" "$PB4"
run_case "Lane B bypass: 3rd git status (bypass) → pass (bypass suppresses)" pass "PRAXIS_FALSIFY_GATE_BYPASS=1" "$PB4"

# ===========================================================================
# Edges / fail-open
# ===========================================================================

run_case "malformed JSON payload → graceful pass" pass "" "not even json"

PE1=$(python3 -c 'import json; print(json.dumps({"tool_name":"Read","tool_input":{"file_path":"/tmp/x"}}))')
run_case "non-target tool (Read) → pass" pass "" "$PE1"

PE2=$(python3 -c 'import json; print(json.dumps({"tool_name":"AskUserQuestion","tool_input":{}}))')
run_case "AskUserQuestion with no questions → pass" pass "" "$PE2"

PE3=$(python3 -c 'import json; print(json.dumps({"session_id":"s","tool_name":"Bash","tool_input":{}}))')
run_case "Bash with no command → pass" pass "" "$PE3"

# ---------------------------------------------------------------------------
# @fail_open structural assertion
# ---------------------------------------------------------------------------

# main() opts into the shared @fail_open guard; verify the decorator is
# applied (fail-open behaviour itself is covered in tests/test_hook_runtime.sh).
_uncaught_out=$(python3 - << PYEOF 2>&1
import sys, importlib.util, io
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
if getattr(mod.main, "__wrapped__", None) is None:
    sys.stderr.write("main not wrapped by @fail_open\n"); sys.exit(1)
PYEOF
)
_uncaught_rc=$?
if [ "$_uncaught_rc" -eq 0 ] && [ -z "$_uncaught_out" ]; then
  echo "  PASS  main() is wrapped by the shared @fail_open guard (exit 0, no stderr)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  main() not wrapped by @fail_open (rc=$_uncaught_rc, out=$(echo "$_uncaught_out" | head -c 200))"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("main() is wrapped by the shared @fail_open guard")
fi

# ---------------------------------------------------------------------------
# Summary
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
