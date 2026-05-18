#!/bin/bash
# test_external_write_path_existence_check.sh — coverage for hooks/external-write-path-existence-check.py
#
# Synthesizes Claude Code PreToolUse hook payloads and asserts:
#   advisory → exit 0 + stderr contains "[phantom-path]"
#   pass     → exit 0 + stderr empty
#
# Usage: bash tests/test_external_write_path_existence_check.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$ROOT_DIR/hooks/external-write-path-existence-check.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expectation payload_json
#   expectation:
#     "advisory" — stderr contains "[phantom-path]", rc=0
#     "pass"     — stderr empty, rc=0
run_case() {
  local name="$1" expectation="$2" payload="$3"

  local err_file
  err_file=$(mktemp)
  # Unset strict env so advisory vs pass tests are not affected by environment.
  echo "$payload" | env -u PRAXIS_PHANTOM_PATH_STRICT python3 "$HOOK" >/dev/null 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"

  local ok=1
  case "$expectation" in
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      echo "$err" | grep -q "\[phantom-path\]" || ok=0
      ;;
    pass)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      ;;
    *)
      echo "FAIL: unknown expectation: $expectation" >&2
      ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS: $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $name (rc=$rc, stderr='${err:0:120}')"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# build_payload writes $1 (body content) to a temp file, runs the hook with
# the given gh command ($2) and session_id ($3), captures stderr into $4,
# and stores the exit code in $5.  The temp file is deleted before returning.
# This avoids subshell-array propagation issues and BSD mktemp suffix limits.
run_with_body() {
  local content="$1" gh_cmd="$2" session_id="$3"
  local out_err_var="$4" out_rc_var="$5"

  local body_file err_file payload rc err_content
  body_file=$(mktemp /tmp/test-phantom-XXXXXX)
  err_file=$(mktemp /tmp/test-phantom-err-XXXXXX)
  printf '%s' "$content" > "$body_file"

  payload=$(python3 -c "
import json, sys
body_file, session_id, repo_root, gh_cmd = sys.argv[1:]
payload = {
    'session_id': session_id,
    'tool_name': 'Bash',
    'tool_input': {
        'command': gh_cmd + ' --body-file ' + body_file,
    },
    'cwd': repo_root,
}
print(json.dumps(payload))
" "$body_file" "$session_id" "$ROOT_DIR" "$gh_cmd")

  env -u PRAXIS_PHANTOM_PATH_STRICT \
    python3 "$HOOK" >/dev/null 2>"$err_file" <<< "$payload"
  rc=$?
  err_content=$(cat "$err_file")
  rm -f "$body_file" "$err_file"

  # Return values via named variables.
  printf -v "$out_err_var" '%s' "$err_content"
  printf -v "$out_rc_var" '%s' "$rc"
}

# ---------------------------------------------------------------------------
# Case 1: existing path — body links to a real path in the repo.
# ---------------------------------------------------------------------------
case1_body='# Test PR

See [hook index](docs/hook/INDEX.md) for the full list.
'
run_with_body "$case1_body" 'gh issue create --title "Test"' "session-exist-$$" c1_err c1_rc

c1_ok=1
[ "$c1_rc" -eq 0 ] || c1_ok=0
[ -z "$c1_err" ] || c1_ok=0
if [ "$c1_ok" -eq 1 ]; then
  echo "PASS: existing path — docs/hook/INDEX.md is real"
  PASS=$((PASS + 1))
else
  echo "FAIL: existing path (rc=$c1_rc, stderr='${c1_err:0:120}')"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("existing path — docs/hook/INDEX.md is real")
fi

# ---------------------------------------------------------------------------
# Case 2: missing path — body links to a phantom path.
# ---------------------------------------------------------------------------
# hooks/pre-tool-use/ does not exist (flat hooks/ layout has no sub-dirs).
case2_body='# Bug report

The file [phantom hook](hooks/pre-tool-use/external-write-path-existence-check.sh)
should be implemented first.
'
run_with_body "$case2_body" 'gh issue create --title "Bug"' "session-miss-$$" c2_err c2_rc

c2_ok=1
[ "$c2_rc" -eq 0 ] || c2_ok=0
echo "$c2_err" | grep -q "\[phantom-path\]" || c2_ok=0
if [ "$c2_ok" -eq 1 ]; then
  echo "PASS: missing path — hooks/pre-tool-use/... phantom"
  PASS=$((PASS + 1))
else
  echo "FAIL: missing path (rc=$c2_rc, stderr='${c2_err:0:120}')"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("missing path — hooks/pre-tool-use/... phantom")
fi

# ---------------------------------------------------------------------------
# Case 3: mixed paths — one real, one phantom.
# Only the phantom should appear in the advisory.
# ---------------------------------------------------------------------------
case3_body='# Mixed links

- Real: [INDEX](docs/hook/INDEX.md)
- Phantom: [nonexistent](hooks/pre-tool-use/nonexistent.sh)
'
run_with_body "$case3_body" 'gh pr create --title "Mixed"' "session-mixed-$$" c3_err c3_rc

c3_ok=1
[ "$c3_rc" -eq 0 ] || c3_ok=0
echo "$c3_err" | grep -q "\[phantom-path\]" || c3_ok=0
echo "$c3_err" | grep -q "pre-tool-use/nonexistent.sh" || c3_ok=0
# docs/hook/INDEX.md must NOT appear as a phantom
echo "$c3_err" | grep -q "INDEX.md" && c3_ok=0
if [ "$c3_ok" -eq 1 ]; then
  echo "PASS: mixed paths — only phantom listed"
  PASS=$((PASS + 1))
else
  echo "FAIL: mixed paths (rc=$c3_rc, stderr='${c3_err:0:200}')"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("mixed paths — only phantom listed")
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for name in "${FAILED_NAMES[@]}"; do
    echo "  - $name"
  done
  exit 1
fi
exit 0
