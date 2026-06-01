#!/usr/bin/env bash
# test_count_assertion_verify.sh — coverage for hooks/advisory-nudge/count-assertion-verify/impl.py
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   advisory → exit 0 + stderr contains ADVISORY_TAG
#   pass     → exit 0 + stderr empty
#
# Usage: bash tests/test_count_assertion_verify.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/count-assertion-verify/impl.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi
if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

ADVISORY_TAG="[count-assertion-verify]"

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expectation bash_command
#   expectation:
#     "advisory" — exit 0 + stderr contains ADVISORY_TAG
#     "pass"     — exit 0 + stderr empty
run_case() {
  local name="$1" expectation="$2" bash_cmd="$3"

  local payload
  payload=$(python3 -c "
import json, sys
payload = {
    'session_id': 'test-session',
    'tool_name': 'Bash',
    'tool_input': {
        'command': sys.argv[1],
        'description': 'test',
    },
    'cwd': '/tmp',
}
print(json.dumps(payload))
" "$bash_cmd")

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)

  printf '%s' "$payload" | "$HOOK" >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expectation" in
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      case "$err" in
        *"$ADVISORY_TAG"*) ;;
        *) ok=0 ;;
      esac
      ;;
    pass)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
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
# Advisory cases — should fire
# ---------------------------------------------------------------------------

# Original incident: BRE alternation with -c
run_case "bre_alternation_basic" advisory \
  "grep -c '^run_case\|^run_test' tests/test_verify_commit_flag_override.sh"

# BRE alternation, longer pattern
run_case "bre_alternation_longer" advisory \
  "grep -c 'foo\|bar\|baz' file.txt"

# ERE alternation with -E -c (separate flags)
run_case "ere_alternation_separate_flags" advisory \
  "grep -E -c 'pat1|pat2' file.sh"

# ERE alternation with combined -Ec flags
run_case "ere_alternation_combined_Ec" advisory \
  "grep -Ec 'pat1|pat2' file.sh"

# ERE alternation with combined -cE flags
run_case "ere_alternation_combined_cE" advisory \
  "grep -cE 'alpha|beta' config.yaml"

# --count --extended-regexp (long flags)
run_case "long_flags_ere" advisory \
  "grep --count --extended-regexp 'foo|bar' README.md"

# --count with BRE alternation
run_case "long_count_bre" advisory \
  "grep --count 'a\|b' test.txt"

# PCRE mode -P with -c
run_case "pcre_alternation" advisory \
  "grep -Pc 'cat|dog' animals.txt"

# Combined -cP
run_case "ere_combined_cP" advisory \
  "grep -cP 'foo|bar' data.log"

# Advisory still fires in a compound command (the grep is not the first cmd)
run_case "compound_cmd_and" advisory \
  "ls /tmp && grep -c 'a\|b' file.txt"

run_case "compound_cmd_semicolon" advisory \
  "echo start; grep -Ec 'x|y' file.sh; echo done"

# Advisory fires with -e PATTERN flag (explicit pattern flag)
run_case "explicit_e_flag" advisory \
  "grep -c -e 'pat1\|pat2' file.txt"

# Regression (issue #516 defect2): alternation lives only in the SECOND -e
# pattern. The old code inspected patterns[0] alone and missed it.
run_case "ere_alternation_second_e_flag" advisory \
  "grep -Ec -e 'a' -e 'b|c' file.txt"

# BRE variant: first -e plain, second -e carries the \| alternation.
run_case "bre_alternation_second_e_flag" advisory \
  "grep -c -e 'a' -e 'b\|c' file.txt"

# ---------------------------------------------------------------------------
# Pass cases — should NOT fire
# ---------------------------------------------------------------------------

# Single pattern (no alternation) — primary false-positive guard
run_case "single_pattern_no_alternation" pass \
  "grep -c 'run_case' file.sh"

# ERE mode but no alternation
run_case "ere_no_alternation" pass \
  "grep -Ec 'run_case' file.sh"

# grep without -c (count) flag — counting not asserted
run_case "grep_no_count_flag" pass \
  "grep 'pat1\|pat2' file.txt"

# grep -l (list files) — not a count assertion
run_case "grep_list_files" pass \
  "grep -l 'foo\|bar' *.sh"

# wc -l — not a grep, no alternation concern here
run_case "wc_l_command" pass \
  "wc -l file.txt"

# git grep (different command family)
run_case "git_grep" pass \
  "git grep -c 'foo\|bar'"

# No grep at all
run_case "no_grep_command" pass \
  "ls -la /tmp"

# Opt-out marker present
run_case "opt_out_marker" pass \
  "grep -c 'pat1\|pat2' file.txt # count-verified"

# Opt-out marker in compound command
run_case "opt_out_compound" pass \
  "echo test && grep -Ec 'a|b' file.sh # count-verified"

# BRE: single escaped | that is NOT a BRE alternation (\| would be alternation,
# but we need to check a bare | in BRE is literal, not alternation).
# In BRE, bare `|` is a literal pipe (not alternation).
run_case "bre_bare_pipe_literal" pass \
  "grep -c 'a|b' file.txt"

# Different tool (not Bash)
run_case "not_bash_tool" pass \
  "cat /tmp/test.txt"

# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

# Malformed JSON → exit 0, silent pass
run_case_raw_malformed() {
  local name="malformed_json_stdin"
  local out err
  out=$(printf '{not-valid-json' | "$HOOK" 2>/dev/null)
  local rc=$?
  err=""
  if [ "$rc" -eq 0 ] && [ -z "$err" ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] rc=$rc"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}
run_case_raw_malformed

# Empty command string → pass
run_case "empty_command" pass \
  ""

# ---------------------------------------------------------------------------
# @fail_open structural assertion
# ---------------------------------------------------------------------------

# main() opts into the shared @fail_open guard; verify the decorator is
# applied (fail-open behaviour itself is covered in tests/test_hook_runtime.sh).
_uncaught_out=$(python3 - << PYEOF 2>&1
import sys, importlib.util, io
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
if getattr(mod.main, "__wrapped__", None) is None:
    sys.stderr.write("main not wrapped by @fail_open\n"); sys.exit(1)
PYEOF
)
_uncaught_rc=$?
if [ "$_uncaught_rc" -eq 0 ] && [ -z "$_uncaught_out" ]; then
  echo "  PASS  main() is wrapped by the shared @fail_open guard (exit 0, no stderr)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  main() not wrapped by @fail_open (rc=$_uncaught_rc, out=$(echo "$_uncaught_out" | head -c 200))"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("main() is wrapped by the shared @fail_open guard")
fi

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
fi

[ "$FAIL" -eq 0 ]
