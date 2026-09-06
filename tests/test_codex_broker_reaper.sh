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
#   3. (#919, #926) The reap decision itself. Idleness does not imply the owner
#      is gone, so a kill needs positive orphan evidence: either the broker's
#      WORKSPACE ROOT has been deleted (signal C), or it still exists but no
#      live process outside the broker's own tree works in it (signal D).
#      Anything undeterminable must keep the broker. (Children are NOT a
#      liveness signal: the broker spawns `codex app-server` at startup and
#      closes it only on its own shutdown, so orphans keep a child too — which
#      is also why signal D excludes the broker's own tree.)
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

# --- Gate 2b: #926 workspace_has_live_owner fails CLOSED --------------------
# The guard is unreachable through the shipped call path (the reap pass builds
# the snapshot first), so nothing in Gate 3 exercises it. Lift the function out
# and drive it directly, the same way Gate 2 lifts is_safe_session_dir: without
# the guard the `done < "$CWD_SNAP"` redirect fails on a missing file, the
# function returns non-zero, and owner_status turns that into `dead` — a kill
# licensed by an absent file. No platform prerequisites, so this runs
# everywhere.
OWNER_FN="$(sed -n '/^collect_tree()/,/^}/p;/^workspace_has_live_owner()/,/^}/p' "$REAPER")"
if [ -z "$OWNER_FN" ]; then
  fail "#926: could not extract workspace_has_live_owner() from reaper"
else
  # $1 is the CWD_SNAP value to test with; echo the function's exit status.
  owner_rc() {
    bash -c "CWD_SNAP=\"\$1\"
$OWNER_FN
workspace_has_live_owner \$\$ /tmp >/dev/null 2>&1; echo \$?" _ "$1"
  }
  if [ "$(owner_rc "/nonexistent-snapshot-$$")" = 0 ]; then
    pass "#926 fail-closed: a missing cwd snapshot reads as OWNED, not as no-owner"
  else
    fail "#926 fail-closed: a missing cwd snapshot licensed a reap"
  fi
  if [ "$(owner_rc "")" = 0 ]; then
    pass "#926 fail-closed: an unset cwd snapshot reads as OWNED"
  else
    fail "#926 fail-closed: an unset cwd snapshot licensed a reap"
  fi
  EMPTY_SNAP="$(mktemp)"
  : > "$EMPTY_SNAP"
  if [ "$(owner_rc "$EMPTY_SNAP")" = 0 ]; then
    pass "#926 fail-closed: an empty cwd snapshot reads as OWNED"
  else
    fail "#926 fail-closed: an empty cwd snapshot licensed a reap"
  fi
  rm -f "$EMPTY_SNAP"
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
# Nine brokers, all equally idle, differing only in owner evidence:
#   ALIVE   workspace present AND a live process works in it → survive
#   SUBDIR  workspace present, the owner's cwd is a nested
#           subdirectory of it (#926 constraint 1)           → survive
#   NOOWNER workspace present, only the broker's own tree
#           has its cwd there (#926 signal D)                → reap
#   SIB     workspace present, the only owner sits in a sibling directory
#           whose name this workspace prefixes (#926 constraint 2) → reap
#   WSGONE  workspaceRoot recorded, directory deleted        → reap
#   NOJOBS  state dir without jobs/*.json (undeterminable)   → survive
#   NOSTATE no state dir claims the pid (undeterminable)     → survive
#   PIDDUP  a stale state dir claims the same pid; the sessionDir-matching one
#           (owned workspace) must win over it               → survive
#   AMBIG   two state dirs match both pid AND sessionDir     → survive
#
# ALIVE and PIDDUP carry an explicit owner process because "the directory
# exists" stopped being sufficient evidence of an owner when #926 landed. That
# owner runs from its own fixture script, NOT the broker one: the reaper copy
# matches brokers by the broker fixture's path, so an owner sharing that path
# would be scanned as a broker itself.
#
# The reaper's idle gate reads mtime through a portable mtime() — BSD `stat -f`,
# then GNU `stat -c`, then python3 (issue #1302) — so this gate runs on Linux
# too, CI included. Only the "#926 constraint 3" sub-case below stays
# platform-gated, for a reason of its own (see its comment).

TMPROOT="${TMPDIR:-/tmp}"; TMPROOT="${TMPROOT%/}"
TMPD="$(mktemp -d "$TMPROOT/px919.XXXXXX")" || { echo "FATAL: mktemp -d failed" >&2; exit 1; }

# The idle gate reads mtime through the reaper's portable mtime() — BSD
# `stat -f`, then GNU `stat -c`, then python3 (issue #1302). Probe the same
# chain once here: when none of them yields a value, the reaper substitutes
# `now`, the stale-idle path never fires, and gates 3 and 5b would fail for a
# reason that belongs to the host, not the reaper. They skip with that reason
# instead, so a failure in those gates always means reap behavior regressed.
mtime_probe() {
  local f="$TMPD/.mtime-probe" m
  : > "$f" || return 1
  m="$(stat -f %m "$f" 2>/dev/null)" || m=""
  case "$m" in ''|*[!0-9]*) m="$(stat -c %Y "$f" 2>/dev/null)" || m="" ;; esac
  case "$m" in ''|*[!0-9]*)
    m="$(python3 -c 'import os, sys; print(int(os.stat(sys.argv[1]).st_mtime))' "$f" 2>/dev/null)" || m="" ;;
  esac
  rm -f "$f"
  case "$m" in ''|*[!0-9]*) return 1 ;; esac
  return 0
}
MTIME_OK=false
mtime_probe && MTIME_OK=true
NO_MTIME_REASON="no mtime provider on this host: BSD stat -f, GNU stat -c and python3 all failed, so the reaper's idle gate would read every broker log as fresh"
FIXTURE="$TMPD/broker-fixture.sh"
OWNER_FIXTURE="$TMPD/owner-fixture.sh"
REAPER_COPY="$TMPD/reaper-copy.sh"
# The space is deliberate: CLAUDE_CONFIG_DIR is relocatable (CONTRIBUTING.md),
# and a bare `for f in $(grep -l ...)` over the state dir splits such a path at
# the space — every lookup misses, every broker reads as "unknown", and the reap
# pass silently dies. Keeping the fixture path spaced makes that a standing
# regression check for the whole gate rather than one bolted-on case.
CONFIG_FIXTURE="$TMPD/config dir"
STATE_ROOT="$CONFIG_FIXTURE/plugins/data/codex-openai-codex/state"
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

