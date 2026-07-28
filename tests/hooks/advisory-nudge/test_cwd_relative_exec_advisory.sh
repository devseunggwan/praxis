#!/bin/bash
# test_cwd_relative_exec_advisory.sh — coverage for
# hooks/advisory-nudge/cwd-relative-exec-advisory/impl.py
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   ask:<marker>  — exit 0, stdout JSON permissionDecision=ask, contains <marker>
#   silent        — exit 0, stdout empty
#
# Usage: bash tests/hooks/advisory-nudge/test_cwd_relative_exec_advisory.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/cwd-relative-exec-advisory/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# ---------------------------------------------------------------------------
# Setup: a scratch git repo with 2 registered worktrees (ambiguous), and a
# separate scratch git repo with exactly 1 worktree (no ambiguity).
# ---------------------------------------------------------------------------

MULTI_BASE=$(mktemp -d)
git -c init.defaultBranch=main init -q "$MULTI_BASE/main"
(
  cd "$MULTI_BASE/main" || exit 1
  git -c user.name=test -c user.email=test@test.com commit -q --allow-empty -m init
)
git -C "$MULTI_BASE/main" worktree add -q -b other-wt "$MULTI_BASE/other" >/dev/null 2>&1

SINGLE_BASE=$(mktemp -d)
git -c init.defaultBranch=main init -q "$SINGLE_BASE/solo"
(
  cd "$SINGLE_BASE/solo" || exit 1
  git -c user.name=test -c user.email=test@test.com commit -q --allow-empty -m init
)

# run_case name expectation command cwd
#   expectation:
#     "ask:<marker>" — exit 0, stdout JSON permissionDecision=ask, contains <marker>
#     "silent"       — exit 0, stdout empty (no JSON emitted)
run_case() {
  local name="$1" expectation="$2" command="$3" cwd="${4:-$MULTI_BASE/main}"

  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({"tool_name": "Bash", "tool_input": {"command": sys.argv[1]}}))
' "$command")

  local out
  out=$(cd "$cwd" && echo "$payload" | python3 "$HOOK" 2>/dev/null)
  local rc=$?

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$out" ]   || ok=0
      ;;
    ask:*)
      local marker="${expectation#ask:}"
      [ "$rc" -eq 0 ] || ok=0
      case "$out" in
        *'"permissionDecision": "ask"'*) ;;
        *) ok=0 ;;
      esac
      case "$out" in
        *"$marker"*) ;;
        *) ok=0 ;;
      esac
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
    echo "FAIL  [$name] expectation=$expectation rc=$rc out=${out:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# === CORE: relative exec, no cd, 2+ worktrees → ask ===
# ---------------------------------------------------------------------------

run_case "relative ./script with no cd, 2 worktrees" \
  "ask:scripts/run-tests.sh" \
  "./scripts/run-tests.sh"

run_case "bare dir/file relative exec (no ./ prefix)" \
  "ask:scripts/foo.sh" \
  "scripts/foo.sh --flag"

run_case "../ prefix relative exec" \
  "ask:../scripts/foo.sh" \
  "../scripts/foo.sh"

run_case "gh --body-file relative value" \
  "ask:tmp/body.md" \
  "gh pr create --body-file tmp/body.md"

run_case "gh -F relative value" \
  "ask:tmp/body.md" \
  "gh pr comment 1 -F tmp/body.md"

run_case "relative exec after unrelated segment (no cd)" \
  "ask:scripts/foo.sh" \
  "echo hi && ./scripts/foo.sh"

# ---------------------------------------------------------------------------
# === SILENT: absolute / tilde / env-var paths ===
# ---------------------------------------------------------------------------

run_case "absolute path is silent" \
  "silent" \
  "/abs/scripts/run-tests.sh"

# shellcheck disable=SC2088  # literal test payload text, not shell-expanded here
run_case "tilde path is silent" \
  "silent" \
  "~/scripts/run-tests.sh"

# shellcheck disable=SC2088  # literal test payload text, not shell-expanded here
run_case "tilde-user path is silent" \
  "silent" \
  "~user/scripts/run-tests.sh"

run_case "env-var interpolated path is silent" \
  "silent" \
  '$REPO_ROOT/scripts/run-tests.sh'

run_case "gh --body-file absolute value is silent" \
  "silent" \
  "gh pr create --body-file /tmp/body.md"

# ---------------------------------------------------------------------------
# === SILENT: cd precedes the relative exec in the same command ===
# ---------------------------------------------------------------------------

run_case "cd then relative exec is silent" \
  "silent" \
  "cd /tmp && ./scripts/foo.sh"

run_case "cd then multiple segments then relative exec is silent" \
  "silent" \
  "cd /tmp && git status && ./scripts/foo.sh"

run_case "pushd then relative exec is silent" \
  "silent" \
  "pushd /tmp && ./scripts/foo.sh"

# ---------------------------------------------------------------------------
# === SILENT: relative exec BEFORE cd still fires (cd doesn't retroactively help) ===
# ---------------------------------------------------------------------------

run_case "relative exec before cd still asks" \
  "ask:scripts/foo.sh" \
  "./scripts/foo.sh && cd /tmp"

# ---------------------------------------------------------------------------
# === SILENT: bare commands / non-path arguments ===
# ---------------------------------------------------------------------------

run_case "bare command with no slash is silent" \
  "silent" \
  "ls -la && git status"

run_case "PATH-searched command with args is silent" \
  "silent" \
  "python3 -m pytest tests/"

# ---------------------------------------------------------------------------
# === SILENT: single worktree — no ambiguity ===
# ---------------------------------------------------------------------------

run_case "relative exec silent with only 1 registered worktree" \
  "silent" \
  "./scripts/foo.sh" \
  "$SINGLE_BASE/solo"

# ---------------------------------------------------------------------------
# === SILENT: opt-out marker ===
# ---------------------------------------------------------------------------

run_case "opt-out marker suppresses advisory" \
  "silent" \
  "./scripts/foo.sh # cwd-advisory:ack"

# ---------------------------------------------------------------------------
# === SILENT: non-Bash tool / malformed payload ===
# ---------------------------------------------------------------------------

out=$(echo '{"tool_name": "Read", "tool_input": {"file_path": "./scripts/foo.sh"}}' | python3 "$HOOK" 2>/dev/null)
rc=$?
if [ "$rc" -eq 0 ] && [ -z "$out" ]; then
  echo "PASS  [non-Bash tool is silent]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [non-Bash tool is silent] rc=$rc out=${out:-<empty>}"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("non-Bash tool is silent")
fi

out=$(echo 'not json' | python3 "$HOOK" 2>/dev/null)
rc=$?
if [ "$rc" -eq 0 ] && [ -z "$out" ]; then
  echo "PASS  [malformed JSON fails open]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [malformed JSON fails open] rc=$rc out=${out:-<empty>}"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("malformed JSON fails open")
fi

# ---------------------------------------------------------------------------
# === SILENT: non-git cwd ===
# ---------------------------------------------------------------------------

NON_GIT_DIR=$(mktemp -d)
run_case "non-git cwd is silent" \
  "silent" \
  "./scripts/foo.sh" \
  "$NON_GIT_DIR"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

git -C "$MULTI_BASE/main" worktree remove --force "$MULTI_BASE/other" >/dev/null 2>&1
rm -rf "$MULTI_BASE" "$SINGLE_BASE" "$NON_GIT_DIR"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "== $PASS passed, $FAIL failed =="
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
  exit 1
fi
exit 0
