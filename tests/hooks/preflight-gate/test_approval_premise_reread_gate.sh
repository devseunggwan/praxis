#!/bin/bash
# test_approval_premise_reread_gate.sh — coverage for approval-premise-reread-gate
#
# The gate emits permissionDecision "ask" on stdout at exit 0; a call it does not
# care about produces no stdout at all. Both branches are asserted, because a
# gate that silently stops firing looks exactly like a quiet session.
#
# Case list comes from the input-surface enumeration recorded in spec.md, not
# from the implementation: the marker arrives with different spacing, casing and
# the MCP leaf name is classified by token rather than by substring: eight
# read-only tools on the measured surface matched a verb as a substring.
#
# Usage: bash tests/hooks/preflight-gate/test_approval_premise_reread_gate.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/approval-premise-reread-gate/impl.py"

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

# Feed a payload; answer "ask" when the gate emitted a decision, "quiet" when it
# did not. A non-zero exit is its own answer -- this gate must never take one.
verdict() {
  local payload="$1" out status
  out=$(printf '%s' "$payload" | python3 "$HOOK" 2>/dev/null)
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "exit${status}"
  elif printf '%s' "$out" | grep -q '"permissionDecision": "ask"'; then
    echo "ask"
  else
    echo "quiet"
  fi
}

bash_payload() {
  python3 -c 'import json,sys; print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]}}))' "$1"
}

mcp_payload() {
  python3 -c 'import json,sys; print(json.dumps({"tool_name":sys.argv[1],"tool_input":json.loads(sys.argv[2])}))' "$1" "$2"
}

echo "test_approval_premise_reread_gate"

# --- Bash: what must stay quiet -------------------------------------------
run_case "no_marker"         "$(verdict "$(bash_payload 'ls -la')")"                                     "quiet"
run_case "dev_phase"         "$(verdict "$(bash_payload 'hubctl dev trigger --phase dev')")"             "quiet"
# Bare `production` is deliberately not a marker: this branch has no mutation
# filter, so a read-only query carrying it would fire on every namespace call.
run_case "bare_production"   "$(verdict "$(bash_payload 'kubectl get pods -n production')")"             "quiet"

# --- The acknowledgement is honoured on both surfaces ----------------------
run_case "bash_ack" \
  "$(verdict "$(bash_payload 'hubctl dev trigger --phase prod # approval-premise:ack run already recovered, re-checked')")" \
  "quiet"
run_case "mcp_ack" \
  "$(verdict "$(mcp_payload 'mcp__laplace-airflow__airflow_trigger_dag' '{"conf":{"phase":"prod"},"approval_premise_ack":"re-read"}')")" \
  "quiet"

# --- MCP: mutation classification by token, not by substring ---------------
# Each of these was misclassified by substring matching on the 374-tool surface.
run_case "mcp_trigger_fires" \
  "$(verdict "$(mcp_payload 'mcp__laplace-airflow__airflow_trigger_dag' '{"dag_id":"dag_sync_v0","conf":{"phase":"prod"}}')")" \
  "ask"
run_case "mcp_label_fires" \
  "$(verdict "$(mcp_payload 'mcp__claude_ai_Gmail__label_message' '{"phase":"prod"}')")" \
  "ask"
run_case "mcp_list_labels_quiet" \
  "$(verdict "$(mcp_payload 'mcp__claude_ai_Gmail__list_labels' '{"phase":"prod"}')")" \
  "quiet"
run_case "mcp_count_records_quiet" \
  "$(verdict "$(mcp_payload 'mcp__laplace-s3__s3_count_records' '{"phase":"prod"}')")" \
  "quiet"
run_case "mcp_component_sets_quiet" \
  "$(verdict "$(mcp_payload 'mcp__laplace-figma__figma_get_component_sets' '{"phase":"prod"}')")" \
  "quiet"

# --- MCP: a read-only call is out of scope even carrying the marker --------
run_case "mcp_query_quiet" \
  "$(verdict "$(mcp_payload 'mcp__laplace-trino__trino_query' '{"phase":"prod","sql":"select 1"}')")" \
  "quiet"
# ... and a mutation without the marker is equally out of scope.
run_case "mcp_mutation_no_marker_quiet" \
  "$(verdict "$(mcp_payload 'mcp__laplace-slack__slack_send_message' '{"channel":"dev-alerts"}')")" \
  "quiet"

# --- Fail open: a payload the gate cannot read must not block the session ---
run_case "malformed_payload"  "$(verdict 'not json at all')"                                             "quiet"
run_case "missing_tool_input" "$(verdict '{"tool_name":"Bash"}')"                                        "quiet"
run_case "null_tool_input"    "$(verdict '{"tool_name":"Bash","tool_input":null}')"                      "quiet"
run_case "empty_object"       "$(verdict '{}')"                                                          "quiet"

echo "----"
echo "PASS: $PASS / FAIL: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  printf 'failed: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
