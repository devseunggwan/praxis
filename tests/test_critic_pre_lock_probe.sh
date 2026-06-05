#!/usr/bin/env bash
# tests/test_critic_pre_lock_probe.sh — Step 5g critic pre-lock probe gate
#
# Regression test for issue #346 (Phase 1):
#   codex-review-wrap SKILL.md Step 5g must require a live probe citation
#   whenever the critic emits a negative claim ("X is fabricated", "X does
#   not exist", etc.).  Memory-only retrieval has historically failed — the
#   gate language must be pinned in the SKILL.md itself.
#
# All assertions are static document checks against SKILL.md.  No hook
# binary is required — the test validates that the specification text is
# present and correct so that prompt drift surfaces in CI.
#
# Run:  bash tests/test_critic_pre_lock_probe.sh
# Exit: 0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILL="$ROOT_DIR/skills/codex-review-wrap/SKILL.md"

if [ ! -f "$SKILL" ]; then
  echo "FAIL: SKILL.md not found at $SKILL" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# Assert that a fixed-string pattern appears at least once in SKILL.md.
assert_present() {
  local name="$1"
  local pattern="$2"
  local hits
  hits=$(grep -Fc -- "$pattern" "$SKILL" 2>/dev/null)
  hits=${hits:-0}
  if [ "$hits" -gt 0 ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] — pattern not found: $pattern"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

# Assert that an extended-regex pattern appears at least once in SKILL.md.
assert_present_re() {
  local name="$1"
  local pattern="$2"
  local hits
  hits=$(grep -Ec -- "$pattern" "$SKILL" 2>/dev/null)
  hits=${hits:-0}
  if [ "$hits" -gt 0 ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] — regex not matched: $pattern"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

# Assert that a fixed-string pattern appears ZERO times (must be absent).
assert_absent() {
  local name="$1"
  local pattern="$2"
  local hits
  hits=$(grep -Fc -- "$pattern" "$SKILL" 2>/dev/null)
  hits=${hits:-0}
  if [ "$hits" -eq 0 ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] — pattern must be absent but found $hits time(s): $pattern"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

# ---------------------------------------------------------------------------
# 1. Execution order — 5g must appear in the numbered execution list
# ---------------------------------------------------------------------------

assert_present \
  "execution-order lists 5g" \
  "5g critic pre-lock probe check"

# ---------------------------------------------------------------------------
# 2. Negative-claim trigger forms — English variants
# ---------------------------------------------------------------------------

assert_present \
  "negative-claim: 'X is fabricated' English" \
  '"X is fabricated"'

assert_present \
  "negative-claim: 'X does not exist' English" \
  '"X does not exist"'

assert_present \
  "negative-claim: 'X is unused' English" \
  '"X is unused"'

assert_present \
  "negative-claim: 'X has no runtime effect' English" \
  '"X has no runtime effect"'

assert_present \
  "negative-claim: 'X is missing from {file/scope}' English" \
  '"X is missing from {file/scope}"'

# ---------------------------------------------------------------------------
# 3. Negative-claim trigger forms — Korean variants
# ---------------------------------------------------------------------------

assert_present \
  "negative-claim: Korean 존재하지 않음 variant" \
  "X 는 존재하지 않음"

assert_present \
  "negative-claim: Korean 없음 short form" \
  "X 는 없음"

assert_present \
  "negative-claim: Korean 사용되지 않음 variant" \
  "X 는 사용되지 않음"

assert_present \
  "negative-claim: Korean runtime effect 없음 variant" \
  "X 는 runtime effect 가 없음"

# ---------------------------------------------------------------------------
# 4. Probe citation format spec
# ---------------------------------------------------------------------------

assert_present \
  "probe citation format keyword 'Probe:'" \
  "Probe: <command>"

assert_present \
  "probe citation one-line output spec" \
  "one-line output"

assert_present \
  "probe re-run requirement (no cached knowledge)" \
  "re-run at the"

# ---------------------------------------------------------------------------
# 5. Cached-knowledge exemption is explicitly rejected
# ---------------------------------------------------------------------------

assert_present \
  "cached knowledge not a valid substitute" \
  '"I already read this file earlier"'

# ---------------------------------------------------------------------------
# 6. Critic prompt template block is present
# ---------------------------------------------------------------------------

assert_present \
  "critic prompt template header present" \
  "CRITIC PRE-LOCK PROBE GATE (mandatory)"

assert_present \
  "critic prompt template negative-claim list present" \
  '  - "X is fabricated"'

assert_present \
  "critic prompt template example probe present" \
  "Probe: grep -n PRAXIS_ASK_END_STRICT"

# ---------------------------------------------------------------------------
# 7. Worked example F2 (PRAXIS_ASK_END_STRICT fabrication claim) is present
# ---------------------------------------------------------------------------

assert_present \
  "worked example F2: PRAXIS_ASK_END_STRICT scenario described" \
  "PRAXIS_ASK_END_STRICT is a fabricated"

assert_present \
  "worked example F2: probe command shown" \
  "grep -n PRAXIS_ASK_END_STRICT hooks/preflight-gate/block-ask-end-option/impl.py"

# ---------------------------------------------------------------------------
# 8. Worked example F1 (git boolean-flag fix) is present
# ---------------------------------------------------------------------------

assert_present \
  "worked example F1: --literal-pathspecs / --super-prefix scenario described" \
  "--literal-pathspecs"

assert_present \
  "worked example F1: --super-prefix mentioned as taking a value" \
  "--super-prefix"

# ---------------------------------------------------------------------------
# 9. Error handling table covers 5g
# ---------------------------------------------------------------------------

assert_present \
  "error-handling: probe missing → halt finding" \
  "Halt the finding; prompt the critic to re-run with probe citation"

# ---------------------------------------------------------------------------
# 10. Regression — negative-claim WITHOUT probe citation must be flagged
#
#     This test checks that the SKILL.md spec explicitly states the gate
#     halts a claim that lacks a Probe: citation — simulating the scenario
#     where a critic outputs "X is fabricated" with no accompanying probe.
# ---------------------------------------------------------------------------

assert_present \
  "spec: negative claim without Probe: citation is halted" \
  "Critic negative claim emitted without"

# ---------------------------------------------------------------------------
# 11. Retraction protocol documented
# ---------------------------------------------------------------------------

assert_present \
  "retraction: 'Retracted:' keyword in spec" \
  "Retracted:"

assert_present \
  "retraction: probe disproves → retract before surfacing" \
  "retract the claim before surfacing"

# ---------------------------------------------------------------------------
# 12. Limitations section covers 5g scope
# ---------------------------------------------------------------------------

assert_present \
  "limitations: pattern-based detection caveat present" \
  "negative-claim detection is pattern-based"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

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
