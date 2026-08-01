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
#      gone, so a kill needs positive orphan evidence: the broker's WORKSPACE
#      ROOT has been deleted. Anything else — including a workspace that still
#      exists — must keep the broker. (Children are NOT a liveness signal
#      either: the broker spawns `codex app-server` at startup and closes it
#      only on its own shutdown, so orphans keep a child too.)
#
# Run:  ./tests/test_codex_broker_reaper.sh
# Exit: 0 on success, 1 on first failure (after summary).
# Gates whose platform prerequisites are missing print a SKIPPED line and are
# listed in the summary; they never fail the run.

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
SKIPPED_NAMES=()

pass() { PASS=$((PASS + 1)); echo "  PASS  $1"; }
fail() { FAIL=$((FAIL + 1)); FAILED_NAMES+=("$1"); echo "  FAIL  $1" >&2; }
# A gate whose platform prerequisites are absent. Announced here and listed in
# the summary — never silently dropped (mirrors run-tests.sh's SKIPPED lines).
skip() { SKIPPED_NAMES+=("$1"); echo "SKIPPED: $1"; }

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

# --- Gate 3: #919 owner-death oracle (behavior) ------------------------------
# Real execution against synthetic processes. The shipped pgrep pattern
# (app-server-broker.mjs) would match a developer host's PRODUCTION brokers, so
# the SUT is copied with ONLY BROKER_PATTERN rewritten to this run's unique
# fixture path — the reap decision under test is the shipped code, and every pid
# the copy can see is one this test started. CLAUDE_CONFIG_DIR points the state
# root at a sandbox tree, so real state dirs are never read or written, and
# TMPDIR keeps the lock inside the sandbox too.
#
# Six brokers, all equally idle, differing only in owner evidence:
#   ALIVE   workspaceRoot recorded and still present        → survive
#   WSGONE  workspaceRoot recorded, directory deleted        → reap
#   NOJOBS  state dir without jobs/*.json (undeterminable)   → survive
#   NOSTATE no state dir claims the pid (undeterminable)     → survive
#   PIDDUP  a stale state dir claims the same pid; the sessionDir-matching one
#           (workspace alive) must win over it               → survive
#   AMBIG   two state dirs match both pid AND sessionDir     → survive
#
# The reaper's idle gate reads mtime through BSD `stat -f %m`, which is Darwin
# syntax, so this gate is skipped elsewhere (the CI runner is Ubuntu).
if [ "$(uname -s)" != "Darwin" ]; then
  skip "gate 3 owner-death behavior (needs Darwin: reaper's idle gate uses BSD 'stat -f %m')"
else

TMPROOT="${TMPDIR:-/tmp}"; TMPROOT="${TMPROOT%/}"
TMPD="$(mktemp -d "$TMPROOT/px919.XXXXXX")"
FIXTURE="$TMPD/broker-fixture.sh"
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

# Broker stand-in: parked in its workspace root with one child that inherits the
# cwd, exactly like `app-server-broker.mjs` -> `codex app-server`. Blocks on a
# fifo through the `read` builtin, so the process tree is exactly two entries.
cat > "$FIXTURE" <<'FIX'
#!/bin/bash
cd "$PX919_CWD" || exit 1
sleep 600 &
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

# The plugin keys its state dir on the workspace root: <slug>-<sha256[0:16]>.
ws_hash() { printf '%s' "$1" | shasum -a 256 | cut -c1-16; }

# A fixture broker running in workspace $2, with a backdated broker.log so the
# idle gate always passes.
start_broker() {
  local name="$1"
  local ws="$2"
  local sdir="$TMPD/cxc-$name"
  local fifo="$TMPD/$name.fifo"
  local pid
  mkdir -p "$sdir"
  mkfifo "$fifo"
  touch -t 202001010000 "$sdir/broker.log"
  PX919_FIFO="$fifo" PX919_CWD="$ws" \
    bash "$FIXTURE" serve --endpoint "unix:$sdir/broker.sock" >/dev/null 2>&1 &
  pid=$!
  printf '%s' "$pid"   # caller records it in FIXTURE_PIDS (this runs in a subshell)
}

