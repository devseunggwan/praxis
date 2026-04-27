#!/bin/bash
# tests/test_builtin_task_postuse.sh — PostToolUse hook coverage for built-in task tools
#
# Claude Code ships two distinct sets of "Task*" tools:
#   - Task           → spawns a subagent (real agent operation)
#   - TaskCreate     → creates a task list entry (NO subagent)
#   - TaskUpdate     → updates a task list entry (NO subagent)
#   - TaskGet        → reads a task list entry   (NO subagent)
#   - TaskList       → lists task list entries   (NO subagent)
#   - TaskStop       → cancels a task list entry (NO subagent)
#   - TaskOutput     → reads task output         (NO subagent)
#
# This test suite verifies that the PostToolUse hook:
#   - emits correction context for built-in task management tools
#   - does NOT emit agent-spawn language ("Spawning agent", "delegation") for them
#   - passes through silently for tools that are not in the built-in task set
#
# Run:  ./tests/test_builtin_task_postuse.sh
# Exit: 0 on success, 1 on at least one failure

set +e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="$REPO_ROOT/hooks/builtin-task-postuse.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "SKIP: python3 not available" >&2
  exit 0
fi

PASS=0
FAIL=0
FAILED_NAMES=()

make_payload() {
  local tool_name="$1"
  python3 -c '
import json, sys
print(json.dumps({
    "tool_name": sys.argv[1],
    "tool_input": {},
    "tool_response": "success",
}))' "$tool_name"
}

# run_case name expected tool_name
#   expected:
#     correct   → hook emits JSON with "continue":true and additionalContext
#                 with NO agent-spawn language
#     pass      → hook emits nothing (exit 0, empty stdout)
run_case() {
  local name="$1" expected="$2" tool_name="$3"

  local out
  out=$(make_payload "$tool_name" | python3 "$HOOK" 2>/dev/null)
  local rc=$?

  if [ "$rc" -ne 0 ]; then
    echo "FAIL  [$name] hook exited $rc (expected 0)"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
  fi

  case "$expected" in
    correct)
      # must have continue:true
      if ! echo "$out" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('continue') is True" 2>/dev/null; then
        echo "FAIL  [$name] expected continue:true, got: ${out:-<empty>}"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      fi
      # must have additionalContext
      if ! echo "$out" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('hookSpecificOutput',{}).get('additionalContext')" 2>/dev/null; then
        echo "FAIL  [$name] expected additionalContext, got: ${out:-<empty>}"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      fi
      # must NOT contain agent-spawn language
      # "no subagent was spawned" in the correction message is intentional — allow it
      if echo "$out" | grep -qi "Spawning agent\|Multiple tasks delegated\|Background task launched\|Task delegation failed"; then
        echo "FAIL  [$name] agent-spawn language in output: $out"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      fi
      ;;
    pass)
      if [ -n "$out" ]; then
        echo "FAIL  [$name] expected empty output, got: $out"
        FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      fi
      ;;
    *)
      echo "FAIL  [$name] unknown expected: $expected"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      ;;
  esac

  PASS=$((PASS + 1))
  printf '  ✓ %s\n' "$name"
}

echo "=== builtin-task-postuse: built-in task management tools ==="

# AC1: built-in task management tools get corrective context
run_case "TaskUpdate gets correction"  correct TaskUpdate
run_case "TaskCreate gets correction"  correct TaskCreate
run_case "TaskGet gets correction"     correct TaskGet
run_case "TaskList gets correction"    correct TaskList
run_case "TaskStop gets correction"    correct TaskStop
run_case "TaskOutput gets correction"  correct TaskOutput

echo ""
echo "=== builtin-task-postuse: non-task tools pass through silently ==="

# AC2: tools NOT in the built-in task management set → silent pass-through
run_case "Task (agent spawn) passes"   pass Task
run_case "Bash passes"                 pass Bash
run_case "Edit passes"                 pass Edit
run_case "Write passes"                pass Write
run_case "Read passes"                 pass Read
run_case "Agent passes"                pass Agent
run_case "Skill passes"                pass Skill

echo ""
echo "=== builtin-task-postuse: edge cases ==="

# AC3: malformed stdin → safe fail-open (exit 0, no output)
malformed_case() {
  local name="$1" input="$2"
  local out
  out=$(echo "$input" | python3 "$HOOK" 2>/dev/null)
  local rc=$?
  if [ "$rc" -ne 0 ] || [ -n "$out" ]; then
    echo "FAIL  [$name] expected silent exit 0, got rc=$rc out=${out:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
  fi
  PASS=$((PASS + 1))
  printf '  ✓ %s\n' "$name"
}

malformed_case "empty stdin"    ""
malformed_case "invalid JSON"   "not-json"
malformed_case "missing tool"   '{"tool_input":{}}'

echo ""
echo "=== builtin-task-postuse: correction content spec ==="

# AC4: correction note explicitly says no subagent was spawned
content_case() {
  local name="$1" tool_name="$2" pattern="$3"
  local out
  out=$(make_payload "$tool_name" | python3 "$HOOK" 2>/dev/null)
  if echo "$out" | grep -qi "$pattern"; then
    PASS=$((PASS + 1))
    printf '  ✓ %s\n' "$name"
  else
    echo "FAIL  [$name] pattern '$pattern' not found in: ${out:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

content_case "TaskUpdate note says no subagent" TaskUpdate "no subagent was spawned"
content_case "TaskCreate note says false positives" TaskCreate "false positives"

echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
  echo "Failed tests:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
