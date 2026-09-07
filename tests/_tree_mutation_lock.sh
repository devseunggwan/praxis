#!/bin/bash
# _tree_mutation_lock.sh — serialize the suites that edit the working tree.
#
# Several checker suites verify scripts/check-plugin-manifests.py by editing a
# real file, running the checker, and restoring from a backup on EXIT. Each is
# correct alone and unsound in pairs: run A mutates, run B snapshots the
# MUTATED file as its baseline, and from there both restore a tree neither of
# them wrote. The failures that follow name the assertion, never the cause —
# `expected=0 got=1` from a checker that exits 0 when run by hand (#1377).
#
# Sourcing this file takes an exclusive advisory lock for the life of the
# sourcing process. scripts/run-tests.sh runs each suite as its own `bash "$f"`
# process, so the lock is released between suites and a full run never blocks
# on itself; a second run in the same checkout waits instead of interleaving.
#
# Usage, once, before the suite mutates anything:
#   . "$(dirname "$0")/_tree_mutation_lock.sh"   # from tests/
#   . "$(dirname "$0")/../../_tree_mutation_lock.sh"  # from tests/hooks/<role>/
#
# Without flock (macOS ships no flock(1) by default) the lock is skipped with
# a note: serialization is a convenience for developers, never a correctness
# requirement of the suite itself, and a missing tool must not fail a run.

_tree_mutation_lock() {
  local lock_dir lock_file wait_secs
  lock_dir="${TMPDIR:-/tmp}"
  # Keyed on the checkout, so two clones do not block each other.
  lock_file="$lock_dir/praxis-tree-mutation-$(printf '%s' "$PRAXIS_LOCK_ROOT" | cksum | cut -d' ' -f1).lock"
  wait_secs="${PRAXIS_TREE_LOCK_WAIT:-300}"

  if ! command -v flock >/dev/null 2>&1; then
    echo "NOTE: flock unavailable — not serializing tree-mutating suites." >&2
    echo "      Do not run another suite or scripts/run-tests.sh in this" >&2
    echo "      checkout concurrently (#1377)." >&2
    return 0
  fi

  # fd 9 stays open for the life of this shell; the lock releases when it exits.
  exec 9>"$lock_file" || {
    echo "NOTE: cannot open $lock_file — not serializing (#1377)." >&2
    return 0
  }
  if flock -n 9; then
    return 0
  fi
  echo "WAIT: another tree-mutating suite holds the lock in this checkout;" >&2
  echo "      waiting up to ${wait_secs}s (#1377). Set PRAXIS_TREE_LOCK_WAIT" >&2
  echo "      to change, or run the suites serially." >&2
  if flock -w "$wait_secs" 9; then
    return 0
  fi
  echo "FAIL  [tree_mutation_lock] another run held the lock for more than" >&2
  echo "      ${wait_secs}s. This suite edits the working tree and cannot" >&2
  echo "      share it — run it serially (#1377)." >&2
  exit 1
}

PRAXIS_LOCK_ROOT="${PRAXIS_LOCK_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
_tree_mutation_lock
