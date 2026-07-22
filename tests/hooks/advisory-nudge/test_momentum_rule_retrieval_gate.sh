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

# --- merge-briefing escalation (issue #797) -----------------------------------
#
# When the assistant text preceding a `gh pr merge` lacks the Pre-Merge
# Reporting briefing (< 4 of 6 items), the merge trigger escalates to a
# permissionDecision deny on stdout. The stderr advisory still fires. Escalation
# requires a readable transcript — the existing merge tests carry no
# transcript_path, so they stay advisory-only (regression-safe).
FIXTURES_DIR="$SCRIPT_DIR/../../fixtures"

# run_merge_escalation_case name expect_deny(yes|no) env_args transcript_fixture
run_merge_escalation_case() {
  local name="$1" expect_deny="$2" env_args="$3" fixture="$4"
  local transcript="$FIXTURES_DIR/$fixture"
  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": "gh pr merge --squash --delete-branch"},
    "transcript_path": sys.argv[1],
    "session_id": "test-momentum-merge",
}))' "$transcript")

  local out_file err_file
  out_file=$(mktemp); err_file=$(mktemp)
  if [ -n "$env_args" ]; then
    # shellcheck disable=SC2086
    echo "$payload" | env $env_args python3 "$HOOK" >"$out_file" 2>"$err_file"
  else
    echo "$payload" | python3 "$HOOK" >"$out_file" 2>"$err_file"
  fi
  local rc=$?
  local out err
  out=$(cat "$out_file"); err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  [ "$rc" -eq 0 ] || ok=0
  # stderr advisory must always fire on the merge trigger.
  echo "$err" | grep -qF "[praxis:momentum-gate]" || ok=0
  if [ "$expect_deny" = "yes" ]; then
    echo "$out" | grep -qF '"permissionDecision": "deny"' || ok=0
  else
    echo "$out" | grep -qF '"permissionDecision"' && ok=0
  fi

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expect_deny=$expect_deny rc=$rc"
    [ -n "$out" ] && echo "        stdout: $(echo "$out" | head -c 200)"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# run_merge_escalation_cmd_case name expect_deny env_args transcript_fixture command
# Variant carrying a custom merge command so the PR-number correlation
# (window extension, issue #826) can be exercised.
run_merge_escalation_cmd_case() {
  local name="$1" expect_deny="$2" env_args="$3" fixture="$4" command="$5"
  local transcript="$FIXTURES_DIR/$fixture"
  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[2]},
    "transcript_path": sys.argv[1],
    "session_id": "test-momentum-merge",
}))' "$transcript" "$command")

  local out_file err_file
  out_file=$(mktemp); err_file=$(mktemp)
  if [ -n "$env_args" ]; then
    # shellcheck disable=SC2086
    echo "$payload" | env $env_args python3 "$HOOK" >"$out_file" 2>"$err_file"
  else
    echo "$payload" | python3 "$HOOK" >"$out_file" 2>"$err_file"
  fi
  local rc=$?
  local out err
  out=$(cat "$out_file"); err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  [ "$rc" -eq 0 ] || ok=0
  echo "$err" | grep -qF "[praxis:momentum-gate]" || ok=0
  if [ "$expect_deny" = "yes" ]; then
    echo "$out" | grep -qF '"permissionDecision": "deny"' || ok=0
  else
    echo "$out" | grep -qF '"permissionDecision"' && ok=0
  fi

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expect_deny=$expect_deny rc=$rc"
    [ -n "$out" ] && echo "        stdout: $(echo "$out" | head -c 200)"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# Window extension (issue #826): briefing in the PRIOR turn + short approval
# reply ("ok") + merge of the SAME PR → no deny (the mandated flow).
run_merge_escalation_cmd_case "merge_escalation_prior_turn_briefing_passes" \
  "no" "" "momentum-merge-prior-turn-briefing.jsonl" "gh pr merge 833 --squash --delete-branch"

