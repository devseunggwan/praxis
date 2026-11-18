---
name: stale-pr-revival
description: >
  Bring an open, stalled pull request back to a mergeable state without
  rewriting its history — read every review surface, merge the base in,
  integrate what landed meanwhile, prove the core change red/green, and update
  the PR's own body and comments. Covers bot version-bump PRs and base
  retargeting; the merge itself stays behind the merge briefing.
when_to_use: >
  Triggers on "revive stale PR", "stalled PR", "dependabot PR", "renovate PR",
  "retarget PR", "묵은 PR 살리기", and on a status check of an open PR
  ("PR 현황 확인", a bare PR link) that turns out `CONFLICTING`/`DIRTY` or
  many commits behind its base — load it before the first merge command. Do NOT activate on "merge cleanup" or
  "post-merge cleanup" (that is `worktree-merge-cleanup`) or on reviewing
  someone else's PR without pushing to it.
verified-against-runtime: true
runtime-verified-at: 2026-10-16
runtime-verified-note: "gh 2.102.0 — against a public repo's merged dependabot PR: `pr view --json body,baseRefName,headRefName,isCrossRepository` and `isCrossRepository,headRepositoryOwner,headRepository,headRefName,maintainerCanModify`, `api repos/<r> --jq .permissions.push` (true on a writable repo, false on one without access) and `headRefOid,mergeable,mergeStateStatus,reviewDecision`, `pr diff` (one -/+ `uses:` pair), `pr checks`, `run list --commit`, paginated reviews and the `reviewThreads` GraphQL (3 threads on a positive-control PR), release-tag body, `contents/action.yml` base64 decode, check-run annotations — all returned live data. The run log's download line carried the pinned SHA, not the tag, so the bot reference now greps the ref the `uses:` line pins. `--help` confirmed `pr edit --base/--body-file`, `pr comment --body-file`, `pr review --approve`, `pr checks --watch`, `run watch --exit-status`, `pr update-branch` (merge by default, `--rebase` opt-in). git 2.50.1 (scratch repo) — `rev-list --count`, conflicting `git merge` exit 1, `git stash` mid-merge printed `error: could not write index`, `checkout --theirs` took the merged-in base side; on a scratch clone whose only remote is not named `origin`, `fetch <remote> <branch>` then `worktree add -b <head> <remote>/<head>` tracked `<remote>/<head>`. The diff-shape awk, on a synthetic diff, listed an unindented `name:` change that `grep '^[+-] '` dropped, and the non-`uses:` filter printed it. No push, edit, comment or review was sent"
---

# stale-pr-revival

## Overview

An open PR that sat for weeks is rarely blocked by its own diff. It is blocked
by everything around it: a base that moved, a review that asked for one more
thing in its body rather than in a thread, conventions that changed since it
was opened, and a merge gate nobody re-ran. Reviving it is a different job
from writing it — the goal is to bring it current, apply its reviews, and
ready the merge **without rewriting its history**.

This skill is that procedure for any open PR, yours or someone else's, plus
two variants: bot dependency PRs
([`references/bot-dependency-prs.md`](references/bot-dependency-prs.md)) and
moving a PR to a different base branch (Step 9).

**Core principle:** the instruction to revive a PR covers pushing to its
branch — it does not cover rewriting that branch's history or deleting it.

**Reference map.** Steps 2, 5, 7, 8, 9 and 10 keep a summary here and their
full procedure in [parallel-work](references/parallel-work.md),
[conflicts](references/conflicts.md), [commits](references/commits.md),
[push-and-surfaces](references/push-and-surfaces.md),
[retarget](references/retarget.md) and
[merge-and-after](references/merge-and-after.md).

## When to Use

- The user asks to "fix", finish, or update an existing open PR — often an
  approved one that is now conflicting and many commits behind its base.
- Several old dependabot / renovate version-bump PRs need re-review, revival,
  or closing as superseded.
- A PR has to move to a different base branch (for example `dev` → `main`).

