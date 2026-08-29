#!/usr/bin/env bash
# test_codex_review_wrap_version_pins.sh — issue #990
#
# The codex-review-wrap Step 4b and Liveness sections assert facts measured
# against a specific codex-companion build. Without a version pin a reader
# cannot tell a still-true claim from a stale one. This test pins both
# directions:
#
#   MUST FIRE   — Step 4b and Liveness each bind codex@openai-codex 1.0.6 to
#                 its re-measure condition in one block; Step 4b names
#                 adversarial-review as sharing handleReviewCommand, and states
#                 --background as a no-op for review.
#   MUST STAY SILENT — no stray version other than 1.0.6 is pinned.
#   INVERSION   — fixtures that keep every keyword and say the opposite are
#                 rejected, so the predicates read the policy, not the terms.
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
# Stops at the next heading of ITS OWN level too, not only at the next `####`.
# Ending only at `####` ran the slice 146 lines to `#### 5a.`, swallowing the
# sibling `##### When the review completes` and `##### Execution order` — so
# deleting Liveness's own pin and putting the same words in either sibling kept
# both liveness assertions green.
liveness="$(awk '/^##### Liveness/{f=1;print;next} f&&/^#{4,5} /{exit} f' "$SKILL")"

assert_match() {
  local name="$1" hay="$2" pat="$3"
  if printf '%s' "$hay" | grep -qE -- "$pat"; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected to match: $pat"; FAIL=$((FAIL + 1))
  fi
}

# --- relationship predicates (pure: return 0 = the section states it) ---

# A pinned version is only useful if the revisit condition travels with it.
# Matching `re-measur` anywhere in the slice bought nothing: an edit that
# scatters the pin and the condition into unrelated paragraphs keeps both
# tokens present and the assertion green. Require instead that ONE
# blank-line-separated block carries the pin, the re-measure directive, and
# the trigger that fires it.
pin_binds_revisit() {
  printf '%s\n' "$1" | awk '
    function check() {
      if (block ~ /codex@openai-codex 1\.0\.6/ && block ~ /[Rr]e-measur/ \
          && block ~ /version rises/) found = 1
    }
    /^[[:space:]]*$/ { check(); block = ""; next }
    { block = block $0 "\n" }
    END { check(); exit (found ? 0 : 1) }
  '
}

# Assert what the doc MUST say, not the phrasings it must avoid. The set of
# ways to claim `--background` works is open — `starts the review
# asynchronously` and `launches a background task` both mean it and neither
# was on the old reject list — so enumeration cannot buy this invariant. The
# set of ways to state the no-op is ours to fix, so require the clause that
# introduces `--background` (up to the next `.` or `;`) to state it.
background_clause_states_noop() {
  printf '%s' "$1" | tr '\n' ' ' \
    | grep -qE -- '`--background`[^.;]*(never read|changes nothing|neither changes anything|no-op)'
}

assert_pred() {
  local name="$1" pred="$2" hay="$3"
  if "$pred" "$hay"; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] $pred rejected the section"; FAIL=$((FAIL + 1))
  fi
}

# Inversion control. A deletion control only shows the predicate reacts to an
# absent token; it never shows the predicate reads the policy. These fixtures
# keep every keyword and say the opposite, so a predicate that passes them is
# still matching terms.
assert_pred_rejects() {
  local name="$1" pred="$2" hay="$3"
  if "$pred" "$hay"; then
    echo "FAIL  [$name] $pred accepted the inverted fixture"; FAIL=$((FAIL + 1))
  else
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  fi
}

# Pin and condition present, but in unrelated blocks — the scatter the old
# `re-measur` match could not see.
FIXTURE_SCATTERED_PIN="$(cat <<'EOF'
Measured against `codex@openai-codex 1.0.6` (`scripts/codex-companion.mjs`):
`options.background` is read at exactly one line, inside `handleTask`.

Re-measure this claim whenever the plugin version rises.
EOF
)"

# `--background` promoted to a working flag, in wording the old reject list
# never enumerated.
FIXTURE_PROMOTED_BACKGROUND="$(cat <<'EOF'
It also *accepts* `--wait` and `--background` — both sit in its
`booleanOptions`; `--background` starts the review asynchronously, and
`options.background` is read by `handleReviewCommand`.
EOF
)"

# --- harness positive control: the slicers actually captured the sections ---
assert_match "control/step4b-slice-nonempty" "$step4b" 'handleReviewCommand'
assert_match "control/liveness-slice-nonempty" "$liveness" 'runTrackedJob'

# --- MUST FIRE ---
assert_match "step4b/version-pin" "$step4b" 'codex@openai-codex 1\.0\.6'
assert_pred "step4b/pin-binds-revisit" pin_binds_revisit "$step4b"
assert_match "step4b/adversarial-shares-path" "$step4b" 'adversarial-review'
assert_match "liveness/version-pin" "$liveness" 'codex@openai-codex 1\.0\.6'
assert_pred "liveness/pin-binds-revisit" pin_binds_revisit "$liveness"

# The whole point of Step 4b is that --background is a parser-only no-op for
# review. A future edit must not quietly promote it to a working flag.
assert_pred "step4b/background-stated-no-op" background_clause_states_noop "$step4b"

# --- INVERSION CONTROLS ---
assert_pred_rejects "inversion/scattered-pin" pin_binds_revisit "$FIXTURE_SCATTERED_PIN"
assert_pred_rejects "inversion/promoted-background" background_clause_states_noop "$FIXTURE_PROMOTED_BACKGROUND"

# --- MUST STAY SILENT ---
# One pinned version only — catches a half-applied bump that leaves two.
#
# Extract every version token and require the distinct set to be exactly
# {1.0.6}, rather than enumerating the versions to reject. An enumeration has to
# predict what a future bump looks like and this one did not: `2.0.0`, `3.1.0`,
# `1.0.60` and `1.0.6-rc1` all failed to match the old pattern and so passed the
# gate silently, which is the whole invariant inverted.
assert_only_version() {
  local name="$1" hay="$2" want="$3" seen
  seen="$(printf '%s' "$hay" \
    | grep -oE 'codex@openai-codex [0-9][0-9A-Za-z.+-]*' \
    | sed 's/^codex@openai-codex //' | sort -u | tr '\n' ' ')"
  seen="${seen% }"
  if [ "$seen" = "$want" ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] version set is '$seen', want exactly '$want'"; FAIL=$((FAIL + 1))
  fi
}

for sec_name in step4b liveness; do
  sec="$step4b"; [ "$sec_name" = liveness ] && sec="$liveness"
  assert_only_version "$sec_name/no-other-pinned-version" "$sec" '1.0.6'
done

echo
echo "Passed: $PASS  Failed: $FAIL"
[ "$FAIL" -eq 0 ]
