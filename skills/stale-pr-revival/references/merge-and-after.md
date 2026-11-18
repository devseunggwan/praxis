# Step 10: Brief the merge, and after it

Full procedure for the matching step of [`SKILL.md`](../SKILL.md).

Follow [`merge-briefing`](../../merge-briefing/SKILL.md). Write the six-part
briefing in the reply **before** the approval question — asking first and
briefing afterwards means asking twice.

- When the merge question also goes out through an async channel (a decision
  board, a chat relay), put the full six-part briefing in this session's own
  final answer in the same turn, not only the short relay text: the merge gate
  reads the transcript, so a bare "merge" reply to a relay-only question is
  blocked and costs another round trip.
- Fold any pre-merge record the team requires (a dev-channel summary post)
  into the same approval choice, then post it and link it on the PR before
  merging. After the user merges a PR themselves, close any merge question
  still open for it instead of answering it later.

- Do not ask for the merge while any PR in the batch has only tests/CI on
  its current head: listing "live check not redone" under part 3 is not a
  substitute — the user answers with "was each one actually verified?" and
  the ask is wasted. When asked that, answer first with a per-PR table
  (`verified live / partly / not`, head sha, surface) and then close the
  gaps in the same turn. A PR with no live surface at all (no preview, no
  reachable environment) may be briefed: say so in part 3 with the risk, so
  the user decides on it.
- Before the ask, read what the merge itself needs on the production base:
  the ruleset's required approvals, whether your account can bypass them
  (`gh api graphql` `pullRequest{viewerCanMergeAsAdmin}`, and the ruleset's
  `current_user_can_bypass`; when the two disagree, report both and ask
  rather than picking one), and whether the change
  touches a class the team reserves for human review (authorization rules,
  billing, deletion). When a human approval is still required, the question
  is "request a reviewer, then ask merge", not "merge now" — and ask who.
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
  to another author ([`worktree-merge-cleanup`](../../worktree-merge-cleanup/SKILL.md)
  owns the post-merge steps). Remove only your own worktree and local branch.
- After merge, check the deploy job of the base-branch CI run
  (`gh run view <id> --json jobs`) before saying the change is live. On a
  GitOps repo a green deploy job only means the image tag was written to the
  config repo; the cluster may still run the previous image for minutes. Read
  the running image (`kubectl get deploy -o jsonpath=…image`) and wait until
  it runs the image the deploy job wrote for the merge commit (tag or digest,
  read from the config repo's commit) with all replicas updated (a background script
  with a time ceiling) before any post-merge live check.
- "Is the fix actually applied?" after deploy is three separate claims; grade
  each: (1) running artifact = merge commit (image tag/replicas, the FE
  host's production deployment for the merge sha); (2) the new read surface
  answers on prod for a target known to hit it (e.g. a GET that now returns
  the new field non-empty for the affected tenant and empty for a control);
  (3) the changed write path ran. For (3), count organic prod requests to
  the endpoint since rollout, then check whether any of them met the fix's
  precondition (query the state the fix acts on for exactly those
  targets), not just whether the fix's log line appeared. Zero qualifying
  requests means UNVERIFIED, not PASS; say so and offer a controlled trigger
  on the affected target as a decision (it is a prod write), with the
  one-line recovery if it fails. Once approved, fire it through the product
  UI in the user's logged-in browser (it exercises FE and BE in one pass)
  and grade it with three readings that must agree: the fix's own log line
  naming the target ids, the target row's `updated_at` equal to that log
  timestamp with the preserved value, and sibling rows of the same object
  unchanged. Then re-read the row after the job the trigger started
  finishes: the job may write the same column again, so the request-time
  PASS is not the final one.
- When the production base requires approvals your account cannot bypass
  (`viewerCanMergeAsAdmin` false), look for the repo's own merge slash
  command workflow (`.github/workflows/*merge*`, e.g. a `/merge force`
  comment) before declaring the merge blocked; using it on the user's
  explicit merge instruction is the merge, so it still needs the briefing
  and per-PR approval, and the report says the required review was skipped.
- A post-merge live check "on <named internal account>" needs two probes
  before planning: that your login can act on that tenant (membership/owner
  rows, not just that it exists), and that the target object is in the
  scheduled path the decisive scenario relies on (e.g. it appears in the
  daily batch's recent runs). Without the second, a pause/skip scenario has
  no positive control: run what the target supports, mark the rest
  unmeasured, and ask for access to a tenant that has it.
- Before writing to a production tenant for the check, snapshot the rows you
  will touch, read the write path for side effects (deletion DAGs, events,
  mail) on that exact transition, restore at the end only rows that still
  hold the value you wrote (report any row someone else changed meanwhile),
  and re-read the rows to prove the end state; report any column you left changed (e.g. `updated_at`). A
  skipped `deploy` job is not a failure when the repo ships through another
  job (a DAG-bundle publish): read which job carries the release before
  judging. Compare the merged `headRefOid` with the head you verified — the
  user or a review pipeline may have pushed after your report — and list any
  extra commits (`git log <verified>..<merged-head>`) in the post-merge note.
- Re-read unresolved review threads right before each merge command, even
  with every check green: a review bot posts a new round on the head your
  last base merge produced, and `mergeStateStatus` stays `BLOCKED` on it.
  Fix it on the branch (red/green the new test), reply `Fixed — <sha>`,
  resolve, and wait for both CI and the bot's verdict on that head (its
  approval reaction or completed summary for the new sha) before merging:
  the bot reviews every push, so the fix commit itself can draw the next
  finding, and a green-CI merge lands it unread.
- A blanket instruction covering several PRs ("close the old one and merge
  now") approves the intent, not each merge command. The merge gate still
  wants the six-part briefing in the reply and a per-PR approval answer, so
  for every PR after the first, write its briefing in the same reply that
  reports the previous merge and ask for that PR alone. Issuing the merge
  command without the briefing gets blocked and costs a round trip.
- When the PR fixes a defect that reverts a manual data correction (a
  toggle, a flag, a disabled row), re-read the corrected rows once the fix
  runs in production. Any user action that hit the old code between the
  correction and the rollout has reverted it silently. When a row flipped,
  find the request in the logs by its `updated_at` window (endpoint, actor,
  run id), report it at the top, and ask before re-applying the correction:
  that is a production write, and sibling rows the same request flipped may
  have no recorded prior value.
- When one required job fails right before the merge, read its assertion
  from the check-run annotations
  (`gh api repos/<o>/<r>/check-runs/<job id>/annotations`): they are there
  the moment the job fails, while `gh run view --log-failed` refuses until
  the whole run finishes and the raw job-log API stops on escape sequences.
  Call it unrelated only with two probes: the failing test's path is absent
  from `git diff --name-only <base-remote>/<base>...HEAD`, and the base's
  recent CI runs are green. Then `gh run rerun <run id> --failed` and merge
  on a green rerun; a second failure is treated as the PR's to fix.
- Re-read each revived PR's `state` before listing it as "awaiting approval"
  in a later status: the user often closes revived PRs as no longer needed
  (a migration made them moot) without telling this session, and a stale
  list reports a closed PR as pending.

Done when: the briefing is in the reply text and ends with the approval
question, or the merge has happened after an explicit approval and its base CI
run is reported.
