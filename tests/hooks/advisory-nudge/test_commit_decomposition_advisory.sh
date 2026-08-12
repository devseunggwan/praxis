#!/bin/bash
# test_commit_decomposition_advisory.sh — coverage for
# hooks/advisory-nudge/commit-decomposition-advisory/impl.py
#
# The hook is argv-only (no git subprocess, no transcript), so every case is a
# synthesized PreToolUse(Bash) payload piped into the hook:
#   surface:<marker>  — exit 0, stderr contains <marker>
#   silent            — exit 0, stderr empty
#
# Case list follows the pre-implementation surface enumeration recorded in
# spec.md: argv-shape variants (glued -m, --message=, global value flags,
# path-invoked git, compound segments, exempt flags) and signal variants
# (bullet threshold boundary, bullet marker forms, title-vs-body scope,
# type disagreement, substring-vs-prefix false positives).
#
# Usage: bash tests/hooks/advisory-nudge/test_commit_decomposition_advisory.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../../" && pwd)"
HOOK="$ROOT_DIR/hooks/advisory-nudge/commit-decomposition-advisory/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

record() {
  local name="$1" ok="$2" rc="$3" err="$4" expectation="$5"
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expectation=$expectation rc=$rc stderr=${err:-<empty>}"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# run_case name expectation command [env_assign]
run_case() {
  local name="$1" expectation="$2" command="$3" env_assign="${4:-}"
  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({"tool_name": "Bash", "tool_input": {"command": sys.argv[1]}}))
' "$command")

  local err_file rc err
  err_file=$(mktemp)
  if [ -n "$env_assign" ]; then
    echo "$payload" | env "$env_assign" "$HOOK" >/dev/null 2>"$err_file"
  else
    echo "$payload" | "$HOOK" >/dev/null 2>"$err_file"
  fi
  rc=$?
  err=$(cat "$err_file"); rm -f "$err_file"

  local ok=1
  case "$expectation" in
    silent)
      [ "$rc" -eq 0 ] || ok=0
      [ -z "$err" ]   || ok=0
      ;;
    surface:*)
      local marker="${expectation#surface:}"
      [ "$rc" -eq 0 ] || ok=0
      case "$err" in *"$marker"*) ;; *) ok=0 ;; esac
      ;;
    *)
      echo "FAIL  [$name] unknown expectation: $expectation"
      FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name"); return
      ;;
  esac
  record "$name" "$ok" "$rc" "$err" "$expectation"
}

THREE=$'- one\n- two\n- three'
TWO=$'- one\n- two'

# ---------------------------------------------------------------------------
# Bullet-count signal
# ---------------------------------------------------------------------------
run_case "three bullets in body surfaces" "surface:[commit-decomp]" \
  "git commit -m 'fix(x): a' -m '$THREE'"
run_case "three bullets names the count" "surface:3 bullets" \
  "git commit -m 'fix(x): a' -m '$THREE'"
run_case "two bullets is below the threshold" silent \
  "git commit -m 'fix(x): a' -m '$TWO'"
run_case "star bullets count" "surface:[commit-decomp]" \
  "git commit -m 'fix(x): a' -m '$(printf '* one\n* two\n* three')'"
run_case "dash without a space is not a bullet" silent \
  "git commit -m 'fix(x): a' -m '$(printf -- '-one\n-two\n-three')'"
run_case "bullets in the title are not body bullets" silent \
  "git commit -m '$THREE'"
run_case "korean body bullets count" "surface:[commit-decomp]" \
  "git commit -m 'fix(x): a' -m '$(printf -- '- 하나\n- 둘\n- 셋')'"

# ---------------------------------------------------------------------------
# Type-disagreement signal
# ---------------------------------------------------------------------------
run_case "title fix vs body refactor surfaces" "surface:also says \`refactor\`" \
  "git commit -m 'fix(x): a' -m 'refactor: pulled the helper out'"
run_case "same type in body is no disagreement" silent \
  "git commit -m 'fix(x): a' -m 'fix: also the sibling'"
run_case "body type behind a bullet is detected" "surface:[commit-decomp]" \
  "git commit -m 'feat: a' -m '- refactor: extracted'"
run_case "breaking-marker title parses its type" "surface:[commit-decomp]" \
  "git commit -m 'feat!: a' -m 'docs: readme too'"
