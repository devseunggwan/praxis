#!/usr/bin/env bash
# test_check_hook_dependency_table.sh — verify check-plugin-manifests.py
# Rule 27 (#1332): README.md `### Hook dependencies` table ↔ manifest
# `requires` cross-check.
#
# Missing:    a component some hook declares has no README row → fails.
# Orphan:     a README row names a component no hook declares → fails.
# Drift:      a row's hook cell differs from the manifest set → fails.
# No install: a row's install cell is empty → fails.
# Baseline:   the untouched tree passes (negative control).
#
# Usage: bash tests/test_check_hook_dependency_table.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECK="$ROOT_DIR/scripts/check-plugin-manifests.py"
README="$ROOT_DIR/README.md"

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

echo "test_check_hook_dependency_table"

if [ ! -f "$README" ]; then
  echo "FATAL: fixture target missing: $README" >&2
  exit 1
fi

BAK="${README}.bak-hook-deps-test"
cp "$README" "$BAK" || exit 1
restore() {
  mv -f "$BAK" "$README" 2>/dev/null
}
trap restore EXIT

# The zsh row is the fixture: one component, one hook, a simple install cell.
ZSH_ROW='| `zsh` | `block-unmatched-glob` |'
grep -qF "$ZSH_ROW" "$BAK"
run_case "fixture row present in README" "$?" "0"

# Baseline — untouched tree passes.
python3 "$CHECK" >/dev/null 2>&1
run_case "baseline tree passes" "$?" "0"

# Missing — delete the zsh row; the manifest still declares zsh.
grep -vF "$ZSH_ROW" "$BAK" > "$README"
python3 "$CHECK" 2>&1 | grep -q "HOOK DEPS MISSING README.md: 'zsh'"
run_case "declared component without a row fails (missing)" "$?" "0"
cp "$BAK" "$README"

# Orphan — add a row for a component no hook declares.
awk -v row="$ZSH_ROW" 'index($0, row) == 1 {print; print "| `zzz-fake-component` | `block-unmatched-glob` | `brew install zzz` |"; next} {print}' \
  "$BAK" > "$README"
python3 "$CHECK" 2>&1 | grep -q "HOOK DEPS ORPHAN README.md: 'zzz-fake-component'"
run_case "row for an undeclared component fails (orphan)" "$?" "0"
cp "$BAK" "$README"

# Drift — the zsh row names a different hook than the manifest.
sed "s/^| \`zsh\` | \`block-unmatched-glob\` |/| \`zsh\` | \`pipefail-advisory\` |/" \
  "$BAK" > "$README"
python3 "$CHECK" 2>&1 | grep -q "HOOK DEPS DRIFT README.md: 'zsh'"
run_case "hook cell differing from manifest fails (drift)" "$?" "0"
cp "$BAK" "$README"

# No install — blank the zsh row's install cell.
python3 - "$BAK" "$README" <<'PY'
import re, sys
src, dst = sys.argv[1:3]
text = open(src, encoding="utf-8").read()
out = re.sub(r"^(\| `zsh` \| `block-unmatched-glob` \|)[^\n]*$", r"\1  |", text, count=1, flags=re.M)
assert out != text
open(dst, "w", encoding="utf-8").write(out)
PY
python3 "$CHECK" 2>&1 | grep -q "HOOK DEPS NO INSTALL README.md: 'zsh'"
run_case "empty install cell fails (no install)" "$?" "0"
cp "$BAK" "$README"

# Restored tree passes again (proves the mutations, not ambient state, drove
# the failures above).
python3 "$CHECK" >/dev/null 2>&1
run_case "restored tree passes" "$?" "0"

echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  printf '  failed: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
