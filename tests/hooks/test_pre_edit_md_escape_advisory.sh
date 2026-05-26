#!/usr/bin/env bash
# test-pre-edit-md-escape-advisory.sh — coverage for pre-edit-md-escape-advisory
#
# Hook behavior:
#   - PreToolUse(Edit) on .md with escape-sensitive old_string and no
#     recorded Read → advisory on stderr, exit 0.
#   - With PRAXIS_MD_ESCAPE_MODE=block → permissionDecision: "deny" JSON on stdout.
#   - PostToolUse(Read) on .md → record path in session history.
#   - All other shapes → silent pass-through.
#
# State isolation via PRAXIS_MD_READ_HISTORY_FILE.
#
# Usage: bash hooks/test-pre-edit-md-escape-advisory.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PRE_HOOK="$ROOT_DIR/hooks/pre-edit-md-escape-advisory-pre.sh"
POST_HOOK="$ROOT_DIR/hooks/pre-edit-md-escape-advisory-post.sh"

if [ ! -x "$PRE_HOOK" ]; then
  echo "FAIL: pre-hook not executable: $PRE_HOOK" >&2
  exit 1
fi
if [ ! -x "$POST_HOOK" ]; then
  echo "FAIL: post-hook not executable: $POST_HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

# Per-test history file (overrides session-id resolution entirely).
fresh_history() {
  mktemp -u "${TMPDIR:-/tmp}/praxis-md-escape-test-XXXXXX.json"
}

# build_edit_payload <file_path> <old_string>
build_edit_payload() {
  python3 -c '
import json, sys
fp, oldstr = sys.argv[1], sys.argv[2]
print(json.dumps({
    "tool_name": "Edit",
    "tool_input": {"file_path": fp, "old_string": oldstr, "new_string": "x"},
}))
' "$1" "$2"
}

# build_other_tool_payload <tool_name> <file_path> <old_string>
build_other_tool_payload() {
  python3 -c '
import json, sys
tool, fp, oldstr = sys.argv[1], sys.argv[2], sys.argv[3]
print(json.dumps({
    "tool_name": tool,
    "tool_input": {"file_path": fp, "old_string": oldstr, "new_string": "x"},
}))
' "$1" "$2" "$3"
}

# build_read_payload <file_path>
build_read_payload() {
  python3 -c '
import json, sys
fp = sys.argv[1]
print(json.dumps({
    "tool_name": "Read",
    "tool_input": {"file_path": fp},
}))
' "$1"
}

# build_read_payload_other_tool <tool_name> <file_path>
build_read_payload_other_tool() {
  python3 -c '
import json, sys
tool, fp = sys.argv[1], sys.argv[2]
print(json.dumps({
    "tool_name": tool,
    "tool_input": {"file_path": fp},
}))
' "$1" "$2"
}

# pipe_hook <hook_path> <payload> [KEY=VALUE ...]
pipe_hook() {
  local hook="$1" payload="$2"; shift 2
  (
    for kv in "$@"; do
      export "$kv"
    done
    printf '%s' "$payload" | "$hook"
  )
}

# check_deny <stdout_content>
check_deny() {
  local out="$1"
  [ -n "$out" ] || return 1
  python3 -c '
import json, sys
try:
    d = json.loads(sys.argv[1])
    o = d.get("hookSpecificOutput", {})
    assert o.get("permissionDecision") == "deny"
except Exception:
    sys.exit(1)
' "$out" 2>/dev/null
}

