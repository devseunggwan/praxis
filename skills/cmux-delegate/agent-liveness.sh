#!/bin/sh
# Liveness probe entry point for cmux-delegate (issue #981).
#
# Step 7 reads the completion report; this answers the other half — when the
# report is absent, is the worker dead, blocked on a keypress, or still working?
# Classification lives in agent_liveness.py. This script exists to normalize a
# path argument to the worktree ROOT — the weak selector, kept only as a
# fallback for callers that have no workspace.
#
# It used to say the normalization matched agent-report-path.sh "so the two are
# keyed on one identity". Since #997 the report is keyed on the WORKSPACE, and
# so is this probe when given a ref or a UUID: the shared identity is real, but
# it is the workspace, and the path branch below is where that identity is
# missing rather than where it is established.
#
# No state is kept. An earlier draft cached an event cursor here; that was a
# bug rather than an optimization — see the module docstring's WINDOW DIRECTION
# note — and removing it took the only writable path with it.
#
#   eval "$(sh path/to/agent-liveness.sh "$PWD")"
#   echo "$state"        # working | idle | waiting-input | crash | unknown
#
# Output is `key=value` pairs on one line. Always exits 0 when the arguments
# are well-formed: an unreachable cmux yields state=unknown, never a failure.
# A probe that exits non-zero on a host without cmux would turn praxis's
# host-neutral posture (ARCHITECTURE.md) into a hard cmux dependency.

set -eu

if [ $# -ne 1 ] || [ -z "$1" ]; then
    echo "usage: agent-liveness.sh <workspace-ref|workspace-uuid|path>" >&2
    exit 2
fi

_AL_DIR="$(cd "$(dirname "$0")" && pwd)"

# A `workspace:N` ref or a UUID names ONE workspace and is passed through
# untouched. Only a path needs the worktree-root normalization that
# `agent-report-path.sh --worktree` also does — and a path is the weak selector, because
# delegation defaults to the delegator's own directory and so routinely names
# several workspaces at once. Prefer the ref that Step 5 captured.
case "$1" in
    workspace:*)
        _AL_TARGET="$1"
        ;;
    *)
        _AL_TARGET="$(git -C "$1" rev-parse --show-toplevel 2>/dev/null || printf '%s' "${1%/}")"
        ;;
esac

exec python3 "$_AL_DIR/agent_liveness.py" "$_AL_TARGET"
