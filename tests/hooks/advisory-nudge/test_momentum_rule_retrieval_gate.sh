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
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/momentum-rule-retrieval-gate/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

# Pin memory directory to the test fixtures so dynamic memory loading is
# deterministic regardless of the host's user-scoped memory directory.
export PRAXIS_MEMORY_DIR="$SCRIPT_DIR/../../fixtures/momentum-memories"

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

run_case "cmux_dispatch_codex_provider" \
  "advisory:[praxis:momentum-gate]" \
  "" \
  'cmux new-workspace --command "codex exec do thing"'

run_case "cmux_dispatch_gemini_provider" \
  "advisory:[praxis:momentum-gate]" \
  "" \
  'cmux new-workspace --command "gemini run task"'

run_case "cmux_dispatch_fused_command_flag" \
  "advisory:[praxis:momentum-gate]" \
  "" \
  'cmux new-workspace --command=claude'

# Round-2 MAJOR 1 fix: ensure non-dispatch cmux invocations stay silent.
run_case "silent_cmux_new_workspace_plain" \
  "silent" \
  "" \
  "cmux new-workspace test-foo"

run_case "silent_cmux_new_workspace_echo" \
  "silent" \
  "" \
  'cmux new-workspace --command "echo hello"'

run_case "silent_cmux_new_workspace_shell" \
  "silent" \
  "" \
  'cmux new-workspace dev --command "bash -i"'

# issue #513 결함2: provider name as an identifier substring (CLAUDE_API_KEY)
# must NOT false-fire the dispatch advisory — word-boundary match required.
run_case "silent_cmux_provider_substring_in_env_var" \
  "silent" \
  "" \
  'cmux new-workspace --command "CLAUDE_API_KEY=abc myapp"'

# issue #513 결함2 negative: a genuine provider invocation still fires.
run_case "cmux_dispatch_provider_word_boundary" \
  "advisory:[praxis:momentum-gate]" \
  "" \
  'cmux new-workspace --command "claude -p do real work"'

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

# Round-2 MAJOR 2 fix: git global flags with separate-token value must not
# disguise the push subcommand from the walker.
run_case "git_dash_c_kv_force_push" \
  "advisory:feedback_force_history_rewrite_mutation" \
  "" \
  "git -c user.name=x push --force origin main"

run_case "git_dash_C_path_force_push" \
  "advisory:feedback_force_history_rewrite_mutation" \
  "" \
  "git -C /tmp/repo push -f origin my-branch"

run_case "git_long_global_force_push" \
  "advisory:feedback_force_history_rewrite_mutation" \
  "" \
  "git --git-dir=/tmp/repo/.git push --force-with-lease"

# Confirm fused-form git global flags also bypass correctly.
run_case "git_fused_global_force_push" \
  "advisory:feedback_force_history_rewrite_mutation" \
  "" \
  "git --work-tree=/tmp/repo push --force"

# Round-3 MAJOR fix: boolean git global flags MUST NOT consume the next token
# as a value. Prior over-broad GIT_GLOBAL_FLAGS_WITH_ARG inclusion of
# --literal-pathspecs / --super-prefix caused the walker to skip `push`,
# masking the force-push gate entirely.
run_case "git_literal_pathspecs_force_push" \
  "advisory:feedback_force_history_rewrite_mutation" \
  "" \
  "git --literal-pathspecs push --force"

run_case "git_super_prefix_fused_force_push" \
  "advisory:feedback_force_history_rewrite_mutation" \
  "" \
  "git --super-prefix=x push --force"

# Issue #512: bundled short-flag clusters carrying `f` (=--force) must fire
# (the prior exact-match force check let `-fu`/`-fv`/`-vf` bypass the gate).
run_case "git_push_bundled_fu_force" \
  "advisory:feedback_force_history_rewrite_mutation" \
  "" \
  "git push origin main -fu"

run_case "git_push_bundled_fv_force" \
  "advisory:feedback_force_history_rewrite_mutation" \
  "" \
  "git push origin main -fv"

run_case "git_push_bundled_vf_force" \
  "advisory:feedback_force_history_rewrite_mutation" \
  "" \
  "git push origin main -vf"

# Clean push with a bundled non-force cluster must stay silent.
run_case "silent_git_push_bundled_no_force" \
  "silent" \
  "" \
  "git push origin main -u"

# --- dynamic memory loading negative cases (issue #359) ----------------------
# Memory with no momentum: field must NOT surface on any trigger.
negative_no_momentum_case() {
  local name="negative_no_momentum_marker_absent"
  local payload
  payload=$(build_payload "Bash" "gh pr merge --squash")
  local err_file
  err_file=$(mktemp)
  echo "$payload" | python3 "$HOOK" >/dev/null 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"
  if [ "$rc" -eq 0 ] && [[ "$err" != *"marker-uniq-no-momentum-marker"* ]]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] rc=$rc unexpectedly emitted marker-uniq-no-momentum-marker"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}
negative_no_momentum_case