# A state dir as the plugin writes it: broker.json carries pid + sessionDir,
# jobs/*.json the workspaceRoot. $5 names the sessionDir it claims, which is how
# a stale duplicate is told apart from the real one.
write_state() {
  local slug="$1" wroot="$2" pid="$3" with_jobs="$4" session="$5"
  local sd
  sd="$STATE_ROOT/$slug-$(ws_hash "$wroot")"
  mkdir -p "$sd"
  printf '{"sessionDir":"%s","pid":%s}\n' "$TMPD/cxc-$session" "$pid" > "$sd/broker.json"
  if [ "$with_jobs" = 1 ]; then
    mkdir -p "$sd/jobs"
    printf '{"id":"job-%s","workspaceRoot":"%s"}\n' "$slug" "$wroot" > "$sd/jobs/job.json"
  fi
}

if ! grep -q "^BROKER_PATTERN='$FIXTURE'$" "$REAPER_COPY"; then
  fail "#919: could not isolate BROKER_PATTERN in the reaper copy (refusing to run against real brokers)"
else
  for w in alive wsgone nojobs nostate piddup pidgone ambig ambgone; do
    mkdir -p "$TMPD/ws-$w"
  done
  WS_ALIVE="$TMPD/ws-alive"; WS_GONE="$TMPD/ws-wsgone"
  WS_NOJOBS="$TMPD/ws-nojobs"; WS_NOSTATE="$TMPD/ws-nostate"
  WS_PIDDUP="$TMPD/ws-piddup"; WS_PIDGONE="$TMPD/ws-pidgone"
  WS_AMBIG="$TMPD/ws-ambig"; WS_AMBGONE="$TMPD/ws-ambgone"

  PID_ALIVE="$(start_broker ALIVE "$WS_ALIVE")"
  PID_WSGONE="$(start_broker WSGONE "$WS_GONE")"
  PID_NOJOBS="$(start_broker NOJOBS "$WS_NOJOBS")"
  PID_NOSTATE="$(start_broker NOSTATE "$WS_NOSTATE")"
  PID_PIDDUP="$(start_broker PIDDUP "$WS_PIDDUP")"
  PID_AMBIG="$(start_broker AMBIG "$WS_AMBIG")"
  FIXTURE_PIDS+=("$PID_ALIVE" "$PID_WSGONE" "$PID_NOJOBS" "$PID_NOSTATE"
                 "$PID_PIDDUP" "$PID_AMBIG")

  write_state alive   "$WS_ALIVE"   "$PID_ALIVE"   1 ALIVE
  write_state wsgone  "$WS_GONE"    "$PID_WSGONE"  1 WSGONE
  write_state nojobs  "$WS_NOJOBS"  "$PID_NOJOBS"  0 NOJOBS
  # NOSTATE deliberately gets no state dir at all.

  # PIDDUP: the "aaa" slug sorts first, so a first-match lookup would take the
  # stale dir — whose sessionDir does not match and whose workspace is gone.
  write_state aaastale "$WS_PIDGONE" "$PID_PIDDUP" 1 OTHER
  write_state piddup   "$WS_PIDDUP"  "$PID_PIDDUP" 1 PIDDUP
  # AMBIG: both candidates match pid AND sessionDir → undecidable → keep.
  write_state aaaambig "$WS_AMBGONE" "$PID_AMBIG"  1 AMBIG
  write_state ambig    "$WS_AMBIG"   "$PID_AMBIG"  1 AMBIG

  rmdir "$WS_GONE" "$WS_PIDGONE" "$WS_AMBGONE"   # the deleted workspaces

  ready=true
  for p in "$PID_ALIVE" "$PID_WSGONE" "$PID_NOJOBS" "$PID_NOSTATE" "$PID_PIDDUP" "$PID_AMBIG"; do
    proc_alive "$p" || ready=false
  done

  if [ "$ready" != true ]; then
    fail "#919: fixture brokers did not come up"
  else
    DRY_OUT="$(TMPDIR="$TMPD" CLAUDE_CONFIG_DIR="$TMPD/config" bash "$REAPER_COPY" --reap --max-age 5 --dry-run 2>&1)"

    case "$DRY_OUT" in
      *"SKIP   pid=$PID_ALIVE (owner alive"*) pass "#919 dry-run: broker with a live workspace is SKIP (owner alive)" ;;
      *) fail "#919 dry-run: expected owner-alive SKIP for pid=$PID_ALIVE, got: $DRY_OUT" ;;
    esac
    case "$DRY_OUT" in
      *"WOULD REAP pid=$PID_WSGONE"*) pass "#919 dry-run: deleted-workspace broker is WOULD REAP" ;;
      *) fail "#919 dry-run: expected WOULD REAP for pid=$PID_WSGONE, got: $DRY_OUT" ;;
    esac

    REAP_OUT="$(TMPDIR="$TMPD" CLAUDE_CONFIG_DIR="$TMPD/config" bash "$REAPER_COPY" --reap --max-age 5 2>&1)"

    # 1. Regression: idle is not death — a live workspace keeps the broker.
    if proc_alive "$PID_ALIVE"; then
      pass "#919 regression: idle broker with a live workspace survived --reap"
    else
      fail "#919 regression: idle broker with a live workspace was killed (output: $REAP_OUT)"
    fi
    if [ -d "$TMPD/cxc-ALIVE" ]; then
      pass "#919 regression: surviving broker's sessionDir kept"
    else
      fail "#919 regression: surviving broker's sessionDir was removed"
    fi
    case "$REAP_OUT" in
      *"SKIP   pid=$PID_ALIVE (owner alive: workspace $WS_ALIVE still exists)"*)
        pass "#919 regression: skip reason names the live workspace" ;;
      *) fail "#919 regression: expected owner-alive SKIP naming $WS_ALIVE, got: $REAP_OUT" ;;
    esac

    # 2. The one orphan signal — workspaceRoot recorded, directory gone → reap.
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

    # 3. Undeterminable owners must fall to KEEP.
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
      *"SKIP   pid=$PID_NOSTATE (owner unknown: no single state dir matches this pid and sessionDir)"*)
        pass "#919 safe-default: missing state dir reported as unknown owner" ;;
      *) fail "#919 safe-default: expected unknown-owner SKIP for pid=$PID_NOSTATE, got: $REAP_OUT" ;;
    esac

    # 4. PID reuse — a stale broker.json claiming the same pid must not decide
    #    the verdict, or its deleted workspace kills a live broker.
    if proc_alive "$PID_PIDDUP"; then
      pass "#919 F3: stale duplicate state dir did not get the live broker killed"
    else
      fail "#919 F3: broker killed via a stale state dir claiming its pid (output: $REAP_OUT)"
    fi
    case "$REAP_OUT" in
      *"SKIP   pid=$PID_PIDDUP (owner alive: workspace $WS_PIDDUP still exists)"*)
        pass "#919 F3: the sessionDir-matching state dir is the one adopted" ;;
      *) fail "#919 F3: expected the sessionDir-matching state dir to decide pid=$PID_PIDDUP, got: $REAP_OUT" ;;
    esac
    if proc_alive "$PID_AMBIG"; then
      pass "#919 F3: two equally-matching state dirs fall back to KEEP"
    else
      fail "#919 F3: ambiguous state dirs got the broker killed (output: $REAP_OUT)"
    fi
    case "$REAP_OUT" in
      *"SKIP   pid=$PID_AMBIG (owner unknown: no single state dir matches this pid and sessionDir)"*)
        pass "#919 F3: ambiguity reported as unknown owner" ;;
      *) fail "#919 F3: expected unknown-owner SKIP for pid=$PID_AMBIG, got: $REAP_OUT" ;;
    esac
  fi
fi

fi   # Darwin gate
echo ""
echo "=== summary ==="
echo "PASS: $PASS"
echo "FAIL: $FAIL"
echo "SKIP: ${#SKIPPED_NAMES[@]}"

if [ "${#SKIPPED_NAMES[@]}" -gt 0 ]; then
  echo ""
  echo "Skipped gates:"
  for n in "${SKIPPED_NAMES[@]}"; do
    echo "  - $n"
  done
fi

if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "Failed cases:"
  for n in "${FAILED_NAMES[@]}"; do
    echo "  - $n"
  done
  exit 1
fi

exit 0
