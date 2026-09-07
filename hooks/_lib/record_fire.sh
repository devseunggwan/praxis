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
# PER-FIRE COST (issue #1183): the record used to be written by spawning
# `python3 -c 'import _fire_ledger; …'` — a full interpreter cold start on
# EVERY fire, including plain passes, of every instrumented shell hook (two
# of which run on each Stop). The append is now pure shell: the record's
# fields are simple scalars, so the JSON line is assembled with printf and
# appended via the shell's own `>>` (O_APPEND). python3 remains for exactly
# two cold paths that cannot regress the per-fire cost:
#   - a field value outside the JSON-safe charset (needs real JSON escaping);
#   - the once-per-UTC-day rollover (`_fire_ledger.rotate_telemetry`: gzip
#     finished days, then the retention sweep).
#
# Fail-open, unconditionally: ledger unwritable, unresolvable _lib, malformed
# argument — every failure is swallowed and the caller continues. A telemetry
# write must never change a hook's decision. python3 being missing no longer
# suppresses the record (only the escape fallback and the sweep need it).

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

# _praxis_record_fire_py <lib_dir> <hook> <role> <decision> <session_id> <tool>
#
# Escape-fallback writer: delegates to `_fire_ledger.record_session_fire`
# with `fold_pass=False`, so it lands the same row the fast path would; its
# json.dumps handles arbitrary field content. Reached only when a field
# value falls outside the JSON-safe charset the fast path allows — in practice
# never for the shipped hooks (session ids are UUID-shaped, the rest are
# manifest literals), so the interpreter cold start stays off the hot path.
_praxis_record_fire_py() {
  command -v python3 >/dev/null 2>&1 || return 0
  python3 -c '
import sys
sys.path.insert(0, sys.argv[1])
try:
    import _fire_ledger
    _fire_ledger.record_session_fire(
        hook=sys.argv[2], role=sys.argv[3], decision=sys.argv[4],
        session_id=sys.argv[5], tool=sys.argv[6],
        # A row, not a counted pass: the fast path writes its row with a
        # plain shell redirect, and this fallback stands in for exactly
        # that path (issue #1238).
        fold_pass=False,
    )
except Exception:
    pass
' "$1" "$2" "$3" "$4" "$5" "$6" >/dev/null 2>&1 || true
  return 0
}

# _praxis_home_fallback
#
# Prints the caller's home directory from the passwd database, or nothing on
# failure. Reached only when $HOME is unset — never on the per-fire hot path
# — so a fork here (id, getent/dscl, awk) costs nothing that matters. Mirrors
# Python's Path.home(), which falls back the same way (pwd.getpwuid) rather
# than treating an unset HOME as empty: without this, resolve_path()'s
# no-.git branch built "${HOME:-}/.praxis/telemetry" as "/.praxis/telemetry"
# with HOME unset — root-owned and unwritable by a normal user, so mkdir
# failed and every fire record was silently dropped.
_praxis_home_fallback() {
  _rf_user=$(id -un 2>/dev/null) || return 1
  [ -n "$_rf_user" ] || return 1
  if command -v getent >/dev/null 2>&1; then
    # Linux/glibc: field 6 of the passwd entry.
    _rf_pw_home=$(getent passwd "$_rf_user" 2>/dev/null | awk -F: '{ print $6 }')
  elif command -v dscl >/dev/null 2>&1; then
    # macOS has no getent; Directory Service holds the same record.
    _rf_pw_home=$(dscl . -read "/Users/$_rf_user" NFSHomeDirectory 2>/dev/null | awk '{ print $2 }')
  else
    _rf_pw_home=""
  fi
  [ -n "$_rf_pw_home" ] || return 1
  printf '%s\n' "$_rf_pw_home"
}

# _praxis_trim <value>
#
# Strips leading/trailing whitespace into _PRAXIS_TRIMMED (no subshell — a
# $(...) capture would fork on every fire). Mirrors Python's str.strip() for
# the env values _fire_ledger reads stripped (`_disabled()`, `resolve_path()`);
# without this a padded value ('1 ', ' /path ') would diverge between the two
# writers — the disable flag ignored, or records split across two files.
_praxis_trim() {
  _PRAXIS_TRIMMED="$1"
  while case "$_PRAXIS_TRIMMED" in [[:space:]]*) true ;; *) false ;; esac; do
    _PRAXIS_TRIMMED="${_PRAXIS_TRIMMED#?}"
  done
  while case "$_PRAXIS_TRIMMED" in *[[:space:]]) true ;; *) false ;; esac; do
    _PRAXIS_TRIMMED="${_PRAXIS_TRIMMED%?}"
  done
}

