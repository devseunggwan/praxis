#!/usr/bin/env bash
# tests/test_worktree_merge_cleanup.sh — worktree-merge-cleanup prune guards
#
# Regression test for issue #865:
#   The cleanup sequence prescribes `git worktree prune`, which is a
#   repository-wide operation — it drops the registration of every worktree
#   whose directory is gone, including siblings the merge never touched.
#   The skill must tell the reader to snapshot and dry-run before pruning and
#   to diff the list afterwards, because without the snapshot a later "did I
#   break that?" question is unanswerable.
#
# All assertions are static document checks against SKILL.md, mirroring
# tests/test_critic_pre_lock_probe.sh.
#
# Run:  bash tests/test_worktree_merge_cleanup.sh
# Exit: 0 = all pass; 1 = at least one fail

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILL="$ROOT_DIR/skills/worktree-merge-cleanup/SKILL.md"

# shellcheck source=./_assert_lib.sh
source "$SCRIPT_DIR/_assert_lib.sh"
assert_lib_init "$SKILL"

# ---------------------------------------------------------------------------
# 1. Pre-existing contracts the prune guards must not displace
# ---------------------------------------------------------------------------

assert_present \
  "base-worktree call site documented" \
  "called from the worktree that"

assert_present \
  "squash-ancestry stale HEAD guard documented" \
  "Squash-ancestry stale HEAD guard"

assert_present \
  "no-&&-chain rule documented" \
  "Do NOT collapse the sequence into a single"

assert_present \
  "submodule --force caveat documented" \
  "git worktree remove --force"

# ---------------------------------------------------------------------------
# 2. prune blast radius is stated (issue #865)
# ---------------------------------------------------------------------------

assert_present \
  "prune section heading present" \
  "### \`prune\` is repository-wide — snapshot before running it"

assert_present \
  "prune takes no target" \
  "takes no target"

assert_present \
  "sibling worktrees named as collateral" \
  "including worktrees belonging to"

assert_present \
  "prune never deletes a live worktree" \
  "deletes a live worktree"

assert_present \
  "locked registrations survive prune" \
  "survives even when its directory is gone"

assert_present \
  "snapshot path is repository-scoped, not a fixed /tmp name" \
  "\$(git rev-parse --path-format=absolute --git-common-dir)/wt-before-<N>.txt"

assert_present \
  "snapshot path survives the separate-Bash-call rule" \
  "variable is gone by the next call"

assert_present \
  "snapshot name is unique per concurrent cleanup" \
  "from overwriting each other's"

assert_present \
  "absolute path format is declared mandatory" \
  "\`--path-format=absolute\` is not optional"

assert_present \
  "relative-path failure mode spelled out" \
  "resolve to different files"

assert_present \
  "dry-run is an explicit stop, not a barrier" \
  "is not a confirmation barrier"

assert_present \
  "concurrent-worktree assumption stated" \
  "removed a worktree in this"

assert_present \
  "comparison is filtered to registration lines only" \
  "grep '^worktree '"

assert_present \
  "snapshot itself is written unfiltered" \
  "Write the **unfiltered** porcelain output"

assert_present \
  "prunable/locked markers named as the forensic evidence" \
  "are precisely the forensic evidence"

assert_present \
  "unfiltered diff false-positive explained" \
  "a removal that never happened"

# ---------------------------------------------------------------------------
# 3. Snapshot / dry-run / diff steps are prescribed
# ---------------------------------------------------------------------------

assert_present \
  "snapshot command prescribed" \
  "git worktree list --porcelain"

assert_present \
  "prunable marker explained" \
  "marks the already-missing, unlocked ones \`prunable\`"

assert_present \
  "dry-run command prescribed" \
  "git worktree prune --dry-run -v"

assert_present \
  "dry-run flags verified against the binary" \
  "confirmed via"

assert_present \
  "unexpected dry-run entry escalates to the user" \
  "stop and surface"

assert_present \
  "post-prune diff prescribed" \
  "diff \`git worktree list --porcelain\` against the snapshot"

assert_present \
  "both sides of the comparison use the same format" \
  "Both sides must be filtered"

assert_present \
  "branch-grep alone declared insufficient" \
  "proves *your* branch is gone"

assert_present \
  "empty dry-run implies an empty post-prune diff" \
  "when the dry-run printed nothing, the \`diff\` must print"

assert_present \
  "own worktree already absent from the snapshot" \
  "so it is already absent"

# ---------------------------------------------------------------------------
# 4. Ordering — guards must appear before the prune they guard
# ---------------------------------------------------------------------------

assert_order \
  "snapshot precedes prune in the cleanup block" \
  "> \"\$(git rev-parse --path-format=absolute --git-common-dir)/wt-before-<N>.txt\"" \
  "git worktree prune --dry-run -v"

assert_order \
  "dry-run precedes the post-prune comparison in the cleanup block" \
  "git worktree prune --dry-run -v" \
  "| diff <(grep '^worktree ' \\"

# The bare prune is the destructive step; both guards are worthless unless it
# sits between them.  Anchor on the line that is exactly `git worktree prune`.
assert_order_re \
  "dry-run precedes the real prune in the cleanup block" \
  "^git worktree prune --dry-run -v" \
  "^git worktree prune\$"

assert_order_re \
  "real prune precedes the post-prune comparison in the cleanup block" \
  "^git worktree prune\$" \
  "^ *\| diff <\(grep '\^worktree ' \\\\\$"

# The stop-and-read instruction only works if it sits between the dry-run and
# the destructive prune — inside the block it would be a comment nobody honours.
assert_order_re \
  "stop-and-read gate sits between the dry-run and the real prune" \
  "^git worktree prune --dry-run -v" \
  "STOP here and read the dry-run output"

assert_order_re \
  "stop-and-read gate precedes the real prune" \
  "STOP here and read the dry-run output" \
  "^git worktree prune\$"

# ---------------------------------------------------------------------------
# 5. Checklist item 4 points at the new guards
# ---------------------------------------------------------------------------

assert_present \
  "manual-cleanup checklist references the prune guards" \
  "snapshot and dry-run"

# ---------------------------------------------------------------------------
# 6. Recovery stays a pointer, not a duplicated procedure
# ---------------------------------------------------------------------------

assert_present \
  "recovery pointer retained" \
  "never \`git clone\`"

# ---------------------------------------------------------------------------
# 7. Squash merged-ness oracle states every condition it depends on
#
# The oracle is a delete gate, so a silently weakened one is worse than none:
# dropping --state merged makes it match nothing (every branch reads as
# "no PR found"), and dropping baseRefName lets a branch merged into a sibling
# base pass while cleaning this one.
# ---------------------------------------------------------------------------

assert_present \
  "oracle query filters to merged PRs" \
  "--state merged"

assert_present \
  "oracle explains why --state merged is required" \
  "defaults to \`--state open\`"

assert_present \
  "oracle checks the merge base, not just state" \
  "\`baseRefName\` equals the \`<base>\` you are cleaning up"

assert_present \
  "3-dot diff is named as an invalid oracle" \
  "\`git diff base...branch\`"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

assert_lib_summary
