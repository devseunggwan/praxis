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
  tmpdir=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
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
  tmpdir=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
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

# #1092: path-prefixed binaries must still trigger. `argv[0] == "gh"` /
# `argv[0] == "git"` exact matches previously let `/usr/bin/gh pr merge` and
# `/usr/bin/git push --force` bypass the gate; `_is_gh_binary`/`_is_git_binary`
# close both.
run_case "strict_mode_blocks_path_prefixed_merge" \
  "block" \
  "PRAXIS_MOMENTUM_STRICT=1" \
  "/usr/bin/gh pr merge --squash"

run_case "strict_mode_blocks_path_prefixed_force_push" \
  "block" \
  "PRAXIS_MOMENTUM_STRICT=1" \
  "/usr/bin/git push --force"

# #1092: `+refspec` force pushes carry no `--force`/`-f` flag. `_is_force_push`
# now flags a leading-`+` refspec (`git push origin +main`,
# `git push origin '+refs/heads/*'`) as a force push.
run_case "strict_mode_blocks_refspec_plus_force_push" \
  "block" \
  "PRAXIS_MOMENTUM_STRICT=1" \
  "git push origin +main"

run_case "strict_mode_blocks_refspec_plus_glob_force_push" \
  "block" \
  "PRAXIS_MOMENTUM_STRICT=1" \
  "git push origin '+refs/heads/*'"

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

# --- issue #985: non-merge commands that only look like a merge -------------
#
# Both families used to reach the briefing gate, which then demanded a merge
# briefing for a command that merges nothing. The must-still-fire cases at the
# end are what keeps the fix from turning into a bypass.

run_case "help_long_flag_is_not_a_merge" \
  "silent" \
  "" \
  "gh pr merge --help"

run_case "help_short_flag_is_not_a_merge" \
  "silent" \
  "" \
  "gh pr merge -h"

run_case "help_flag_after_pr_number_is_not_a_merge" \
  "silent" \
  "" \
  "gh pr merge 985 --help"

run_case "heredoc_body_quoting_a_merge_is_not_a_merge" \
  "silent" \
  "" \
  "$(printf 'git commit -m "$(cat <<'EOF'\ngh pr merge is only quoted here\nEOF\n)"')"

run_case "heredoc_unquoted_delimiter_body_is_not_a_merge" \
  "silent" \
  "" \
  "$(printf 'cat <<EOF\ngh pr merge 985\nEOF')"

run_case "heredoc_dash_form_body_is_not_a_merge" \
  "silent" \
  "" \
  "$(printf 'cat <<-EOF\n\tgh pr merge 985\n\tEOF')"

run_case "herestring_is_not_a_heredoc_and_stays_silent" \
  "silent" \
  "" \
  "grep x <<< 'gh pr merge 985'"

# Must still fire — the fix must not become a bypass.

run_case "help_flag_as_a_subject_value_still_merges" \
  "advisory:TRIGGER: gh pr merge" \
  "" \
  "gh pr merge 985 --squash --subject -h"

run_case "merge_after_heredoc_terminator_still_merges" \
  "advisory:TRIGGER: gh pr merge" \
  "" \
  "$(printf 'cat <<'EOF'\nbody\nEOF\ngh pr merge 985 --squash')"

run_case "arithmetic_shift_does_not_swallow_a_later_merge" \
  "advisory:TRIGGER: gh pr merge" \
  "" \
  'echo $((1 << 3)); gh pr merge 985 --squash'

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

