#!/bin/bash
# test_response_language_nudge.sh — coverage for the PostToolUse
# response-language drift nudge (issue #1476).
#
# Synthesizes a transcript fixture (current turn: one user message, one
# assistant message) and a PostToolUse payload pointing at it, then asserts:
#   nudge  -> exit 0 + stdout JSON with hookSpecificOutput.additionalContext
#   silent -> exit 0 + stdout empty
#
# Usage: bash tests/hooks/advisory-nudge/test_response_language_nudge.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/response-language-nudge/impl.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

new_case_dir() {
  T=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
}

# make_transcript <assistant_text> <uuid> [<sid_marker>]
#   Writes a two-line JSONL transcript: a user turn-boundary message, then an
#   assistant message carrying `assistant_text` as its sole text block and
#   `uuid` as its transcript uuid.
make_transcript() {
  local text="$1" uuid="$2"
  python3 -c '
import json, sys
text, uuid = sys.argv[1], sys.argv[2]
user = {"type": "user", "message": {"role": "user", "content": "hi"}}
assistant = {
    "type": "assistant",
    "uuid": uuid,
    "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
}
print(json.dumps(user))
print(json.dumps(assistant))
' "$text" "$uuid" > "$T/transcript.jsonl"
}

# payload_for <uuid-suffix> — a PostToolUse payload pointing at $T/transcript.jsonl.
payload_for() {
  local sid="${1:-sess-1}"
  python3 -c '
import json, sys
sid, cwd = sys.argv[1], sys.argv[2]
print(json.dumps({
    "session_id": sid,
    "tool_name": "Bash",
    "tool_input": {"command": "echo hi"},
    "hook_event_name": "PostToolUse",
    "transcript_path": cwd + "/transcript.jsonl",
}))' "$sid" "$T"
}

run_hook() {
  local env_setup="$1" payload="$2"
  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)
  local prefix="${env_setup:-true}"
  bash -c "$prefix ; echo '$payload' | python3 '$HOOK'" >"$out_file" 2>"$err_file"
  LAST_RC=$?
  LAST_OUT=$(cat "$out_file")
  LAST_ERR=$(cat "$err_file")
  rm -f "$out_file" "$err_file"
}

