# When the user merged out-of-band

Full procedure for the matching step of [`SKILL.md`](../SKILL.md).

A repository bot can also merge the PR once approval and checks are green, so your own `gh pr merge` prints `already merged` (exit 0). Treat that as an out-of-band merge: read `gh pr view <N> --json state,mergedAt,mergedBy,mergeCommit` and report who merged it. The user's approval to merge still covers the cleanup below.

When the user reports "merged" after merging themselves (a `/merge` PR-comment
slash command, the web UI), the cleanup below deletes things: the head
worktree, the local branch, scratch files, the remote head branch, and it may
post an issue close-out. A merge report is not an instruction to delete. Run
the whole sequence without asking only when the user's own instructions
(CLAUDE.md, AGENTS.md, or agent memory) record a standing approval for
post-merge cleanup. Without one, run the read-only checks (steps 1–2), then
ask once for the whole set, naming the worktree path, the branch, and the
close-out action. Either way, stop and report when a precondition below fails.
Confirm before touching anything:

1. `gh pr view <N> --json state,mergedAt,mergeCommit,headRefOid,isCrossRepository`
   — a slash-command merge
   runs as a workflow, so the PR still reads `OPEN` for tens of seconds. If it
   is `OPEN`, find that run (`gh run list --workflow <merge-workflow> --limit 3`)
   and `gh run watch <id> --exit-status`, then re-read the state. Never start
   cleanup on `OPEN`, and never tell the user "that PR is not merged" from a
   single read taken within a minute of their report: wait ~30 s and re-read
   first — when they merged several PRs back to back, the last merge can land
   seconds after your first read, and a false "you didn't merge it" sends them
   chasing a non-problem.
2. Confirm the merge commit is on the base: `git fetch origin --prune` then
   `git merge-base --is-ancestor <mergeCommit> origin/<base>`.
3. Remove the head worktree and local branch as separate calls (checklist item 2),
   deleting the local branch only when its tip (`git rev-parse <branch>`)
   equals `headRefOid`, or is an ancestor of it
   (`git fetch origin <branch>` then
   `git merge-base --is-ancestor <branch> origin/<branch>` exit `0` with
   `origin/<branch>` equal to `headRefOid`). The ancestor case is common when a
   remote review bot or a "merge base into branch" pushed commits after your
   last local commit: the local branch holds nothing the merged PR lacks, so
   deleting it loses nothing — say so in the report.
   A tip that is neither equal to nor an ancestor of `headRefOid` is often
   only an unpushed `git merge origin/<base>` made locally while the PR's
   remote branch got its own base merge (`gh pr update-branch`): check
   `git log --no-merges <headRefOid>..<branch>` is empty and
   `git cherry -v <headRefOid> <branch>` prints nothing, and merging the
   local tip into `headRefOid` changes nothing
   (`git merge-tree --write-tree <headRefOid> <branch>` prints the same tree
   as `git rev-parse <headRefOid>^{tree}`; the first two checks skip merge
   commits, and a conflict-resolution edit lives in one) — then the extra
   commits carry no content of their own; report that evidence and ask once
   rather than deleting under the standing approval.
   A dirty worktree (`git -C <path> status --porcelain` non-empty) or a tip
   with non-merge commits the PR never saw is the stop condition: report it
   and leave it. Before calling a
   dirty file stale, check whether another session is mid-run in that worktree
   (`lsof -d cwd 2>/dev/null | grep -F "<path>"`, `pgrep -fl "<path>"` for a
   script given the path as an argument): a pipeline verifier or mutation test
   stages an old version of a file on purpose and restores it when it ends.
   Never revert or stash it yourself; tell that session the PR merged, ask it
   to restore and leave the worktree clean, and retry cleanup after it exits. `--porcelain`
   alone does not list ignored files, and `git worktree remove` deletes them
   with the directory (a `.env`, local config, a scratch dataset). Also run
   `git -C <path> status --porcelain --ignored` and read its `!!` lines: when
   any of them is not a regenerable dependency or build cache
   (`node_modules/`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `dist/`,
   `build/` and the like), stop and report the list instead of removing.
   Files a local test run wrote into the worktree (an Airflow `airflow.db`, an
   empty `logs/`, a coverage file) count as regenerable only when every file
   in them is newer than the worktree's creation (the worktree's `.git` file):
   `find <artifact> -type f ! -newer <path>/.git` prints nothing — say so in
   the report; anything it prints was there before you and stays a stop
   condition. A `.env` byte-identical to the main checkout's
   (`cmp <path>/.env <main checkout>/.env` exit `0`) is a copy, also
   regenerable. When every `!!` line falls in these classes, remove with
   `--force` under the standing approval without asking — users who run many
   worktrees read a "may I delete the leftovers?" question as the cleanup
   leaving debris behind, and it costs a round trip each time.
   An ignored *source-looking* directory (`providers/<x>/`) that `!!` lists
   whole is often a package whose sources the base merge deleted while its
   `__pycache__/` stayed: `find <dir> -type f -not -path '*/__pycache__/*'`
   printing nothing makes it a cache, not user data. When
   `git worktree remove` succeeded,
   skip `git worktree prune` — there is nothing of yours left to prune, and the
   prune is repository-wide (see below).
4. The remote head branch survives when the repo has
   `delete_branch_on_merge: false` (`gh api repos/<o>/<r> --jq .delete_branch_on_merge`)
   and nobody passed `--delete-branch`. When `isCrossRepository` is `true` the
   head branch lives in a fork, not in `origin`: delete nothing and report the
   fork branch as remaining. Otherwise, under a standing approval or the
   user's answer, run `git -C <repo> push origin --delete <branch>` after
   steps 1–2 passed and the remote tip equals `headRefOid`
   (`git ls-remote origin refs/heads/<branch>`). Never delete any other ref,
   and stop on a tip mismatch. Run it with `-C <repo>` — the session cwd may
   be the worktree you just removed. Re-read `ls-remote` and `git branch
   --list <branch>` right before each delete, even when you read them a
   minute earlier: a sibling cleanup session or repo automation often deletes
   both within a minute of `MERGED` despite `delete_branch_on_merge: false`,
   and the delete then fails with `branch not found` / `remote ref does not
   exist`. An empty read means already gone: skip the delete, find who removed
   it (`gh api repos/<o>/<r>/issues/<N>/events --jq '.[]|select(.event=="head_ref_deleted")'`)
   and report it as done-by-other, not as a failed step.
5. Delete the scratch files you wrote for that PR (body drafts, anchor drafts).
6. Run the repository's issue close-out convention if it has one (a tracker
   overlay skill may define it, e.g. a release comment on the parent issue),
   under the same approval rule as step 4.
7. Verify and report in one status: `git worktree list` no longer shows the
   path, `git branch --list <branch>` is empty, and — when step 4 ran —
   `git ls-remote origin refs/heads/<branch>` is empty.
