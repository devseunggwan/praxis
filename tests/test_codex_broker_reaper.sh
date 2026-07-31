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
#   3. (#919) The reap decision itself: a broker with children owns a live codex
#      backend — collect_tree() would kill it, so it must survive even when the
#      idle gate says "idle". Only a childless idle broker may be reaped.
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

# --- Gate 3: #919 owner-liveness — a broker with children is never reaped ----
# Real execution against synthetic processes. The shipped pgrep pattern
# (app-server-broker.mjs) would match this host's PRODUCTION brokers, so the SUT
# is copied with ONLY BROKER_PATTERN rewritten to this run's unique fixture path
# — the reap decision under test is the shipped code, and every pid the copy can
# see is one this test started. TMPDIR/CLAUDE_CONFIG_DIR are redirected so the
# lock and the GC pass stay inside the sandbox.

TMPD="$(mktemp -d /private/tmp/px919.XXXXXX)"
FIXTURE="$TMPD/broker-fixture.sh"
REAPER_COPY="$TMPD/reaper-copy.sh"
FIXTURE_PIDS=()

cleanup_fixtures() {
  local p kid
  for p in "${FIXTURE_PIDS[@]}"; do
    [ -n "$p" ] || continue
    for kid in $(pgrep -P "$p" 2>/dev/null); do kill -KILL "$kid" 2>/dev/null; done
    kill -KILL "$p" 2>/dev/null
  done
  rm -rf "$TMPD"
}
trap cleanup_fixtures EXIT INT TERM

# Blocks on a fifo via the `read` builtin, so a "childless" fixture really has
# no child process of its own.
cat > "$FIXTURE" <<'FIX'
#!/bin/bash
if [ "${PX919_SPAWN_CHILD:-0}" = 1 ]; then
  sleep 600 &
fi
exec 3<> "$PX919_FIFO"
read -r -u 3
FIX

sed "s|^BROKER_PATTERN=.*|BROKER_PATTERN='$FIXTURE'|" "$REAPER" > "$REAPER_COPY"

proc_alive() {
  local st; st="$(ps -o state= -p "$1" 2>/dev/null | tr -d ' ')"
  [ -n "$st" ] || return 1
  case "$st" in Z*) return 1 ;; esac
  return 0
}
proc_gone() {
  local i
  for i in $(seq 1 30); do
    proc_alive "$1" || return 0
    sleep 0.1
  done
  return 1
}

# Start a fixture broker whose command line carries a resolvable sessionDir, and
# backdate its broker.log so the idle gate always considers it idle.
start_fixture() {
  # One assignment per line: bash 3.2 does not let a later `local` assignment
  # see an earlier one from the same statement.
  local name="$1"
  local spawn_child="$2"
  local sdir="$TMPD/cxc-$name"
  local fifo="$TMPD/$name.fifo"
  local pid
  mkdir -p "$sdir"
  mkfifo "$fifo"
  touch -t 202001010000 "$sdir/broker.log"
  PX919_FIFO="$fifo" PX919_SPAWN_CHILD="$spawn_child" \
    bash "$FIXTURE" serve --endpoint "unix:$sdir/broker.sock" >/dev/null 2>&1 &
  pid=$!
  printf '%s' "$pid"   # caller records it in FIXTURE_PIDS (this runs in a subshell)
}

if ! grep -q "^BROKER_PATTERN='$FIXTURE'$" "$REAPER_COPY"; then
  fail "#919: could not isolate BROKER_PATTERN in the reaper copy (refusing to run against real brokers)"
else
  OWNED_PID="$(start_fixture AAA 1)"   # has a child → stands in for a live backend
  LONE_PID="$(start_fixture BBB 0)"    # childless → a real orphan
  FIXTURE_PIDS+=("$OWNED_PID" "$LONE_PID")

  # Wait for the owned fixture's child to exist; otherwise the run would race.
  ready=false
  for _ in $(seq 1 50); do
    if [ -n "$(pgrep -P "$OWNED_PID" 2>/dev/null)" ]; then ready=true; break; fi
    sleep 0.1
  done

  if [ "$ready" != true ] || ! proc_alive "$LONE_PID"; then
    fail "#919: fixture brokers did not come up (owned=$OWNED_PID lone=$LONE_PID)"
  else
    DRY_OUT="$(TMPDIR="$TMPD" CLAUDE_CONFIG_DIR="$TMPD/config" bash "$REAPER_COPY" --reap --max-age 5 --dry-run 2>&1)"

    case "$DRY_OUT" in
      *"SKIP   pid=$OWNED_PID (owner alive"*) pass "#919 dry-run: broker with a child reported as SKIP (owner alive)" ;;
      *) fail "#919 dry-run: expected owner-alive SKIP for pid=$OWNED_PID, got: $DRY_OUT" ;;
    esac
    case "$DRY_OUT" in
      *"WOULD REAP pid=$LONE_PID"*) pass "#919 dry-run: childless idle broker reported as WOULD REAP" ;;
      *) fail "#919 dry-run: expected WOULD REAP for pid=$LONE_PID, got: $DRY_OUT" ;;
    esac

    REAP_OUT="$(TMPDIR="$TMPD" CLAUDE_CONFIG_DIR="$TMPD/config" bash "$REAPER_COPY" --reap --max-age 5 2>&1)"

    # 1. Regression: the idle broker that still owns a backend must survive.
    if proc_alive "$OWNED_PID"; then
      pass "#919 regression: idle broker WITH a child survived --reap"
    else
      fail "#919 regression: idle broker WITH a child was killed by --reap (output: $REAP_OUT)"
    fi
    if [ -d "$TMPD/cxc-AAA" ]; then
      pass "#919 regression: surviving broker's sessionDir kept"
    else
      fail "#919 regression: surviving broker's sessionDir was removed"
    fi
    case "$REAP_OUT" in
      *"SKIP   pid=$OWNED_PID (owner alive"*) pass "#919 regression: skip reason names the live owner" ;;
      *) fail "#919 regression: expected owner-alive SKIP line for pid=$OWNED_PID, got: $REAP_OUT" ;;
    esac

    # 2. The childless idle broker is still reaped (the fix is not a blanket no-op).
    if proc_gone "$LONE_PID"; then
      pass "#919: childless idle broker was reaped"
    else
      fail "#919: childless idle broker survived --reap (output: $REAP_OUT)"
    fi
    if [ ! -d "$TMPD/cxc-BBB" ]; then
      pass "#919: reaped broker's sessionDir removed"
    else
      fail "#919: reaped broker's sessionDir still present"
    fi
    case "$REAP_OUT" in
      *"REAPED pid=$LONE_PID"*) pass "#919: reap reported for the childless broker" ;;
      *) fail "#919: expected REAPED line for pid=$LONE_PID, got: $REAP_OUT" ;;
    esac
  fi
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
