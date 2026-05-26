#!/bin/bash
# test-block-gh-state-all.sh — coverage for block-gh-state-all.sh
#
# Synthesizes Claude Code PreToolUse hook payloads and asserts:
#   block → exit 2 + stderr non-empty
#   pass  → exit 0 + stderr empty
#
# Usage: bash hooks/test-block-gh-state-all.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/block-gh-state-all/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expected tool_name command
#   expected: "block" (exit 2, stderr non-empty) | "pass" (exit 0, stderr empty)
run_case() {
  local name="$1" expected="$2" tool_name="$3" command="$4"

  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": sys.argv[1],
    "tool_input": {"command": sys.argv[2]},
}))' "$tool_name" "$command")

  local err_file
  err_file=$(mktemp)
  echo "$payload" | "$HOOK" >/dev/null 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"

  local ok=1
  case "$expected" in
    block)
      [ "$rc" -eq 2 ] || ok=0
      [ -n "$err" ]   || ok=0
      ;;
    pass)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    *)
      echo "FAIL  [$name] unknown expected: $expected"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected rc=$rc stderr=${err:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# --- BLOCK: gh search subcommands with --state all --------------------------
run_case "block: search issues --state all (space)"   block Bash 'gh search issues "test" --state all'
run_case "block: search prs --state=all (equals)"     block Bash 'gh search prs "x" --state=all'
run_case "block: search repos --limit 1 --state all"  block Bash 'gh search repos foo --limit 1 --state all'
run_case "block: search commits --state all"          block Bash 'gh search commits "fix" --repo owner/repo --state all'
run_case "block: gh --no-pager search issues --state all" block Bash 'gh --no-pager search issues "q" --state all'
run_case "block: gh -R repo search issues --state all"    block Bash 'gh -R owner/repo search issues "q" --state all'
run_case "block: backslash continuation"              block Bash "$(printf 'gh search issues \\\n  --state all')"
run_case "block: env prefix FOO=1 gh search"         block Bash 'FOO=1 gh search issues "q" --state all'
run_case "block: sudo wrapper gh search"             block Bash 'sudo gh search issues "q" --state all'
run_case "block: chained after echo &&"              block Bash 'echo x && gh search issues "q" --state all'

# --- PASS: gh search without --state or with valid states --------------------
run_case "pass: search issues no --state"             pass  Bash 'gh search issues "test"'
run_case "pass: search issues --state open"           pass  Bash 'gh search issues "test" --state open'
run_case "pass: search issues --state closed"         pass  Bash 'gh search issues "test" --state closed'
run_case "pass: search prs --state open"              pass  Bash 'gh search prs "bug" --state open'
run_case "pass: search issues --state all-things"     pass  Bash 'gh search issues "q" --state all-things'

# --- PASS: gh issue/pr list legitimately accept --state all ------------------
run_case "pass: issue list --state all"               pass  Bash 'gh issue list --state all'
run_case "pass: pr list --state all"                  pass  Bash 'gh pr list --state all'
run_case "pass: issue list --state all --repo x"      pass  Bash 'gh issue list --repo owner/repo --state all'

# --- PASS: pattern inside strings / echo / pr body must not trigger ----------
run_case "pass: echo quoting the pattern"             pass  Bash 'echo "Use gh search issues --state open, not --state all"'
run_case "pass: grep containing pattern"              pass  Bash 'cat README.md | grep "gh search issues --state all"'
run_case "pass: gh pr comment body mentioning pattern" pass Bash 'gh pr comment 128 --body "The hook blocks: gh search issues --state all"'
run_case "pass: comment line before real command"     pass  Bash "$(printf '# gh search issues --state all\nls')"
# false-positive regressions: non-gh-search contexts must never block
run_case "pass: gh pr create body describes alternative" pass Bash 'gh pr create --body "documenting that --state all alternative works"'
run_case "pass: git commit message mentions state flag"  pass Bash 'git commit -m "fix: handle --state all rejection"'
run_case "pass: grep with -- pattern sentinel"           pass Bash 'grep -- "--state all" docs.md'
run_case "pass: echo standalone mention"                 pass Bash 'echo "--state all is invalid for gh search"'

# --- PASS: non-Bash tool → exit 0 -------------------------------------------
run_case "pass: tool_name Read"                       pass  Read 'gh search issues --state all'

# --- PASS: malformed stdin → fail-open exit 0 --------------------------------
malformed_test() {
  local name="pass: malformed stdin → exit 0"
  local err_file
  err_file=$(mktemp)
  echo "not-valid-json" | "$HOOK" >/dev/null 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$err_file"
  if [ "$rc" -eq 0 ] && [ -z "$err" ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] rc=$rc stderr=${err:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}
malformed_test

# --- PASS: empty command → exit 0 -------------------------------------------
run_case "pass: empty command"                        pass  Bash ''

# ---------- summary ----------------------------------------------------------
echo ""
echo "Passed: $PASS  Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed tests:"
  for t in "${FAILED_NAMES[@]}"; do
    echo "  - $t"
  done
  exit 1
fi
exit 0
