#!/usr/bin/env bash
# count-assertion-verify.sh — thin shim (praxis #277)
# Logic in .py; shim keeps hooks.json entry stable across refactors.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$SCRIPT_DIR/count-assertion-verify.py"
command -v python3 >/dev/null 2>&1 || exit 0
[ -f "$PY" ] || exit 0
exec python3 "$PY"
