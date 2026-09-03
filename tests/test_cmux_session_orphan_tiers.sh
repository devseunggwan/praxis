#!/bin/bash
# tests/test_cmux_session_orphan_tiers.sh — guard the three-tier orphan verdict
#
# find_orphans types every orphan tmux session, and cmux-session-cleanup Phase 1
# kills exactly one of those types — safe_numeric — without prompting. Adding an
# idle tier between safe and unsafe therefore has one hard requirement: it must
# move sessions OUT of unsafe and never INTO safe. Every gate below is a check
# on that direction.
#
# The oracle itself (quiet window ANDed with a startup-prompt screen) has its own
# gates in tests/test_cmux_session_idleness_oracle.sh. This file checks only what
# find_orphans does with its verdict, and the consumers' handling of it.
#
# Run:  ./tests/test_cmux_session_orphan_tiers.sh
# Exit: 0 on success, 1 on first failure (after summary).
# Only an unusable tmux is a SKIP; a fixture failure fails the run.

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

SOCKET="praxis-test-tiers-$$"
TMUX_T=(tmux -L "$SOCKET")
SOCKET_PATH=""
cleanup() {
  "${TMUX_T[@]}" kill-server 2>/dev/null
  [ -n "$SOCKET_PATH" ] && rm -f "$SOCKET_PATH"
}
trap cleanup EXIT

if ! command -v tmux >/dev/null 2>&1; then
  skip "tmux absent — orphan classification gates cannot run"
  summary_and_exit
fi

# --- Fixtures ----------------------------------------------------------------
# All three names are numeric, because numeric is the tier whose safe branch
# Phase 1 kills unprompted. A named fixture would exercise the report-only side
# and leave a regression on the kill path invisible.
PARKED="91$$"    # the observed case: non-shell, stopped at the startup prompt
WORKING="92$$"   # negative control: non-shell, doing something
SHELLY="93$$"    # regression control: shell only

PROMPT_CMD='printf "Do you trust the files in this folder?\n 1. Yes, I trust this folder\n 2. No, exit\n"; exec sleep 300'
WORK_CMD='printf "Compiling target 41/128 ...\n"; exec sleep 300'

if ! "${TMUX_T[@]}" new-session -d -s "$PARKED" "$PROMPT_CMD" 2>/dev/null; then
  skip "tmux server would not start on an isolated socket — gates cannot run"
  summary_and_exit
fi
SOCKET_PATH=$("${TMUX_T[@]}" display-message -p '#{socket_path}' 2>/dev/null)

if ! "${TMUX_T[@]}" new-session -d -s "$WORKING" "$WORK_CMD" 2>/dev/null; then
  fail "fixture: could not create the working session"
  summary_and_exit
fi
if ! "${TMUX_T[@]}" new-session -d -s "$SHELLY" 2>/dev/null; then
  fail "fixture: could not create the shell-only session"
  summary_and_exit
fi

# `exec sleep` only replaces the shell once the printf drains, so poll for the
# non-shell command rather than sleeping a fixed span — on a slow host a fixed
# sleep would leave every fixture reading as shell-only and pass vacuously.
wait_for_nonshell() {
  local sess="$1" i=0 cmds
  while [ "$i" -lt 50 ]; do
    cmds=$("${TMUX_T[@]}" list-panes -s -t "$sess" -F '#{pane_current_command}' 2>/dev/null)
    case "$cmds" in
      *sleep*) return 0 ;;
    esac
    sleep 0.1
    i=$((i + 1))
  done
  return 1
}
for s in "$PARKED" "$WORKING"; do
  if ! wait_for_nonshell "$s"; then
    fail "fixture: $s never reached a non-shell pane command"
    summary_and_exit
  fi
done