For many stale PRs across several repositories, run this procedure once per
repository — one delegated worker per repository, each following the steps
below — rather than interleaving them in one context. This holds for a
cross-repo PR set serving one issue too: give each repo's PR its own worker
and keep the cross-repo contract check (names one side writes, the other
reads) and the deploy order for the orchestrator. One worker carrying three
large revivals runs out of iterations mid-way and leaves an unpushed merge
plus uncommitted fixes for someone else to reconstruct.

Order a batch by the stale clock: a PR carrying a stale-bot label closes
(and on many repos loses its head branch) a fixed number of days after the
label, and any push or comment resets it — revive or at least comment on
those first.

Every status line, briefing, and final report goes out in the user's working
language from their rule set — long tool chains (merge, tests, gate retries)
are exactly where replies drift into English and the user has to ask again.

## The Iron Law

- **Merge the base in; never rebase, never force-push.** The branch may belong
  to someone else, and its review history anchors on its commits.
- **Never delete a branch you did not create.** Merge without
  `--delete-branch` when the head belongs to another author.
- **No merge without the briefing.** Merging goes through
  [`merge-briefing`](../merge-briefing/SKILL.md), one PR per approval.

## Process

### Step 1: Read every review surface before touching code

Requested changes usually live in review **bodies**, not in inline threads.
Read all three surfaces:

```bash
gh api repos/<owner>/<repo>/pulls/<N>/reviews --paginate     # review bodies
gh api graphql -f query='<reviewThreads query for PR N>'     # inline threads
gh pr view <N> --repo <owner>/<repo> --json body,baseRefName,headRefName,isCrossRepository
```

List each finding with its decoration. An approval that still says "update
the base first" is a requested change, not a formality.

Then resolve where the head branch lives and whether you can write to it —
for a fork PR it is not on `origin`:

```bash
gh pr view <N> --repo <owner>/<repo> \
  --json isCrossRepository,headRepositoryOwner,headRepository,headRefName,maintainerCanModify
gh api repos/<head-owner>/<head-repo> --jq .permissions.push
```

The head is writable when `.permissions.push` is `true`, or for a
cross-repository PR when `maintainerCanModify` is `true` and you maintain the
base. Otherwise stop and report: never push the branch to the base repository
instead. Name the local remotes from `git remote -v`: `<head-remote>` is the
one whose URL is the head repository (add it with `git remote add` when none
is), `<base-remote>` the one for `<owner>/<repo>`; for a same-repository PR
both are usually `origin`.

Check the PR's `baseRefName` against the user's base-branch rule (their rule
set may say "base is the production branch by default" even where the repo's
own docs or default branch say `dev`) **before** Step 4. Merging the wrong
base in first makes a later retarget impossible without dragging that base's
unreleased commits along (Step 9), so a mismatch is a one-line question now,
not a rebuild later.

Done when: every review body and every thread is listed as a finding with its
decoration, the head/base branch names are known and checked against the
user's base rule, and the head repository,
its write access, and `<head-remote>` / `<base-remote>` are recorded — or the
revival stopped because the head is not writable.

### Step 2: Check for parallel work on the same defect

Before reviving, search open **and merged** PRs, issues and local worktrees for
the same mechanism: the goal often shipped through another PR while this one
waited, the base may have grown a house-standard mechanism for the whole class
of change, the vendor may now refuse the input itself, or the code path the PR
hooks into may be gated off. A superseded PR is closed with the superseding URL
instead of revived; a large or multi-repo revival first asks whether the work
still has demand and whether the repo is frozen. Full procedure: [references/parallel-work.md](references/parallel-work.md).

Done when: the searches and their keywords are listed, any overlapping PR
or issue (open or merged) is named with its URL (or "none found" with the scan
scope), a superseded PR is routed to close instead of Step 3, and the
repository's freeze/refactor status is known.

### Step 3: Open a worktree on the PR's own branch

