#!/bin/bash
# test_branch_name_check.sh — coverage for hooks/preflight-gate/branch-name-check/impl.py
#
# Synthesizes Claude Code PreToolUse hook payloads and asserts:
#   block  → stdout contains "permissionDecision" / "deny", rc=2
#   silent → stdout empty, stderr empty, rc=0
#   warn   → stderr non-empty, stdout empty, rc=0
#
# Usage: bash tests/hooks/preflight-gate/test_branch_name_check.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/branch-name-check/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expectation payload_json [env_vars...]
#   expectation:
#     "block"  — stdout contains permissionDecision deny, rc=2
#     "silent" — stdout empty, stderr empty, rc=0
#     "warn"   — stderr non-empty, stdout empty, rc=0
run_case() {
  local name="$1" expectation="$2" payload="$3"
  shift 3
  local env_args=()
  for kv in "$@"; do
    env_args+=("$kv")
  done

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)

  local base_env=()
  base_env+=("PRAXIS_BRANCH_NAME_REGEX=")
  base_env+=("PRAXIS_BRANCH_NAME_STRICT=")
  base_env+=("PRAXIS_BRANCH_NAME_WHITELIST=")

  if [ "${#env_args[@]}" -gt 0 ]; then
    echo "$payload" | env -u PRAXIS_BRANCH_NAME_REGEX \
                          -u PRAXIS_BRANCH_NAME_STRICT \
                          -u PRAXIS_BRANCH_NAME_WHITELIST \
                          "${env_args[@]}" python3 "$HOOK" >"$out_file" 2>"$err_file"
  else
    echo "$payload" | env -u PRAXIS_BRANCH_NAME_REGEX \
                          -u PRAXIS_BRANCH_NAME_STRICT \
                          -u PRAXIS_BRANCH_NAME_WHITELIST \
                          python3 "$HOOK" >"$out_file" 2>"$err_file"
  fi
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    block)
      [ "$rc" -eq 2 ] || ok=0
      echo "$out" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['hookSpecificOutput']['permissionDecision']=='deny'" 2>/dev/null || ok=0
      ;;
    warn)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      [ -n "$err" ]   || ok=0
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
    [ -n "$out" ] && echo "        stdout: $(echo "$out" | head -c 400)"
    [ -n "$err" ] && echo "        stderr: $(echo "$err" | head -c 400)"
  fi
}

echo "test_branch_name_check"

# ---------------------------------------------------------------------------
# PASS cases — valid branch names
# ---------------------------------------------------------------------------

run_case "checkout -b good hub branch (pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout -b hub-434-feat-branch-name-check"}}'

run_case "checkout -b good issue branch (pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout -b issue-12-fix-null-ptr"}}'

run_case "switch -c good hub branch (pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git switch -c hub-1-chore-cleanup"}}'

run_case "worktree add path-first good branch (pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git worktree add ../path -b hub-434-feat-branch-name-check"}}'

run_case "worktree add flag-first good branch (pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git worktree add -b hub-434-feat-branch-name-check ../path"}}'

# ---------------------------------------------------------------------------
# PASS — whitelisted branch names
# ---------------------------------------------------------------------------

run_case "checkout -b main (whitelisted, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout -b main"}}'

run_case "checkout -b dev (whitelisted, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout -b dev"}}'

run_case "checkout -b master (whitelisted, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout -b master"}}'

run_case "checkout -b prod (whitelisted, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout -b prod"}}'

run_case "checkout -b staging (whitelisted, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout -b staging"}}'

# ---------------------------------------------------------------------------
# PASS — bare checkout/switch (NOT a creation)
# ---------------------------------------------------------------------------

run_case "bare git checkout main (no -b, not creation, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout main"}}'

run_case "bare git switch dev (no -c, not creation, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git switch dev"}}'

run_case "git checkout with other flags but no -b (pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout --detach HEAD"}}'

# ---------------------------------------------------------------------------
# PASS — non-branch-creating git commands
# ---------------------------------------------------------------------------

run_case "git status (ignored, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git status"}}'

run_case "git push origin main (ignored, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git push origin main"}}'

run_case "git worktree add (no -b flag, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git worktree add ../path existing-branch"}}'

# ---------------------------------------------------------------------------
# PASS — non-Bash tool
# ---------------------------------------------------------------------------