# Owner stand-in: a live session working inside a workspace. Deliberately a
# DIFFERENT script path from the broker fixture — the reaper copy matches
# brokers by that path, so reusing it would enrol every owner as a broker.
cat > "$OWNER_FIXTURE" <<'OWN'
#!/bin/bash
cd "$PX926_CWD" || exit 1
exec 3<> "$PX926_FIFO"
read -r -u 3
OWN

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

# A process whose cwd is $2 and which is NOT descended from any broker — the
# thing signal D looks for.
start_owner() {
  local name="$1" cwd="$2"
  local fifo="$TMPD/owner-$name.fifo" pid
  mkfifo "$fifo"
  PX926_FIFO="$fifo" PX926_CWD="$cwd" bash "$OWNER_FIXTURE" >/dev/null 2>&1 &
  pid=$!
  printf '%s' "$pid"
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
elif [ "$MTIME_OK" != true ]; then
  skip "gate 3 owner-death behavior ($NO_MTIME_REASON)"
else
  for w in alive subdir noowner wsgone nojobs nostate piddup pidgone ambig ambgone sib; do
    mkdir -p "$TMPD/ws-$w"
  done
  # Sibling worktrees routinely share a name prefix, and "ws-sib" is a bare
  # string prefix of "ws-sib-extra". Measured on the author's host: for
  # workspace `laplace-dev-hub`, a bare-prefix test claimed 8 processes living
  # in `laplace-dev-hub-hub-4682` / `-4687` as its owners. The owner below sits
  # ONLY in the sibling, so the ws-sib broker must still be reaped.
  mkdir -p "$TMPD/ws-sib-extra"
  WS_ALIVE="$TMPD/ws-alive"; WS_GONE="$TMPD/ws-wsgone"
  WS_SUBDIR="$TMPD/ws-subdir"; WS_NOOWNER="$TMPD/ws-noowner"
  WS_NOJOBS="$TMPD/ws-nojobs"; WS_NOSTATE="$TMPD/ws-nostate"
  WS_PIDDUP="$TMPD/ws-piddup"; WS_PIDGONE="$TMPD/ws-pidgone"
  WS_AMBIG="$TMPD/ws-ambig"; WS_AMBGONE="$TMPD/ws-ambgone"
  # The plugin records the GIT ROOT as workspaceRoot while a session's cwd may
  # be any subdirectory of it (lib/workspace.mjs resolveWorkspaceRoot ->
  # ensureGitRepository), so containment must hold several levels down.
  mkdir -p "$WS_SUBDIR/nested/deep"

  PID_ALIVE="$(start_broker ALIVE "$WS_ALIVE")"
  PID_SUBDIR="$(start_broker SUBDIR "$WS_SUBDIR")"
  PID_NOOWNER="$(start_broker NOOWNER "$WS_NOOWNER")"
  WS_SIB="$TMPD/ws-sib"
  PID_SIB="$(start_broker SIB "$WS_SIB")"
  OWNER_SIB="$(start_owner sib "$TMPD/ws-sib-extra")"
  PID_WSGONE="$(start_broker WSGONE "$WS_GONE")"
  PID_NOJOBS="$(start_broker NOJOBS "$WS_NOJOBS")"
  PID_NOSTATE="$(start_broker NOSTATE "$WS_NOSTATE")"
  PID_PIDDUP="$(start_broker PIDDUP "$WS_PIDDUP")"
  PID_AMBIG="$(start_broker AMBIG "$WS_AMBIG")"
  OWNER_ALIVE="$(start_owner alive "$WS_ALIVE")"
  OWNER_SUBDIR="$(start_owner subdir "$WS_SUBDIR/nested/deep")"
  OWNER_PIDDUP="$(start_owner piddup "$WS_PIDDUP")"
  FIXTURE_PIDS+=("$PID_ALIVE" "$PID_SUBDIR" "$PID_NOOWNER" "$PID_SIB" "$PID_WSGONE"
                 "$PID_NOJOBS" "$PID_NOSTATE" "$PID_PIDDUP" "$PID_AMBIG"
                 "$OWNER_ALIVE" "$OWNER_SUBDIR" "$OWNER_SIB" "$OWNER_PIDDUP")

  write_state alive   "$WS_ALIVE"   "$PID_ALIVE"   1 ALIVE
  write_state subdir  "$WS_SUBDIR"  "$PID_SUBDIR"  1 SUBDIR
  write_state noowner "$WS_NOOWNER" "$PID_NOOWNER" 1 NOOWNER
  write_state sib     "$WS_SIB"     "$PID_SIB"     1 SIB
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
  for p in "$PID_ALIVE" "$PID_SUBDIR" "$PID_NOOWNER" "$PID_SIB" "$PID_WSGONE" \
           "$PID_NOJOBS" "$PID_NOSTATE" "$PID_PIDDUP" "$PID_AMBIG" \
           "$OWNER_ALIVE" "$OWNER_SUBDIR" "$OWNER_SIB" "$OWNER_PIDDUP"; do
    proc_alive "$p" || ready=false
  done

  if [ "$ready" != true ]; then
    fail "#919: fixture brokers did not come up"
  else
    DRY_OUT="$(TMPDIR="$TMPD" CLAUDE_CONFIG_DIR="$CONFIG_FIXTURE" bash "$REAPER_COPY" --reap --max-age 5 --dry-run 2>&1)"

    case "$DRY_OUT" in
      *"SKIP   pid=$PID_ALIVE (owner alive"*) pass "#919 dry-run: broker with a live workspace is SKIP (owner alive)" ;;
      *) fail "#919 dry-run: expected owner-alive SKIP for pid=$PID_ALIVE, got: $DRY_OUT" ;;
    esac
    case "$DRY_OUT" in
      *"WOULD REAP pid=$PID_WSGONE"*) pass "#919 dry-run: deleted-workspace broker is WOULD REAP" ;;
      *) fail "#919 dry-run: expected WOULD REAP for pid=$PID_WSGONE, got: $DRY_OUT" ;;
    esac

    # #926 constraint 3 — no cwd source means the owner is UNDETERMINED, which
    # must fall to KEEP. Darwin has no /proc, so dropping lsof's directory from
    # PATH removes every source. The broker under test is the one signal D
    # would otherwise reap: if a missing source silently read as "no owner",
    # this is exactly where a live session would be killed on a host without
    # lsof. Only lsof lives in that directory among the binaries the reaper
    # needs (ps, pgrep, jq, stat, date, mktemp, shasum, sed, awk), so removing
    # it does not disable the rest of the run.
    #
    # Darwin-only, and NOT because of stat (that split is gone, #1302): on
    # Linux cwd_pairs() falls back to /proc/<pid>/cwd once lsof is gone, so
    # the snapshot is still populated and the NOOWNER broker is correctly read
    # as unowned — the "no cwd source" premise cannot be staged here without
    # hiding /proc, which the reaper does not let a caller do. The assertion
    # is right; only its precondition is unreachable on this platform.
    LSOF_BIN="$(command -v lsof || true)"
    if [ -z "$LSOF_BIN" ]; then
      skip "#926 constraint 3 no-cwd-source KEEP (lsof not installed — nothing to remove)"
    elif [ "$(uname -s)" = "Linux" ] && [ -r /proc/self/cwd ]; then
      skip "#926 constraint 3 no-cwd-source KEEP (Linux: the reaper falls back to /proc/<pid>/cwd, so removing lsof leaves a cwd source)"
    else
      LSOF_DIR="$(dirname "$LSOF_BIN")"
      NOLSOF_PATH="$(printf '%s' "$PATH" | tr ':' '\n' | grep -vxF "$LSOF_DIR" | paste -sd: -)"
      NOLSOF_OUT="$(PATH="$NOLSOF_PATH" TMPDIR="$TMPD" CLAUDE_CONFIG_DIR="$CONFIG_FIXTURE" \
        bash "$REAPER_COPY" --reap --max-age 5 --dry-run 2>&1)"
      case "$NOLSOF_OUT" in
        *"SKIP   pid=$PID_NOOWNER (owner alive: workspace $WS_NOOWNER exists (no cwd source"*)
          pass "#926 constraint 3: no cwd source falls to KEEP, not to reap" ;;
        *) fail "#926 constraint 3: expected undetermined-owner SKIP for pid=$PID_NOOWNER without lsof, got: $NOLSOF_OUT" ;;
      esac
      # Signal C is independent of the cwd source and must still fire.
      case "$NOLSOF_OUT" in
        *"WOULD REAP pid=$PID_WSGONE"*)
          pass "#926 constraint 3: signal C still reaps without a cwd source" ;;
        *) fail "#926 constraint 3: expected signal C to survive the missing cwd source, got: $NOLSOF_OUT" ;;
      esac
    fi

    # #926 constraint 4 — ONE cwd snapshot per pass, reused across candidates.
    # Asserted by counting invocations rather than by wall-clock: a timing
    # threshold on a shared dev host is flaky, and the property that actually
    # matters is "not once per broker". With 9 brokers scanned, a per-broker
    # rebuild would show 9+.
    #
    # Scoped to --dry-run deliberately, and the exact count is the right
    # oracle there: a real reap pass rebuilds once more per pre-kill re-check
    # (by design — that re-check exists to see state the pass-entry snapshot
    # predates), so only the dry-run path has a fixed expected count. A looser
    # upper bound would still pass the regression this caught (5 enumerations)
    # while going blind to a smaller one.
    if [ -z "${LSOF_BIN:-}" ]; then
      skip "#926 constraint 4 single-snapshot-per-pass (needs lsof to intercept)"
    else
      COUNT_BIN="$TMPD/countbin"
      mkdir -p "$COUNT_BIN"
      COUNTER="$TMPD/lsof.count"
      : > "$COUNTER"
      cat > "$COUNT_BIN/lsof" <<COUNTLSOF
