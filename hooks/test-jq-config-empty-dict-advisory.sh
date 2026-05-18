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
SID_C1="test-c1-subst-$$"
SID_C2A="test-c2a-eflag-$$"
SID_C2B="test-c2b-sortkeys-$$"
SID_M3A="test-m3a-$$"
SID_M3B="test-m3b-$$"
SID_M4A="test-m4a-pipe-$$"
SID_M4B="test-m4b-redirect-$$"

mkdir -p "$TMP_DIR/.claude"

# --- Case 1: empty file -------------------------------------------------------

EMPTY_JSON="$TMP_DIR/.claude/settings.json"
: > "$EMPTY_JSON"

run_case "empty config file" \
  "advisory:[config-empty]" \
  "jq '.' $EMPTY_JSON" \
  "$SID_EMPTY"

# --- Case 2: invalid JSON file ------------------------------------------------

INVALID_JSON="$TMP_DIR/.claude/hooks.json"
printf '{not valid json' > "$INVALID_JSON"

run_case "invalid JSON config file" \
  "advisory:[config-invalid]" \
  "jq '.hooks' $INVALID_JSON" \
  "$SID_INVALID"

# --- Case 3: valid JSON file --------------------------------------------------

VALID_JSON="$TMP_DIR/.claude/valid.json"
printf '{"key": "value"}' > "$VALID_JSON"

run_case "valid JSON config file" \
  "silent" \
  "jq '.key' $VALID_JSON" \
  "$SID_VALID"

# --- Case 4 (C1): command substitution — threshold=$(jq -r '...' file) --------
# Issue body originating pattern. safe_tokenize splits threshold=$(jq into one
# token; _coalesce_subst_runs joins the $(…) run; _scan_subst_for_config_paths
# then extracts the path from the coalesced SUBST_RUN token.

C1_JSON="$TMP_DIR/.claude/c1.json"
: > "$C1_JSON"

run_case "C1 command substitution empty file" \
  "advisory:[config-empty]" \
  "threshold=\$(jq -r '.ambiguity_threshold // 0.2' $C1_JSON)" \
  "$SID_C1"

# --- Case 5 (C2a): jq -e flag (boolean, must not consume filter as value) -----

C2A_JSON="$TMP_DIR/.claude/c2a.json"
: > "$C2A_JSON"

run_case "C2a -e flag (boolean) empty file" \
  "advisory:[config-empty]" \
  "jq -e '.theme' $C2A_JSON" \
  "$SID_C2A"

# --- Case 6 (C2b): jq --sort-keys flag (boolean, must not consume value) ------

C2B_JSON="$TMP_DIR/.claude/c2b.json"
: > "$C2B_JSON"

run_case "C2b --sort-keys flag (boolean) empty file" \
  "advisory:[config-empty]" \
  "jq --sort-keys '.foo' $C2B_JSON" \
  "$SID_C2B"

# --- Case 7 (M3): dedup — advisory-emitting keys are deduplicated, ---------
# but a file not yet advisory-emitting is NOT blocked from future checks.
# Sub-case A: first call on valid file → silent, key NOT stored.
# Sub-case B: same session, same file now empty → advisory must fire.
# We simulate by sharing a session_id across A and B.

M3_JSON="$TMP_DIR/.claude/m3.json"
printf '{"ok": true}' > "$M3_JSON"

run_case "M3a valid file first call (no advisory stored)" \
  "silent" \
  "jq '.' $M3_JSON" \
  "$SID_M3A"

# Now make the file empty and re-check with same session_id.
: > "$M3_JSON"

run_case "M3b same file now empty — advisory must fire" \
  "advisory:[config-empty]" \
  "jq '.' $M3_JSON" \
  "$SID_M3A"

# --- Case 8 (M4a): cat config | jq '.' — pipe correlation --------------------

M4A_JSON="$TMP_DIR/.claude/m4a.json"
: > "$M4A_JSON"

run_case "M4a piped cat config | jq empty file" \
  "advisory:[config-empty]" \
  "cat $M4A_JSON | jq '.'" \
  "$SID_M4A"

# --- Case 9 (M4b): jq '.' < config — stdin redirect --------------------------

M4B_JSON="$TMP_DIR/.claude/m4b.json"
: > "$M4B_JSON"

run_case "M4b stdin redirect jq '.' < config empty file" \
  "advisory:[config-empty]" \
  "jq '.' < $M4B_JSON" \
  "$SID_M4B"

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
