---
name: merge-briefing
description: >
  On-demand home for the pre-merge approval procedure — the three-surface
  pre-ask probe, grading every open finding by its blocking decoration, carrying
  the anchor's `Unverified` gaps into a follow-up, the six-part briefing, and the
  `AskUserQuestion` approve-ask that ends it.
when_to_use: >
  Triggers on "merge briefing", "pre-merge briefing", "머지 브리핑",
  "머지해도 되나", "approve merge", "pre-ask probe", "merge approval".
  Do NOT activate on post-merge cleanup (use `worktree-merge-cleanup`) or on
  "merge conflict resolution".
allowed-tools:
  - Grep
  - AskUserQuestion
  - Skill
  - Bash(gh pr checks *)
  - Bash(gh pr view *)
  - Bash(gh api graphql *)
verified-against-runtime: true
runtime-verified-at: 2026-09-19
runtime-verified-note: "AskUserQuestion approve-ask (2026-09-19) — the Step 5 shapes were replayed through `momentum-rule-retrieval-gate/impl.py` on `gh pr merge 999 --squash --delete-branch` after an executed merge: a picked `승인 — 머지` emitted no decision (allow), a picked `보류 — 추가 확인 후` and a picked `진행 보류` both denied, a picked `진행 — 나중에 다시 확인` allowed (the leading-segment hazard the hold row names), and the same briefing moved out of the text block into the question body denied with `not preceded by the Pre-Merge Reporting briefing`. gh 2.97.0 — `gh pr view --json mergeable,mergeStateStatus,headRefOid,reviews,comments` and the paginated `reviewThreads` GraphQL both returned live rows (thread query positive-controlled on a PR with 3 known threads); `GH_DEBUG=api` showed that same `pr view` sends `comments(first: 100)` / `reviews(first: 100)` with no cursor variable."
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

- **One PR, one briefing, one answer.** Approving PR X approves only X. The
  one exception is the [bulk merge](#bulk-merge-several-prs-one-answer): one
  turn with a separate briefing section per PR, one ask that lists them, and
  an answer that approves the listed set.
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

**Read which commit each human approval covers** before writing "approved":
`reviewDecision: APPROVED` survives later pushes. Group the reviews by
reviewer and take each one's **latest** `APPROVED` review (an earlier approval
is superseded by a re-approval; a later `CHANGES_REQUESTED` or `DISMISSED`
from the same reviewer means no current approval). Compare that review's
`commit_id` with `headRefOid` and list the non-merge commits after it
(`git log --no-merges <commit_id>..<head>`) in part 5 as "not reviewed by
<human>" (see the Failure Modes row for the command).

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

**`CLEAN` says nothing about base commits that landed since the branch was
cut.** Count them (`git fetch origin` then `git rev-list --count
HEAD..origin/<base>`) and list the files they touched that the PR also
touches (`git diff --name-only HEAD...origin/<base>`). For each shared file,
grep that base diff for the symbols this PR changes and run `git merge-tree
--write-tree HEAD origin/<base>` — zero symbol hits and exit `0` is the
evidence for "no semantic overlap", quoted in part 5. When the pre-merge
rebase trigger fires (3+ base commits, a review round finished), bring the
base in with `gh pr update-branch <N>` (a merge commit, never a rewrite),
which restarts CI and the bot review: wait for both on the new head, re-rev
the anchor with the head that moved, then re-run Steps 1–3 against the new
head and rebuild the briefing before asking — the findings, merge state, and
approval coverage collected earlier describe the old head.

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
- When the follow-up is recorded as a comment instead (`Carried: <comment URL>`),
  put it on an issue that **stays open after the merge** — the PR's `Ref:`
  issue (e.g. a hub/umbrella issue closed later by a release step), never the
  `Closes #N` issue, which the merge itself closes and nobody reopens. The same
  goes for out-of-scope notes written while the PR is open.
