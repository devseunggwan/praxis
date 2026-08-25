#!/bin/bash
# tests/test_cmux_session_orphan_windows.sh — guard find_orphans' pane enumeration
#
# find_orphans classifies an orphan tmux session by whether EVERY pane in it
# runs a bare shell. A NUMERIC session name that comes back shell-only is typed
# safe_numeric, and safe_numeric is the one branch cmux-session-cleanup Phase 1
# kills without prompting — every other type takes the report_orphan path with
# auto_delete:false. So the shell-only verdict must cover the whole session.
#
# `tmux list-panes -t <session>` lists only the ACTIVE window's panes. A session
# whose active window is a bare shell and whose inactive window runs an agent
# therefore reads as shell-only, and Phase 1 kills the agent unprompted. `-s`
# spans all windows and is what makes the verdict cover the session.
#
# The fixture is numeric on purpose: a named fixture would exercise
# safe_named -> unsafe_named, which is the report-only path, and would leave a
# regression on the actual kill path undetected.
#
# Run:  ./tests/test_cmux_session_orphan_windows.sh
# Exit: 0 on success, 1 on first failure (after summary).
# Only an unusable tmux is a SKIP. Once the server is up, every fixture command
# and query is checked for failure and fails the run — a broken fixture must
# never look like an absent prerequisite.

set +e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIB="$REPO_ROOT/skills/cmux-session-manager/cmux-session-lib"

if [ ! -f "$LIB" ]; then
  echo "FAIL: cmux-session-lib not found at $LIB" >&2
  exit 1
fi

PASS=0
FAIL=0
FAILED_NAMES=()
SKIPPED_NAMES=()

pass() { PASS=$((PASS + 1)); echo "  PASS  $1"; }
fail() { FAIL=$((FAIL + 1)); FAILED_NAMES+=("$1"); echo "  FAIL  $1" >&2; }
skip() { SKIPPED_NAMES+=("$1"); echo "SKIPPED: $1"; }