#!/bin/bash
echo x >> "$COUNTER"
exec "$LSOF_BIN" "\$@"
COUNTLSOF
      chmod +x "$COUNT_BIN/lsof"
      PATH="$COUNT_BIN:$PATH" TMPDIR="$TMPD" CLAUDE_CONFIG_DIR="$CONFIG_FIXTURE" \
        bash "$REAPER_COPY" --reap --max-age 5 --dry-run >/dev/null 2>&1 || true
      N_LSOF="$(wc -l < "$COUNTER" | tr -d ' ')"
      if [ "$N_LSOF" = 1 ]; then
        pass "#926 constraint 4: one cwd enumeration for a whole dry-run pass (9 brokers)"
      else
        fail "#926 constraint 4: expected 1 cwd enumeration per dry-run pass, got $N_LSOF"
      fi
    fi

    REAP_OUT="$(TMPDIR="$TMPD" CLAUDE_CONFIG_DIR="$CONFIG_FIXTURE" bash "$REAPER_COPY" --reap --max-age 5 2>&1)"

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
      *"SKIP   pid=$PID_ALIVE (owner alive: workspace $WS_ALIVE has a live process working in it)"*)
        pass "#919 regression: skip reason names the live workspace" ;;
      *) fail "#919 regression: expected owner-alive SKIP naming $WS_ALIVE, got: $REAP_OUT" ;;
    esac

    # 1b. #926 constraint 1 — the owner's cwd is a nested subdirectory of the
    #     recorded workspaceRoot, not the root itself.
    if proc_alive "$PID_SUBDIR"; then
      pass "#926 subdir: owner working in a subdirectory keeps the broker"
    else
      fail "#926 subdir: broker killed despite an owner inside its workspace (output: $REAP_OUT)"
    fi
    case "$REAP_OUT" in
      *"SKIP   pid=$PID_SUBDIR (owner alive: workspace $WS_SUBDIR has a live process working in it)"*)
        pass "#926 subdir: containment is decided below the workspace root" ;;
      *) fail "#926 subdir: expected owner-alive SKIP for pid=$PID_SUBDIR, got: $REAP_OUT" ;;
    esac

    # 1c. #926 signal D — workspace intact, but only the broker's own tree has
    #     its cwd there. This is the recovery the signal exists for; before it,
    #     this broker was KEPT forever.
    if proc_gone "$PID_NOOWNER"; then
      pass "#926 D: broker whose workspace nobody works in is reaped"
    else
      fail "#926 D: unowned-workspace broker survived --reap (output: $REAP_OUT)"
    fi
    case "$REAP_OUT" in
      *"REAPED pid=$PID_NOOWNER"*) pass "#926 D: reap reported for the unowned-workspace broker" ;;
      *) fail "#926 D: expected REAPED line for pid=$PID_NOOWNER, got: $REAP_OUT" ;;
    esac

    # 1d. #926 constraint 2 — containment on component boundaries. The only
    #     owner lives in the SIBLING directory whose name this workspace is a
    #     bare string prefix of; a prefix test would read it as owned and keep
    #     the broker forever.
    if proc_gone "$PID_SIB"; then
      pass "#926 boundary: an owner in a name-prefix sibling does not keep the broker"
    else
      fail "#926 boundary: sibling-directory owner wrongly kept pid=$PID_SIB (output: $REAP_OUT)"
    fi

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
      *"SKIP   pid=$PID_PIDDUP (owner alive: workspace $WS_PIDDUP has a live process working in it)"*)
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