run_case "untyped title cannot disagree" silent \
  "git commit -m 'just a message' -m 'refactor: pulled out'"
run_case "type-like longer word does not match" silent \
  "git commit -m 'fix(x): a' -m 'features: are listed below'"
run_case "type mid-line is not a line-start prefix" silent \
  "git commit -m 'fix(x): a' -m 'the refactor: was unavoidable'"

# ---------------------------------------------------------------------------
# argv shapes
# ---------------------------------------------------------------------------
run_case "glued -m form" "surface:[commit-decomp]" \
  "git commit -m'fix: a' -m'$THREE'"
run_case "--message= form" "surface:[commit-decomp]" \
  "git commit --message='fix: a' --message='$THREE'"
run_case "git -C skips the global value flag" "surface:[commit-decomp]" \
  "git -C /tmp/repo commit -m 'fix: a' -m '$THREE'"
run_case "path-invoked git binary" "surface:[commit-decomp]" \
  "/usr/bin/git commit -m 'fix: a' -m '$THREE'"
run_case "second segment of a compound command" "surface:[commit-decomp]" \
  "git add -A && git commit -m 'fix: a' -m '$THREE'"
run_case "git --help commit runs no commit" silent \
  "git --help commit -m 'fix: a' -m '$THREE'"
run_case "another subcommand is not commit" silent \
  "git rebase -m 'fix: a' -m '$THREE'"

# ---------------------------------------------------------------------------
# Exempt flags and opt-outs
# ---------------------------------------------------------------------------
run_case "--amend is not an authoring point" silent \
  "git commit --amend -m 'fix: a' -m '$THREE'"
run_case "--dry-run commits nothing" silent \
  "git commit --dry-run -m 'fix: a' -m '$THREE'"
run_case "-F takes the message off argv" silent \
  "git commit -F /tmp/msg"
run_case "--fixup= equals-form is exempt" silent \
  "git commit --fixup=abc123 -m 'fix: a' -m '$THREE'"
run_case "an exempt flag NAMED IN THE MESSAGE does not silence" "surface:[commit-decomp]" \
  "git commit -m 'fix: a' -m '--amend is scary' -m '$THREE'"
run_case "no -m at all is the editor flow" silent \
  "git commit"
run_case "ack marker silences" silent \
  "git commit -m 'fix: a' -m '$THREE' # [commit-decomp-ack]"
run_case "env bypass silences" silent \
  "git commit -m 'fix: a' -m '$THREE'" \
  "PRAXIS_SKIP_COMMIT_DECOMPOSITION_ADVISORY=1"

# ---------------------------------------------------------------------------
# Advisory content: the axis direction must be stated, or the hook converts a
# commit-axis defect into tracker fragmentation.
# ---------------------------------------------------------------------------
run_case "advisory names the commit axis only" "surface:COMMIT axis only" \
  "git commit -m 'fix(x): a' -m '$THREE'"

# ---------------------------------------------------------------------------
# Fail-open
# ---------------------------------------------------------------------------
_err_file=$(mktemp)
echo 'not json' | "$HOOK" >/dev/null 2>"$_err_file"
_rc=$?
_err=$(cat "$_err_file"); rm -f "$_err_file"
if [ "$_rc" -eq 0 ] && [ -z "$_err" ]; then
  record "malformed stdin fails open" 1 "$_rc" "$_err" silent
else
  record "malformed stdin fails open" 0 "$_rc" "$_err" silent
fi

_err_file=$(mktemp)
echo '{"tool_name":"Write","tool_input":{"file_path":"/tmp/x"}}' | "$HOOK" >/dev/null 2>"$_err_file"
_rc=$?
_err=$(cat "$_err_file"); rm -f "$_err_file"
if [ "$_rc" -eq 0 ] && [ -z "$_err" ]; then
  record "non-Bash tool is silent" 1 "$_rc" "$_err" silent
else
  record "non-Bash tool is silent" 0 "$_rc" "$_err" silent
fi

# main() must be wrapped by the shared @fail_open guard.
if grep -q '^@fail_open' "$HOOK"; then
  record "main() is wrapped by the shared @fail_open guard" 1 0 "" surface
else
  record "main() is wrapped by the shared @fail_open guard" 0 1 "" surface
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  echo "Failed:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
fi

[ "$FAIL" -eq 0 ]
