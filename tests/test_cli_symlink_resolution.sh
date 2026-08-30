#!/usr/bin/env bash
# test_cli_symlink_resolution.sh — regression coverage for issue #1174.
#
# cmux-session-status / cmux-session-cleanup source their sibling
# cmux-session-lib via `dirname "$0"`, and claude-recover locates its sibling
# claude-recover-scan the same way. install.sh ships these as symlinks in
# ~/.local/bin, where $0 is the *symlink* path — cmux-session-lib is
# deliberately NOT in CLI_SCRIPTS (it is a sourced library, not an
# executable), so no sibling exists next to the link and the scripts died at
# the `source` line with "cmux-session-lib: No such file or directory"
# (claude-recover only survived because claude-recover-scan happens to be
# installed alongside it). The fix ports the readlink resolution loop from
# cmux-recover-sessions; this suite runs each script through a symlink in a
# temp "bin" dir and asserts it gets past sibling resolution.
#
# Determinism: a stub `cmux` (always exit 1) is prepended to PATH so the cmux
# scripts always stop at preflight_check ("cmux is not running") — the first
# check AFTER the source line — whether or not real cmux is installed.
# claude-recover runs with an isolated $HOME so it scans an empty session
# store and exits 0 without touching the developer's real ~/.claude*.
#
# Usage: bash tests/test_cli_symlink_resolution.sh
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

echo "test_cli_symlink_resolution"

# ---------------------------------------------------------------------------
# Fixture: temp "bin" dir with symlinks to the real scripts (absolute and
# chained-relative, mirroring what install.sh writes), plus a failing cmux
# stub and an empty HOME.
# ---------------------------------------------------------------------------

TMP_ROOT=$(mktemp -d) || { echo "FATAL: mktemp -d failed — no writable temp dir" >&2; exit 1; }
trap 'rm -rf "$TMP_ROOT"' EXIT

BIN="$TMP_ROOT/bin"
STUB="$TMP_ROOT/stub"
FAKE_HOME="$TMP_ROOT/home"
mkdir -p "$BIN" "$STUB" "$FAKE_HOME"

printf '#!/bin/sh\nexit 1\n' > "$STUB/cmux"
chmod +x "$STUB/cmux"

ln -s "$ROOT_DIR/skills/cmux-session-manager/cmux-session-status"  "$BIN/cmux-session-status"
ln -s "$ROOT_DIR/skills/cmux-session-manager/cmux-session-cleanup" "$BIN/cmux-session-cleanup"
ln -s "$ROOT_DIR/skills/recover-sessions/claude-recover"           "$BIN/claude-recover"

# ---------------------------------------------------------------------------
# cmux-session-status / cmux-session-cleanup through an absolute symlink:
# must get past the `source cmux-session-lib` line and stop at
# preflight_check's deterministic "cmux is not running" instead.
# ---------------------------------------------------------------------------

out=$(PATH="$STUB:$PATH" "$BIN/cmux-session-status" 2>&1); rc=$?
echo "$out" | grep -q 'cmux-session-lib'
run_case "status: no cmux-session-lib source error via symlink" "$?" "1"
echo "$out" | grep -q 'cmux is not running'
run_case "status: reaches preflight_check (past source line)" "$?" "0"
run_case "status: preflight failure exits 1 (not source death)" "$rc" "1"

out=$(PATH="$STUB:$PATH" "$BIN/cmux-session-cleanup" --dry-run 2>&1); rc=$?
echo "$out" | grep -q 'cmux-session-lib'
run_case "cleanup: no cmux-session-lib source error via symlink" "$?" "1"
echo "$out" | grep -q 'cmux is not running'
run_case "cleanup: reaches preflight_check (past source line)" "$?" "0"
run_case "cleanup: preflight failure exits 1 (not source death)" "$rc" "1"

# ---------------------------------------------------------------------------
# Chained relative symlink (link -> relative link -> real script): the
# resolution loop must follow multiple hops and relative targets.
# ---------------------------------------------------------------------------

mkdir -p "$TMP_ROOT/hop"
ln -s "$ROOT_DIR/skills/cmux-session-manager/cmux-session-status" "$TMP_ROOT/hop/status-hop"
ln -s "../hop/status-hop" "$BIN/status-rel"

out=$(PATH="$STUB:$PATH" "$BIN/status-rel" 2>&1); rc=$?
echo "$out" | grep -q 'cmux-session-lib'
run_case "status: no source error via chained relative symlink" "$?" "1"
echo "$out" | grep -q 'cmux is not running'
run_case "status: chained relative symlink reaches preflight_check" "$?" "0"

# ---------------------------------------------------------------------------
# claude-recover through a symlink: must resolve sibling claude-recover-scan
# next to the REAL script (no scan copy exists in $BIN) and complete a --list
# run against an empty HOME.
# ---------------------------------------------------------------------------

out=$(HOME="$FAKE_HOME" "$BIN/claude-recover" --list 2>&1); rc=$?
echo "$out" | grep -q "can't open file"
run_case "recover: no claude-recover-scan open failure via symlink" "$?" "1"
echo "$out" | grep -q 'No sessions to recover.'
run_case "recover: --list completes against empty HOME" "$?" "0"
run_case "recover: --list exits 0" "$rc" "0"

# ---------------------------------------------------------------------------
# Direct (non-symlink) invocation still works after the fix.
# ---------------------------------------------------------------------------

out=$(PATH="$STUB:$PATH" "$ROOT_DIR/skills/cmux-session-manager/cmux-session-status" 2>&1)
echo "$out" | grep -q 'cmux is not running'
run_case "status: direct invocation still reaches preflight_check" "$?" "0"

out=$(HOME="$FAKE_HOME" "$ROOT_DIR/skills/recover-sessions/claude-recover" --list 2>&1); rc=$?
run_case "recover: direct invocation still exits 0" "$rc" "0"

# ---------------------------------------------------------------------------

echo ""
echo "=== summary ==="
echo "PASS: $PASS"
echo "FAIL: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
exit 0
