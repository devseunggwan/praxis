#!/bin/bash
# test_block_rename_sweep_survivors.sh — coverage for
#   hooks/preflight-gate/block-rename-sweep-survivors/impl.py
#
# Tests that require git diff --cached output are run inside a temporary
# git repo created per-test-group. Pure command-detection cases use an
# empty repo (no staged diff) to verify the hook passes through without
# blocking.
#
# Coverage:
#   DENY  — sweep ≥ threshold + survivors in tracked tree
#   PASS  — sweep ≥ threshold but all occurrences staged (no survivors)
#   PASS  — below threshold (count < SWEEP_THRESHOLD)
#   PASS  — survivors marked exempt
#   PASS  — not a git commit command
#   PASS  — git commit --amend (exempt)
#   PASS  — PRAXIS_SKIP_RENAME_SWEEP_CHECK=1
#   PASS  — grouped hunk layout (regression for buffer-flush parser)
#
# Usage: bash tests/hooks/preflight-gate/test_block_rename_sweep_survivors.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/block-rename-sweep-survivors/impl.py"

if [ ! -f "$HOOK" ]; then
  echo "FAIL: hook not found: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# run_case name expectation payload_json [extra_env_var=value ...]
#   expectation:
#     "block"  — rc=2, stderr contains " blocked$" (standard emit_block header)
#     "silent" — rc=0, stderr empty
run_case() {
  local name="$1" expectation="$2" payload="$3"
  local env_override="${4:-}"

  local out_file err_file
  out_file=$(mktemp); err_file=$(mktemp)

  if [ -n "$env_override" ]; then
    echo "$payload" | env "$env_override" \
      PRAXIS_SKIP_RENAME_SWEEP_CHECK='' python3 "$HOOK" >"$out_file" 2>"$err_file"
  else
    echo "$payload" | PRAXIS_SKIP_RENAME_SWEEP_CHECK='' \
      python3 "$HOOK" >"$out_file" 2>"$err_file"
  fi
  local rc=$?
  local err
  err=$(cat "$err_file")
  rm -f "$out_file" "$err_file"

  local ok=1
  case "$expectation" in
    block)
      [ "$rc" -eq 2 ] || ok=0
      echo "$err" | grep -q " blocked$" || ok=0
      ;;
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ] || ok=0
      ;;
    *)
      echo "UNKNOWN expectation: $expectation" >&2
      ok=0
      ;;
  esac

  if [ "$ok" -eq 1 ]; then
    echo "PASS: $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $name (rc=$rc)"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
    [ -n "$err" ] && echo "  stderr: $(echo "$err" | head -c 300)"
  fi
}

COMMIT_PAYLOAD='{"tool_name":"Bash","tool_input":{"command":"git commit -m test"}}'
NOT_COMMIT_PAYLOAD='{"tool_name":"Bash","tool_input":{"command":"git status"}}'
AMEND_PAYLOAD='{"tool_name":"Bash","tool_input":{"command":"git commit --amend --no-edit"}}'

# ---------------------------------------------------------------------------
# Group 1: command-detection only (no staged diff needed)
# These run from the existing cwd — git diff --cached returns empty, so the
# hook passes through regardless of any sweep detection.
# ---------------------------------------------------------------------------

run_case "not_git_commit_passes" "silent" "$NOT_COMMIT_PAYLOAD"
run_case "git_commit_amend_passes" "silent" "$AMEND_PAYLOAD"
run_case "bypass_env_passes" "silent" "$COMMIT_PAYLOAD" "PRAXIS_SKIP_RENAME_SWEEP_CHECK=1"
run_case "non_bash_tool_passes" "silent" \
  '{"tool_name":"Write","tool_input":{"command":"git commit -m test"}}'
run_case "empty_command_passes" "silent" \
  '{"tool_name":"Bash","tool_input":{"command":""}}'

# ---------------------------------------------------------------------------
# Group 2: real git repo with staged rename sweep
# ---------------------------------------------------------------------------

setup_repo() {
  local repo
  repo=$(mktemp -d)
  cd "$repo" || return 1
  git init -q
  git config user.email "test@example.com"
  git config user.name "Test"
  echo "$repo"
}

ORIG_DIR="$PWD"

# --- Case: sweep with survivors → DENY ---
REPO=$(setup_repo)
cd "$REPO" || exit
python3 -c "
for i in range(1, 5):
    open(f'f{i}.py', 'w').write(f'lifecycle-stage = {i}\n')
