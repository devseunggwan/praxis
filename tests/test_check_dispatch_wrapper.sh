#!/usr/bin/env bash
# test_check_dispatch_wrapper.sh — verify check-plugin-manifests.py Rule 6 now
# covers the generated hooks/_dispatch.sh dispatch wrapper (ADR-0002, #613).
#
# Guards the CodeRabbit finding on PR #616: Rule 6 derived expected_wrappers
# only from manifest hook entries, so a stale/missing _dispatch.sh (emitted by
# emit_wrappers outside that loop) slipped through CI undetected.
#
# Usage: bash tests/test_check_dispatch_wrapper.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECK="$ROOT_DIR/scripts/check-plugin-manifests.py"
WRAPPER="$ROOT_DIR/hooks/_dispatch.sh"

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

echo "test_check_dispatch_wrapper"

# Fail fast if the wrapper is absent — otherwise the EXIT trap below would
# write an empty backup back over $WRAPPER and corrupt it.
if [ ! -f "$WRAPPER" ]; then
  echo "FAIL  [wrapper_exists] expected=yes got=no"
  exit 1
fi

# Preserve the wrapper and always restore it, even on early exit.
BACKUP="$(mktemp)"
cp "$WRAPPER" "$BACKUP"
restore() { cp "$BACKUP" "$WRAPPER"; rm -f "$BACKUP"; }
trap restore EXIT

# 1. The wrapper exists (guarded above) and the baseline check is clean.
run_case "wrapper_exists" "yes" "yes"
python3 "$CHECK" >/dev/null 2>&1
run_case "baseline_check_clean" "$?" "0"

# 2. Tampering the wrapper makes the check FAIL and name _dispatch.sh.
printf '\n# tampered by test\n' >> "$WRAPPER"
OUT="$(python3 "$CHECK" 2>&1)"
CODE="$?"
run_case "tampered_check_nonzero" "$CODE" "1"
echo "$OUT" | grep -q "WRAPPER DRIFT hooks/_dispatch.sh"
run_case "tampered_names_dispatch_wrapper" "$?" "0"

# 3. Restoring makes the check clean again.
restore
trap - EXIT
python3 "$CHECK" >/dev/null 2>&1
run_case "restored_check_clean" "$?" "0"

echo "---"
echo "PASS=$PASS FAIL=$FAIL"
if [ "$FAIL" -ne 0 ]; then
  printf 'FAILED: %s\n' "${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
