#!/bin/bash
# PreToolUse(Write/Edit/NotebookEdit) hook — delegates to Python.
#
# Advisory mode (default): emits a stderr warning when a Write targets a nested
# worktree path whose parent directory has not been enumerated this session.
# Set PRAXIS_PATH_PROBE_STRICT=1 to escalate to a hard deny (exit 2).
#
# Fail-safe: if python3 is unavailable, exit 0 (pass-through) rather than
# breaking the Claude Code session.

set +e

if ! command -v python3 >/dev/null 2>&1; then
  exit 0
fi

exec python3 "$(dirname "$0")/path-probe-gate.py"
