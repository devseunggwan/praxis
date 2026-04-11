#!/bin/bash
# verify-symlinks.sh — Confirm $HOME/.local/bin symlinks point at *this* clone
#
# Uses realpath-level comparison (not just readlink text) and rejects
# dangling links / non-executable targets so a drift that shipped a
# broken binary can't silently report OK. Exits non-zero on any drift,
# so it can be wired into CI / SessionStart hooks that catch the "patch
# landed in the wrong clone" failure mode.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${PRAXIS_BIN_DIR:-$HOME/.local/bin}"

CLI_SCRIPTS=(
  "skills/recover-sessions/claude-recover"
  "skills/recover-sessions/claude-recover-scan"
  "skills/cmux-resume-sessions/cmux-resume-sessions"
  "skills/cmux-save-sessions/cmux-save-sessions"
  "skills/cmux-recover-sessions/cmux-recover-sessions"
  "skills/cmux-session-manager/cmux-session-status"
  "skills/cmux-session-manager/cmux-session-cleanup"
)

drift=0
for script in "${CLI_SCRIPTS[@]}"; do
  src="$REPO_ROOT/$script"
  name=$(basename "$script")
  dst="$BIN_DIR/$name"

  # Source sanity — if this clone is missing the script there is nothing
  # we can match against. Surface the problem instead of silently skipping.
  if [[ ! -f "$src" ]]; then
    echo "NO-SOURCE  $name (source $src does not exist in this clone)"
    drift=$((drift + 1))
    continue
  fi
  src_real=$(/usr/bin/env python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$src" 2>/dev/null || echo "")

  if [[ ! -L "$dst" && ! -e "$dst" ]]; then
    echo "MISSING    $name (expected at $dst)"
    drift=$((drift + 1))
    continue
  fi

  if [[ ! -L "$dst" ]]; then
    echo "NOT-A-LINK $name ($dst is a regular file)"
    drift=$((drift + 1))
    continue
  fi

  # Dangling? (-L true but -e false means the link exists but the target
  # it points at does not resolve to anything on disk.)
  if [[ ! -e "$dst" ]]; then
    echo "DANGLING   $name -> $(readlink "$dst") (target does not exist)"
    drift=$((drift + 1))
    continue
  fi

  dst_real=$(/usr/bin/env python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$dst" 2>/dev/null || echo "")

  if [[ -z "$src_real" || -z "$dst_real" ]]; then
    echo "UNRESOLVED $name (could not resolve realpath)"
    drift=$((drift + 1))
    continue
  fi

  if [[ "$dst_real" != "$src_real" ]]; then
    echo "DRIFT      $name -> $(readlink "$dst")"
    echo "                       resolves to $dst_real"
    echo "                       expected     $src_real"
    drift=$((drift + 1))
    continue
  fi

  if [[ ! -x "$dst" ]]; then
    echo "NOT-EXEC   $name (target exists but is not executable)"
    drift=$((drift + 1))
    continue
  fi

  echo "OK         $name"
done

echo ""
if [[ $drift -gt 0 ]]; then
  echo "FAIL: $drift symlink(s) drifted. Run scripts/install.sh (optionally with --force) to fix."
  exit 1
fi

echo "All symlinks point at this clone."
echo "Repo: $REPO_ROOT"
