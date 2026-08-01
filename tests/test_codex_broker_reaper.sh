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
#   3. (#919) The reap decision itself. Idleness does not imply the owner is
#      gone, so a kill needs positive orphan evidence about the broker's
#      WORKSPACE ROOT: it was deleted, or no live process has it as its cwd.
#      Anything undetermined must keep the broker. (Children are NOT a liveness
#      signal — the broker spawns `codex app-server` at startup and closes it
#      only on its own shutdown, so orphans keep a child too.)
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

# --- Gate 3: #919 owner-liveness oracle (behavior) ---------------------------
# Real execution against synthetic processes. The shipped pgrep pattern
# (app-server-broker.mjs) would match this host's PRODUCTION brokers, so the SUT
# is copied with ONLY BROKER_PATTERN rewritten to this run's unique fixture path
# — the reap decision under test is the shipped code, and every pid the copy can
# see is one this test started. CLAUDE_CONFIG_DIR points the state root at a
# sandbox tree, so the real ~/.claude state dirs are never read or written, and
# TMPDIR keeps the lock inside the sandbox too.
#
# Four fixture brokers, all equally idle, differing only in owner evidence:
#   ALIVE   workspace exists + a live process cwd'd there   → must survive
#   WSGONE  workspaceRoot recorded, directory deleted       → reap (signal C)
#   NOCWD   workspace exists, nothing working in it         → reap (signal D)
#   NOJOBS  state dir without jobs/*.json (undeterminable)  → must survive
#   NOSTATE no state dir claims the pid (undeterminable)    → must survive

TMPD="$(mktemp -d /private/tmp/px919.XXXXXX)"
FIXTURE="$TMPD/broker-fixture.sh"
HOLDER="$TMPD/cwd-holder.sh"
REAPER_COPY="$TMPD/reaper-copy.sh"
STATE_ROOT="$TMPD/config/plugins/data/codex-openai-codex/state"
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

# Both fixtures block on a fifo via the `read` builtin — no child process, no
# busy loop, and they die only when killed.
cat > "$FIXTURE" <<'FIX'
#!/bin/bash
exec 3<> "$PX919_FIFO"
read -r -u 3
FIX

cat > "$HOLDER" <<'HOLD'
#!/bin/bash
cd "$PX919_CWD" || exit 1
exec 3<> "$PX919_FIFO"
read -r -u 3
HOLD

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

# The plugin keys its state dir on the workspace root: <slug>-<sha256[0:16]>.
ws_hash() { printf '%s' "$1" | shasum -a 256 | cut -c1-16; }

# A fixture broker whose command line carries a resolvable sessionDir, with a
# backdated broker.log so the idle gate always passes.
start_broker() {
  local name="$1"
  local sdir="$TMPD/cxc-$name"
  local fifo="$TMPD/$name.fifo"
  local pid
  mkdir -p "$sdir"
  mkfifo "$fifo"
  touch -t 202001010000 "$sdir/broker.log"
  PX919_FIFO="$fifo" bash "$FIXTURE" serve --endpoint "unix:$sdir/broker.sock" >/dev/null 2>&1 &
  pid=$!
  printf '%s' "$pid"   # caller records it in FIXTURE_PIDS (this runs in a subshell)
}

# A process parked in $1, i.e. what "someone is working in this workspace"
# looks like to the cwd scan.
start_cwd_holder() {
  local dir="$1"
  local fifo="$TMPD/holder-$2.fifo"
  local pid
  mkfifo "$fifo"
  PX919_FIFO="$fifo" PX919_CWD="$dir" bash "$HOLDER" >/dev/null 2>&1 &
  pid=$!
  printf '%s' "$pid"
}

# state dir for a broker pid: broker.json carries the pid, jobs/*.json the
# workspaceRoot — the same two files the oracle reads in production.
write_state() {
  local name="$1" wroot="$2" pid="$3" with_jobs="$4"
  local sd
  sd="$STATE_ROOT/$name-$(ws_hash "$wroot")"
  mkdir -p "$sd"
  printf '{"sessionDir":"%s","pid":%s}\n' "$TMPD/cxc-$name" "$pid" > "$sd/broker.json"
  if [ "$with_jobs" = 1 ]; then
    mkdir -p "$sd/jobs"
    printf '{"id":"job-%s","workspaceRoot":"%s"}\n' "$name" "$wroot" > "$sd/jobs/job.json"
  fi
}

if ! grep -q "^BROKER_PATTERN='$FIXTURE'$" "$REAPER_COPY"; then
  fail "#919: could not isolate BROKER_PATTERN in the reaper copy (refusing to run against real brokers)"