# --- Gate 4: #921 GC sessionDir ownership (behavior) -------------------------
# The GC pass deletes a sessionDir on one signal: the broker.json naming it
# records a dead pid. That is not evidence the directory is unowned — a crashed
# broker leaves its json behind and pids get reused, so a stale record can name
# a directory a LIVE broker is still writing to. #923 gave the reap pass a
# pid+sessionDir disambiguation; the GC pass had none, and `--gc` is the DEFAULT
# mode whose --help calls it "zero risk".
#
# Unlike gate 3 this needs no BSD `stat`: the GC pass has no idle gate, so it
# runs everywhere. Three state dirs:
#   LIVE    pid alive, sessionDir S            → S must survive
#   STALE   pid dead,  sessionDir S (the same) → must not decide S's fate
#   ORPHAN  pid dead,  sessionDir O (its own)  → O must still be GC'd
if ! command -v jq >/dev/null 2>&1; then
  skip "gate 4 GC sessionDir ownership (needs jq: the reaper reads broker.json with it)"
else

TMPROOT4="${TMPDIR:-/tmp}"; TMPROOT4="${TMPROOT4%/}"
TMPD4="$(mktemp -d "$TMPROOT4/px921.XXXXXX")" || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
# Spaced on purpose, same standing regression as gate 3's fixture.
CONFIG4="$TMPD4/config dir"
STATE4="$CONFIG4/plugins/data/codex-openai-codex/state"
LIVE_PID=""

# Chains gate 3's handler: a bare `trap cleanup_gate4 EXIT` would replace it and
# strand that gate's fixture tree and broker processes.
cleanup_gate4() {
  [ -n "$LIVE_PID" ] && kill -KILL "$LIVE_PID" 2>/dev/null
  rm -rf "$TMPD4"
  declare -F cleanup_fixtures >/dev/null && cleanup_fixtures
  return 0
}
trap cleanup_gate4 EXIT INT TERM

# A state dir as the plugin writes it. The GC pass reads only pid + sessionDir,
# so the slug is free — "aaa" sorts first, putting the stale record ahead of the
# live one in the glob so a first-match implementation would take it.
write_gc_state() {
  local slug="$1" pid="$2" session="$3" sd
  sd="$STATE4/$slug"
  mkdir -p "$sd"
  printf '{"sessionDir":"%s","pid":%s}\n' "$session" "$pid" > "$sd/broker.json"
}

SHARED_DIR="$TMPD4/cxc-SHARED"
ORPHAN_DIR="$TMPD4/cxc-ORPHAN"
mkdir -p "$SHARED_DIR" "$ORPHAN_DIR"

sleep 600 &
LIVE_PID=$!

