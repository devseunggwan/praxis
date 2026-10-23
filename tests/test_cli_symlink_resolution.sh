#!/usr/bin/env bash
# test_cli_symlink_resolution.sh — regression coverage for issue #1174.
#
# cmux-recover-sessions locates its sibling claude-recover-scan via the
# directory of the script. install.sh ships both as symlinks in ~/.local/bin,
# where $0 is the *symlink* path, so the script resolves the link chain with a
# readlink loop (capped at 40 hops) before it computes SCRIPT_DIR. This suite
# runs the script through a symlink in a temp "bin" dir that holds no copy of
# the scanner, asserts it still finds claude-recover-scan next to the REAL
# script, and drives the loop against a symlink cycle. The block's presence
# and hop cap are also guarded in tests/test_cli_scripts_parity.sh.
#
# Determinism: every run uses --list (which never calls cmux) and an isolated
# $HOME, so the scan sees an empty session store and exits 0 without touching
# the developer's real ~/.claude*.
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
FAKE_HOME="$TMP_ROOT/home"
mkdir -p "$BIN" "$FAKE_HOME"

RECOVER="$ROOT_DIR/skills/cmux-recover-sessions/cmux-recover-sessions"
ln -s "$RECOVER" "$BIN/cmux-recover-sessions"

# ---------------------------------------------------------------------------
# Absolute symlink: must resolve sibling claude-recover-scan next to the REAL
# script (no scan copy exists in $BIN) and complete a --list run against an
# empty HOME.
# ---------------------------------------------------------------------------

out=$(HOME="$FAKE_HOME" "$BIN/cmux-recover-sessions" --list 2>&1); rc=$?
echo "$out" | grep -q "can't open file"
run_case "recover: no claude-recover-scan open failure via symlink" "$?" "1"
echo "$out" | grep -q 'No sessions to recover.'
run_case "recover: --list completes against empty HOME" "$?" "0"
run_case "recover: --list exits 0" "$rc" "0"

# ---------------------------------------------------------------------------
# Chained relative symlink (link -> relative link -> real script): the
# resolution loop must follow multiple hops and relative targets.
# ---------------------------------------------------------------------------

mkdir -p "$TMP_ROOT/hop"
ln -s "$RECOVER" "$TMP_ROOT/hop/recover-hop"
ln -s "../hop/recover-hop" "$BIN/recover-rel"

out=$(HOME="$FAKE_HOME" "$BIN/recover-rel" --list 2>&1); rc=$?
echo "$out" | grep -q "can't open file"
run_case "recover: no scan open failure via chained relative symlink" "$?" "1"
echo "$out" | grep -q 'No sessions to recover.'
run_case "recover: chained relative symlink completes --list" "$?" "0"
run_case "recover: chained relative symlink exits 0" "$rc" "0"

# ---------------------------------------------------------------------------
# Symlink cycle: the resolution loop must bail with a clear error after its
# hop cap instead of spinning forever. A cyclic link cannot be
# exec'd (the kernel refuses with ELOOP before bash ever runs), so the loop
# is exercised directly: extract the shipped block from cmux-recover-sessions
# and run it with $0 pointed at the cycle via `bash -c '...' <argv0>`.
# The watchdog is pure bash rather than `timeout`, which stock macOS does not
# ship: a `command -v timeout` fallback would leave this case unguarded on
# exactly the hosts where a hop-cap regression hangs the suite. rc 137 means
# the loop spun until killed.
# ---------------------------------------------------------------------------

ln -s "$BIN/cycle-b" "$BIN/cycle-a"
ln -s "$BIN/cycle-a" "$BIN/cycle-b"

BLOCK=$(awk '/^REAL_PATH="\$0"$/{f=1} f{print} f && /^SCRIPT_DIR=/{exit}' \
  "$RECOVER")
run_case "cycle: resolution block extracted from shipped script" \
  "$([ -n "$BLOCK" ] && echo yes || echo no)" "yes"

CYCLE_OUT="$TMP_ROOT/cycle.out"
bash -c "set -euo pipefail
$BLOCK
echo \"RESOLVED \$SCRIPT_DIR\"" "$BIN/cycle-a" >"$CYCLE_OUT" 2>&1 &
cycle_pid=$!
( sleep 5; kill -9 "$cycle_pid" 2>/dev/null ) &
watchdog_pid=$!
wait "$cycle_pid"; rc=$?
kill "$watchdog_pid" 2>/dev/null
wait "$watchdog_pid" 2>/dev/null
out=$(cat "$CYCLE_OUT")
run_case "cycle: hop cap bails with exit 1 (no hang, no resolve)" "$rc" "1"
echo "$out" | grep -q 'symlink resolution exceeded'
run_case "cycle: error message names the hop cap" "$?" "0"

# ---------------------------------------------------------------------------
# Direct (non-symlink) invocation still works.
# ---------------------------------------------------------------------------

out=$(HOME="$FAKE_HOME" "$RECOVER" --list 2>&1); rc=$?
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
