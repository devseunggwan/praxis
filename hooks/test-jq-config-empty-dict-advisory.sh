#!/bin/bash
# test-jq-config-empty-dict-advisory.sh — coverage for the advisory hook.
#
# Synthesizes Claude Code PreToolUse(Bash) payloads against real temporary
# files and asserts:
#   advisory:<marker>  → exit 0 + stderr contains the marker string
#   silent             → exit 0 + stderr empty
#
# Usage: bash hooks/test-jq-config-empty-dict-advisory.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="$SCRIPT_DIR/jq-config-empty-dict-advisory.sh"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expected command [session_id]
#   expected: "advisory:<marker>"  (exit 0, stderr contains <marker>)
#             "silent"             (exit 0, stderr empty)
run_case() {
  local name="$1" expected="$2" command="$3" session_id="${4:-test-session-$$}"

  local payload
  payload=$(python3 -c '
import json, sys
d = {
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
    "session_id": sys.argv[2],
}
print(json.dumps(d))' "$command" "$session_id")

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)
  echo "$payload" | "$HOOK" >"$out_file" 2>"$err_file"
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
      [ -z "$out" ]   || ok=0
      echo "$err" | grep -qF "$marker" || ok=0
      ;;
    silent)
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
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected rc=$rc"
    [ -n "$out" ] && echo "        stdout: $out"
    [ -n "$err" ] && echo "        stderr: $err"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# Shared tmp dir — unique per test run so dedup state doesn't bleed between
# cases (each case uses a distinct session_id derived from the tmp dir name).
# ---------------------------------------------------------------------------
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

# Unique session IDs per case to avoid dedup state interference.
SID_EMPTY="test-empty-$$"
SID_INVALID="test-invalid-$$"
SID_VALID="test-valid-$$"

# --- Case 1: empty file -------------------------------------------------------

EMPTY_JSON="$TMP_DIR/.claude/settings.json"
mkdir -p "$(dirname "$EMPTY_JSON")"
# Create a zero-byte file
: > "$EMPTY_JSON"

run_case "empty config file" \
  "advisory:[config-empty]" \
  "jq '.' $EMPTY_JSON" \
  "$SID_EMPTY"

# --- Case 2: invalid JSON file ------------------------------------------------

INVALID_JSON="$TMP_DIR/.claude/hooks.json"
mkdir -p "$(dirname "$INVALID_JSON")"
printf '{not valid json' > "$INVALID_JSON"

run_case "invalid JSON config file" \
  "advisory:[config-invalid]" \
  "jq '.hooks' $INVALID_JSON" \
  "$SID_INVALID"

# --- Case 3: valid JSON file --------------------------------------------------

VALID_JSON="$TMP_DIR/.claude/valid.json"
mkdir -p "$(dirname "$VALID_JSON")"
printf '{"key": "value"}' > "$VALID_JSON"

run_case "valid JSON config file" \
  "silent" \
  "jq '.key' $VALID_JSON" \
  "$SID_VALID"

# ---------------------------------------------------------------------------
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