# run_merge_escalation_case name expect_deny(yes|no) env_args transcript_fixture [command]
# The optional 5th arg overrides the merge command (defaults to the standard
# numberless squash merge), letting PR-number correlation (window extension,
# issue #826) be exercised without a second near-identical helper.
run_merge_escalation_case() {
  local name="$1" expect_deny="$2" env_args="$3" fixture="$4"
  local command="${5:-gh pr merge --squash --delete-branch}"
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

# Window extension (issue #826): briefing in the PRIOR turn + short approval
# reply ("ok") + merge of the SAME PR → no deny (the mandated flow).
run_merge_escalation_case "merge_escalation_prior_turn_briefing_passes" \
  "no" "" "momentum-merge-prior-turn-briefing.jsonl" "gh pr merge 833 --squash --delete-branch"

# Prior-turn briefing is for a DIFFERENT PR (#999) than the merge target (833)
# → correlation fails, extension denied → deny.
run_merge_escalation_case "merge_escalation_prior_turn_wrong_pr_denies" \
  "yes" "" "momentum-merge-prior-turn-wrong-pr.jsonl" "gh pr merge 833 --squash"

# --- serial-merge window cut (issue #1214) -----------------------------------
#
# All three fixtures share a PARTIAL prior-turn briefing (2 of 6 items), so the
# bare merge denies in every one of them and only the marker can release it —
# the load-bearing population #1214 targets. Against the shipped gate all three
# release; the cut is what separates them, not the fixture text.
#
#   serial-second        first merge RAN      → window spent   → deny
#   serial-blocked-retry first merge is_error → nothing spent  → allow
#   serial-rebriefed     briefing item after  → re-armed       → allow
#
# Positive control that the fixtures are genuinely load-bearing: the same three
# with the marker stripped.
run_merge_escalation_case "merge_serial_second_bare_denies" \
  "yes" "" "momentum-merge-serial-second.jsonl" "gh pr merge 999 --squash --delete-branch"
run_merge_escalation_case "merge_serial_blocked_retry_bare_denies" \
  "yes" "" "momentum-merge-serial-blocked-retry.jsonl" "gh pr merge 999 --squash --delete-branch"
run_merge_escalation_case "merge_serial_rebriefed_bare_denies" \
  "yes" "" "momentum-merge-serial-rebriefed.jsonl" "gh pr merge 999 --squash --delete-branch"

# The marker attests to completeness, never to existence — a spent window has
# nothing left for it to be about, so it does not release the second merge.
run_merge_escalation_case "merge_serial_second_with_marker_denies" \
  "yes" "" "momentum-merge-serial-second.jsonl" \
  "gh pr merge 999 --squash --delete-branch # briefing-surfaced: prior turn"

# A merge the gate itself blocked never executed, so the retry is not serial —
# without this the gate would make its own block unrecoverable.
run_merge_escalation_case "merge_serial_blocked_first_retry_passes" \
  "no" "" "momentum-merge-serial-blocked-retry.jsonl" \
  "gh pr merge 999 --squash --delete-branch # briefing-surfaced: prior turn"

# A briefing item authored after the first merge re-arms the window on its own.
run_merge_escalation_case "merge_serial_rebriefed_passes" \
  "no" "" "momentum-merge-serial-rebriefed.jsonl" \
  "gh pr merge 999 --squash --delete-branch # briefing-surfaced: prior turn"

# A merge on the right of `||` never ran, yet the call exits clean. Crediting it
# would advance the cut past a briefing nothing consumed and deny THIS merge —
# the over-block direction. `&&` needs no such guard: it skips the merge only
# when its left side fails, which makes the whole tool_result `is_error`.
run_merge_escalation_case "merge_serial_skipped_branch_not_credited" \
  "no" "" "momentum-merge-serial-skipped-branch.jsonl" \
  "gh pr merge 999 --squash --delete-branch # briefing-surfaced: prior turn"

# Positive control: the same fixture is load-bearing — bare, it still denies.
run_merge_escalation_case "merge_serial_skipped_branch_bare_denies" \
  "yes" "" "momentum-merge-serial-skipped-branch.jsonl" \
  "gh pr merge 999 --squash --delete-branch"

# A newline is a clause boundary too — the approval sits on its own line under
# the briefing. Whitespace normalization used to collapse it before the split,
# so the newline alternation in _CLAUSE_TAIL_RE was dead (CodeRabbit, PR #1089).
approval_probe=$(python3 - "$ROOT_DIR" <<'PY_A'
import importlib.util as u, sys
spec = u.spec_from_file_location(
    "m", f"{sys.argv[1]}/hooks/advisory-nudge/momentum-rule-retrieval-gate/impl.py")
m = u.module_from_spec(spec); spec.loader.exec_module(m)
print(m._is_approval_reply("briefing\napprove"))
PY_A
)
if [ "$approval_probe" = "True" ]; then
  echo "PASS  [approval_reply_newline_is_a_clause_boundary]"; PASS=$((PASS + 1))
else
  echo "FAIL  [approval_reply_newline_is_a_clause_boundary] got=$approval_probe"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("approval_reply_newline_is_a_clause_boundary")
fi

# A SENTENCE that ends in an approval is still an approval (issue #1087). Exact
# string equality denied these, so the mandated briefing → approval → merge flow
# was blocked whenever the user approved in prose rather than one word.
run_merge_escalation_case "merge_escalation_sentence_approval_ko_passes" \
  "no" "" "momentum-merge-sentence-approval-ko.jsonl" "gh pr merge 833 --squash"

run_merge_escalation_case "merge_escalation_sentence_approval_en_passes" \
  "no" "" "momentum-merge-sentence-approval-en.jsonl" "gh pr merge 833 --squash"

# NEGATIVE CONTROL: an approval token appears mid-sentence but the LAST clause
# is a fresh instruction → still not an approval reply → deny. This is the
# property exact equality was protecting, and clause splitting must keep it.
run_merge_escalation_case "merge_escalation_trailing_instruction_denies" \
  "yes" "" "momentum-merge-sentence-trailing-instruction.jsonl" "gh pr merge 833 --squash"

# The deny message must not present the env bypass as an inline prefix (issue
# #1087). The hook is spawned by the harness, not as a child of the command, so
# `PRAXIS_MOMENTUM_MERGE_ADVISORY=1 gh pr merge …` can never reach it — guidance
# that reads as runnable sends the author down a path that always fails.
deny_msg=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": "gh pr merge 833 --squash"},
    "transcript_path": sys.argv[1],
    "session_id": "test-momentum-merge",
}))' "$FIXTURES_DIR/momentum-merge-substantive-reply.jsonl" | python3 "$HOOK" 2>&1)
if printf '%s' "$deny_msg" | grep -q 'session environment'; then
  echo "PASS  [merge_escalation_deny_names_env_reachability]"; PASS=$((PASS + 1))
