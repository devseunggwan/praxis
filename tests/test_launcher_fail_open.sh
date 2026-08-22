#!/usr/bin/env bash
# test_launcher_fail_open.sh — the generated hook launchers must fall through
# when their impl cannot be found, and must still forward a real deny (#1053).
#
# Two halves, and the second is the one that keeps the fix honest:
#   1. impl absent  -> exit 0. python3 exits 2 on "can't open file", and 2 is
#      this repo's deny code (_dispatch.py aggregates on rc == 2), so an absent
#      impl otherwise reaches the host as a block nobody decided.
#   2. impl exits 2 -> exit 2. The reviewed alternative (`python3 … || exit 0`)
#      passes half 1 and silently disarms every gate in the repo.
#
# The launchers under test are copied into a scratch tree with a stub impl, so
# no real impl is executed and the repo is never mutated.
#
# Usage: bash tests/test_launcher_fail_open.sh
# Exit:  0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

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

echo "test_launcher_fail_open"

TMP="$(mktemp -d)" || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
trap 'rm -rf "$TMP"' EXIT

# Resolve one launcher per template from the generated tree, so the test breaks
# if a template stops being emitted rather than silently covering nothing.
PY_LAUNCHER="$ROOT_DIR/hooks/approval-premise-reread-gate.sh"
PY_IMPL_REL="preflight-gate/approval-premise-reread-gate/impl.py"
SH_LAUNCHER="$ROOT_DIR/hooks/strike-counter.sh"
SH_IMPL_REL="completion-verify/strike-counter/impl.sh"
DISPATCH_LAUNCHER="$ROOT_DIR/hooks/_dispatch.sh"
DISPATCH_IMPL_REL="_lib/_dispatch.py"

for f in "$PY_LAUNCHER" "$SH_LAUNCHER" "$DISPATCH_LAUNCHER"; do
  if [ ! -f "$f" ]; then
    echo "FAIL  [launcher_present] missing $f"
    exit 1
  fi
done
run_case "launchers_present" "yes" "yes"

# stage <launcher> <impl-rel-path> -> scratch copy, impl NOT created yet
stage() {
  local launcher="$1" impl_rel="$2" dir="$3"
  rm -rf "$dir"
  mkdir -p "$dir/$(dirname "$impl_rel")"
  cp "$launcher" "$dir/launcher.sh"
  chmod +x "$dir/launcher.sh"
}

# --- python-wrapper template -------------------------------------------------

PY_DIR="$TMP/py"
stage "$PY_LAUNCHER" "$PY_IMPL_REL" "$PY_DIR"

echo '{}' | sh "$PY_DIR/launcher.sh" >/dev/null 2>&1
run_case "py_missing_impl_is_fail_open" "$?" "0"

printf 'import sys\nsys.exit(2)\n' > "$PY_DIR/$PY_IMPL_REL"
echo '{}' | sh "$PY_DIR/launcher.sh" >/dev/null 2>&1
run_case "py_deny_is_forwarded" "$?" "2"

printf 'import sys\nsys.exit(0)\n' > "$PY_DIR/$PY_IMPL_REL"
echo '{}' | sh "$PY_DIR/launcher.sh" >/dev/null 2>&1
run_case "py_pass_is_forwarded" "$?" "0"

# A crashing impl keeps its own non-blocking code — Claude Code treats 1 as a
# non-blocking error, so flattening it to 0 would only hide the breakage.
printf 'raise RuntimeError("boom")\n' > "$PY_DIR/$PY_IMPL_REL"
echo '{}' | sh "$PY_DIR/launcher.sh" >/dev/null 2>&1
run_case "py_crash_is_not_deny" "$?" "1"

# --- body-as-sh wrapper template ---------------------------------------------

SH_DIR="$TMP/sh"
stage "$SH_LAUNCHER" "$SH_IMPL_REL" "$SH_DIR"

echo '{}' | sh "$SH_DIR/launcher.sh" >/dev/null 2>&1
run_case "sh_missing_impl_is_fail_open" "$?" "0"

printf '#!/bin/bash\nexit 2\n' > "$SH_DIR/$SH_IMPL_REL"
chmod +x "$SH_DIR/$SH_IMPL_REL"
echo '{}' | sh "$SH_DIR/launcher.sh" >/dev/null 2>&1
run_case "sh_deny_is_forwarded" "$?" "2"

# --- dispatch wrapper template -----------------------------------------------

D_DIR="$TMP/dispatch"
stage "$DISPATCH_LAUNCHER" "$DISPATCH_IMPL_REL" "$D_DIR"

echo '{}' | sh "$D_DIR/launcher.sh" >/dev/null 2>&1
run_case "dispatch_missing_impl_is_fail_open" "$?" "0"

printf 'import sys\nsys.exit(2)\n' > "$D_DIR/$DISPATCH_IMPL_REL"
echo '{}' | sh "$D_DIR/launcher.sh" >/dev/null 2>&1
run_case "dispatch_deny_is_forwarded" "$?" "2"

echo
echo "test_launcher_fail_open: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  printf 'failed: %s\n' "${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
