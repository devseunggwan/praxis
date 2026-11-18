---
name: merge-briefing
description: >
  Brief and ask before merging any PR: probe CI, reviews and inline threads,
  grade every open finding by its blocking decoration, carry the anchor's
  `Unverified` gaps, write the six-part briefing, and end on the approve-ask.
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

**Reference map.** The spine below is the decision flow; each step links the
file that holds its full procedure: [probe](references/probe.md) (Step 1),
[grading and carry](references/grading-and-carry.md) (Steps 2-3),
[briefing](references/briefing.md) (Step 4), [bulk merge](references/bulk-merge.md),
[failure modes](references/failure-modes.md), and [Hermes Agent](references/hermes.md).

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
three before writing a word of the briefing: `gh pr checks <N>`; `gh pr view
<N> --json mergeable,mergeStateStatus,isDraft,headRefOid,reviews,comments`;
and the paginated `reviewThreads` GraphQL query (the inline threads #2 does
not include). Both `gh pr view` lists stop at 100 rows without saying so, and
an empty thread list counts only after the same query printed rows on a PR
known to have threads. Exact commands, the pagination and fork rules:
[references/probe.md](references/probe.md).

Stop before asking while CI is pending or failing or a review bot still runs on
the head. Ask only when `mergeable` is `MERGEABLE` **and** `mergeStateStatus` is
`CLEAN` or `HAS_HOOKS`, and treat `isDraft` as not ready. Read which commit each
human approval covers, count the base commits that landed since the branch was
cut, and re-probe live before answering "did you check?". The table of merge
states, the semantic-overlap check and when to `update-branch` are in the same
reference.

### Step 2: Grade every finding on all three surfaces

Grade each finding (inline thread, conversation comment, review body) by **the
decoration, not the label**: `(blocking)` blocks, `(non-blocking)`, `nitpick:`
and `note:` do not, and an unlabeled finding (most bot output) is graded by
reading it. Review bodies hold CodeRabbit's actionable findings and have no
thread to leave open, so they go into the open items. A blocking finding leaves
only by being fixed and closed or regraded; the review's `REQUEST_CHANGES` is
cleared after its last blocker. Detail: [references/grading-and-carry.md](references/grading-and-carry.md).

### Step 3: Carry the anchor's `Unverified` gaps

