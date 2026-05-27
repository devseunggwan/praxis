#!/bin/bash
# test_block_sciomc_finding_commit.sh — coverage for hooks/preflight-gate/block-sciomc-finding-commit/impl.py
#
# Synthesizes Claude Code PreToolUse hook payloads + scratch transcript
# fixtures and asserts:
#   block  → rc=2, stderr non-empty (BLOCKED: prefix)
#   silent → rc=0, stderr empty
#
# Usage: bash tests/hooks/preflight-gate/test_block_sciomc_finding_commit.sh

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/block-sciomc-finding-commit/impl.py"

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

# --allow-empty does NOT prevent staged content from being committed — must block
run_case "git commit --allow-empty with staged content (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit --allow-empty -m 'trigger ci'\"},\"transcript_path\":\"$TX_FINDING\"}"

# --allow-empty-message likewise does not prevent staged content — must block
run_case "git commit --allow-empty-message with staged content (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit --allow-empty-message -m ''\"},\"transcript_path\":\"$TX_FINDING\"}"

# `--amend` inside the -m message value is NOT the amend flag — must still block
run_case "amend word inside message still blocks" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'docs: explain the --amend flag'\"},\"transcript_path\":\"$TX_FINDING\"}"

# `-m --amend`: the message VALUE is exactly "--amend" — must NOT be exempted
run_case "git commit -m --amend (value is --amend, not flag) blocks" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m --amend\"},\"transcript_path\":\"$TX_FINDING\"}"

# `-am --amend`: clustered flag consumes "--amend" as the message value — must block
run_case "git commit -am --amend (clustered, value is --amend) blocks" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -am --amend\"},\"transcript_path\":\"$TX_FINDING\"}"

# ratification token outside the -m value must NOT bypass
run_case "user-approved token outside message (semicolon) still blocks" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: x'; echo '[user-approved]'\"},\"transcript_path\":\"$TX_FINDING\"}"

# subshell-grouped commit: shlex tokenizes binary as "(git" — must still block
# (regression guard: the prior raw-regex caught this; token parser must too)
run_case "subshell-grouped git commit (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"(git commit -m 'fix: sub')\"},\"transcript_path\":\"$TX_FINDING\"}"

# command-substitution wrapped commit: binary tokenizes as "\$(git" — must still block
run_case "command-substitution git commit (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"echo \$(git commit -m 'fix: subst')\"},\"transcript_path\":\"$TX_FINDING\"}"

# QUOTED command-substitution: bash executes the inner commit even inside double
# quotes (shlex folds it into one token) — hybrid span scan must catch it
run_case "quoted command-substitution echo (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"echo \\\"\$(git commit -m subst)\\\"\"},\"transcript_path\":\"$TX_FINDING\"}"

# assignment with quoted command-substitution
run_case "assignment quoted command-substitution (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"out=\\\"\$(git commit -m subst)\\\"\"},\"transcript_path\":\"$TX_FINDING\"}"

# quoted command-substitution of a NON-commit must NOT match
run_case "quoted command-substitution git status (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"echo \\\"\$(git status)\\\"\"},\"transcript_path\":\"$TX_FINDING\"}"

# nested command-substitution containing a commit (paren-depth scan)
run_case "nested command-substitution git commit (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"echo \\\"\$(echo \$(git commit -m nested))\\\"\"},\"transcript_path\":\"$TX_FINDING\"}"

# SINGLE-quoted substitution is a literal — bash does NOT execute it → silent
run_case "single-quoted substitution literal (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"echo '\$(git commit -m x)'\"},\"transcript_path\":\"$TX_FINDING\"}"

# apostrophe inside double quotes is literal — substitution still executes → block
run_case "apostrophe in double-quoted substitution (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"echo \\\"it's \$(git commit -m x)\\\"\"},\"transcript_path\":\"$TX_FINDING\"}"

# space-less shell separator: `true;git commit` must tokenize git as its own token
run_case "no-space semicolon separator before git commit (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"true;git commit -m 'fix: chained'\"},\"transcript_path\":\"$TX_FINDING\"}"

# space-less && separator
run_case "no-space && separator before git commit (block)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git status&&git commit -m 'fix: andand'\"},\"transcript_path\":\"$TX_FINDING\"}"

# a literal ';' INSIDE the quoted message must NOT be treated as a separator
run_case "semicolon inside message value still blocks (no token)" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: a;b'\"},\"transcript_path\":\"$TX_FINDING\"}"

# ---------------------------------------------------------------------------
# SILENT cases — should not block
# ---------------------------------------------------------------------------

run_case "git commit + finding + gh pr view --json body AFTER (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: aligned'\"},\"transcript_path\":\"$TX_FINDING_REFETCH\"}"

run_case "git commit + no finding in transcript (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'chore: bump'\"},\"transcript_path\":\"$TX_NORMAL\"}"

run_case "git commit + finding + [user-approved] in message (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: change [user-approved]'\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "git commit + finding + [ratified-by-user] in message (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: change [ratified-by-user]'\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "git commit + finding + [user-ratified] in message (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -m 'fix: change [user-ratified]'\"},\"transcript_path\":\"$TX_FINDING\"}"

# clustered short option `-am`: ratification token in message value must bypass
run_case "git commit -am with [user-approved] in message (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -am 'fix: change [user-approved]'\"},\"transcript_path\":\"$TX_FINDING\"}"

# attached form `-am'msg'` tokenizes as -ammsg — token must still be extracted
run_case "git commit -ammsg attached with [user-approved] (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -am'done [user-approved]'\"},\"transcript_path\":\"$TX_FINDING\"}"

# `-am` content commit WITHOUT a token must still block (escape-hatch is opt-in)
run_case "git commit -am without token still blocks" \
  "block" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit -am 'fix: real change'\"},\"transcript_path\":\"$TX_FINDING\"}"

# legit --amend is still exempt (it fixes up a prior already-gated commit)
run_case "git commit --amend + finding (silent — exempt)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit --amend --no-edit\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "git revert + finding (silent — non-content)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git revert abc123\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "git merge + finding (silent — non-content)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git merge origin/main\"},\"transcript_path\":\"$TX_FINDING\"}"

# git commit-tree is plumbing (one token "commit-tree") — must NOT match
run_case "git commit-tree plumbing (silent — not a git commit)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git commit-tree abc123 -m 'tree'\"},\"transcript_path\":\"$TX_FINDING\"}"

# `git --help commit` / `git --version commit` are terminal — no commit runs
run_case "git --help commit (silent — terminal option)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git --help commit\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "git --version commit (silent — terminal option)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git --version commit\"},\"transcript_path\":\"$TX_FINDING\"}"

# echo containing "git commit" — no token-level git+commit adjacency
run_case "echo git commit string not a commit (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"echo 'run git commit later'\"},\"transcript_path\":\"$TX_FINDING\"}"

run_case "non-Bash tool (silent)" \
  "silent" \
  "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"/tmp/x\",\"old_string\":\"a\",\"new_string\":\"b\"}}"

run_case "git status (non-commit, silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git status\"},\"transcript_path\":\"$TX_FINDING\"}"

# subshell-grouped non-commit must NOT over-match (prefix-strip is binary-only)
run_case "subshell-grouped git status (silent — not a commit)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"(git status)\"},\"transcript_path\":\"$TX_FINDING\"}"

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