summary_and_exit() {
  echo ""
  echo "Passed: $PASS  Failed: $FAIL  Skipped: ${#SKIPPED_NAMES[@]}"
  if [ ${#SKIPPED_NAMES[@]} -gt 0 ]; then
    for n in "${SKIPPED_NAMES[@]}"; do echo "  SKIPPED  $n"; done
  fi
  if [ "$FAIL" -gt 0 ]; then
    for n in "${FAILED_NAMES[@]}"; do echo "  FAILED  $n" >&2; done
    exit 1
  fi
  exit 0
}

# --- Prerequisites -----------------------------------------------------------
# An isolated tmux server: -L gives this test its own socket so it can never
# enumerate, classify, or kill anything in the developer's real tmux server.
SOCKET="praxis-test-orphan-$$"
TMUX_T=(tmux -L "$SOCKET")
# find_orphans types a session safe_numeric only when its name matches ^[0-9]+$.
SESSION="9$$"

# kill-server ends the server process but leaves its socket file on disk, so a
# run that only kills the server leaks one dead socket inode per invocation.
# SOCKET_PATH is filled in from the live server below; until then it is empty
# and cleanup has nothing to unlink.
SOCKET_PATH=""
cleanup() {
  "${TMUX_T[@]}" kill-server 2>/dev/null
  [ -n "$SOCKET_PATH" ] && rm -f "$SOCKET_PATH"
}
trap cleanup EXIT

if ! command -v tmux >/dev/null 2>&1; then
  skip "tmux absent — pane-enumeration gates cannot run"
  summary_and_exit
fi

# The only tolerated prerequisite failure: this environment cannot run a tmux
# server at all (no socket dir, sandbox restriction). Everything after this
# point is fixture construction, and a failure there is a real failure.
if ! "${TMUX_T[@]}" new-session -d -s "$SESSION" -n active-shell 2>/dev/null; then
  skip "tmux server would not start on an isolated socket — gates cannot run"
  summary_and_exit
fi

# Ask the running server where its socket is rather than rebuilding the path
# from TMUX_TMPDIR/TMPDIR/tmp precedence, which differs per platform.
SOCKET_PATH=$("${TMUX_T[@]}" display-message -p '#{socket_path}' 2>/dev/null)

# --- Fixture -----------------------------------------------------------------
# Window 0 (active): a bare shell.  Window 1 (inactive): a non-shell process.
# `sleep` stands in for an agent binary: what matters to find_orphans is only
# that pane_current_command is not in its shell allowlist.
# The trailing colon is load-bearing: a bare numeric target is read as a WINDOW
# INDEX first, so `-t 912345` would add the window to the caller's own session
# and leave the fixture with one window. `-t 912345:` forces the session
# reading. (find_orphans itself is safe here — `-s` forces the same reading.)
if ! "${TMUX_T[@]}" new-window -d -t "$SESSION:" -n hidden-agent 'sleep 300' 2>/dev/null; then
  fail "fixture: could not create the inactive window (tmux new-window failed)"
  summary_and_exit
fi
if ! "${TMUX_T[@]}" select-window -t "$SESSION:0" 2>/dev/null; then
  fail "fixture: could not make window 0 active (tmux select-window failed)"
  summary_and_exit
fi

active_cmds=$("${TMUX_T[@]}" list-panes -t "$SESSION" -F '#{pane_current_command}' 2>/dev/null)
active_rc=$?
all_cmds=$("${TMUX_T[@]}" list-panes -s -t "$SESSION" -F '#{pane_current_command}' 2>/dev/null)
all_rc=$?

if [ "$active_rc" -ne 0 ] || [ -z "$active_cmds" ]; then
  fail "fixture: active-window pane query failed (rc=$active_rc, output=[$active_cmds])"
  summary_and_exit
fi
if [ "$all_rc" -ne 0 ] || [ -z "$all_cmds" ]; then
  fail "fixture: session-wide pane query failed (rc=$all_rc, output=[$all_cmds])"
  summary_and_exit
fi
# Equal output means the second window never materialized. That is a broken
# fixture, not an absent prerequisite — the gates below would pass vacuously.
if [ "$active_cmds" = "$all_cmds" ]; then
  fail "fixture: no hidden window present (active=[$active_cmds] all=[$all_cmds])"
  summary_and_exit
fi

# --- Gate 1: the enumeration itself ------------------------------------------
# Positive control for gate 2: proves the fixture hides a pane that a
# session-wide enumeration finds, so gate 2's verdict is not vacuous.
if printf '%s\n' "$all_cmds" | grep -q 'sleep'; then
  pass "session-wide enumeration sees the inactive window's process"
else
  fail "session-wide enumeration missed the inactive window (all=[$all_cmds])"
fi

if printf '%s\n' "$active_cmds" | grep -q 'sleep'; then
  fail "active-window enumeration unexpectedly saw the hidden pane — fixture invalid"
else
  pass "active-window enumeration alone misses it (the defect's mechanism)"
fi

# --- Gate 2: find_orphans' verdict -------------------------------------------
# Source the lib in a subshell under the flags it documents as required, point
# it at the isolated server, and let it classify the fixture. WS_COUNT=0 stands
# for "no live cmux workspaces", which is what makes the fixture an orphan and
# also what keeps the numeric name from matching a live workspace number.
verdict=$(
  set -euo pipefail
  # shellcheck source=/dev/null
  source "$LIB"
  tmux() { command tmux -L "$SOCKET" "$@"; }
  WS_COUNT=0
  find_orphans
  i=0
  while [ "$i" -lt "$ORPHAN_COUNT" ]; do
    entry="${ORPHAN_DATA[$i]}"
    name="${entry%%|*}"
    rest="${entry#*|}"
    type="${rest%%|*}"
    if [ "$name" = "$SESSION" ]; then printf '%s' "$type"; fi
    i=$((i + 1))
  done
) 2>/dev/null

case "$verdict" in
  unsafe_numeric)
    pass "find_orphans classifies the numeric fixture as unsafe_numeric (Phase 1 will not kill it)"
    ;;
  safe_numeric)
    fail "find_orphans classified the fixture as safe_numeric — Phase 1 would kill the hidden agent unprompted"
    ;;
  "")
    fail "find_orphans did not classify the fixture at all (empty verdict)"
    ;;
  *)
    fail "find_orphans returned '$verdict' — the fixture is numeric, so the verdict must be one of safe_numeric/unsafe_numeric"
    ;;
esac

summary_and_exit
