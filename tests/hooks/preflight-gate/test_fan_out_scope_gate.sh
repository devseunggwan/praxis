#!/bin/bash
# test_fan_out_scope_gate.sh — coverage for hooks/preflight-gate/fan-out-scope-gate/impl.py
#
# Synthesizes Claude Code PreToolUse hook payloads plus a transcript fixture
# and asserts:
#   ask    → exit 0 + stdout JSON has permissionDecision "ask"
#   silent → exit 0 + stdout empty (no JSON, no permissionDecision)
#
# Usage: bash tests/hooks/preflight-gate/test_fan_out_scope_gate.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/fan-out-scope-gate/impl.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

TMPDIR=$(mktemp -d) || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
trap 'rm -rf "$TMPDIR"' EXIT

USER_MSG='{"type":"user","message":{"role":"user","content":"이 PR 하나 확인하도록 분산"}}'

# --- fixtures -------------------------------------------------------------

# No target yet in this turn.
TX_EMPTY="$TMPDIR/tx-empty.jsonl"
cat > "$TX_EMPTY" <<EOF
$USER_MSG
EOF

# One successful workspace creation already in this turn.
TX_ONE_OK="$TMPDIR/tx-one-ok.jsonl"
cat > "$TX_ONE_OK" <<EOF
$USER_MSG
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"cmux workspace create --name a --command 'bash x.sh'"}}]}}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","is_error":false}]}}
EOF

# The one prior creation FAILED — no worker started.
TX_ONE_FAILED="$TMPDIR/tx-one-failed.jsonl"
cat > "$TX_ONE_FAILED" <<EOF
$USER_MSG
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"cmux workspace create --name a"}}]}}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","is_error":true}]}}
EOF

# One successful Agent call already in this turn.
TX_ONE_AGENT="$TMPDIR/tx-one-agent.jsonl"
cat > "$TX_ONE_AGENT" <<EOF
$USER_MSG
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"Agent","input":{"prompt":"investigate"}}]}}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","is_error":false}]}}
EOF

# The prior creation belongs to a PREVIOUS turn (a real user message follows it).
TX_PRIOR_TURN="$TMPDIR/tx-prior-turn.jsonl"
cat > "$TX_PRIOR_TURN" <<EOF
{"type":"user","message":{"role":"user","content":"먼저 이것부터"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"t0","name":"Bash","input":{"command":"cmux workspace create --name old"}}]}}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t0","is_error":false}]}}
$USER_MSG
EOF

# A prior turn-mate that only PRINTED the command.
TX_ONE_ECHOED="$TMPDIR/tx-one-echoed.jsonl"
cat > "$TX_ONE_ECHOED" <<EOF
$USER_MSG
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"echo cmux workspace create --name a"}}]}}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","is_error":false}]}}
EOF

# --- runner ---------------------------------------------------------------

run_case() {
  local name="$1" expectation="$2" payload="$3"

  local out_file
  out_file=$(mktemp)
  echo "$payload" | python3 "$HOOK" >"$out_file" 2>/dev/null
  local rc=$?
  local out
  out=$(cat "$out_file")
  rm -f "$out_file"

  local ok=1
  case "$expectation" in
    ask)
      [ "$rc" -eq 0 ] || ok=0
      echo "$out" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    decision = d.get('hookSpecificOutput', {}).get('permissionDecision', '')
    sys.exit(0 if decision == 'ask' else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null || ok=0
      ;;
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ] || ok=0
      ;;
    *)
      echo "  internal: unknown expectation '$expectation'" >&2
      ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    PASS=$((PASS + 1))
    echo "  PASS  $name"
  else
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
    echo "  FAIL  $name (rc=$rc, expected=$expectation)"
    [ -n "$out" ] && echo "        stdout: $out" | head -c 400
  fi
}

bash_payload() {  # $1=transcript $2=command
  python3 -c "
import json, sys
print(json.dumps({
    'tool_name': 'Bash',
    'tool_input': {'command': sys.argv[2]},
    'transcript_path': sys.argv[1],
}))
" "$1" "$2"
}