else
  echo "FAIL  [merge_escalation_deny_names_env_reachability] msg=<$deny_msg>"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("merge_escalation_deny_names_env_reachability")
fi

# Last user message is a substantive instruction, not an approval reply → no
# window extension → deny.
run_merge_escalation_case "merge_escalation_substantive_reply_denies" \
  "yes" "" "momentum-merge-substantive-reply.jsonl" "gh pr merge 833 --squash"

# Tool-only turn, no recorded briefing narration, single human message (no
# approval reply) → fidelity fallback removed (issue #826), so a genuine
# briefing skip now denies rather than demoting to advisory.
run_merge_escalation_case "merge_escalation_tool_only_no_briefing_denies" \
  "yes" "" "momentum-merge-fidelity-unreliable.jsonl"

# Numberless branch merge (`gh pr merge --squash`, the project standard): the
# window carries the mandated Pre-Merge probe (`gh pr checks 833`), so the real
# target (833) is derived and correlated against the prior-turn briefing → pass.
run_merge_escalation_case "merge_escalation_numberless_context_pr_passes" \
  "no" "" "momentum-merge-prior-turn-briefing.jsonl" "gh pr merge --squash --delete-branch"

# Numberless merge but NO Pre-Merge probe in the window → target unresolvable →
# fail closed (No Approval Transfer: an unresolved target may differ from the
# briefed PR). Deny even though a prior-turn briefing + approval exist.
run_merge_escalation_case "merge_escalation_numberless_no_probe_denies" \
  "yes" "" "momentum-merge-numberless-no-probe.jsonl" "gh pr merge --squash --delete-branch"

# Named-branch merge (`gh pr merge feature-for-999`) targets that branch's PR,
# which the window probe (#833) does NOT identify → fail closed → deny (round-3
# P1b: a prior probe of a different PR is not this branch's target). The `-for-`
# in the branch name must also NOT be read as a loop keyword (whole-token match).
run_merge_escalation_case "merge_escalation_named_branch_denies" \
  "yes" "" "momentum-merge-prior-turn-briefing.jsonl" "gh pr merge feature-for-999 --squash"

# Repo flag trailing the subcommand (`gh pr merge -R o/r 833`) must skip its
# value so the target resolves to 833, not `o/r` (round-3 P1d). 833 correlates
# with the prior-turn briefing → pass. Value-flag arity skip (`--subject 999`
# not read as target) is exercised here too since -R sits before the positional.
run_merge_escalation_case "merge_escalation_repo_flag_arity_passes" \
  "no" "" "momentum-merge-prior-turn-briefing.jsonl" "gh pr merge -R o/r 833 --squash"

# Shell loop repeating the merge (`for pr in 833 999; do gh pr merge "$pr"`) →
# one approval must not ride a loop → extension disabled → deny (P1#3). The `do`
# keyword is peeled by strip_prefix so the inner `gh pr merge` IS detected.
run_merge_escalation_case "merge_escalation_loop_repetition_denies" \
  "yes" "" "momentum-merge-prior-turn-briefing.jsonl" 'for pr in 833 999; do gh pr merge "$pr" --squash; done'

