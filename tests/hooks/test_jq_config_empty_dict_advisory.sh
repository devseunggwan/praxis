#!/bin/bash
# test_jq_config_empty_dict_advisory.sh — coverage for
# hooks/jq-config-empty-dict-advisory.py
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   advisory:<marker>  — exit 0, stderr contains <marker>
#   silent             — exit 0, stderr empty
#
# In-scope items covered (issue #338):
#   1. --arg/--argjson/--slurpfile/--rawfile 2-token skip — config path after
#      them is correctly recognized and not swallowed as a flag operand.
#   2. jq -n (null input) short-circuit — hook is silent.
#   3. Multi-file (jq '.' a.json b.json) — each config path is checked.
#   4. jq PATH-missing fail-open — hook is silent when jq is not on PATH.
#   5. Broken-symlink detection — emits [config-broken-symlink] advisory.
#
# NOTE: The hook deduplicates advisories per (session_id, canonical path).
# Each test case uses a unique session_id (case_N_$$) to prevent dedup
# from causing false silent results across unrelated cases.
#
# Usage: bash tests/test_jq_config_empty_dict_advisory.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOOK="$ROOT_DIR/hooks/jq-config-empty-dict-advisory.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# _CASE_N: monotonic counter so each run_case gets a unique default session_id.
_CASE_N=0

# run_case name expectation command [session_id]
#   expectation:
#     "advisory:<marker>" — exit 0, stderr contains <marker>
#     "silent"            — exit 0, stderr empty
#
#   When session_id is omitted a unique id is generated from _CASE_N + PID
#   so dedup state never bleeds between independent test cases.
run_case() {
  local name="$1" expectation="$2" command="$3"
  _CASE_N=$((_CASE_N + 1))
  local sid="${4:-case-${_CASE_N}-$$}"

  local payload
  payload=$(python3 -c '
import json, sys
d = {
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
    "session_id": sys.argv[2],
}
print(json.dumps(d))
' "$command" "$sid")

  local err_file
  err_file=$(mktemp)
  echo "$payload" | python3 "$HOOK" >/dev/null 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    advisory:*)
      local marker="${expectation#advisory:}"
      [ "$rc" -eq 0 ] || ok=0
      case "$err" in
        *"$marker"*) ;;
        *) ok=0 ;;
      esac
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
    echo "FAIL  [$name] expectation=$expectation rc=$rc stderr=${err:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# Setup: temp directory for fixture files
# ---------------------------------------------------------------------------

TMPDIR_TEST=$(mktemp -d)
trap 'rm -rf "$TMPDIR_TEST"' EXIT

# Create fixture files
mkdir -p "$TMPDIR_TEST/.claude"

EMPTY_CLAUDE_JSON="$TMPDIR_TEST/.claude/settings.json"
touch "$EMPTY_CLAUDE_JSON"                            # empty (size 0)

VALID_SETTINGS="$TMPDIR_TEST/settings.json"
echo '{"theme":"dark"}' > "$VALID_SETTINGS"          # valid JSON, repo-root name

INVALID_CLAUDE_JSON="$TMPDIR_TEST/.claude/invalid.json"
printf '{"bad": json' > "$INVALID_CLAUDE_JSON"        # invalid JSON

VALID_CLAUDE="$TMPDIR_TEST/.claude/valid.json"
echo '{"ok":true}' > "$VALID_CLAUDE"                 # valid JSON under .claude/

EMPTY_CLAUDE2="$TMPDIR_TEST/.claude/settings2.json"
touch "$EMPTY_CLAUDE2"                                # second empty config file

EMPTY_CLAUDE_N="$TMPDIR_TEST/.claude/settings-n.json"
touch "$EMPTY_CLAUDE_N"                               # config path with -n in filename

# ---------------------------------------------------------------------------
# === Baseline: existing advisory paths still work ===
# ---------------------------------------------------------------------------

run_case "empty config file emits [config-empty]" \
  "advisory:[config-empty]" \
  "jq '.' $EMPTY_CLAUDE_JSON"

