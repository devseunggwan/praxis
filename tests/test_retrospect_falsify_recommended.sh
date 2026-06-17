#!/bin/bash
# tests/test_retrospect_falsify_recommended.sh — Stage 3 Pre-Output Falsification Gate
#
# Regression test for issue #228 (parent #227): the retrospect skill's Stage 3
# MUST require an explicit premise-falsification step before any `(Recommended)`
# or confidence-anchoring label, AND must carry Stage 2 caveats forward into the
# per-finding plan.
#
# Memory-only enforcement of this rule has failed historically — retrieval at
# Stage 3 output time does not fire reliably. The Stage 3 reference owns the gate;
# this test pins the gate's language in place so prompt drift surfaces in CI.
#
# Run:  ./tests/test_retrospect_falsify_recommended.sh
# Exit: 0 on success, 1 on first failure (after summary).

set +e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SPEC="$REPO_ROOT/skills/retrospect/references/stage3-reporting.md"

if [ ! -f "$SPEC" ]; then
  echo "FAIL: stage3-reporting.md not found at $SPEC" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

assert_present() {
  local name="$1"
  local pattern="$2"
  local hits
  hits=$(grep -c -- "$pattern" "$SPEC" 2>/dev/null)
  hits=${hits:-0}
  if [ "$hits" -gt 0 ]; then
    echo "PASS: $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $name — pattern '$pattern' missing in stage3-reporting.md"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

assert_present_extended() {
  local name="$1"
  local pattern="$2"
  local hits
  hits=$(grep -E -c -- "$pattern" "$SPEC" 2>/dev/null)
  hits=${hits:-0}
  if [ "$hits" -gt 0 ]; then
    echo "PASS: $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $name — extended regex '$pattern' missing in stage3-reporting.md"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

echo "=== retrospect Stage 3 reference falsification gate checks ==="
echo ""

# --- 1. Gate section header exists -------------------------------------------
assert_present \
  "Pre-Output Falsification Gate section header" \
  "Pre-Output Falsification Gate (AskUserQuestion)"

# --- 2. Trigger detection enumerated -----------------------------------------
assert_present \
  "trigger — literal (Recommended) label" \
  "Literal \`(Recommended)\` suffix"

assert_present \
  "trigger — English confidence-anchoring phrases" \
  "safer"

assert_present \
  "trigger — natural fit phrase" \
  "natural fit"

assert_present \
  "trigger — Korean confidence-anchoring phrase 안전한" \
  "안전한"

assert_present \
  "trigger — Korean confidence-anchoring phrase 자연스러운" \
  "자연스러운"

# --- 3. Mandatory falsification question is present verbatim -----------------
assert_present \
  "mandatory falsification question — premise wrong" \
  "If this proposal's premise is wrong"

assert_present \
  "mandatory falsification question — observation missing" \
  "what observation should be"

# --- 4. Outcome rules — three branches ---------------------------------------
assert_present \
  "outcome — premise survives" \
  "Premise survives"

assert_present \
  "outcome — premise fails" \
  "Premise fails"

assert_present \
  "outcome — falsification step not run" \
  "Falsification step not run"

assert_present \
  "outcome — (Recommended) DISALLOWED keyword" \
  "DISALLOWED"

assert_present \
  "outcome — escalate with open premise" \
  "ESCALATE to user with open premise"

# --- 5. Falsification trace line anchor (downstream parsers depend on it) ----
assert_present_extended \
  "Falsification: trace line prefix referenced" \
  '`Falsification:'

# --- 6. Stage 2 caveat carry-forward enumerated ------------------------------
assert_present \
  "carry-forward — Stage 2 caveats label" \
  "Stage 2 caveats"

assert_present \
  "carry-forward — tracer confidence" \
  "tracer confidence"

assert_present \
  "carry-forward — single observation" \
  "single observation"

assert_present \
  "carry-forward — alternative root cause not ruled out" \
  "alternative root cause not ruled out"

assert_present \
  "carry-forward — Gate-3 (c) downgrade" \
  "Gate-3 (c) downgrade applied"

assert_present \
  "carry-forward — Gate-3 (b) demotion" \
  "Gate-3 (b) sibling demoted to trigger"

assert_present \
  "carry-forward — analyst cluster overlap" \
  "analyst clustered with"

# --- 7. Stage 3 escalation and no-pattern audit contracts --------------------
assert_present \
  "no-pattern audit — enumerate before empty fence" \
  "Enumerate-before-empty-fence"

assert_present \
  "no-pattern audit — review-bot marker enumeration" \
  "codex/review-bot finding"

assert_present \
  "trigger section — Gate-3 demotion format" \
  "Finding #N: file"

assert_present \
  "action ladder — all six action types" \
  "evaluate all six action types"

assert_present \
  "action ladder — repeat count precedence" \
  "Repeat-count is the highest-priority axis"

# --- 8. Composition between caveats and gate (LOW confidence blocks rec) ----
assert_present \
  "caveat composition — LOW confidence blocks survives" \
  "tracer confidence: LOW"

# --- 9. Red Flag entry exists ------------------------------------------------
assert_present \
  "Red Flag — AskUserQuestion without Falsification trace" \
  "without an accompanying \`Falsification:\` trace line"

assert_present \
  "Red Flag — Stage 3 ranking contradicts Stage 2 caveats" \
  "Stage 3 ranking that contradicts Stage 2 caveats"

assert_present \
  "Red Flag — omitting Stage 2 caveats line" \
  "Omitting the \`Stage 2 caveats:\` line"

# --- 10. Quick Reference updated ---------------------------------------------
assert_present \
  "Quick Reference — Pre-Output Falsification Gate mentioned" \
  "Pre-Output Falsification Gate before each"

# --- 11. Error Handling rows for the gate ------------------------------------
assert_present \
  "Error Handling — gate triggered but premise un-falsifiable" \
  "Pre-Output Falsification Gate triggered but premise cannot be falsified"

assert_present \
  "Error Handling — missing caveat line blocks Stage 3 emission" \
  "lacks Stage 2 caveats line"

# --- 12. Example block demonstrates suppressed (Recommended) -----------------
assert_present \
  "example — gate-suppressed (Recommended) demonstration" \
  "gate-suppressed"

echo ""
echo "=== summary ==="
echo "PASS: $PASS"
echo "FAIL: $FAIL"

if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
  exit 1
fi

exit 0
