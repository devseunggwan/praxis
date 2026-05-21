#!/bin/bash
# tests/test_retrospect_routing.sh — assert retrospect SKILL.md is repo-agnostic
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
SKILL="$REPO_ROOT/skills/retrospect/SKILL.md"

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
  hits=$(grep -c -- "$pattern" "$SKILL" 2>/dev/null)
  hits=${hits:-0}
  if [ "$hits" -eq 0 ]; then
    echo "PASS: $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $name — found $hits occurrence(s) of '$pattern':"
    grep -n -- "$pattern" "$SKILL" | sed 's/^/    /'
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

# Required placeholder presence — ensure routing uses placeholder convention
# rather than concrete repo names.
run_placeholder_check() {
  local name="$1"
  local placeholder="$2"
  local hits
  hits=$(grep -c -- "$placeholder" "$SKILL" 2>/dev/null)
  hits=${hits:-0}
  if [ "$hits" -gt 0 ]; then
    echo "PASS: $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $name — placeholder '$placeholder' missing in SKILL.md"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

echo "=== retrospect SKILL.md repo-agnosticism checks ==="
echo ""

for pattern in "${FORBIDDEN_PATTERNS[@]}"; do
  run_forbidden_check "no hardcoded '$pattern'" "$pattern"
done

echo ""
run_placeholder_check "uses <resolved_backing_repo> placeholder" "<resolved_backing_repo>"
run_placeholder_check "uses <resolved-praxis-repo> placeholder" "<resolved-praxis-repo>"

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