# --- Driver ------------------------------------------------------------------
# Prints "<type>|<evidence>" for one session. WS_COUNT=0 stands for "no live
# cmux workspaces", which is what makes these sessions orphans at all.
# CMUX_IDLE_ACTIVITY_SECS=0 turns the oracle's quiet-window signal on for
# fixtures created seconds ago; the signal's own load-bearingness is proved in
# tests/test_cmux_session_idleness_oracle.sh, not here.
classify() {
  local want="$1"
  (
    set -euo pipefail
    export CMUX_IDLE_ACTIVITY_SECS=0
    # shellcheck source=/dev/null
    source "$LIB"
    tmux() { command tmux -L "$SOCKET" "$@"; }
    WS_COUNT=0
    find_orphans
    i=0
    while [ "$i" -lt "$ORPHAN_COUNT" ]; do
      entry="${ORPHAN_DATA[$i]}"
      name=$(printf '%s' "$entry" | cut -d'|' -f1)
      if [ "$name" = "$want" ]; then
        printf '%s|%s' "$(printf '%s' "$entry" | cut -d'|' -f2)" "$(printf '%s' "$entry" | cut -d'|' -f4)"
      fi
      i=$((i + 1))
    done
  ) 2>/dev/null
}

# --- Gate 1: the observed case lands in the new tier --------------------------
out=$(classify "$PARKED"); type="${out%%|*}"; ev="${out#*|}"
if [ "$type" = "idle_numeric" ]; then
  pass "a parked non-shell session is typed idle_numeric (was unsafe_numeric)"
else
  fail "parked session typed '$type', expected idle_numeric"
fi

# The tier is report-only, so the evidence is the entire deliverable — an idle
# verdict with nothing to show is worse than no verdict.
if [ -n "$ev" ]; then
  pass "the idle verdict carries evidence to the caller ($ev)"
else
  fail "the idle verdict reached the caller with no evidence"
fi

# --- Gate 2: negative control — real work stays unsafe ------------------------
# This is the gate that separates "classify idle sessions" from "demote every
# non-shell session". Same threshold, same command, different screen.
out=$(classify "$WORKING"); type="${out%%|*}"
if [ "$type" = "unsafe_numeric" ]; then
  pass "a working non-shell session stays unsafe_numeric"
else
  fail "working session typed '$type', expected unsafe_numeric — the tier demotes real work"
fi

# --- Gate 3: no regression on the auto-kill tier ------------------------------
out=$(classify "$SHELLY"); type="${out%%|*}"
if [ "$type" = "safe_numeric" ]; then
  pass "a shell-only session is still safe_numeric"
else
  fail "shell-only session typed '$type', expected safe_numeric"
fi

# --- Gate 4: idle never widens what Phase 1 kills -----------------------------
# cmux-session-cleanup kills on an exact match against safe_numeric. Reading the
# call site is what proves the new tier cannot reach it — a classification test
# alone would still pass if the kill branch had been widened to a prefix match.
CLEANUP="$REPO_ROOT/skills/cmux-session-manager/cmux-session-cleanup"
if grep -qE '^\s*if \[\[ "\$otype" == "safe_numeric" \]\]; then$' "$CLEANUP"; then
  pass "Phase 1 still kills on an exact safe_numeric match (idle_* cannot reach it)"
else
  fail "Phase 1's kill condition is no longer an exact safe_numeric match"
fi

# --- Gate 5: the idle tier's counter does not clobber the dashboard's ---------
# cmux-session-status counts workspace states first and orphan tiers second. The
# two blocks live in one shell, so an orphan counter reusing `count_idle` is
# re-initialised to 0 after the workspace loop has filled it, and the dashboard's
# Idle column silently reads 0. The bug is invisible to a classification test —
# find_orphans is correct either way — so it is checked at the source.
STATUS="$REPO_ROOT/skills/cmux-session-manager/cmux-session-status"
if grep -qE '^count_safe=0;.*count_idle=0' "$STATUS"; then
  fail "the orphan tier counter reuses count_idle — it resets the workspace Idle count to 0"
else
  pass "the orphan tier counter is separate from the workspace Idle counter"
fi

summary_and_exit
