#!/bin/bash
# tests/test_codex_broker_reaper.sh — guard the reaper's destructive-op safety gates
#
# codex-broker-reaper.sh runs `rm -rf` on broker sessionDirs and SIGKILLs broker
# trees. Two validation gates protect those destructive ops:
#   1. --max-age must be an integer >= 1. A zero-valued age makes max_age_sec=0,
#      so the idle gate skips nothing and fresh, in-use brokers get reaped.
#   2. is_safe_session_dir() must reject traversal-shaped paths. A path like
#      /tmp/../Users/me/cxc-x string-matches the /tmp/* allowlist yet resolves
#      OUTSIDE the temp root, letting rm -rf escape.
#
# Run:  ./tests/test_codex_broker_reaper.sh
# Exit: 0 on success, 1 on first failure (after summary).

set +e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REAPER="$REPO_ROOT/skills/codex-review-wrap/codex-broker-reaper.sh"

if [ ! -f "$REAPER" ]; then
  echo "FAIL: codex-broker-reaper.sh not found at $REAPER" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()

pass() { PASS=$((PASS + 1)); echo "  PASS  $1"; }
fail() { FAIL=$((FAIL + 1)); FAILED_NAMES+=("$1"); echo "  FAIL  $1" >&2; }

# --- Gate 1: --max-age validation -------------------------------------------
# Expect exit 2 (rejected) for non-positive / non-numeric values.
assert_max_age_rejected() {
  local val="$1"
  bash "$REAPER" --reap --max-age "$val" --dry-run >/dev/null 2>&1
  if [ $? -eq 2 ]; then pass "--max-age '$val' rejected"; else fail "--max-age '$val' should be rejected (exit 2)"; fi
}
# Expect non-2 (accepted) for positive integers, including leading-zero forms.
assert_max_age_accepted() {
  local val="$1"
  bash "$REAPER" --reap --max-age "$val" --dry-run >/dev/null 2>&1
  if [ $? -ne 2 ]; then pass "--max-age '$val' accepted"; else fail "--max-age '$val' should be accepted"; fi
}

for v in 0 00 000 abc "" "1.5" "-1"; do assert_max_age_rejected "$v"; done
for v in 1 5 30 030; do assert_max_age_accepted "$v"; done

# --- Gate 2: is_safe_session_dir traversal hardening ------------------------
# Extract the pure function and exercise it in isolation (the script body runs
# lock acquisition on source, so we lift just the function).
FN="$(sed -n '/^is_safe_session_dir()/,/^}/p' "$REAPER")"
if [ -z "$FN" ]; then
  fail "could not extract is_safe_session_dir() from reaper"
else
  safe_rc() { bash -c "$FN"$'\n'"is_safe_session_dir \"\$1\"; echo \$?" _ "$1"; }
  assert_safe() {
    local d="$1"
    if [ "$(safe_rc "$d")" = 0 ]; then pass "safe: '$d'"; else fail "'$d' should be SAFE"; fi
  }
  assert_reject() {
    local d="$1"
    if [ "$(safe_rc "$d")" != 0 ]; then pass "reject: '$d'"; else fail "'$d' should be REJECTED"; fi
  }

  # Clean absolute cxc-* dirs under known temp roots → SAFE.
  assert_safe "/tmp/cxc-abc"
  assert_safe "/var/folders/xx/cxc-y"
  assert_safe "/private/tmp/cxc-z"
  assert_safe "/private/var/folders/aa/cxc-w"

  # Traversal-shaped and out-of-root paths → REJECT.
  assert_reject "/tmp/../Users/me/cxc-x"      # embedded /../ escapes temp root
  assert_reject "/tmp/cxc-a/../../../etc"      # climbs out
  assert_reject "/tmp/cxc-a/.."                # trailing /..
  assert_reject "//tmp/cxc-a"                  # empty segment
  assert_reject "relative/cxc-a"               # not absolute
  assert_reject "/tmp/notcxc"                  # basename not cxc-*
  assert_reject "/etc/cxc-a"                   # outside allowlist
fi

echo ""
echo "=== summary ==="
echo "PASS: $PASS"
echo "FAIL: $FAIL"

if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
  exit 1
fi

exit 0
