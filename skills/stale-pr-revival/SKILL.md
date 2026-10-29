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

Done when: every review body and every thread is listed as a finding with its
decoration, the head/base branch names are known, and the head repository,
its write access, and `<head-remote>` / `<base-remote>` are recorded — or the
revival stopped because the head is not writable.

### Step 2: Check for parallel work on the same defect

Before reviving a fix, search for an existing PR or umbrella issue on the same
mechanism — `gh search issues` and `gh search prs` with the mechanism's
keywords (function name, config key), plus `git worktree list` for a local
attempt. A stalled PR is often one of two parallel fixes for the same defect.

Include **merged** PRs, not only open ones (`gh pr list --state merged
--search "<file or mechanism>"`, `git log <base-remote>/<deploy-branch> -- <files
the PR touches>`): the goal often shipped through a different PR while this one
waited. Then read the deploy branch's version of the touched files and compare
it to the PR's intent line by line — a flag the PR adds may now be the
operator's default, so the PR's remaining delta can shrink to a tuning value.

Also check whether the base grew a **house-standard mechanism for the whole
class of change** while the PR waited (a shared operator, builder, or tier
table now used by several siblings: `git grep -l <ClassName> <base-remote>/<base>`).
When it did, narrowing the old diff keeps a one-off path whose original
justification may now be handled by that mechanism (re-read the PR body's
"why not X" against the new code's docstring). Offer the user the choice
before Step 3 — narrow the residual, or rewrite the PR onto the standard on
the same branch (new commits, retitled body, no force-push) — with the
trade-off in one table (path, resource cost per account, precedent count).
A rewrite follows the two closest siblings' wiring rather than inventing
constants, and the change comment names them with file:line.
Re-run the PR's reproduction against the **current** base build and live
vendor before porting anything (a control run: the base binary, the same
input the PR's body says misbehaves). The vendor or an upstream change may now
refuse that input itself (a new server-side validation returning 400), which
turns the revival into a much smaller change — often only docs or a clearer
error — and the PR into one to close with that evidence. Recount any impact
number the PR body states from the field the system actually acts on, not a
compatibility echo; the old count is frequently inflated. When the premise
shrinks, stop and re-ask scope before committing the full port.

If the goal is already live, do not revive: close the PR with a comment naming
the superseding PR and the residual difference, and close the linked issue only
after a deploy-branch run shows the goal working (cite the run and the log
lines). Ask the user before closing when the residual difference is a real
behavior choice.

Ask whether the work still has demand before reviving a large PR or a
cross-repo PR set whose rollout needs human prerequisites (secret seeding,
infra apply, deploy ordering): a one-line question with the cost (repos,
files, prerequisites) is cheap, while a full multi-worker revival the user then
closes as unneeded is wasted. Small single-file revivals can proceed without
asking.

Also ask whether the target repository is under a freeze or a large in-flight
refactor before spending a revival on it (one line in the plan, or a quick
question when several PRs in one repo are queued). A revived head merged into
a moving refactor conflicts again within days and the owners may refuse
outside merges until it lands, so the whole Step 3–8 cost is thrown away;
record such PRs as on hold with their worktree kept, not revived.

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

- **The base often grew its own workaround for the same defect** while the PR
  waited. Make it build on the PR's abstraction (change its base class, keep
  its extra checks) instead of shipping two mechanisms for one problem.
- **Rewrite tests whose premise the PR changes.** A test that relied on the
  old fall-through path now has to drive the new event sequence.
- **Taking the base side of a file and re-applying the PR's small additions**
  (`git -C <worktree> checkout --theirs <file>`, then re-add) is usually cleaner than
  hand-merging hunks — and it drops the PR's unrelated formatting churn,
  which reviewers tend to flag.
- **When the base refactored the code the PR fixes**, the PR's hunk no longer
  has a home: take the base side, find where the logic lives now
  (`git -C <worktree> log <old-base>..<base-remote>/<base> -- <file>`, grep
  the raised symbol), and re-apply the fix there — keeping the old hunk
  resurrects code the refactor deleted. Re-check the blast radius at the new
  location: a new shared helper can have more callers than the old site.

- **Hunt semantic conflicts the text merge cannot show.** When the PR removes
  or renames a parameter, symbol, or default, callers that landed on the base
  while it waited still use the old shape and merge cleanly into a broken tree.
  `git -C <worktree> grep -n <removed symbol>` on the merged tree, and run the repository's
  import/load test (e.g. DagBag import errors) before committing; fold the
  caller fixes into the merge commit so every commit stays buildable.
- For a read-only conflict preview while only reporting status (no worktree
  needed): `git merge-tree --write-tree --name-only <base-remote>/<base>
  <head-sha>` lists the conflicted files.

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

Done when: one failing run (core line reverted) and one passing run (restored)
are captured as output, with the commands that produced them.

### Step 7: Commit in order

The merge commit first (resolution plus test rewrites), then one commit per
review nit.

- Stage only the files you resolved. An in-progress merge commits only the
  index, so unrelated edits can wait in the worktree.
- **Do not `git stash` during a merge.** It fails with `could not write
  index`, and the following `stash pop` targets whatever sits at
  `stash@{0}` — which may belong to another session.
- A pre-commit formatter may reformat a line that had already drifted on the
  base. Re-add the file, commit again, and mention it in the change comment.
- **Split a file that mixes two commits' changes by writing each commit's
  content into the index, not by hand-picking hunks.** Build the
  intermediate version in scratch (base plus only this commit's change, from
  `git show HEAD:<path>`), then `git update-index --cacheinfo
  100644,$(git hash-object -w <scratch file>),<path>`. `git add -p` needs a
  TTY, and `git apply --cached --unidiff-zero` of a `-U0` diff with some
  hunks dropped places later pure insertions at shifted lines. Before each
  commit, prove the index builds: `git checkout-index -a -f
  --prefix=<scratch>/idxN/` and run the tests there.
- A pre-commit check that fails on the merge commit may be failing on the base
  itself (a repo-wide guard the base already violates). Prove it by running
  that hook against the base tree first; only then skip that single hook
  (`SKIP=<hook-id> git commit`) for the merge commit, keep every hook on your
  own follow-up commits, and name the skip in the change comment.

Done when: `git log <head-remote>/<head>..HEAD` shows the merge commit followed by one
commit per addressed review finding.

### Step 8: Push and update the PR's own surfaces

```bash
git push <head-remote> <head>
gh pr edit <N> --repo <owner>/<repo> --body-file <body.md>
gh pr comment <N> --repo <owner>/<repo> --body-file <comment.md>
```

- Fix the body's stale facts — usually the verification-environment line, the
  "applied to" scope, the test list, and any count or "0 users" premise that
  Step 6's re-measurement changed.
- When the PR keeps a verification anchor comment, edit it in place as a new
  revision (rows re-measured vs. carried over named in its history) after CI
  on the new head finishes, then post the one-line update notice.
- Post one change comment: a table mapping each review finding → what was done
  (with commit SHA), the conflict resolution in prose, and the red/green
  output from Step 6.
- Reply to each review thread first, then resolve it by the explicit thread
  id you replied to, one call each — never in parallel (a reply a gate stops
  leaves a resolved thread with no answer), and never as a loop over "all
  unresolved threads": a review bot posts its next round minutes after a
  push, and such a loop closes a fresh finding nobody answered. If that
  happened, `unresolveReviewThread` it and say so.

Then wait for CI on the new head before any merge talk.

Re-read `gh pr view <N> --json headRefOid` before writing the verification
anchor or the final report: an auto-review pipeline running on the same
account often pushes a follow-up commit (a type annotation, a thread reply)
seconds after your push and cancels your CI run. Measure and cite the final
head, and say which commits were not yours.

Done when: the push landed, the body no longer states a fact the new head
contradicts, the change comment is posted, and CI on the new head has a final
status.

### Step 9: Retarget the base (only when asked)

Check that the new base already contains everything the old one does:

```bash
git diff --quiet <base-remote>/<new> <base-remote>/<old>; echo $?     # 0 = trees equal
gh pr edit <N> --base <new>
gh pr view <N> --json files,additions,deletions          # must be unchanged
```

If the trees differ, retargeting drags unreleased base commits into the PR —
stop and report instead.

Done when: either the PR is on `<new>` with unchanged `files` / `additions` /
`deletions`, or the tree difference is reported and nothing was edited.

### Step 10: Brief the merge

Follow [`merge-briefing`](../merge-briefing/SKILL.md). Write the six-part
briefing in the reply **before** the approval question — asking first and
briefing afterwards means asking twice.

- Name the staleness of the approval under "not verified": it was given on an
  older commit, and if you pushed from the approver's account, nobody
  independent has reviewed your resolution.
- `Closes #N` auto-closes its issue only on merge to the default branch; a
  plain reference does not. Say which issues stay open.
- When several revived PRs were assumed to merge in a fixed order, test the
  assumption before briefing: `git merge-tree --write-tree --name-only
  <remote>/<headA> <remote>/<headB>` (exit 0 = no textual conflict) plus each
  PR's own runtime dependency (a backend route it calls, an unmerged sibling).
  Run it against each sibling's pushed head and any in-progress local head,
  and re-run it whenever either HEAD moves. Positive-control it without
  touching a branch, in a subshell that exports a temporary index so every
  command below uses it (a `GIT_INDEX_FILE=… git read-tree` prefix covers that
  one command only, and `write-tree` then reads the real index):
  `(export GIT_INDEX_FILE=<scratch>/pc.index; git read-tree <remote>/<base>`,
  edit the same line differently with `git hash-object -w --stdin` +
  `git update-index --cacheinfo <mode>,<blob>,<file>`, then `git commit-tree
  $(git write-tree) -p <remote>/<base> -m pc)`; merge-tree against that
  dangling commit must exit 1.
  A PR blocked only by a stated order but with no conflict and no dependency can
  be briefed on its own; one whose dependency is still unmerged cannot, whatever
  its CI says.
- Merge with `--squash`, and without `--delete-branch` when the head belongs
  to another author ([`worktree-merge-cleanup`](../worktree-merge-cleanup/SKILL.md)
  owns the post-merge steps). Remove only your own worktree and local branch.
- After merge, check the deploy job of the base-branch CI run
  (`gh run view <id> --json jobs`) before saying the change is live. A
  skipped `deploy` job is not a failure when the repo ships through another
  job (a DAG-bundle publish): read which job carries the release before
  judging. Compare the merged `headRefOid` with the head you verified — the
  user or a review pipeline may have pushed after your report — and list any
  extra commits (`git log <verified>..<merged-head>`) in the post-merge note.
- Re-read each revived PR's `state` before listing it as "awaiting approval"
  in a later status: the user often closes revived PRs as no longer needed
  (a migration made them moot) without telling this session, and a stale
  list reports a closed PR as pending.

Done when: the briefing is in the reply text and ends with the approval
question, or the merge has happened after an explicit approval and its base CI
run is reported.

## Error Handling

| Error | Recovery |
| ------- | ---------- |
| `git stash` fails with `could not write index` mid-merge | Do not retry and do not `stash pop`; stage the resolved files and commit the merge |
| Push rejected as non-fast-forward | Someone pushed meanwhile: fetch and merge `<head-remote>/<head>` in; never force |
| `git push` exits non-zero for another reason (timeout, hook output) | Compare `git ls-remote <head-remote> refs/heads/<head>` with `git rev-parse HEAD` before retrying or reporting a failure — the push often landed |
| Retarget trees differ | Stop (Step 9); report which commits would be dragged in |
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
