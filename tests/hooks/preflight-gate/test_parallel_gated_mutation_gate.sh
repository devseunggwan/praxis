#!/usr/bin/env bash
# test_parallel_gated_mutation_gate.sh — coverage for
# hooks/preflight-gate/parallel-gated-mutation-gate/impl.py
#
# Synthesizes Claude Code PostToolBatch payloads and asserts:
#   block → exit 2 + stderr mentions the rule name
#   pass  → exit 0
#
# The payload shape is the MEASURED one (`tool_calls`, entries keyed
# `tool_name`/`tool_input`/`tool_response`/`tool_use_id`), not the shape the
# published hooks reference describes. One case pins that difference: a payload
# using the documented `tools` name must NOT satisfy the gate by accident.
#
# Usage: bash tests/hooks/preflight-gate/test_parallel_gated_mutation_gate.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
HOOK="$ROOT_DIR/hooks/preflight-gate/parallel-gated-mutation-gate/impl.py"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable: $HOOK" >&2
  exit 1
fi

PASS=0; FAIL=0; FAILED_NAMES=()
_case_n=0

# build_payload <field> <command>... — each command becomes one Bash entry.
build_payload() {
  python3 -c '
import json, sys
field = sys.argv[1]
calls = [
    {
        "tool_name": "Bash",
        "tool_input": {"command": c},
        "tool_response": "",
        "tool_use_id": f"tu_{i}",
    }
    for i, c in enumerate(sys.argv[2:])
]
print(json.dumps({
    "hook_event_name": "PostToolBatch",
    "session_id": "sess-test",
    "cwd": "/tmp",
    field: calls,
}))
' "$@"
}

# run_case <name> <expect: block|pass> <field> <command>...
run_case() {
  local name="$1" expect="$2" field="$3"
  shift 3
  local out rc ok=1
  out=$(build_payload "$field" "$@" \
    | env -u PRAXIS_HOOK_BYPASS_PARALLEL_MUTATION python3 "$HOOK" 2>&1 1>/dev/null)
  rc=$?
  case "$expect" in
    block) { [ "$rc" -eq 2 ] && [[ "$out" == *"PARALLEL GATED MUTATION"* ]]; } || ok=0 ;;
    pass)  [ "$rc" -eq 0 ] || ok=0 ;;
  esac
  if [ "$ok" -eq 1 ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] (rc=$rc, stderr=$out)"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# --- the recorded harm: two creations in one batch (#98/#99) ------------------
_case_n=$((_case_n + 1))
run_case "$_case_n block: two gh issue create in one batch" block tool_calls \
  "gh issue create --title a --body-file /tmp/a.md" \
  "gh issue create --title b --body-file /tmp/b.md"

_case_n=$((_case_n + 1))
run_case "$_case_n block: two gh pr comment on the SAME pr" block tool_calls \
  "gh pr comment 12 --body one" \
  "gh pr comment 12 --body two"

_case_n=$((_case_n + 1))
run_case "$_case_n block: duplicate creations serially in ONE call" block tool_calls \
  "gh issue create --title a && gh issue create --title b"

# --- legitimate parallel work must pass --------------------------------------
_case_n=$((_case_n + 1))
run_case "$_case_n pass: comments on two DIFFERENT prs" pass tool_calls \
  "gh pr comment 12 --body one" \
  "gh pr comment 34 --body two"

_case_n=$((_case_n + 1))
run_case "$_case_n pass: an issue and its pr" pass tool_calls \
  "gh issue create --title a" \
  "gh pr create --title a"

_case_n=$((_case_n + 1))
run_case "$_case_n pass: two reads" pass tool_calls \
  "gh issue list --limit 5" \
  "gh pr list --limit 5"

_case_n=$((_case_n + 1))
run_case "$_case_n pass: single creation" pass tool_calls \
  "gh issue create --title a"

_case_n=$((_case_n + 1))
run_case "$_case_n pass: non-gh commands" pass tool_calls \
  "echo alpha" "echo beta"

_case_n=$((_case_n + 1))
run_case "$_case_n pass: two --help invocations create nothing" pass tool_calls \
  "gh issue create --help" \
  "gh issue create --help"

# --- regressions found by adversarial review, each a silent pass -------------
# Every case below returned "no finding" before the fix, which is
# indistinguishable from a clean batch. Controls for all four sit above.

_case_n=$((_case_n + 1))
run_case "$_case_n block: persistent flag BEFORE the noun" block tool_calls \
  "gh --repo o/r issue create --title a" \
  "gh --repo o/r issue create --title b"

_case_n=$((_case_n + 1))
run_case "$_case_n block: persistent flag BETWEEN noun and verb" block tool_calls \
  "gh issue --repo o/r create --title a" \
  "gh issue --repo o/r create --title b"

