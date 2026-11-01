#!/bin/bash
# tests/test_retrospect_routing.sh — assert retrospect skill docs are repo-agnostic
# and keep the formats audit-distribution-gates.py parses
#
# Regression test for the routing rule: user-specific GitHub org/repo names
# MUST NOT appear in the retrospect skill. The skill is distributed across
# users; any hardcoded name misroutes tool-friction issues for users on
# different fork/company environments.
#
# Run:  ./tests/test_retrospect_routing.sh
# Exit: 0 on success, 1 on first failure (after summary).

set +e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SKILL_DIR="$REPO_ROOT/skills/retrospect"
SKILL="$SKILL_DIR/SKILL.md"

if [ ! -f "$SKILL" ]; then
  echo "FAIL: SKILL.md not found at $SKILL" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

# Forbidden patterns — any match is a regression toward user-specific
# hardcoding. Extend via PRAXIS_RETROSPECT_FORBIDDEN_PATTERNS env var
# (newline-separated) when bringing this test into a downstream fork that
# has additional org/handle names to guard against.
FORBIDDEN_PATTERNS=(
  "devseunggwan/"
  "Yeachan-Heo/"
  "ai-dotfiles"
)
if [ -n "${PRAXIS_RETROSPECT_FORBIDDEN_PATTERNS:-}" ]; then
  while IFS= read -r extra; do
    [ -n "$extra" ] && FORBIDDEN_PATTERNS+=("$extra")
  done <<< "$PRAXIS_RETROSPECT_FORBIDDEN_PATTERNS"
fi

run_forbidden_check() {
  local name="$1"
  local pattern="$2"
  local hits
  hits=$(grep -R -c -- "$pattern" "$SKILL_DIR" 2>/dev/null | awk -F: '{sum += $2} END {print sum + 0}')
  hits=${hits:-0}
  if [ "$hits" -eq 0 ]; then
    echo "PASS: $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $name — found $hits occurrence(s) of '$pattern':"
    grep -R -n -- "$pattern" "$SKILL_DIR" | sed 's/^/    /'
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

run_anchor_check() {
  local name="$1"
  local path="$2"
  local pattern="$3"
  if [ ! -f "$path" ]; then
    echo "FAIL: $name — missing file $path"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
    return
  fi
  if grep -q -- "$pattern" "$path" 2>/dev/null; then
    echo "PASS: $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $name — pattern '$pattern' missing in $path"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

echo "=== retrospect docs repo-agnosticism checks ==="
echo ""

for pattern in "${FORBIDDEN_PATTERNS[@]}"; do
  run_forbidden_check "no hardcoded '$pattern'" "$pattern"
done

# Formats skills/retrospect/audit-distribution-gates.py parses out of a draft
# built from these references. A reworded format here makes the script miss
# the block it gates on, so these stay pinned.
echo ""
echo "=== formats audit-distribution-gates.py parses ==="
run_anchor_check "stage3 reference keeps distribution fence" "$SKILL_DIR/references/stage3-reporting.md" "retrospect:distribution begin"
run_anchor_check "stage3 reference keeps memory-scan evidence block" "$SKILL_DIR/references/stage3-reporting.md" "memory_scan finding #<n>"
run_anchor_check "stage3 reference keeps memory-scan HTML comment" "$SKILL_DIR/references/stage3-reporting.md" "<!-- memory_scan finding #<n>:"
run_anchor_check "report template includes memory-scan evidence block" "$SKILL_DIR/references/report-template.md" "<!-- memory_scan finding #<n>:"
run_anchor_check "stage1-2 reference keeps backing_repo declaration" "$SKILL_DIR/references/stage1-2-analysis.md" "backing_repo: <owner>/<repo>"
run_anchor_check "stage1-2 reference keeps memory-scan passable field" "$SKILL_DIR/references/stage1-2-analysis.md" "memory_scan.scanned=true"

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
