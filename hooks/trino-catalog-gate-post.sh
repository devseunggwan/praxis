#!/usr/bin/env bash
# trino-catalog-gate-post.sh — PostToolUse shim (praxis #336)
# Deletes the session marker when SHOW CATALOGS returns an error so the
# optimistic PreToolUse marker does not persist after a failed enumeration.
# Logic in trino-catalog-gate.py (run_post subcommand).
set +e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$SCRIPT_DIR/trino-catalog-gate.py"
command -v python3 >/dev/null 2>&1 || exit 0
[ -f "$PY" ] || exit 0
exec python3 "$PY" post
