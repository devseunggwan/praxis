#!/bin/bash
# PreToolUse(Bash) hook — delegates to the Python implementation.
# Advisory-only by default (exit 0). Strict mode via PRAXIS_MOMENTUM_STRICT=1.
# Fail-safe: if python3 is unavailable, exit 0 rather than break the session.

set +e

if ! command -v python3 >/dev/null 2>&1; then
  exit 0
fi

exec python3 "$(dirname "$0")/momentum-rule-retrieval-gate.py"
