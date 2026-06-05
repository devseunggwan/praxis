#!/usr/bin/env bash
# test_check_opt_in_wrapper.sh — verify check-plugin-manifests.py Rule 6c now
# covers the OPT_IN_HOOKS wrapper class (#605).
#
# Guards M1: Rule 6 derived expected_wrappers only from manifest hook entries,
# so an opt-in wrapper (e.g. hooks/external-write-falsify-check.sh — emitted by
# emit_wrappers' separate OPT_IN_HOOKS loop, NOT a manifest entry) had its
# byte-identity unchecked. A stale or missing opt-in wrapper slipped through CI.
#
# Usage: bash tests/test_check_opt_in_wrapper.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECK="$ROOT_DIR/scripts/check-plugin-manifests.py"
# The sole OPT_IN_HOOKS member (scripts/constants.py). Mirrors the dispatch
# wrapper test's hardcoded _dispatch.sh — same coupling to the build constants.
WRAPPER="$ROOT_DIR/hooks/external-write-falsify-check.sh"

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

echo "test_check_opt_in_wrapper"

# Fail fast if the wrapper is absent — otherwise the EXIT trap below would
# write an empty backup back over $WRAPPER and corrupt it.
if [ ! -f "$WRAPPER" ]; then
  echo "FAIL  [wrapper_exists] expected=yes got=no"
  exit 1
fi

# Preserve the wrapper and always restore it, even on early exit.
BACKUP="$(mktemp)"
cp "$WRAPPER" "$BACKUP"
restore() { cp "$BACKUP" "$WRAPPER"; chmod 755 "$WRAPPER"; }
trap 'restore; rm -f "$BACKUP"' EXIT

# 1. The wrapper exists (guarded above) and the baseline check is clean.
run_case "wrapper_exists" "yes" "yes"
python3 "$CHECK" >/dev/null 2>&1
run_case "baseline_check_clean" "$?" "0"

# 2. Tampering the wrapper makes the check FAIL and name the opt-in wrapper.
printf '\n# tampered by test\n' >> "$WRAPPER"
OUT="$(python3 "$CHECK" 2>&1)"
CODE="$?"
run_case "tampered_check_nonzero" "$CODE" "1"
echo "$OUT" | grep -q "WRAPPER DRIFT hooks/external-write-falsify-check.sh"
run_case "tampered_names_opt_in_wrapper" "$?" "0"

# 3. Restoring makes the check clean again.
restore
python3 "$CHECK" >/dev/null 2>&1
run_case "restored_check_clean" "$?" "0"

# 4. Deleting the wrapper makes the check FAIL with a MISSING (not DRIFT) signal.
rm -f "$WRAPPER"
OUT="$(python3 "$CHECK" 2>&1)"
CODE="$?"
run_case "missing_check_nonzero" "$CODE" "1"
echo "$OUT" | grep -q "WRAPPER MISSING hooks/external-write-falsify-check.sh"
run_case "missing_names_opt_in_wrapper" "$?" "0"

# 5. Restoring (again) makes the check clean.
restore
trap - EXIT
rm -f "$BACKUP"
python3 "$CHECK" >/dev/null 2>&1
run_case "restored_check_clean_2" "$?" "0"

echo "---"
echo "PASS=$PASS FAIL=$FAIL"
if [ "$FAIL" -ne 0 ]; then
  printf 'FAILED: %s\n' "${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
