#!/usr/bin/env bash
# Case x regression matrix for the own-PR quick pass.
# Each mutant is a full replacement copy of TARGET (write them under MUTANT_DIR as M<n>_<what>.<ext>).
# For every mutant: refuse unless TARGET equals HEAD, copy the mutant over, run TEST_CMD, print the
# failed test names, restore from HEAD. Last line restored_clean=0 proves nothing was left mutated.
#
#   WT=<worktree> TARGET=<path in WT> MUTANT_DIR=<abs dir> \
#   TEST_CMD='pytest -q -rf -p no:cacheprovider <test file>' bash mutation_matrix.sh
#
# Read the output: a test whose failing rows are always a subset of another kept test's rows is
# redundant; a row no test fails is either a missing test or an equivalent mutant (behavior
# unchanged) - say which. Pass MUTANT_DIR as an absolute path: the script cd's into WT first.
set -u
cd "$WT" || exit 2
for mut in "$MUTANT_DIR"/M*; do
  name=$(basename "$mut"); name=${name%.*}
  git diff --quiet HEAD -- "$TARGET" || { echo "refusing: $TARGET differs from HEAD before $name"; exit 3; }
  cp "$mut" "$TARGET"
  failed=$(eval "$TEST_CMD" 2>&1 | grep '^FAILED' | sed -E 's/^FAILED [^:]+::([A-Za-z0-9_]+).*/\1/' | tr '\n' ' ')
  git checkout HEAD -- "$TARGET"
  echo "$name | ${failed:-(none)}"
done
git diff --quiet HEAD -- "$TARGET"; echo restored_clean=$?
