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

# Observability: swallowed exception is recorded to the JSONL log; a failed
# log write still fails open.
TMP_LOG="$(mktemp -u "${TMPDIR:-/tmp}/praxis-hook-err-test-XXXXXX").jsonl"
rc_logged=$(PRAXIS_HOOK_ERROR_LOG="$TMP_LOG" python3 - "$LIB" << 'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
from _hook_runtime import fail_open
@fail_open
def boom():
    raise ValueError("diagnose me")
print(boom())
PYEOF
)
assert_eq "swallowed exc still returns 0 (with logging)" "0" "$rc_logged"
if [ -s "$TMP_LOG" ] && grep -q '"exc_type": "ValueError"' "$TMP_LOG" \
   && grep -q '"message": "diagnose me"' "$TMP_LOG" && grep -q '"traceback"' "$TMP_LOG"; then
  echo "PASS  [error log records swallowed exception (type+message+traceback)]"; PASS=$((PASS + 1))
else
  echo "FAIL  [error log missing/incomplete] content=$(cat "$TMP_LOG" 2>/dev/null | head -c 200)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("error log records swallowed exception")
fi
rm -f "$TMP_LOG"

# Default placement (no PRAXIS_HOOK_ERROR_LOG): lands under <PRAXIS_HOME>/logs.
TMP_HOME=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
rc_default=$(env -u PRAXIS_HOOK_ERROR_LOG PRAXIS_HOME="$TMP_HOME" python3 - "$LIB" << 'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
from _hook_runtime import fail_open
@fail_open
def boom():
    raise ValueError("home-routed")
print(boom())
PYEOF
)
assert_eq "default error log routes via ~/.praxis (returns 0)" "0" "$rc_default"
if [ -s "$TMP_HOME/logs/hook-errors.jsonl" ] \
   && grep -q '"message": "home-routed"' "$TMP_HOME/logs/hook-errors.jsonl"; then
  echo "PASS  [default error log written under <PRAXIS_HOME>/logs/hook-errors.jsonl]"; PASS=$((PASS + 1))
else
  echo "FAIL  [default error log not under PRAXIS_HOME/logs]"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("default error log placement")
fi
rm -rf "$TMP_HOME"

# Size rotation (issue #1282): a log past the cap is renamed to <log>.1 and a
# fresh file takes the next record, so a hook that crashes on every call
# cannot grow the file without bound. The cap is set tiny so the existing
# content is already past it; 0 must leave the file alone.
ROT_LOG="$(mktemp -u "${TMPDIR:-/tmp}/praxis-hook-rot-test-XXXXXX").jsonl"
head -c 4096 /dev/zero | tr '\0' 'x' > "$ROT_LOG"
rc_rot=$(PRAXIS_HOOK_ERROR_LOG="$ROT_LOG" PRAXIS_HOOK_ERROR_LOG_MAX_BYTES=1024 python3 - "$LIB" << 'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
from _hook_runtime import fail_open
@fail_open
def boom():
    raise ValueError("after-rotation")
print(boom())
PYEOF
)
assert_eq "rotation keeps fail-open (returns 0)" "0" "$rc_rot"
if [ -f "$ROT_LOG.1" ] && [ "$(wc -c < "$ROT_LOG.1" | tr -d ' ')" = "4096" ] \
   && grep -q '"message": "after-rotation"' "$ROT_LOG" \
   && [ "$(wc -c < "$ROT_LOG" | tr -d ' ')" -lt 4096 ]; then
  echo "PASS  [error log past the cap rotates to .1 and restarts]"; PASS=$((PASS + 1))
else
  echo "FAIL  [error log did not rotate] main=$(wc -c < "$ROT_LOG" 2>/dev/null) backup=$(wc -c < "$ROT_LOG.1" 2>/dev/null)"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("error log rotation")
fi
rm -f "$ROT_LOG" "$ROT_LOG.1"

# Two processes past the cap at once: the second rollover must find the file
# already rotated and only reopen, never delete the `.1` the first one saved.
# Simulated in one process: a handler whose stream is on the old (now `.1`)
# file while the base file is small again.
race_out=$(PRAXIS_HOOK_ERROR_LOG_MAX_BYTES=1024 python3 - "$LIB" << 'PYEOF'
import os, sys, tempfile
sys.path.insert(0, sys.argv[1])
import _hook_runtime as R
d = tempfile.mkdtemp()
base = os.path.join(d, "hook-errors.jsonl")
h = R._make_error_log_handler(base, 1024)
with open(base, "w") as f:
    f.write("x" * 4096)
h.acquire(); h.stream = h._open(); h.release()   # stream on the big file
os.rename(base, base + ".1")                      # another process rotated
with open(base, "w") as f:
    f.write("fresh")                              # ...and started a small one
h.doRollover()
print("backup_kept=%d base=%r" % (os.path.getsize(base + ".1"), open(base).read()))
PYEOF
)
assert_eq "second rollover keeps the sibling's backup" "backup_kept=4096 base='fresh'" "$race_out"