run_case "valid config file is silent" \
  "silent" \
  "jq '.' $VALID_SETTINGS"

run_case "invalid JSON config emits [config-invalid]" \
  "advisory:[config-invalid]" \
  "jq '.' $INVALID_CLAUDE_JSON"

run_case "non-config path is silent" \
  "silent" \
  "jq '.' /tmp/other.json"

# ---------------------------------------------------------------------------
# === Item 1: --arg/--argjson/--slurpfile/--rawfile 2-token skip ===
#
# Each of these flags consumes TWO following tokens (name + value/file).
# The scanner increments i by 3: flag + name + value.
# The config path that comes *after* all flag operands must still be detected.
# ---------------------------------------------------------------------------

run_case "--arg name value: config path after is detected" \
  "advisory:[config-empty]" \
  "jq --arg foo bar '.' $EMPTY_CLAUDE_JSON"

run_case "--argjson name value: config path after is detected" \
  "advisory:[config-empty]" \
  "jq --argjson limit 10 '.' $EMPTY_CLAUDE_JSON"

# --slurpfile name file: 'file' here is /tmp/data.json (not a config path),
# and the config path appears after as the main input file.
run_case "--slurpfile name file: config path after is detected" \
  "advisory:[config-empty]" \
  "jq --slurpfile data /tmp/data.json '.' $EMPTY_CLAUDE_JSON"

run_case "--rawfile name file: config path after is detected" \
  "advisory:[config-empty]" \
  "jq --rawfile tmpl /tmp/template.txt '.' $EMPTY_CLAUDE_JSON"

# Regression: config path must NOT be swallowed as the value operand of --arg.
# With correct 2-token skip (i+=3), the config path is left as a positional.
run_case "--arg x y: config path is the file arg, not the value" \
  "advisory:[config-empty]" \
  "jq --arg x y '.' $EMPTY_CLAUDE_JSON"

# Multiple --arg pairs before the config path
run_case "two --arg pairs: config path after both is detected" \
  "advisory:[config-empty]" \
  "jq --arg a 1 --arg b 2 '.' $EMPTY_CLAUDE_JSON"

# ---------------------------------------------------------------------------
# === Item 2: jq -n (null input) short-circuit ===
# jq -n reads no files — hook must be silent; the early-return fires before
# the path-scanning loop is entered.
# ---------------------------------------------------------------------------

run_case "jq -n is silent (null input, no file read)" \
  "silent" \
  "jq -n 'null'"

# Even when a config-path token appears after the filter in -n mode, it is a
# program argument to the jq program, not a file input. Hook must be silent.
run_case "jq -n with config path token is silent" \
  "silent" \
  "jq -n '{a: 1}' $EMPTY_CLAUDE_JSON"

run_case "jq --null-input is silent (long form)" \
  "silent" \
  "jq --null-input '{b: 2}'"

# -n combined with other flags — still silent
run_case "jq -r -n is silent" \
  "silent" \
  "jq -r -n '.foo'"

# Combined short flags containing n: -rn / -nr — jq reads no files.
run_case "jq -rn combined flag is silent" \
  "silent" \
  "jq -rn '{}' $EMPTY_CLAUDE_JSON"

run_case "jq -nr combined flag is silent" \
  "silent" \
  "jq -nr '{}' $EMPTY_CLAUDE_JSON"

# --arg name -n: the -n is the *value* of --arg, not the null-input flag.
# Hook must NOT short-circuit; the config path after the filter is a real file.
run_case "--arg name -n: -n as value operand does not suppress advisory" \
  "advisory:[config-empty]" \
  "jq --arg myvar -n '.' $EMPTY_CLAUDE_JSON"

# command substitution with jq -n: jq reads no file in null-input mode,
# so the hook must be silent even when a config path appears in the subst text.
run_case "command substitution jq -n is silent" \
  "silent" \
  "x=\$(jq -n '{}' $EMPTY_CLAUDE_JSON)"

run_case "command substitution jq -rn is silent" \
  "silent" \
  "x=\$(jq -rn '{}' $EMPTY_CLAUDE_JSON)"