agent_payload() {  # $1=transcript
  python3 -c "
import json, sys
print(json.dumps({
    'tool_name': 'Agent',
    'tool_input': {'prompt': 'investigate'},
    'transcript_path': sys.argv[1],
}))
" "$1"
}

echo "test_fan_out_scope_gate"

# --- 1st target in a turn → SILENT

run_case "1st workspace creation in the turn (silent)" \
  "silent" "$(bash_payload "$TX_EMPTY" "cmux workspace create --name a")"

run_case "1st Agent call in the turn (silent)" \
  "silent" "$(agent_payload "$TX_EMPTY")"

# --- 2nd target in a turn → ASK

run_case "2nd workspace creation after a successful one (ask)" \
  "ask" "$(bash_payload "$TX_ONE_OK" "cmux workspace create --name b")"

run_case "legacy alias new-workspace counts as a target (ask)" \
  "ask" "$(bash_payload "$TX_ONE_OK" "cmux new-workspace --name b --cwd /tmp")"

run_case "env-var prefix does not exempt a target (ask)" \
  "ask" "$(bash_payload "$TX_ONE_OK" "env CMUX_QUIET=1 cmux workspace create --name b")"

run_case "2nd Agent call after a successful Agent call (ask)" \
  "ask" "$(agent_payload "$TX_ONE_AGENT")"

run_case "Agent call after a workspace creation — mixed surfaces (ask)" \
  "ask" "$(agent_payload "$TX_ONE_OK")"

run_case "workspace creation after an Agent call — mixed surfaces (ask)" \
  "ask" "$(bash_payload "$TX_ONE_AGENT" "cmux workspace create --name b")"

run_case "marker comment does not bypass (ask)" \
  "ask" "$(bash_payload "$TX_ONE_OK" "cmux workspace create --name b # fan-out-mapped: 지시에 대응")"

run_case "chained command: creation in the 2nd segment (ask)" \
  "ask" "$(bash_payload "$TX_ONE_OK" "git status && cmux workspace create --name b")"

# --- prior call did not create a target → SILENT

run_case "prior creation failed — no worker started (silent)" \
  "silent" "$(bash_payload "$TX_ONE_FAILED" "cmux workspace create --name b")"

run_case "prior creation belongs to a previous turn (silent)" \
  "silent" "$(bash_payload "$TX_PRIOR_TURN" "cmux workspace create --name b")"

run_case "prior command only echoed the creation (silent)" \
  "silent" "$(bash_payload "$TX_ONE_ECHOED" "cmux workspace create --name b")"

# --- the intercepted call is not a target → SILENT

run_case "--help rehearsal is not a target (silent)" \
  "silent" "$(bash_payload "$TX_ONE_OK" "cmux workspace create --help")"

run_case "--dry-run rehearsal is not a target (silent)" \
  "silent" "$(bash_payload "$TX_ONE_OK" "cmux workspace create --name b --dry-run")"

run_case "echoed creation is not a target (silent)" \
  "silent" "$(bash_payload "$TX_ONE_OK" "echo cmux workspace create --name b")"

run_case "cmux workspace list is not a creation (silent)" \
  "silent" "$(bash_payload "$TX_ONE_OK" "cmux workspace list --json")"

run_case "cmux workspace close is not a creation (silent)" \
  "silent" "$(bash_payload "$TX_ONE_OK" "cmux workspace close workspace:99")"

run_case "unrelated Bash command (silent)" \
  "silent" "$(bash_payload "$TX_ONE_OK" "git status --porcelain")"

run_case "unrelated tool name (silent)" \
  "silent" '{"tool_name":"Read","tool_input":{"file_path":"/tmp/x"},"transcript_path":"'"$TX_ONE_OK"'"}'

# --- fail-open

run_case "missing transcript_path (silent)" \
  "silent" '{"tool_name":"Bash","tool_input":{"command":"cmux workspace create --name b"}}'

run_case "unreadable transcript (silent)" \
  "silent" "$(bash_payload "$TMPDIR/does-not-exist.jsonl" "cmux workspace create --name b")"

run_case "malformed stdin (silent)" \
  "silent" 'not json at all'

echo
echo "  $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  printf '  failed: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
