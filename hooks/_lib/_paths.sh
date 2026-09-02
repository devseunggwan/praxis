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
