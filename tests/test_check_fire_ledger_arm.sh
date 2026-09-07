#!/usr/bin/env bash
# test_check_fire_ledger_arm.sh — verify check-plugin-manifests.py Rule 18 (#848):
# every shell hook arms fire-ledger instrumentation with an executable call.
#
# The rule exists so that deleting the arm call cannot pass CI. Its whole value
# therefore rests on rejecting text that merely *looks* like the call — a
# comment, an echoed string, an assignment — because each of those reads
# identically to a live call while recording nothing at runtime.
#
# Usage: bash tests/test_check_fire_ledger_arm.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECK="$ROOT_DIR/scripts/check-plugin-manifests.py"
# completion-verify is the probe target since #1304 ported codex-review-route,
# the previous target, to Python (Rule 18 only covers impl.sh bodies).
TARGET="$ROOT_DIR/hooks/completion-verify/completion-verify/impl.sh"

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

echo "test_check_fire_ledger_arm"

if [ ! -f "$TARGET" ]; then
  echo "FAIL  [target_exists] expected=yes got=no"
  exit 1
fi

BACKUP="$(mktemp)"
cp "$TARGET" "$BACKUP"
trap 'cp "$BACKUP" "$TARGET"; rm -f "$BACKUP"' EXIT

# The live arm call, matched exactly as it appears in the hook.
ARM='  praxis_fire_arm completion-verify completion-verify "$TELEMETRY_SESSION_ID" ""'

# Replace the arm call with $1 and report the checker's exit code.
sub_arm() {
  python3 - "$TARGET" "$ARM" "$1" <<'PY'
import sys
path, old, new = sys.argv[1:4]
body = open(path, encoding="utf-8").read()
assert old in body, "arm call not found verbatim — fixture is stale"
open(path, "w", encoding="utf-8").write(body.replace(old, new, 1))
PY
  python3 "$CHECK" >/dev/null 2>&1
  echo "$?"
  cp "$BACKUP" "$TARGET"
}

# 1. Baseline: the real call satisfies the rule.
python3 "$CHECK" >/dev/null 2>&1
run_case "baseline_check_clean" "$?" "0"

# 2. Deleting the call entirely is the case the rule was written for.
run_case "deleted_arm_call_fails" "$(sub_arm '')" "1"

# 3-5. Text that mentions the call without executing it must not satisfy it.
#      Each of these passed under the original "appears anywhere" pattern.
run_case "commented_arm_call_fails" \
  "$(sub_arm '  # praxis_fire_arm completion-verify completion-verify "$TELEMETRY_SESSION_ID" ""')" "1"
run_case "echoed_arm_call_fails" \
  "$(sub_arm '  echo "praxis_fire_arm completion-verify completion-verify"')" "1"
run_case "assigned_arm_call_fails" \
  "$(sub_arm "  MSG='praxis_fire_arm completion-verify completion-verify'")" "1"

# 6. A different hook's name in the call does not satisfy this hook's rule —
#    the match is name-anchored, so a copy-paste from a sibling still fails.
run_case "wrong_hook_name_fails" \
  "$(sub_arm '  praxis_fire_arm retrospect-mix-check completion-verify "$TELEMETRY_SESSION_ID" ""')" "1"

echo "----"
echo "PASS: $PASS / FAIL: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  printf 'failed: %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
