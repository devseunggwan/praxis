#!/bin/bash
# Coverage for hooks/advisory-nudge/long-foreground-call-advisory/impl.py.
#
# Both directions are pinned deliberately (issue #991): the issue's own
# proposed signal — foreground + NO timeout — must stay SILENT, because a
# foreground call with no timeout returns control at the 120000ms default
# (2.0 min), below the 3-5 min briefing threshold. A one-directional suite
# would let the inverted trigger back in.
#
# Usage: bash tests/hooks/advisory-nudge/test_long_foreground_call_advisory.sh
# Exit: 0 = all pass; 1 = at least one failure

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/long-foreground-call-advisory/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

WORK_DIR=$(mktemp -d) || {
  echo "FATAL: mktemp -d failed" >&2
  exit 1
}
trap 'rm -rf "$WORK_DIR"' EXIT

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expectation tool_input_json
#   expectation:
#     advisory:<marker> — exit 0, stdout empty, stderr contains marker
#     silent            — exit 0, stdout and stderr empty
run_case() {
  local name="$1" expectation="$2" tool_input="$3"
  local payload stdout_file stderr_file stdout_text stderr_text rc marker ok

  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": json.loads(sys.argv[1]),
    "cwd": "/tmp",
}))
' "$tool_input") || {
    echo "FAIL  [$name] could not build payload"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
    return
  }

  stdout_file="$WORK_DIR/stdout"
  stderr_file="$WORK_DIR/stderr"
  printf '%s\n' "$payload" | python3 "$HOOK" >"$stdout_file" 2>"$stderr_file"
  rc=$?
  stdout_text=$(<"$stdout_file")
  stderr_text=$(<"$stderr_file")
  ok=1

  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$stdout_text" ] || ok=0
      [ -z "$stderr_text" ] || ok=0
      ;;
    advisory:*)
      marker="${expectation#advisory:}"
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$stdout_text" ] || ok=0
      case "$stderr_text" in
        *"$marker"*) ;;
        *) ok=0 ;;
      esac
      ;;
    *)
      echo "FAIL  [$name] unknown expectation: $expectation"
      FAIL=$((FAIL + 1))
      FAILED_NAMES+=("$name")
      return
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expectation=$expectation rc=$rc"
    echo "      stdout=${stdout_text:-<empty>}"
    echo "      stderr=${stderr_text:-<empty>}"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# FIRE direction — foreground call with an explicit timeout past the threshold.
# This is the only class of Bash call that can physically outlast the 3-5 min
# briefing trigger.
# ---------------------------------------------------------------------------
run_case "explicit 15-minute timeout" \
  "advisory:timeout=900000ms (15.0 min)" \
  '{"command": "bash scripts/run-tests.sh", "timeout": 900000}'
run_case "issue #991 16.1-minute observation" \
  "advisory:16.1 min" \
  '{"command": "npm test", "timeout": 966000}'
run_case "one millisecond past the threshold" \
  "advisory:long-foreground-call-advisory" \
  '{"command": "sleep 200", "timeout": 180001}'
run_case "advisory names run_in_background escape route" \
  "advisory:run_in_background" \
  '{"command": "docker build .", "timeout": 600000}'
run_case "advisory names the lower-timeout escape route" \
  "advisory:lower \`timeout\` below 180000ms" \
  '{"command": "docker build .", "timeout": 600000}'
run_case "explicit run_in_background false still fires" \
  "advisory:long-foreground-call-advisory" \
  '{"command": "pytest", "timeout": 400000, "run_in_background": false}'
run_case "float timeout past the threshold" \
  "advisory:long-foreground-call-advisory" \
  '{"command": "pytest", "timeout": 300000.0}'
run_case "numeric-string timeout past the threshold" \
  "advisory:long-foreground-call-advisory" \
  '{"command": "pytest", "timeout": "600000"}'
run_case "command text is never inspected — short command, long timeout" \
  "advisory:long-foreground-call-advisory" \
  '{"command": "echo hi", "timeout": 900000}'

# ---------------------------------------------------------------------------
# SILENT direction — everything that cannot breach the threshold, plus the
# escape routes themselves. A false positive here wears the signal out; the
# no-timeout case in particular is the issue's own inverted proposal.
# ---------------------------------------------------------------------------
run_case "no timeout field — 120000ms default returns below the threshold" \
  "silent" \
  '{"command": "bash scripts/run-tests.sh"}'
run_case "no timeout field on a pattern-list command (gh run watch)" \
  "silent" \
  '{"command": "gh run watch"}'
run_case "null timeout" \
  "silent" \
  '{"command": "pytest", "timeout": null}'
run_case "backgrounded with a long timeout" \
  "silent" \
  '{"command": "bash scripts/run-tests.sh", "timeout": 900000, "run_in_background": true}'
run_case "backgrounded with no timeout" \
  "silent" \
  '{"command": "bash scripts/run-tests.sh", "run_in_background": true}'
run_case "backgrounded via string true" \
  "silent" \
  '{"command": "pytest", "timeout": 900000, "run_in_background": "true"}'
run_case "timeout exactly at the threshold" \
  "silent" \
  '{"command": "pytest", "timeout": 180000}'
run_case "timeout below the threshold" \
  "silent" \
  '{"command": "pytest tests/test_one.py::test_x", "timeout": 60000}'
run_case "timeout equal to the 120000ms default" \
  "silent" \
  '{"command": "pytest", "timeout": 120000}'
run_case "non-numeric timeout fails open" \
  "silent" \
  '{"command": "pytest", "timeout": "fifteen minutes"}'
run_case "boolean timeout fails open (True is an int in Python)" \
  "silent" \
  '{"command": "pytest", "timeout": true}'
run_case "object timeout fails open" \
  "silent" \
  '{"command": "pytest", "timeout": {"ms": 900000}}'
run_case "zero timeout fails open" \
  "silent" \
  '{"command": "pytest", "timeout": 0}'
run_case "negative timeout fails open" \
  "silent" \
  '{"command": "pytest", "timeout": -900000}'
run_case "empty command with a long timeout still fires (command is not read)" \
  "advisory:long-foreground-call-advisory" \
  '{"command": "", "timeout": 900000}'

# ---------------------------------------------------------------------------
# Fail-open protocol.
# ---------------------------------------------------------------------------
stdout_file="$WORK_DIR/stdout"
stderr_file="$WORK_DIR/stderr"

check_raw() {
  local name="$1" stdin_text="$2"
  local rc
  printf '%s\n' "$stdin_text" | python3 "$HOOK" >"$stdout_file" 2>"$stderr_file"
  rc=$?
  if [ "$rc" -eq 0 ] && [ ! -s "$stdout_file" ] && [ ! -s "$stderr_file" ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] rc=$rc"
    echo "      stdout=$(<"$stdout_file")"
    echo "      stderr=$(<"$stderr_file")"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

check_raw "non-Bash tool is silent" \
  '{"tool_name":"Read","tool_input":{"timeout":900000}}'
check_raw "malformed JSON fails open" \
  'not json'
check_raw "non-dict payload fails open" \
  '["Bash", 900000]'
check_raw "missing tool_input fails open" \
  '{"tool_name":"Bash"}'
check_raw "non-dict tool_input fails open" \
  '{"tool_name":"Bash","tool_input":"timeout=900000"}'

echo ""
echo "== $PASS passed, $FAIL failed =="
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for name in "${FAILED_NAMES[@]}"; do
    echo "  - $name"
  done
  exit 1
fi