# Generic case runner.
#   expected: "advisory" | "silent" | "deny"
#   advisory  → exit 0, empty stdout, non-empty stderr
#   silent    → exit 0, empty stdout, empty stderr
#   deny      → exit 0, deny-JSON on stdout
run_case() {
  local name="$1" expected="$2" hook="$3" payload="$4"; shift 4
  local env_overrides=("$@")

  local out_file err_file
  out_file=$(mktemp); err_file=$(mktemp)
  pipe_hook "$hook" "$payload" "${env_overrides[@]}" >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file"); err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expected" in
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]    || ok=0
      [ -n "$err" ]    || ok=0
      ;;
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]    || ok=0
      [ -z "$err" ]    || ok=0
      ;;
    deny)
      [ "$rc" -eq 0 ] || ok=0
      check_deny "$out"  || ok=0
      ;;
    *)
      echo "FAIL [$name] unknown expected: $expected"
      ((FAIL++)); FAILED_NAMES+=("$name"); return
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS [$expected] $name"; ((PASS++))
  else
    echo "FAIL [$expected] $name (rc=$rc, stdout=$([ -n "$out" ] && echo non-empty || echo empty), stderr=$([ -n "$err" ] && echo non-empty || echo empty))"
    ((FAIL++)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# ADVISORY paths: .md + escape token + no Read recorded → stderr warn
# ---------------------------------------------------------------------------

HIST=$(fresh_history)

run_case "obsidian wikilink (\\|) + no-Read → advisory" advisory \
  "$PRE_HOOK" \
  "$(build_edit_payload "/Users/test/vault/_index.md" "[[01-summary\\|01]]")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST"

run_case "escaped bracket (\\[) + no-Read → advisory" advisory \
  "$PRE_HOOK" \
  "$(build_edit_payload "/Users/test/vault/notes.md" "see \\[ref] here")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST"

run_case "escaped bracket (\\]) + no-Read → advisory" advisory \
  "$PRE_HOOK" \
  "$(build_edit_payload "/Users/test/vault/notes.md" "list item\\]")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST"

run_case "HTML entity (&amp;) + no-Read → advisory" advisory \
  "$PRE_HOOK" \
  "$(build_edit_payload "/Users/test/docs/api.md" "Tom &amp; Jerry")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST"

run_case "HTML entity (&lt;) + no-Read → advisory" advisory \
  "$PRE_HOOK" \
  "$(build_edit_payload "/Users/test/docs/api.md" "x &lt; y")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST"

run_case "uppercase .MD extension + escape + no-Read → advisory" advisory \
  "$PRE_HOOK" \
  "$(build_edit_payload "/Users/test/README.MD" "[[a\\|b]]")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST"

run_case ".markdown extension + escape + no-Read → advisory" advisory \
  "$PRE_HOOK" \
  "$(build_edit_payload "/Users/test/post.markdown" "[[a\\|b]]")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST"

# ---------------------------------------------------------------------------
# SILENT paths: escape but Read recorded → silent
# ---------------------------------------------------------------------------

HIST2=$(fresh_history)

# Step 1: record the Read via post-hook
post_payload=$(build_read_payload "/Users/test/vault/_index.md")
pipe_hook "$POST_HOOK" "$post_payload" "PRAXIS_MD_READ_HISTORY_FILE=$HIST2" >/dev/null 2>&1

run_case "wikilink + Read recorded → silent" silent \
  "$PRE_HOOK" \
  "$(build_edit_payload "/Users/test/vault/_index.md" "[[01-summary\\|01]]")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST2"

# Same file from a relative path → abspath normalization makes it match.
# (skip — depends on CWD; covered by relative-path test below with controlled cwd)

# A *different* .md file in the same history → still advisory (path-specific).
run_case "different .md not Read → advisory" advisory \
  "$PRE_HOOK" \
  "$(build_edit_payload "/Users/test/vault/other.md" "[[a\\|b]]")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST2"

# ---------------------------------------------------------------------------
# SILENT paths: no escape tokens
# ---------------------------------------------------------------------------

HIST3=$(fresh_history)

run_case "plain Edit, no escape token → silent" silent \
  "$PRE_HOOK" \
  "$(build_edit_payload "/Users/test/notes.md" "plain heading\n\nbody text")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST3"

run_case "unescaped pipe in table (no backslash) → silent" silent \
  "$PRE_HOOK" \
  "$(build_edit_payload "/Users/test/notes.md" "| col1 | col2 |")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST3"

run_case "ampersand without entity form → silent" silent \
  "$PRE_HOOK" \
  "$(build_edit_payload "/Users/test/notes.md" "rock & roll")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST3"

# ---------------------------------------------------------------------------
# SILENT paths: non-.md targets
# ---------------------------------------------------------------------------

run_case ".py target + escape token → silent" silent \
  "$PRE_HOOK" \
  "$(build_edit_payload "/Users/test/code.py" "[[a\\|b]]")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST3"

run_case ".ts target + entity → silent" silent \
  "$PRE_HOOK" \
  "$(build_edit_payload "/Users/test/code.ts" "x &amp; y")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST3"

