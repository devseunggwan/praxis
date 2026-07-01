#!/bin/bash
# cli-scripts.sh — Canonical list of praxis public CLI scripts.
#
# Single source of truth shared by:
#   - scripts/install.sh         (symlink each into ~/.local/bin)
#   - scripts/verify-symlinks.sh (drift check those same symlinks)
#
# Add a new entry here when a skills/ directory ships an executable CLI
# (whether or not it is also a skill — e.g. bypass-review is CLI-only, no
# SKILL.md) — both consumers pick it up automatically, so the two lists can
# never drift apart again.
#
# This file is meant to be *sourced*, not executed. It defines the
# CLI_SCRIPTS array (repo-relative paths) and nothing else.

# shellcheck disable=SC2034  # CLI_SCRIPTS is consumed by the sourcing script
CLI_SCRIPTS=(
  "skills/recover-sessions/claude-recover"
  "skills/recover-sessions/claude-recover-scan"
  "skills/cmux-resume-sessions/cmux-resume-sessions"
  "skills/cmux-save-sessions/cmux-save-sessions"
  "skills/cmux-recover-sessions/cmux-recover-sessions"
  "skills/cmux-session-manager/cmux-session-status"
  "skills/cmux-session-manager/cmux-session-cleanup"
  "skills/bypass-review/bypass-review"
)