run_case "non-Bash tool (pass)" \
  "silent" \
  '{"tool_name":"Edit","tool_input":{"command":"git checkout -b bad-branch"}}'

# ---------------------------------------------------------------------------
# PASS — malformed JSON fail-open
# ---------------------------------------------------------------------------

run_case "malformed JSON (fail-open, pass)" \
  "silent" \
  'not valid json {'

# ---------------------------------------------------------------------------
# BLOCK cases — invalid branch names
# ---------------------------------------------------------------------------

run_case "checkout -b wrong format (no issue number, block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout -b feature/my-cool-change"}}'

run_case "checkout -b missing prefix (block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout -b 434-feat-branch-check"}}'

run_case "checkout -b missing issue number (block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout -b hub-feat-something"}}'

run_case "checkout -b wrong type token (block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout -b issue-1-foo-something"}}'

run_case "switch -c bad branch (block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git switch -c bad-branch-name"}}'

run_case "worktree add path-first bad branch (block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git worktree add ../path -b bad-branch-name"}}'

run_case "worktree add flag-first bad branch (block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git worktree add -b bad-branch-name ../path"}}'

run_case "checkout -b UpperCase (block, regex requires lowercase)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout -b Hub-434-Feat-branch"}}'

# ---------------------------------------------------------------------------
# Advisory mode — STRICT=0
# ---------------------------------------------------------------------------

run_case "advisory mode: bad branch → warn, no block" \
  "warn" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout -b feature/bad-name"}}' \
  "PRAXIS_BRANCH_NAME_STRICT=0"

run_case "advisory mode: good branch → silent (not warned)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout -b hub-1-feat-good"}}' \
  "PRAXIS_BRANCH_NAME_STRICT=0"

# ---------------------------------------------------------------------------
# Custom regex env
# ---------------------------------------------------------------------------

run_case "custom regex: matching name (pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout -b custom-branch-ok"}}' \
  "PRAXIS_BRANCH_NAME_REGEX=^custom-.*$"

run_case "custom regex: non-matching name (block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout -b other-branch"}}' \
  "PRAXIS_BRANCH_NAME_REGEX=^custom-.*$"

# ---------------------------------------------------------------------------
# Custom whitelist env
# ---------------------------------------------------------------------------

run_case "custom whitelist: custom-allowed passes even without regex match" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout -b my-special-branch"}}' \
  "PRAXIS_BRANCH_NAME_WHITELIST=my-special-branch,another"

run_case "custom whitelist: name not in custom list and not matching regex (block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout -b feature/not-whitelisted"}}' \
  "PRAXIS_BRANCH_NAME_WHITELIST=my-special-branch"

# ---------------------------------------------------------------------------
# Worktree argument order regression
# ---------------------------------------------------------------------------

run_case "worktree reversed: -b <good> <path> (pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git worktree add -b issue-99-docs-update /tmp/wt"}}'

run_case "worktree reversed: -b <bad> <path> (block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git worktree add -b random-name /tmp/wt"}}'

run_case "worktree standard: <path> -b <good> (pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git worktree add /tmp/wt -b hub-10-test-suite"}}'

run_case "worktree standard: <path> -b <bad> (block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git worktree add /tmp/wt -b feature-x"}}'

# ---------------------------------------------------------------------------
# Compound command (chained with &&)
# ---------------------------------------------------------------------------

run_case "chained: mkdir && git checkout -b good (pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"mkdir -p /tmp/foo && git checkout -b hub-5-feat-foo"}}'

run_case "chained: mkdir && git checkout -b bad (block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"mkdir -p /tmp/foo && git checkout -b bad-name"}}'

# ---------------------------------------------------------------------------
# echo false-positive guard
# ---------------------------------------------------------------------------

run_case "echo with branch creation string (no false positive, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"echo \"git checkout -b bad-branch\""}}'

# ---------------------------------------------------------------------------
# git switch --create / --orphan long forms (P2 bypass paths)
# ---------------------------------------------------------------------------

run_case "switch --create bad branch (block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git switch --create bad-branch-name"}}'

run_case "switch --create good branch (pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git switch --create hub-434-feat-test"}}'

run_case "switch --orphan bad branch (block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git switch --orphan bad-branch"}}'

run_case "switch --orphan good branch (pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git switch --orphan hub-5-feat-orphan"}}'

