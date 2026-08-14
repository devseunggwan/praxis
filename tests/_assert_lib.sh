#!/usr/bin/env bash
# tests/_assert_lib.sh — shared static-document assertion helpers
#
# Extracted from tests/test_worktree_merge_cleanup.sh (issue #872).
#
# Problem: static doc tests assert fixed-string prose fragments against
# SKILL.md with `grep -Fc`. SKILL.md is markdown prose that wraps at ~78
# columns, so a fragment quoted across a wrap boundary (e.g. "are precisely
# the forensic evidence") breaks the instant an unrelated edit shifts where
# the line wraps — this happened ~8 times in one review round (issue #865).
#
# Fix: `assert_present` / `assert_absent` match against a normalized buffer
# where every newline becomes a single space, so a sentence broken across a
# wrap reads as one continuous run and matches regardless of where the wrap
# falls. Pre-existing intra-line spacing (e.g. a markdown nested-list "  - "
# indent) is left untouched — only newlines are substituted — so assertions
# that intentionally pin indentation inside a code fence still work.
#
# `assert_present_re` / `assert_order` / `assert_order_re` still match
# against the RAW file. Their callers rely on line anchors (`^`/`$`) or line
# numbers to pin an exact physical line — usually inside a code fence, which
# markdown reflow never touches — so normalizing would break the very
# distinction they exist to make (e.g. `^git worktree prune$` vs
# `^git worktree prune --dry-run -v`).
#
# Usage:
#   source "$SCRIPT_DIR/_assert_lib.sh"
#   assert_lib_init "$TARGET_FILE"
#   assert_present "name" "fixed string fragment"
#   assert_absent  "name" "fixed string that must not appear"
#   assert_present_re "name" "regex"
#   assert_order "name" "first fixed string" "second fixed string"
#   assert_order_re "name" "first regex" "second regex"
#   assert_lib_retarget "path"  # next document, same tally
#   assert_lib_summary   # prints Passed/Failed, exits 1 if any FAIL

# Swap the document under test without touching the tally. A second
# assert_lib_init would zero PASS/FAIL, so a suite spanning two documents would
# exit 0 on a failure in the first one — the failures print and then stop
# counting, which is worse than not running them.
assert_lib_retarget() {
  _ASSERT_TARGET="$1"
  if [ ! -f "$_ASSERT_TARGET" ]; then
    echo "FAIL: target not found at $_ASSERT_TARGET" >&2
    exit 1
  fi
  _ASSERT_NORMALIZED="$(sed -e ':a' -e 'N' -e '$!ba' \
    -e 's/\n[[:blank:]]*/ /g' "$_ASSERT_TARGET")"
}

assert_lib_init() {
  _ASSERT_TARGET="$1"
  if [ ! -f "$_ASSERT_TARGET" ]; then
    echo "FAIL: target not found at $_ASSERT_TARGET" >&2
    exit 1
  fi
  # Collapse each newline *and the indentation that follows it* into a single
  # space, so a fixed-string match is insensitive to where prose wraps. The
  # trailing indent matters: most of this repo's prose lives in markdown list
  # items, whose continuation lines are indented, so replacing only the newline
  # leaves "precisely" + "\n  the" as "precisely   the" and the match still
  # fails — the exact fragility this helper exists to remove.
  #
  # Indentation NOT adjacent to a wrap point is preserved, so nested list
  # markers inside a code fence still compare as written.
  #
  # sed idiom: :a/N/$!ba slurps the whole file into the pattern space (the file
  # is a SKILL.md, not a log), then one global substitution does the join.
  # [[:blank:]] rather than \t — BSD sed does not read \t as a tab.
  _ASSERT_NORMALIZED="$(sed -e ':a' -e 'N' -e '$!ba' \
    -e 's/\n[[:blank:]]*/ /g' "$_ASSERT_TARGET")"
  PASS=0
  FAIL=0
  FAILED_NAMES=()
}

_assert_record() {
  local name="$1" ok="$2" reason="$3"
  if [ "$ok" = "1" ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] — $reason"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

# Wrap-insensitive: fixed-string match against the normalized buffer.
assert_present() {
  local name="$1" pattern="$2"
  if grep -Fq -- "$pattern" <<<"$_ASSERT_NORMALIZED"; then
    _assert_record "$name" 1 ""
  else
    _assert_record "$name" 0 "pattern not found: $pattern"
  fi
}

# Wrap-insensitive: fixed-string pattern must be absent from the normalized
# buffer.
assert_absent() {
  local name="$1" pattern="$2"
  if grep -Fq -- "$pattern" <<<"$_ASSERT_NORMALIZED"; then
    _assert_record "$name" 0 "pattern must be absent but was found: $pattern"
  else
    _assert_record "$name" 1 ""
  fi
}

# Raw-file regex match — see header note on why this does not normalize.
assert_present_re() {
  local name="$1" pattern="$2"
  local hits
  hits=$(grep -Ec -- "$pattern" "$_ASSERT_TARGET" 2>/dev/null)
  hits=${hits:-0}
  if [ "$hits" -gt 0 ]; then
    _assert_record "$name" 1 ""
  else
    _assert_record "$name" 0 "regex not matched: $pattern"
  fi
}

# Raw-file, line-number based ordering — see header note.
assert_order() {
  local name="$1" first="$2" second="$3"
  local line_first line_second
  line_first=$(grep -Fn -m1 -- "$first" "$_ASSERT_TARGET" 2>/dev/null | cut -d: -f1)
  line_second=$(grep -Fn -m1 -- "$second" "$_ASSERT_TARGET" 2>/dev/null | cut -d: -f1)
  if [ -z "$line_first" ] || [ -z "$line_second" ]; then
    _assert_record "$name" 0 "one of the patterns is absent (first=${line_first:-none} second=${line_second:-none})"
  elif [ "$line_first" -lt "$line_second" ]; then
    _assert_record "$name" 1 ""
  else
    _assert_record "$name" 0 "'$first' (line $line_first) must precede '$second' (line $line_second)"
  fi
}

# Raw-file, line-number based ordering with extended-regex anchors — see
# header note.
assert_order_re() {
  local name="$1" first="$2" second="$3"
  local line_first line_second
  line_first=$(grep -En -m1 -- "$first" "$_ASSERT_TARGET" 2>/dev/null | cut -d: -f1)
  line_second=$(grep -En -m1 -- "$second" "$_ASSERT_TARGET" 2>/dev/null | cut -d: -f1)
  if [ -z "$line_first" ] || [ -z "$line_second" ]; then
    _assert_record "$name" 0 "one of the patterns is absent (first=${line_first:-none} second=${line_second:-none})"
  elif [ "$line_first" -lt "$line_second" ]; then
    _assert_record "$name" 1 ""
  else
    _assert_record "$name" 0 "'$first' (line $line_first) must precede '$second' (line $line_second)"
  fi
}

assert_lib_summary() {
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
}