- When the carry issue is the one a post-merge release step closes (a hub
  issue closed by `/release`), the release buries the carry exactly as a
  `Closes` target would. Name that conflict in the merge report (release now
  and lose the open items, or release after they are checked) rather than
  resolving it silently either way. Also check the
  `Closes #N` target after the merge: GitHub closes it only when the PR merges
  into the repository's default branch, so a PR into a non-default base
  (`prod` when the default is `dev`) leaves it open for the release step.

### Step 4: Compose the six-part briefing

Six parts, in this order (this list is the canonical shape; it was originally
derived from the author's dotfiles PR-workflow template):

1. **What changed** — scope (files, logical changes), not the issue title again
2. **What was verified** — real output, with the command cited
3. **What was NOT verified** — skipped, deferred, blocked, CI-pending
4. **Risk / blast radius** — who breaks if this is wrong
5. **Open items** — every finding Step 2 graded and left open, on all three
   surfaces; the gaps from Step 3
6. **Explicit ask** — `Approve merge?` on its own line, in the text, with the
   `AskUserQuestion` call of Step 5 right after it (the line stays even though
   the tool asks, because it is the item the gate scores)

Part 3 is the one under pressure, and it is the reason the briefing exists.

When an unverified item is the very step the merge sets off (a post-merge
deploy, an asset build the CI never ran, a multi-platform build you ran on one
platform), it is a risk, not a footnote: put it in part 4 with what breaks if
that step fails and who else it blocks (later deploys of the same stack). For a
PR whose value is bookkeeping (version pins, catalogs, docs), part 4 also says
in one line what happens if it is **not** merged — the user asks that before
approving a low-stakes PR that still triggers a deploy.

Part 3 anti-patterns:
"완료했습니다, 머지할까요?" with no evidence; a skipped check
demoted to a trailing aside; reporting at merge time what should have surfaced
at PR-ready time.

**Calibrate the length.** A typo, comment-only, or single-line config PR gets
two lines — the bar is "enough context to decide", not "long report".

### Step 5: Ask through `AskUserQuestion`, then wait

**Brief in prose, ask in the tool** — and in that order. The merge gate scores
assistant `text` blocks *and* the body of an `AskUserQuestion` question that asks
to merge the target PR (`_assistant_text(..., pr)`,
[`momentum-rule-retrieval-gate/impl.py`](../../hooks/advisory-nudge/momentum-rule-retrieval-gate/impl.py)).
Other questions are not read. On a host whose mid-turn prose never reaches the
hook (Hermes: text written between tool calls is not in the transcript the gate
reads), the merge question's own body is the only place the briefing counts —
put the six items there, named for the PR (or one section per PR for a bulk ask).

Then call `AskUserQuestion` for this PR. Four fields decide whether the answer
is readable as an approval at all:

| Field | What it carries | Why it is not free-form |
| --- | --- | --- |
| `question` | the PR number — `PR #999 를 머지할까요?` | the gate resolves the answer against the PR the merge targets; an unnamed question answers no merge |
| approval option `label` | **leads** with an approval token and negates nothing — `승인 — 머지`, `Approve merge`, `머지 — Carried: none` | only the segment before the first `— – : , ( -` is read (`_ask_label_approves`), so a token buried mid-label is not an approval; a negation anywhere in the label (`승인 — 머지하지 않기`) disqualifies the whole label |
| hold option `label` | a leading segment that is **not** an approval token — `보류 — 추가 확인 후`, `취소` | the same split runs on the hold, so keep the refusal in the lead; a trailing `나중에`/`취소`/`하지 않` is caught as a negation, but do not rely on the qualifier to carry the refusal |
| `multiSelect` | `false` | one PR, one answer |

The tool is the surface, not a softer gate. Everything the prose ask carried
still binds:

- **One PR per question**, except a bulk ask (below) that lists every PR it
  covers and follows one briefing section per PR. Do not read a
  neighbouring approval as covering this one — a companion PR, a
  dependency-completing half, a regenerated or mechanical PR, and a hotfix
  blocker each need their own briefing and their own answer. A cluster
  instruction ("do a+b+c") authorizes the intent, not each shared-state
  mutation inside it.
- **No option may skip a step.** An option reading "브리핑 생략하고 머지" turns
  the user's pick into consent for a bypass, which is what an option list makes
  look approved.
- **No session-end option** — that surface is restricted independently
  (`block-ask-end-option`).
- The Step 3 follow-up decision (`Carried: #N` / `Carried: none — <사유>`) may
  ride along as a second question in the same call; it is about this same merge.
  Any other question (a plan approval, a next-task choice) goes in a separate
  call: the gate reads every question in the call that mentions the merge, and
  a non-approval label there (`이대로 — …`) is graded as a hold on this PR.

**A pick is not always a listed label.** The runtime lets the user answer in
their own words, and that text arrives in place of a label — grade it yourself
rather than branching on an option. Anything short of an approval, an answer
that adds a condition, or an answer you cannot grade leaves the merge
unapproved: ask again.

Re-asking later re-runs Step 1. The state moved while you waited.

### Step 6: On approval, chain the cleanup — never merge by hand

On approval the **next action is the skill call**, before any `gh pr merge`
reaches a Bash call:

```text
Skill(skill="praxis:worktree-merge-cleanup")
```

That skill owns the merge call itself and everything after it — the
base-worktree call site, the head-worktree removal that `--delete-branch`
requires, the squash-ancestry stale-HEAD guard, and the no-`&&`-chain rule.
Composing the merge command here from memory skips all four, and each one fails
quietly enough to look like a clean merge.

The turn closes on three items, not one: **approval answered · cleanup skill
loaded · merge executed through it.** If approval does not come, stop — there
is nothing to clean up.

### Bulk merge (several PRs, one answer)

When several PRs are ready at once, brief them all in one turn and ask once.
Each PR still gets its own probe (Step 1–3) and its own six-part section; what
is shared is the question and the answer. When the PRs depend on each other
(one repo's change must deploy before another's), open the turn with a short
**merge order** section — the order, and for each constraint the mechanism that
breaks if it is reversed, marked measured or inferred — then the per-PR sections
in that order. The user asks for the order before the briefings when it is
missing. A PR that cannot merge yet (needs a reviewer, waits on another PR's
deploy) gets a status section but stays **out of the ask**: naming it in the ask
with a condition ("… and #B after #A deploys") makes the ask conditional and the
gate scores no PR. Ask for it in a later turn once its precondition holds.
The gate reads a fixed shape, so keep to it:

```text
### PR #833 — <title>
- 변경 사항 / What changed: …
- 검증 / Verified: …
- 미검증 / NOT verified: …
- 리스크 / Risk: …
- 남은 항목 / Open items: …

### PR #999 — <title>
- …

#833, #999 를 머지할까요?
```

| Part | Rule | Why |
| --- | --- | --- |
| section heading | a markdown heading, or a line bold end to end, naming **exactly one** of the PRs as `PR #N` (`### PR #833 — <title>`), all at the same level | a heading naming two PRs belongs to neither; decorating the heading (`### ③ backend #833`) instead of the `PR #N` form left the merge scored as "briefed turn does not name the PR"; a bold lead inside a bullet (`- **리스크**: #999 …`), a deeper sub-heading, or a `#` line in a code fence is content, not a heading |
| section body | at least 4 of the 6 items inside that PR's own section, before the next heading at the same level | the shared ask counts as every section's approve-ask item; a shared block after the last section belongs to no PR |
| ask | one merge question whose object is the PR list and whose ask ending follows the verb (`#833, #999 를 머지할까요?`, `Approve merging #833 and #999?`), as the **last** question of the turn | a question that only mentions the PRs (`#833, #999 머지 전에 CI 를 돌릴까요?`) or an unlisted `Approve merge?` is not a listed ask, and a later question (`그 전에 리베이스해 둘까요?`) is what a bare `ok` answers; then only a reply naming the PRs counts |
| answer | `모두 머지`, `둘다 승인`, `ok`, `#833 #999 머지`, `98, 1118 머지`, or an `AskUserQuestion` pick on the listed question (`승인 — 모두 머지`) | every sentence must be approval only — a deferral or precondition anywhere (`CI 한번 더 돌려보고. ok`), a condition (`머지, 근데 #999는 …`), a refusal word, or a trailing `?` voids the whole answer; a 보류 on any question in the same `AskUserQuestion` voids it; `#833만 머지` approves only #833 |

Then merge the approved PRs **one command per PR**, back to back, without
waiting for another answer — `gh pr merge 833 --squash`, then
`gh pr merge 999 --squash`. Each command must be the merge alone and name its
PR number. Anything chained to it (`cd <repo> && …`), a loop, a `GH_REPO=`
prefix, or a numberless merge is not released by a bulk answer. Once any call
after the answer has changed directory (`cd`, `pushd`) — the worktree cleanup
between merges usually does — pass `--repo <owner>/<repo>` on every later
merge, matching the repo the Step 1 probes named.
Stop at the first failed merge and report; the rest of the set stays approved
only until a merge outside the set runs.

Step 6 still applies to each PR: merge from the base worktree after removing
that PR's head worktree, and report every PR with its URL.

## Relationship to enforcement

Three hooks fire on `gh pr merge` unconditionally, whatever the flags; a fourth
fires only with `--delete-branch`. They are backstops for this procedure, not
substitutes — each catches the omission at the call site, by which point the
user is already looking at a prompt you should have preceded with a briefing.

| Hook | Fires | What it catches | Relation to this skill |
| --- | --- | --- | --- |
| `momentum-rule-retrieval-gate` | always | Fewer than 4 of the 6 briefing items present in the turn | Step 4 is what satisfies it |
| `pre-merge-approval-gate` | always | Any direct-session merge → `ask`, no agent-attachable bypass; silent for a PR the user approved in a bulk answer | Step 5 (or the bulk answer) is the answer it demands |
| `side-effect-scan` | always | Unacknowledged intentional side effect | Opt in-band with `# side-effect:ack` |
| `gh-merge-worktree-precondition` | `--delete-branch` / `-d` only | Deleting a head branch from the wrong worktree | Owned by `worktree-merge-cleanup` (Step 6) |

`commit-title-length-check` additionally advises when `--squash` is used and the
PR title exceeds 50 chars — the PR title becomes the squash commit title.

**The `AskUserQuestion` answer of Step 5 is a surface the merge gate already
reads.** Once a merge has run in the session, `momentum-rule-retrieval-gate`
requires an approval of *this* merge on top of the item count, and it accepts
either a typed reply or a non-`is_error` `AskUserQuestion` result whose question
names the PR and whose picked label leads with an approval token and negates
nothing (`_ask_answer_approves` / `_ask_label_approves`). The same surface
withdraws an approval: a hold pick, or a declined question, about this PR takes
back an earlier `ok`. That is why Step 5's field
table is a contract and not a style preference: the same pick, phrased outside
it, leaves the merge unanswered.

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
| A progress signal (`ok`, `계속`) consumed as merge consent | The ask was prose, so any short reply landed on it | Ask through `AskUserQuestion` (Step 5) |
| The gate counts 0 briefing items although a full briefing was written | The briefing lived in a question that does not name the target PR, or in prose the host never delivered to the hook | Name the PR in the merge question and carry the items there or in delivered prose (Step 5) |
| Hermes: merge denied with `no briefing is present in the window` mid-turn although the briefing was written as prose right before the call | Prose between tool calls in one Hermes turn is not seen by the hook; after a merge has run, only what follows that merge counts | Put the six items inside the `clarify` merge question, naming the PR; for several PRs, one section per PR inside the same question body plus the listed ask. After that answer, merge each listed PR as its own command (`gh pr merge N --repo o/r --squash # side-effect:ack`), back to back, no `cd`/chain. Pre-merge writes the procedure requires between the answer and the merge (anchor `Carried:` rev, update notice, head-worktree removal) can still make the gate score fewer than 4 items on that merge call. Then, and only then, the briefing provably sits in this turn's answered `clarify` body, so re-issue the same merge once with `# briefing-surfaced: six-item briefing in this turn's clarify merge question for PR N, answered <label>` — a truthful attestation, not a bypass. Never use the marker when no answered PR-named briefing exists in the turn |
| Hermes: `briefed turn does not name the PR this merge targets` after one briefing covered two PRs and the user said `merge` | A joint briefing is scored for neither PR, and re-writing the briefing in the same turn as the merge call does not count either | Stop after the first block (no third retry). Brief exactly one PR, end the turn, merge it on the reply; brief the next PR in the merge-result turn. A short delta (e.g. `UNKNOWN` → `MERGEABLE`) plus the PR-named ask is enough when the full briefing for that PR was the previous turn |
| Hermes: two PRs in different repos asked as two questions of one `clarify` call, both answered `머지`; the first merge passes, the second is denied with `no briefing is present in the window` | Cross-repo PRs cannot use the bulk shape, and once the first merge has run the gate only reads what follows it, so the second PR's earlier answer no longer counts | Finish the first PR's cleanup, then ask the second PR alone in a new `clarify` whose question body carries the six items labelled (`① What changed … ⑥ #N 을 머지할까요?`), and merge it on that answer with the `# briefing-surfaced` marker. Plan cross-repo pairs as two sequential asks from the start |
| A picked hold read as approval | The hold label's leading segment was an approval token (`진행 — 나중에`) | Hold labels never lead with one (Step 5) |
| User stops the merge with "검증이 필요합니다" after the briefing | The briefing's evidence was self-produced; this user wants an independent check before approving a multi-PR cluster | Before the bulk ask, dispatch `evidence-audit` (one read-only auditor per repo group), close what can be measured now, fix doc drift on the PR branches, re-rev the anchors, and only then brief. A head that moved re-opens every approval |
| Hermes: merge blocked with `no briefing is present in the window` although the user answered `merge` to a full briefing | A typed message landed between the briefing and the answer (a question, a correction, a `merge` the host dropped), so the gate reads the turn after it as the one right before the approval and finds no briefing in it. This includes the answer to **your own** clarifying question: if the user's `merge` came with an ambiguous extra ("1번으로") and you asked which item they meant, their reply ("A") is a disambiguation, not an approval. A background-process or subagent notification does not do this: the Hermes adapter tags it `origin.kind` from its `display_kind`, as Claude Code tags its own, and the gate skips it | Do not retry with `# briefing-surfaced` (the marker attests an answered PR-named briefing in this turn, which a disambiguation reply is not — the gate blocks it too). Re-run Step 1, write the full six-part briefing again (not a "same as before" delta, and fold in anything that changed since, e.g. a version bump after a base merge), end the turn, and merge on the next reply. When the PR changed after the user's `merge` (base merge, new commit), say so in the re-brief rather than merging on the old answer |
| Hermes: the `clarify` ask comes back `cancelled`/`unanswered` and the next turn's reply is a bare `merge` | The user answers in chat rather than in the tool, so the bundled `Carried:` question stays unanswered | Re-run Step 1, then merge on the typed reply. For the unanswered carry, record the named follow-ups as a comment on the open issue the PR refs and that outlives the merge — not its `Closes` target, which the merge closes (never file a new issue) and write `Carried: <comment URL>` into the anchor before the merge call |
| Briefing says "reviewer approved" but the approval predates the last behavior commit | `reviewDecision: APPROVED` survives later pushes; the approving review's `commit_id` can be several commits behind HEAD (reviewers also post "re-approved at <sha>" in the PR's Slack thread) | Read `gh api repos/<o>/<r>/pulls/<N>/reviews --jq '.[]\|select(.state=="APPROVED")\|{user:.user.login,commit_id}'`, list the non-merge commits after it (`git log --no-merges <commit_id>..<head>`), and put "not reviewed by <human>: <commits>" in part 5. Re-read the PR's Slack review thread too |
| A comment or review that arrived after your last check is missing from the briefing | Findings were read once at PR-ready time and not re-listed since | List every unresolved thread with its first-comment author at compose time; a bot thread you have not answered is an open item even when you remember handling "the" bot finding |
| User asks why the PR shows no merge button | `mergeStateStatus: BLOCKED` from a ruleset requiring an approving review on a PR the user authored (authors cannot approve their own PR) | Read the `pull_request` record from `gh api repos/<o>/<r>/rules/branches/<base>` (`.[] \| select(.type=="pull_request") \| .parameters.required_approving_review_count`) and the ruleset's `current_user_can_bypass`. If `always` or `pull_requests_only`, tell the user the "Merge without waiting for requirements to be met (bypass rules)" checkbox in the blocked merge box reveals the button; if `never`, a reviewer is needed. Say this in the briefing's open items before they hit it — and even when bypass is possible, put a hold option `보류 — 리뷰어 지정 후` in the merge question: users often prefer a human approval over bypassing the rule |
| User picks "reviewer first" but no reviewer is named | Reviewer choice treated as a guess, or the candidates are logins nobody recognizes | Rank candidates from evidence, then ask (assigning notifies people): authors of the changed directory (`git log origin/<base> --since="6 months ago" --format=%an -- <dir> \| sort \| uniq -c \| sort -rn`), each mapped to a login via `gh api repos/<o>/<r>/commits/<sha> --jq .author.login` on their latest commit there, plus top approvers on recent merged PRs (`gh pr list --state merged --limit 40 --json reviews --jq '[.[].reviews[]\|select(.state=="APPROVED")\|.author.login]\|group_by(.)\|map({u:.[0],n:length})'`). Show `login (real name)` with the count behind each; the user may pick several. Then `gh pr edit <N> --add-reviewer a,b,c` and confirm with `gh pr view --json reviewRequests` |
| CI reported failed because a background `gh run watch --exit-status` exited 1 | The watch itself died (network read timeout on the API poll), not the run | Before calling CI failed, read `gh run view <id> --json status,conclusion,jobs` and the tail of the watch log; only `conclusion` decides |
| Merge approval asked on a PR that cannot merge yet | `mergeStateStatus: BLOCKED` only because a required approving review is missing and `current_user_can_bypass` is `never`; everything else (CI, threads, mergeable) is green | Do not ask the merge question. Still finish Step 3 now (write `Carried:` into the anchor, record named follow-ups on the issue that outlives the merge), write the per-PR sections as a status briefing, and ask instead whether to request a reviewer — assigning one notifies a person, so it needs its own yes. If the user declines ("직접 처리"), stop; they may merge out-of-band with the repo's force-merge command, so on their "merged" report go straight to `worktree-merge-cleanup`'s out-of-band path |
| `--delete-branch` run from the wrong worktree after approval | Step 6 read as advice, merge command hand-rolled | Chain `worktree-merge-cleanup` as the next action (Step 6) |
| User says "both merged" out-of-band, your check shows one still OPEN, and you warn them (or offer a revert) | The user merges PRs seconds apart; a status read taken right after their message can land before the second merge | Before reporting a reported-merged PR as unmerged, re-read `gh pr view <N> --json state,mergedAt,mergeCommit` once more (a few seconds later) for every PR named; only a second OPEN read is a finding, and the report quotes both `mergedAt` values |

## Limitations

- The three surfaces cover GitHub. A finding raised in Slack, in a meeting, or
  in a sibling issue is outside this probe — bring it into the briefing yourself.
- `mergeable` / `mergeStateStatus` return `UNKNOWN` while GitHub computes the
  merge state on a freshly-pushed PR. `UNKNOWN` is not `CLEAN`; re-poll rather
  than reporting it as mergeable.
- Grading an unlabeled bot thread is a judgement call. When it is genuinely
  ambiguous, list it in part 5 with your grade and its reason, and let the user
  overrule.
