#!/bin/bash
# test_model_routing_advisory.sh — coverage for
# hooks/advisory-nudge/model-routing-advisory/impl.py
#
# The hook is purely command-string based (no git repo, no transcript). Each
# case feeds a synthesized PreToolUse(Bash) payload and asserts:
#   surface:<marker>  — exit 0, stderr contains <marker>
#   silent            — exit 0, stderr empty
#
# Usage: bash tests/hooks/advisory-nudge/test_model_routing_advisory.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../../" && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/model-routing-advisory/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

record() {
  local name="$1" ok="$2" rc="$3" err="$4" expectation="$5"
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expectation=$expectation rc=$rc stderr=${err:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# run_case name expectation command [env_assign] [raw_stdin]
#   raw_stdin, when set to "raw", sends $command as literal stdin (malformed-JSON
#   / non-Bash cases build their own stdin via the command argument).
run_case() {
  local name="$1" expectation="$2" command="$3" env_assign="${4:-}" mode="${5:-}"
  local payload
  if [ "$mode" = "raw" ]; then
    payload="$command"
  else
    payload=$(python3 -c '
import json, sys
print(json.dumps({"tool_name": "Bash", "tool_input": {"command": sys.argv[1]}}))
' "$command")
  fi

  local err_file rc err
  err_file=$(mktemp)
  if [ -n "$env_assign" ]; then
    printf '%s' "$payload" | env "$env_assign" "$HOOK" >/dev/null 2>"$err_file"
  else
    printf '%s' "$payload" | "$HOOK" >/dev/null 2>"$err_file"
  fi
  rc=$?
  err=$(cat "$err_file"); rm -f "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    surface:*)
      local marker="${expectation#surface:}"
      [ "$rc" -eq 0 ] || ok=0
      case "$err" in *"$marker"*) ;; *) ok=0 ;; esac
      ;;
    *)
      echo "FAIL  [$name] unknown expectation: $expectation"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      ;;
  esac
  record "$name" "$ok" "$rc" "$err" "$expectation"
}

# --- Mismatch → surface -----------------------------------------------------
run_case "opus for find (over-powered)" "surface:over-powered" \
  'cmux new-workspace --command "claude -p '"'"'find all callers of X'"'"' --model opus"'
run_case "haiku for architect (under-powered)" "surface:under-powered" \
  'cmux-delegate --model haiku "architect the new system"'
run_case "opus for cleanup (over-powered)" "surface:over-powered" \
  'claude -p "cleanup stale worktrees" --model opus'
run_case "sonnet for search (over-powered)" "surface:over-powered" \
  'claude -p "search the logs" --model sonnet'
run_case "haiku for implement (under-powered)" "surface:under-powered" \
  'claude -p "implement the parser" --model haiku'
run_case "model-routing marker present" "surface:[model-routing]" \
  'claude -p "find X" --model opus'

# --- opus-precedence when multiple signals co-occur -------------------------
run_case "mixed find+architect implies opus (silent at opus)" "silent" \
  'claude -p "find files then architect the module" --model opus'
run_case "mixed find+architect implies opus (surface at haiku)" "surface:under-powered" \
  'claude -p "find files then architect the module" --model haiku'

# --- Match → silent ---------------------------------------------------------
run_case "sonnet for implement" "silent" 'claude -p "implement feature Y" --model sonnet'
run_case "haiku for search" "silent" 'claude -p "search the logs" --model haiku'
run_case "opus for security" "silent" 'claude -p "security audit of auth" --model opus'
run_case "claude:opus prefix inspected + matches" "silent" \
  'claude -p "design the schema" --model claude:opus'

# --- No comparable tier / no signal → silent --------------------------------
run_case "no --model" "silent" 'claude -p "implement feature Y"'
run_case "non-claude provider" "silent" 'cmux-delegate --model codex:gpt-5 "architect X"'
run_case "gemini provider" "silent" 'cmux-delegate --model gemini:pro "find X"'
run_case "full model id not classified" "silent" 'claude -p "design X" --model claude-opus-4-8'
run_case "model tier but no signal keyword" "silent" 'claude -p "do the thing" --model opus'

# --- Word-boundary collisions must NOT trigger a signal ---------------------
run_case "remove !~ move" "silent" 'claude -p "remove old files" --model opus'
run_case "research !~ search" "silent" 'claude -p "research the approach" --model haiku'
run_case "address !~ add" "silent" 'claude -p "update the address parser" --model opus'
run_case "prefix !~ fix" "silent" 'claude -p "handle the prefix case" --model haiku'
run_case "blocklist !~ list" "silent" 'claude -p "update the blocklist file" --model opus'
run_case "latest !~ test" "silent" 'claude -p "pull the latest data" --model opus'

# --- CLI flag / hyphenated-path / numbered-token must NOT hijack implied tier
# (regression: --add-dir's `add` must not outrank the real `find` signal)
run_case "--add-dir add !~ sonnet (find stays haiku)" "silent" \
  'claude -p "find all callers of X" --model haiku --add-dir /repo/src'
run_case "--add-dir hyphenated path test-utils" "silent" \
  'claude -p "find X" --model haiku --add-dir /repo/test-utils'
run_case "fix2 numbered token !~ fix" "silent" 'claude -p "run fix2 script" --model opus'
run_case "test_v3 underscore token !~ test" "silent" 'claude -p "run test_v3" --model opus'

# --- trailing shell punctuation on the --model value still classifies -------
# (regression: `opus;` must not be swallowed into an unrecognized value)
run_case "opus; semicolon still classifies" "surface:over-powered" \
  'claude -p "cleanup files" --model opus; echo done'
run_case "opus,sonnet comma keeps leading" "surface:over-powered" \
  'claude -p "cleanup files" --model opus,sonnet'

# --- bare debug is NOT an opus signal (only debug.*race is, per policy) ------
run_case "bare debug at sonnet stays silent" "silent" \
  'claude -p "debug the parser failure" --model sonnet'

# --- documented limitations (pin current behavior) --------------------------
# Negation is not parsed: a negated keyword still fires (known false-surface).
run_case "negated design still fires (documented)" "surface:under-powered" \
  'claude -p "find callers; do not design anything" --model haiku'
# A literal --model in the prompt is read as the chosen tier (first-match misread):
# chosen=opus (from the prompt), implied=sonnet (from `fix`) → over-powered.
run_case "prompt-literal --model misread (documented)" "surface:over-powered" \
  'claude -p "fix the --model opus docs" --model sonnet'

# --- opt-out / bypass -------------------------------------------------------
run_case "ack marker" "silent" 'claude -p "find X" --model opus # [model-routing-ack]'
run_case "env bypass" "silent" 'claude -p "find X" --model opus' "PRAXIS_SKIP_MODEL_ROUTING=1"

# --- fail-open --------------------------------------------------------------
run_case "non-Bash tool" "silent" '{"tool_name":"Edit","tool_input":{"file_path":"/tmp/x"}}' "" raw
run_case "malformed JSON stdin" "silent" 'not json at all' "" raw
run_case "empty command" "silent" ''

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
