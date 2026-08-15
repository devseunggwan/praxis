#!/usr/bin/env bash
# test_codex_review_wrap_version_pins.sh — issue #990
#
# The codex-review-wrap Step 4b and Liveness sections assert facts measured
# against a specific codex-companion build. Without a version pin a reader
# cannot tell a still-true claim from a stale one. This test pins both
# directions:
#
#   MUST FIRE   — Step 4b and Liveness each name codex@openai-codex 1.0.6 and
#                 say what a version rise would require; Step 4b names
#                 adversarial-review as sharing handleReviewCommand.
#   MUST STAY SILENT — no section claims --background does anything for
#                 review, and no stray version other than 1.0.6 is pinned.
#
# Usage: bash tests/test_codex_review_wrap_version_pins.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="${PRAXIS_TEST_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
SKILL="$ROOT_DIR/skills/codex-review-wrap/SKILL.md"

PASS=0
FAIL=0

if [ ! -f "$SKILL" ]; then
  echo "FAIL: SKILL.md not found: $SKILL" >&2
  exit 1
fi

# Section slicing. Liveness is a "##### " subsection nested inside 4b, so the
# 4b slice must stop at it — otherwise 4b's pin assertion would be satisfied by
# Liveness's pin and neither section would be independently required.
step4b="$(awk '/^#### 4b\./{f=1} f&&/^##### Liveness/{exit} f&&/^#### /&&!/^#### 4b\./{exit} f' "$SKILL")"
liveness="$(awk '/^##### Liveness/{f=1} f&&/^#### /{exit} f' "$SKILL")"

assert_match() {
  local name="$1" hay="$2" pat="$3"
  if printf '%s' "$hay" | grep -qE -- "$pat"; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected to match: $pat"; FAIL=$((FAIL + 1))
  fi
}

assert_no_match() {
  local name="$1" hay="$2" pat="$3"
  if printf '%s' "$hay" | grep -qE -- "$pat"; then
    echo "FAIL  [$name] expected NO match, found: $pat"
    printf '%s' "$hay" | grep -nE -- "$pat" | head -5
    FAIL=$((FAIL + 1))
  else
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  fi
}

# --- harness positive control: the slicers actually captured the sections ---
assert_match "control/step4b-slice-nonempty" "$step4b" 'handleReviewCommand'
assert_match "control/liveness-slice-nonempty" "$liveness" 'runTrackedJob'

# --- MUST FIRE ---
assert_match "step4b/version-pin" "$step4b" 'codex@openai-codex 1\.0\.6'
assert_match "step4b/revisit-condition" "$step4b" '(re-measur|Re-measur)'
assert_match "step4b/adversarial-shares-path" "$step4b" 'adversarial-review'
assert_match "liveness/version-pin" "$liveness" 'codex@openai-codex 1\.0\.6'
assert_match "liveness/revisit-condition" "$liveness" '(re-measur|Re-measur)'

# --- MUST STAY SILENT ---
# The whole point of Step 4b is that --background is a parser-only no-op for
# review. A future edit must not quietly promote it to a working flag.
assert_no_match "step4b/no-working-background-flag" "$step4b" '`--background`[^.]*(detach|backgrounds the review|works for review)'
# One pinned version only — catches a half-applied bump that leaves two.
for sec_name in step4b liveness; do
  sec="$step4b"; [ "$sec_name" = liveness ] && sec="$liveness"
  assert_no_match "$sec_name/no-other-pinned-version" "$sec" 'codex@openai-codex (0|1\.0\.[0-57-9]|1\.[1-9])'
done

echo
echo "Passed: $PASS  Failed: $FAIL"
[ "$FAIL" -eq 0 ]