run_case "command substitution jq --null-input is silent" \
  "silent" \
  "x=\$(jq --null-input '{}' $EMPTY_CLAUDE_JSON)"

# Normal command substitution with config path: advisory should still fire.
run_case "command substitution normal jq: advisory fires" \
  "advisory:[config-empty]" \
  "x=\$(jq '.' $EMPTY_CLAUDE_JSON)"

# command substitution --arg name -n: -n is value operand, advisory must fire.
run_case "command substitution --arg name -n: advisory fires" \
  "advisory:[config-empty]" \
  "x=\$(jq --arg myvar -n '.' $EMPTY_CLAUDE_JSON)"

# config path whose filename contains -n: must NOT be treated as null-input flag.
run_case "command substitution: config path with -n in name fires advisory" \
  "advisory:[config-empty]" \
  "x=\$(jq '.' $EMPTY_CLAUDE_N)"

# ---------------------------------------------------------------------------
# === Item 3: multi-file (jq '.' a.json b.json) — each path checked ===
# The filter is the first positional; every subsequent positional is a file.
# Each file in the list is checked independently.
# ---------------------------------------------------------------------------

# Both config paths in scope and both empty → advisory fires (at least one)
run_case "multi-file: both empty config paths trigger advisory" \
  "advisory:[config-empty]" \
  "jq '.' $EMPTY_CLAUDE_JSON $EMPTY_CLAUDE2"

# One non-config path followed by an empty config path → advisory fires
run_case "multi-file: non-config + empty config → advisory fires" \
  "advisory:[config-empty]" \
  "jq '.' /tmp/unrelated.json $EMPTY_CLAUDE_JSON"

# Empty config path followed by a non-config path → advisory fires
run_case "multi-file: empty config + non-config → advisory fires" \
  "advisory:[config-empty]" \
  "jq '.' $EMPTY_CLAUDE_JSON /tmp/unrelated.json"

# Valid config + empty config → advisory fires for the empty one
run_case "multi-file: valid + empty → advisory fires for the empty one" \
  "advisory:[config-empty]" \
  "jq '.' $VALID_CLAUDE $EMPTY_CLAUDE_JSON"

# Two valid config paths → silent
run_case "multi-file: both valid config paths are silent" \
  "silent" \
  "jq '.' $VALID_CLAUDE $VALID_SETTINGS"

# ---------------------------------------------------------------------------
# === Item 4: jq PATH-missing fail-open ===
# When jq is not on PATH, _check_file catches FileNotFoundError from
# subprocess.run(["jq", ...]) and returns None — hook must exit 0 silently.
# We simulate this by running the hook with PATH set to an empty directory.
# ---------------------------------------------------------------------------

EMPTY_BIN_DIR=$(mktemp -d)
# Resolve the real python3 binary (not a pyenv shim) so it remains callable
# after we strip PATH down to EMPTY_BIN_DIR (which removes the shim layer).
PYTHON3_BIN="$(python3 -c 'import sys; print(sys.executable)')"

# Use the valid config file here, not the empty one: _check_file short-circuits
# on size==0 before calling jq, so PATH-missing would never be exercised with
# an empty file. The valid JSON file forces _check_file to reach subprocess.run
# where FileNotFoundError is caught and fail-open (silent) is exercised.
jq_missing_payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": "jq \".\" " + sys.argv[1]},
    "session_id": "jq-missing-" + sys.argv[2],
}))
' "$VALID_CLAUDE" "$$")

jq_missing_err=$(mktemp)
# Use PATH containing only EMPTY_BIN_DIR so `jq` cannot be found.
# Call python3 via its resolved absolute path so the shim layer is bypassed.
echo "$jq_missing_payload" | \
  env PATH="$EMPTY_BIN_DIR" \
    "$PYTHON3_BIN" "$HOOK" >/dev/null 2>"$jq_missing_err"
jq_missing_rc=$?
jq_missing_err_content=$(cat "$jq_missing_err")
rm -f "$jq_missing_err"
rmdir "$EMPTY_BIN_DIR"

