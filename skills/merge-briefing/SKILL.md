---
name: merge-briefing
description: >
  On-demand home for the pre-merge approval procedure — the three-surface
  pre-ask probe, grading every open finding by its blocking decoration, carrying
  the anchor's `Unverified` gaps into a follow-up, and the six-part briefing that
  ends in an explicit approve-ask.
when_to_use: >
  Triggers on "merge briefing", "pre-merge briefing", "머지 브리핑",
  "머지해도 되나", "approve merge", "pre-ask probe", "merge approval".
  Do NOT activate on post-merge cleanup (use `worktree-merge-cleanup`) or on
  "merge conflict resolution".
allowed-tools:
  - Grep
  - Bash(gh pr checks *)
  - Bash(gh pr view *)
  - Bash(gh api graphql *)
verified-against-runtime: true
runtime-verified-at: 2026-08-13
runtime-verified-note: "gh 2.97.0 — `gh pr view --json mergeable,mergeStateStatus,headRefOid,reviews,comments` and the paginated `reviewThreads` GraphQL both returned live rows (thread query positive-controlled on a PR with 3 known threads); `GH_DEBUG=api` showed that same `pr view` sends `comments(first: 100)` / `reviews(first: 100)` with no cursor variable."
---

# merge-briefing

## Overview

A merge is irreversible shared state, so it is gated on the user's explicit
per-PR answer. The gate is cheap to satisfy formally and easy to satisfy
falsely: the honest version needs a probe wide enough to support "nothing is
open", and the surface where blocking findings actually live — inline review
threads — is the one a natural-looking `gh pr view --json comments,reviews`
call silently omits.

