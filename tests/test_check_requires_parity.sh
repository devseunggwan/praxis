#!/usr/bin/env bash
# test_check_requires_parity.sh — verify check-plugin-manifests.py Rule 20
# (#1158): spec `Requires:` ↔ manifest `requires` cross-check.
#
# Forward:  a manifest `requires` whose spec has no `Requires:` line fails.
# Reverse:  a spec `Requires:` line whose manifest entry has no `requires`
#           fails.
# Mismatch: both present but naming different components fails.
# Baseline: the untouched tree passes (negative control — proves the harness
#           can tell a difference at all).
#
# Usage: bash tests/test_check_requires_parity.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECK="$ROOT_DIR/scripts/check-plugin-manifests.py"
# A hook that carries `requires` in both places (manifest + spec).
DECLARED_SPEC="$ROOT_DIR/hooks/advisory-nudge/memory-hint/spec.md"
# A hook with no `requires` anywhere, used for the reverse case.
UNDECLARED_SPEC="$ROOT_DIR/hooks/advisory-nudge/pipefail-advisory/spec.md"

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

echo "test_check_requires_parity"

for f in "$DECLARED_SPEC" "$UNDECLARED_SPEC"; do
  if [ ! -f "$f" ]; then
    echo "FATAL: fixture target missing: $f" >&2
    exit 1
  fi
done

BAK_DECLARED="${DECLARED_SPEC}.bak-requires-test"
BAK_UNDECLARED="${UNDECLARED_SPEC}.bak-requires-test"
cp "$DECLARED_SPEC" "$BAK_DECLARED" || exit 1
cp "$UNDECLARED_SPEC" "$BAK_UNDECLARED" || exit 1
restore() {
  mv -f "$BAK_DECLARED" "$DECLARED_SPEC" 2>/dev/null
  mv -f "$BAK_UNDECLARED" "$UNDECLARED_SPEC" 2>/dev/null
}
trap restore EXIT

# Baseline — untouched tree passes.
python3 "$CHECK" >/dev/null 2>&1
run_case "baseline tree passes" "$?" "0"

# Forward — manifest declares `requires` but the spec's Requires: line is
# gone. Delete the line from the declared spec.
grep -v '^Requires:' "$BAK_DECLARED" > "$DECLARED_SPEC"
python3 "$CHECK" 2>&1 | grep -q "requires mismatch memory-hint"
run_case "manifest-without-spec-line fails (forward)" "$?" "0"
cp "$BAK_DECLARED" "$DECLARED_SPEC"

# Reverse — spec declares a Requires: line with no manifest counterpart.
awk 'NR==3{print; print "Requires: zzz-fake-component"; next} {print}' \
  "$BAK_UNDECLARED" > "$UNDECLARED_SPEC"
python3 "$CHECK" 2>&1 | grep -q "requires mismatch pipefail-advisory"
run_case "spec-line-without-manifest fails (reverse)" "$?" "0"
cp "$BAK_UNDECLARED" "$UNDECLARED_SPEC"

# Mismatch — both present, different component names.
sed 's/^Requires: hookable-memory-store/Requires: zzz-other-component/' \
  "$BAK_DECLARED" > "$DECLARED_SPEC"
python3 "$CHECK" 2>&1 | grep -q "requires mismatch memory-hint"
run_case "value mismatch fails" "$?" "0"
cp "$BAK_DECLARED" "$DECLARED_SPEC"

# Restored tree passes again (proves the mutations, not ambient state, drove
# the failures above).
python3 "$CHECK" >/dev/null 2>&1
run_case "restored tree passes" "$?" "0"

echo ""
echo "Result: $PASS/$((PASS + FAIL)) passed"
if [ "$FAIL" -gt 0 ]; then
  printf 'Failed: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
