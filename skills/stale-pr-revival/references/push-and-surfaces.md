# Step 8: Push and update the PR's own surfaces

Full procedure for the matching step of [`SKILL.md`](../SKILL.md).

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

Every revival status report — not only the merge briefing — opens with a
per-PR line of how deep verification went on the current head (`live / partly
/ tests+CI only`, head sha, surface). A report that lists pushes, CI, and
thread work without that line gets "did you actually verify?" back.

When an auto-review bot keeps pushing fix commits after your push, start the
live proof now anyway (a background worker on a preview) rather than parking
it behind convergence: the user asks "did you actually verify?" again on every
report until it runs. Tell the worker to record the head it deployed and, when
the head moves mid-run, fast-forward, redeploy, and measure every scenario on
the new head. Never register "wait for review, then ask merge"; the order is
converged head + live proof on that head → anchor revision → merge ask.

A live-proof worker that writes to a shared dev database snapshots every row
it will touch to a file first, restores them at the end (status through the
API, timestamps by SQL to the snapshot values) only where a row still holds
the value the worker wrote (a row that differs was changed by someone else:
leave it and report it), and proves the restore with a snapshot diff of 0
over the restored rows plus an independent read; it deletes the preview and
confirms the namespace is gone. Scope the snapshot from the write path, not
from the tables the PR touches: read which rows the exercised endpoint can
delete and list every table that cascades from them (`information_schema`
foreign keys with `ON DELETE CASCADE`), because a re-insert by primary key
restores the parent but not cascaded children outside the snapshot. Any job
the scenario triggers (a dev DAG run that stays queued because the DAG is
paused) is reported with its run id, then marked failed or left as the user
decides, so it cannot fire later against restored rows. Ask it for a per-scenario table
(PASS(live)/FAIL/BLOCKED, evidence line, head sha) so the anchor rows come
straight from it.

When the user's writing rules ban the em dash, the anchor needs none: the
anchor gate reads ``### Verification - `<sha>` (rev N)`` and an `Evidence N -` prefix
with a hyphen as well as with `—` (`anchor-comment-gate/impl.py`, the
`[—-]` in its heading and evidence patterns).

Done when: the push landed, the body no longer states a fact the new head
contradicts, the change comment is posted, and CI on the new head has a final
status.
