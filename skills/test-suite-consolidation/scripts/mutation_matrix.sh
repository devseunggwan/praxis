#!/usr/bin/env bash
# Case x regression matrix for the own-PR quick pass.
# Each mutant is a full replacement copy of TARGET (write them under MUTANT_DIR as M<n>_<what>.<ext>).
# For every mutant: refuse unless TARGET is tracked and equals HEAD, copy the mutant over, run TEST_CMD
# under a per-mutant timeout, print the failed pytest node IDs, restore from HEAD. A trap restores
# TARGET on interrupt or early exit. Last line restored_clean=0 proves nothing was left mutated.
#
#   WT=<worktree> TARGET=<path in WT> MUTANT_DIR=<abs dir> [MUTANT_TIMEOUT=300] \
#   TEST_CMD='pytest -q -rf -p no:cacheprovider <test file>' bash mutation_matrix.sh
#
# Row values: failed node IDs; "(none)" = the run completed and nothing failed; "caught (hung)" =
# the run hit MUTANT_TIMEOUT; "INVALID (exit N)" = the run failed without any FAILED line
# (collection/setup error, missing executable) - fix the harness or mutant, it is not a matrix row.
# Read the output: a test whose failing rows are always a subset of another kept test's rows is
# redundant; a row no test fails is either a missing test or an equivalent mutant (behavior
# unchanged) - say which. Pass MUTANT_DIR as an absolute path: the script cd's into WT first.
# TEST_CMD runs in a child bash, so it sees only exported variables.
set -u
# Same-size mutants written within one second reuse each other's stale .pyc (mtime+size check).
export PYTHONDONTWRITEBYTECODE=1
cd "$WT" || exit 2
git cat-file -e "HEAD:$TARGET" 2>/dev/null || { echo "refusing: $TARGET is not tracked at HEAD"; exit 3; }
limit=${MUTANT_TIMEOUT:-300}
if command -v timeout >/dev/null 2>&1; then run_bounded() { timeout "$limit" bash -c "$TEST_CMD"; }
elif command -v gtimeout >/dev/null 2>&1; then run_bounded() { gtimeout "$limit" bash -c "$TEST_CMD"; }
else run_bounded() { perl -e 'alarm shift; exec @ARGV or exit 127' "$limit" bash -c "$TEST_CMD"; }
fi
mutated=0
restore() {
  [ "$mutated" = 1 ] || return 0
  git checkout HEAD -- "$TARGET" || { echo "restore FAILED: $TARGET is still a mutant"; exit 4; }
  mutated=0
}
trap restore EXIT
trap 'exit 130' INT TERM
for mut in "$MUTANT_DIR"/M*; do
  name=$(basename "$mut"); name=${name%.*}
  git diff --quiet HEAD -- "$TARGET" || { echo "refusing: $TARGET differs from HEAD before $name"; exit 3; }
  mutated=1
  cp "$mut" "$TARGET" || { echo "copy FAILED for $name"; exit 4; }
  out=$(run_bounded 2>&1); rc=$?
  restore
  failed=$(printf '%s\n' "$out" | grep '^FAILED ' | sed -E 's/^FAILED //; s/ - .*//' | tr '\n' ' ')
  if [ "$rc" = 124 ] || [ "$rc" = 142 ]; then failed="caught (hung)"
  elif [ "$rc" != 0 ] && [ -z "$failed" ]; then failed="INVALID (exit $rc)"
  fi
  echo "$name | ${failed:-(none)}"
done
git diff --quiet HEAD -- "$TARGET"; echo restored_clean=$?
