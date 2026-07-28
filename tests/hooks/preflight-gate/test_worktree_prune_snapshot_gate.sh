#!/usr/bin/env bash
# test_worktree_prune_snapshot_gate.sh — coverage for
# hooks/preflight-gate/worktree-prune-snapshot-gate/impl.py
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   block → exit 2 + stderr mentions the rule name
#   pass  → exit 0
#
# Each case gets a fresh session_id (and a fresh $TMPDIR-scoped state file)
# so cases do not pollute each other's snapshot-taken flag.
#
# Usage: bash tests/hooks/preflight-gate/test_worktree_prune_snapshot_gate.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/worktree-prune-snapshot-gate/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

STATE_DIR=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
trap 'rm -rf "$STATE_DIR"' EXIT
_case_n=0

# build_payload <command> <session_id>
build_payload() {
  python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
    "session_id": sys.argv[2],
}))
' "$1" "$2"
}

# run_hook <session_id> <command>
run_hook() {
  local session_id="$1" command="$2" state_file
  state_file="$STATE_DIR/state-$session_id.json"
  build_payload "$command" "$session_id" \
    | PRAXIS_WORKTREE_PRUNE_SNAPSHOT_FILE="$state_file" \
      env -u PRAXIS_HOOK_BYPASS_WORKTREE_PRUNE_SNAPSHOT \
      python3 "$HOOK"
}

# run_case <name> <expect: block|pass> <session_id> <command...>
# `<command...>` may be given multiple times to simulate sequential Bash
# calls within the same session before the final assertion.
run_case() {
  local name="$1" expect="$2" session_id="$3"
  shift 3
  local out rc cmd
  local -a cmds=("$@")
  local last_idx=$((${#cmds[@]} - 1))
  for i in "${!cmds[@]}"; do
    cmd="${cmds[$i]}"
    out=$(run_hook "$session_id" "$cmd" 2>&1 1>/dev/null)
    rc=$?
    if [ "$i" -eq "$last_idx" ]; then
      local ok=1
      case "$expect" in
        block)
          [ "$rc" -eq 2 ] && [[ "$out" == *"WORKTREE-PRUNE-SNAPSHOT-GATE"* ]] || ok=0
          ;;
        pass)
          [ "$rc" -eq 0 ] || ok=0
          ;;
      esac
      if [ "$ok" -eq 1 ]; then
        echo "PASS  [$name]"
        PASS=$((PASS + 1))
      else
        echo "FAIL  [$name] (rc=$rc, stderr=$out)"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
      fi
    fi
  done
}

_case_n=$((_case_n + 1))
run_case "$_case_n block: bare prune, no prior snapshot" \
  block "sess-$_case_n" "git worktree prune"

_case_n=$((_case_n + 1))
run_case "$_case_n pass: dry-run prune, no snapshot" \
  pass "sess-$_case_n" "git worktree prune -n"

_case_n=$((_case_n + 1))
run_case "$_case_n pass: --dry-run long form, no snapshot" \
  pass "sess-$_case_n" "git worktree prune --dry-run"

_case_n=$((_case_n + 1))
run_case "$_case_n pass: snapshot taken in an earlier call, then bare prune" \
  pass "sess-$_case_n" \
  "git worktree list --porcelain > /tmp/snap.txt" \
  "git worktree prune"

_case_n=$((_case_n + 1))
run_case "$_case_n pass: snapshot + prune in one compound command" \
  pass "sess-$_case_n" \
  "git worktree list --porcelain > /tmp/snap.txt && git worktree prune"

_case_n=$((_case_n + 1))
run_case "$_case_n pass: git worktree list without --porcelain still counts as snapshot" \
  pass "sess-$_case_n" \
  "git worktree list" \
  "git worktree prune"

_case_n=$((_case_n + 1))
run_case "$_case_n block: git -C <dir> worktree prune, no snapshot" \
  block "sess-$_case_n" "git -C /some/worktree worktree prune"

_case_n=$((_case_n + 1))
run_case "$_case_n pass: git -C <dir> worktree list counts, then -C prune passes" \
  pass "sess-$_case_n" \
  "git -C /some/worktree worktree list --porcelain" \
  "git -C /some/worktree worktree prune"

_case_n=$((_case_n + 1))
run_case "$_case_n pass: unrelated git command" \
  pass "sess-$_case_n" "git status"

_case_n=$((_case_n + 1))
run_case "$_case_n pass: git worktree add is not a prune" \
  pass "sess-$_case_n" "git worktree add -b foo ../foo origin/main"

_case_n=$((_case_n + 1))
run_case "$_case_n pass: git worktree remove is not a prune" \
  pass "sess-$_case_n" "git worktree remove ../foo"

_case_n=$((_case_n + 1))
run_case "$_case_n pass: unrelated Bash command" \
  pass "sess-$_case_n" "echo hello"

# non-Bash tool -> pass
_out=$(printf '%s' '{"tool_name":"Read","tool_input":{}}' | python3 "$HOOK" 2>&1 1>/dev/null)
_rc=$?
if [ "$_rc" -eq 0 ]; then
  echo "PASS  [non-Bash tool passthrough]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [non-Bash tool passthrough] (rc=$_rc, stderr=$_out)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("non-Bash tool passthrough")
fi

# malformed JSON -> fail-open
_out=$(printf 'not json at all <<<' | python3 "$HOOK" 2>&1 1>/dev/null)
_rc=$?
if [ "$_rc" -eq 0 ]; then
  echo "PASS  [malformed JSON stdin -> fail-open]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [malformed JSON stdin -> fail-open] (rc=$_rc, stderr=$_out)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("malformed JSON stdin")
fi

# bypass env var -> pass even without snapshot
_session="sess-bypass"
_state_file="$STATE_DIR/state-$_session.json"
_payload=$(build_payload "git worktree prune" "$_session")
_out=$(echo "$_payload" | PRAXIS_WORKTREE_PRUNE_SNAPSHOT_FILE="$_state_file" \
  PRAXIS_HOOK_BYPASS_WORKTREE_PRUNE_SNAPSHOT=1 python3 "$HOOK" 2>&1 1>/dev/null)
_rc=$?
if [ "$_rc" -eq 0 ]; then
  echo "PASS  [bypass env var set]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [bypass env var set] (rc=$_rc, stderr=$_out)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("bypass env var")
fi

# --- summary -----------------------------------------------------------------
echo ""
echo "Passed: $PASS  Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed tests:"
  for t in "${FAILED_NAMES[@]}"; do
    echo "  - $t"
  done
  exit 1
fi
exit 0