This skill is the on-demand home for that procedure. The *Pre-Merge Reporting*
rule ([`ETHOS.md` → Rules praxis carries](../../ETHOS.md#rules-praxis-carries))
keeps the gate; the six-part list in Step 4 below is the report's shape; the
steps below are how you get from "the PR looks done" to a question the user
can answer in one word.

**Core principle:** the briefing is a claim about the PR's state, so every line
in it is probed at compose time — not recalled from when the PR was opened.

## When to Use

- About to ask the user to approve a merge.
- A merge was requested and you have not yet surfaced a briefing this turn.
- Re-asking after a previous ask went unanswered, or after CI/review moved.
- A companion, follow-up, or regenerated PR is next in a cluster the user
  already approved once.

## The Iron Law

- **One PR, one briefing, one answer.** Approving PR X approves only X.
- **Compose the briefing from this turn's probe output.** An entry-time `CLEAN`
  status is not permanent; a stale anchor makes the user discover a conflict
  you should have caught.
- **Never merge on a progress signal.** "계속", "ok", "진행" move work forward;
  they are not consent for a shared-state mutation.

## Process

### Step 1: Probe all three surfaces

Findings live on three surfaces and no single command covers them. Run all
three before writing a word of the briefing.

```bash
# 1 — CI
gh pr checks <N> --repo <owner>/<repo>

# 2 — conversation timeline + review bodies + mergeability + pushed HEAD
gh pr view <N> --repo <owner>/<repo> \
  --json mergeable,mergeStateStatus,isDraft,headRefOid,reviews,comments
```

```bash
# 3 — inline review threads (the surface #2 does NOT include)
gh api graphql --paginate -f query='
query($owner:String!, $repo:String!, $pr:Int!, $endCursor:String) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$pr) {
      reviewThreads(first:100, after:$endCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id isResolved isOutdated path line
          comments(first:1) { nodes { author { login } body } }
        }
      }
    }
  }
}' -F owner=<owner> -F repo=<repo> -F pr=<N> \
  --jq '.data.repository.pullRequest.reviewThreads.nodes[]
        | select(.isResolved | not)
        | {id, path, line, author: .comments.nodes[0].author.login,
           body: .comments.nodes[0].body}'
```

`body` is in the projection because the label is in the thread's own text, and
the label is what Step 2 grades on. `--paginate` with `$endCursor` is not
decoration — a PR two bots have worked over passes 100 threads, and a single
page silently drops the rest, which then reads as "no open threads".

**Surface #2 truncates at 100 as well, and silently.** `gh pr view --json
comments,reviews` issues `comments(first: 100)` / `reviews(first: 100)` with no
cursor variable, so on a PR past either count the tail is simply absent. When
one of them is at 100 rows, re-collect that surface through the paginated REST
endpoint before grading:

```bash
gh api repos/<owner>/<repo>/pulls/<N>/reviews --paginate -q '.[] | {user: .user.login, state, body}'
gh api repos/<owner>/<repo>/issues/<N>/comments --paginate -q '.[] | {user: .user.login, body}'
```

**An empty result is not yet an answer.** It means "no open threads" and "the
query is wrong" at the same time until you have seen this exact query print
rows on a PR known to have them. Run it once against such a PR; that is the
positive control the negative claim rests on.

If CI is pending or failing, **stop here** — do not ask yet. A briefing whose
verification rows are still moving is asking the user to approve a guess.

**The merge state is an allowlist, not a "not obviously broken" check.** Ask
only when `mergeable` is `MERGEABLE` **and** `mergeStateStatus` is `CLEAN` or
`HAS_HOOKS` — those two are the only values that mean mergeable with a passing
commit status. Every other value stops the ask and names itself in the briefing:

| `mergeStateStatus` | What it means | Ask? |
| --- | --- | --- |
| `CLEAN` | Mergeable and passing commit status | Yes |
| `HAS_HOOKS` | Mergeable, passing, pre-receive hooks present | Yes |
| `UNSTABLE` | Mergeable with a **non-passing** commit status | No — that is the failing CI above |
| `BLOCKED` | Merge is blocked (protection rule, required review) | No |
| `BEHIND` | Head ref is out of date | No — rebase first |
| `DIRTY` | Merge commit cannot be cleanly created | No — conflict |
| `UNKNOWN` | State cannot currently be determined | No — re-poll |

Draft status is **not** in this enum — it is the separate `isDraft` field, so a
draft PR can report `CLEAN`. Add `isDraft` to the `--json` list and treat a
draft as not ready regardless of its merge state.

### Step 2: Grade every finding on all three surfaces

An open thread count is not a blocker count, and the thread surface is not the
whole corpus. Grade each finding Step 1 collected — inline thread, conversation
comment, and review body alike — by **the decoration, not the label**:

| First line of the finding | Blocks the merge? |
| --- | --- |
| `issue (blocking):` / `suggestion (blocking):` | Yes — the merge waits |
| `issue (non-blocking):` / `suggestion (non-blocking):` | No |
| `nitpick:` / `note:` | No — unresolved by design |
| No label at all | Grade it yourself — read the body |

`suggestion` is not a lower rung than `issue`: both carry exactly one of
`(blocking)` / `(non-blocking)`, so the decoration is the whole judgement and
the label only says what kind of finding it is. Reading the label instead is how
a declared blocker gets waved through as "just a suggestion".

**A review body is not a thread and cannot be resolved**, which is exactly why
it goes unread: nothing about it looks open. CodeRabbit files its actionable
findings in collapsible review-body sections rather than as line comments, so
grep the bodies collected in Step 1 for `Actionable`, `Nitpick`,
`outside diff`, `🧹` and grade what you find. A conversation comment raising a
defect is graded the same way. Blocking findings on either surface stop the ask
just as an inline one does — they simply have no thread to leave open, so they
are carried in the briefing's open items instead.

The unlabeled case is the common one, not the exception: bots write in their
own vocabulary. CodeRabbit opens with its own severity line
(`_🎯 Functional Correctness_ | _🟠 Major_ | …`), Copilot and BugBot with
neither. Treat a bot's severity as an input to your grade, never as the grade —
and never as a reason to skip the finding because "it's just a bot".

A thread you graded as blocking has one legitimate exit before the merge: it is
fixed and closed, or it is **regraded** — editing the comment to
`(non-blocking)` and posting the `Revised —` notice. Parking a blocking finding
in a follow-up without regrading leaves the branch locked while every label
reads non-blocking.

**Clearing the review-level `REQUEST_CHANGES` is a separate, later step.** That
state belongs to the review, which may hold several findings, so it is cleared
only once **every** blocking finding in it is fixed-and-closed or regraded —
never on the strength of one. Regrading the last one is what unlocks it; until
then the review correctly still blocks.

### Step 3: Carry the anchor's `Unverified` gaps

Open the PR's verification anchor and read its `Unverified` list. A gap written
as "머지 후 실전송으로 확인" dies with the PR unless something outlives it.

- Surface exactly those gaps that **named a next action** — a gap with no
  stated next action is not carried.
- Ask, as part of the briefing, whether to open a follow-up issue.
- Write the answer into the anchor's final rev as `Carried: #N` or
  `Carried: none — <사유>` **before** the merge.
- Writing that final rev needs `gh` — a session whose only GitHub surface is
  the MCP server cannot update a comment body when that server's active tool
  list exposes no comment `update` (confirm before assuming). There, put the
  `Carried:` line in the merge commit or the PR body instead, and never in a
  second anchor comment. See
  [`anchor-comment-gate/spec.md` → Procedure for a gh-less session](../../hooks/preflight-gate/anchor-comment-gate/spec.md#procedure-for-a-gh-less-session).
- Never auto-file the follow-up: a new issue still needs its own
  implementation-approach review first.

### Step 4: Compose the six-part briefing

Six parts, in this order (this list is the canonical shape; it was originally
derived from the author's dotfiles PR-workflow template):

1. **What changed** — scope (files, logical changes), not the issue title again
2. **What was verified** — real output, with the command cited
3. **What was NOT verified** — skipped, deferred, blocked, CI-pending
4. **Risk / blast radius** — who breaks if this is wrong
5. **Open items** — every finding Step 2 graded and left open, on all three
   surfaces; the gaps from Step 3
6. **Explicit ask** — `Approve merge?` on its own line

Part 3 is the one under pressure, and it is the reason the briefing exists.
Anti-patterns: "완료했습니다, 머지할까요?" with no evidence; a skipped check
demoted to a trailing aside; reporting at merge time what should have surfaced
at PR-ready time.

**Calibrate the length.** A typo, comment-only, or single-line config PR gets
two lines — the bar is "enough context to decide", not "long report".

### Step 5: Ask, then wait

Ask for this PR. Do not bundle, and do not read a neighbouring approval as
covering it — a companion PR, a dependency-completing half, a regenerated or
mechanical PR, and a hotfix blocker each need their own briefing and their own
answer. A cluster instruction ("do a+b+c") authorizes the intent, not each
shared-state mutation inside it.

Re-asking later re-runs Step 1. The state moved while you waited.

### Step 6: Merge, then chain the cleanup

On approval, the merge call itself and everything after it belong to the
cleanup procedure — the base-worktree call site, the head-worktree removal that
`--delete-branch` requires, the squash-ancestry stale-HEAD guard, and the
no-`&&`-chain rule:

```text
Skill(skill="praxis:worktree-merge-cleanup")
```

Chain it as the next action after the user's approval; do not hand-roll the
merge command from memory. If approval does not come, stop — there is nothing
to clean up.

## Relationship to enforcement

Three hooks fire on `gh pr merge` unconditionally, whatever the flags; a fourth
fires only with `--delete-branch`. They are backstops for this procedure, not
substitutes — each catches the omission at the call site, by which point the
user is already looking at a prompt you should have preceded with a briefing.

| Hook | Fires | What it catches | Relation to this skill |
| --- | --- | --- | --- |
| `momentum-rule-retrieval-gate` | always | Fewer than 4 of the 6 briefing items present in the turn | Step 4 is what satisfies it |
| `pre-merge-approval-gate` | always | Any direct-session merge → `ask`, no agent-attachable bypass | Step 5 is the answer it demands |
| `side-effect-scan` | always | Unacknowledged intentional side effect | Opt in-band with `# side-effect:ack` |
| `gh-merge-worktree-precondition` | `--delete-branch` / `-d` only | Deleting a head branch from the wrong worktree | Owned by `worktree-merge-cleanup` (Step 6) |

`commit-title-length-check` additionally advises when `--squash` is used and the
PR title exceeds 50 chars — the PR title becomes the squash commit title.

**Do not reach for a bypass token to get past a block.**
`PRAXIS_MOMENTUM_MERGE_ADVISORY=1` and `# briefing-surfaced: <reason>` assert
that the briefing happened. Attaching either to a turn where it did not is a
false attestation, and it is the documented path by which the gate becomes
decorative.

## Failure Modes

| Failure | Cause | Fix |
| --- | --- | --- |
| "No open findings" while a blocking thread sits open | `--json comments,reviews` only covers two of three surfaces | Run the `reviewThreads` query (Step 1 #3) |
| Merge stalls on an unresolved nit | Open-thread count treated as blocker count | Grade by decoration (Step 2) |
| A declared blocker waved through as "just a suggestion" | Graded on the label instead of the decoration | `suggestion (blocking):` blocks (Step 2) |
| CodeRabbit's actionable findings never reach the briefing | Review bodies collected but not graded | Grep the bodies for `Actionable` / `Nitpick` (Step 2) |
| Half the threads never appear | `first:100` without `--paginate` | Keep `$endCursor` + `pageInfo` |
| The 101st comment or review never appears | `gh pr view --json` sends `first: 100` with no cursor | Re-collect that surface via `gh api … --paginate` (Step 1) |
| "No open threads" that was really a broken query | Empty result consumed as an answer | Positive-control the query on a PR with known threads |
| Anchor gap silently dies at merge | `Unverified` item never carried | `Carried: #N` / `Carried: none — <사유>` before merging (Step 3) |
| User discovers a conflict you missed | Briefing composed from an entry-time probe | Re-probe at compose time, every re-ask |
| Companion PR merged on the neighbour's approval | Approval transfer | One briefing per PR (Step 5) |

## Limitations

- The three surfaces cover GitHub. A finding raised in Slack, in a meeting, or
  in a sibling issue is outside this probe — bring it into the briefing yourself.
- `mergeable` / `mergeStateStatus` return `UNKNOWN` while GitHub computes the
  merge state on a freshly-pushed PR. `UNKNOWN` is not `CLEAN`; re-poll rather
  than reporting it as mergeable.
- Grading an unlabeled bot thread is a judgement call. When it is genuinely
  ambiguous, list it in part 5 with your grade and its reason, and let the user
  overrule.
