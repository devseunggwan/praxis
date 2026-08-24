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

# Public CLI scripts — single source of truth, shared with verify-symlinks.sh.
# Defines the CLI_SCRIPTS array; add a new entry in scripts/cli-scripts.sh.
# shellcheck source=scripts/cli-scripts.sh
source "$(dirname "${BASH_SOURCE[0]}")/cli-scripts.sh"

mkdir -p "$BIN_DIR"

linked=0
already=0
missing=0
refused=0
overwritten=0
backup_seq=0  # monotonic suffix so same-second forced backups never collide (#1097)

# Small helper: resolve realpath of a path (or empty string on failure).
# We shell out to python3 because BSD and GNU realpath accept different
# flag sets and we don't want to hard-code one.
resolve_realpath() {
  /usr/bin/env python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$1" 2>/dev/null || true
}

src_real_cache() {
  local p="$1"
  resolve_realpath "$p"
}

for script in "${CLI_SCRIPTS[@]}"; do
  src="$REPO_ROOT/$script"
  name=$(basename "$script")
  dst="$BIN_DIR/$name"

  if [[ ! -f "$src" ]]; then
    echo "MISSING  $name (no source at $src)"
    missing=$((missing + 1))
    continue
  fi

  # Realpath-based equality: a symlink that resolves to the same
  # canonical source (relative path, intermediate symlink, etc.) is
  # already correct. This keeps re-runs idempotent even when an earlier
  # install wrote the link via a slightly different path spelling.
  if [[ -L "$dst" ]]; then
    src_real=$(resolve_realpath "$src")
    dst_real=$(resolve_realpath "$dst")
    if [[ -n "$src_real" && -n "$dst_real" && "$src_real" == "$dst_real" ]]; then
      echo "OK       $name"
      already=$((already + 1))
      continue
    fi
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

    # Log a REBIND message when the existing symlink points at a different
    # clone (stale worktree path, legacy clone name, etc.) so operators can
    # confirm which path is being repointed. readlink works even for dangling
    # links so this is safe regardless of whether the old target still exists.
    old_target="(not a symlink)"
    if [[ -L "$dst" ]]; then
      old_target=$(readlink "$dst")
      echo "REBIND   $name: $old_target -> $src"
    fi

    # A directory destination cannot be atomically replaced by the
    # temp-symlink + rename(2) swap below: `mv -f "$tmp" "$dst"` would move
    # the staged link INTO the directory and falsely report LINK success.
    # Refuse loudly — the surrounding code is a symlink installer, not a
    # tree remover, so blowing away a directory here is not its intent
    # (#1097).
    if [[ -d "$dst" && ! -L "$dst" ]]; then
      echo "ERROR    $name ($dst is a directory; refusing to replace it — remove it by hand)"
      refused=$((refused + 1))
      continue
    fi

    # Backup FIRST so we never lose the old binary even if the new
    # symlink write fails later. For symlinks we preserve the link
    # itself (not its target) so recovery keeps its original semantics.
    # The name embeds $$ and a per-run counter as well as the epoch so two
    # forced runs landing in the same second (or two conflicts in one run)
    # cannot collide on the backup path (#1097).
    backup="$dst.bak.$(date +%s).$$.$backup_seq"
    backup_seq=$((backup_seq + 1))
    # A failed backup must not abort the whole loop under `set -e`; guard it
    # like the stage/rename steps below and treat it as a per-script error.
    if [[ -L "$dst" ]]; then
      if ! ln -s "$(readlink "$dst")" "$backup"; then
        echo "ERROR    $name failed to write backup $backup; dst unchanged"
        exit 1
      fi
    else
      if ! cp -a "$dst" "$backup"; then
        echo "ERROR    $name failed to write backup $backup; dst unchanged"
        exit 1
      fi
    fi

    # Stage the new link in a sibling temp path. Installing via a
    # temp path + rename(2) means the swap is atomic: either the old
    # dst is still there or the new one is, never a gap.
    tmp="$dst.new.$$"
    if ! ln -s "$src" "$tmp"; then
      echo "ERROR    $name failed to stage new symlink; dst unchanged"
      rm -f "$backup"
      exit 1
    fi
    if ! mv -f "$tmp" "$dst"; then
      echo "ERROR    $name atomic rename failed; dst unchanged"
      rm -f "$tmp" "$backup"
      exit 1
    fi

    echo "BACKUP   $name -> $backup"
    echo "LINK     $name -> $src"
    overwritten=$((overwritten + 1))
    linked=$((linked + 1))
    continue
  fi

  # Fresh install: create the symlink via the same atomic pattern so
  # failure modes stay consistent.
  tmp="$dst.new.$$"
  if ! ln -s "$src" "$tmp"; then
    echo "ERROR    $name failed to create symlink"
    exit 1
  fi
  if ! mv -f "$tmp" "$dst"; then
    echo "ERROR    $name atomic rename failed"
    rm -f "$tmp"
    exit 1
  fi
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
