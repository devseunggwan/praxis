#!/usr/bin/env bash
# test_external_api_literal_trigger.sh — coverage for external-api-literal-trigger hook
#
# Synthesizes Claude Code PreToolUse payloads and asserts:
#   advisory:<needle> → exit 0 + stderr contains <needle>
#   pass              → exit 0 + stderr empty
#
# Usage: bash tests/test_external_api_literal_trigger.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/external-api-literal-trigger/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expectation payload
#   expectation:
#     "advisory:<needle>" — exit 0 + stderr contains <needle>
#     "pass"              — exit 0 + stderr empty
run_case() {
  local name="$1" expectation="$2" payload="$3"

  local err_file
  err_file=$(mktemp)
  printf '%s' "$payload" | python3 "$HOOK" >/dev/null 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"

  local ok=1
  case "$expectation" in
    advisory:*)
      local needle="${expectation#advisory:}"
      [ "$rc" -eq 0 ] || ok=0
      case "$err" in
        *"$needle"*) ;;
        *) ok=0 ;;
      esac
      ;;
    pass)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
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
    echo "FAIL  [$name] expectation=$expectation rc=$rc stderr=${err:0:200}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------

bash_payload() {
  python3 -c "import json,sys; print(json.dumps({'tool_name':'Bash','tool_input':{'command':sys.argv[1]}}))" "$1"
}

write_payload() {
  python3 -c "import json,sys; print(json.dumps({'tool_name':'Write','tool_input':{'file_path':'x.py','content':sys.argv[1]}}))" "$1"
}

edit_payload() {
  python3 -c "import json,sys; print(json.dumps({'tool_name':'Edit','tool_input':{'file_path':'x.py','old_string':'x','new_string':sys.argv[1]}}))" "$1"
}

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

echo "test_external_api_literal_trigger"

# --- Pass cases ---

run_case "clean_bash_git_status" "pass" \
  "$(bash_payload 'git status')"

run_case "clean_bash_echo_plain" "pass" \
  "$(bash_payload 'echo hello world')"

run_case "stop_word_README" "pass" \
  "$(bash_payload 'cat README.md')"

run_case "stop_word_LICENSE" "pass" \
  "$(write_payload 'MIT LICENSE')"

run_case "unknown_tool" "pass" \
  '{"tool_name":"Read","tool_input":{"file_path":"x.py"}}'

run_case "malformed_stdin" "pass" \
  'not-json-at-all'

run_case "short_allcaps_under_min" "pass" \
  "$(bash_payload 'echo ABORT')"

# --- Advisory cases: Bash with safe_tokenize path ---

run_case "bash_allcaps_unquoted" "advisory:LAST_365_DAYS" \
  "$(bash_payload 'psql -c "SELECT LAST_365_DAYS FROM t"')"

run_case "bash_allcaps_quoted_arg" "advisory:SHOPBY_AUTH_TOKEN" \
  "$(bash_payload 'curl -H "Authorization: Bearer SHOPBY_AUTH_TOKEN"')"

run_case "bash_compound_allcaps" "advisory:VENDOR_TOKEN_TYPE" \
  "$(bash_payload 'echo ok && gh issue create --body VENDOR_TOKEN_TYPE')"

run_case "bash_sql_3part_from" "advisory:catalog.schema.table" \
  "$(bash_payload 'psql -c "SELECT * FROM catalog.schema.table WHERE id=1"')"

# --- Advisory cases: Write / Edit (raw content path) ---

run_case "write_allcaps" "advisory:TOKEN_TYPE_BEARER" \
  "$(write_payload 'TOKEN_TYPE_BEARER value')"

run_case "edit_allcaps" "advisory:AUTH_TOKEN_SCOPE" \
  "$(edit_payload 'AUTH_TOKEN_SCOPE')"

run_case "write_sql_3part" "advisory:prod.analytics.events" \
  "$(write_payload 'SELECT * FROM prod.analytics.events WHERE dt > now()')"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "ALL $PASS PASSED"
  exit 0
else
  echo "$FAIL FAILED / $PASS PASSED — ${FAILED_NAMES[*]}"
  exit 1
fi
