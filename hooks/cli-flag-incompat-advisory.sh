#!/bin/bash
# PreToolUse(Bash) hook — delegates to the Python implementation.
# Advisory-only: writes to stderr, exits 0. Never blocks tool execution.

set +e

if ! command -v python3 >/dev/null 2>&1; then
  exit 0
fi

exec python3 "$(dirname "$0")/cli-flag-incompat-advisory.py"