# A pid that is definitively gone before the reaper looks at it.
DEAD_PID="$(bash -c 'echo $$')"
dead_ready=false
for _ in $(seq 1 30); do
  if ! kill -0 "$DEAD_PID" 2>/dev/null; then dead_ready=true; break; fi
  sleep 0.1
done

write_gc_state aaastale "$DEAD_PID" "$SHARED_DIR"
write_gc_state live     "$LIVE_PID" "$SHARED_DIR"
write_gc_state orphan   "$DEAD_PID" "$ORPHAN_DIR"

if [ "$dead_ready" != true ] || ! kill -0 "$LIVE_PID" 2>/dev/null; then
  fail "#921: gate 4 fixture pids not in the expected state (live=$LIVE_PID dead=$DEAD_PID)"
else
  GC_OUT="$(TMPDIR="$TMPD4" CLAUDE_CONFIG_DIR="$CONFIG4" bash "$REAPER" --gc 2>&1)"

  if [ -d "$SHARED_DIR" ]; then
    pass "#921: sessionDir claimed by a live broker survives --gc despite a stale dead-pid record"
  else
    fail "#921: --gc deleted a live broker's sessionDir on a stale record (output: $GC_OUT)"
  fi
  case "$GC_OUT" in
    *"SKIP GC dir=$SHARED_DIR (a live broker still claims this sessionDir)"*)
      pass "#921: the skip names the live claimant as the reason" ;;
    *) fail "#921: expected a live-claimant SKIP GC line for $SHARED_DIR, got: $GC_OUT" ;;
  esac
  # The guard must not turn GC off: a dir no live broker claims is still swept.
  if [ ! -d "$ORPHAN_DIR" ]; then
    pass "#921: a sessionDir with only dead claimants is still GC'd"
  else
    fail "#921: the ownership guard suppressed a legitimate GC (output: $GC_OUT)"
  fi
fi

fi   # jq gate

# --- Gate 5: #1056 sibling CLAUDE_CONFIG_DIR state ---------------------------
# A host may run more than one Claude config dir (~/.claude, ~/.claude-2). The
# broker records its broker.json under the config dir of the session that
# started it, so a reaper reading only its OWN config dir cannot resolve those
# brokers: pid+sessionDir matches nothing, owner_status returns `unknown`, and
# the under-reap bias keeps them forever. Measured on the author's host: 2161 of
# 2687 skips over 544 launchd runs were `owner unknown`, and pointing
# CLAUDE_CONFIG_DIR at the sibling flipped 17 of 19 to reapable.
#
# The fix reads every config dir that is a SIBLING of CONFIG_DIR. Both halves of
# the sweep must widen together: extending the GC loop across config dirs while
# leaving the live-claimant check on one dir would let --gc delete a sessionDir
# a live broker in a sibling config still claims — #921, reintroduced on a new
# axis. 5a covers that; 5b covers the reap-side resolution.
if ! command -v jq >/dev/null 2>&1; then
  skip "gate 5 sibling config dir (needs jq: the reaper reads broker.json with it)"
else

TMPROOT5="${TMPDIR:-/tmp}"; TMPROOT5="${TMPROOT5%/}"
TMPD5="$(mktemp -d "$TMPROOT5/px1056.XXXXXX")" || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
# Both spaced on purpose, same standing regression as gates 3 and 4.
CONFIG5A="$TMPD5/config dir"          # the one CLAUDE_CONFIG_DIR names
# HIDDEN on purpose. The real siblings are dotfiles (~/.claude-2), so candidate
# discovery only reaches them with dotglob set -- and a visible fixture name here
# leaves that load-bearing detail unpinned: dropping dotglob from the fix keeps
# the whole suite green (measured: 55 PASS / 0 FAIL without dotglob).
CONFIG5B="$TMPD5/.config dir 2"       # the sibling holding the real records
STATE5A="$CONFIG5A/plugins/data/codex-openai-codex/state"
STATE5B="$CONFIG5B/plugins/data/codex-openai-codex/state"
LIVE5_PID=""
BROKER5_PID=""

cleanup_gate5() {
  local kid
  if [ -n "$BROKER5_PID" ]; then
    for kid in $(pgrep -P "$BROKER5_PID" 2>/dev/null); do kill -KILL "$kid" 2>/dev/null; done
    kill -KILL "$BROKER5_PID" 2>/dev/null
  fi
  [ -n "$LIVE5_PID" ] && kill -KILL "$LIVE5_PID" 2>/dev/null
  rm -rf "$TMPD5"
  declare -F cleanup_gate4 >/dev/null && cleanup_gate4
  return 0
}
trap cleanup_gate5 EXIT INT TERM

# $1 state root, $2 slug, $3 pid, $4 sessionDir
write_state5() {
  local root="$1" slug="$2" pid="$3" session="$4" sd
  sd="$root/$slug"
  mkdir -p "$sd"
  printf '{"sessionDir":"%s","pid":%s}\n' "$session" "$pid" > "$sd/broker.json"
}

# --- 5a: GC reaches sibling records, and respects sibling live claimants -----
SD5_ORPHAN="$TMPD5/cxc-ORPHAN5"
SD5_SHARED="$TMPD5/cxc-SHARED5"
mkdir -p "$SD5_ORPHAN" "$SD5_SHARED"

sleep 600 &
LIVE5_PID=$!

# A high, currently-unused pid rather than a just-exited one: the kernel can
# hand a recently freed pid to a new process before the reaper runs, and the
# record would then read as live, failing this gate for an unrelated reason.
DEAD5_PID=99999
while kill -0 "$DEAD5_PID" 2>/dev/null; do DEAD5_PID=$((DEAD5_PID - 1)); done
dead5_ready=true

