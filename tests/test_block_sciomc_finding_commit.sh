#!/bin/bash
# test_block_sciomc_finding_commit.sh — coverage for hooks/block-sciomc-finding-commit.py
#
# Synthesizes Claude Code PreToolUse hook payloads + scratch transcript
# fixtures and asserts:
#   block  → rc=2, stderr non-empty (BLOCKED: prefix)
#   silent → rc=0, stderr empty
#
# Usage: bash tests/test_block_sciomc_finding_commit.sh

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$ROOT_DIR/hooks/block-sciomc-finding-commit.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# Fixture: transcript with sciomc finding markers
TX_FINDING="$TMPDIR/tx-finding.jsonl"
cat > "$TX_FINDING" <<'EOF'
{"type":"assistant","message":"Running sciomc Stage 5 analysis"}
{"type":"tool_use","content":"[FINDING:F1] sibling-deviant pattern detected"}
{"type":"assistant","message":"about to commit"}
EOF

# Fixture: transcript with sciomc finding AND consensus re-fetch after
TX_FINDING_REFETCH="$TMPDIR/tx-finding-refetch.jsonl"
cat > "$TX_FINDING_REFETCH" <<'EOF'
{"type":"assistant","message":"sciomc Stage 5 sibling-deviant"}
{"type":"tool_use","content":"gh pr view 8299 --json body --jq .body"}
{"type":"assistant","message":"compared with user design"}
EOF

# Fixture: transcript with no finding markers
TX_NORMAL="$TMPDIR/tx-normal.jsonl"
cat > "$TX_NORMAL" <<'EOF'
{"type":"assistant","message":"normal fix work"}
EOF

# run_case name expectation payload_json [env_vars...]
run_case() {
  local name="$1" expectation="$2" payload="$3"
  shift 3
  local env_args=()
  for kv in "$@"; do env_args+=("$kv"); done

  local out_file err_file
  out_file=$(mktemp)
  err_file=$(mktemp)

  if [ "${#env_args[@]}" -gt 0 ]; then
    echo "$payload" | env "${env_args[@]}" python3 "$HOOK" >"$out_file" 2>"$err_file"
  else
    echo "$payload" | env -u CLAUDE_HOOK_BYPASS_SCIOMC_GATE python3 "$HOOK" >"$out_file" 2>"$err_file"
  fi
  local rc=$?
  local out err
  out=$(cat "$out_file"); err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    block)
      [ "$rc" -eq 2 ] || ok=0
      echo "$err" | grep -q "^BLOCKED:" || ok=0
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
    [ -n "$err" ] && echo "        stderr: $(echo "$err" | head -c 400)"
  fi
}

echo "test_block_sciomc_finding_commit"

# ---------------------------------------------------------------------------
# BLOCK cases — finding marker present + no consensus re-fetch after
# ---------------------------------------------------------------------------

run_case "git commit + finding + no refetch (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: flip literal'\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "git commit -am + finding + no refetch (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -am 'fix: stuff'\"},\"transcript_path\":\"$TX_FINDING\"}"

# ---------------------------------------------------------------------------
# SILENT cases — should not block
# ---------------------------------------------------------------------------

run_case "git commit + finding + gh pr view --json body AFTER (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: aligned'\"},\"transcript_path\":\"$TX_FINDING_REFETCH\"}"

run_case "git commit + no finding in transcript (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'chore: bump'\"},\"transcript_path\":\"$TX_NORMAL\"}"

run_case "git commit + finding + [user-approved] token (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: change [user-approved]'\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "git commit + finding + [ratified-by-user] token (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: change [ratified-by-user]'\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "git commit --amend + finding (silent — non-content)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit --amend --no-edit\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "git revert + finding (silent — non-content)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git revert abc123\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "git merge + finding (silent — non-content)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git merge origin/main\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "git commit --allow-empty + finding (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit --allow-empty -m 'trigger ci'\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "non-Bash tool (silent)" \
  "silent" \
  "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"/tmp/x\",\"old_string\":\"a\",\"new_string\":\"b\"}}"

run_case "git status (non-commit, silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git status\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "env var bypass (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix'\"},\"transcript_path\":\"$TX_FINDING\"}" \
  "CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1"

run_case "missing transcript_path (silent — cannot enforce)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix'\"}}"

run_case "malformed JSON (silent — fail-open)" \
  "silent" \
  "not-json"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo
TOTAL=$((PASS + FAIL))
echo "Result: $PASS/$TOTAL passed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