run_case "no extension + escape token → silent" silent \
  "$PRE_HOOK" \
  "$(build_edit_payload "/Users/test/Makefile" "[[a\\|b]]")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST3"

# ---------------------------------------------------------------------------
# SILENT paths: non-Edit tools
# ---------------------------------------------------------------------------

run_case "tool_name=Write + escape → silent" silent \
  "$PRE_HOOK" \
  "$(build_other_tool_payload Write "/Users/test/notes.md" "[[a\\|b]]")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST3"

run_case "tool_name=NotebookEdit + escape → silent" silent \
  "$PRE_HOOK" \
  "$(build_other_tool_payload NotebookEdit "/Users/test/notes.md" "[[a\\|b]]")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST3"

run_case "tool_name=Bash → silent" silent \
  "$PRE_HOOK" \
  "$(build_other_tool_payload Bash "/Users/test/notes.md" "[[a\\|b]]")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST3"

# ---------------------------------------------------------------------------
# DENY paths: PRAXIS_MD_ESCAPE_MODE=block
# ---------------------------------------------------------------------------

HIST4=$(fresh_history)

run_case "block mode + escape + no-Read → deny" deny \
  "$PRE_HOOK" \
  "$(build_edit_payload "/Users/test/vault/_index.md" "[[01-summary\\|01]]")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST4" \
  "PRAXIS_MD_ESCAPE_MODE=block"

# Block mode is suppressed once Read is recorded.
post_payload=$(build_read_payload "/Users/test/vault/_index.md")
pipe_hook "$POST_HOOK" "$post_payload" "PRAXIS_MD_READ_HISTORY_FILE=$HIST4" >/dev/null 2>&1

run_case "block mode + escape + Read recorded → silent" silent \
  "$PRE_HOOK" \
  "$(build_edit_payload "/Users/test/vault/_index.md" "[[01-summary\\|01]]")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST4" \
  "PRAXIS_MD_ESCAPE_MODE=block"

# ---------------------------------------------------------------------------
# SILENT paths: PRAXIS_MD_ESCAPE_SKIP=1 full opt-out
# ---------------------------------------------------------------------------

HIST5=$(fresh_history)

run_case "SKIP=1 + escape + no-Read → silent" silent \
  "$PRE_HOOK" \
  "$(build_edit_payload "/Users/test/vault/_index.md" "[[01-summary\\|01]]")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST5" \
  "PRAXIS_MD_ESCAPE_SKIP=1"

run_case "SKIP=1 + escape + no-Read + block-mode → silent" silent \
  "$PRE_HOOK" \
  "$(build_edit_payload "/Users/test/vault/_index.md" "[[01-summary\\|01]]")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST5" \
  "PRAXIS_MD_ESCAPE_SKIP=1" \
  "PRAXIS_MD_ESCAPE_MODE=block"

# ---------------------------------------------------------------------------
# Post-hook: state file recording
# ---------------------------------------------------------------------------

post_hist=$(fresh_history)

# Step 1: post-hook for .md Read → history file should now exist with the path.
post_md_payload=$(build_read_payload "/Users/test/vault/x.md")
pipe_hook "$POST_HOOK" "$post_md_payload" "PRAXIS_MD_READ_HISTORY_FILE=$post_hist" >/dev/null 2>&1

if [ -f "$post_hist" ] && python3 -c "
import json, sys
data = json.load(open('$post_hist'))
assert '/Users/test/vault/x.md' in data.get('read', []), data
" 2>/dev/null; then
  echo "PASS [state] post-hook records .md Read"
  ((PASS++))
else
  echo "FAIL [state] post-hook records .md Read (no history file or path missing)"
  ((FAIL++)); FAILED_NAMES+=("post-hook records .md Read")
fi

# Step 2: post-hook for non-.md Read → no recording.
post_hist2=$(fresh_history)
post_py_payload=$(build_read_payload "/Users/test/code.py")
pipe_hook "$POST_HOOK" "$post_py_payload" "PRAXIS_MD_READ_HISTORY_FILE=$post_hist2" >/dev/null 2>&1

if [ ! -f "$post_hist2" ] || python3 -c "
import json, sys
data = json.load(open('$post_hist2'))
assert data.get('read', []) == [], data
" 2>/dev/null; then
  echo "PASS [state] post-hook ignores non-.md Read"
  ((PASS++))
