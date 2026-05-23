#!/usr/bin/env bash
# completion-signal-gate.sh — thin shim (praxis #392)
# Logic in .py; shim keeps hooks.json entry stable across refactors.
set +e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$SCRIPT_DIR/completion-signal-gate.py"
command -v python3 >/dev/null 2>&1 || exit 0
[ -f "$PY" ] || exit 0
exec python3 "$PY"