_case_n=$((_case_n + 1))
run_case "$_case_n block: same target set, different argument order" block tool_calls \
  "gh issue edit 23 34 --add-label x" \
  "gh issue edit 34 23 --add-label y"

_case_n=$((_case_n + 1))
run_case "$_case_n block: issue URL and bare number are one record" block tool_calls \
  "gh issue edit https://github.com/o/r/issues/42 --add-label x" \
  "gh issue edit 42 --add-label y"

_case_n=$((_case_n + 1))
run_case "$_case_n block: -h consumed as a --subject VALUE is not help" block tool_calls \
  "gh pr merge 123 --subject -h --squash" \
  "gh pr merge 123 --subject -h --admin"

_case_n=$((_case_n + 1))
run_case "$_case_n block: pr revert (absent from the old allowlist)" block tool_calls \
  "gh pr revert 42" "gh pr revert 42"

_case_n=$((_case_n + 1))
run_case "$_case_n block: issue transfer (absent from the old allowlist)" block tool_calls \
  "gh issue transfer 5 o/r" "gh issue transfer 5 o/r"

_case_n=$((_case_n + 1))
run_case "$_case_n pass: edits on disjoint target sets" pass tool_calls \
  "gh issue edit 1 2 --add-label x" \
  "gh issue edit 3 4 --add-label y"

# --- regressions found by the second review round, each a FALSE POSITIVE ------
# The block above fixed silent passes; these three went the other way and
# blocked legitimate parallel work. Blocking controls for all of them follow.

_case_n=$((_case_n + 1))
run_case "$_case_n pass: same number in two DIFFERENT repos (URL form)" pass tool_calls \
  "gh issue edit https://github.com/org-a/repo-a/issues/42 --add-label x" \
  "gh issue edit https://github.com/org-b/repo-b/issues/42 --add-label y"

_case_n=$((_case_n + 1))
run_case "$_case_n pass: same number in two DIFFERENT repos (--repo form)" pass tool_calls \
  "gh issue edit --repo org-a/repo-a 42 --add-label x" \
  "gh issue edit --repo org-b/repo-b 42 --add-label y"

_case_n=$((_case_n + 1))
run_case "$_case_n pass: targets sit AFTER the persistent flag" pass tool_calls \
  "gh issue edit --repo org/repo 1 --add-label x" \
  "gh issue edit --repo org/repo 2 --add-label y"

_case_n=$((_case_n + 1))
run_case "$_case_n pass: creations in two different repos" pass tool_calls \
  "gh issue create --repo a/x --title p" \
  "gh issue create --repo b/y --title q"

_case_n=$((_case_n + 1))
run_case "$_case_n block: same repo and same number, targets after --repo" block tool_calls \
  "gh issue edit --repo org/repo 1 --add-label x" \
  "gh issue edit --repo org/repo 1 --add-label y"

_case_n=$((_case_n + 1))
run_case "$_case_n block: a URL scope and an unscoped number still collide" block tool_calls \
  "gh issue edit https://github.com/o/r/issues/42 --add-label x" \
  "gh issue edit 42 --add-label y"

# --- the documented-but-wrong field name must not satisfy the gate -----------
# Positive control: the SAME two commands under `tool_calls` block (case 1
# above). Under the reference's `tools` name the gate sees an empty batch, so a
# pass here proves the field name is load-bearing rather than incidental.
_case_n=$((_case_n + 1))
run_case "$_case_n pass: duplicate creations under the documented 'tools' name" \
  pass tools \
  "gh issue create --title a" \
  "gh issue create --title b"

# --- fail-open on a malformed payload ----------------------------------------
_out=$(printf 'not json' | python3 "$HOOK" 2>&1 1>/dev/null)
_rc=$?
if [ "$_rc" -eq 0 ]; then
  echo "PASS  [malformed payload fails open]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [malformed payload fails open] (rc=$_rc, stderr=$_out)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("malformed payload")
fi

# --- bypass env ---------------------------------------------------------------
_out=$(build_payload tool_calls "gh issue create --title a" "gh issue create --title b" \
  | PRAXIS_HOOK_BYPASS_PARALLEL_MUTATION=1 python3 "$HOOK" 2>&1 1>/dev/null)
_rc=$?
if [ "$_rc" -eq 0 ]; then
  echo "PASS  [bypass env var set]"
  PASS=$((PASS + 1))
else
  echo "FAIL  [bypass env var set] (rc=$_rc, stderr=$_out)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("bypass env var")
fi

# --- summary -----------------------------------------------------------------
echo ""
echo "Passed: $PASS  Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed tests:"
  for t in "${FAILED_NAMES[@]}"; do
    echo "  - $t"
  done
  exit 1
fi
exit 0
