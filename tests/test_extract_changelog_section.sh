#!/usr/bin/env bash
# test_extract_changelog_section.sh — verify scripts/extract-changelog-section.sh,
# the single source of truth for every release note body (issue #630).
#
# Covers the three non-obvious behaviors a future awk edit could silently
# regress: last-section stop at the trailing link-reference block, prefix-
# collision avoidance via the literal "]" (2.1.0 must not bleed into 2.10.0),
# and the version-label-only stop so an in-body markdown link-reference is not
# mistaken for the trailing block (review #630 MEDIUM-1).
#
# Usage: bash tests/test_extract_changelog_section.sh
# Exit:  0 = all pass; 1 = at least one fail
set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
EXTRACT="$ROOT_DIR/scripts/extract-changelog-section.sh"

PASS=0
FAIL=0
FAILED_NAMES=()

run_case() {
  local name="$1" result="$2" expected="$3"
  if [ "$result" = "$expected" ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected got=$result"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

assert_contains() { # haystack needle name
  if printf '%s' "$1" | grep -qF -- "$2"; then
    run_case "$3" present present
  else
    run_case "$3" absent present
  fi
}
assert_absent() { # haystack needle name
  if printf '%s' "$1" | grep -qF -- "$2"; then
    run_case "$3" present absent
  else
    run_case "$3" absent absent
  fi
}

echo "test_extract_changelog_section"

# Fixture: a self-contained CHANGELOG exercising every edge case. The 1.0.0
# body deliberately contains a column-0 markdown link-reference ("[guide]: …")
# that is NOT a version/Unreleased label — it must survive extraction.
FIX="$(mktemp)"
trap 'rm -f "$FIX"' EXIT
cat >"$FIX" <<'EOF'
# Changelog

## [Unreleased]

## [2.10.0] - 2026-01-03

### Added
- ten feature (#100)

## [2.1.0] - 2026-01-02

### Added
- one feature (#50)

## [1.0.0] - 2026-01-01

### Added
- initial (#1)

[guide]: https://example.com/guide

[Unreleased]: https://example.com/compare/v2.10.0...HEAD
[2.10.0]: https://example.com/compare/v2.1.0...v2.10.0
[2.1.0]: https://example.com/compare/v1.0.0...v2.1.0
[1.0.0]: https://example.com/releases/tag/v1.0.0
EOF

export CHANGELOG_FILE="$FIX"

# 1. Middle version: own body present, next version's body absent (header stop).
out_mid="$("$EXTRACT" 2.10.0)"
assert_contains "$out_mid" "ten feature" "middle: own body present"
assert_absent   "$out_mid" "one feature" "middle: next-version body absent"
printf '%s' "$out_mid" | head -1 | grep -qF "## [2.10.0] - 2026-01-03"
run_case "middle: header line included" "$?" "0"

# 2. Prefix collision: 2.1.0 must NOT capture 2.10.0's body (literal "]").
out_one="$("$EXTRACT" 2.1.0)"
assert_contains "$out_one" "one feature" "prefix: own body present"
assert_absent   "$out_one" "ten feature" "prefix: 2.10.0 body not bled in"
assert_absent   "$out_one" "initial"     "prefix: 1.0.0 body absent"

# 3. Last version: body present, trailing link-ref block stopped, but an
#    in-body markdown link-ref ([guide]) is preserved (MEDIUM-1 fix).
out_last="$("$EXTRACT" 1.0.0)"
assert_contains "$out_last" "initial"             "last: own body present"
assert_contains "$out_last" "[guide]: https"      "last: in-body link-ref preserved"
assert_absent   "$out_last" "[Unreleased]: https" "last: trailing block stopped"
assert_absent   "$out_last" "[1.0.0]: https"      "last: version link-ref stopped"

# 4. v-prefix strip: v2.1.0 output identical to 2.1.0.
[ "$("$EXTRACT" v2.1.0)" = "$out_one" ]
run_case "v-prefix == bare version" "$?" "0"

# 5. Missing version → exit 2.
"$EXTRACT" 9.9.9 >/dev/null 2>&1
run_case "missing version exits 2" "$?" "2"

# 6. No args → exit 1.
"$EXTRACT" >/dev/null 2>&1
run_case "no args exits 1" "$?" "1"

echo "---"
echo "PASS=$PASS FAIL=$FAIL"
if [ "$FAIL" -ne 0 ]; then
  printf 'FAILED: %s\n' "${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
