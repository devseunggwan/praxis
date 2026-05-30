#!/bin/bash
# test_block_gh_issue_create_without_dup_search.sh —
#   coverage for hooks/preflight-gate/block-gh-issue-create-without-dup-search/impl.py
#
# Asserts:
#   block  → rc=2, stderr starts with "BLOCKED:"
#   silent → rc=0, stderr empty
#
# Usage: bash tests/test_block_gh_issue_create_without_dup_search.sh

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/block-gh-issue-create-without-dup-search/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

TX_EMPTY="$TMPDIR/tx-empty.jsonl"
cat > "$TX_EMPTY" <<'EOF'
{"type":"assistant","message":"creating issue"}
EOF

TX_OVERLAP="$TMPDIR/tx-overlap.jsonl"
cat > "$TX_OVERLAP" <<'EOF'
{"type":"tool_use","content":"gh search issues brands --repo acme/repo"}
{"type":"assistant","message":"no dup found"}
EOF

TX_NO_OVERLAP="$TMPDIR/tx-no-overlap.jsonl"
cat > "$TX_NO_OVERLAP" <<'EOF'
{"type":"tool_use","content":"gh search issues authentication --repo acme/repo"}
EOF

# Regression fixture for issue #384: the prior `--repo` value contains a
# substring that matches one of the title keywords (`widget`). Under the
# old substring-overlap check, `widget` ⊂ `--repo acme/widget` flipped
# overlap=true and silently passed. With set-intersection on extracted
# topic tokens (flag values skipped), this case must BLOCK.
TX_NO_OVERLAP_FLAG_LEAK="$TMPDIR/tx-no-overlap-flag-leak.jsonl"
cat > "$TX_NO_OVERLAP_FLAG_LEAK" <<'EOF'
{"type":"tool_use","content":"gh search issues authentication --repo acme/widget"}
EOF

TX_ISSUE_LIST="$TMPDIR/tx-issue-list.jsonl"
cat > "$TX_ISSUE_LIST" <<'EOF'
{"type":"tool_use","content":"gh issue list --repo acme/repo --search brands"}
EOF

run_case() {
  local name="$1" expectation="$2" payload="$3"
  shift 3
  local env_args=()
  for kv in "$@"; do env_args+=("$kv"); done

  local out_file err_file
  out_file=$(mktemp); err_file=$(mktemp)

  if [ "${#env_args[@]}" -gt 0 ]; then
    echo "$payload" | env "${env_args[@]}" python3 "$HOOK" >"$out_file" 2>"$err_file"
  else
    echo "$payload" | env -u CLAUDE_HOOK_BYPASS_DUP_GATE python3 "$HOOK" >"$out_file" 2>"$err_file"
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
      # Standard block format (issue #439): header line "⚠️ <RULE> blocked".
      echo "$err" | grep -q " blocked$" || ok=0
      ;;
    block:no-search)
      [ "$rc" -eq 2 ] || ok=0
      echo "$err" | grep -q "no prior \`gh search issues\`" || ok=0
      ;;
    block:no-overlap)
      [ "$rc" -eq 2 ] || ok=0
      echo "$err" | grep -q "none of their keywords overlap" || ok=0
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

echo "test_block_gh_issue_create_without_dup_search"

# ---------------------------------------------------------------------------
# BLOCK cases
# ---------------------------------------------------------------------------

run_case "gh issue create + no prior search (block)" \
  "block:no-search" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo acme/repo --title 'feat(provider): add zeta brands lookup pattern'\"},\"transcript_path\":\"$TX_EMPTY\"}"

run_case "gh issue create + prior search but no keyword overlap (block)" \
  "block:no-overlap" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo acme/repo --title 'feat(provider): add zeta brands lookup pattern'\"},\"transcript_path\":\"$TX_NO_OVERLAP\"}"

# Regression for issue #384: title keyword `widget` collides with the value
# of a prior `--repo acme/widget` flag. With the buggy substring overlap
# check, this silently passed; with set-intersection on extracted topic
# tokens (flag values skipped), it must BLOCK with the no-overlap reason.
run_case "gh issue create + prior search w/ keyword leaking from --repo value (regression #384)" \
  "block:no-overlap" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo acme/repo --title 'feat(provider): add widget brands lookup pattern'\"},\"transcript_path\":\"$TX_NO_OVERLAP_FLAG_LEAK\"}"

# ---------------------------------------------------------------------------
# SILENT cases
# ---------------------------------------------------------------------------

run_case "gh issue create + prior search with overlap (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo acme/repo --title 'feat(provider): add zeta brands lookup pattern'\"},\"transcript_path\":\"$TX_OVERLAP\"}"

run_case "gh issue create + prior gh issue list with overlap (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo acme/repo --title 'feat(provider): add brands lookup'\"},\"transcript_path\":\"$TX_ISSUE_LIST\"}"

run_case "gh issue create on personal repo (silent — devseunggwan)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo devseunggwan/scratchs --title 'feat: experiment'\"},\"transcript_path\":\"$TX_EMPTY\"}"

run_case "gh issue create + [dup-checked] token (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo acme/foo --title 'feat(y): brands [dup-checked]'\"},\"transcript_path\":\"$TX_EMPTY\"}"

run_case "gh issue create + [no-search-needed] token (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo acme/foo --title 'feat(y): brands [no-search-needed]'\"},\"transcript_path\":\"$TX_EMPTY\"}"

run_case "gh issue create + env var bypass (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo acme/foo --title 'feat: brands'\"},\"transcript_path\":\"$TX_EMPTY\"}" \
  "CLAUDE_HOOK_BYPASS_DUP_GATE=1"

run_case "gh issue list (not create) — silent" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue list --repo acme/foo\"},\"transcript_path\":\"$TX_EMPTY\"}"

run_case "gh pr create (not issue create) — silent" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh pr create --title 'feat: foo' --body bar\"},\"transcript_path\":\"$TX_EMPTY\"}"

run_case "non-Bash tool (silent)" \
  "silent" \
  "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"/tmp/x\"}}"

run_case "missing transcript_path (silent — cannot enforce)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo acme/foo --title 'feat: brands'\"}}"

run_case "gh issue create with title that has no usable keywords (silent)" \
  "silent" \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"gh issue create --repo acme/foo --title 'fix: a b c d'\"},\"transcript_path\":\"$TX_EMPTY\"}"

run_case "malformed JSON (silent — fail-open)" \
  "silent" \
  "not-json"

# ---------------------------------------------------------------------------
# Uncaught exception fail-open (outer Exception guard)
# ---------------------------------------------------------------------------

# Simulate an uncaught exception inside _main_inner (e.g. MemoryError) and
# verify the outer try/except Exception wrapper in main() returns 0 (fail-open).
_uncaught_out=$(python3 - << PYEOF 2>&1
import sys, importlib.util, io
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
def _raise(): raise MemoryError("simulated OOM")
mod._main_inner = _raise
sys.stdin = io.StringIO('{}')
sys.exit(mod.main())
PYEOF
)
_uncaught_rc=$?
if [ "$_uncaught_rc" -eq 0 ] && [ -z "$_uncaught_out" ]; then
  echo "  PASS  uncaught exception in _main_inner fails open (exit 0, no stderr)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  uncaught exception in _main_inner (rc=$_uncaught_rc, out=$(echo "$_uncaught_out" | head -c 200))"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("uncaught exception in _main_inner fails open")
fi

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
