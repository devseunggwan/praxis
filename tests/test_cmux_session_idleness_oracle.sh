#!/bin/bash
# tests/test_cmux_session_idleness_oracle.sh — guard the idleness oracle itself
#
# orphan_idle_evidence answers one question about an orphan tmux session: is the
# process attached to it parked, or working? A wrong "parked" demotes a session
# that is doing something to a tier the operator is invited to clear, so every
# gate below is written around what must NOT be called idle.
#
# The oracle ANDs two signals, and the matrix here exists to show that both are
# load-bearing — each gate turns exactly one of them off:
#   quiet window   (#{window_activity} older than IDLE_ACTIVITY_SECS)
#   startup prompt (IDLE_PROMPT_PATTERN on every non-shell pane's screen)
#
# Run:  ./tests/test_cmux_session_idleness_oracle.sh
# Exit: 0 on success, 1 on first failure (after summary).
# Only an unusable tmux is a SKIP. Once the server is up, every fixture command
# is checked and a fixture failure fails the run — a broken fixture must never
# look like an absent prerequisite.

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
# -L gives this test its own tmux server, so it can never read or kill anything
# in the developer's real one.
SOCKET="praxis-test-idle-$$"
TMUX_T=(tmux -L "$SOCKET")

SOCKET_PATH=""
cleanup() {
  "${TMUX_T[@]}" kill-server 2>/dev/null
  [ -n "$SOCKET_PATH" ] && rm -f "$SOCKET_PATH"
}
trap cleanup EXIT

if ! command -v tmux >/dev/null 2>&1; then
  skip "tmux absent — the idleness oracle cannot run"
  summary_and_exit
fi

# --- Fixtures ----------------------------------------------------------------
# PARKED reproduces the observed case: a non-shell process stopped at the
# startup trust prompt. `exec sleep` is what makes pane_current_command a name
# outside the shell allowlist, which is the whole reason such a session reaches
# the oracle at all; the printf before it puts the real prompt text on screen.
PARKED="parked-$$"
# WORKING is the negative control: same non-shell command, same quiet window,
# but a screen that is not a startup prompt.
WORKING="working-$$"
# SHELLY has no non-shell pane at all — the oracle must not answer "idle" for a
# session that never had a process to be idle about.
SHELLY="shelly-$$"

PROMPT_CMD='printf "Do you trust the files in this folder?\n 1. Yes, I trust this folder\n 2. No, exit\n"; exec sleep 300'
# The second line is the string that false-matched an unanchored prompt pattern:
# ordinary build output whose words overlap the startup menu. Keeping it in the
# control is what makes this file catch a regression back to fragment matching.
WORK_CMD='printf "Compiling target 41/128 ...\nbuild complete: No, exit is disabled here\n"; exec sleep 300'

# The exit status of the first new-session is not on its own proof that a
# usable server exists — a sandbox that denies the socket directory can leave
# the client reporting success with nothing behind it. Everything after this
# block is treated as a hard failure, so the server has to be confirmed
# reachable here, or a restricted environment fails the suite instead of
# skipping the way this file documents.
if ! "${TMUX_T[@]}" new-session -d -s "$PARKED" "$PROMPT_CMD" 2>/dev/null; then
  skip "tmux server would not start on an isolated socket — gates cannot run"
  summary_and_exit
fi
SOCKET_PATH=$("${TMUX_T[@]}" display-message -p '#{socket_path}' 2>/dev/null)
socket_rc=$?
if [ "$socket_rc" -ne 0 ] || [ -z "$SOCKET_PATH" ]; then
  skip "tmux server is not reachable on the isolated socket (rc=$socket_rc) — gates cannot run"
  summary_and_exit
fi
if ! "${TMUX_T[@]}" list-sessions -F '#{session_name}' 2>/dev/null | grep -q "^$PARKED$"; then
  skip "tmux reported success but the session is not listed — gates cannot run"
  summary_and_exit
fi

if ! "${TMUX_T[@]}" new-session -d -s "$WORKING" "$WORK_CMD" 2>/dev/null; then
  fail "fixture: could not create the working session"
  summary_and_exit
fi
if ! "${TMUX_T[@]}" new-session -d -s "$SHELLY" 2>/dev/null; then
  fail "fixture: could not create the shell-only session"
  summary_and_exit
fi

# `exec sleep` replaces the shell only after the printf drains; until then
# pane_current_command is still the shell and every fixture would read as
# shell-only. Poll rather than sleep a fixed span so a slow host cannot make
# the whole matrix pass vacuously.
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

# The prompt text must actually be on screen, or the startup-prompt signal would
# read "absent" for a reason that has nothing to do with the oracle.
if ! "${TMUX_T[@]}" capture-pane -p -t "$PARKED" 2>/dev/null | grep -q 'I trust this folder'; then
  fail "fixture: the parked session's screen does not show the startup prompt"
  summary_and_exit
