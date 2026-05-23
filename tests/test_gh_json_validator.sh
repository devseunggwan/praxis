#!/bin/bash
# test_gh_json_validator.sh — coverage for hooks/gh-json-validator.py
#
# 10-case suite covering the full block / pass / bypass / skip / fail-open
# surface documented in issue #403 and docs/hook/gh-json-validator.md.
#
# Payload format: PreToolUse(Bash) JSON with tool_name, tool_input.command,
# and session_id piped directly to hooks/gh-json-validator.py (Python invoked
# directly — not via the .sh wrapper — for deterministic exit-code control).
#
# Usage: bash tests/test_gh_json_validator.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK_PY="$ROOT_DIR/hooks/gh-json-validator.py"

if [ ! -f "$HOOK_PY" ]; then
  echo "FAIL: hook script not found: $HOOK_PY" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# ---------------------------------------------------------------------------
# run_case name expectation command [ENV=VAL ...]
#   expectation:
#     "block:<marker>" — exit 2, stdout contains <marker>
#     "pass"           — exit 0, stdout empty
# ---------------------------------------------------------------------------
run_case() {
  local name="$1" expectation="$2" command="$3"
  shift 3
  # remaining args are ENV=VAL pairs
  local extra_env=("$@")

  local sid
  sid="test-$(date +%s)-$$-$RANDOM"
  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
    "session_id": sys.argv[2],
}))
' "$command" "$sid")

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)

  printf '%s' "$payload" | env "${extra_env[@]}" python3 "$HOOK_PY" >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expectation" in
    pass)
      [ "$rc" -eq 0 ] || ok=0
      ;;
    block:*)
      local marker="${expectation#block:}"
      [ "$rc" -eq 2 ] || ok=0
      case "$out" in
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
    echo "FAIL  [$name] expectation=$expectation rc=$rc stderr=${err:-<empty>} stdout=${out:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# Case 1: Block — invalid field (`--json merged`)
# `merged` is not a valid `gh pr view` JSON field; hook must block (exit 2).
# ---------------------------------------------------------------------------
run_case "block: invalid field --json merged" \
  "block:BLOCKED" \
  "gh pr view 1 --json merged"

# ---------------------------------------------------------------------------
# Case 2: Pass — valid field (`--json state`)
# `state` IS a valid `gh pr view` JSON field; hook must pass (exit 0).
# ---------------------------------------------------------------------------
run_case "pass: valid field --json state" \
  "pass" \
  "gh pr view 1 --json state"

# ---------------------------------------------------------------------------
# Case 3: Pass — multiple valid fields (`--json state,author,title`)
# All three fields are valid; hook must pass.
# ---------------------------------------------------------------------------
run_case "pass: multiple valid fields --json state,author,title" \
  "pass" \
  "gh pr view 1 --json state,author,title"

# ---------------------------------------------------------------------------
# Case 4: Block — mixed valid+invalid (`--json state,merged`)
# One field valid, one invalid; any invalid field triggers block.
# ---------------------------------------------------------------------------
run_case "block: mixed valid+invalid --json state,merged" \
  "block:BLOCKED" \
  "gh pr view 1 --json state,merged"

# ---------------------------------------------------------------------------
# Case 5: Bypass — inline comment marker
# Command contains `# CLAUDE_HOOK_BYPASS=gh-json-validator`; must pass even
# with an invalid field.
# ---------------------------------------------------------------------------
run_case "bypass: inline comment marker" \
  "pass" \
  "gh pr view 1 --json merged  # CLAUDE_HOOK_BYPASS=gh-json-validator"

# ---------------------------------------------------------------------------
# Case 6: Bypass — env var CLAUDE_HOOK_BYPASS_GH_JSON=1
# Must pass even with an invalid field when env var is set.
# ---------------------------------------------------------------------------
run_case "bypass: env var CLAUDE_HOOK_BYPASS_GH_JSON=1" \
  "pass" \
  "gh api repos/owner/repo/pulls/1 --json merged" \
  "CLAUDE_HOOK_BYPASS_GH_JSON=1"

# ---------------------------------------------------------------------------
# Case 7: Skip — `gh api` subcommand is out of scope
# `gh api` follows REST schema; hook must pass unconditionally.
# ---------------------------------------------------------------------------
run_case "skip: gh api subcommand is out of scope" \
  "pass" \
  "gh api repos/owner/repo/pulls/1 --json merged"

# ---------------------------------------------------------------------------
# Case 8: Skip — non-gh Bash command (`ls -la`)
# Hook only intercepts gh commands; plain shell commands must pass.
# ---------------------------------------------------------------------------
run_case "skip: non-gh bash command ls -la" \
  "pass" \
  "ls -la /tmp"

# ---------------------------------------------------------------------------
# Case 9: Fail-open — gh not in PATH
# When gh binary is missing, hook must exit 0 (fail-open, never break session).
# ---------------------------------------------------------------------------
run_case "fail-open: gh not in PATH" \
  "pass" \
  "gh pr view 1 --json merged" \
  "PATH=/usr/bin:/bin"

# ---------------------------------------------------------------------------
# Case 10: Skip — no `--json` flag present
# Command references gh but has no --json; prefilter skips it (exit 0).
# ---------------------------------------------------------------------------
run_case "skip: no --json flag" \
  "pass" \
  "gh pr view 1"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
  exit 1
fi
exit 0
