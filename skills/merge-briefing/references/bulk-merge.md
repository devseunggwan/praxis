# Bulk merge (several PRs, one answer)

Full procedure for [`SKILL.md` Bulk merge](../SKILL.md#bulk-merge-several-prs-one-answer).

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
| section heading | a markdown heading, or a line bold end to end, naming **exactly one** of the PRs as `PR #N` (`### PR #833 — <title>`), all at the same level | a heading naming two PRs belongs to neither; decorating the heading (`### ③ backend #833`) instead of the `PR #N` form left the merge scored as "briefed turn does not name the PR". The punctuation after `PR #N` is free (`—`, `:` and `-` all parse, so follow the user's punctuation), but the title must name no other number: `### PR #833: fix #999 follow-up` names two PRs and belongs to neither; a bold lead inside a bullet (`- **리스크**: #999 …`), a deeper sub-heading, or a `#` line in a code fence is content, not a heading |
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

Step 6 still applies to each PR: merge from the base worktree without
`--delete-branch`, confirm `MERGED`, then remove that PR's head worktree, and
report every PR with its URL.