ROT_LOG="$(mktemp -u "${TMPDIR:-/tmp}/praxis-hook-rot0-test-XXXXXX").jsonl"
head -c 4096 /dev/zero | tr '\0' 'x' > "$ROT_LOG"
PRAXIS_HOOK_ERROR_LOG="$ROT_LOG" PRAXIS_HOOK_ERROR_LOG_MAX_BYTES=0 python3 - "$LIB" << 'PYEOF' >/dev/null
import sys
sys.path.insert(0, sys.argv[1])
from _hook_runtime import fail_open
@fail_open
def boom():
    raise ValueError("no-rotation")
boom()
PYEOF
if [ ! -e "$ROT_LOG.1" ] && grep -q '"message": "no-rotation"' "$ROT_LOG"; then
  echo "PASS  [MAX_BYTES=0 disables rotation]"; PASS=$((PASS + 1))
else
  echo "FAIL  [MAX_BYTES=0 rotated or dropped the record]"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("rotation disable")
fi
rm -f "$ROT_LOG" "$ROT_LOG.1"

# Unwritable log path must NOT re-break fail-open (recorder is self-guarded).
rc_unwritable=$(PRAXIS_HOOK_ERROR_LOG="/proc/nonexistent-dir/err.jsonl" python3 - "$LIB" << 'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
from _hook_runtime import fail_open
@fail_open
def boom():
    raise RuntimeError("x")
print(boom())
PYEOF
)
assert_eq "unwritable error log still fails open -> 0" "0" "$rc_unwritable"

# Unwritable log + stderr opt-in OFF must leak NOTHING to stderr (guards the
# lastResort/handleError hazard; NullHandler + raiseExceptions=False).
stderr_unwritable=$(env -u PRAXIS_HOOK_ERROR_STDERR PRAXIS_HOOK_ERROR_LOG="/proc/nonexistent-dir/err.jsonl" python3 - "$LIB" << 'PYEOF' 2>&1 1>/dev/null
import sys
sys.path.insert(0, sys.argv[1])
from _hook_runtime import fail_open
@fail_open
def boom():
    raise ValueError("no-stderr-leak")
boom()
PYEOF
)
assert_eq "unwritable log leaks nothing to stderr (no lastResort)" "" "$stderr_unwritable"

# Opt-in stderr surfacing: off by default, on with PRAXIS_HOOK_ERROR_STDERR=1.
TMP_LOG2="$(mktemp -u "${TMPDIR:-/tmp}/praxis-hook-err-test2-XXXXXX").jsonl"
stderr_default=$(PRAXIS_HOOK_ERROR_LOG="$TMP_LOG2" python3 - "$LIB" << 'PYEOF' 2>&1 1>/dev/null
import sys
sys.path.insert(0, sys.argv[1])
from _hook_runtime import fail_open
@fail_open
def boom():
    raise ValueError("q")
boom()
PYEOF
)
assert_eq "stderr quiet by default" "" "$stderr_default"
stderr_optin=$(PRAXIS_HOOK_ERROR_LOG="$TMP_LOG2" PRAXIS_HOOK_ERROR_STDERR=1 python3 - "$LIB" << 'PYEOF' 2>&1 1>/dev/null
import sys
sys.path.insert(0, sys.argv[1])
from _hook_runtime import fail_open
@fail_open
def boom():
    raise ValueError("q")
boom()
PYEOF
)
case "$stderr_optin" in
  *"[praxis:hook-error]"*"ValueError"*) echo "PASS  [opt-in stderr surfaces hook error]"; PASS=$((PASS + 1)) ;;
  *) echo "FAIL  [opt-in stderr] got=$stderr_optin"; FAIL=$((FAIL + 1)); FAILED_NAMES+=("opt-in stderr surfaces hook error") ;;
esac
rm -f "$TMP_LOG2"

echo ""
echo "Result: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  printf '  - %s\n' "${FAILED_NAMES[@]}"
  exit 1
fi
exit 0
