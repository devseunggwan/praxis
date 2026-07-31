#!/bin/sh
# Host-neutral path resolver for praxis runtime files (issue #903).
#
# Shell equivalent of hooks/_lib/_paths.py. The two must agree: a hook ported
# between languages, and the writer/reader halves of a protocol split across
# them, resolve the same file. Layout and PRAXIS_HOME semantics are documented
# in the Python module.
#
#   . "$(dirname "$0")/../../_lib/_paths.sh"
#   log_dir="$(praxis_home)/scope-confirm"
#   state="$(praxis_resolve_writable cache "retrospect-mix-$SESSION_ID")"
#
# Every function writes its result to stdout and never exits non-zero.

# PRAXIS_HOME override, else ~/.praxis. Not created.
praxis_home() {
    if [ -n "${PRAXIS_HOME:-}" ]; then
        printf '%s\n' "${PRAXIS_HOME%/}"
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