praxis_record_fire() {
  # Mirrors _fire_ledger._disabled(): stripped comparison against "1".
  _praxis_trim "${PRAXIS_FIRE_TELEMETRY_DISABLE:-}"
  [ "$_PRAXIS_TRIMMED" = "1" ] && return 0

  # CDPATH is unset inside the subshell, not prefixed on `cd` — a prefixed
  # `CDPATH= cd` reads to shellcheck as a stray empty assignment (SC1007).
  # cd -P / pwd -P resolve PHYSICALLY, matching Path(__file__).resolve() in
  # _fire_ledger._checkout_root(): under a symlinked hooks layout a logical
  # resolution would probe a different root than Python and split the ledger
  # across two directories — exactly the corruption resolve_telemetry_dir()'s
  # docstring warns against.
  _rf_lib_dir="${PRAXIS_LIB_DIR:-$(unset CDPATH; cd -P -- "$(dirname -- "$0")/../../_lib" 2>/dev/null && pwd -P)}"

  # Escape fallback: any field containing a character outside the JSON-safe
  # charset (needs \" / \\ / \uXXXX escaping json.dumps performs) goes through
  # _fire_ledger. POSIX sh has no substring replacement, so the fast path
  # allowlists instead of escaping. Free text can only enter via session_id
  # and tool (payload-derived); hook/role/decision are caller literals, but
  # all five are checked uniformly — a new caller must not be able to corrupt
  # the ledger.
  case "${1:-}${2:-}${3:-}${4:-}${5:-}" in
    *[!A-Za-z0-9._:-]*)
      [ -n "$_rf_lib_dir" ] || return 0
      _praxis_record_fire_py "$_rf_lib_dir" "${1:-}" "${2:-}" "${3:-}" "${4:-}" "${5:-}"
      return 0
      ;;
  esac

  # Path resolution mirrors _fire_ledger.resolve_path():
  #   PRAXIS_FIRE_TELEMETRY_FILE (stripped, like Python) → dev checkout
  #   ledger → real ledger ($PRAXIS_HOME/telemetry, else ~/.praxis/telemetry).
  # The dev-checkout probe mirrors _checkout_root(): the package root sits
  # exactly two levels above _lib, and `.git` there (dir in a clone, file in
  # a linked worktree) marks a development checkout.
  _praxis_trim "${PRAXIS_FIRE_TELEMETRY_FILE:-}"
  _rf_path="$_PRAXIS_TRIMMED"
  if [ -z "$_rf_path" ]; then
    [ -n "$_rf_lib_dir" ] || return 0
    _rf_today=$(date -u +%Y-%m-%d) || return 0
    _rf_root=$(unset CDPATH; cd -P -- "$_rf_lib_dir/../.." 2>/dev/null && pwd -P)
    [ -n "$_rf_root" ] || return 0
    if [ -e "$_rf_root/.git" ]; then
      _rf_dir="$_rf_root/.praxis-dev-telemetry"
    else
      # PRAXIS_HOME relocates the whole ~/.praxis tree (issue #1340). The
      # rule is _paths.sh praxis_home() — one trailing slash stripped, a
      # leading `~` or `~/` expanded against the home directory, empty means
      # unset — restated inline rather than sourced. This file is
      # deliberately self-contained: it is copied on its own into probe
      # trees (tests/hooks/_lib/test_record_fire.sh) and into installs that
      # resolve _lib through PRAXIS_LIB_DIR, where a `.` of a sibling that
      # is not there would abort the hook under `set -e`; and the per-fire
      # path (#1183) has no room for another file read. Keep the two in
      # agreement; tests/test_paths.sh pins shell/Python parity on this.
      _rf_praxis_home="${PRAXIS_HOME:-}"
      _rf_praxis_home="${_rf_praxis_home%/}"
      # Held in a variable so the tilde stays a literal to match against
      # rather than something the shell might try to expand here.
      _rf_tilde='~'
      _rf_home=""
      case "$_rf_praxis_home" in
        ''|"$_rf_tilde"|"$_rf_tilde"/*)
          # The home directory is needed: for the default root, or to expand
          # a tilde the way _paths.sh does against $HOME. _paths.py runs
          # expanduser, which consults the passwd database when HOME is
          # unset, so the same fallback applies here (PR #1207 round 2).
          _rf_home="${HOME:-}"
          if [ -z "$_rf_home" ]; then
            _rf_home=$(_praxis_home_fallback) || _rf_home=""
          fi
          # No portable answer (no id/getent/dscl, or no matching passwd
          # entry): refuse the write rather than fall through to
          # "${HOME:-}/..." again, which would silently resolve to the
          # unwritable root-level path this fix exists to avoid.
          [ -n "$_rf_home" ] || return 0
          ;;
      esac
      case "$_rf_praxis_home" in
        '') _rf_dir="$_rf_home/.praxis/telemetry" ;;
        "$_rf_tilde") _rf_dir="$_rf_home/telemetry" ;;
        "$_rf_tilde"/*) _rf_dir="$_rf_home/${_rf_praxis_home#"$_rf_tilde"/}/telemetry" ;;
        *) _rf_dir="$_rf_praxis_home/telemetry" ;;
      esac
      unset _rf_praxis_home _rf_tilde
    fi
    _rf_path="$_rf_dir/fire-events-$_rf_today.jsonl"
  fi

  # Non-regular-file guard, mirroring _atomic_append's refusal to write to a
  # FIFO / device / symlink at the target (planted, or via the env override).
  # `>>` on a FIFO would BLOCK the hook, which is worse than a lost record.
  # A pre-open [ -f ] check narrows but cannot close the lstat→open race the
  # Python writer closes with O_NOFOLLOW|O_NONBLOCK; for this fail-open
  # telemetry append the narrowed window is accepted.
  _rf_first=1
  if [ -e "$_rf_path" ] || [ -L "$_rf_path" ]; then
    { [ -f "$_rf_path" ] && [ ! -L "$_rf_path" ]; } || return 0
    _rf_first=0
  fi

  # Timestamp shape matches datetime.now(timezone.utc).isoformat() —
  # microsecond precision where `date` supports GNU %N; readers parse it with
  # datetime.fromisoformat, which also accepts the fraction-less fallback
  # (Python itself omits the fraction when microsecond == 0).
  _rf_ts=$(date -u +%Y-%m-%dT%H:%M:%S.%6N+00:00 2>/dev/null) || _rf_ts=""
  case "$_rf_ts" in
    ''|*N*) _rf_ts=$(date -u +%Y-%m-%dT%H:%M:%S+00:00) || return 0 ;;
  esac

  mkdir -p -- "$(dirname -- "$_rf_path")" 2>/dev/null || return 0

  # Key order and ": " / ", " separators match json.dumps' defaults in
  # _fire_ledger.record_session_fire, so shell- and Python-written records are
  # byte-compatible (timestamp aside). Single printf of one short line
  # (~200 B, far under PIPE_BUF's >=4096) under the shell's O_APPEND `>>`
  # keeps the same no-torn-lines concurrency contract as _atomic_append's
  # per-line os.write.
  if [ "$_rf_first" = "1" ]; then
    # `>>` alone requests mode 0666 at creation, narrowed only by the ambient
    # umask; _atomic_append requests 0o644 explicitly (os.open(path, flags,
    # 0o644), hooks/_lib/_fire_ledger.py:297). Under a permissive umask
    # (000/002) that gap survives into the file: 0666/0664 for the shell
    # writer vs 0644 for the Python one, so a group/other-writable audit
    # ledger. OR-ing 022 into the umask for exactly this open() reproduces
    # Python's request (0666 & ~(umask|022) == 0644 & ~umask) without ever
    # widening a STRICTER ambient umask (e.g. 077 stays 077) — and because
    # the mode is fixed by the umask in effect at open(), there is no
    # create-then-chmod window where the file sits group/other-writable.
    _rf_old_umask=$(umask) || _rf_old_umask=""
    if [ -n "$_rf_old_umask" ]; then
      umask "$(printf '%04o' $(( 0${_rf_old_umask} | 022 )) )" 2>/dev/null
    fi
    { printf '{"timestamp": "%s", "session_id": "%s", "tool": "%s", "hook": "%s", "role": "%s", "decision": "%s", "granularity": "rich"}\n' \
        "$_rf_ts" "${4:-}" "${5:-}" "${1:-}" "${2:-}" "${3:-}" >> "$_rf_path"; } 2>/dev/null
    _rf_write_rc=$?
    [ -n "$_rf_old_umask" ] && umask "$_rf_old_umask" 2>/dev/null
    [ "$_rf_write_rc" = "0" ] || return 0
  else
    { printf '{"timestamp": "%s", "session_id": "%s", "tool": "%s", "hook": "%s", "role": "%s", "decision": "%s", "granularity": "rich"}\n' \
        "$_rf_ts" "${4:-}" "${5:-}" "${1:-}" "${2:-}" "${3:-}" >> "$_rf_path"; } 2>/dev/null || return 0
  fi

  # Day rollover (#1078 sweep, #1238 gzip) on the first write of the UTC day,
  # exactly the edge _atomic_append keys on ("today's file does not exist
  # yet"). Reuses rotate_telemetry rather than reimplementing it in shell;
  # the interpreter cold start lands once per day per directory, never on the
  # per-fire path. Best-effort: no python3 / no _fire_ledger → skipped.
  if [ "$_rf_first" = "1" ] && [ -n "$_rf_lib_dir" ] \
      && command -v python3 >/dev/null 2>&1; then
    python3 -c '
import sys
sys.path.insert(0, sys.argv[1])
try:
    from pathlib import Path
    import _fire_ledger
    _fire_ledger.rotate_telemetry(Path(sys.argv[2]).parent)
except Exception:
    pass
' "$_rf_lib_dir" "$_rf_path" >/dev/null 2>&1 || true
  fi
  return 0
}
