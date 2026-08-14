#!/bin/sh
# Decision-gate entry point for cmux-delegate (issue #984).
#
# The completion report answers "did the worker finish". This answers the
# question that arises before that: the worker has hit a point it must not
# decide alone, and needs the delegator's verdict to continue. The cmux calls
# and the record format live in decision_gate.py; this script owns addressing,
# because the two sides must land on one file without either transcribing a
# derivation — the same reason agent-report-path.sh exists.
#
#   # worker — publishes the question and blocks
#   eval "$(sh path/to/decision-gate.sh ask 'DROP TABLE 를 실행해도 됩니까')"
#   case "$verdict" in approved) ... ;; *) ... ;; esac
#
#   # delegator — at Step 7, where it already stands
#   sh path/to/decision-gate.sh status "$WS_ID"
#   sh path/to/decision-gate.sh resolve "$WS_ID" approve '이 테이블은 임시본입니다'
#
# The worker is identified by its own environment ($CMUX_WORKSPACE_ID); the
# delegator passes the UUID it stashed in the `.ws` file at launch.
#
# Path resolution stays in this shell layer on purpose. `_paths.sh` lives under
# hooks/, and a skill's Python importing hooks/_lib would invert the layering
# (#981); sourcing the shell half here is what agent-report-path.sh already
# does, so the decision file and the report file resolve through one primitive.
#
# Output is `key='value'` pairs on one line, and the exit status is 0 whenever
# the arguments are well-formed. A rejected or timed-out decision is a VERDICT,
# not a failure of this command: callers branch on `$verdict`, and a non-zero
# exit would collapse "the delegator said no" into "the probe broke".

set -eu

_DG_USAGE="usage: decision-gate.sh ask [--timeout <seconds>] <question>
       decision-gate.sh resolve <workspace-uuid> approve|reject [reason]
       decision-gate.sh status <workspace-uuid>"

# Long enough for a delegator who is doing something else — this gate exists
# because the delegator is NOT sitting in a poll loop, so a wait sized for an
# attentive supervisor would time out on the normal case.
_DG_TIMEOUT=600

[ $# -ge 1 ] || { echo "$_DG_USAGE" >&2; exit 2; }
_DG_CMD="$1"
shift

_DG_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../../hooks/_lib/_paths.sh
. "$_DG_DIR/../../hooks/_lib/_paths.sh"

# The UUID becomes a filename and arrives from `cat`-ing a file. Rejected
# rather than escaped: a value outside the alphabet is either a traversal
# attempt or a corrupt `.ws`, and both deserve to stop here.
_dg_resolve() {
    case "$1" in
        *[!0-9A-Fa-f-]* | "" | *--* | -* | *-)
            echo "decision-gate.sh: not a workspace UUID: $1" >&2
            exit 2
            ;;
    esac
    praxis_resolve_writable agent-decisions "$1.json"
}

case "$_DG_CMD" in
    ask)
        if [ "${1:-}" = "--timeout" ]; then
            [ $# -ge 2 ] || { echo "$_DG_USAGE" >&2; exit 2; }
            _DG_TIMEOUT="$2"
            shift 2
        fi
        [ $# -eq 1 ] && [ -n "$1" ] || { echo "$_DG_USAGE" >&2; exit 2; }
        # The worker never names itself. An explicit --workspace here would be
        # a way for one worker to publish a question under another's name, and
        # the environment is the only self-identification cmux offers.
        _DG_WS="${CMUX_WORKSPACE_ID:-}"
        [ -n "$_DG_WS" ] || {
            echo "decision-gate.sh: no workspace — ask runs inside cmux only" >&2
            exit 2
        }
        _DG_FILE="$(_dg_resolve "$_DG_WS")"
        exec python3 "$_DG_DIR/decision_gate.py" ask "$_DG_FILE" "$_DG_WS" "$_DG_TIMEOUT" "$1"
        ;;
    resolve)
        [ $# -ge 2 ] && [ $# -le 3 ] || { echo "$_DG_USAGE" >&2; exit 2; }
        _DG_FILE="$(_dg_resolve "$1")"
        exec python3 "$_DG_DIR/decision_gate.py" resolve "$_DG_FILE" "$@"
        ;;
    status)
        [ $# -eq 1 ] || { echo "$_DG_USAGE" >&2; exit 2; }
        _DG_FILE="$(_dg_resolve "$1")"
        exec python3 "$_DG_DIR/decision_gate.py" status "$_DG_FILE" "$1"
        ;;
    *)
        echo "$_DG_USAGE" >&2
        exit 2
        ;;
esac
