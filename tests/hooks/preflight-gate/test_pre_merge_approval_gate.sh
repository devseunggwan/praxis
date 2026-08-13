#!/bin/bash
# test_pre_merge_approval_gate.sh — coverage for hooks/preflight-gate/pre-merge-approval-gate/impl.py
#
# Synthesizes Claude Code PreToolUse hook payloads and asserts:
#   ask    → exit 0 + stdout JSON has permissionDecision "ask"
#   silent → exit 0 + stdout empty (no JSON, no permissionDecision)
#
# Usage: bash tests/test_pre_merge_approval_gate.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/pre-merge-approval-gate/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# run_case name expectation delegate_env payload_json
#   expectation:
#     "ask"    — stdout JSON has permissionDecision "ask", rc=0
#     "silent" — stdout empty, rc=0
#   delegate_env:
#     "delegate"    — run with CMUX_DELEGATE=1 set
#     "no-delegate" — run with CMUX_DELEGATE unset
run_case() {
  local name="$1" expectation="$2" delegate="$3" payload="$4"

  local out_file
  out_file=$(mktemp)

  if [ "$delegate" = "delegate" ]; then
    echo "$payload" | CMUX_DELEGATE=1 python3 "$HOOK" >"$out_file" 2>/dev/null
  else
    echo "$payload" | env -u CMUX_DELEGATE python3 "$HOOK" >"$out_file" 2>/dev/null
  fi
  local rc=$?
  local out
  out=$(cat "$out_file")
  rm -f "$out_file"

  local ok=1
  case "$expectation" in
    ask)
      [ "$rc" -eq 0 ] || ok=0
      echo "$out" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    decision = d.get('hookSpecificOutput', {}).get('permissionDecision', '')
    sys.exit(0 if decision == 'ask' else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null || ok=0
      ;;
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ] || ok=0
      ;;
    *)
      echo "  internal: unknown expectation '$expectation'" >&2
      ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    PASS=$((PASS + 1))
    echo "  PASS  $name"
  else
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
    echo "  FAIL  $name (rc=$rc, expected=$expectation)"
    [ -n "$out" ] && echo "        stdout: $out" | head -c 400
  fi
}

echo "test_pre_merge_approval_gate"

# --- Direct session (CMUX_DELEGATE unset) → ASK

run_case "direct session: gh pr merge --squash (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr merge 1 --squash"}}'

run_case "direct session: gh pr merge --merge (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr merge 42 --merge"}}'

run_case "direct session: gh pr merge --delete-branch (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr merge 99 --squash --delete-branch"}}'

# --- Background agent (CMUX_DELEGATE=1) → SILENT

run_case "background agent: gh pr merge (silent)" \
  "silent" "delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr merge 1"}}'

run_case "background agent: gh pr merge --squash (silent)" \
  "silent" "delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr merge 1 --squash --delete-branch"}}'

# --- R4-F1: marker removed — agent-attachable bypass cannot exist → ASK

run_case "direct session: merge-approval:ack marker no longer bypasses (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr merge 1 --squash # merge-approval:ack"}}'

# --- Non-merge gh commands → SILENT

run_case "direct session: gh pr view (silent)" \
  "silent" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr view 1"}}'

run_case "direct session: gh pr list (silent)" \
  "silent" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr list --state open"}}'

run_case "direct session: gh pr create (silent, not merge)" \
  "silent" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr create --title foo --body bar"}}'

run_case "direct session: git commit with merge in message (silent)" \
  "silent" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"merge note\""}}'

# --- Chained commands: scan beyond first segment → ASK

run_case "direct session: chained gh issue list && gh pr merge (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh issue list && gh pr merge 1 --squash"}}'

run_case "direct session: echo prep; gh pr merge 5 (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"echo prep; gh pr merge 5"}}'

# --- Quoted body containing 'gh pr merge' text → SILENT (not executed as cmd)

run_case "direct session: quoted body with gh pr merge text (silent)" \
  "silent" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh pr comment 1 --body \"next step: gh pr merge\""}}'

