#!/usr/bin/env bash
# test_worktree_edit_gate.sh — coverage for hooks/preflight-gate/worktree-edit-gate/impl.py
#
# Uses a REAL temp git repo to exercise genuine branch detection — git is not
# mocked. All inline git commands use -c user.name / -c user.email to avoid
# env-sensitive config failures in CI.
#
# Usage: bash tests/hooks/preflight-gate/test_worktree_edit_gate.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/worktree-edit-gate/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()

# ---------------------------------------------------------------------------
# Real git repo fixture
# ---------------------------------------------------------------------------

TMPDIR_BASE=$(mktemp -d)
REPO_DIR="$TMPDIR_BASE/myorg/myrepo"
mkdir -p "$REPO_DIR"

trap 'rm -rf "$TMPDIR_BASE"' EXIT

# Init repo, make initial commit on main, then create a feature branch.
(
  cd "$REPO_DIR"
  git -c user.name="Test" -c user.email="test@test.com" init -b main
  touch dummy.txt
  git -c user.name="Test" -c user.email="test@test.com" add dummy.txt
  git -c user.name="Test" -c user.email="test@test.com" commit -m "init"
  # Add a fake origin remote so org/repo form matching resolves via URL.
  git remote add origin "https://github.com/myorg/myrepo.git"
  git -c user.name="Test" -c user.email="test@test.com" checkout -b dev
  git -c user.name="Test" -c user.email="test@test.com" checkout -b prod
  git -c user.name="Test" -c user.email="test@test.com" checkout -b "feature/my-feature"
  # Also create a branch with spaces in path for later test
  git -c user.name="Test" -c user.email="test@test.com" checkout main
)

REPO_BASENAME="myrepo"
REPO_ORG_REPO="myorg/myrepo"

# A second repo NOT in the enforced list
OTHER_REPO_DIR="$TMPDIR_BASE/other-repo"
mkdir -p "$OTHER_REPO_DIR"
(
  cd "$OTHER_REPO_DIR"
  git -c user.name="Test" -c user.email="test@test.com" init -b main
  touch dummy.txt
  git -c user.name="Test" -c user.email="test@test.com" add dummy.txt
  git -c user.name="Test" -c user.email="test@test.com" commit -m "init"
)

# A repo with a space in its path
SPACED_REPO_DIR="$TMPDIR_BASE/space repo"
mkdir -p "$SPACED_REPO_DIR"
(
  cd "$SPACED_REPO_DIR"
  git -c user.name="Test" -c user.email="test@test.com" init -b main
  touch dummy.txt
  git -c user.name="Test" -c user.email="test@test.com" add dummy.txt
  git -c user.name="Test" -c user.email="test@test.com" commit -m "init"
)

# Helpers: switch the real repo to a branch
checkout_branch() {
  git -C "$REPO_DIR" -c user.name="Test" -c user.email="test@test.com" checkout "$1" 2>/dev/null
}

checkout_other() {
  git -C "$OTHER_REPO_DIR" -c user.name="Test" -c user.email="test@test.com" checkout "$1" 2>/dev/null
}

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------

build_payload() {
  local tool="$1" path="$2"
  python3 -c '
import json, sys
tool, path = sys.argv[1], sys.argv[2]
print(json.dumps({"tool_name": tool, "tool_input": {"file_path": path}}))
' "$tool" "$path"
}

pipe_hook() {
  local payload="$1"; shift
  (
    for kv in "$@"; do
      export "$kv"
    done
    printf '%s' "$payload" | python3 "$HOOK"
  )
}

check_block() {
  local rc="$1" err="$2"
  [ "$rc" -eq 2 ] || return 1
  echo "$err" | grep -qi "worktree-edit-gate" || return 1
  return 0
}

