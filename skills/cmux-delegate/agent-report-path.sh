#!/bin/sh
# Agent-report path derivation for cmux-delegate (issue #997).
#
# The delegated agent writes its completion report and the delegator reads it
# back in Step 7. Both must land on the same file, and the key is the WORKSPACE
# the work ran in. Inlining the derivation in both halves of SKILL.md is how one
# half gets fixed and the completion judgment silently stops finding anything.
#
#   report="$(sh path/to/agent-report-path.sh)"                  # writer
#   report="$(sh path/to/agent-report-path.sh --workspace "$ID")" # delegator
#
# The key used to be a hash of the worktree path, on the stated grounds that the
# worktree was "the only thing the two share". Two consequences, both measured:
# `--distribute` opens N workers in ONE worktree (the default cwd is the
# delegator's own), so N workers shared one report file and per-worker
# completion was unanswerable; and a report from a PREVIOUS delegation in that
# worktree passed both of Step 7's checks, because the only guard is the
# `worktree` field and that field matches too.
#
# The premise was false. A worker knows its own workspace — cmux exports
# CMUX_WORKSPACE_ID into the process — and the delegator already stashes the
# same UUID in a `.ws` file at launch. Both sides know it, and unlike the
# worktree it differs per worker and per delegation.
#
# Reports live under <PRAXIS_HOME>/agent-reports/ rather than the worktree
# root: the worktree usually belongs to an unrelated repository, and praxis
# has no business leaving files in it.

set -eu

_ARP_USAGE='usage: agent-report-path.sh --worktree <path-inside-worktree>
       agent-report-path.sh [--workspace <uuid>]'

_ARP_MODE=path
_ARP_WS=""
case "${1:-}" in
    --worktree)
        _ARP_MODE=worktree
        shift
        ;;
    --workspace)
        [ $# -ge 2 ] || { echo "$_ARP_USAGE" >&2; exit 2; }
        # An empty value is the delegator handing over a corrupt or empty `.ws`
        # file, and it must NOT fall through to CMUX_WORKSPACE_ID: the delegator
        # usually runs inside a workspace of its own, so the fall-through would
        # answer with the delegator's own report path and read one worker's
        # completion as another's.
        [ -n "$2" ] || {
            echo "agent-report-path.sh: --workspace given an empty value" >&2
            exit 2
        }
        _ARP_WS="$2"
        shift 2
        ;;
esac

if [ "$_ARP_MODE" = worktree ]; then
    [ $# -eq 1 ] && [ -n "$1" ] || { echo "$_ARP_USAGE" >&2; exit 2; }
elif [ $# -ne 0 ]; then
    # Path mode takes no positional. An old `... "$PWD"` call site must fail
    # loudly rather than have its argument ignored: it would otherwise keep
    # running while silently addressing a different file than the other half.
    echo "$_ARP_USAGE" >&2
    exit 2
fi

_ARP_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../../hooks/_lib/_paths.sh
. "$_ARP_DIR/../../hooks/_lib/_paths.sh"

# `--worktree` normalizes to the worktree ROOT. The agent writing the report and
# the delegator reading it are rarely standing in the same directory — the agent
# commonly works from a package subdir (`<worktree>/analytics_backend`) while the
# delegator holds the path it passed to `--cwd`. Both sides get that value from
# here rather than from two hand-written commands, so the report's `worktree`
# field and the value compared against it cannot drift. A path outside any repo
# is returned as given.
#
# This mode returns BEFORE the workspace check below, and the order is load
# bearing: the value feeds the report's `worktree` field, which must still be
# obtainable on a host with no cmux. Moving the check above this line breaks it.
if [ "$_ARP_MODE" = worktree ]; then
    printf '%s\n' "$(git -C "$1" rev-parse --show-toplevel 2>/dev/null || printf '%s' "${1%/}")"
    exit 0
fi

# The delegator passes --workspace (read from the `.ws` file it stashed at
# launch); the agent passes nothing and is identified by its own environment.
[ -n "$_ARP_WS" ] || _ARP_WS="${CMUX_WORKSPACE_ID:-}"

if [ -z "$_ARP_WS" ]; then
    # No fallback to a worktree key. The one path where a worker runs outside a
    # workspace is manual execution after workspace creation failed — and there
    # no `.ws` file exists, so no delegator is reading anything. A file written
    # there would be read by nobody now and found by an unrelated delegation
    # later, which is the exact failure this change removes.
    echo "agent-report-path.sh: no workspace — pass --workspace <uuid> or run inside cmux" >&2
    exit 2
fi

# The UUID becomes a filename, and it arrives from `cat`-ing a file. Anything
# outside the UUID alphabet is rejected rather than escaped: it is either a
# traversal attempt or a corrupt `.ws`, and both deserve to stop here.
case "$_ARP_WS" in
    *[!0-9A-Fa-f-]* | "" | *--* | -* | *-)
        echo "agent-report-path.sh: not a workspace UUID: $_ARP_WS" >&2
        exit 2
        ;;
esac

praxis_resolve_writable agent-reports "$_ARP_WS.json"