# Only the SIBLING knows about the orphan — the primary state dir stays empty of
# it, so a single-dir reaper never sees this record at all.
write_state5 "$STATE5B" orphan "$DEAD5_PID" "$SD5_ORPHAN"
# The live claimant lives in the SIBLING; the stale dead record naming the same
# sessionDir lives in the PRIMARY. A GC that widened its record sweep but not
# its claimant check reads only the stale one and deletes a live dir.
write_state5 "$STATE5B" live      "$LIVE5_PID" "$SD5_SHARED"
write_state5 "$STATE5A" aaastale  "$DEAD5_PID" "$SD5_SHARED"

if [ "$dead5_ready" != true ] || ! kill -0 "$LIVE5_PID" 2>/dev/null; then
  fail "#1056: gate 5a fixture pids not in the expected state (live=$LIVE5_PID dead=$DEAD5_PID)"
else
  GC5_OUT="$(TMPDIR="$TMPD5" CLAUDE_CONFIG_DIR="$CONFIG5A" bash "$REAPER" --gc 2>&1)"

  if [ ! -d "$SD5_ORPHAN" ]; then
    pass "#1056: --gc sweeps a stale sessionDir recorded only in a sibling config dir"
  else
    fail "#1056: --gc missed a sibling config dir's stale record (output: $GC5_OUT)"
  fi

  if [ -d "$SD5_SHARED" ]; then
    pass "#1056: --gc keeps a sessionDir a sibling config dir's live broker claims"
  else
    fail "#1056: --gc deleted a dir claimed by a live broker in a sibling config dir (output: $GC5_OUT)"
  fi
fi

# --- 5b: the reap pass resolves an owner recorded in a sibling config dir ----
# Signal C (workspaceRoot deleted) is used deliberately: it needs no cwd
# snapshot, so this case does not depend on lsof. The idle gate's mtime() is
# portable (#1302), so this runs everywhere gate 5 does.

FIXTURE5="$TMPD5/broker-fixture.sh"
REAPER5="$TMPD5/reaper-copy.sh"
SD5_REAP="$TMPD5/cxc-REAP5"
WS5_GONE="$TMPD5/workspace-deleted"

cat > "$FIXTURE5" <<'FIX5'
#!/bin/bash
exec 3<> "$PX1056_FIFO"
read -r -u 3
FIX5

sed "s|^BROKER_PATTERN=.*|BROKER_PATTERN='$FIXTURE5'|" "$REAPER" > "$REAPER5"

mkdir -p "$SD5_REAP"
FIFO5="$TMPD5/broker5.fifo"
mkfifo "$FIFO5"
# Backdated so the idle gate always passes.
touch -t 202001010000 "$SD5_REAP/broker.log"
PX1056_FIFO="$FIFO5" bash "$FIXTURE5" serve --endpoint "unix:$SD5_REAP/broker.sock" >/dev/null 2>&1 &
BROKER5_PID=$!

# The record lives ONLY in the sibling config dir, and names a workspace root
# that does not exist — signal C, an unambiguous orphan.
SD5_STATE="$STATE5B/reapme-$(printf '%s' "$WS5_GONE" | shasum -a 256 | cut -c1-16)"
mkdir -p "$SD5_STATE/jobs"
printf '{"sessionDir":"%s","pid":%s}\n' "$SD5_REAP" "$BROKER5_PID" > "$SD5_STATE/broker.json"
printf '{"workspaceRoot":"%s"}\n' "$WS5_GONE" > "$SD5_STATE/jobs/job.json"

broker5_ready=false
for _ in $(seq 1 30); do
  if kill -0 "$BROKER5_PID" 2>/dev/null; then broker5_ready=true; break; fi
  sleep 0.1
done

if [ "$broker5_ready" != true ]; then
  fail "#1056: gate 5b fixture broker did not come up"
elif [ "$MTIME_OK" != true ]; then
  skip "gate 5b sibling-config reap decision ($NO_MTIME_REASON)"
else
  DRY5_OUT="$(TMPDIR="$TMPD5" CLAUDE_CONFIG_DIR="$CONFIG5A" bash "$REAPER5" --reap --max-age 5 --dry-run 2>&1)"
  case "$DRY5_OUT" in
    *"WOULD REAP pid=$BROKER5_PID"*)
      pass "#1056: reap resolves a broker whose state lives in a sibling config dir" ;;
    *"owner unknown"*)
      fail "#1056: sibling-config broker still reads as 'owner unknown' (output: $DRY5_OUT)" ;;
    *)
      fail "#1056: expected WOULD REAP for pid=$BROKER5_PID, got: $DRY5_OUT" ;;
  esac
fi

fi   # jq gate (gate 5)

echo ""

