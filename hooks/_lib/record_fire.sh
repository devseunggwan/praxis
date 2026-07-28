#!/bin/sh
# Fire-ledger recording for pure-shell hooks (issue #848).
#
# Every Python hook reaches the ledger through `@fail_open`
# (`record_standalone_fire`) or the Bash dispatcher (`record_group_fires`).
# A hook written in shell runs through neither, so its engagements were
# invisible: the four `impl.sh` hooks held 0 ledger records while the audit
# that reads the ledger treated absence as "never fires".
#
# Source this file, then call praxis_record_fire at each terminal branch:
#
#   . "$(dirname "$0")/../../_lib/record_fire.sh"
#   praxis_record_fire completion-verify completion-verify block "$SESSION_ID" ""
#
# Args (positional, all required — pass "" for an unknown value):
#   1 hook        — manifest `name` of the firing hook
#   2 role        — manifest `role` (preflight-gate / advisory-nudge / …)
#   3 decision    — block | ask | advise | pass
#   4 session_id  — from the hook's own stdin payload
#   5 tool        — tool name for PreToolUse-family hooks; "" elsewhere
#
# Writes a granularity="rich" record, the same shape
# `_fire_ledger.record_session_fire` writes for a standalone Python hook —
# NOT the coarse shape, because a shell hook has already parsed its stdin
# payload and holds a real session_id. Shell hooks have no coarse fallback,
# so unlike the Python callers there is nothing to suppress on success and
# nothing to restore on failure.
#
# Fail-open, unconditionally: python3 missing, _lib unreadable, ledger
# unwritable, malformed argument — every failure is swallowed and the caller
# continues. A telemetry write must never change a hook's decision.

# praxis_fire_arm <hook> <role> <session_id> [tool]
#
# Records exactly one fire at process exit, whichever branch got there. A
# shell hook exits from many places (guard clauses, early passes, the emit
# path); arming an EXIT trap once is what keeps a later-added early `exit 0`
# from silently dropping out of the ledger. Set PRAXIS_FIRE_DECISION before
# the exiting branch to record something other than the "pass" default.
#
# Call it AFTER the hook has parsed session_id — a record keyed to an unknown
# session cannot be aggregated per-session, which is the whole reason a shell
# hook writes the rich shape. Guard clauses that run before parsing (missing
# jq, unparseable stdin) are therefore deliberately unrecorded.
praxis_fire_arm() {
  PRAXIS_FIRE_HOOK="$1"
  PRAXIS_FIRE_ROLE="$2"
  PRAXIS_FIRE_SESSION="${3:-}"
  PRAXIS_FIRE_TOOL="${4:-}"
  PRAXIS_FIRE_DECISION="pass"
  trap 'praxis_record_fire "$PRAXIS_FIRE_HOOK" "$PRAXIS_FIRE_ROLE" "$PRAXIS_FIRE_DECISION" "$PRAXIS_FIRE_SESSION" "$PRAXIS_FIRE_TOOL"' EXIT
}

praxis_record_fire() {
  # CDPATH is unset inside the subshell, not prefixed on `cd` — a prefixed
  # `CDPATH= cd` reads to shellcheck as a stray empty assignment (SC1007).
  _rf_lib_dir="${PRAXIS_LIB_DIR:-$(unset CDPATH; cd -- "$(dirname -- "$0")/../../_lib" 2>/dev/null && pwd)}"
  [ -n "$_rf_lib_dir" ] || return 0
  command -v python3 >/dev/null 2>&1 || return 0

  python3 -c '
import sys
sys.path.insert(0, sys.argv[1])
try:
    import _fire_ledger
    _fire_ledger.record_session_fire(
        hook=sys.argv[2], role=sys.argv[3], decision=sys.argv[4],
        session_id=sys.argv[5], tool=sys.argv[6],
    )
except Exception:
    pass
' "$_rf_lib_dir" "$1" "$2" "$3" "${4:-}" "${5:-}" >/dev/null 2>&1 || true
  return 0
}
