#!/bin/bash
# test_inspection_chain_advisory.sh — coverage for the &&-chain inspection
# advisory (issue #469).
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   advisory → exit 0 + stderr contains the advisory marker
#   silent   → exit 0 + stderr empty
#
# Usage: bash tests/hooks/advisory-nudge/test_inspection_chain_advisory.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/inspection-chain-advisory/impl.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

run_case() {
  local name="$1" expected="$2" command="$3"

  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
}))' "$command")

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)
  echo "$payload" | python3 "$HOOK" >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expected" in
    advisory)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      echo "$err" | grep -q "\[inspection-chain-advisory\]" || ok=0
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

# === ADVISORY — pure-inspection &&-chains =================================

run_case "grep && grep (both read)" \
  advisory \
  "grep alpha foo.txt && grep beta bar.txt"

run_case "grep && grep && grep (three reads)" \
  advisory \
  "grep a foo && grep b foo && grep c foo"

run_case "find && wc (file enumeration + count)" \
  advisory \
  "find . -name '*.py' && wc -l *.py"

run_case "ls && cat (list + read)" \
  advisory \
  "ls /tmp && cat /tmp/foo"

run_case "git log && git status (inspection subcmds)" \
  advisory \
  "git log --oneline && git status"

run_case "git rev-parse && git log" \
  advisory \
  "git rev-parse HEAD && git log -1"

run_case "gh issue view && gh pr view" \
  advisory \
  "gh issue view 1 && gh pr view 2"

run_case "gh search && gh list" \
  advisory \
  "gh search issues foo && gh issue list"

run_case "git -C /tmp log && git -C /tmp status (with -C global)" \
  advisory \
  "git -C /tmp log -1 && git -C /tmp status"

run_case "test && grep (POSIX cond + read)" \
  advisory \
  "test -f foo && grep X foo"

run_case "echo && grep (echo cannot silent-cascade)" \
  silent \
  "echo running && grep X foo"

run_case "printf && true (constant-exit chain)" \
  silent \
  "printf hi && true"

run_case "echo && echo && echo (all constant-exit)" \
  silent \
  "echo step1 && echo step2 && echo step3"

# === SILENT — chain contains a state-changing command =====================

run_case "mkdir && ls (make then verify)" \
  silent \
  "mkdir /tmp/newdir && ls /tmp/newdir"

run_case "cd && grep (cwd mutation)" \
  silent \
  "cd /tmp && grep X foo"

run_case "cp && diff (copy then compare)" \
  silent \
  "cp a b && diff a b"

run_case "mv && ls (move then verify)" \
  silent \
  "mv a b && ls b"

run_case "rm && ls (delete then verify)" \
  silent \
  "rm tmpfile && ls tmpfile"

run_case "git commit && git push (mutation subcmds)" \
  silent \
  "git commit -m x && git push"

run_case "gh pr create && gh pr view (mutation + read)" \
  silent \
  "gh pr create --title X && gh pr view"

run_case "git checkout && git log (mutation + read)" \
  silent \
  "git checkout main && git log -1"

run_case "touch && cat (touch is state)" \
  silent \
  "touch foo && cat foo"

run_case "curl -o file && cat file (download + read)" \
  silent \
  "curl -o /tmp/x https://example.com && cat /tmp/x"

# === SILENT — mutation form of inspection tools ===========================

run_case "find -delete && ls (-delete is mutation)" \
  silent \
  "find /tmp -name '*.tmp' -delete && ls /tmp"

run_case "sed -i && cat (sed -i is mutation)" \
  silent \
  "sed -i 's/a/b/' foo && cat foo"

run_case "sed --in-place && cat" \
  silent \
  "sed --in-place 's/a/b/' foo && cat foo"

run_case "sed -i.bak && cat (BSD glued suffix)" \
  silent \
  "sed -i.bak 's/a/b/' foo && cat foo"

run_case "sed --in-place=.bak && cat (GNU with extension)" \
  silent \
  "sed --in-place=.bak 's/a/b/' foo && cat foo"

run_case "find -exec rm && ls (exec callout to mutator)" \
  silent \
  "find /tmp -name '*.tmp' -exec rm {} + && ls /tmp"

run_case "find -execdir rm && ls" \
  silent \
  "find /tmp -name '*.tmp' -execdir rm {} + && ls /tmp"

run_case "awk -i inplace && cat (gawk in-place)" \
  silent \
  "awk -i inplace '{print toupper(\$0)}' foo && cat foo"

run_case "gh pr ready && gh pr view (ready is mutation)" \
  silent \
  "gh pr ready 1 && gh pr view 1"

# === SILENT — non-&& separators ===========================================

run_case "grep || echo (|| is fallback)" \
  silent \
  "grep X foo || echo missing"

run_case "grep ; grep (; is unconditional)" \
  silent \
  "grep X foo ; grep Y foo"

run_case "grep | sort | uniq (| is pipe)" \
  silent \
  "grep X foo | sort | uniq"

run_case "grep & grep (& is backgrounding)" \
  silent \
  "grep X foo & grep Y foo"

run_case "grep |& grep (|& is pipe-with-stderr)" \
  silent \
  "grep X foo |& grep Y bar"

# === SILENT — single-command (no chain) ===================================

run_case "single grep" \
  silent \
  "grep X foo"

run_case "single git log" \
  silent \
  "git log --oneline -10"

# === SILENT — unknown commands not in inspection allowlist ================

run_case "mycustomtool && othertool (unknown bins)" \
  silent \
  "mycustomtool a && othertool b"

# === Fail-open infrastructure =============================================

# non-Bash tool → exit 0 silent
run_case_raw_payload() {
  local name="$1" expected="$2" payload="$3"

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)
  echo "$payload" | python3 "$HOOK" >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file")
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expected" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      [ -z "$err" ]   || ok=0
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

run_case_raw_payload "non-Bash tool passes silently" silent \
  '{"tool_name": "Edit", "tool_input": {"file_path": "/tmp/x"}}'

run_case_raw_payload "malformed JSON fails open silently" silent \
  'not valid json {{{'

run_case_raw_payload "empty Bash command silent" silent \
  '{"tool_name": "Bash", "tool_input": {"command": ""}}'

run_case_raw_payload "whitespace-only Bash command silent" silent \
  '{"tool_name": "Bash", "tool_input": {"command": "   "}}'

# === Summary ==============================================================

echo
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