Every `Unverified` gap that named a next action is surfaced in the briefing,
and the anchor's final rev gets `Carried: #N` or `Carried: none — <reason>`
**before** the merge, recorded on an issue that stays open after it (never the
`Closes` target). Never auto-file the follow-up. Detail, including the gh-less
session and release-step cases: [references/grading-and-carry.md](references/grading-and-carry.md#step-3-carry-the-anchors-unverified-gaps).

### Step 4: Compose the six-part briefing

Six parts, in this order:

1. **What changed**: one plain sentence of what the PR does and what changes
   for the user, then the scope
2. **What was verified**: real output with the command, and the deepest real
   surface each claim reached on the current head
3. **What was NOT verified**: skipped, deferred, blocked, CI-pending
4. **Risk / blast radius**: who breaks if this is wrong
5. **Open items**: every finding Step 2 left open, and the gaps from Step 3
6. **Explicit ask**: `Approve merge?` on its own line, in the text

Part 3 is the reason the briefing exists. Pre-answer why each new component
must exist, why the problem arose, and the security question; close part 3
items by observing, not by reading; and calibrate the length (a one-line PR
gets two lines). The rules for each part and its anti-patterns:
[references/briefing.md](references/briefing.md).

### Step 5: Ask through `AskUserQuestion`, then wait

**When the host has its own approval path, ask through it.** A host that
collects the merge answer in its own channel (a host approval provider named by
`PRAXIS_MERGE_APPROVAL_PROVIDER`, or an unattended run where `clarify` is not
delivered and questions go to an external decision board) gets the same
briefing and the same one-PR question through that channel; the field rules
below still decide whether its answer reads as an approval. Hermes Agent's
shapes: [references/hermes.md](references/hermes.md).

**Brief in prose, ask in the tool** — and in that order. The merge gate scores
assistant `text` blocks *and* the body of an `AskUserQuestion` question that asks
to merge the target PR (`_assistant_text(..., pr)`,
[`merge-gate/briefing.py`](../../hooks/preflight-gate/merge-gate/briefing.py)).
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
- **Merge has no preview tier, so say so in the question body.** praxis
  `menu-mutation-tier-advisory` holds an approval menu whose options all
  mutate (`승인 — 머지` / `보류`; a hold does not count as a safe tier).
  Put a column-0 line `Safe-tier-unavailable: <why — e.g. merge has no preview;
  post-merge behaviour already shown by <dry-run / staging preview>>` in the
  merge question so the menu runs on the first call.
- **A PR no reviewer has looked at** (0 reviews, e.g. the review bot skipped
  the head on a rate limit) gets a hold option that names the missing step —
  `Hold — run codex-review-wrap first` — so the user can pick the review over
  the merge.
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

Pass `--match-head-commit <sha>` on the merge so a head that moved after the briefing fails the merge instead of landing unreviewed commits; pass the **full 40-character** `headRefOid` your Step 1 probe recorded for the briefing, not one re-read after the approval (when `gh pr view <N> --json headRefOid` now differs, re-brief instead of merging); an abbreviated sha fails with `Could not coerce value … to GitObjectID` before anything merges. Copy the 40 characters from the probe output; never extend a short sha from memory: a composed tail fails with `Head branch was modified`, which reads like a moved head and invites a needless re-brief (re-read `headRefOid` to tell the two apart).

Never remove the head worktree before the merge has passed: a blocked or
interrupted merge leaves the PR open with no worktree, and in a multi-stage
pipeline a sibling session (verification, review) may be working in that same
directory, so removing it breaks their run mid-measurement. Merge without
`--delete-branch` first, confirm `MERGED`, then clean up.

The turn closes on three items, not one: **approval answered · cleanup skill
loaded · merge executed through it.** If approval does not come, stop — there
is nothing to clean up.

### Bulk merge (several PRs, one answer)

Several ready PRs can be briefed in one turn and asked once: each PR keeps its
own Step 1–3 probe and its own six-part section under a heading that names
exactly that PR (`### PR #833 — <title>`), the last question of the turn lists
the PRs (`#833, #999 를 머지할까요?`), and the approved PRs merge one command per
PR, each naming its number with nothing chained to it. A PR that cannot merge
yet stays out of the ask, and dependent PRs open with a merge-order section.
The shape the gate reads and the answer forms it accepts:
[references/bulk-merge.md](references/bulk-merge.md).

## Relationship to enforcement

Three hooks fire on `gh pr merge` unconditionally, whatever the flags; a fourth
fires only with `--delete-branch`. They are backstops for this procedure, not
substitutes — each catches the omission at the call site, by which point the
user is already looking at a prompt you should have preceded with a briefing.

| Hook | Fires | What it catches | Relation to this skill |
| --- | --- | --- | --- |
| `merge-gate` (briefing check) | always | Fewer than 4 of the 6 briefing items present in the turn | Step 4 is what satisfies it |
| `merge-gate` (approval check) | always | Any direct-session merge → `ask`, no agent-attachable bypass; silent for a PR the user approved in a bulk answer | Step 5 (or the bulk answer) is the answer it demands |
| `side-effect-scan` | always | Unacknowledged intentional side effect | Opt in-band with `# side-effect:ack` |
| `merge-gate` (worktree check) | `--delete-branch` / `-d` only | Deleting a head branch from the wrong worktree | Owned by `worktree-merge-cleanup` (Step 6) |

`commit-title-length-check` additionally advises when `--squash` is used and the
PR title exceeds 50 chars — the PR title becomes the squash commit title.

**The `AskUserQuestion` answer of Step 5 is a surface the merge gate already
reads.** Once a merge has run in the session, `merge-gate` (briefing check)
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

Each observed failure, its cause and its fix, as a table. Open the file that
matches the host when a merge is denied or an answer did not count:

| Open | When |
| --- | --- |
| [references/failure-modes.md](references/failure-modes.md) | Any host: a finding missed on one of the surfaces, a stale approval or probe, `BLOCKED` / `UNSTABLE` merge states, rulesets and admin bypass, CI watch errors, prerequisites in the PR body, out-of-band merges |
| [references/hermes.md](references/hermes.md) | Hermes Agent: a denial naming `no briefing is present in the window`, `briefed turn does not name the PR`, `fewer than 4 of 6 items`, or `is not an approval reply`; `clarify` answers; bulk and cross-repo sets; external decision board and host approval provider answers |

## Limitations

- The three surfaces cover GitHub. A finding raised in Slack, in a meeting, or
  in a sibling issue is outside this probe — bring it into the briefing yourself.
- `mergeable` / `mergeStateStatus` return `UNKNOWN` while GitHub computes the
  merge state on a freshly-pushed PR. `UNKNOWN` is not `CLEAN`; re-poll rather
  than reporting it as mergeable.
- Grading an unlabeled bot thread is a judgement call. When it is genuinely
  ambiguous, list it in part 5 with your grade and its reason, and let the user
  overrule.
