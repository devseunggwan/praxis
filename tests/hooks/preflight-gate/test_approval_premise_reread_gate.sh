#!/bin/bash
# test_approval_premise_reread_gate.sh — coverage for approval-premise-reread-gate
#
# The gate emits permissionDecision "ask" on stdout at exit 0; a call it does not
# care about produces no stdout at all. Both branches are asserted, because a
# gate that silently stops firing looks exactly like a quiet session.
#
# Case list comes from the input-surface enumeration recorded in spec.md, not
# from the implementation: the marker arrives with different spacing, casing and
# flag names, and the MCP leaf name is classified by token rather than by
# substring. Every variant there is a case here.
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

# --- Bash: the marker's spacing, casing and flag-name variants -------------
run_case "flag_space"        "$(verdict "$(bash_payload 'hubctl dev trigger --phase prod')")"            "ask"
run_case "flag_equals"       "$(verdict "$(bash_payload 'hubctl dev trigger --phase=prod')")"            "ask"
run_case "flag_double_space" "$(verdict "$(bash_payload 'hubctl dev trigger --phase  prod')")"           "ask"
run_case "flag_uppercase"    "$(verdict "$(bash_payload 'hubctl dev trigger --phase PROD')")"            "ask"
run_case "flag_short"        "$(verdict "$(bash_payload 'hubctl dev trigger -p prod')")"                 "ask"
run_case "flag_profile"      "$(verdict "$(bash_payload 'aws s3 rm s3://bucket/key --profile prod')")"   "ask"
run_case "flag_env_suffixed" "$(verdict "$(bash_payload 'deploy --env prod-apne2')")"                    "ask"
run_case "flag_production"   "$(verdict "$(bash_payload 'hubctl dev trigger --phase production-mirror')")" "ask"

# --- Bash: what must stay quiet -------------------------------------------
run_case "no_marker"         "$(verdict "$(bash_payload 'ls -la')")"                                     "quiet"
run_case "dev_phase"         "$(verdict "$(bash_payload 'hubctl dev trigger --phase dev')")"             "quiet"
# Bare `production` is deliberately not a marker: this branch has no mutation
# filter, so a read-only query carrying it would fire on every namespace call.
run_case "bare_production"   "$(verdict "$(bash_payload 'kubectl get pods -n production')")"             "quiet"

# --- Bash: a prod-marked command that is provably read-only stays quiet ----
# The filter recognises read-only shapes rather than mutating ones, so an
# unrecognised command falls through to "ask". Each case below is a shape the
# rule set already names as a sanctioned read-only production call.
run_case "ro_kubectl_get"    "$(verdict "$(bash_payload 'kubectl get pods -n prod-apne2')")"             "quiet"
run_case "ro_kubectl_logs"   "$(verdict "$(bash_payload 'kubectl logs pod-x --profile prod')")"          "quiet"
run_case "ro_hubctl_token"   "$(verdict "$(bash_payload 'hubctl token fetch datadog --phase prod')")"    "quiet"
run_case "ro_aws_sts"        "$(verdict "$(bash_payload 'aws sts get-caller-identity --profile prod')")" "quiet"
run_case "ro_git_log"        "$(verdict "$(bash_payload 'git log --oneline --phase prod')")"             "quiet"
run_case "ro_gh_pr_view"     "$(verdict "$(bash_payload 'gh pr view 1 --profile prod')")"                "quiet"
run_case "ro_gh_api_get"     "$(verdict "$(bash_payload 'gh api repos/o/r/issues --profile prod')")"     "quiet"
run_case "ro_sudo_wrapper"   "$(verdict "$(bash_payload 'sudo kubectl get pods --profile prod')")"       "quiet"
run_case "ro_env_prefix"     "$(verdict "$(bash_payload 'FOO=1 kubectl get pods --profile prod')")"      "quiet"
run_case "ro_abs_path"       "$(verdict "$(bash_payload '/usr/bin/kubectl get pods --profile prod')")"   "quiet"
run_case "ro_pipe_both_read" "$(verdict "$(bash_payload 'kubectl get pods --profile prod | grep Running')")" "quiet"

# --- Bash: every segment must be read-only, not just the first -------------
# A pipeline or chain whose later segment mutates is the case this filter must
# never wave through -- the first segment reads, and reading only that is how a
# gate goes quiet on a deletion.
run_case "mut_pipe_tail"   "$(verdict "$(bash_payload 'kubectl get pods --profile prod | xargs kubectl delete pod')")" "ask"
run_case "mut_chain_tail"  "$(verdict "$(bash_payload 'kubectl get pods --profile prod && kubectl delete pod x')")"    "ask"
run_case "mut_semicolon"   "$(verdict "$(bash_payload 'aws sts get-caller-identity --profile prod; rm -rf /tmp/x')")"  "ask"
run_case "mut_redirect"    "$(verdict "$(bash_payload 'kubectl get pods --profile prod > /tmp/out')")"                 "ask"
run_case "mut_kubectl_del" "$(verdict "$(bash_payload 'kubectl delete pod x --profile prod')")"                        "ask"
run_case "mut_aws_rm"      "$(verdict "$(bash_payload 'aws s3 rm s3://b/k --profile prod')")"                          "ask"
run_case "mut_gh_api_post" "$(verdict "$(bash_payload 'gh api -X POST repos/o/r/issues --profile prod')")"             "ask"
run_case "mut_unknown_bin" "$(verdict "$(bash_payload 'some-unknown-binary --phase prod')")"                           "ask"

# --- Bash: the subcommand is read by position, never by membership ---------
# Scanning every token for a read-only word would call these read-only, because
# the word appears as an argument value rather than as the verb.
run_case "pos_gh_create_titled_view" "$(verdict "$(bash_payload 'gh pr create --title view --profile prod')")" "ask"
run_case "pos_git_commit_msg_log"    "$(verdict "$(bash_payload 'git commit -m log --phase prod')")"           "ask"
run_case "pos_git_branch_delete"     "$(verdict "$(bash_payload 'git branch -D x --phase prod')")"             "ask"

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
