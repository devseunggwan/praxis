#!/bin/sh
# Run/Task ledger entry point for cmux-orchestrate (issue #982).
#
# Thin wrapper over run_ledger.py, mirroring agent-liveness.sh: the shell side
# resolves the interpreter and its own directory, the Python side holds the
# logic. Output is `key='value'` pairs on one line, so a caller reads it with
#
#   eval "$(sh run-ledger.sh summary "$RUN")"
#   [ "$state" = blocked ] && echo "누군가 손을 봐야 합니다"
#
# Exit codes: 2 for a malformed invocation (the caller has a bug and silence
# would hide it), 0 for everything else — a ledger that fails a delegation is
# worse than no ledger, so a missing run, an unwritable state dir, and an absent
# cmux all answer rather than fail.

set -eu

_RL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

exec python3 "$_RL_DIR/run_ledger.py" "$@"