if [ "$jq_missing_rc" -eq 0 ] && [ -z "$jq_missing_err_content" ]; then
  echo "PASS  [jq PATH-missing: fail-open — silent exit 0]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [jq PATH-missing: fail-open] rc=$jq_missing_rc stderr=${jq_missing_err_content:-<empty>}"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("jq PATH-missing: fail-open — silent exit 0")
fi

# ---------------------------------------------------------------------------
# === Item 5: broken-symlink detection ===
# A dangling symlink (link exists, target missing) should emit
# [config-broken-symlink] rather than being silently skipped as "missing".
# Decision: same advisory flow as config-empty/config-invalid (no new branch).
# ---------------------------------------------------------------------------

BROKEN_LINK="$TMPDIR_TEST/.claude/broken.json"
NONEXISTENT_TARGET="$TMPDIR_TEST/.claude/does_not_exist_target.json"
ln -s "$NONEXISTENT_TARGET" "$BROKEN_LINK"

run_case "broken symlink emits [config-broken-symlink] advisory" \
  "advisory:[config-broken-symlink]" \
  "jq '.' $BROKEN_LINK"

# Broken symlink must NOT emit [config-empty] — it is a distinct advisory code
broken_payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": "jq \".\" " + sys.argv[1]},
    "session_id": "broken-not-empty-" + sys.argv[2],
}))
' "$BROKEN_LINK" "$$")

broken_err=$(mktemp)
echo "$broken_payload" | python3 "$HOOK" >/dev/null 2>"$broken_err"
broken_rc=$?
broken_err_content=$(cat "$broken_err")
rm -f "$broken_err"

bsym_ok=1
[ "$broken_rc" -eq 0 ] || bsym_ok=0
case "$broken_err_content" in
  *"[config-empty]"*) bsym_ok=0 ;;
esac
if [ "$bsym_ok" -eq 1 ]; then
  echo "PASS  [broken-symlink advisory is distinct from [config-empty]]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [broken-symlink advisory is distinct from [config-empty]] stderr=${broken_err_content:-<empty>}"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("broken-symlink advisory is distinct from [config-empty]")
fi

# Genuinely missing file (no symlink, no file) → silent
run_case "genuinely missing config file is silent" \
  "silent" \
  "jq '.' $TMPDIR_TEST/.claude/truly_absent.json"

# ---------------------------------------------------------------------------
# === Fail-open / infrastructure cases ===
# ---------------------------------------------------------------------------

# Non-Bash tool_name → silent
infra_payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Read",
    "tool_input": {"command": "jq \".\" ~/.claude/settings.json"},
    "session_id": "infra-read-$$",
}))' 2>/dev/null)
infra_err=$(mktemp)
echo "$infra_payload" | python3 "$HOOK" >/dev/null 2>"$infra_err"
infra_rc=$?
infra_err_content=$(cat "$infra_err")
rm -f "$infra_err"
if [ "$infra_rc" -eq 0 ] && [ -z "$infra_err_content" ]; then
  echo "PASS  [non-Bash tool_name passthrough is silent]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [non-Bash tool_name passthrough] rc=$infra_rc stderr=${infra_err_content:-<empty>}"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("non-Bash tool_name passthrough is silent")
fi

# Malformed JSON stdin → silent (fail-open)
malformed_err=$(mktemp)
echo "not-valid-json" | python3 "$HOOK" >/dev/null 2>"$malformed_err"
malformed_rc=$?
malformed_err_content=$(cat "$malformed_err")
rm -f "$malformed_err"
if [ "$malformed_rc" -eq 0 ] && [ -z "$malformed_err_content" ]; then
  echo "PASS  [malformed JSON stdin is silent (fail-open)]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [malformed JSON stdin is silent] rc=$malformed_rc stderr=${malformed_err_content:-<empty>}"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("malformed JSON stdin is silent (fail-open)")
fi

run_case "empty command is silent" \
  "silent" \
  ""

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
fi

[ "$FAIL" -eq 0 ]