# --- Gate 6: an unusable broker index must not read as an empty sweep --------
# The GC pass iterates the per-pass index rather than re-parsing every
# broker.json (two jq spawns per record was the script's whole runtime). That
# makes a failed index build silently equivalent to "no records exist": the
# loop reads zero rows and the summary still prints gc_dirs=0, which is exactly
# what a clean sweep prints. This gate pins the refusal instead.
# Strip the trailing slash TMPDIR carries on macOS before building a path under
# it: is_safe_session_dir rejects any path containing "//" outright (its
# traversal screen), so the doubled separator makes a legitimate sessionDir
# unsafe and nothing is ever collected. The refusal assertions below would then
# pass for the wrong reason — which is what the positive control exists to catch.
TMPROOT6="${TMPDIR:-/tmp}"; TMPROOT6="${TMPROOT6%/}"
TMPD6="$(mktemp -d "$TMPROOT6/px1056g6.XXXXXX")" || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
# Chain gate 5's handler rather than replacing it: an early exit anywhere below
# would otherwise leave this fixture behind, since the active trap only knows
# about $TMPD5.
cleanup_gate6() {
  rm -rf "$TMPD6"
  declare -F cleanup_gate5 >/dev/null && cleanup_gate5
  return 0
}
trap cleanup_gate6 EXIT INT TERM
CONFIG6="$TMPD6/config"
SD6="$CONFIG6/plugins/data/codex-openai-codex/state/ws6-deadbeef"
mkdir -p "$SD6"
SESS6="$TMPD6/cxc-GATE6"
mkdir -p "$SESS6"
# A dead pid whose sessionDir still exists — the one shape --gc collects.
DEAD6=99999
while kill -0 "$DEAD6" 2>/dev/null; do DEAD6=$((DEAD6 - 1)); done
printf '{"pid":%s,"sessionDir":"%s"}\n' "$DEAD6" "$SESS6" > "$SD6/broker.json"

if ! command -v jq >/dev/null 2>&1; then
  skip "#1056 gate 6: needs jq for the positive control"
else
  # Positive control first: without it, a refusal below proves nothing — a
  # fixture that never collected anything also reports gc_dirs=0.
  OK6="$(TMPDIR="$TMPD6" CLAUDE_CONFIG_DIR="$CONFIG6" bash "$REAPER" --gc --dry-run 2>&1)"
  case "$OK6" in
    *"WOULD GC  dir=$SESS6"*) pass "#1056 gate 6 control: --gc collects the dead record" ;;
    *) fail "#1056 gate 6 control: --gc should have collected $SESS6 (output: $OK6)" ;;
  esac

  # Now break the index build by putting a failing `jq` first on PATH.
  BIN6="$TMPD6/bin"; mkdir -p "$BIN6"
  printf '#!/bin/sh\nexit 127\n' > "$BIN6/jq"; chmod +x "$BIN6/jq"
  BAD6="$(PATH="$BIN6:$PATH" TMPDIR="$TMPD6" CLAUDE_CONFIG_DIR="$CONFIG6" \
          bash "$REAPER" --gc --dry-run 2>&1)"
  case "$BAD6" in
    *"WOULD GC"*) fail "#1056 gate 6: --gc collected with an unusable index (output: $BAD6)" ;;
    *"SKIP GC"*)  pass "#1056 gate 6: unusable index refuses the sweep and says so" ;;
    *)            fail "#1056 gate 6: unusable index swept silently — no SKIP GC line (output: $BAD6)" ;;
  esac

  # The refusal must not have deleted anything on the way out.
  if [ -d "$SESS6" ]; then pass "#1056 gate 6: refusal left the sessionDir intact"
  else fail "#1056 gate 6: refusal deleted $SESS6"; fi
fi



# --- Gate 7: a malformed record must not truncate the index -----------------
# jq aborts the whole stream at the first parse error when handed many files,
# so one unparseable broker.json used to drop every record after it. The GC
# pass reads that index: a live broker whose record was dropped no longer
# claims its sessionDir, and the dir gets collected while still in use (#921
# by another road). The emptiness guard of gate 6 cannot see this — the index
# is short, not empty.
TMPROOT7="${TMPDIR:-/tmp}"; TMPROOT7="${TMPROOT7%/}"
TMPD7="$(mktemp -d "$TMPROOT7/px1056g7.XXXXXX")" || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
cleanup_gate7() {
  rm -rf "$TMPD7"
  declare -F cleanup_gate6 >/dev/null && cleanup_gate6
  return 0
}
trap cleanup_gate7 EXIT INT TERM

if ! command -v jq >/dev/null 2>&1; then
  skip "#1056 gate 7: needs jq"
else
  CONFIG7="$TMPD7/config"
  SHARED7="$TMPD7/cxc-SHARED7"
  mkdir -p "$SHARED7"
  # Ordered so the malformed record sits BETWEEN the dead claimant and the live
  # one. Directory iteration is sorted, so 01/02/03 fixes that order.
  DEAD7=99999
  while kill -0 "$DEAD7" 2>/dev/null; do DEAD7=$((DEAD7 - 1)); done
  mkdir -p "$CONFIG7/plugins/data/codex-openai-codex/state/01-dead" \
           "$CONFIG7/plugins/data/codex-openai-codex/state/02-broken" \
           "$CONFIG7/plugins/data/codex-openai-codex/state/03-live"
  ST7="$CONFIG7/plugins/data/codex-openai-codex/state"
  printf '{"pid":%s,"sessionDir":"%s"}\n' "$DEAD7" "$SHARED7" > "$ST7/01-dead/broker.json"
  printf '{"pid":1,"sessionDir":"/nope"\n'                     > "$ST7/02-broken/broker.json"
  # This shell is alive and claims the same dir — the record that must survive.
  printf '{"pid":%s,"sessionDir":"%s"}\n' "$$" "$SHARED7"      > "$ST7/03-live/broker.json"

  GC7="$(TMPDIR="$TMPD7" CLAUDE_CONFIG_DIR="$CONFIG7" bash "$REAPER" --gc 2>&1)"
  if [ -d "$SHARED7" ]; then
    pass "#1056 gate 7: live claim behind a malformed record still protects the dir"
  else
    fail "#1056 gate 7: --gc deleted $SHARED7 despite a live claimant (output: $GC7)"
  fi
  case "$GC7" in
    *"a live broker still claims this sessionDir"*)
      pass "#1056 gate 7: the skip names the live claim" ;;
    *) fail "#1056 gate 7: expected a live-claimant SKIP GC line (output: $GC7)" ;;
  esac
