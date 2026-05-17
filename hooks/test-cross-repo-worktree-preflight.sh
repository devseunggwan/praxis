#!/bin/bash
# test-cross-repo-worktree-preflight.sh — coverage for the hook.
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   ask  → exit 0 + stdout JSON with permissionDecision "ask"
#   pass → exit 0 + stdout empty
#   block→ exit 2 + stderr non-empty   (currently unused; reserved)
#
# Usage: bash hooks/test-cross-repo-worktree-preflight.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="$SCRIPT_DIR/cross-repo-worktree-preflight.sh"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# Build two disposable git repos with worktrees so the hook has a real
# filesystem to query. The "cwd repo" owns ONE_OWNED_WT; the "other repo"
# owns OTHER_OWNED_WT. Asserting against OTHER_OWNED_WT from inside CWD_REPO
# must trigger the ask path.

TMPROOT=$(mktemp -d)
CWD_REPO="$TMPROOT/cwd-repo"
OTHER_REPO="$TMPROOT/other-repo"
ONE_OWNED_WT="$TMPROOT/cwd-repo-wt"
OTHER_OWNED_WT="$TMPROOT/other-repo-wt"

cleanup() {
  rm -rf "$TMPROOT" 2>/dev/null
}
trap cleanup EXIT

setup_repos() {
  # Pin identity via `-c` rather than relying on the caller's global
  # ~/.gitconfig — clean CI runners and fresh containers commonly have no
  # user.name / user.email set, and `git commit` aborts with
  # "Author identity unknown". Errors are NOT redirected to /dev/null —
  # if setup fails, the test must surface the cause loudly, not cascade
  # into opaque worktree-add failures downstream.
  local git_id=(-c "user.name=praxis-test" -c "user.email=praxis-test@example.invalid")
  for repo in "$CWD_REPO" "$OTHER_REPO"; do
    git init -q "$repo"
    git "${git_id[@]}" -C "$repo" commit --allow-empty -q -m init
  done
  git -C "$CWD_REPO" worktree add -q "$ONE_OWNED_WT" -b feature-a
  git -C "$OTHER_REPO" worktree add -q "$OTHER_OWNED_WT" -b feature-b
}

setup_repos

# run_case name expected command
#   expected: "ask"  (exit 0, stdout JSON with permissionDecision)
#             "pass" (exit 0, stdout empty)
run_case() {
  local name="$1" expected="$2" command="$3"

  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
}))' "$command")

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)
  # Run inside CWD_REPO so the hook's os.getcwd() lands in the cwd-repo.
  ( cd "$CWD_REPO" && echo "$payload" | "$HOOK" >"$out_file" 2>"$err_file" )
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expected" in
    ask)
      [ "$rc" -eq 0 ] || ok=0
      echo "$out" | grep -q '"permissionDecision": "ask"' || ok=0
      ;;
    pass)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    *)
      echo "FAIL  [$name] unknown expected: $expected"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected rc=$rc"
    [ -n "$out" ] && echo "        stdout: $out"
    [ -n "$err" ] && echo "        stderr: $err"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# run_passthrough name tool_name command
#   Tests non-Bash payloads / malformed input.
run_passthrough() {
  local name="$1" tool_name="$2" command="$3"
  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": sys.argv[1],
    "tool_input": {"command": sys.argv[2]},
}))' "$tool_name" "$command")

  local out_file err_file
  out_file=$(mktemp); err_file=$(mktemp)
  ( cd "$CWD_REPO" && echo "$payload" | "$HOOK" >"$out_file" 2>"$err_file" )
  local rc=$?
  local out err; out=$(cat "$out_file"); err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected silent pass rc=0 stdout='' stderr=''"
    [ -n "$out" ] && echo "        stdout: $out"
    [ -n "$err" ] && echo "        stderr: $err"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# === ASK paths (cross-repo target) =========================================

run_case "cross-repo abs path" \
  ask \
  "git worktree remove $OTHER_OWNED_WT"

run_case "cross-repo abs path with --force" \
  ask \
  "git worktree remove --force $OTHER_OWNED_WT"

run_case "cross-repo abs path with -f shorthand" \
  ask \
  "git worktree remove -f $OTHER_OWNED_WT"

run_case "cross-repo abs path with trailing slash" \
  ask \
  "git worktree remove $OTHER_OWNED_WT/"

run_case "cross-repo chained with gh pr merge" \
  ask \
  "git worktree remove $OTHER_OWNED_WT && echo done"

# Regression: -c name=value separated-arg global flag must not let the
# subcommand-finder mistake `name=value` for the subcommand and bail out
# silently (codex review round 1 — F1).
run_case "cross-repo with -c name=value global flag" \
  ask \
  "git -c user.name=foo worktree remove $OTHER_OWNED_WT"

# Regression: unquoted $() command substitution as a -c value used to be
# split by safe_tokenize across tokens, and only the first piece ('$(echo')
# was consumed as the -c value. The remaining piece ('k=v)') then sat in
# the subcommand slot and the hook returned silently — bypass. Codex
# review round 2 sibling finding (mirror of #248 F1). _coalesce_subst_runs
# coalesces the multi-token run into a single value before parsing.
run_case "cross-repo with -c \$(...) (unquoted multi-token subst)" \
  ask \
  "git -c \$(echo k=v) worktree remove $OTHER_OWNED_WT"

# === PASS paths (cwd repo owns target / opt-out / non-target) ==============

run_case "cwd-repo abs path owned" \
  pass \
  "git worktree remove $ONE_OWNED_WT"

run_case "cwd-repo main worktree" \
  pass \
  "git worktree remove $CWD_REPO"