# ---------------------------------------------------------------------------
# git branch creation (P2 bypass path)
# ---------------------------------------------------------------------------

run_case "git branch bad-branch-name (block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch bad-branch-name"}}'

run_case "git branch hub-434-feat-test (pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch hub-434-feat-test"}}'

run_case "git branch hub-434-feat-test main (with start point, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch hub-434-feat-test main"}}'

run_case "git branch bad-name main (with start point, block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch bad-name main"}}'

run_case "git branch -d some-branch (delete, not creation, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch -d some-branch"}}'

run_case "git branch -D bad-branch (force-delete, not creation, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch -D bad-branch"}}'

run_case "git branch -m old new (rename, not creation, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch -m old-name new-bad-name"}}'

run_case "git branch --list (listing, not creation, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch --list"}}'

run_case "git branch -r (remote listing, not creation, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch -r"}}'

run_case "git branch -a (all listing, not creation, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch -a"}}'

run_case "git branch issue-1-style-format (style type, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch issue-1-style-format"}}'

# ---------------------------------------------------------------------------
# Shell redirect false-positive guard (issue #806)
# `git branch <redirect>` is a QUERY — the redirect token must not be read as
# the new branch name. Enumerate every redirect form; all must pass.
# ---------------------------------------------------------------------------

run_case "git branch 2>&1 (fd merge, query, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch 2>&1"}}'

run_case "git branch 1>&2 (fd merge reversed, query, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch 1>&2"}}'

run_case "git branch >file (attached output redirect, query, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch >file"}}'

run_case "git branch > /tmp/out (spaced output redirect, query, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch > /tmp/out"}}'

run_case "git branch >> file (append redirect, query, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch >> file"}}'

run_case "git branch 2>/dev/null (fd output attached, query, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch 2>/dev/null"}}'

run_case "git branch &>file (fd-all merge, query, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch &>file"}}'

run_case "git branch <file (attached input redirect, query, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch <file"}}'

run_case "git branch < input.txt (spaced input redirect, query, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch < input.txt"}}'

run_case "git branch 3>&2 (arbitrary fd, query, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch 3>&2"}}'

run_case "git branch | head (pipe, downstream not read as name, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch | head"}}'

# Regression: real creation with a trailing redirect must STILL be blocked —
# stripping redirects must not blind the creation check.
run_case "git branch bad-name > /tmp/log (creation + redirect, block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch bad-name > /tmp/log"}}'

run_case "git checkout -b bad-branch 2>&1 (creation + redirect, block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout -b bad-branch 2>&1"}}'

run_case "git branch hub-5-feat-ok 2>&1 (good creation + redirect, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch hub-5-feat-ok 2>&1"}}'

# ---------------------------------------------------------------------------
# git worktree add implicit basename branch creation (P2 bypass paths)
# ---------------------------------------------------------------------------

run_case "worktree add <bad-path> (implicit bad basename, block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git worktree add ../bad-name"}}'

run_case "worktree add <good-path> (implicit good basename, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git worktree add ../hub-99-feat-implicit"}}'

run_case "worktree add --orphan <bad-path> (implicit bad basename, block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git worktree add --orphan ../bad-orphan"}}'

run_case "worktree add --orphan <good-path> (implicit good basename, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git worktree add --orphan ../hub-7-feat-orphan"}}'

run_case "worktree add <path> <commit-ish> (two positionals, skip check, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git worktree add ../path existing-branch"}}'

run_case "worktree add -d <path> (detached HEAD, no branch, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git worktree add -d ../path"}}'

run_case "worktree add --lock <bad-path> (--lock is boolean, basename still checked, block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git worktree add --lock ../bad-name"}}'

run_case "worktree add --lock <good-path> (--lock is boolean, basename still checked, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git worktree add --lock ../hub-10-feat-locked"}}'

run_case "git branch --set-upstream-to=origin/main legacy (non-create equals form, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch --set-upstream-to=origin/main legacy-branch"}}'

# ---------------------------------------------------------------------------
# git checkout/switch --track implicit branch creation (P2 bypass paths)
# ---------------------------------------------------------------------------

run_case "checkout --track origin/bad-name (implicit bad branch, block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout --track origin/bad-name"}}'

run_case "checkout --track origin/hub-434-feat-test (implicit good branch, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout --track origin/hub-434-feat-test"}}'