# run_case <name> <expected:block|pass> <tool_name> <file_path> [KEY=VALUE ...]
run_case() {
  local name="$1" expected="$2" tool="$3" path="$4"; shift 4
  local env_overrides=("$@")

  local payload
  payload=$(build_payload "$tool" "$path")

  local out_file err_file
  out_file=$(mktemp); err_file=$(mktemp)
  pipe_hook "$payload" "${env_overrides[@]}" >"$out_file" 2>"$err_file"
  local rc=$?
  local out err
  out=$(cat "$out_file"); err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expected" in
    block)
      check_block "$rc" "$err" || ok=0
      ;;
    pass)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    *)
      echo "FAIL [$name] unknown expected: $expected"
      ((FAIL++)); FAILED_NAMES+=("$name"); return
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS [$expected] $name"; ((PASS++))
  else
    echo "FAIL [$expected] $name (rc=$rc, stderr=$([ -n "$err" ] && echo non-empty || echo empty))"
    ((FAIL++)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# Surface 1: default no config → pass (no-op)
# ---------------------------------------------------------------------------

checkout_branch main
run_case "no ENFORCED_REPOS → pass (no-op)" pass \
  Edit "$REPO_DIR/src/main.py"

# ---------------------------------------------------------------------------
# Surface 2 & 3: source file, enforced repo, base branches → BLOCK
# ---------------------------------------------------------------------------

checkout_branch main
run_case "source file + enforced repo + main → block (Edit)" block \
  Edit "$REPO_DIR/src/main.py" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_BASENAME"

run_case "source file + enforced repo + main → block (Write)" block \
  Write "$REPO_DIR/src/new_file.py" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_BASENAME"

checkout_branch dev
run_case "source file + enforced repo + dev → block" block \
  Edit "$REPO_DIR/src/main.py" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_BASENAME"

checkout_branch prod
run_case "source file + enforced repo + prod → block" block \
  Edit "$REPO_DIR/src/main.py" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_BASENAME"

# ---------------------------------------------------------------------------
# Surface 4 & 5: non-source files → PASS
# ---------------------------------------------------------------------------

checkout_branch main
run_case "non-source .md file + enforced repo + main → pass" pass \
  Edit "$REPO_DIR/README.md" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_BASENAME"

run_case "non-source .json file + enforced repo + main → pass" pass \
  Edit "$REPO_DIR/config.json" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_BASENAME"

# ---------------------------------------------------------------------------
# Surface 6: source file on feature branch → PASS
# ---------------------------------------------------------------------------

checkout_branch "feature/my-feature"
run_case "source file + enforced repo + feature branch → pass" pass \
  Edit "$REPO_DIR/src/main.py" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_BASENAME"

# ---------------------------------------------------------------------------
# Surface 7: repo NOT in enforced list → PASS
# ---------------------------------------------------------------------------

checkout_branch main
run_case "source file + repo not in enforced list → pass" pass \
  Edit "$OTHER_REPO_DIR/src/main.py" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_BASENAME"

# ---------------------------------------------------------------------------
# Surface 8: bypass env set → PASS
# ---------------------------------------------------------------------------

checkout_branch main
run_case "bypass env set → pass" pass \
  Edit "$REPO_DIR/src/main.py" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_BASENAME" \
  "PRAXIS_HOOK_BYPASS_WORKTREE_GATE=1"

# ---------------------------------------------------------------------------
# Surface 9: file not in any git repo → PASS (fail-open)
# ---------------------------------------------------------------------------

checkout_branch main
run_case "file not in git repo → pass (fail-open)" pass \
  Edit "/tmp/random-file.py" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_BASENAME"

# ---------------------------------------------------------------------------
# Surface 10: detached HEAD → PASS (fail-open)
# ---------------------------------------------------------------------------

# Create detached HEAD state
git -C "$REPO_DIR" checkout --detach HEAD 2>/dev/null
run_case "detached HEAD → pass (fail-open)" pass \
  Edit "$REPO_DIR/src/main.py" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_BASENAME"
# Restore to main
checkout_branch main

# ---------------------------------------------------------------------------
# Surface 11: custom extensions
# ---------------------------------------------------------------------------

checkout_branch main
# With default extensions: .rb is not a source file → pass
run_case "custom ext: .rb not in default set → pass" pass \
  Edit "$REPO_DIR/src/app.rb" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_BASENAME"

# With custom extensions including rb: .rb → block
run_case "custom ext: .rb in WORKTREE_SOURCE_EXTENSIONS → block" block \
  Edit "$REPO_DIR/src/app.rb" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_BASENAME" \
  "PRAXIS_WORKTREE_SOURCE_EXTENSIONS=py,rb"

# Compound extension j2.sql → block with default set
run_case "compound ext: .j2.sql → block (via compound match)" block \
  Edit "$REPO_DIR/src/query.j2.sql" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_BASENAME"

# ---------------------------------------------------------------------------
# Surface 12: custom base branches
# ---------------------------------------------------------------------------

checkout_branch "feature/my-feature"
# feature/my-feature is NOT a default base branch, but add it as custom → block
run_case "custom base branches: feature branch now blocked → block" block \
  Edit "$REPO_DIR/src/main.py" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_BASENAME" \
  "PRAXIS_WORKTREE_BASE_BRANCHES=main,feature/my-feature"

# main is no longer in custom set → pass
checkout_branch main
run_case "custom base branches: main excluded from set → pass" pass \
  Edit "$REPO_DIR/src/main.py" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_BASENAME" \
  "PRAXIS_WORKTREE_BASE_BRANCHES=release,stable"

# ---------------------------------------------------------------------------
# Surface 13: file path with spaces
# ---------------------------------------------------------------------------

checkout_branch main
# Switch the space repo to main (already on main from init)
run_case "file path with spaces → block" block \
  Edit "$SPACED_REPO_DIR/src/main.py" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=space repo"

# ---------------------------------------------------------------------------
# Surface 14: malformed JSON stdin → PASS (fail-open)
# ---------------------------------------------------------------------------

malformed_test() {
  local name="malformed stdin → pass (fail-open)"
  local out_file err_file
  out_file=$(mktemp); err_file=$(mktemp)
  PRAXIS_WORKTREE_ENFORCED_REPOS="$REPO_BASENAME" \
    printf 'not-valid-json' | python3 "$HOOK" >"$out_file" 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"
  if [ "$rc" -eq 0 ] && [ -z "$err" ]; then
    echo "PASS [pass] $name"; ((PASS++))
  else
    echo "FAIL [pass] $name (rc=$rc, stderr=$([ -n "$err" ] && echo non-empty || echo empty))"
    ((FAIL++)); FAILED_NAMES+=("$name")
  fi
}
malformed_test

# ---------------------------------------------------------------------------
# Surface 15: org/repo form identifier matching
# ---------------------------------------------------------------------------

checkout_branch main
run_case "org/repo form identifier → block" block \
  Edit "$REPO_DIR/src/main.py" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_ORG_REPO"

# Wrong org → pass
run_case "org/repo form: wrong org → pass" pass \
  Edit "$REPO_DIR/src/main.py" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=wrongorg/myrepo"

# ---------------------------------------------------------------------------
# Surface 16: ts, tsx, go extensions (default set)
# ---------------------------------------------------------------------------

checkout_branch main
run_case ".ts source file → block" block \
  Edit "$REPO_DIR/src/App.ts" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_BASENAME"

run_case ".tsx source file → block" block \
  Edit "$REPO_DIR/src/Component.tsx" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_BASENAME"

run_case ".go source file → block" block \
  Edit "$REPO_DIR/src/main.go" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_BASENAME"

run_case ".sql source file → block" block \
  Edit "$REPO_DIR/migrations/001.sql" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_BASENAME"

# ---------------------------------------------------------------------------
# Surface 17: NotebookEdit tool → PASS (not in scope)
# ---------------------------------------------------------------------------

checkout_branch main
notebook_test() {
  local name="NotebookEdit tool → pass (not in scope)"
  local payload
  payload=$(python3 -c '
import json
print(json.dumps({"tool_name": "NotebookEdit", "tool_input": {"notebook_path": "/'"$REPO_DIR"'/notebook.py"}}))
')
  local out_file err_file
  out_file=$(mktemp); err_file=$(mktemp)
  printf '%s' "$payload" | \
    PRAXIS_WORKTREE_ENFORCED_REPOS="$REPO_BASENAME" \
    python3 "$HOOK" >"$out_file" 2>"$err_file"
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"
  if [ "$rc" -eq 0 ] && [ -z "$err" ]; then
    echo "PASS [pass] $name"; ((PASS++))
  else
    echo "FAIL [pass] $name (rc=$rc)"
    ((FAIL++)); FAILED_NAMES+=("$name")
  fi
}
notebook_test

# Bash tool → pass
run_case "Bash tool → pass (not in scope)" pass \
  Bash "$REPO_DIR/src/main.py" \
  "PRAXIS_WORKTREE_ENFORCED_REPOS=$REPO_BASENAME"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
# Fail-open: entrypoint wrapped by the shared @fail_open guard (issue #498)
# ---------------------------------------------------------------------------
# The blocking logic returns 2; _hook_runtime.fail_open turns any uncaught
# exception into exit 0. The decorator's BEHAVIOR is tested centrally in
# tests/test_hook_runtime.sh — here we assert only that THIS hook opted into
# the guard (functools.wraps exposes __wrapped__ on a decorated main()).
_failopen_out=$(python3 - << PYEOF 2>&1
import importlib.util
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
assert getattr(mod.main, "__wrapped__", None) is not None, "main is not @fail_open-wrapped"
print("OK")
PYEOF
)
_failopen_rc=$?
if [ "$_failopen_rc" -eq 0 ] && [ "$_failopen_out" = "OK" ]; then
  echo "PASS  [fail-open] main() is wrapped by the shared @fail_open guard"
  PASS=$((PASS+1))
else
  echo "FAIL  [fail-open] main() not @fail_open-wrapped (rc=$_failopen_rc out=$_failopen_out)"
  FAIL=$((FAIL+1)); FAILED_NAMES+=("fail-open guard wrapping")
fi


# ---------------------------------------------------------------------------

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