"
git add .
git commit -q -m "initial"
# Rename 3 files (staged), leave f4.py as a survivor
python3 -c "
for i in range(1, 4):
    content = open(f'f{i}.py').read().replace('lifecycle-stage', 'lifecycle_stage')
    open(f'f{i}.py', 'w').write(content)
"
git add f1.py f2.py f3.py
run_case "sweep_with_survivors_denied" "block" "$COMMIT_PAYLOAD"
cd "$ORIG_DIR" || exit
rm -rf "$REPO"

# --- Case: sweep with all occurrences staged → PASS ---
REPO=$(setup_repo)
cd "$REPO" || exit
python3 -c "
for i in range(1, 4):
    open(f'f{i}.py', 'w').write(f'lifecycle-stage = {i}\n')
"
git add .
git commit -q -m "initial"
# Rename all 3 files
python3 -c "
for i in range(1, 4):
    content = open(f'f{i}.py').read().replace('lifecycle-stage', 'lifecycle_stage')
    open(f'f{i}.py', 'w').write(content)
"
git add f1.py f2.py f3.py
run_case "sweep_no_survivors_passes" "silent" "$COMMIT_PAYLOAD"
cd "$ORIG_DIR" || exit
rm -rf "$REPO"

# --- Case: below threshold (only 2 renames) → PASS ---
REPO=$(setup_repo)
cd "$REPO" || exit
python3 -c "
for i in range(1, 3):
    open(f'f{i}.py', 'w').write(f'lifecycle-stage = {i}\n')
"
git add .
git commit -q -m "initial"
python3 -c "
for i in range(1, 3):
    content = open(f'f{i}.py').read().replace('lifecycle-stage', 'lifecycle_stage')
    open(f'f{i}.py', 'w').write(content)
"
git add f1.py f2.py
run_case "below_threshold_passes" "silent" "$COMMIT_PAYLOAD"
cd "$ORIG_DIR" || exit
rm -rf "$REPO"

# --- Case: all survivors marked exempt → PASS ---
REPO=$(setup_repo)
cd "$REPO" || exit
python3 -c "
for i in range(1, 4):
    open(f'f{i}.py', 'w').write(f'lifecycle-stage = {i}\n')
# f4 has the exempt marker
open('f4.py', 'w').write('lifecycle-stage = 4  # [rename-sweep-exempt]\n')
"
git add .
git commit -q -m "initial"
python3 -c "
for i in range(1, 4):
    content = open(f'f{i}.py').read().replace('lifecycle-stage', 'lifecycle_stage')
    open(f'f{i}.py', 'w').write(content)
"
git add f1.py f2.py f3.py
run_case "survivors_all_exempt_passes" "silent" "$COMMIT_PAYLOAD"
cd "$ORIG_DIR" || exit
rm -rf "$REPO"

# --- Case: grouped hunk layout (regression for buffer-flush parser) ---
# Consecutive lines in one file produce a grouped hunk: all - lines then all + lines.
REPO=$(setup_repo)
cd "$REPO" || exit
# One file with 3 consecutive lifecycle-stage lines + 1 survivor in another file
python3 -c "
open('f1.py', 'w').write('lifecycle-stage = 1\nlifecycle-stage = 2\nlifecycle-stage = 3\n')
open('f2.py', 'w').write('lifecycle-stage = 4\n')
"
git add .
git commit -q -m "initial"
# Replace all 3 lines in f1 (grouped hunk), leave f2 as survivor
python3 -c "
content = open('f1.py').read().replace('lifecycle-stage', 'lifecycle_stage')
open('f1.py', 'w').write(content)
"
git add f1.py
run_case "grouped_hunk_with_survivors_denied" "block" "$COMMIT_PAYLOAD"
cd "$ORIG_DIR" || exit
rm -rf "$REPO"

# --- Case: grouped hunk, no survivors → PASS ---
REPO=$(setup_repo)
cd "$REPO" || exit
python3 -c "
open('f1.py', 'w').write('lifecycle-stage = 1\nlifecycle-stage = 2\nlifecycle-stage = 3\n')
"
git add .
git commit -q -m "initial"
python3 -c "
content = open('f1.py').read().replace('lifecycle-stage', 'lifecycle_stage')
open('f1.py', 'w').write(content)
"
git add f1.py
run_case "grouped_hunk_no_survivors_passes" "silent" "$COMMIT_PAYLOAD"
cd "$ORIG_DIR" || exit
rm -rf "$REPO"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed: ${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
