#!/bin/bash
# tests/test_retrospect_cursor_concurrency.sh — Stage 1.5 cursor write mandate
#
# Regression test for issue #568: the retrospect Stage 1.5 hygiene cursor
# (`.omc/state/retrospect-hygiene-cursor.json`) is shared mutable state. Two
# sessions running /retrospect concurrently both read the same cursor and the
# second session's plain Write clobbers the first session's `note` carry-forward.
# The Stage 1/2 reference MUST re-read the on-disk cursor before persisting and union-merge on
# a detected concurrent advance instead of overwriting.
#
# Like the Stage 3 falsification gate, memory-only enforcement of this rule does
# not fire reliably at cursor-write time. The skill itself owns the mandate; this
# test pins its language in place so prompt drift surfaces in CI.
#
# Run:  ./tests/test_retrospect_cursor_concurrency.sh
# Exit: 0 on success, 1 on first failure (after summary).

set +e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SPEC="$REPO_ROOT/skills/retrospect/references/stage1-2-analysis.md"

if [ ! -f "$SPEC" ]; then
  echo "FAIL: stage1-2-analysis.md not found at $SPEC" >&2
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
    echo "FAIL: $name — pattern '$pattern' missing in stage1-2-analysis.md"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

echo "=== retrospect Stage 1/2 reference Stage 1.5 cursor write mandate checks ==="
echo ""

# --- 1. Mandate section header exists ----------------------------------------
assert_present \
  "Cursor write mandate section header" \
  "Cursor write mandate (exit"

assert_present \
  "mandate — read-modify-write union framing" \
  "read-modify-write union under concurrency"

# --- 2. Re-read-before-persist requirement -----------------------------------
assert_present \
  "re-read on-disk cursor before persisting" \
  "re-read"

# --- 3. Concurrent-advance branch + union-merge ------------------------------
assert_present \
  "branch — concurrent advance detected" \
  "Concurrent advance detected"

assert_present \
  "union-merge instead of overwrite" \
  "UNION-merge the on-disk cursor"

# --- 4. Per-field merge rules ------------------------------------------------
assert_present \
  "note field union (carry-forward preserved)" \
  "concatenate the on-disk \`note\` lines"

assert_present \
  "pointer advances to the further of the two" \
  "FURTHER of the two pointers"

# --- 5. Audit line recorded on reconciliation --------------------------------
assert_present \
  "Stage 3 trail records concurrent advance" \
  "concurrent advance detected — union-merged note"

# --- 6. Red Flag entry exists ------------------------------------------------
assert_present \
  "Red Flag — silent overwrite of concurrently-advanced cursor" \
  "Silent overwrite of a concurrently-advanced cursor"

# --- 7. Cross-reference to the single-session falsification gate --------------
assert_present \
  "cross-session complement of falsification gate" \
  "cross-session complement of the single-session"

echo ""
echo "=== summary ==="
echo "PASS: $PASS"
echo "FAIL: $FAIL"

if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "FAILED: ${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
