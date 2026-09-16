#!/usr/bin/env bash
# Prepare and score a perf-leak-review fixture run (#1429).
#
# `scripts/run-tests.sh` never executes a skill, so recall and the clean
# controls cannot be a CI gate — they are measured live and transcribed into the
# PR verification anchor. This script is the deterministic half of that: it
# builds the tree the reviewer reads, and it scores what the reviewer returned.
# Neither subcommand runs the fixture's code.
#
#   prepare <fixture-dir> <results-dir>
#     Copies ONLY <fixture-dir>/repo/ into a fresh temp tree, commits it,
#     applies change.diff, commits that, then write-protects the tree. The
#     fixture's meta.json, its change.diff name, and the fixture directory name
#     are the answer key, so none of them reach the reviewer: the diff is copied
#     to <results-dir>/input.diff under a neutral name and the skill is given
#     that path plus the prepared tree.
#
#   score <results-dir> [--keep]
#     Reads <results-dir>/prepared.json (the ONLY source of paths — the
#     reviewer's own envelope is never trusted for one) and
#     <results-dir>/envelope.json, then checks: the tree is unmodified, the
#     echoed repo_root is the prepared tree, every findings[].file exists, and
#     the findings match the fixture's expected_class. Exits 0 only when all of
#     them hold.
#
# What the porcelain check can and cannot see: writes that leave a trace INSIDE
# the prepared tree. A write outside it, a write-then-delete, executing the
# fixture's code, or a reviewer with Bash lifting the write protection itself
# are all outside this oracle, and the anchor says so.
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
usage: perf-leak-review-eval.sh prepare <fixture-dir> <results-dir>
       perf-leak-review-eval.sh score <results-dir> [--keep]
USAGE
  exit 2
}

# The prepared tree the EXIT trap acts on. A global, because a `local` is out of
# scope by the time the trap fires and the trap would then act on an empty path.
TREE_TO_RESTORE=""

restore_tree_writability() {
  if [ -n "$TREE_TO_RESTORE" ]; then
    chmod -R u+w "$TREE_TO_RESTORE" 2>/dev/null || true
  fi
}

git_commit() {
  # A fixed identity so the script works on a machine with no git config, and
  # -q so the fixture's own file list never lands in the eval output.
  git -C "$1" -c user.name=praxis-eval -c user.email=eval@praxis.local \
    commit -q -m "$2"
}

cmd_prepare() {
  [ "$#" -eq 2 ] || usage
  local fixture results_dir
  fixture="$(cd "$1" && pwd)"
  mkdir -p "$2"
  results_dir="$(cd "$2" && pwd)"

  [ -d "$fixture/repo" ] || { echo "FATAL: $fixture/repo is missing" >&2; exit 1; }
  [ -f "$fixture/change.diff" ] || { echo "FATAL: $fixture/change.diff is missing" >&2; exit 1; }

  local prepared
  prepared="$(mktemp -d)" || { echo "FATAL: mktemp -d failed" >&2; exit 1; }
  # realpath both here and in `score`: on macOS mktemp hands back /var/... while
  # the same directory reads back as /private/var/..., and an unnormalized
  # comparison would fail on the platform difference rather than on the answer.
  prepared="$(realpath "$prepared")"

  cp -R "$fixture/repo/." "$prepared/"
  git -C "$prepared" init -q
  git -C "$prepared" add -A
  git_commit "$prepared" "chore: fixture base"
  local base_sha
  base_sha="$(git -C "$prepared" rev-parse HEAD)"

  # set -e carries an unappliable patch out of here; the porcelain check below
  # is what catches a patch that applied and changed nothing.
  git -C "$prepared" apply "$fixture/change.diff"
  if [ -z "$(git -C "$prepared" status --porcelain)" ]; then
    echo "FATAL: $fixture/change.diff applied nothing — the fixture is inert" >&2
    exit 1
  fi
  git -C "$prepared" add -A
  git_commit "$prepared" "chore: fixture change"
  local change_sha
  change_sha="$(git -C "$prepared" rev-parse HEAD)"

  # Write protection is the pre-check; `score`'s porcelain read is the
  # after-the-fact one. Neither reaches a reviewer that can run chmod itself.
  chmod -R a-w "$prepared"

  cp "$fixture/change.diff" "$results_dir/input.diff"
  cat > "$results_dir/prepared.json" <<JSON
{
  "fixture": "$fixture",
  "prepared_path": "$prepared",
  "diff_path": "$results_dir/input.diff",
  "base_sha": "$base_sha",
  "change_sha": "$change_sha"
}
JSON

  echo "prepared: $prepared"
  echo "diff:     $results_dir/input.diff"
  echo "pass to the skill: --diff $results_dir/input.diff --repo $prepared"
}

cmd_score() {
  [ "$#" -ge 1 ] || usage
  local results_dir keep=0
  results_dir="$(cd "$1" && pwd)"
  shift
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --keep) keep=1 ;;
      *) usage ;;
    esac
    shift
  done

  local prepared_json="$results_dir/prepared.json"
  [ -f "$prepared_json" ] || {
    echo "FATAL: $prepared_json is missing — run prepare first" >&2
    exit 1
  }
  local envelope_json="$results_dir/envelope.json"
  [ -f "$envelope_json" ] || {
    echo "FATAL: $envelope_json is missing — write the reviewer envelope there" >&2
    exit 1
  }

  local prepared
  prepared="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["prepared_path"])' "$prepared_json")"
  # Restore write permission on every exit path, so a failed run never leaves an
  # undeletable tree behind.
  TREE_TO_RESTORE="$prepared"
  trap restore_tree_writability EXIT

  local rc=0
  local porcelain
  porcelain="$(git -C "$prepared" status --porcelain)"
  if [ -n "$porcelain" ]; then
    echo "FAIL tree-unmodified: the prepared tree changed during the review"
    printf '%s\n' "$porcelain"
    rc=1
  else
    echo "PASS tree-unmodified: git status --porcelain empty"
  fi

  if python3 "$(dirname "$0")/perf_leak_review_score.py" \
    --prepared-json "$prepared_json" --envelope "$envelope_json"; then
    :
  else
    rc=1
  fi

  if [ "$keep" -eq 1 ]; then
    echo "kept: $prepared"
  else
    chmod -R u+w "$prepared"
    rm -rf "$prepared"
  fi
  return "$rc"
}

[ "$#" -ge 1 ] || usage
subcommand="$1"
shift
case "$subcommand" in
  prepare) cmd_prepare "$@" ;;
  score) cmd_score "$@" ;;
  *) usage ;;
esac
