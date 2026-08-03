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
# The reaper's idle gate reads mtime through BSD `stat -f %m`, which is Darwin
# syntax, so this gate is skipped elsewhere (the CI runner is Ubuntu).
if [ "$(uname -s)" != "Darwin" ]; then
  skip "gate 3 owner-death behavior (needs Darwin: reaper's idle gate uses BSD 'stat -f %m')"
else

TMPROOT="${TMPDIR:-/tmp}"; TMPROOT="${TMPROOT%/}"
TMPD="$(mktemp -d "$TMPROOT/px919.XXXXXX")" || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
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
    LSOF_BIN="$(command -v lsof || true)"
    if [ -z "$LSOF_BIN" ]; then
      skip "#926 constraint 3 no-cwd-source KEEP (lsof not installed — nothing to remove)"
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

fi   # Darwin gate

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
