#!/bin/bash
# test_pipefail_advisory.sh — coverage for the pipefail advisory (issue #788).
#
# Synthesizes Claude Code PreToolUse(Bash) payloads and asserts:
#   advisory → exit 0 + stderr contains the advisory marker
#   silent   → exit 0 + stderr empty
#
# Usage: bash tests/hooks/advisory-nudge/test_pipefail_advisory.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/pipefail-advisory/impl.py"

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
      echo "$err" | grep -q "\[pipefail-advisory\]" || ok=0
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

# === ADVISORY — mutating command piped to tail/head/grep ==================

run_case "git commit | tail (gen-1 pattern)" \
  advisory \
  "git commit -m x | tail"

run_case "gh pr merge 2>&1 | tail -3 (gen-2 pattern)" \
  advisory \
  "gh pr merge 123 --squash 2>&1 | tail -3"

run_case "git push 2>&1 | tail -3" \
  advisory \
  "git push origin main 2>&1 | tail -3"

run_case "git merge | tail" \
  advisory \
  "git merge feature-branch | tail -5"

run_case "git rebase | tail" \
  advisory \
  "git rebase origin/main | tail -10"

run_case "git cherry-pick | tail" \
  advisory \
  "git cherry-pick abc123 | tail"

run_case "git revert | tail" \
  advisory \
  "git revert HEAD | tail"

run_case "gh issue create | head" \
  advisory \
  "gh issue create --title x --body y | head"

run_case "gh issue close | grep" \
  advisory \
  "gh issue close 42 | grep -i done"

run_case "gh release create | tail" \
  advisory \
  "gh release create v1.0 | tail"

run_case "gh label create | tail" \
  advisory \
  "gh label create bug --color ff0000 | tail"

run_case "gh workflow run | tail" \
  advisory \
  "gh workflow run ci.yml | tail"

run_case "3-segment chain: git commit | tee | tail" \
  advisory \
  "git commit -m x | tee /tmp/log | tail -20"

run_case "git -C dir commit | tail (global flag)" \
  advisory \
  "git -C /tmp/repo commit -m x | tail"

# === ADVISORY — subshell / substitution / |& prefix recovery (codex review) ==

run_case "OUT=\$(gh pr merge ... | tail) (assignment+subst prefix)" \
  advisory \
  'OUT=$(gh pr merge 123 | tail -3)'

run_case "(git commit ... | tail) (compact subshell, bare sink)" \
  advisory \
  "(git commit -m x | tail)"

run_case "(git commit ... | tail -3) (compact subshell, sink w/ args)" \
  advisory \
  "(git commit -m x | tail -3)"

run_case "gh pr merge 1 |& tail -3 (pipe-with-stderr operator)" \
  advisory \
  "gh pr merge 1 |& tail -3"

# === ADVISORY — round-2 codex review (here-string, gh flag position, ======
# === multi-assignment prefix) ==============================================

run_case "here-string before mutating pipe is not mistaken for a heredoc" \
  advisory \
  "read x <<< value; git commit -m x | tail"

run_case "gh -R before object still finds the mutating verb" \
  advisory \
  "gh issue -R owner/repo create --body x | head"

run_case "gh --repo between object and verb still finds the mutating verb" \
  advisory \
  "gh pr --repo owner/repo merge 1 | tail"

run_case "plain env assignment before a capture assignment (LANG=C RESULT=\$(...))" \
  advisory \
  'LANG=C RESULT=$(gh pr merge 1 | tail -3)'

# === SILENT — read-only command piped (no mutation) ========================

run_case "git log | head (read-only)" \
  silent \
  "git log --oneline | head -20"

run_case "git status | grep (read-only)" \
  silent \
  "git status | grep modified"

run_case "git diff | tail (read-only)" \
  silent \
  "git diff | tail -20"

run_case "gh pr list | head (read-only)" \
  silent \
  "gh pr list | head -5"

run_case "gh pr view | grep (read-only)" \
  silent \
  "gh pr view 1 | grep merged"

run_case "gh issue list | tail (read-only)" \
  silent \
  "gh issue list | tail -10"

# === SILENT — mutating command piped to a non-truncating sink ==============

run_case "git commit | cat (non-truncating sink)" \
  silent \
  "git commit -m x | cat"

run_case "git push | sort (non-truncating sink)" \
  silent \
  "git push origin main | sort"

run_case "gh pr merge | wc -l (non-truncating sink)" \
  silent \
  "gh pr merge 1 | wc -l"

# === SILENT — no pipe (different separator / single command) ==============

run_case "git commit && git push (&& is a different defect class)" \
  silent \
  "git commit -m x && git push"

run_case "git commit ; git push (; sequencing)" \
  silent \
  "git commit -m x ; git push"

run_case "git commit || echo failed (|| fallback)" \
  silent \
  "git commit -m x || echo failed"

run_case "single git commit (no chain)" \
  silent \
  "git commit -m x"

run_case "grep | sort | uniq (no mutating segment)" \
  silent \
  "grep X foo | sort | uniq"

# === SILENT — false-positive surfaces (issue #788 requirement) ============

run_case "quoted-string literal pipe in --body" \
  silent \
  'gh issue create --title x --body "example: git commit -m x | tail -3"'

run_case "heredoc body containing mutating+pipe example text" \
  silent \
  "$(printf 'BODY=$(cat <<'"'"'EOF'"'"'\nExample of the bug: git commit -m x | tail -3\nEOF\n)\ngh issue create --title x --body "$BODY"')"

run_case "heredoc body line starting with marker word but not alone on the line (EOF-not-end guard)" \
  silent \
  "$(printf 'cat <<EOF\ngit commit | tail\nEOF not-end\nreal body line: git push | tail -1\nEOF')"

# === Fail-open infrastructure =============================================

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

# ---------------------------------------------------------------------------
# @fail_open structural assertion
# ---------------------------------------------------------------------------

_uncaught_out=$(python3 - << PYEOF 2>&1
import sys, importlib.util
spec = importlib.util.spec_from_file_location("impl", "$HOOK")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
if getattr(mod.main, "__wrapped__", None) is None:
    sys.stderr.write("main not wrapped by @fail_open\n"); sys.exit(1)
PYEOF
)
_uncaught_rc=$?
if [ "$_uncaught_rc" -eq 0 ] && [ -z "$_uncaught_out" ]; then
  echo "  PASS  main() is wrapped by the shared @fail_open guard (exit 0, no stderr)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  main() not wrapped by @fail_open (rc=$_uncaught_rc, out=$(echo "$_uncaught_out" | head -c 200))"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("main() is wrapped by the shared @fail_open guard")
fi

# === Summary ==============================================================

echo
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
