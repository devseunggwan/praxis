#!/usr/bin/env bash
# test_hook_operating_matrix.sh - verify docs/hook-operating-matrix.md is
# generated and drift-checked by scripts/check-plugin-manifests.py.

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECK="$ROOT_DIR/scripts/check-plugin-manifests.py"
BUILD="$ROOT_DIR/scripts/build-plugin-manifests.py"
MATRIX="$ROOT_DIR/docs/hook-operating-matrix.md"

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

echo "test_hook_operating_matrix"

if [ ! -f "$MATRIX" ]; then
  echo "FAIL  [matrix_exists] expected=yes got=no"
  exit 1
fi

BACKUP="$(mktemp)"
cp "$MATRIX" "$BACKUP"
restore() { cp "$BACKUP" "$MATRIX"; }
trap 'restore; rm -f "$BACKUP"' EXIT

python3 "$CHECK" >/dev/null 2>&1
run_case "baseline_check_clean" "$?" "0"

printf '\n<!-- tampered by test -->\n' >> "$MATRIX"
OUT="$(python3 "$CHECK" 2>&1)"
run_case "tampered_check_nonzero" "$?" "1"
echo "$OUT" | grep -q "MATRIX DRIFT docs/hook-operating-matrix.md"
run_case "tampered_names_matrix" "$?" "0"

python3 "$BUILD" >/dev/null 2>&1
python3 "$CHECK" >/dev/null 2>&1
run_case "build_restores_matrix" "$?" "0"

restore
trap - EXIT
rm -f "$BACKUP"

echo "---"
echo "PASS=$PASS FAIL=$FAIL"
if [ "$FAIL" -ne 0 ]; then
  printf 'FAILED: %s\n' "${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