```bash
git fetch <head-remote> <head>
git fetch <base-remote> <base>
git worktree add ../<repo>-<slug> -b <head> <head-remote>/<head>
git rev-list --count HEAD..<base-remote>/<base>   # how far behind
```

When the PR is months behind, list the **convention changes** that landed on
the base since it opened before merging anything: the repo's agent/contributor
docs, the PR template, new `.github/workflows` (new required checks), and
framework or API migrations. A clean text merge does not surface any of them;
apply them in the same revival (a missing PR-body link line, a migrated API).

From here on, address the worktree explicitly in every git call
(`git -C <worktree> merge …`, or `cd <worktree> && …` in the same command):
shell cwd resets between calls, and a `cd` that fails silently runs the merge
in the main checkout, fast-forwarding or dirtying a branch another session
uses.

Done when: the worktree is on `<head>`, the behind-count is recorded, and the
convention changes since the PR opened are listed (or "none").

### Step 4: Merge the base in

```bash
git -C <worktree> merge <base-remote>/<base>
```

Not `git rebase`, and nothing that needs `--force` afterwards.

Done when: the merge is in progress with its conflicts listed, or completed
cleanly.

### Step 5: Resolve conflicts by integrating, not by picking a side

Build the base's own workaround on the PR's abstraction instead of shipping two
mechanisms, rewrite tests whose premise the PR changes, prefer taking the base
side of a file and re-applying the PR's small additions (after listing what
`--theirs` would drop), re-run `merge-tree` against the current base, move a
fix whose code the base refactored to where the logic lives now, grep the
merged tree for every symbol the PR removes or the base deleted, and carry the
base's new guards onto the PR's parallel paths. Full procedure: [references/conflicts.md](references/conflicts.md).

Done when: no conflict markers remain, every conflicted file has a
one-line note saying which side's mechanism survived and why, and a grep of
the merged tree for each symbol the PR removes/renames returns only intended
hits.

### Step 6: Prove the core change red/green

Revert only the core line (for example the base-class swap), run the touched
tests, and show they fail. Restore it, then run the touched test directories
plus the repository's import / smoke tests.

- Sync submodules first (`git submodule update --init <path>`): the base merge
  moves the submodule pin, and a stale checkout produces import errors that
  look like regressions.
- Do not quiet pytest with `-p no:logging`; it removes the `caplog` fixture
  and turns every test that uses it into a setup error.
- **Re-measure every enumeration the PR body or anchor rests on** against the
  merged tree, not only CI. Absence claims ("0 callers of X", "no DAG built by
  Y", "only Z changes") go stale while a PR waits — new users of the changed
  path land on the base. Diff the before/after behavior map (base tree vs
  merged tree) and treat any new entry as a fresh equivalence proof, with a
  positive control showing the probe can detect a difference.

- **Run the base's new CI gates locally on the merged tree before pushing.**
  A months-behind base often added a gate that only judges changed lines
  (a changed-line lint, a new required job); the PR's own old lines now fail
  it. Read the base's workflow diff since the merge-base, run each new gate
  command on the merged tree, and fix at the root in its own commit.
- **Re-run the PR's live proof on the merged head — tests and CI are not it.**
  When the PR's anchor carries a real-environment row (prod/preview response,
  screen, timing, body-equality), the base merge moved the head, so that row
  now describes a commit that no longer exists. `git diff --quiet` on the
  PR's paths does not carry it over: the merge also moved dependencies,
  pins, and neighbours the path calls. Redo the same live measurement on the
  new head (same targets the issue names, all of them) before Step 10, and
  grade any row you could not redo as stale in the anchor.

Done when: one failing run (core line reverted) and one passing run (restored)
are captured as output, with the commands that produced them, and every live
anchor row is either re-measured on the merged head or marked stale.

### Step 7: Commit in order

The merge commit first (resolution plus test rewrites), then one commit per
review nit, staging only the files you resolved. Run a reviewer's suggested
mutant against the new test before committing it, never `git stash` mid-merge,
never run a formatter over a whole resolved file, and split a file that mixes
two commits by writing each commit's content into the index. Full procedure: [references/commits.md](references/commits.md).

