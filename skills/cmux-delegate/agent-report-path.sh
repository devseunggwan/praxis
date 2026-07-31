#!/bin/sh
# Agent-report path derivation for cmux-delegate (issue #903).
#
# The delegated agent writes its completion report and the delegator reads it
# back in Step 7. Both must land on the same file, and the only thing the two
# share is the worktree path — so the key is derived from that, here, once.
# Inlining the derivation in both halves of SKILL.md is how one half gets
# fixed and the completion judgment silently stops finding anything.
#
#   report="$(sh path/to/agent-report-path.sh "$PWD")"
#
# Reports live under <PRAXIS_HOME>/agent-reports/ rather than the worktree
# root: the worktree usually belongs to an unrelated repository, and praxis
# has no business leaving files in it.
#
# Hashing goes through python3 rather than shasum/sha1sum — praxis hooks
# already require python3, and the two checksum binaries are not both present
# across the platforms praxis targets.

set -eu

_ARP_MODE=path
if [ "${1:-}" = "--worktree" ]; then
    _ARP_MODE=worktree
    shift
fi

if [ $# -ne 1 ] || [ -z "$1" ]; then
    echo "usage: agent-report-path.sh [--worktree] <path-inside-worktree>" >&2
    exit 2
fi

_ARP_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../../hooks/_lib/_paths.sh
. "$_ARP_DIR/../../hooks/_lib/_paths.sh"

# Normalize to the worktree ROOT before hashing. The agent writing the report
# and the delegator reading it are rarely standing in the same directory — the
# agent commonly works from a package subdir (`<worktree>/analytics_backend`)
# while the delegator holds the path it passed to `--cwd`. Hashing the raw
# argument would give the two different files, and the reader would report a
# finished task as incomplete. A path outside any repo hashes as given.
_ARP_TARGET="$(git -C "$1" rev-parse --show-toplevel 2>/dev/null || printf '%s' "${1%/}")"

# `--worktree` exposes that same normalization, so the value the agent records
# in the report's `worktree` field and the value the delegator compares it
# against come from one implementation rather than two hand-written commands.
if [ "$_ARP_MODE" = worktree ]; then
    printf '%s\n' "$_ARP_TARGET"
    exit 0
fi

_ARP_KEY="$(python3 -c 'import hashlib,sys; print(hashlib.sha1(sys.argv[1].rstrip("/").encode()).hexdigest())' "$_ARP_TARGET")"

praxis_resolve_writable agent-reports "$_ARP_KEY.json"