assert_nudge() {
  local name="$1"
  local ok=1
  [ "$LAST_RC" -eq 0 ] || ok=0
  echo "$LAST_OUT" | grep -q '"hookEventName": "PostToolUse"' || ok=0
  echo "$LAST_OUT" | grep -q '"additionalContext"' || ok=0
  echo "$LAST_OUT" | grep -q 'response-language-nudge' || ok=0
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=nudge rc=$LAST_RC"
    [ -n "$LAST_OUT" ] && echo "        stdout: $LAST_OUT"
    [ -n "$LAST_ERR" ] && echo "        stderr: $LAST_ERR"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

assert_silent() {
  local name="$1"
  local ok=1
  [ "$LAST_RC" -eq 0 ] || ok=0
  [ -z "$LAST_OUT" ]   || ok=0
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=silent rc=$LAST_RC"
    [ -n "$LAST_OUT" ] && echo "        stdout: $LAST_OUT"
    [ -n "$LAST_ERR" ] && echo "        stderr: $LAST_ERR"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# A ~40-char English sentence — well over the 20-char floor, ~0% Hangul.
ENGLISH_TEXT="Running the build now and will report the test output shortly."
# A ~40-char Korean sentence — well over the 20-char floor, high Hangul ratio.
KOREAN_TEXT="지금 빌드를 실행하고 있으며 테스트 결과를 곧 보고하겠습니다."
# Code-only block: fences + inline code + a path, nothing else.
CODE_ONLY_TEXT='```bash
git status
```
Also see `hooks/_lib/_paths.py` at /Users/x/projects/praxis/hooks/_lib/_paths.py'
# Under the 20-char floor even before stripping.
SHORT_TEXT="ok, done."

STATE_TMPDIR=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
export PRAXIS_HOME="$STATE_TMPDIR/home"
STATE_ENV="export PRAXIS_HOME='$STATE_TMPDIR/home'"

# =============================================================================
# Opt-in gate
# =============================================================================

new_case_dir
make_transcript "$ENGLISH_TEXT" "uuid-1"
PAYLOAD=$(payload_for "sess-unset")
run_hook "unset PRAXIS_RESPONSE_LANGUAGE; $STATE_ENV" "$PAYLOAD"
assert_silent "env unset: silent even on a drifted English block"

new_case_dir
make_transcript "$ENGLISH_TEXT" "uuid-2"
PAYLOAD=$(payload_for "sess-blank")
run_hook "export PRAXIS_RESPONSE_LANGUAGE='   '; $STATE_ENV" "$PAYLOAD"
assert_silent "env blank (whitespace only): treated as unset"

new_case_dir
make_transcript "$ENGLISH_TEXT" "uuid-3"
PAYLOAD=$(payload_for "sess-unsupported")
run_hook "export PRAXIS_RESPONSE_LANGUAGE=fr; $STATE_ENV" "$PAYLOAD"
assert_silent "env set to an unsupported (non-Korean) language: silent"

# =============================================================================
# Korean-alias normalization
# =============================================================================

for alias in ko KR Korean ko-kr ko_kr 한국어; do
  new_case_dir
  make_transcript "$ENGLISH_TEXT" "uuid-alias-$alias"
  PAYLOAD=$(payload_for "sess-alias-$alias")
  run_hook "export PRAXIS_RESPONSE_LANGUAGE='$alias'; $STATE_ENV" "$PAYLOAD"
  assert_nudge "language alias '$alias' recognized as Korean"
done

# =============================================================================
# Ratio detection — positive and negative controls
# =============================================================================

new_case_dir
make_transcript "$ENGLISH_TEXT" "uuid-english"
PAYLOAD=$(payload_for "sess-english")
run_hook "export PRAXIS_RESPONSE_LANGUAGE=ko; $STATE_ENV" "$PAYLOAD"
assert_nudge "English narration block: nudge (positive control)"

new_case_dir
make_transcript "$KOREAN_TEXT" "uuid-korean"
PAYLOAD=$(payload_for "sess-korean")
run_hook "export PRAXIS_RESPONSE_LANGUAGE=ko; $STATE_ENV" "$PAYLOAD"
assert_silent "Korean narration block: silent (negative control)"

new_case_dir
make_transcript "$CODE_ONLY_TEXT" "uuid-code"
PAYLOAD=$(payload_for "sess-code")
run_hook "export PRAXIS_RESPONSE_LANGUAGE=ko; $STATE_ENV" "$PAYLOAD"
assert_silent "code-only block (fences/inline-code/path stripped below floor): silent"

new_case_dir
make_transcript "$SHORT_TEXT" "uuid-short"
PAYLOAD=$(payload_for "sess-short")
run_hook "export PRAXIS_RESPONSE_LANGUAGE=ko; $STATE_ENV" "$PAYLOAD"
assert_silent "under-20-char block: silent"

# =============================================================================
# Dedup — same assistant-message uuid must not nudge twice
# =============================================================================

new_case_dir
make_transcript "$ENGLISH_TEXT" "uuid-dedup"
PAYLOAD=$(payload_for "sess-dedup")
run_hook "export PRAXIS_RESPONSE_LANGUAGE=ko; $STATE_ENV" "$PAYLOAD"
assert_nudge "dedup: first PostToolUse for this uuid nudges"
run_hook "export PRAXIS_RESPONSE_LANGUAGE=ko; $STATE_ENV" "$PAYLOAD"
assert_silent "dedup: second PostToolUse for the SAME uuid stays silent"

# A different uuid in the same session must still nudge — dedup is per
# message, not a session-wide kill switch.
new_case_dir
make_transcript "$ENGLISH_TEXT" "uuid-dedup-2"
PAYLOAD2=$(payload_for "sess-dedup")
run_hook "export PRAXIS_RESPONSE_LANGUAGE=ko; $STATE_ENV" "$PAYLOAD2"
assert_nudge "dedup: a different uuid in the same session still nudges"

# =============================================================================
# Fail-open
# =============================================================================

run_hook "export PRAXIS_RESPONSE_LANGUAGE=ko; $STATE_ENV" '{not valid json'
assert_silent "malformed JSON stdin: fail-open"

new_case_dir
PAYLOAD=$(python3 -c "import json; print(json.dumps({'tool_name': 'Bash', 'transcript_path': '$T/transcript.jsonl'}))")
run_hook "export PRAXIS_RESPONSE_LANGUAGE=ko; $STATE_ENV" "$PAYLOAD"
assert_silent "missing session_id: fail-open"

new_case_dir
PAYLOAD=$(python3 -c "import json; print(json.dumps({'session_id': 'sess-x', 'tool_name': 'Bash', 'transcript_path': '$T/does-not-exist.jsonl'}))")
run_hook "export PRAXIS_RESPONSE_LANGUAGE=ko; $STATE_ENV" "$PAYLOAD"
assert_silent "unreadable transcript_path: fail-open"

rm -rf "$STATE_TMPDIR"

# =============================================================================
# Summary
# =============================================================================

echo
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
  exit 1
fi
exit 0