else
  WS_ALIVE="$TMPD/ws-alive"; WS_GONE="$TMPD/ws-gone"
  WS_NOCWD="$TMPD/ws-nocwd"; WS_NOJOBS="$TMPD/ws-nojobs"; WS_NOSTATE="$TMPD/ws-nostate"
  mkdir -p "$WS_ALIVE" "$WS_GONE" "$WS_NOCWD" "$WS_NOJOBS" "$WS_NOSTATE"

  PID_ALIVE="$(start_broker ALIVE)"
  PID_WSGONE="$(start_broker WSGONE)"
  PID_NOCWD="$(start_broker NOCWD)"
  PID_NOJOBS="$(start_broker NOJOBS)"
  PID_NOSTATE="$(start_broker NOSTATE)"
  HOLDER_PID="$(start_cwd_holder "$WS_ALIVE" alive)"
  FIXTURE_PIDS+=("$PID_ALIVE" "$PID_WSGONE" "$PID_NOCWD" "$PID_NOJOBS" "$PID_NOSTATE" "$HOLDER_PID")

  write_state ALIVE   "$WS_ALIVE"   "$PID_ALIVE"   1
  write_state WSGONE  "$WS_GONE"    "$PID_WSGONE"  1
  write_state NOCWD   "$WS_NOCWD"   "$PID_NOCWD"   1
  write_state NOJOBS  "$WS_NOJOBS"  "$PID_NOJOBS"  0
  # NOSTATE deliberately gets no state dir at all.
  rmdir "$WS_GONE"   # signal C: the workspace is deleted after the job recorded it

  # Wait until the holder has actually chdir'd, or the cwd scan would race.
  ready=false
  for _ in $(seq 1 50); do
    if lsof -a -d cwd -p "$HOLDER_PID" -Fn -w 2>/dev/null | grep -qx "n$WS_ALIVE"; then ready=true; break; fi
    sleep 0.1
  done

  if [ "$ready" != true ]; then
    fail "#919: cwd holder never entered $WS_ALIVE (pid=$HOLDER_PID)"
  elif ! proc_alive "$PID_WSGONE" || ! proc_alive "$PID_NOCWD"; then
    fail "#919: fixture brokers did not come up"
  else
    RUN_ENV_OUT="$(TMPDIR="$TMPD" CLAUDE_CONFIG_DIR="$TMPD/config" bash "$REAPER_COPY" --reap --max-age 5 --dry-run 2>&1)"

    case "$RUN_ENV_OUT" in
      *"SKIP   pid=$PID_ALIVE (owner alive"*) pass "#919 dry-run: broker whose workspace has a live cwd is SKIP (owner alive)" ;;
      *) fail "#919 dry-run: expected owner-alive SKIP for pid=$PID_ALIVE, got: $RUN_ENV_OUT" ;;
    esac
    case "$RUN_ENV_OUT" in
      *"WOULD REAP pid=$PID_WSGONE"*) pass "#919 dry-run: deleted-workspace broker is WOULD REAP" ;;
      *) fail "#919 dry-run: expected WOULD REAP for pid=$PID_WSGONE, got: $RUN_ENV_OUT" ;;
    esac

    REAP_OUT="$(TMPDIR="$TMPD" CLAUDE_CONFIG_DIR="$TMPD/config" bash "$REAPER_COPY" --reap --max-age 5 2>&1)"

    # 1. Regression: idle + workspace alive + someone working in it → survive.
    if proc_alive "$PID_ALIVE"; then
      pass "#919 regression: idle broker with a live workspace cwd survived --reap"
    else
      fail "#919 regression: idle broker with a live workspace cwd was killed (output: $REAP_OUT)"
    fi
    if [ -d "$TMPD/cxc-ALIVE" ]; then
      pass "#919 regression: surviving broker's sessionDir kept"
    else
      fail "#919 regression: surviving broker's sessionDir was removed"
    fi
    case "$REAP_OUT" in
      *"SKIP   pid=$PID_ALIVE (owner alive: a live process is working in $WS_ALIVE)"*)
        pass "#919 regression: skip reason names the live workspace" ;;
      *) fail "#919 regression: expected owner-alive SKIP naming $WS_ALIVE, got: $REAP_OUT" ;;
    esac

    # 2. Signal C — workspaceRoot recorded but the directory is gone → reap.
    if proc_gone "$PID_WSGONE"; then
      pass "#919 C: broker whose workspace was deleted is reaped"
    else
      fail "#919 C: deleted-workspace broker survived --reap (output: $REAP_OUT)"
    fi
    case "$REAP_OUT" in
      *"REAPED pid=$PID_WSGONE"*) pass "#919 C: reap reported for the deleted-workspace broker" ;;
      *) fail "#919 C: expected REAPED line for pid=$PID_WSGONE, got: $REAP_OUT" ;;
    esac
    if [ ! -d "$TMPD/cxc-WSGONE" ]; then
      pass "#919 C: reaped broker's sessionDir removed"
    else
      fail "#919 C: reaped broker's sessionDir still present"
    fi

    # 3. Signal D — workspace exists but no live process is cwd'd there → reap.
    if proc_gone "$PID_NOCWD"; then
      pass "#919 D: broker with no live process in its workspace is reaped"
    else
      fail "#919 D: unused-workspace broker survived --reap (output: $REAP_OUT)"
    fi
    case "$REAP_OUT" in
      *"REAPED pid=$PID_NOCWD"*) pass "#919 D: reap reported for the unused-workspace broker" ;;
      *) fail "#919 D: expected REAPED line for pid=$PID_NOCWD, got: $REAP_OUT" ;;
    esac

    # 4. Undeterminable owners must fall to KEEP.
    if proc_alive "$PID_NOJOBS"; then
      pass "#919 safe-default: broker with no jobs file survived --reap"
    else
      fail "#919 safe-default: broker with no jobs file was killed (output: $REAP_OUT)"
    fi
    case "$REAP_OUT" in
      *"SKIP   pid=$PID_NOJOBS (owner unknown: no workspaceRoot recorded"*)
        pass "#919 safe-default: missing jobs file reported as unknown owner" ;;
      *) fail "#919 safe-default: expected unknown-owner SKIP for pid=$PID_NOJOBS, got: $REAP_OUT" ;;
    esac
    if proc_alive "$PID_NOSTATE"; then
      pass "#919 safe-default: broker with no state dir survived --reap"
    else
      fail "#919 safe-default: broker with no state dir was killed (output: $REAP_OUT)"
    fi
    case "$REAP_OUT" in
      *"SKIP   pid=$PID_NOSTATE (owner unknown: no state dir claims this pid)"*)
        pass "#919 safe-default: missing state dir reported as unknown owner" ;;
      *) fail "#919 safe-default: expected unknown-owner SKIP for pid=$PID_NOSTATE, got: $REAP_OUT" ;;
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
