#!/usr/bin/env bash
# test_wrapper_interpreter_selection.sh — verify the generated wrappers honor
# PRAXIS_PYTHON and keep their fall-through contract when it points nowhere
# (issue #1403).
#
# Usage: bash tests/test_wrapper_interpreter_selection.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DISPATCH="$ROOT_DIR/hooks/_dispatch.sh"

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

echo "test_wrapper_interpreter_selection"

if [ ! -f "$DISPATCH" ]; then
  echo "FAIL  [dispatch_wrapper_exists] expected=yes got=no"
  exit 1
fi

TMP="$(mktemp -d)" || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
trap 'rm -rf "$TMP"' EXIT

# A payload whose group has no members, so the dispatcher's own exit status is
# what the wrapper reports rather than any hook's decision.
PAYLOAD='{"session_id":"t","transcript_path":"/dev/null","cwd":"/","tool_name":"Read","tool_input":{"file_path":"/dev/null"}}'

# 1. Unset PRAXIS_PYTHON keeps today's behaviour: the dispatcher runs and the
#    empty group falls through with 0.
env -u PRAXIS_PYTHON "$DISPATCH" PreToolUse Read claude <<<"$PAYLOAD" >/dev/null 2>&1
run_case "unset_runs_dispatcher" "$?" "0"

# 2. A set PRAXIS_PYTHON is the interpreter that actually execs. The stub
#    records its own invocation, so the marker is the evidence — an exit code
#    alone cannot tell the two interpreters apart.
STUB="$TMP/stub-python"
cat > "$STUB" <<STUB_EOF
#!/bin/sh
echo "stub ran" > "$TMP/marker"
exit 0
STUB_EOF
chmod +x "$STUB"

PRAXIS_PYTHON="$STUB" "$DISPATCH" PreToolUse Read claude <<<"$PAYLOAD" >/dev/null 2>&1
run_case "set_execs_that_interpreter" "$?" "0"
if [ -f "$TMP/marker" ]; then
  run_case "stub_actually_ran" "yes" "yes"
else
  run_case "stub_actually_ran" "no" "yes"
fi

# 3. Positive control for case 2: with the same payload and no override the
#    stub cannot have run, so the marker's presence in case 2 is attributable
#    to the override rather than to a leftover file.
rm -f "$TMP/marker"
env -u PRAXIS_PYTHON "$DISPATCH" PreToolUse Read claude <<<"$PAYLOAD" >/dev/null 2>&1
if [ -f "$TMP/marker" ]; then
  run_case "control_no_marker_without_override" "present" "absent"
else
  run_case "control_no_marker_without_override" "absent" "absent"
fi

# 4. An unresolvable PRAXIS_PYTHON must fall through with 0, never block. exit 2
#    is this repo's deny code, so a wrapper that cannot start an interpreter has
#    to stay silent instead of handing the host a decision nobody made.
PRAXIS_PYTHON="$TMP/definitely-not-here" "$DISPATCH" PreToolUse Read claude <<<"$PAYLOAD" >/dev/null 2>&1
run_case "missing_interpreter_falls_through" "$?" "0"

# 5. The same contract holds for a per-hook wrapper, which is generated from a
#    different template than the dispatcher.
HOOK_WRAPPER="$ROOT_DIR/hooks/memory-hint.sh"
if [ -f "$HOOK_WRAPPER" ]; then
  PRAXIS_PYTHON="$TMP/definitely-not-here" "$HOOK_WRAPPER" <<<"$PAYLOAD" >/dev/null 2>&1
  run_case "hook_wrapper_falls_through" "$?" "0"
else
  echo "FAIL  [hook_wrapper_present] expected=yes got=no"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("hook_wrapper_present")
fi

echo "----"
echo "PASS=$PASS FAIL=$FAIL"
if [ "$FAIL" -ne 0 ]; then
  echo "failed: ${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