# --- Inline env prefix DOES NOT satisfy CMUX_DELEGATE check → ASK
# env CMUX_DELEGATE=1 sets the env for the child process (gh), not for this
# hook. The hook reads its own process env, so it still sees no CMUX_DELEGATE.

run_case "inline env CMUX_DELEGATE=1 gh pr merge (ask, not a delegate session)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"env CMUX_DELEGATE=1 gh pr merge 1 --squash"}}'

# --- Non-Bash tool → SILENT

run_case "non-Bash tool (Read) → silent" \
  "silent" "no-delegate" \
  '{"tool_name":"Read","tool_input":{"file_path":"/tmp/foo"}}'

run_case "non-Bash tool (Write) → silent" \
  "silent" "no-delegate" \
  '{"tool_name":"Write","tool_input":{"file_path":"/tmp/bar","content":"x"}}'

# --- Malformed JSON → SILENT (fail-open)

run_case "malformed JSON → silent" \
  "silent" "no-delegate" \
  'not-json'

# --- Empty / blank command → SILENT

run_case "empty command → silent" \
  "silent" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":""}}'

# --- gh global flags between `gh` and subcommand → ASK (F1 regression)

run_case "direct session: gh -R owner/repo pr merge --squash (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh -R owner/repo pr merge 1 --squash"}}'

run_case "direct session: gh --repo owner/repo pr merge (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh --repo owner/repo pr merge 1"}}'

run_case "direct session: gh --hostname github.com pr merge (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh --hostname github.com pr merge 1"}}'

run_case "direct session: gh -R=owner/repo pr merge (= form, ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh -R=owner/repo pr merge 1"}}'

run_case "direct session: gh -R owner/repo pr list (read, not merge, silent)" \
  "silent" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh -R owner/repo pr list"}}'

run_case "direct session: gh -R repo pr merge + ack marker no longer bypasses (ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"gh -R owner/repo pr merge 1 # merge-approval:ack"}}'

run_case "inline CMUX_DELEGATE=1 gh -R owner/repo pr merge (ask, not delegate session)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"CMUX_DELEGATE=1 gh -R owner/repo pr merge 1"}}'

# --- #511: path-prefix / command-wrapper bypass must NOT slip past the gate → ASK
# `argv[0] == "gh"` exact match previously let `/usr/bin/gh pr merge` and
# `command gh pr merge` through silently. _is_gh_binary (basename) + the
# command/builtin peel in strip_prefix close both.

run_case "direct session: /usr/bin/gh pr merge 5 (path-prefix, ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"/usr/bin/gh pr merge 5"}}'

run_case "direct session: command gh pr merge 5 (command wrapper, ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"command gh pr merge 5"}}'

run_case "direct session: builtin command gh pr merge 5 (builtin wrapper, ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"builtin command gh pr merge 5"}}'

run_case "direct session: /usr/bin/gh -R owner/repo pr merge (path-prefix + global flag, ask)" \
  "ask" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"/usr/bin/gh -R owner/repo pr merge 5"}}'

run_case "direct session: /usr/bin/gh pr list (path-prefix read, not merge, silent)" \
  "silent" "no-delegate" \
  '{"tool_name":"Bash","tool_input":{"command":"/usr/bin/gh pr list"}}'

# --- #985: help and heredoc bodies are not merges ---------------------------
# A gate that asks on `gh pr merge --help` blocks the one command run to learn
# the flags; a heredoc body is data, so a commit message quoting the command is
# not the command. The last two cases keep the fix from becoming a bypass.

run_case "direct session: commit message quoting the merge (heredoc body, silent)" \
  "silent" "no-delegate" \
  '{"tool_name": "Bash", "tool_input": {"command": "git commit -m \"$(cat <<'EOF'\ngh pr merge is only quoted here\nEOF\n)\""}}'

run_case "direct session: merge after a heredoc terminator (still a merge, ask)" \
  "ask" "no-delegate" \
  '{"tool_name": "Bash", "tool_input": {"command": "cat <<'EOF'\nbody\nEOF\ngh pr merge 985 --squash"}}'


echo ""
echo "Result: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed:"
  for name in "${FAILED_NAMES[@]}"; do
    echo "  - $name"
  done
  exit 1
fi
exit 0
