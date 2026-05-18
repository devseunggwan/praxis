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
# Case 4 (M1): anchor fragment in link — should NOT produce advisory.
# docs/hook/INDEX.md#preflight-gate references a real file; fragment must be
# stripped before the existence check so this produces no advisory.
# ---------------------------------------------------------------------------
case4_body='# Anchor test

See [preflight hooks](docs/hook/INDEX.md#preflight-gate) for details.
'
run_with_body "$case4_body" 'gh issue create --title "Anchor"' "session-anchor-$$" c4_err c4_rc

c4_ok=1
[ "$c4_rc" -eq 0 ] || c4_ok=0
[ -z "$c4_err" ] || c4_ok=0
if [ "$c4_ok" -eq 1 ]; then
  echo "PASS: anchor fragment — stripped, no false-positive advisory"
  PASS=$((PASS + 1))
else
  echo "FAIL: anchor fragment (rc=$c4_rc, stderr='${c4_err:0:120}')"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("anchor fragment — stripped, no false-positive advisory")
fi

# ---------------------------------------------------------------------------
# Case 5 (M2): same body content re-submitted — dedup suppresses second advisory.
# ---------------------------------------------------------------------------
dedup_body='# Phantom again

See [phantom](hooks/pre-tool-use/phantom.sh) for details.
'
# First call — advisory expected.
run_with_body "$dedup_body" 'gh issue create --title "Dedup1"' "session-dedup-$$" c5a_err c5a_rc
# Second call with identical content + same session_id — dedup must suppress.
run_with_body "$dedup_body" 'gh issue edit 1 --body-file' "session-dedup-$$" c5b_err c5b_rc

c5_ok=1
# First call: advisory fired.
[ "$c5a_rc" -eq 0 ] || c5_ok=0
echo "$c5a_err" | grep -q "\[phantom-path\]" || c5_ok=0
# Second call with same content: dedup suppresses advisory.
[ "$c5b_rc" -eq 0 ] || c5_ok=0
[ -z "$c5b_err" ] || c5_ok=0
if [ "$c5_ok" -eq 1 ]; then
  echo "PASS: dedup — same body content suppresses duplicate advisory"
  PASS=$((PASS + 1))
else
  echo "FAIL: dedup (c5a_rc=$c5a_rc stderr='${c5a_err:0:80}' c5b_rc=$c5b_rc stderr='${c5b_err:0:80}')"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("dedup — same body content suppresses duplicate advisory")
fi

# ---------------------------------------------------------------------------
# Case 6 (M3): binary body file — hook must exit 0 with no advisory.
# ---------------------------------------------------------------------------
bin_body_file=$(mktemp /tmp/test-phantom-bin-XXXXXX)
# Write null bytes (binary) into the file.
python3 -c "import sys; sys.stdout.buffer.write(b'\x00\x01\x02\xff\xfe')" > "$bin_body_file"

bin_err_file=$(mktemp /tmp/test-phantom-err-XXXXXX)
bin_payload=$(python3 -c "
import json, sys
body_file = sys.argv[1]
session_id = sys.argv[2]
repo_root = sys.argv[3]
payload = {
    'session_id': session_id,
    'tool_name': 'Bash',
    'tool_input': {
        'command': 'gh issue create --title \"Binary\" --body-file ' + body_file,
    },
    'cwd': repo_root,
}
print(json.dumps(payload))
" "$bin_body_file" "session-binary-$$" "$ROOT_DIR")

env -u PRAXIS_PHANTOM_PATH_STRICT \
  python3 "$HOOK" >/dev/null 2>"$bin_err_file" <<< "$bin_payload"
bin_rc=$?
bin_err=$(cat "$bin_err_file")
rm -f "$bin_body_file" "$bin_err_file"

c6_ok=1
[ "$bin_rc" -eq 0 ] || c6_ok=0
[ -z "$bin_err" ] || c6_ok=0
if [ "$c6_ok" -eq 1 ]; then
  echo "PASS: binary body file — fail-open, exit 0, no advisory"
  PASS=$((PASS + 1))
else
  echo "FAIL: binary body file (rc=$bin_rc, stderr='${bin_err:0:120}')"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("binary body file — fail-open, exit 0, no advisory")
fi

# ---------------------------------------------------------------------------
# Case 7 (P1-Phase2): inline code span — `hooks/foo.sh` style phantom path
# triggers advisory; `docs/hook/INDEX.md` real path does not.
# ---------------------------------------------------------------------------
case7_body='# Inline code span test

The file `hooks/pre-tool-use/nonexistent.sh` should be implemented.
See `docs/hook/INDEX.md` for examples.
'
run_with_body "$case7_body" 'gh issue create --title "Inline"' "session-inline-$$" c7_err c7_rc

c7_ok=1
[ "$c7_rc" -eq 0 ] || c7_ok=0
echo "$c7_err" | grep -q "\[phantom-path\]" || c7_ok=0
echo "$c7_err" | grep -q "pre-tool-use/nonexistent.sh" || c7_ok=0
# Real path in inline code must NOT appear as phantom
echo "$c7_err" | grep -q "INDEX.md" && c7_ok=0
if [ "$c7_ok" -eq 1 ]; then
  echo "PASS: inline code span — phantom detected, real path not flagged"
  PASS=$((PASS + 1))
else
  echo "FAIL: inline code span (rc=$c7_rc, stderr='${c7_err:0:200}')"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("inline code span — phantom detected, real path not flagged")
fi

# ---------------------------------------------------------------------------
# Case 8 (P1-lstrip): lstrip("./") quirk — `./../escape.md` must NOT be
# mangled to `escape.md`.  The path starts with `./` so it is a recognized
# prefix; after stripping the `./` prefix it becomes `../escape.md` which
# does not exist under repo root and must trigger an advisory.
# ---------------------------------------------------------------------------
case8_body='# lstrip quirk test

See [escape](./../escape.md) for details.
'
run_with_body "$case8_body" 'gh issue create --title "Lstrip"' "session-lstrip-$$" c8_err c8_rc

c8_ok=1
[ "$c8_rc" -eq 0 ] || c8_ok=0
# The advisory must mention the path (not silently pass because `escape.md`
# happened to not exist — we verify the correct path appears in the message).
echo "$c8_err" | grep -q "\[phantom-path\]" || c8_ok=0
# The stripped path should be `../escape.md`, not `escape.md`
echo "$c8_err" | grep -q "\.\./escape\.md" || c8_ok=0
if [ "$c8_ok" -eq 1 ]; then
  echo "PASS: lstrip quirk — ./../escape.md -> ../escape.md (not mangled to escape.md)"
  PASS=$((PASS + 1))
else
  echo "FAIL: lstrip quirk (rc=$c8_rc, stderr='${c8_err:0:200}')"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("lstrip quirk — ./../escape.md -> ../escape.md (not mangled to escape.md)")
fi

# ---------------------------------------------------------------------------
# Case 9 (P1-binary): binary file with NUL bytes — hook must exit 0 with no
# advisory (NUL sniff causes early skip before link extraction).
# Distinct from Case 6 (M3) which tests non-UTF-8 bytes without NUL.
# ---------------------------------------------------------------------------
bin9_body_file=$(mktemp /tmp/test-phantom-bin9-XXXXXX)
# Write content that includes NUL bytes AND a phantom path string.
# Without NUL detection the phantom string would trigger an advisory;
# with detection the file should be skipped silently.
python3 -c "
import sys
# Embed a phantom-looking path string around NUL bytes
content = b'hooks/pre-tool-use/phantom.sh\x00binary\x00data'
sys.stdout.buffer.write(content)
" > "$bin9_body_file"

bin9_err_file=$(mktemp /tmp/test-phantom-err9-XXXXXX)
bin9_payload=$(python3 -c "
import json, sys
body_file = sys.argv[1]
session_id = sys.argv[2]
repo_root = sys.argv[3]
payload = {
    'session_id': session_id,
    'tool_name': 'Bash',
    'tool_input': {
        'command': 'gh issue create --title \"Binary9\" --body-file ' + body_file,
    },
    'cwd': repo_root,
}
print(json.dumps(payload))
" "$bin9_body_file" "session-binary9-$$" "$ROOT_DIR")

env -u PRAXIS_PHANTOM_PATH_STRICT \
  python3 "$HOOK" >/dev/null 2>"$bin9_err_file" <<< "$bin9_payload"
bin9_rc=$?
bin9_err=$(cat "$bin9_err_file")
rm -f "$bin9_body_file" "$bin9_err_file"

c9_ok=1
[ "$bin9_rc" -eq 0 ] || c9_ok=0
[ -z "$bin9_err" ] || c9_ok=0
if [ "$c9_ok" -eq 1 ]; then
  echo "PASS: binary NUL sniff — NUL-containing file skipped, no false-positive advisory"
  PASS=$((PASS + 1))
else
  echo "FAIL: binary NUL sniff (rc=$bin9_rc, stderr='${bin9_err:0:120}')"
  FAIL=$((FAIL + 1))
  FAILED_NAMES+=("binary NUL sniff — NUL-containing file skipped, no false-positive advisory")
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