fi
# The control's screen must carry the overlapping words (so the gate is a real
# test of anchoring) while not being a startup prompt (so idle stays wrong).
if ! "${TMUX_T[@]}" capture-pane -p -t "$WORKING" 2>/dev/null | grep -q 'No, exit'; then
  fail "fixture: the working session's screen lost the overlapping words — control is vacuous"
  summary_and_exit
fi
if "${TMUX_T[@]}" capture-pane -p -t "$WORKING" 2>/dev/null | grep -q 'I trust this folder'; then
  fail "fixture: the working session shows the real trust prompt — control invalid"
  summary_and_exit
fi

# --- Oracle driver -----------------------------------------------------------
# Runs orphan_idle_evidence against the isolated server and prints
# "<rc>|<evidence>". IDLE_ACTIVITY_SECS is passed in per gate: these fixtures
# were created seconds ago, so the quiet-window threshold is what decides
# whether that signal is on or off, and each gate sets it deliberately.
ask_oracle() {
  local sess="$1" secs="$2"
  (
    set -euo pipefail
    # shellcheck source=/dev/null
    source "$LIB"
    tmux() { command tmux -L "$SOCKET" "$@"; }
    IDLE_ACTIVITY_SECS="$secs"
    if ev=$(orphan_idle_evidence "$sess"); then
      printf '0|%s' "$ev"
    else
      printf '1|%s' "$ev"
    fi
  ) 2>/dev/null
}

# --- Gate 1: the observed case, both signals on -------------------------------
out=$(ask_oracle "$PARKED" 0)
rc="${out%%|*}"; ev="${out#*|}"
if [ "$rc" = "0" ]; then
  pass "parked session is called idle (evidence: $ev)"
else
  fail "parked session was not called idle (rc=$rc, evidence=[$ev])"
fi

# Evidence is what the operator decides from, so an empty one is a failure even
# when the verdict is right.
case "$ev" in
  *"startup prompt"*"no output for"*) pass "the idle verdict carries both signals as evidence" ;;
  *) fail "evidence does not name both signals: [$ev]" ;;
esac

# --- Gate 2: negative control — real work stays out of idle -------------------
# Same command, same threshold. Only the screen differs, so a pass here is
# attributable to the startup-prompt signal and nothing else.
out=$(ask_oracle "$WORKING" 0)
rc="${out%%|*}"
if [ "$rc" != "0" ]; then
  pass "a working non-shell pane whose output overlaps the prompt wording is NOT called idle"
else
  fail "a working non-shell pane was called idle — the oracle demotes real work"
fi

# --- Gate 3: the quiet-window signal is load-bearing --------------------------
# Same parked fixture that passed gate 1, with a threshold no freshly-created
# window can clear. A pass proves gate 1 was not decided by the screen alone.
out=$(ask_oracle "$PARKED" 86400)
rc="${out%%|*}"
if [ "$rc" != "0" ]; then
  pass "a recently-active parked session is NOT called idle (quiet-window signal is load-bearing)"
else
  fail "the quiet-window threshold had no effect — a just-opened prompt would be called idle"
fi

# --- Gate 4: no non-shell pane, no verdict -----------------------------------
out=$(ask_oracle "$SHELLY" 0)
rc="${out%%|*}"
if [ "$rc" != "0" ]; then
  pass "a shell-only session gets no idle verdict (it is the safe tier's business)"
else
  fail "a shell-only session was called idle"
fi

# --- Gate 5: an unreadable session does not answer ----------------------------
out=$(ask_oracle "no-such-session-$$" 0)
rc="${out%%|*}"
if [ "$rc" != "0" ]; then
  pass "an unreadable session yields no idle verdict (uncertainty keeps the stricter tier)"
else
  fail "an unreadable session was called idle"
fi

# --- Gate 6: an unusable threshold does not answer ----------------------------
# A negative threshold makes `[[ age -lt threshold ]]` false for every age, and
# bash evaluates a non-numeric one to 0, so both would wave a fresh pane past
# the quiet window and leave the screen match deciding alone. PARKED is the
# fixture that DOES pass at a valid threshold, so a failure here is attributable
# to the threshold and nothing else.
for bad in -1 abc ""; do
  out=$(ask_oracle "$PARKED" "$bad")
  rc="${out%%|*}"
  if [ "$rc" != "0" ]; then
    pass "threshold [$bad] yields no idle verdict (unusable input keeps the stricter tier)"
  else
    fail "threshold [$bad] produced an idle verdict"
  fi
done

# Positive control for gate 6: the same fixture, same driver, at a valid
# threshold, still answers — so the three refusals above are the threshold
# being rejected, not the oracle having gone silent.
out=$(ask_oracle "$PARKED" 0)
rc="${out%%|*}"
if [ "$rc" = "0" ]; then
  pass "positive control: the same fixture still answers at a valid threshold"
else
  fail "positive control failed — the oracle is silent regardless of threshold"
fi

summary_and_exit