# Compound multi-target (`merge 833 && merge 999`) → one approval cannot cover
# both → no extension → deny (No Approval Transfer).
run_merge_escalation_case "merge_escalation_multi_target_denies" \
  "yes" "" "momentum-merge-prior-turn-briefing.jsonl" "gh pr merge 833 --squash && gh pr merge 999 --squash"

# --- `# briefing-surfaced` marker re-verification (issue #940) -----------------
#
# The marker used to short-circuit before the transcript was read, so it
# released a merge no matter how far back the last briefing was — measured at
# 8 of 11 merges with no preceding block at all, one 627 events out. It now
# stands in for a briefing's COMPLETENESS (the counter reads prose and
# under-counts), never for its EXISTENCE.

# Marker present, no briefing anywhere in the window → deny. This is the case
# that passed before the fix.
run_merge_escalation_case "merge_escalation_marker_without_briefing_denies" \
  "yes" "" "momentum-merge-marker-no-briefing.jsonl" \
  "gh pr merge 833 --squash  # briefing-surfaced: attested"

# Same fixture, same command WITHOUT the marker → also deny. Pins that the deny
# above comes from the absent briefing, not from the marker itself being read
# as a violation.
run_merge_escalation_case "merge_escalation_no_briefing_no_marker_denies" \
  "yes" "" "momentum-merge-marker-no-briefing.jsonl" "gh pr merge 833 --squash"

# Briefing present but phrased so the counter recognises only 1 of 6 items;
# marker attests the rest → pass. Without this the marker would have no purpose
# left and the fix would be an outright removal.
run_merge_escalation_case "merge_escalation_marker_with_partial_briefing_passes" \
  "no" "" "momentum-merge-marker-partial-briefing.jsonl" \
  "gh pr merge 833 --squash  # briefing-surfaced: counter under-counted"

# Same partial briefing without the marker → deny (1 item < 4).
run_merge_escalation_case "merge_escalation_partial_briefing_no_marker_denies" \
  "yes" "" "momentum-merge-marker-partial-briefing.jsonl" "gh pr merge 833 --squash"

# The mandated flow puts the briefing in the turn BEFORE the approval, so the
# current turn is empty. A partial briefing there plus the marker must release
# the merge: scoring the current turn alone would deny a merge whose briefing
# the user did see — the exact failure the marker exists to absorb.
run_merge_escalation_case "merge_escalation_marker_with_prior_turn_partial_passes" \
  "no" "" "momentum-merge-prior-turn-partial-briefing.jsonl" \
  "gh pr merge 833 --squash  # briefing-surfaced: counter under-counted"

# Same prior-turn partial briefing WITHOUT the marker → deny (1 item < 4). Pins
# that the pass above is bought by the marker, not by the extension path.
run_merge_escalation_case "merge_escalation_prior_turn_partial_no_marker_denies" \
  "yes" "" "momentum-merge-prior-turn-partial-briefing.jsonl" "gh pr merge 833 --squash"

# Marker + prior turn that briefed a DIFFERENT PR → deny. The lower marker floor
# reads the prior turn only when the merge correlates to it (No Approval
# Transfer); an uncorrelated window must not supply the item it needs.
run_merge_escalation_case "merge_escalation_marker_uncorrelated_prior_turn_denies" \
  "yes" "" "momentum-merge-prior-turn-partial-briefing.jsonl" \
  "gh pr merge 999 --squash  # briefing-surfaced: attested"

# An injected (`isMeta`) user entry — a skill body loaded mid-turn — must not
# start a new window and discard the briefing the user actually saw.
run_merge_escalation_case "merge_escalation_injected_meta_keeps_window_passes" \
  "no" "" "momentum-merge-injected-meta-turn.jsonl" "gh pr merge 833 --squash"