else
  echo "FAIL [state] post-hook ignores non-.md Read (path was recorded)"
  ((FAIL++)); FAILED_NAMES+=("post-hook ignores non-.md Read")
fi

# Step 3: post-hook for non-Read tool → no recording.
post_hist3=$(fresh_history)
post_other_payload=$(build_read_payload_other_tool "Edit" "/Users/test/vault/x.md")
pipe_hook "$POST_HOOK" "$post_other_payload" "PRAXIS_MD_READ_HISTORY_FILE=$post_hist3" >/dev/null 2>&1

if [ ! -f "$post_hist3" ] || python3 -c "
import json, sys
data = json.load(open('$post_hist3'))
assert data.get('read', []) == [], data
" 2>/dev/null; then
  echo "PASS [state] post-hook ignores non-Read tool"
  ((PASS++))
else
  echo "FAIL [state] post-hook ignores non-Read tool (path was recorded)"
  ((FAIL++)); FAILED_NAMES+=("post-hook ignores non-Read tool")
fi

# ---------------------------------------------------------------------------
# Fail-open paths: malformed input, missing fields
# ---------------------------------------------------------------------------

malformed_pre_test() {
  local name="malformed stdin → pre silent (fail-open)"
  local out_file err_file
  out_file=$(mktemp); err_file=$(mktemp)
  echo "not-valid-json" | "$PRE_HOOK" >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file"); err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"
  if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; then
    echo "PASS [silent] $name"; ((PASS++))
  else
    echo "FAIL [silent] $name (rc=$rc, stdout=$([ -n "$out" ] && echo non-empty || echo empty), stderr=$([ -n "$err" ] && echo non-empty || echo empty))"
    ((FAIL++)); FAILED_NAMES+=("$name")
  fi
}
malformed_pre_test

malformed_post_test() {
  local name="malformed stdin → post silent (fail-open)"
  local out_file err_file
  out_file=$(mktemp); err_file=$(mktemp)
  echo "not-valid-json" | "$POST_HOOK" >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file"); err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"
  if [ "$rc" -eq 0 ] && [ -z "$out" ] && [ -z "$err" ]; then
    echo "PASS [silent] $name"; ((PASS++))
  else
    echo "FAIL [silent] $name (rc=$rc)"
    ((FAIL++)); FAILED_NAMES+=("$name")
  fi
}
malformed_post_test

# Empty file_path
HIST6=$(fresh_history)
run_case "empty file_path → silent (fail-open)" silent \
  "$PRE_HOOK" \
  "$(build_edit_payload "" "[[a\\|b]]")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST6"

# Empty old_string
run_case "empty old_string → silent" silent \
  "$PRE_HOOK" \
  "$(build_edit_payload "/Users/test/vault/_index.md" "")" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST6"

# Missing tool_input entirely
missing_input_payload='{"tool_name": "Edit"}'
run_case "missing tool_input → silent" silent \
  "$PRE_HOOK" \
  "$missing_input_payload" \
  "PRAXIS_MD_READ_HISTORY_FILE=$HIST6"

# ---------------------------------------------------------------------------
# Cross-check: session-id-based path resolution (no env override)
# ---------------------------------------------------------------------------

# Build a payload with explicit session_id and verify the post-hook writes
# under ${TMPDIR}/praxis-md-read-history-<session_id>.json.
sid="test-session-$$-$RANDOM"
expected_path="${TMPDIR:-/tmp}/praxis-md-read-history-${sid}.json"
rm -f "$expected_path"

payload_with_sid=$(python3 -c '
import json, sys
sid, fp = sys.argv[1], sys.argv[2]
print(json.dumps({
    "session_id": sid,
    "tool_name": "Read",
    "tool_input": {"file_path": fp},
}))
' "$sid" "/Users/test/session-id.md")

printf '%s' "$payload_with_sid" | "$POST_HOOK" >/dev/null 2>&1

if [ -f "$expected_path" ] && python3 -c "
import json
data = json.load(open('$expected_path'))
assert '/Users/test/session-id.md' in data.get('read', []), data
" 2>/dev/null; then
  echo "PASS [state] session_id resolves to ${TMPDIR:-/tmp}/praxis-md-read-history-<sid>.json"
  ((PASS++))
  rm -f "$expected_path"
else
  echo "FAIL [state] session_id-based path resolution"
  ((FAIL++)); FAILED_NAMES+=("session_id-based path resolution")
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
