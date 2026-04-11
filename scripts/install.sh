#!/bin/bash
# install.sh — Symlink praxis CLI tools into ~/.local/bin
#
# Default mode is fail-safe: if a destination already exists and is not
# already the correct symlink to this clone, the script refuses to touch
# it and exits non-zero. Pass --force to overwrite such destinations; a
# timestamped .bak file is preserved so the previous target can be
# recovered.
#
# Exit codes:
#   0   every tracked CLI is pointed at this clone
#   1   at least one missing source or refused/overwritten conflict
#   2   bad arguments

set -euo pipefail

FORCE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=true; shift ;;
    -h|--help)
      cat <<'HELP'
Usage: scripts/install.sh [--force]

Symlink praxis CLI tools into $HOME/.local/bin (or $PRAXIS_BIN_DIR).

  --force   Overwrite an existing file/symlink when it does not already
            point at this clone. The previous target is saved as
            <path>.bak.<epoch> before the new symlink is written.

Environment:
  PRAXIS_BIN_DIR   Override the install directory (default: ~/.local/bin)
HELP
      exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${PRAXIS_BIN_DIR:-$HOME/.local/bin}"

# Public CLI scripts. Add new entries here when a skill ships an executable.
CLI_SCRIPTS=(
  "skills/recover-sessions/claude-recover"
  "skills/recover-sessions/claude-recover-scan"
  "skills/cmux-resume-sessions/cmux-resume-sessions"
  "skills/cmux-save-sessions/cmux-save-sessions"
  "skills/cmux-recover-sessions/cmux-recover-sessions"
  "skills/cmux-session-manager/cmux-session-status"
  "skills/cmux-session-manager/cmux-session-cleanup"
)

mkdir -p "$BIN_DIR"

linked=0
already=0
missing=0
refused=0
overwritten=0

for script in "${CLI_SCRIPTS[@]}"; do
  src="$REPO_ROOT/$script"
  name=$(basename "$script")
  dst="$BIN_DIR/$name"

  if [[ ! -f "$src" ]]; then
    echo "MISSING  $name (no source at $src)"
    missing=$((missing + 1))
    continue
  fi

  if [[ -L "$dst" ]] && [[ "$(readlink "$dst")" == "$src" ]]; then
    echo "OK       $name"
    already=$((already + 1))
    continue
  fi

  # Destination exists but is not the canonical symlink.
  if [[ -e "$dst" || -L "$dst" ]]; then
    if ! $FORCE; then
      current="(unknown)"
      if [[ -L "$dst" ]]; then
        current=$(readlink "$dst")
      fi
      echo "REFUSE   $name ($dst already exists; currently -> $current)"
      echo "         re-run with --force to overwrite (a .bak backup will be written)"
      refused=$((refused + 1))
      continue
    fi
    backup="$dst.bak.$(date +%s)"
    mv "$dst" "$backup"
    echo "BACKUP   $name -> $backup"
    overwritten=$((overwritten + 1))
  fi

  ln -s "$src" "$dst"
  echo "LINK     $name -> $src"
  linked=$((linked + 1))
done

echo ""
echo "Done. linked=$linked already=$already missing=$missing refused=$refused overwritten=$overwritten"
echo "Repo:  $REPO_ROOT"
echo "Bin:   $BIN_DIR"

if [[ $missing -gt 0 || $refused -gt 0 ]]; then
  exit 1
fi
