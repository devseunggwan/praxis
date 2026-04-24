#!/bin/bash
# PreToolUse(Bash) hook entry — delegates to the Python implementation so the
# heavy lifting (shlex tokenization, category matching) stays in one place.
#
# Fail-safe: if python3 is unavailable, exit 0 (pass) rather than break the
# Claude Code session.

set +e

if ! command -v python3 >/dev/null 2>&1; then
  exit 0
fi

exec python3 "$(dirname "$0")/side-effect-scan.py"
