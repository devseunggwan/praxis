#!/usr/bin/env bash
# test_check_changelog_completeness.sh — verify
# scripts/check-changelog-completeness.sh, the release-count guard (issue #750).
#
# The script's git-boundary detection is bypassed via PRAXIS_CC_SUBJECTS_FILE so
# every case is a self-contained fixture: a synthetic CHANGELOG (its newest
# section carrying — or omitting — a "N PRs since X.Y.Z" line) plus an injected
# list of release-range commit subjects. This exercises the parse + count +
# compare logic without a real git history.
#
# Usage: bash tests/test_check_changelog_completeness.sh
# Exit:  0 = all pass; 1 = at least one fail
set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
GUARD="$ROOT_DIR/scripts/check-changelog-completeness.sh"

PASS=0
FAIL=0
FAILED_NAMES=()

run_case() { # name actual_exit expected_exit
  local name="$1" actual="$2" expected="$3"
  if [ "$actual" = "$expected" ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected_exit=$expected got=$actual"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

echo "test_check_changelog_completeness"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Shared subject list: 4 real PRs since 7.0.0 (a bump commit is included to
# prove it is excluded from the count).
cat >"$TMP/subjects_4.txt" <<'EOF'
feat(skill): add debt deferred-decision ledger (#732)
fix(retrospect): read each is_error body individually (#729)
docs(release): unify version bump checklist (#728)
feat(hooks): re-fetch PR state before PR-contingent question (#733)
chore: bump version to 7.1.0 (#746)
EOF

changelog_with_claim() { # version prev count  -> path
  local ver="$1" prev="$2" count="$3"
  local path="$TMP/cl_${ver}_${count}.md"
  cat >"$path" <<EOF
# Changelog

## [Unreleased]

## [${ver}] - 2026-07-03

${count} PRs since ${prev}. Minor release.

### Added
- something (#732)

## [${prev}] - 2026-06-28

### Removed
- old thing (#726)
EOF
  printf '%s' "$path"
}

# Case 1: declared count matches actual (4) -> pass (exit 0).
cl="$(changelog_with_claim 7.1.0 7.0.0 4)"
CHANGELOG_FILE="$cl" PRAXIS_CC_SUBJECTS_FILE="$TMP/subjects_4.txt" bash "$GUARD" >/dev/null 2>&1
run_case "count-matches-passes" "$?" 0

# Case 2: declared count too low (the real v7.1.0 bug: said 4, 4 merged here but
# claim says 9) -> mismatch -> fail (exit 1).
cl="$(changelog_with_claim 7.1.0 7.0.0 9)"
CHANGELOG_FILE="$cl" PRAXIS_CC_SUBJECTS_FILE="$TMP/subjects_4.txt" bash "$GUARD" >/dev/null 2>&1
run_case "count-mismatch-fails" "$?" 1

# Case 3: bump commit is not counted — a subjects list of exactly one real PR
# plus a bump must count as 1, not 2.
cat >"$TMP/subjects_1.txt" <<'EOF'
fix(hooks): scan subagent transcripts (#738)
chore(release): bump version to 7.1.0 (#746)
EOF
cl="$(changelog_with_claim 7.1.0 7.0.0 1)"
CHANGELOG_FILE="$cl" PRAXIS_CC_SUBJECTS_FILE="$TMP/subjects_1.txt" bash "$GUARD" >/dev/null 2>&1
run_case "bump-commit-excluded" "$?" 0

# Case 4: a section without a "N PRs since" line opts out -> pass (exit 0),
# even though the injected range has PRs.
cat >"$TMP/cl_noclaim.md" <<'EOF'
# Changelog

## [Unreleased]

## [7.0.0] - 2026-06-28

Major release. Removes the cmux-browser skill.

### Removed
- cmux-browser (#726)
EOF
CHANGELOG_FILE="$TMP/cl_noclaim.md" PRAXIS_CC_SUBJECTS_FILE="$TMP/subjects_4.txt" bash "$GUARD" >/dev/null 2>&1
run_case "no-claim-line-skips" "$?" 0

# Case 5: duplicate PR reference in the range is counted once.
cat >"$TMP/subjects_dup.txt" <<'EOF'
feat(a): thing (#732)
feat(a): thing follow-up squashed (#732)
fix(b): other (#729)
EOF
cl="$(changelog_with_claim 7.1.0 7.0.0 2)"
CHANGELOG_FILE="$cl" PRAXIS_CC_SUBJECTS_FILE="$TMP/subjects_dup.txt" bash "$GUARD" >/dev/null 2>&1
run_case "duplicate-pr-counted-once" "$?" 0

# Case 6: missing CHANGELOG -> environment error (exit 2).
CHANGELOG_FILE="$TMP/does-not-exist.md" PRAXIS_CC_SUBJECTS_FILE="$TMP/subjects_4.txt" bash "$GUARD" >/dev/null 2>&1
run_case "missing-changelog-errors" "$?" 2

echo ""
echo "test_check_changelog_completeness: $PASS passed, $FAIL failed"
if [ "$FAIL" -ne 0 ]; then
  echo "failed: ${FAILED_NAMES[*]}" >&2
  exit 1
fi
exit 0
