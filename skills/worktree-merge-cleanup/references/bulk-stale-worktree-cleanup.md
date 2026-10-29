# Bulk stale-worktree cleanup ("clean up every stale worktree")

Use when the user asks to remove worktrees across many repos at once, not right after one merge. The single-PR sequence in SKILL.md still governs each removal; this file adds the inventory, the classification order, and the oracles that bulk work gets wrong.

## 1. Inventory (read-only, one scratch script)

- Main checkouts = directories under the projects root whose `.git` is a **directory** (depth ≤ 3; skip `node_modules`, `.venv`). For each: `git -C <main> worktree list --porcelain`, saved unfiltered to `wt-before-<repo>.txt` (the prune snapshot). Keep each worktree's `HEAD` from this snapshot — it is the recovery point if a removal turns out wrong.
- Per linked worktree: branch or detached, `status --porcelain --ignored`, `rev-list --count HEAD --not --remotes`, last commit date, `.git` file mtime (= creation time), `.gitmodules` present.
- PR per branch: `gh pr list --repo <slug> --head <branch> --state all --json number,state,headRefOid,mergedAt,baseRefName`. Detached review checkouts (`*-pr<N>-review`, `*-review-<N>`, `*-pr-<N>`) get their PR from the directory name via `gh pr view <N>`.
- Issue state for every issue number in a branch name or path (the repo's branch convention says where the number sits); live-session cwds and owned issues from the session orchestrator's ownership record, when one exists.

## 2. Classify, first match wins

| Order | Condition | Verdict |
| --- | --- | --- |
| 1 | directory missing / `prunable` | prune (after dry-run) |
| 2 | path is a live session's cwd (or contains it), **or sits under a live session's scratch dir** (the agent host's per-session scratch directory) | keep |
| 3 | issue owned by a live domain session | keep |
| 4 | any OPEN PR for the branch | keep |
| 5 | linked issue OPEN and no finished PR | keep |
| 6 | no PR and no linked issue (base snapshots, scratch/agent worktrees, other tools' worktrees) | report, do not remove unasked |
| 7 | worktree on a base branch (`dev`/`prod`/`main`) | hold — `--head dev` returns every release PR, so the PR oracle is meaningless |
| 8 | PR finished but linked issue still OPEN | hold — the issue's next PR may build on it |
| 9 | finished PR or closed issue, and the gates below pass | remove |

Row 2 is not a process check: a session's scratch worktree is in use between its runs, so `lsof -d cwd` showing no process (its script just finished) does not free it. Decide it by the owning session being live.

Gates for 9 (any failure → hold, with the reason in the report):

- **Content preserved**: unpushed count is 0, **or** HEAD equals some PR's `headRefOid`. Do not trust the unpushed count alone: after a squash merge plus remote-branch deletion every branch commit is "not on any remote", yet `refs/pull/<N>/head` still holds it. HEAD ≠ every `headRefOid` with unpushed > 0 is real local work.
- **No tracked changes** (`status --porcelain` lines not starting `??`).
- **Every untracked/ignored file is a cache or a local-run artifact.** Caches: `node_modules/`, `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `dist/`, `build/`, `.next/`, `coverage/` and similar. Local-run artifacts count only when their mtime is ≥ the worktree's `.git` mtime: `logs/`, `*.log`, `*.log.<n>` (sqlalchemy/logger/otel rotations), `airflow.db`, `airflow.cfg`, `webserver_config.py`, `unittests.cfg/db`, `.husky/_/`, `.hypothesis/`, `__screenshots__/`, `.vitest-attachments/`, `playwright-report/`, `cdktf/.gen/`, `cdktf/cdktf.out/`, `cdk/cdk.out/`, generated `cdktf/crd/`; an ignored directory holding only `.pyc` also counts. Apply the same rule to `??` entries, not just `!!`. Always blockers: `.env`, `.claude/settings.local.json`, any token/credential file, agent state dirs (`.omc/`, `.omx/`), `e2e/.auth/`, `local/`, telemetry dirs — report them by name.

The user's "clean up all stale worktrees" covers the remove set built this way; it does not cover the report-only rows (6) or the holds. List those grouped by reason instead of deleting them.

## 3. Execute

1. Smoke-test one item in the foreground, then verify: directory gone, absent from `git worktree list`, `git branch --list <branch>` empty.
2. Run the rest as a background script (`terminal(background=true, notify=true)`), one item at a time, failures isolated (log and continue):
   - re-read HEAD and skip if it moved since classification; skip if tracked changes appeared;
   - `git -C <main> worktree remove --force <path>` — `--force` is needed for the regenerable untracked files and for submodule worktrees, and is safe only because step 2 already proved nothing else is there;
   - `git -C <main> branch -D <branch>` only when the branch tip equals the HEAD you just checked; never touch base branches or remote refs in this pass;
   - append each path to a `done.json` checkpoint and a timestamped log, so a rerun resumes and a status request is answered from the log.
3. Prune per repo only when its `git worktree prune --dry-run -v` lists exactly the expected missing registrations; run each prune as its own call.
4. Report: removed count (from the log), branches deleted vs kept, prunes, then holds and report-only rows grouped by reason, with the paths.

## 4. When the user then says "remove those too" (holds and report-only rows)

Remove them, but keep every commit and every uncommitted edit recoverable inside git (no backup files):

- **Uncommitted edits** → `git -C <wt> stash push --include-untracked -m "wt-cleanup <date>: <path>"` (lands in the repo's shared stash). Prove the stash was created: compare `git stash list` length (or the `stash@{0}` message) before and after. `stash push` exits 0 with `No local changes to save` when the only change is a **submodule gitlink**, and reading `stash@{0}` then reports someone else's entry. For gitlink-only dirt, record `git -C <wt>/<submodule> rev-parse HEAD` and confirm it is on the submodule's remote (`git -C <wt>/<submodule> branch -r --contains <sha>`) before removing — the worktree's submodule git dir is deleted with the worktree.
- A stash that fails (`Entry '<file>' not uptodate. Cannot merge.` — conflicted index) means leave that worktree and report it; never fall back to `--force` without the stash.
- **Commits not on a remote and not at a PR head** → keep the local branch; for a detached HEAD create `wt-archive/<dir-name>` at HEAD first.
- Delete the branch only when its commits are on a remote or at a PR head.
- Ignored blockers (`.env`, token files, agent state) do not go into a stash — say in the plan that they will be deleted.
- Re-apply row 2 at execution time (live session scratch dirs stay).
- Report per bucket: branch deleted / stash `<sha>` / branch kept / new `wt-archive/*`, and list the stash names so the user can find them.

**Undo a wrong removal**: `git -C <main> worktree add --detach <path> <HEAD from the inventory snapshot>` (or the branch name if it was kept), then `git -C <path> stash apply <its stash>`. Ignored files are not recoverable; say so.