# Empty memory dir → static rule still emitted, no Memory: line.
empty_memory_dir_case() {
  local name="empty_memory_dir_static_only"
  local tmpdir
  tmpdir=$(mktemp -d)
  local payload
  payload=$(build_payload "Bash" "gh pr merge --squash")
  local err_file
  err_file=$(mktemp)
  echo "$payload" | env PRAXIS_MEMORY_DIR="$tmpdir" python3 "$HOOK" >/dev/null 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"; rmdir "$tmpdir"
  if [ "$rc" -eq 0 ] && [[ "$err" == *"Pre-Merge Reporting"* ]] && [[ "$err" != *"Memory:"* ]]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    # Diagnostics computed into vars first — a nested $([[ ... ]] ...) inside a
    # double-quoted echo trips the conditional-expression parser on both bashes,
    # but with different blast radius: on bash 5.x it is a parse-time error that
    # fails `bash -n`, aborting the whole test file on CI; on bash 3.2 it errors
    # only at command-substitution runtime inside this FAIL branch, corrupting
    # the diagnostic line but not the verdict — which is why the suite passed
    # locally and the breakage surfaced only on Linux CI.
    local _static_emitted _memory_cite
    [[ "$err" == *"Pre-Merge Reporting"* ]] && _static_emitted=yes || _static_emitted=no
    [[ "$err" == *"Memory:"* ]] && _memory_cite=yes || _memory_cite=no
    echo "FAIL  [$name] rc=$rc static-rule-emitted=$_static_emitted memory-cite=$_memory_cite"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}
empty_memory_dir_case

# Codex round-1 P2 regression: force-push trigger MUST emit actionable warning
# (history-rewrite mutation rule) even when no migrated memory exists. Prior
# refactor left force-push as memory-cite only, so unmigrated installs got
# only a header line on the highest-risk trigger.
empty_memory_dir_force_push_case() {
  local name="empty_memory_dir_force_push_actionable"
  local tmpdir
  tmpdir=$(mktemp -d)
  local payload
  payload=$(build_payload "Bash" "git push --force origin main")
  local err_file
  err_file=$(mktemp)
  echo "$payload" | env PRAXIS_MEMORY_DIR="$tmpdir" python3 "$HOOK" >/dev/null 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"; rmdir "$tmpdir"
  if [ "$rc" -eq 0 ] && [[ "$err" == *"History rewrite is a mutation"* ]] && [[ "$err" != *"Memory:"* ]]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    # See note at the sibling diagnostic above: nested $([[ ... ]] ...) inside a
    # double-quoted echo is a bash-5.x syntax error; compute into vars first.
    local _actionable_emitted _memory_cite
    [[ "$err" == *"History rewrite is a mutation"* ]] && _actionable_emitted=yes || _actionable_emitted=no
    [[ "$err" == *"Memory:"* ]] && _memory_cite=yes || _memory_cite=no
    echo "FAIL  [$name] rc=$rc actionable-emitted=$_actionable_emitted memory-cite=$_memory_cite"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}
empty_memory_dir_force_push_case

# Combination: boolean + value-taking global flag together must both be
# walked past correctly so the subcommand check still lands on `push`.
run_case "git_literal_pathspecs_and_c_force_push" \
  "advisory:feedback_force_history_rewrite_mutation" \
  "" \
  "git --literal-pathspecs -c user.name=x push -f"

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
#
# Round-2 MINOR 2 fix: a compound command that contains BOTH `gh pr merge` and
# `cmux new-workspace --command "claude ..."` must surface BOTH the merge AND
# the dispatch rule blocks — not just the first trigger encountered.

run_case "compound_merge_and_dispatch_merge_surface" \
  "advisory:TRIGGER: gh pr merge" \
  "" \
  'gh pr merge --squash && cmux new-workspace --command "claude -p next"'

run_case "compound_merge_and_dispatch_dispatch_surface" \
  "advisory:TRIGGER: cmux new-workspace" \
  "" \
  'gh pr merge --squash && cmux new-workspace --command "claude -p next"'

# Dedicated multi-trigger inspector — asserts BOTH surfaces appear in the SAME
# stderr capture, not separated by re-invocation.
run_compound_multi_assert() {
  local name="$1" command="$2"
  local payload
  payload=$(build_payload "Bash" "$command")
  local err_file
  err_file=$(mktemp)
  echo "$payload" | python3 "$HOOK" >/dev/null 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"

  local ok=1
  [ "$rc" -eq 0 ] || ok=0
  echo "$err" | grep -qF "TRIGGER: gh pr merge" || ok=0
  echo "$err" | grep -qF "TRIGGER: cmux new-workspace" || ok=0

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] rc=$rc"
    [ -n "$err" ] && echo "        stderr first lines: $(echo "$err" | head -3)"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

run_compound_multi_assert \
  "compound_emits_both_surfaces_same_run" \
  'gh pr merge --squash && cmux new-workspace --command "claude -p next"'

run_compound_force_merge_assert() {
  local name="$1" command="$2"
  local payload
  payload=$(build_payload "Bash" "$command")
  local err_file
  err_file=$(mktemp)
  echo "$payload" | python3 "$HOOK" >/dev/null 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"

  local ok=1
  [ "$rc" -eq 0 ] || ok=0
  echo "$err" | grep -qF "TRIGGER: git push --force" || ok=0
  echo "$err" | grep -qF "TRIGGER: gh pr merge" || ok=0

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] rc=$rc"
    [ -n "$err" ] && echo "        stderr: $(echo "$err" | head -3)"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

run_compound_force_merge_assert \
  "compound_force_push_and_merge_both_surfaces" \
  'git push --force && gh pr merge --squash'

run_compound_multi_assert_force_dispatch() {
  local name="$1" command="$2"
  local payload
  payload=$(build_payload "Bash" "$command")
  local err_file
  err_file=$(mktemp)
  echo "$payload" | python3 "$HOOK" >/dev/null 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"

  local ok=1
  [ "$rc" -eq 0 ] || ok=0
  echo "$err" | grep -qF "TRIGGER: git push --force" || ok=0
  echo "$err" | grep -qF "TRIGGER: cmux new-workspace" || ok=0

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] rc=$rc"
    [ -n "$err" ] && echo "        stderr: $(echo "$err" | head -3)"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

run_compound_multi_assert_force_dispatch \
  "compound_force_and_dispatch_both_surfaces" \
  'git push --force && cmux new-workspace --command "claude -p next"'

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