# Prior-turn briefing is for a DIFFERENT PR (#999) than the merge target (833)
# → correlation fails, extension denied → deny.
run_merge_escalation_cmd_case "merge_escalation_prior_turn_wrong_pr_denies" \
  "yes" "" "momentum-merge-prior-turn-wrong-pr.jsonl" "gh pr merge 833 --squash"

# Last user message is a substantive instruction, not an approval reply → no
# window extension → deny.
run_merge_escalation_cmd_case "merge_escalation_substantive_reply_denies" \
  "yes" "" "momentum-merge-substantive-reply.jsonl" "gh pr merge 833 --squash"

# Tool-only turn, no recorded briefing narration, single human message (no
# approval reply) → fidelity fallback removed (issue #826), so a genuine
# briefing skip now denies rather than demoting to advisory.
run_merge_escalation_case "merge_escalation_tool_only_no_briefing_denies" \
  "yes" "" "momentum-merge-fidelity-unreliable.jsonl"

# Numberless branch merge (`gh pr merge --squash`, the project standard): the
# window carries the mandated Pre-Merge probe (`gh pr checks 833`), so the real
# target (833) is derived and correlated against the prior-turn briefing → pass.
run_merge_escalation_cmd_case "merge_escalation_numberless_context_pr_passes" \
  "no" "" "momentum-merge-prior-turn-briefing.jsonl" "gh pr merge --squash --delete-branch"

# Numberless merge but NO Pre-Merge probe in the window → target unresolvable →
# fail closed (No Approval Transfer: an unresolved target may differ from the
# briefed PR). Deny even though a prior-turn briefing + approval exist.
run_merge_escalation_cmd_case "merge_escalation_numberless_no_probe_denies" \
  "yes" "" "momentum-merge-numberless-no-probe.jsonl" "gh pr merge --squash --delete-branch"

# Flag value must not be misread as the merge target (P2#6): `merge feature-x
# --subject 999` targets branch `feature-x` (numberless), NOT PR 999. Target
# resolves to None → context PR 833 (from the probe) correlates → pass. A
# regression that read 999 as the target would fail correlation and wrongly deny.
run_merge_escalation_cmd_case "merge_escalation_flag_value_not_target" \
  "no" "" "momentum-merge-prior-turn-briefing.jsonl" "gh pr merge feature-x --subject 999 --squash"

# Shell loop repeating the merge (`for pr in 833 999; do gh pr merge "$pr"`) →
# one approval must not ride a loop → extension disabled → deny (P1#3). The `do`
# keyword is peeled by strip_prefix so the inner `gh pr merge` IS detected.
run_merge_escalation_cmd_case "merge_escalation_loop_repetition_denies" \
  "yes" "" "momentum-merge-prior-turn-briefing.jsonl" 'for pr in 833 999; do gh pr merge "$pr" --squash; done'

# Compound multi-target (`merge 833 && merge 999`) → one approval cannot cover
# both → no extension → deny (No Approval Transfer).
run_merge_escalation_cmd_case "merge_escalation_multi_target_denies" \
  "yes" "" "momentum-merge-prior-turn-briefing.jsonl" "gh pr merge 833 --squash && gh pr merge 999 --squash"

# Fused global flag (`gh --repo=o/r pr merge 833`) → tokenizer still extracts
# target 833, correlation passes → no deny.
run_merge_escalation_cmd_case "merge_escalation_fused_repo_flag_passes" \
  "no" "" "momentum-merge-prior-turn-briefing.jsonl" "gh --repo=o/r pr merge 833 --squash"

# Trivial-PR briefing in the PRIOR turn + approval → carve-out applies, no deny.
run_merge_escalation_cmd_case "merge_escalation_prior_turn_trivial_exempt" \
  "no" "" "momentum-merge-prior-turn-trivial.jsonl" "gh pr merge 833 --squash"

# Incomplete briefing (3 of 6 items) → deny. Reproduces the #795 failure shape.
run_merge_escalation_case "merge_escalation_incomplete_briefing" \
  "yes" "" "momentum-merge-incomplete.jsonl"

