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
  "retarget PR", "묵은 PR 살리기". Do NOT activate on "merge cleanup" or
  "post-merge cleanup" (that is `worktree-merge-cleanup`) or on reviewing
  someone else's PR without pushing to it.
verified-against-runtime: true
runtime-verified-at: 2026-10-16
runtime-verified-note: "gh 2.102.0 — against a public repo's merged dependabot PR: `pr view --json body,baseRefName,headRefName,isCrossRepository` and `headRefOid,mergeable,mergeStateStatus,reviewDecision`, `pr diff` (one -/+ `uses:` pair), `pr checks`, `run list --commit`, paginated reviews and the `reviewThreads` GraphQL (3 threads on a positive-control PR), release-tag body, `contents/action.yml` base64 decode, check-run annotations — all returned live data. The run log's download line carried the pinned SHA, not the tag, so the bot reference now greps the ref the `uses:` line pins. `--help` confirmed `pr edit --base/--body-file`, `pr comment --body-file`, `pr review --approve`, `pr checks --watch`, `run watch --exit-status`, `pr update-branch` (merge by default, `--rebase` opt-in). git 2.50.1 (scratch repo) — `rev-list --count`, conflicting `git merge` exit 1, `git stash` mid-merge printed `error: could not write index`, `checkout --theirs` took the merged-in base side. No push, edit, comment or review was sent"
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
below — rather than interleaving them in one context.

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

Done when: every review body and every thread is listed as a finding with its
decoration, and the head/base branch names are known.

### Step 2: Check for parallel work on the same defect

Before reviving a fix, search for an existing PR or umbrella issue on the same
mechanism — `gh search issues` and `gh search prs` with the mechanism's
keywords (function name, config key), plus `git worktree list` for a local
attempt. A stalled PR is often one of two parallel fixes for the same defect.

Done when: the searches and their keywords are listed, and any overlapping PR
or issue is named with its URL (or "none found" with the scan scope).

### Step 3: Open a worktree on the PR's own branch

```bash
git worktree add ../<repo>-<slug> -b <head> origin/<head>
git rev-list --count HEAD..origin/<base>          # how far behind
```

When the PR is months behind, list the **convention changes** that landed on
the base since it opened before merging anything: the repo's agent/contributor
docs, the PR template, new `.github/workflows` (new required checks), and
framework or API migrations. A clean text merge does not surface any of them;
apply them in the same revival (a missing PR-body link line, a migrated API).

Done when: the worktree is on `<head>`, the behind-count is recorded, and the
convention changes since the PR opened are listed (or "none").

### Step 4: Merge the base in

```bash
git merge origin/<base>
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
  (`git checkout --theirs <file>`, then re-add) is usually cleaner than
  hand-merging hunks — and it drops the PR's unrelated formatting churn,
  which reviewers tend to flag.

Done when: no conflict markers remain, and every conflicted file has a
one-line note saying which side's mechanism survived and why.

### Step 6: Prove the core change red/green

Revert only the core line (for example the base-class swap), run the touched
tests, and show they fail. Restore it, then run the touched test directories
plus the repository's import / smoke tests.

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

Done when: `git log origin/<head>..HEAD` shows the merge commit followed by one
commit per addressed review finding.

### Step 8: Push and update the PR's own surfaces

```bash
git push origin <head>
gh pr edit <N> --repo <owner>/<repo> --body-file <body.md>
gh pr comment <N> --repo <owner>/<repo> --body-file <comment.md>
```

- Fix the body's stale facts — usually the verification-environment line, the
  "applied to" scope, and the test list.
- Post one change comment: a table mapping each review finding → what was done
  (with commit SHA), the conflict resolution in prose, and the red/green
  output from Step 6.

Then wait for CI on the new head before any merge talk.

Done when: the push landed, the body no longer states a fact the new head
contradicts, the change comment is posted, and CI on the new head has a final
status.

### Step 9: Retarget the base (only when asked)

Check that the new base already contains everything the old one does:

```bash
git diff --quiet origin/<new> origin/<old>; echo $?     # 0 = trees equal
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
- Merge with `--squash`, and without `--delete-branch` when the head belongs
  to another author ([`worktree-merge-cleanup`](../worktree-merge-cleanup/SKILL.md)
  owns the post-merge steps). Remove only your own worktree and local branch.
- After merge, check the deploy job of the base-branch CI run
  (`gh run view <id> --json jobs`) before saying the change is live.

Done when: the briefing is in the reply text and ends with the approval
question, or the merge has happened after an explicit approval and its base CI
run is reported.

## Error Handling

| Error | Recovery |
| ------- | ---------- |
| `git stash` fails with `could not write index` mid-merge | Do not retry and do not `stash pop`; stage the resolved files and commit the merge |
| Push rejected as non-fast-forward | Someone pushed meanwhile: fetch and merge `origin/<head>` in; never force |
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
