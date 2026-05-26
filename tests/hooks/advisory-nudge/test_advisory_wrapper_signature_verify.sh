#!/usr/bin/env bash
# test-advisory-wrapper-signature-verify.sh — coverage for the wrapper-signature advisory hook
#
# Synthesizes Claude Code PreToolUse payloads and asserts:
#   advisory → exit 0 + stderr non-empty
#   pass     → exit 0 + stderr empty
#
# Usage: bash hooks/test-advisory-wrapper-signature-verify.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/advisory-wrapper-signature-verify/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

# Run a single test case.
# $1 = name
# $2 = expected: "advisory" (exit 0 + stderr non-empty) | "pass" (exit 0 + stderr empty)
# $3 = JSON payload (string)
run_case() {
  local name="$1" expected="$2" payload="$3"
  local err_file rc
  err_file=$(mktemp)
  printf '%s' "$payload" | "$HOOK" >/dev/null 2>"$err_file"
  rc=$?
  local err_content
  err_content=$(cat "$err_file"); rm -f "$err_file"

  local ok=1
  case "$expected" in
    advisory)
      [ "$rc" -eq 0 ] && [ -n "$err_content" ] || ok=0
      ;;
    pass)
      [ "$rc" -eq 0 ] && [ -z "$err_content" ] || ok=0
      ;;
    *)
      echo "FAIL: unknown expected: $expected" >&2
      ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS: $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $name (rc=$rc, stderr='${err_content:0:120}')"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------

make_write_payload() {
  # $1 = file_path, $2 = content
  python3 -c "
import json, sys
payload = {
    'session_id': 'test-session',
    'tool_name': 'Write',
    'tool_input': {
        'file_path': sys.argv[1],
        'content': sys.argv[2],
    },
    'cwd': '/tmp',
}
print(json.dumps(payload))
" "$1" "$2"
}

make_edit_payload() {
  # $1 = file_path, $2 = new_string
  python3 -c "
import json, sys
payload = {
    'session_id': 'test-session',
    'tool_name': 'Edit',
    'tool_input': {
        'file_path': sys.argv[1],
        'old_string': 'foo',
        'new_string': sys.argv[2],
    },
    'cwd': '/tmp',
}
print(json.dumps(payload))
" "$1" "$2"
}

make_other_tool_payload() {
  # $1 = tool_name, $2 = file_path, $3 = content
  python3 -c "
import json, sys
payload = {
    'session_id': 'test-session',
    'tool_name': sys.argv[1],
    'tool_input': {
        'file_path': sys.argv[2],
        'content': sys.argv[3],
    },
    'cwd': '/tmp',
}
print(json.dumps(payload))
" "$1" "$2" "$3"
}

# ---------------------------------------------------------------------------
# ADVISORY cases (wrapper shape path + delegation pattern)
# ---------------------------------------------------------------------------

run_case "Write client.py with return get_*( delegation" advisory \
  "$(make_write_payload '/repo/foo/client.py' 'def fetch():
    return get_user(id)')"

run_case "Write client.py with return create_*( delegation" advisory \
  "$(make_write_payload '/repo/foo/client.py' 'def make():
    return create_session(user_id)')"

run_case "Write *_wrapper path with from queries import" advisory \
  "$(make_write_payload '/repo/orders_wrapper.py' 'from foo.queries import get_order
def run(): return get_order(1)')"

run_case "Edit client.py with from *.client import" advisory \
  "$(make_edit_payload '/repo/svc/client.py' 'from acme.client import APIClient
client = APIClient()')"

run_case "Edit *_wrapper path with return get_*(" advisory \
  "$(make_edit_payload '/repo/pkg/auth_wrapper/main.py' 'return get_token(scope)')"

run_case "Write user_client.py (endswith client.py)" advisory \
  "$(make_write_payload '/repo/user_client.py' 'from a.queries import x')"

# ---------------------------------------------------------------------------
# PASS cases — wrong shape OR no delegation pattern OR wrong tool
# ---------------------------------------------------------------------------

run_case "Pass: client.py without delegation patterns" pass \
  "$(make_write_payload '/repo/foo/client.py' 'def hello():
    return \"hello\"')"

run_case "Pass: regular .py path without _wrapper/client.py" pass \
  "$(make_write_payload '/repo/foo/service.py' 'from a.queries import get_x
return get_x(1)')"

run_case "Pass: README.md path" pass \
  "$(make_write_payload '/repo/README.md' 'return get_x()')"

run_case "Pass: Read tool not in scope" pass \
  "$(make_other_tool_payload 'Read' '/repo/foo/client.py' 'return get_x()')"

run_case "Pass: NotebookEdit not in scope" pass \
  "$(make_other_tool_payload 'NotebookEdit' '/repo/foo/client.py' 'return get_x()')"

run_case "Pass: client.py with return without get_/create_ prefix" pass \
  "$(make_write_payload '/repo/foo/client.py' 'def run():
    return self.value')"

run_case "Pass: similar substring 'queries' not as full module path" pass \
  "$(make_write_payload '/repo/foo/client.py' '# notes about queries import patterns')"

run_case "Pass: _wrapper path but .md file (not Python)" pass \
  "$(make_write_payload '/repo/foo_wrapper.md' 'return get_user(id)')"

run_case "Pass: test file under /tests/ excluded" pass \
  "$(make_write_payload '/repo/tests/test_client.py' 'return get_user(1)')"

run_case "Pass: test_*.py file excluded" pass \
  "$(make_write_payload '/repo/foo/test_client.py' 'from a.queries import x')"

run_case "Pass: *_test.py file excluded" pass \
  "$(make_write_payload '/repo/foo/client_test.py' 'from a.queries import x')"

run_case "Pass: /test/ directory excluded" pass \
  "$(make_write_payload '/repo/test/foo_wrapper.py' 'return create_x(1)')"

# ---------------------------------------------------------------------------
# EDGE cases — fail-open
# ---------------------------------------------------------------------------

run_case "Edge: malformed JSON" pass \
  'not valid json at all'

run_case "Edge: empty content field" pass \
  "$(make_write_payload '/repo/foo/client.py' '')"

run_case "Edge: missing file_path" pass \
  "$(python3 -c "
import json
print(json.dumps({
    'session_id': 't',
    'tool_name': 'Write',
    'tool_input': {'content': 'return get_x()'},
}))
")"

run_case "Edge: Edit with missing new_string on client.py" pass \
  "$(python3 -c "
import json
print(json.dumps({
    'session_id': 't',
    'tool_name': 'Edit',
    'tool_input': {'file_path': '/repo/foo/client.py', 'old_string': 'a'},
}))
")"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ ${#FAILED_NAMES[@]} -gt 0 ]; then
  echo "Failed:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
  exit 1
fi
exit 0
