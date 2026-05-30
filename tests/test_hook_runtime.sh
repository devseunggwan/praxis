#!/bin/bash
# test_hook_runtime.sh — behavioral coverage for hooks/_lib/_hook_runtime.py
#
# fail_open is the single, shared entrypoint guard for blocking PreToolUse
# gates (issue #498). Its contract is tested HERE once, instead of being
# re-tested in every hook (issue #470 DRY playbook). Each hook's own suite
# only asserts it opted into the guard (main.__wrapped__ is set).
#
# Asserts:
#   - an uncaught Exception (MemoryError / RecursionError) -> exit 0 (allow)
#   - a real block return (2) is passed through untouched
#   - a normal allow return (0) is passed through
#   - BaseException (KeyboardInterrupt / SystemExit) is NOT swallowed
#   - functools.wraps exposes __wrapped__ on the decorated function

set +e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LIB="$ROOT_DIR/hooks/_lib"

PASS=0
FAIL=0
FAILED_NAMES=()

assert_eq() {
  local name="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "PASS  [$name]"; PASS=$((PASS + 1))
  else
    echo "FAIL  [$name] expected=$expected actual=$actual"
    FAIL=$((FAIL + 1)); FAILED_NAMES+=("$name")
  fi
}

# Each check prints a single token we compare against.
run() {
  python3 - "$LIB" << 'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
from _hook_runtime import fail_open

results = []

@fail_open
def boom_mem():
    raise MemoryError("catastrophic")
results.append(("mem", boom_mem()))

@fail_open
def boom_rec():
    raise RecursionError("deep")
results.append(("rec", boom_rec()))

@fail_open
def real_block():
    return 2
results.append(("block", real_block()))

@fail_open
def allow():
    return 0
results.append(("allow", allow()))

# BaseException must propagate (not become 0).
@fail_open
def interrupt():
    raise KeyboardInterrupt()
try:
    interrupt()
    results.append(("base", "SWALLOWED"))
except KeyboardInterrupt:
    results.append(("base", "PROPAGATED"))

# functools.wraps seam
results.append(("wrapped", "yes" if getattr(real_block, "__wrapped__", None) else "no"))

print(";".join(f"{k}={v}" for k, v in results))
PYEOF
}

OUT="$(run)"
echo "raw: $OUT"

get() { echo "$OUT" | tr ';' '\n' | grep "^$1=" | cut -d= -f2; }

assert_eq "MemoryError fails open -> 0"      "0"          "$(get mem)"
assert_eq "RecursionError fails open -> 0"   "0"          "$(get rec)"
assert_eq "real block (2) passed through"    "2"          "$(get block)"
assert_eq "allow (0) passed through"         "0"          "$(get allow)"
assert_eq "BaseException propagates"         "PROPAGATED" "$(get base)"
assert_eq "functools.wraps __wrapped__ set"  "yes"        "$(get wrapped)"

echo ""
echo "Result: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  printf '  - %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
