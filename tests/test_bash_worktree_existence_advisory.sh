#!/bin/bash
# test_bash_worktree_existence_advisory.sh — coverage for
# hooks/bash-worktree-existence-advisory.sh
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   advisory:<marker>  — exit 0, stderr contains <marker>
#   silent             — exit 0, stderr empty
#
# Usage: bash tests/test_bash_worktree_existence_advisory.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$ROOT_DIR/hooks/bash-worktree-existence-advisory.sh"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expectation command [session_id]
#   expectation:
#     "advisory:<marker>" — exit 0, stderr contains <marker>
#     "silent"            — exit 0, stderr empty
run_case() {
  local name="$1" expectation="$2" command="$3" sid="${4:-test-session-$$}"

  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
    "session_id": sys.argv[2],
}))' "$command" "$sid")

  local err_file
  err_file=$(mktemp)
  echo "$payload" | "$HOOK" >/dev/null 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    advisory:*)
      local marker="${expectation#advisory:}"
      [ "$rc" -eq 0 ] || ok=0
      case "$err" in
        *"$marker"*) ;;
        *) ok=0 ;;
      esac
      ;;
    *)
      echo "FAIL  [$name] unknown expectation: $expectation"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expectation=$expectation rc=$rc stderr=${err:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# Setup: scratch directories for testing
# ---------------------------------------------------------------------------

# Existing directory that is NOT a registered git worktree.
NON_WT_DIR=$(mktemp -d)

# Path that does not exist at all.
MISSING_PATH="$NON_WT_DIR/does-not-exist-$$"

# A real registered worktree: the root of the praxis repo itself.
# `git worktree list` always includes the main worktree.
REAL_WT_PATH="$(git -C "$ROOT_DIR" rev-parse --show-toplevel 2>/dev/null)"

# ---------------------------------------------------------------------------
# Case 1: missing path → [worktree-missing] advisory
# ---------------------------------------------------------------------------

run_case "missing path emits worktree-missing advisory" \
  "advisory:[worktree-missing]" \
  "cd $MISSING_PATH && ls"

# ---------------------------------------------------------------------------
# Case 2: existing path that is NOT a registered worktree → [worktree-stale]
# ---------------------------------------------------------------------------

run_case "existing non-worktree path emits worktree-stale advisory" \
  "advisory:[worktree-stale]" \
  "cd $NON_WT_DIR && ls"

# ---------------------------------------------------------------------------
# Case 3: registered worktree → silent (no advisory)
# ---------------------------------------------------------------------------

if [ -n "$REAL_WT_PATH" ] && [ -d "$REAL_WT_PATH" ]; then
  run_case "registered worktree path is silent" \
    "silent" \
    "cd $REAL_WT_PATH && git status"
else
  echo "SKIP  [registered worktree path is silent] — could not determine real worktree path"
fi

# ---------------------------------------------------------------------------
# Infrastructure / fail-open cases
# ---------------------------------------------------------------------------

run_case "non-Bash tool name is silent" \
  "silent" \
  "cd $MISSING_PATH"

# Override tool_name to Read
read_payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Read",
    "tool_input": {"command": "cd /nonexistent/path && ls"},
    "session_id": "test-infra-$$",
}))' 2>/dev/null)
err_file_infra=$(mktemp)
echo "$read_payload" | "$HOOK" >/dev/null 2>"$err_file_infra"
rc_infra=$?
err_infra=$(cat "$err_file_infra")
rm -f "$err_file_infra"
if [ "$rc_infra" -eq 0 ] && [ -z "$err_infra" ]; then
  echo "PASS  [non-Bash tool_name passthrough]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [non-Bash tool_name passthrough] rc=$rc_infra stderr=${err_infra:-<empty>}"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("non-Bash tool_name passthrough")
fi

run_case "malformed command bare cd is silent" \
  "silent" \
  "cd"

run_case "cd with variable expansion is silent" \
  "silent" \
  'cd $SOME_VAR && ls'

run_case "opt-out marker suppresses advisory" \
  "silent" \
  "cd $MISSING_PATH  # worktree-advisory:ack"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

rm -rf "$NON_WT_DIR" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
fi

[ "$FAIL" -eq 0 ]
