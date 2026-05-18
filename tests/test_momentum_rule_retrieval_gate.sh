#!/bin/bash
# test_momentum_rule_retrieval_gate.sh — coverage for the momentum-rule-retrieval-gate hook.
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   advisory:<marker> → exit 0 + stderr contains <marker>
#   block             → exit 2 + stderr contains [praxis:momentum-gate]
#   silent            → exit 0 + stderr empty
#
# Usage: bash tests/test_momentum_rule_retrieval_gate.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$ROOT_DIR/hooks/momentum-rule-retrieval-gate.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# ---------------------------------------------------------------------------
# Helper: build_payload <tool_name> <command>
# ---------------------------------------------------------------------------
build_payload() {
  local tool_name="$1" command="$2"
  python3 -c '
import json, sys
d = {
    "tool_name": sys.argv[1],
    "tool_input": {"command": sys.argv[2]},
    "session_id": "test-momentum-$$",
}
print(json.dumps(d))' "$tool_name" "$command"
}

# ---------------------------------------------------------------------------
# run_case name expected env_args command
#   expected:
#     advisory:<marker>  → exit 0 + stderr contains <marker>
#     block              → exit 2 + stderr contains [praxis:momentum-gate]
#     silent             → exit 0 + stderr empty
#   env_args: list of VAR=VALUE to pass to env (or "" for none)
# ---------------------------------------------------------------------------
run_case() {
  local name="$1" expected="$2" env_args="$3" command="$4" tool_name="${5:-Bash}"

  local payload
  payload=$(build_payload "$tool_name" "$command")

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)

  if [ -n "$env_args" ]; then
    # shellcheck disable=SC2086
    echo "$payload" | env $env_args python3 "$HOOK" >"$out_file" 2>"$err_file"
  else
    echo "$payload" | python3 "$HOOK" >"$out_file" 2>"$err_file"
  fi
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expected" in
    advisory:*)
      local marker="${expected#advisory:}"
      [ "$rc" -eq 0 ] || ok=0
      echo "$err" | grep -qF "$marker" || ok=0
      ;;
    block)
      [ "$rc" -eq 2 ] || ok=0
      echo "$err" | grep -qF "[praxis:momentum-gate]" || ok=0
      ;;
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]  || ok=0
      ;;
    *)
      echo "FAIL  [$name] unknown expected: $expected"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected rc=$rc"
    [ -n "$out" ] && echo "        stdout: $out"
    [ -n "$err" ] && echo "        stderr: $(echo "$err" | head -5)"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# ===========================================================================
# Test cases
# ===========================================================================

# --- gh pr merge trigger -------------------------------------------------------

run_case "gh_pr_merge_basic" \
  "advisory:[praxis:momentum-gate]" \
  "" \
  "gh pr merge --squash --delete-branch"

run_case "gh_pr_merge_with_pr_number" \
  "advisory:Pre-Merge Reporting" \
  "" \
  "gh pr merge 42 --squash"

run_case "gh_pr_merge_compound_with_cleanup" \
  "advisory:No Approval Transfer" \
  "" \
  "cd /tmp/repo && gh pr merge --squash --delete-branch && git worktree remove ."

run_case "gh_pr_merge_repo_flag" \
  "advisory:[praxis:momentum-gate]" \
  "" \
  "gh -R owner/repo pr merge 123"

run_case "gh_pr_merge_content_check" \
  "advisory:feedback_pre_merge_briefing_compound_imperative" \
  "" \
  "gh pr merge --squash"

# --- cmux new-workspace (dispatch) trigger ------------------------------------

run_case "cmux_dispatch_basic" \
  "advisory:[praxis:momentum-gate]" \
  "" \
  'cmux new-workspace --command "claude -p do something"'

run_case "cmux_dispatch_content_check" \
  "advisory:Multi-PR" \
  "" \
  'cmux new-workspace myws --command "claude -p implement feature"'

run_case "cmux_dispatch_self_authored_labels" \
  "advisory:Self-Authored Labels" \
  "" \
  'cmux new-workspace --command "claude -p write code"'

# --- force-push triggers -------------------------------------------------------

run_case "git_push_force" \
  "advisory:[praxis:momentum-gate]" \
  "" \
  "git push --force"

run_case "git_push_force_f_flag" \
  "advisory:feedback_force_history_rewrite_mutation" \
  "" \
  "git push -f origin main"

run_case "git_push_force_with_lease" \
  "advisory:feedback_force_history_rewrite_mutation" \
  "" \
  "git push --force-with-lease origin my-branch"

run_case "git_push_force_with_lease_ref" \
  "advisory:[praxis:momentum-gate]" \
  "" \
  "git push --force-with-lease=origin/main origin my-branch"

# --- bypass env var ------------------------------------------------------------

run_case "bypass_env_gh_merge" \
  "silent" \
  "PRAXIS_MOMENTUM_BYPASS=1" \
  "gh pr merge --squash"

run_case "bypass_env_force_push" \
  "silent" \
  "PRAXIS_MOMENTUM_BYPASS=1" \
  "git push --force"

run_case "bypass_env_cmux" \
  "silent" \
  "PRAXIS_MOMENTUM_BYPASS=1" \
  'cmux new-workspace --command "claude -p foo"'

# --- strict mode ---------------------------------------------------------------

run_case "strict_mode_blocks_merge" \
  "block" \
  "PRAXIS_MOMENTUM_STRICT=1" \
  "gh pr merge --squash"

run_case "strict_mode_blocks_force_push" \
  "block" \
  "PRAXIS_MOMENTUM_STRICT=1" \
  "git push --force"

run_case "strict_mode_with_ack_passes" \
  "advisory:[praxis:momentum-gate]" \
  "PRAXIS_MOMENTUM_STRICT=1 PRAXIS_MOMENTUM_ACK=1" \
  "gh pr merge --squash"

# --- fail-open cases -----------------------------------------------------------

run_case "fail_open_non_bash_tool" \
  "silent" \
  "" \
  "gh pr merge --squash" \
  "Write"

run_case "fail_open_malformed_json" \
  "silent" \
  "" \
  "not-valid" \
  "MALFORMED"  # will use tool_name=MALFORMED which won't match Bash

run_case "fail_open_empty_command" \
  "silent" \
  "" \
  ""

# Override: pass malformed JSON directly for the real fail-open test.
run_case_raw_stdin() {
  local name="$1" expected="$2" stdin_data="$3"
  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)
  echo "$stdin_data" | python3 "$HOOK" >"$out_file" 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"
  local ok=1
  case "$expected" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]  || ok=0
      ;;
  esac
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected rc=$rc"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

run_case_raw_stdin "fail_open_malformed_json_raw" "silent" "{not valid json"

# --- silent cases (unrelated commands) -----------------------------------------

run_case "silent_git_push_normal" \
  "silent" \
  "" \
  "git push origin my-branch"

run_case "silent_gh_pr_list" \
  "silent" \
  "" \
  "gh pr list --state open"

run_case "silent_cmux_other_subcommand" \
  "silent" \
  "" \
  "cmux list-sessions"

run_case "silent_unrelated_bash" \
  "silent" \
  "" \
  "echo hello world"

run_case "silent_git_push_no_flag" \
  "silent" \
  "" \
  "git push origin feature-branch --tags"

# --- compound command multi-trigger ----------------------------------------

run_case "compound_merge_and_dispatch" \
  "advisory:TRIGGER: gh pr merge" \
  "" \
  'gh pr merge --squash && cmux new-workspace --command "claude -p next"'

# ===========================================================================
# Summary
# ===========================================================================

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed: ${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