Done when: `git log <head-remote>/<head>..HEAD` shows the merge commit followed by one
commit per addressed review finding.

### Step 8: Push and update the PR's own surfaces

Push to the head remote, then fix the PR body's stale facts, revise the
verification anchor in place after CI on the new head, post one change comment
(finding → what was done → SHA, the conflict resolution, the red/green output),
and reply to each thread before resolving it by its id, one call at a time.
Re-read `headRefOid` before writing the anchor (an auto-review pipeline may
push), open every status report with a per-PR verification-depth line, and run
a live proof on the converged head with a snapshot-and-restore of every row it
touches. Full procedure: [references/push-and-surfaces.md](references/push-and-surfaces.md).

Done when: the push landed, the body no longer states a fact the new head
contradicts, the change comment is posted, and CI on the new head has a final
status.

### Step 9: Retarget the base (only when asked)

Retarget with `gh pr edit --base` only when `git diff --quiet <new> <old>` shows
equal trees and the PR's `files` / `additions` / `deletions` are unchanged
after it. When the trees differ and the user still wants the new base, port the
PR's net change onto a fresh branch (generated files rebuilt, equality probes
before reusing live proof, `Co-authored-by:` for the author), open the new PR,
and leave closing the old one to the user. Full procedure: [references/retarget.md](references/retarget.md).

Done when: either the PR is on `<new>` with unchanged `files` / `additions` /
`deletions`, or a port PR on `<new>` exists with the equality probes recorded
and the old PR carries a pointer comment.

### Step 10: Brief the merge

Follow [`merge-briefing`](../merge-briefing/SKILL.md): the six-part briefing
goes in the reply **before** the approval question, also when the question is
relayed through an async channel. Do not ask while a PR in the batch has only
tests/CI on its head, read what the production base requires (approvals,
bypass right, reserved review classes) before the ask, test any assumed merge
order with `merge-tree`, and merge with `--squash` and without
`--delete-branch` on another author's head. After the merge, confirm the
running artifact, the new read surface and the changed write path separately
before calling the fix applied. Full procedure: [references/merge-and-after.md](references/merge-and-after.md).

Done when: the briefing is in the reply text and ends with the approval
question, or the merge has happened after an explicit approval and its base CI
run is reported.

## Error Handling

| Error | Recovery |
| ------- | ---------- |
| `git stash` fails with `could not write index` mid-merge | Do not retry and do not `stash pop`; stage the resolved files and commit the merge |
| Push rejected as non-fast-forward | Someone pushed meanwhile: fetch and merge `<head-remote>/<head>` in; never force |
| `git push` exits non-zero for another reason (timeout, hook output) | Compare `git ls-remote <head-remote> refs/heads/<head>` with `git rev-parse HEAD` before retrying or reporting a failure — the push often landed |
| Retarget trees differ | Report which commits would be dragged in; if the user still wants the new base, port the net change to a fresh branch (Step 9) |
| Required check fails only on a body/issue-link rule | Read that workflow for its documented bypass ([`references/bot-dependency-prs.md`](references/bot-dependency-prs.md), Step 4) |
| `gh` not authenticated for the repo | Abort with `gh auth status` output |

## Limitations

- **Closing a PR superseded by a redesign: absorb first, close second.** Move
  the commits worth keeping into the replacing PR (cherry-pick, push, open it),
  then `gh pr close <N> --comment "<new PR URL> absorbs this — <what was
  carried>"`. Repositories with a stale/cleanup workflow delete the head branch
  on close, so closing first loses the commits. A PR that mixes two concerns,
  only one of which is superseded, is not closed: revert that half in one
  commit, narrow the title and body, and list the removed files.
- **A merged shared fix does not cover call sites still on the old class.**
  Grep for the subclasses or factories that must switch over and state the
  uncovered scope in the report.
- **Approval independence.** If the reviewing account is the one that pushed
  the resolution, the approval is not independent — say so.