fi


# --- Gate 8: version-drift warning against the plugin manifest (#1059) ------
# The launchd plist pins the reaper's path down to a plugin version, so an
# updated plugin leaves the job running an old copy with no signal at all. The
# reaper compares its own location against the manifest's installPath and says
# so. Two boundaries the assertions below pin: the check speaks ONLY when the
# script itself is running from inside the plugin cache (a dev checkout must
# not warn on every run, or the warning becomes noise nobody reads), and a
# mismatch never changes what the run does or what it exits with.
TMPROOT8="${TMPDIR:-/tmp}"; TMPROOT8="${TMPROOT8%/}"
TMPD8="$(mktemp -d "$TMPROOT8/px1059g8.XXXXXX")" || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
cleanup_gate8() {
  rm -rf "$TMPD8"
  declare -F cleanup_gate7 >/dev/null && cleanup_gate7
  return 0
}
trap cleanup_gate8 EXIT INT TERM

if ! command -v jq >/dev/null 2>&1; then
  skip "#1059 gate 8: needs jq"
else
  CONFIG8="$TMPD8/config"
  CACHE8="$CONFIG8/plugins/cache/praxis/praxis"
  mkdir -p "$CONFIG8/plugins" "$CACHE8/9.9.9/skills/codex-review-wrap" \
           "$CACHE8/9.9.8/skills/codex-review-wrap"
  # The manifest names 9.9.9 as the installed version; a copy of the reaper is
  # planted under BOTH that path and the older 9.9.8, so the same script can be
  # run from the current tree and from a stale one.
  printf '{"plugins":{"praxis@praxis":[{"version":"9.9.9","installPath":"%s/9.9.9"}]}}\n' \
    "$CACHE8" > "$CONFIG8/plugins/installed_plugins.json"
  cp "$REAPER" "$CACHE8/9.9.9/skills/codex-review-wrap/codex-broker-reaper.sh"
  cp "$REAPER" "$CACHE8/9.9.8/skills/codex-review-wrap/codex-broker-reaper.sh"

  # (a) stale copy inside the cache -> warns, and names both versions
  OUT8_STALE="$(TMPDIR="$TMPD8" CLAUDE_CONFIG_DIR="$CONFIG8" \
    bash "$CACHE8/9.9.8/skills/codex-review-wrap/codex-broker-reaper.sh" --gc --dry-run 2>&1)"
  RC8_STALE=$?
  case "$OUT8_STALE" in
    *"version drift"*9.9.8*9.9.9*) pass "#1059 gate 8: stale cache copy warns and names both versions" ;;
    *) fail "#1059 gate 8: expected a version-drift warning naming 9.9.8 and 9.9.9 (output: $OUT8_STALE)" ;;
  esac

  # (b) the warning is advisory — the run still completes and exits 0
  if [ "$RC8_STALE" -eq 0 ]; then
    pass "#1059 gate 8: a drifted run still exits 0"
  else
    fail "#1059 gate 8: drifted run exited $RC8_STALE, expected 0"
  fi
  case "$OUT8_STALE" in
    *"codex-broker-reaper: mode=gc"*) pass "#1059 gate 8: a drifted run still does its work" ;;
    *) fail "#1059 gate 8: drifted run printed no summary line (output: $OUT8_STALE)" ;;
  esac

  # (c) positive control — the current copy must NOT warn. Without this, a check
  # that never fires at all would satisfy (a) only by accident of the fixture.
  OUT8_CUR="$(TMPDIR="$TMPD8" CLAUDE_CONFIG_DIR="$CONFIG8" \
    bash "$CACHE8/9.9.9/skills/codex-review-wrap/codex-broker-reaper.sh" --gc --dry-run 2>&1)"
  case "$OUT8_CUR" in
    *"version drift"*) fail "#1059 gate 8: the current copy warned (output: $OUT8_CUR)" ;;
    *) pass "#1059 gate 8: the copy the manifest points at stays silent" ;;
  esac

  # (d) run from outside the cache (this repo's own tree) -> silent, even though
  # its path can never equal installPath.
  OUT8_DEV="$(TMPDIR="$TMPD8" CLAUDE_CONFIG_DIR="$CONFIG8" bash "$REAPER" --gc --dry-run 2>&1)"
  case "$OUT8_DEV" in
    *"version drift"*) fail "#1059 gate 8: a checkout outside the cache warned (output: $OUT8_DEV)" ;;
    *) pass "#1059 gate 8: a checkout outside the cache stays silent" ;;
  esac

  # (e) no manifest at all -> silent. An unreadable manifest is not evidence of
  # drift, and a warning there would fire on every host that installed the
  # plugin some other way.
  CONFIG8B="$TMPD8/config-nomanifest"
  mkdir -p "$CONFIG8B/plugins/cache/praxis/praxis/9.9.8/skills/codex-review-wrap"
  cp "$REAPER" "$CONFIG8B/plugins/cache/praxis/praxis/9.9.8/skills/codex-review-wrap/codex-broker-reaper.sh"
  OUT8_NOMAN="$(TMPDIR="$TMPD8" CLAUDE_CONFIG_DIR="$CONFIG8B" \
    bash "$CONFIG8B/plugins/cache/praxis/praxis/9.9.8/skills/codex-review-wrap/codex-broker-reaper.sh" \
    --gc --dry-run 2>&1)"
  case "$OUT8_NOMAN" in
    *"version drift"*) fail "#1059 gate 8: warned with no manifest present (output: $OUT8_NOMAN)" ;;
    *) pass "#1059 gate 8: a missing manifest is not read as drift" ;;
  esac
fi


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
