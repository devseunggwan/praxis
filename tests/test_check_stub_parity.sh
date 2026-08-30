#!/usr/bin/env bash
# test_check_stub_parity.sh — verify check-plugin-manifests.py Rule 15 (#606):
# docs/hook/<name>.md redirect-stub parity.
#
# Forward:  every hook dir owns a byte-identical 1-line "Moved to" stub.
# Reverse:  no orphan stub (docs/hook/*.md without a backing hook dir) survives,
#           except INDEX.md and the NON_HOOK_DOCS allowlist (block-message-format).
#
# Usage: bash tests/test_check_stub_parity.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECK="$ROOT_DIR/scripts/check-plugin-manifests.py"
# A real hook stub used for the delete/tamper cases.
TARGET="$ROOT_DIR/docs/hook/skill-gate-commands.md"
# A fake orphan stub for the reverse-parity case. Must NOT pre-exist.
ORPHAN="$ROOT_DIR/docs/hook/zzz-test-orphan-stub.md"

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

echo "test_check_stub_parity"

# Fail fast if the target stub is absent (would corrupt it via empty backup) or
# the orphan slot is already taken (would delete a real file on cleanup).
if [ ! -f "$TARGET" ]; then
  echo "FAIL  [target_exists] expected=yes got=no"
  exit 1
fi
if [ -e "$ORPHAN" ]; then
  echo "FAIL  [orphan_slot_free] expected=yes got=no ($ORPHAN already exists)"
  exit 1
fi

BACKUP="$(mktemp)"
cp "$TARGET" "$BACKUP"
restore() { cp "$BACKUP" "$TARGET"; }
trap 'restore; rm -f "$BACKUP" "$ORPHAN"' EXIT

# 1. Baseline is clean (proves block-message-format orphan is correctly excepted).
python3 "$CHECK" >/dev/null 2>&1
run_case "baseline_check_clean" "$?" "0"

# 2. Deleting a hook stub → STUB MISSING, exit non-zero.
rm -f "$TARGET"
OUT="$(python3 "$CHECK" 2>&1)"
run_case "missing_check_nonzero" "$?" "1"
echo "$OUT" | grep -q "STUB MISSING docs/hook/skill-gate-commands.md"
run_case "missing_names_stub" "$?" "0"
restore

# 3. Tampering a hook stub → STUB DRIFT, exit non-zero.
printf '\n<!-- tampered by test -->\n' >> "$TARGET"
OUT="$(python3 "$CHECK" 2>&1)"
run_case "drift_check_nonzero" "$?" "1"
echo "$OUT" | grep -q "STUB DRIFT docs/hook/skill-gate-commands.md"
run_case "drift_names_stub" "$?" "0"
restore

# 4. An orphan stub (no backing hook dir, not in NON_HOOK_DOCS) → ORPHAN STUB.
printf '> orphan stub with no hook dir\n' > "$ORPHAN"
OUT="$(python3 "$CHECK" 2>&1)"
run_case "orphan_check_nonzero" "$?" "1"
echo "$OUT" | grep -q "ORPHAN STUB docs/hook/zzz-test-orphan-stub.md"
run_case "orphan_names_stub" "$?" "0"
rm -f "$ORPHAN"

# 5. Restored tree is clean again.
restore
trap - EXIT
rm -f "$BACKUP"
python3 "$CHECK" >/dev/null 2>&1
run_case "restored_check_clean" "$?" "0"

echo "---"
echo "PASS=$PASS FAIL=$FAIL"
if [ "$FAIL" -ne 0 ]; then
  printf 'FAILED: %s\n' "${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