# Regression (codex round 3 F1): relative paths used to be skipped
# unconditionally — bypassing the cross-repo check whenever the operator
# wrote `../sibling-wt` from inside repo A's worktree. Now relative
# targets are resolved against effective_cwd before lookup; if the
# resolved path is not in cwd repo's list, ask must fire.
# Setup: from CWD_REPO, relative path `../other-repo-wt` resolves to
# TMPROOT/other-repo-wt = OTHER_OWNED_WT — owned by OTHER_REPO.
run_case "cross-repo via relative path (../)" \
  ask \
  "git worktree remove ../other-repo-wt"

# But a relative path that resolves into cwd repo's own worktree must pass.
run_case "cwd-repo relative path (../cwd-repo-wt)" \
  pass \
  "git worktree remove ../cwd-repo-wt"

# Regression (codex round 3 F2): `cd /owning-repo && git worktree remove
# /owning-repo-wt` used to false-ask because the hook used the process
# cwd (CWD_REPO) instead of the post-cd cwd (OTHER_REPO). After tracking
# cd across segments, the lookup runs against OTHER_REPO's list and
# OTHER_OWNED_WT is recognized as owned by it.
run_case "cd to owning repo then remove its worktree" \
  pass \
  "cd $OTHER_REPO && git worktree remove $OTHER_OWNED_WT"

# Compound cd with relative target — both axes combined.
run_case "cd to owning repo then remove via relative" \
  pass \
  "cd $OTHER_REPO && git worktree remove ../other-repo-wt"

# cd target that we cannot statically resolve must NOT update
# effective_cwd; the hook then falls back to the original process cwd
# and the cross-repo target still triggers ask.
run_case "cd \$VAR (unresolvable) then cross-repo remove" \
  ask \
  "cd \$SOME_REPO && git worktree remove $OTHER_OWNED_WT"

run_case "opt-out marker present" \
  pass \
  "git worktree remove $OTHER_OWNED_WT  # worktree-chain:ack"

run_case "-C override to other repo" \
  pass \
  "git -C $OTHER_REPO worktree remove $OTHER_OWNED_WT"

run_case "--git-dir override" \
  pass \
  "git --git-dir=$OTHER_REPO/.git worktree remove $OTHER_OWNED_WT"

# Regression (codex round 4 F1): `--work-tree` ALONE does not switch git
# to the other repo's gitdir — git still uses cwd repo's .git/worktrees
# registry and fails with `not a working tree`. The hook must keep firing
# the ask. Previously this case was incorrectly classified as pass.
# Verified against real git: `cd /repo-a && git --work-tree=/repo-b
# worktree remove /repo-b-wt` -> fatal: '<path>' is not a working tree.
run_case "--work-tree alone is NOT an override (still cross-repo)" \
  ask \
  "git --work-tree=$OTHER_REPO worktree remove $OTHER_OWNED_WT"

# But --work-tree paired with --git-dir IS a real override.
run_case "--git-dir + --work-tree pair is an override" \
  pass \
  "git --git-dir=$OTHER_REPO/.git --work-tree=$OTHER_REPO worktree remove $OTHER_OWNED_WT"

run_case "non-remove worktree subcommand — list" \
  pass \
  "git worktree list"

run_case "non-remove worktree subcommand — add" \
  pass \
  "git worktree add /tmp/new-wt -b feature"

run_case "non-worktree git subcommand" \
  pass \
  "git status"

run_case "non-git command with worktree word" \
  pass \
  "echo 'git worktree remove /tmp/x'"

run_case "empty command" \
  pass \
  ""

# Comment-only command — must not parse as worktree remove.
run_case "comment-only command" \
  pass \
  "# git worktree remove $OTHER_OWNED_WT"

# === Infrastructure passthrough ============================================

run_passthrough "non-Bash tool passthrough" "Write" "any"

# Malformed JSON → fail-open.
malformed_payload="{not-json"
mal_out=$(mktemp); mal_err=$(mktemp)
( cd "$CWD_REPO" && echo "$malformed_payload" | "$HOOK" >"$mal_out" 2>"$mal_err" )
mal_rc=$?
mal_out_v=$(cat "$mal_out"); mal_err_v=$(cat "$mal_err")
rm -f "$mal_out" "$mal_err"
if [ "$mal_rc" -eq 0 ] && [ -z "$mal_out_v" ] && [ -z "$mal_err_v" ]; then
  echo "PASS  [malformed JSON fail-open]"; PASS=$((PASS + 1))
else
  echo "FAIL  [malformed JSON fail-open] rc=$mal_rc out='$mal_out_v' err='$mal_err_v'"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("malformed JSON fail-open")
fi

# Non-git cwd → fail-open (returns 0, no ask).
NON_GIT_DIR=$(mktemp -d)
ng_out=$(mktemp); ng_err=$(mktemp)
ng_payload=$(python3 -c '
import json
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": "git worktree remove '$OTHER_OWNED_WT'"},
}))')
( cd "$NON_GIT_DIR" && echo "$ng_payload" | "$HOOK" >"$ng_out" 2>"$ng_err" )
ng_rc=$?
ng_out_v=$(cat "$ng_out"); ng_err_v=$(cat "$ng_err")
rm -f "$ng_out" "$ng_err"; rm -rf "$NON_GIT_DIR"
if [ "$ng_rc" -eq 0 ] && [ -z "$ng_out_v" ] && [ -z "$ng_err_v" ]; then
  echo "PASS  [non-git cwd fail-open]"; PASS=$((PASS + 1))
else
  echo "FAIL  [non-git cwd fail-open] rc=$ng_rc out='$ng_out_v' err='$ng_err_v'"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("non-git cwd fail-open")
fi

# === Summary ===============================================================

echo
echo "----"
echo "Passed: $PASS"
echo "Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
