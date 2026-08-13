#!/bin/sh
# Liveness probe entry point for cmux-delegate (issue #981).
#
# Step 7 reads the completion report; this answers the other half — when the
# report is absent, is the worker dead, blocked on a keypress, or still working?
# Classification lives in agent_liveness.py. This script exists for one reason:
# to normalize the argument to the worktree ROOT exactly as agent-report-path.sh
# does, so the liveness answer and the report lookup are keyed on one identity.
# Inlining that derivation in both is how one half gets fixed and the two stop
# agreeing about which worker they are talking about.
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
# agent-report-path.sh also does — and a path is the weak selector, because
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