# --- verb gate checklist on both channels (issues #873, #932) -----------------
#
# The briefing is one of several gates on `gh pr merge`. Before #873 the deny
# named only its own requirement, so the rest were each discovered by a separate
# block, costing a retry turn apiece.
#
# The checklist must go out on BOTH channels, because neither alone reaches the
# model in every case:
#   - decision JSON — `_dispatch.py:195-201` surfaces only the FIRST deny's
#     stdout, so a sibling denying first (gh-merge-worktree-precondition on
#     `--delete-branch`) discards this hook's reason string;
#   - stderr — forwarded unconditionally, but a PreToolUse hook's stderr reaches
#     the model only when the dispatcher exits 2 (the deny path), never on the
#     exit-0 ask path. #873 shipped stderr-only and #932 caught the gap.
run_merge_checklist_case() {
  local name="merge_checklist_on_both_channels"
  local payload out err ok=1
  local out_file err_file
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": "gh pr merge --squash --delete-branch"},
    "transcript_path": sys.argv[1],
    "session_id": "test-momentum-checklist",
}))' "$FIXTURES_DIR/momentum-merge-fidelity-unreliable.jsonl")
  out_file=$(mktemp); err_file=$(mktemp)
  echo "$payload" | python3 "$HOOK" >"$out_file" 2>"$err_file"
  out=$(cat "$out_file"); err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  echo "$out" | grep -qF '"permissionDecision": "deny"' || ok=0

  # Assert against the decision REASON, not raw stdout — a token matching
  # anywhere else in the JSON envelope would be a false pass.
  local reason
  reason=$(printf '%s' "$out" | python3 -c '
import json, sys
print(json.load(sys.stdin)["hookSpecificOutput"]["permissionDecisionReason"])
') || ok=0

  # Derive every gate name from the registry itself rather than restating a
  # list here. A hardcoded subset is the same under-enumeration defect this
  # checklist exists to remove: #932 review found this test naming 5 of the 8
  # registered gates, so a new registry entry could ship untested.
  local tokens
  tokens=$(python3 -c '
import re, sys
sys.path.insert(0, sys.argv[1])
from block_message import verb_gate_checklist
print("\n".join(sorted(set(re.findall(r"←\s*(\S+)", verb_gate_checklist("gh pr merge"))))))
' "$ROOT_DIR/hooks/_lib")

  [ -n "$tokens" ] || { ok=0; echo "        derived zero gate names from the registry"; }

  local token
  while IFS= read -r token; do
    [ -n "$token" ] || continue
    printf '%s' "$err" | grep -qF "$token" || { ok=0; echo "        missing from stderr: $token"; }
    printf '%s' "$reason" | grep -qF "$token" || { ok=0; echo "        missing from decision reason: $token"; }
  done <<< "$tokens"

  # Relief affordances are prose, not `←`-tagged gate names, so they are not
  # derivable above and stay explicit.
  for token in "PRAXIS_MOMENTUM_MERGE_ADVISORY=1" "briefing-surfaced:"; do
    printf '%s' "$err" | grep -qF "$token" || { ok=0; echo "        missing from stderr: $token"; }
    printf '%s' "$reason" | grep -qF "$token" || { ok=0; echo "        missing from decision reason: $token"; }
  done

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name]"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}
run_merge_checklist_case

# Fused global flag (`gh --repo=o/r pr merge 833`) → tokenizer still extracts
# target 833, correlation passes → no deny.
run_merge_escalation_case "merge_escalation_fused_repo_flag_passes" \
  "no" "" "momentum-merge-prior-turn-briefing.jsonl" "gh --repo=o/r pr merge 833 --squash"

# Trivial-PR briefing in the PRIOR turn + approval → carve-out applies, no deny.
run_merge_escalation_case "merge_escalation_prior_turn_trivial_exempt" \
  "no" "" "momentum-merge-prior-turn-trivial.jsonl" "gh pr merge 833 --squash"

# Incomplete briefing (3 of 6 items) → deny. Reproduces the #795 failure shape.
run_merge_escalation_case "merge_escalation_incomplete_briefing" \
  "yes" "" "momentum-merge-incomplete.jsonl"

# Complete 6-item briefing → advisory only, no deny.
run_merge_escalation_case "merge_escalation_complete_briefing" \
  "no" "" "momentum-merge-complete.jsonl"

# Natural Korean wording regression (issue #1118): a real 6-item briefing
# blocked at 3/6 because group0/2/4 used "무엇이 바뀌는 것" / "검증 안 된" /
# "열린 항목" instead of the hook's dictionary phrasing. Now 6/6 → no deny.
run_merge_escalation_case "merge_escalation_natural_korean_wording_passes" \
  "no" "" "momentum-merge-natural-korean-wording.jsonl"

# Same natural wording but only 2 of 6 items present → still denied. Proves
# the group0/2/4 vocab widening did not neuter the gate.
run_merge_escalation_case "merge_escalation_natural_korean_partial_denies" \
  "yes" "" "momentum-merge-natural-korean-partial.jsonl"

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

# Issue #1055: CMUX_DELEGATE=1 is inert — an incomplete briefing still denies.
run_merge_escalation_case "merge_escalation_delegate_no_longer_skips" \
  "yes" "CMUX_DELEGATE=1" "momentum-merge-incomplete.jsonl"

# Demote-to-advisory escape hatch → no deny, advisory still fires.
run_merge_escalation_case "merge_escalation_advisory_env_demotes" \
  "no" "PRAXIS_MOMENTUM_MERGE_ADVISORY=1" "momentum-merge-incomplete.jsonl"

# In-band bypass marker (issue #826): `# briefing-surfaced` in the command
# demotes to advisory where the env var cannot reach the hook. Incomplete
# briefing fixture would otherwise deny → marker present → no deny.
run_merge_escalation_case "merge_escalation_briefing_marker_demotes" \
  "no" "" "momentum-merge-incomplete.jsonl" "gh pr merge --squash # briefing-surfaced: env unreachable in this harness"

# Marker with no reason still demotes (reason is recommended, not required).
run_merge_escalation_case "merge_escalation_briefing_marker_bare_demotes" \
  "no" "" "momentum-merge-incomplete.jsonl" "gh pr merge --squash --delete-branch # briefing-surfaced"

# Sanity: WITHOUT the marker the same incomplete briefing still denies (the
# marker, not the fixture, is what demotes).
run_merge_escalation_case "merge_escalation_no_marker_still_denies" \
  "yes" "" "momentum-merge-incomplete.jsonl" "gh pr merge --squash --delete-branch"

# round-1 P1: one self-attestation must not release a compound multi-merge.
# Marker present but TWO merge segments → not demoted → deny.
run_merge_escalation_case "merge_escalation_marker_multi_merge_denies" \
  "yes" "" "momentum-merge-incomplete.jsonl" "gh pr merge 833 --squash && gh pr merge 999 --squash # briefing-surfaced"

# round-1 P1: a looped merge with the marker also stays denied.
run_merge_escalation_case "merge_escalation_marker_loop_denies" \
  "yes" "" "momentum-merge-incomplete.jsonl" 'for pr in 833 999; do gh pr merge "$pr" --squash; done # briefing-surfaced'

# round-1 P2: the marker inside a QUOTED argument is data, not an attestation
# comment → must NOT bypass. `grep '# briefing-surfaced' …` before the merge
# still denies on the incomplete briefing.
run_merge_escalation_case "merge_escalation_marker_quoted_denies" \
  "yes" "" "momentum-merge-incomplete.jsonl" "grep '# briefing-surfaced' spec.md && gh pr merge --squash"

# round-2 P2a: a mid-word `#` (no preceding word boundary) is NOT a shell
# comment in Bash. `echo x# briefing-surfaced` is an argument, not an
# attestation → must still deny (shlex commenters="#" wrongly stripped this).
run_merge_escalation_case "merge_escalation_marker_midword_hash_denies" \
  "yes" "" "momentum-merge-incomplete.jsonl" "echo x# briefing-surfaced && gh pr merge --squash"

# round-2 P2b: a reason clause containing a loop keyword ("for") must not trip
# the repetition guard — segment/repetition run on the comment-stripped code
# only, so the single merge still demotes.
run_merge_escalation_case "merge_escalation_marker_reason_with_for_demotes" \
  "no" "" "momentum-merge-incomplete.jsonl" "gh pr merge --squash # briefing-surfaced: required for bridge harness"

# round-3 P2: a `# briefing-surfaced` inside a single-quoted string that spans
# physical lines is DATA, not a comment. Quote state must persist across the
# newline so the marker is not misdetected → incomplete briefing still denies.
run_merge_escalation_case "merge_escalation_marker_multiline_quoted_denies" \
  "yes" "" "momentum-merge-incomplete.jsonl" $'gh pr merge --squash\nprintf \'line\n# briefing-surfaced\n\''

# round-3: a backslash-escaped `\#` is a literal, not a comment start → deny.
run_merge_escalation_case "merge_escalation_marker_escaped_hash_denies" \
  "yes" "" "momentum-merge-incomplete.jsonl" "gh pr merge --squash \\# briefing-surfaced"

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
