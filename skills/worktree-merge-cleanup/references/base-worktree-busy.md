# When the base worktree has uncommitted edits, or no worktree holds the base

Full procedure for the matching step of [`SKILL.md`](../SKILL.md).

The same remote-only path below applies when `git worktree list` shows no
worktree on the base branch (the main checkout sits on a detached HEAD or
another branch): do not switch a shared checkout just to merge. Run the merge
from a directory outside any repository and pin it to the head you verified:
`gh pr merge <N> --repo <owner>/<repo> --squash --match-head-commit <sha>`
(add `--admin` only when the merge briefing named it under Risk and the
user approved that). The pin makes the merge
refuse when a bot or another session pushed after your pre-merge probe,
instead of merging an unverified head.

A base worktree shared with other sessions can hold their uncommitted edits.
Then `git pull origin <base>` refuses (`Your local changes to the following
files would be overwritten by merge`, or the same for untracked files), and `gh pr merge` run from that worktree
fails its local sync. Those edits are not yours to stash, discard, or commit.

1. Run the merge remotely only: `cd` somewhere outside the repository and call
   `gh pr merge <N> --repo <owner>/<repo> --squash`. Outside a repository gh
   has no local checkout to sync, so it only merges. After it reads
   `MERGED`, remove the head worktree as above, and delete the remote head
   branch when `delete_branch_on_merge` is off (`git push origin --delete
   <branch>`, after its remote tip equals `headRefOid`).
2. Confirm the local base has no commits of its own:
   `git -C <base-worktree> fetch origin --prune`, then
   `git -C <base-worktree> merge-base --is-ancestor HEAD origin/<base>`
   (exit `0`). On any other exit, stop and surface it.
3. Move the ref and bring in the merged files, keeping their edits:
   `git -C <base-worktree> reset --keep origin/<base>`. It updates every file
   the merge changed and leaves every other uncommitted edit where it is; a
   staged edit stays as an unstaged one. It refuses as a whole, changing
   nothing, when a dirty file is one the merge changed or an untracked file
   sits where the merge adds one (`error: Entry '<file>' not uptodate` /
   `Untracked working tree file '<file>' would be overwritten`, exit 128).
   On that refusal, stop: report the file it named and that the checkout
   stays behind `origin/<base>` by N commits. When that checkout is also a
   live load path (an agent's skills or plugin directory), whatever loads
   from it keeps the pre-merge version until the owner of those edits syncs
   it. Never fall back to `--mixed` (it moves the ref but leaves every merged
   file at its pre-merge content, which then shows as an edit reverting the
   merge) or to `--hard` (it discards their edits).
4. Delete the local head branch (`git -C <base-worktree> branch -D <branch>`, after checking its
   tip equals the PR's `headRefOid`) and read `git status` to report which
   edits remain.
