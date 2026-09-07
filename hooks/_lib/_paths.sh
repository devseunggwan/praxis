#!/bin/sh
# Host-neutral path resolver for praxis runtime files (issue #903).
#
# Shell equivalent of hooks/_lib/_paths.py. The two must agree: a hook ported
# between languages, and the writer/reader halves of a protocol split across
# them, resolve the same file. Layout and PRAXIS_HOME semantics are documented
# in the Python module.
#
#   . "$(dirname "$0")/../../_lib/_paths.sh"
#   log="$(praxis_resolve_writable logs stop-triggered.log)"
#   state="$(praxis_resolve_writable cache "retrospect-mix-$SESSION_ID")"
#
# Every function writes its result to stdout and never exits non-zero.

# PRAXIS_HOME override, else ~/.praxis. Not created.
#
# The tilde is expanded explicitly: an unquoted PRAXIS_HOME=~/praxis is expanded
# by the assigning shell, but a quoted or exported-from-config one arrives here
# literally, while _paths.py runs os.path.expanduser on the same value. Left
# alone, shell and Python halves of one protocol resolve different directories.
praxis_home() {
    if [ -n "${PRAXIS_HOME:-}" ]; then
        _ph_raw="${PRAXIS_HOME%/}"
        # Held in a variable so the tilde stays a literal to match against
        # rather than something the shell might try to expand here.
        _ph_tilde='~'
        case "$_ph_raw" in
            "$_ph_tilde") _ph_raw="$HOME" ;;
            "$_ph_tilde"/*) _ph_raw="$HOME/${_ph_raw#"$_ph_tilde"/}" ;;
        esac
        printf '%s\n' "$_ph_raw"
        unset _ph_raw _ph_tilde
    else
        printf '%s\n' "$HOME/.praxis"
    fi
}

# Durable, cross-session state root. An explicit PRAXIS_STATE_DIR always wins,
# for back-compat with the pre-#527 convention. Not created.
praxis_state_dir() {
    if [ -n "${PRAXIS_STATE_DIR:-}" ]; then
        printf '%s\n' "${PRAXIS_STATE_DIR%/}"
    else
        printf '%s\n' "$(praxis_home)/state"
    fi
}

# Volatile, regenerable, session-scoped cache root. Not created.
praxis_cache_dir() {
    printf '%s\n' "$(praxis_home)/cache"
}

# Diagnostics root (hook errors, bypass telemetry). Not created.
praxis_logs_dir() {
    printf '%s\n' "$(praxis_home)/logs"
}

# Feature-spec store. Holds documents a person writes, not files praxis
# generates, and sits outside any checkout so the convention need not be
# adopted per repository — see docs/spec-store.md. Not created.
praxis_specs_dir() {
    printf '%s\n' "$(praxis_home)/docs/specs"
}

# Path under <praxis_home>/$1/$2, creating the directory. Falls back to
# ${TMPDIR}/praxis-$2 when the home dir cannot be written, so a hook on a
# read-only or unwritable HOME degrades instead of failing.
praxis_resolve_writable() {
    _prw_subdir="$1"
    _prw_name="$2"
    _prw_dir="$(praxis_home)/$_prw_subdir"

    if mkdir -p "$_prw_dir" 2>/dev/null && [ -w "$_prw_dir" ]; then
        printf '%s\n' "$_prw_dir/$_prw_name"
    else
        _prw_tmp="${TMPDIR:-/tmp}"
        printf '%s\n' "${_prw_tmp%/}/praxis-$_prw_name"
    fi

    unset _prw_subdir _prw_name _prw_dir _prw_tmp
}

# praxis_rotate_log <path> [max_bytes]
#
# Size-bounded retention for an append-only log (issue #1282). When <path> is
# larger than max_bytes (default 1 MiB) it is renamed to <path>.1, replacing
# the previous predecessor, so a writer that fires on every Stop cannot grow
# the file without bound — ~/.praxis/logs has no TTL sweep, unlike cache/ and
# telemetry/. Call it right before the `>>`. Never exits non-zero and prints
# nothing: a failed rotation costs one oversized log, never a hook decision.
praxis_rotate_log() {
    _prl_path="$1"
    _prl_max="${2:-1048576}"
    # A non-numeric cap would make the -gt test print an error to stderr —
    # surfaced to the user on a Stop block — so it falls back to the default.
    case "$_prl_max" in
        ''|*[!0-9]*) _prl_max=1048576 ;;
    esac
    if [ -f "$_prl_path" ]; then
        _prl_size=$(wc -c < "$_prl_path" 2>/dev/null | tr -d '[:space:]')
        case "$_prl_size" in
            ''|*[!0-9]*) _prl_size=0 ;;
        esac
        if [ "$_prl_size" -gt "$_prl_max" ]; then
            mv -f -- "$_prl_path" "$_prl_path.1" 2>/dev/null || true
        fi
    fi
    unset _prl_path _prl_max _prl_size
    return 0
}
