#!/bin/bash
# test-cli-flag-incompat-advisory.sh — coverage for the advisory hook.
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   advisory → exit 0 + stderr contains the advisory marker
#   silent   → exit 0 + stderr empty
#
# Usage: bash hooks/test-cli-flag-incompat-advisory.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="$SCRIPT_DIR/cli-flag-incompat-advisory.sh"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expected command
#   expected: "advisory"  (exit 0, stderr contains the marker substring)
#             "silent"    (exit 0, stderr empty)
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
  echo "$payload" | "$HOOK" >"$out_file" 2>"$err_file"
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
      echo "$err" | grep -q "\[cli-flag-incompat-advisory\]" || ok=0
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

# === ADVISORY paths (git merge-tree mode conflict) =========================

run_case "merge-tree --name-only with 3 positionals" \
  advisory \
  "git merge-tree --name-only abc123 HEAD origin/main"

run_case "merge-tree --name-only chained via && (3 positionals)" \
  advisory \
  "git merge-tree --name-only A B C && echo done"

run_case "merge-tree --write-tree with 3 positionals" \
  advisory \
  "git merge-tree --write-tree A B C"

run_case "merge-tree --name-only with -C global flag (3 positionals)" \
  advisory \
  "git -C /tmp merge-tree --name-only A B C"

# === ADVISORY paths (kubectl deprecated flag) ==============================

run_case "kubectl --use-protocol-buffers" \
  advisory \
  "kubectl --use-protocol-buffers get pods"

run_case "kubectl --use-protocol-buffers=true" \
  advisory \
  "kubectl --use-protocol-buffers=true get pods"

# === SILENT paths (correct merge-tree usage) ===============================

run_case "merge-tree --name-only with 2 positionals (correct modern usage)" \
  silent \
  "git merge-tree --name-only HEAD origin/main"

run_case "merge-tree --name-only with 1 positional" \
  silent \
  "git merge-tree --name-only origin/main"

run_case "merge-tree with no modern flag (legacy 3-arg form)" \
  silent \
  "git merge-tree abc123 HEAD origin/main"

run_case "merge-tree --name-only with --merge-base=A (equals form)" \
  silent \
  "git merge-tree --merge-base=A --name-only HEAD origin/main"

# Regression — codex review round 1 F1: --merge-base in space-separated form
# (`--merge-base A`) was counting `A` as a 3rd positional and falsely
# tripping the advisory. The merge-tree flag-with-arg set must include
# --merge-base / -X / --strategy-option.
run_case "merge-tree --name-only with --merge-base A (space form)" \
  silent \
  "git merge-tree --merge-base A --name-only HEAD origin/main"

run_case "merge-tree --name-only with -X strategy-opt + 2 positionals" \
  silent \
  "git merge-tree -X ours --name-only HEAD origin/main"

run_case "merge-tree --name-only with --strategy-option (space) + 2 positionals" \
  silent \
  "git merge-tree --strategy-option ours --name-only HEAD origin/main"

# === SILENT paths (unrelated commands) =====================================

run_case "git status (different subcommand)" \
  silent \
  "git status"

run_case "git log (different subcommand)" \
  silent \
  "git log --oneline -10"

run_case "kubectl get pods (no deprecated flag)" \
  silent \
  "kubectl get pods -n default"

run_case "non-git non-kubectl command" \
  silent \
  "echo merge-tree --name-only A B C"

run_case "empty command" \
  silent \
  ""

run_case "merge-tree mention in echo" \
  silent \
  "echo 'use git merge-tree --name-only A B C'"

# === Infrastructure passthrough ============================================

# Non-Bash tool → silent pass.
ipayload=$(python3 -c '
import json
print(json.dumps({
    "tool_name": "Write",
    "tool_input": {"command": "any"},
}))')
i_out=$(mktemp); i_err=$(mktemp)
echo "$ipayload" | "$HOOK" >"$i_out" 2>"$i_err"
i_rc=$?
i_out_v=$(cat "$i_out"); i_err_v=$(cat "$i_err")
rm -f "$i_out" "$i_err"
if [ "$i_rc" -eq 0 ] && [ -z "$i_out_v" ] && [ -z "$i_err_v" ]; then
  echo "PASS  [non-Bash passthrough]"; PASS=$((PASS + 1))
else
  echo "FAIL  [non-Bash passthrough] rc=$i_rc out='$i_out_v' err='$i_err_v'"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("non-Bash passthrough")
fi

# Malformed JSON → fail-open.
mpayload="{not-json"
m_out=$(mktemp); m_err=$(mktemp)
echo "$mpayload" | "$HOOK" >"$m_out" 2>"$m_err"
m_rc=$?
m_out_v=$(cat "$m_out"); m_err_v=$(cat "$m_err")
rm -f "$m_out" "$m_err"
if [ "$m_rc" -eq 0 ] && [ -z "$m_out_v" ] && [ -z "$m_err_v" ]; then
  echo "PASS  [malformed JSON fail-open]"; PASS=$((PASS + 1))
else
  echo "FAIL  [malformed JSON fail-open] rc=$m_rc out='$m_out_v' err='$m_err_v'"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("malformed JSON fail-open")
fi

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