# Complete 6-item briefing → advisory only, no deny.
run_merge_escalation_case "merge_escalation_complete_briefing" \
  "no" "" "momentum-merge-complete.jsonl"

# Substring-pollution regression (CodeRabbit): a changed+not-verified+risk
# briefing is 3 real items. "미검증" ⊃ "검증" must NOT also satisfy the verified
# group (which would inflate to 4 and wrongly pass). Expect deny.
run_merge_escalation_case "merge_escalation_notverified_substring_denies" \
  "yes" "" "momentum-merge-notverified-substring.jsonl"

# Direct count assertion: not-verified phrasing must NOT double-count as verified.
notverified_count_case() {
  local name="briefing_count_notverified_not_double_counted"
  local out
  out=$(python3 - "$HOOK" << 'PYEOF'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("impl", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
cases = {
    "en": "변경 사항: x. Prod path is not verified. 리스크 낮음.",
    "ko": "변경 사항: x. prod 경로 미검증. 리스크 낮음.",
    "unverified": "what changed: x. prod path unverified. risk low.",
}
bad = [f"{k}={m._briefing_item_count(v)}" for k, v in cases.items() if m._briefing_item_count(v) >= 4]
# Complete briefing must still score both verified AND not-verified (6 total).
full = "변경 사항: x. 테스트 통과 확인. 미검증: prod. 리스크 없음. 남은 항목 없음. Approve merge?"
if m._briefing_item_count(full) != 6:
    bad.append(f"complete={m._briefing_item_count(full)}!=6")
print("OK" if not bad else "BAD:" + ",".join(bad))
PYEOF
)
  if [ "$out" = "OK" ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] $out"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}
notverified_count_case

# Trivial-PR marker present → carve-out, no deny even with 0 items.
run_merge_escalation_case "merge_escalation_trivial_pr_exempt" \
  "no" "" "momentum-merge-trivial.jsonl"

# Background delegate session → no escalation (mirror pre-merge-approval-gate).
run_merge_escalation_case "merge_escalation_delegate_skips" \
  "no" "CMUX_DELEGATE=1" "momentum-merge-incomplete.jsonl"

# Demote-to-advisory escape hatch → no deny, advisory still fires.
run_merge_escalation_case "merge_escalation_advisory_env_demotes" \
  "no" "PRAXIS_MOMENTUM_MERGE_ADVISORY=1" "momentum-merge-incomplete.jsonl"

# Full bypass silences everything, including the escalation.
merge_escalation_bypass_silent_case() {
  local name="merge_escalation_full_bypass_silent"
  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": "gh pr merge --squash"},
    "transcript_path": sys.argv[1],
}))' "$FIXTURES_DIR/momentum-merge-incomplete.jsonl")
  local out_file err_file
  out_file=$(mktemp); err_file=$(mktemp)
  echo "$payload" | env PRAXIS_MOMENTUM_BYPASS=1 python3 "$HOOK" >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file"); err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"
  if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] rc=$rc out='$out' err-first='$(echo "$err" | head -1)'"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}
merge_escalation_bypass_silent_case

# Force-push / dispatch triggers must NEVER emit a deny — escalation is merge-only.
run_nonmerge_no_deny_case() {
  local name="$1" command="$2"
  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
    "transcript_path": sys.argv[2],
}))' "$command" "$FIXTURES_DIR/momentum-merge-incomplete.jsonl")
  local out_file
  out_file=$(mktemp)
  echo "$payload" | python3 "$HOOK" >"$out_file" 2>/dev/null
  local rc=$?
  local out
  out=$(cat "$out_file"); rm -f "$out_file"
  if [ "$rc" -eq 0 ] && ! echo "$out" | grep -qF '"permissionDecision"'; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] rc=$rc out='$(echo "$out" | head -c 120)'"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}
run_nonmerge_no_deny_case "force_push_never_denies" "git push --force origin main"
run_nonmerge_no_deny_case "dispatch_never_denies" 'cmux new-workspace --command "claude -p go"'

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