run_case "checkout --track origin/main (whitelisted remote branch, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout --track origin/main"}}'

run_case "checkout --track no-slash (no explicit remote, skip, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout --track remote-only"}}'

run_case "switch --track origin/bad-name (implicit bad branch, block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git switch --track origin/bad-name"}}'

run_case "switch --track origin/hub-10-feat-ok (implicit good branch, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git switch --track origin/hub-10-feat-ok"}}'

run_case "checkout -b explicit --track (explicit -b wins over track, block bad)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout -b bad-name --track origin/hub-434-feat-test"}}'

run_case "switch --track=direct origin/bad-name (equals mode, implicit bad branch, block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git switch --track=direct origin/bad-name"}}'

run_case "checkout --track=inherit origin/bad-name (equals mode, implicit bad branch, block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout --track=inherit origin/bad-name"}}'

run_case "checkout --track=direct origin/hub-434-feat-test (equals mode, good branch, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout --track=direct origin/hub-434-feat-test"}}'

run_case "git branch -l pattern (list with pattern, not creation, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch -l '"'"'hub-*'"'"'"}}'

run_case "git branch --format query (not creation, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch --format '"'"'%(refname:short)'"'"'"}}'

run_case "git branch --sort committerdate (not creation, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch --sort committerdate"}}'

run_case "git branch --format=embedded (not creation, pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git branch --format=%(refname:short)"}}'

# ---------------------------------------------------------------------------
# git switch --force-create (long form of -C, P2 bypass path)
# ---------------------------------------------------------------------------

run_case "switch --force-create bad branch (block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git switch --force-create bad-branch"}}'

run_case "switch --force-create good branch (pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git switch --force-create hub-5-feat-ok"}}'

# ---------------------------------------------------------------------------
# git checkout --orphan (P2 bypass path)
# ---------------------------------------------------------------------------

run_case "checkout --orphan bad branch (block)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout --orphan bad-branch"}}'

run_case "checkout --orphan good branch (pass)" \
  "silent" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout --orphan hub-7-feat-orphan"}}'

# ---------------------------------------------------------------------------
# bypass message format (P3: should not show '=1 =0')
# ---------------------------------------------------------------------------

run_case "bypass message has no '=1 =0' artifact (P3 regression)" \
  "block" \
  '{"tool_name":"Bash","tool_input":{"command":"git checkout -b bad-name"}}'

# Verify the denial reason does NOT contain the '=1 =0' artifact.
raw_out=$(echo '{"tool_name":"Bash","tool_input":{"command":"git checkout -b bad-name"}}' | \
  env -u PRAXIS_BRANCH_NAME_REGEX -u PRAXIS_BRANCH_NAME_STRICT -u PRAXIS_BRANCH_NAME_WHITELIST \
  python3 "$HOOK" 2>/dev/null)
if echo "$raw_out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
reason = d['hookSpecificOutput']['permissionDecisionReason']
assert '=1 =0' not in reason, f'bypass message artifact found: {reason}'
" 2>/dev/null; then
  PASS=$((PASS + 1))
  echo "  PASS  bypass message: no '=1 =0' artifact"
else
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("bypass message: no '=1 =0' artifact")
  echo "  FAIL  bypass message: no '=1 =0' artifact"
fi
# Fail-open guard opt-in (issue #498): main() must be @fail_open-wrapped;
# guard behavior is tested centrally in tests/test_hook_runtime.sh.
_failopen_out=$(python3 - << PYEOF 2>&1
import importlib.util
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
assert getattr(mod.main, "__wrapped__", None) is not None, "main is not @fail_open-wrapped"
print("OK")
PYEOF
)
_failopen_rc=$?
if [ "$_failopen_rc" -eq 0 ] && [ "$_failopen_out" = "OK" ]; then
  echo "PASS  [fail-open] main() is wrapped by the shared @fail_open guard"
  PASS=$((PASS+1))
else
  echo "FAIL  [fail-open] main() not @fail_open-wrapped (rc=$_failopen_rc out=$_failopen_out)"
  FAIL=$((FAIL+1)); FAILED_NAMES+=("fail-open guard wrapping")
fi



# ---------------------------------------------------------------------------
echo ""
echo "Result: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed:"
  for name in "${FAILED_NAMES[@]}"; do
    echo "  - $name"
  done
  exit 1
fi
exit 0
