#!/usr/bin/env bash
# tests/test_assert_lib_wrap_insensitive.sh — _assert_lib.sh regression test
#
# Regression test for issue #872: assert_present/assert_absent must keep
# passing when a sentence is re-wrapped at a different column, since that is
# precisely the failure mode that broke tests/test_worktree_merge_cleanup.sh
# ~8 times in one review round. This test builds two on-disk variants of the
# same prose — unwrapped (one line) and wrapped (split mid-sentence) — and
# asserts both produce identical PASS/FAIL results.
#
# Run:  bash tests/test_assert_lib_wrap_insensitive.sh
# Exit: 0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

UNWRAPPED="$TMP_DIR/unwrapped.md"
WRAPPED="$TMP_DIR/wrapped.md"

cat >"$UNWRAPPED" <<'EOF'
# Example

The prunable and locked markers are precisely the forensic evidence for
what actually happened during cleanup, and a diff against the snapshot
can reveal a removal that never happened.
EOF

# Same sentence, re-wrapped mid-fragment (simulates an unrelated prose edit
# shifting the ~78-column wrap boundary).
cat >"$WRAPPED" <<'EOF'
# Example

The prunable and locked markers
are precisely
the forensic evidence for
what actually happened during cleanup, and a diff against the snapshot
can reveal a removal
that never happened.
EOF

# shellcheck source=./_assert_lib.sh
source "$SCRIPT_DIR/_assert_lib.sh"

RESULTS_MATCH=1
FAIL_DETAIL=""

for target in "$UNWRAPPED" "$WRAPPED"; do
  assert_lib_init "$target"
  assert_present "forensic evidence fragment" "are precisely the forensic evidence"
  assert_present "removal fragment" "a removal that never happened"
  assert_absent "unrelated string is absent" "this string is not in the doc"

  if [ "$target" = "$UNWRAPPED" ]; then
    unwrapped_pass=$PASS
    unwrapped_fail=$FAIL
  else
    wrapped_pass=$PASS
    wrapped_fail=$FAIL
  fi
done

echo ""
if [ "$unwrapped_pass" -eq 3 ] && [ "$unwrapped_fail" -eq 0 ]; then
  echo "PASS  [unwrapped variant: all 3 assertions pass]"
else
  echo "FAIL  [unwrapped variant: expected 3 pass/0 fail, got $unwrapped_pass pass/$unwrapped_fail fail]"
  RESULTS_MATCH=0
fi

if [ "$wrapped_pass" -eq 3 ] && [ "$wrapped_fail" -eq 0 ]; then
  echo "PASS  [wrapped variant: all 3 assertions pass despite mid-sentence re-wrap]"
else
  echo "FAIL  [wrapped variant: expected 3 pass/0 fail, got $wrapped_pass pass/$wrapped_fail fail]"
  RESULTS_MATCH=0
fi

echo ""
if [ "$RESULTS_MATCH" -eq 1 ]; then
  echo "Passed: 2  Failed: 0"
  exit 0
else
  echo "Passed: 0  Failed: 1"
  echo "Failed tests:"
  echo "  - wrap-insensitivity regression"
  exit 1
fi
