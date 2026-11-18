# Steps 2 and 3: grade findings, carry the anchor's gaps

Full procedure for [`SKILL.md` Steps 2 and 3](../SKILL.md#step-2-grade-every-finding-on-all-three-surfaces).

## Step 2: Grade every finding on all three surfaces

An open thread count is not a blocker count, and the thread surface is not the
whole corpus. Grade each finding Step 1 collected — inline thread, conversation
comment, and review body alike — by **the decoration, not the label**:

| First line of the finding | Blocks the merge? |
| --- | --- |
| `issue (blocking):` / `suggestion (blocking):` | Yes — the merge waits |
| `issue (non-blocking):` / `suggestion (non-blocking):` | No |
| `nitpick:` / `note:` | No — unresolved by design, unless the base requires conversation resolution (see the [failure-modes](failure-modes.md) row starting "`mergeStateStatus: BLOCKED` with CI green"): then reply, resolve, and re-read the merge state before the ask |
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

Reconcile the CodeRabbit count before grading is done: count its findings by
the `` `LINES`: ** `` markers in the bodies and match the total the review
header states (e.g. `Major comments: N`). Answer the set in **one**
conversation comment grouped `Fixed — <sha> (n)` / `Not fixed (n)` /
`False positive (n)`, whose counts sum to that total.

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

## Step 3: Carry the anchor's `Unverified` gaps

Open the PR's verification anchor and read its `Unverified` list. A gap written
as "머지 후 실전송으로 확인" dies with the PR unless something outlives it.

- Surface exactly those gaps that **named a next action** — a gap with no
  stated next action is not carried.
- Ask, as part of the briefing, whether to open a follow-up issue.
- Write the answer into the anchor's final rev as `Carried: #N` or
  `Carried: none — <사유>` **before** the merge. Do it in the briefing turn,
  before the ask, whenever the carry target is already known (the follow-ups
  are recorded as a comment on the issue that outlives the merge): an anchor
  rev deferred to the answer turn is easy to skip once the merge is the only
  thing left, and on Hermes it is also the pre-merge write that opens the
  gate gap. Right before `gh pr merge`, `grep -c '^Carried:'` the anchor body
  you fetch; 0 means write the rev first. A `Carried:` line added after the
  merge is a reported miss, not a fix.
- Writing that final rev needs `gh` — a session whose only GitHub surface is
  the MCP server cannot update a comment body when that server's active tool
  list exposes no comment `update` (confirm before assuming). There, put the
  `Carried:` line in the merge commit or the PR body instead, and never in a
  second anchor comment. See
  [`anchor-comment-gate/spec.md` → Procedure for a gh-less session](../../../hooks/preflight-gate/anchor-comment-gate/spec.md#procedure-for-a-gh-less-session).
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
