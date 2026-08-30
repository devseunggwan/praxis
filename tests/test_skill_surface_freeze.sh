#!/usr/bin/env bash
# test_skill_surface_freeze.sh — verify check-plugin-manifests.py Rule 12 (#465)
#
# Asserts:
#   1. Current seed (14 skills) passes
#   2. Unexpected skill dir triggers non-zero exit + UNEXPECTED SKILL message
#   3. Removed (declared-but-missing) skill triggers non-zero exit + REMOVED SKILL message
#   4. SKILL.md.tmpl (file, not dir) and underscore-prefixed dirs are excluded
#
# Usage: bash tests/test_skill_surface_freeze.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECK="$ROOT_DIR/scripts/check-plugin-manifests.py"

if [ ! -x "$CHECK" ]; then
  echo "FAIL: check script not executable: $CHECK" >&2
  exit 1
fi

PASS=0
FAIL=0
GHOST="$ROOT_DIR/skills/ghost-skill-fixture"
GHOST_UNDERSCORE="$ROOT_DIR/skills/_internal-fixture"
BACKUP_DIR="$(mktemp -d)" || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
BACKUP="$BACKUP_DIR/strikes"

cleanup() {
  # Idempotent restore: handles interruption (SIGINT/TERM) mid-fixture.
  # GHOST now carries a SKILL.md (see Case 2), so rm -rf — not rmdir.
  [ -d "$GHOST" ] && rm -rf "$GHOST" 2>/dev/null
  [ -d "$GHOST_UNDERSCORE" ] && rm -rf "$GHOST_UNDERSCORE" 2>/dev/null
  if [ -d "$BACKUP" ] && [ ! -d "$ROOT_DIR/skills/strikes" ]; then
    mv "$BACKUP" "$ROOT_DIR/skills/strikes"
  fi
  rm -rf "$BACKUP_DIR" 2>/dev/null
}
trap cleanup EXIT INT TERM

run_case() {
  local name="$1" cmd="$2" expected_rc="$3" expected_grep="$4"
  local out rc
  out="$(eval "$cmd" 2>&1)"
  rc=$?
  local ok=1
  if [ "$rc" != "$expected_rc" ]; then
    ok=0
  fi
  if [ -n "$expected_grep" ] && ! echo "$out" | grep -q -- "$expected_grep"; then
    ok=0
  fi
  if [ "$ok" = "1" ]; then
    echo "PASS  [$name]"
    PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] rc=$rc (want $expected_rc), grep=$expected_grep"
    echo "----- output -----"
    echo "$out"
    echo "------------------"
    FAIL=$((FAIL + 1))
  fi
}

# Case 1: current 14-skill seed passes (bypass-review demoted to CLI, #582)
run_case "seed-14-passes" "python3 \"$CHECK\"" 0 "plugin-manifest check OK"

# Case 2: unexpected skill dir triggers UNEXPECTED SKILL
# Since #582, a dir counts as a skill only if it has a SKILL.md, so the
# fixture must carry one to be detected as a genuine unexpected skill.
mkdir -p "$GHOST"
: > "$GHOST/SKILL.md"
run_case "unexpected-skill-fails" "python3 \"$CHECK\"" 1 "UNEXPECTED SKILL(S).*ghost-skill-fixture"
rm -rf "$GHOST"

# Case 3: removed (declared-but-missing) skill triggers REMOVED SKILL
mv "$ROOT_DIR/skills/strikes" "$BACKUP"
run_case "removed-skill-fails" "python3 \"$CHECK\"" 1 "REMOVED SKILL(S).*strikes"
mv "$BACKUP" "$ROOT_DIR/skills/strikes"

# Case 4: SKILL.md.tmpl (file) and _-prefixed dirs are excluded.
# The fixture carries a SKILL.md so this isolates the underscore rule: with a
# SKILL.md present, the only reason it stays excluded is the leading underscore
# (not the #582 SKILL.md filter, which would otherwise mask a regression here).
mkdir -p "$GHOST_UNDERSCORE"
: > "$GHOST_UNDERSCORE/SKILL.md"
run_case "underscore-prefix-excluded" "python3 \"$CHECK\"" 0 "plugin-manifest check OK"
rm -rf "$GHOST_UNDERSCORE"

# Case 5: post-restore returns to passing state
run_case "post-restore-passes" "python3 \"$CHECK\"" 0 "plugin-manifest check OK"

echo ""
echo "Results: $PASS pass, $FAIL fail"
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
