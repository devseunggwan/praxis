# Bot dependency PRs (dependabot / renovate)

Detail for [`stale-pr-revival`](../SKILL.md) when the stalled PRs are
version bumps opened by a bot — most often GitHub Actions `uses:` tags. Triage
each before touching it, then revive the needed ones one PR each. Do not
consolidate several bumps into one PR unless asked.

## Step 1: Triage against the base, not the PR

Per bumped dependency:

```bash
git grep -nE '<action>@' <base-remote>/<base> -- .github/
```

`<base-remote>` is the remote for the base repository, resolved in `SKILL.md`
Step 1 (usually `origin`; fetch it first).

When the workflows pin actions by commit SHA, the version is only in the
trailing comment (`uses: <action>@<sha> # v1.2.3`); read the base state from
that comment, and treat a SHA with no comment as unknown until resolved.

| Base state | Verdict |
| ------------ | --------- |
| Already at or above the target version everywhere | Superseded — recommend closing, do not revive |
| Bumped in some files only | Still needed for the rest |
| Not bumped | Needed |

Report one table: PR URL · bump · base state · verdict.

Done when: every open bot PR has a row with a verdict.

## Step 2: Read the release notes of each major jump

```bash
gh api repos/<owner>/<action>/releases/tags/<tag> --jq .body \
  | grep -iE 'break|node|runner|remove'
```

- A runtime requirement (for example "Node 24 / runner ≥ x.y.z") is evidenced
  by an action on that runtime that already runs green on the base, **on the
  same runner label**. A separate runner (for example a self-hosted ARM64
  label) needs its own proof.
- For a removed or renamed input, grep the workflows for the old input names.

Done when: every major jump lists its breaking items and, for each, the
evidence that this repository is or is not affected.

## Step 3: Revive

Follow `SKILL.md` Steps 3, 4 and 8 on the bot branch, with these specifics:

- Resolve workflow conflicts with `git checkout --theirs <file>` (base side),
  then re-apply the bump as a version-string replace across **every**
  `.github/` usage (the SHA and its `# v…` comment together when pinned by
  SHA). Bots often bump one file, so a merged bot PR leaves the
  other workflows behind.
- Commit the merge, then one `ci(deps): bump <action> to <ver> in remaining
  workflows` commit.
- Check the effective diff is only tag lines:
  `git diff <base-remote>/<base>...HEAD` must show nothing but `uses:` changes.

Done when: `git diff <base-remote>/<base>...HEAD` contains only `uses:` tag lines
and every `.github/` usage of the action is on the target version.

## Step 4: Issue-link checks

Bot PRs have no issue. Use the check's documented bypass — read its workflow
(for example a `<!-- no-issue -->` body marker set via `gh pr edit
--body-file`). Never create an issue just to satisfy it.

Edit the body **before** pushing. The push's `synchronize` run reads the body
from its event payload; if the body was still old, that run fails, a re-run
reuses the same payload, and the red row stays on the PR next to the passing
`edited` run.

Done when: the body carries the bypass marker and the first CI run on the new
head saw it.

## Step 5: Hand off to CI

Pushing human commits stops the bot from auto-rebasing that PR — say so in the
report. Start one background `gh pr checks <N> --watch` per PR and end the
turn; brief each merge separately once green.

Done when: every revived PR has a watcher running and the report names the
bot auto-rebase loss.

## Step 6: Approving (when asked to verify-then-approve each)

One PR at a time. Pin the head SHA in the review body and skip the review if
the head moved. The body carries four parts: scope · executed evidence ·
breaking changes vs the inputs this repo passes · what stays unverified.

```bash
gh pr review <N> --approve --body-file <review.md>
```

Collect the evidence per PR with these reads (formerly a helper script; run
them directly):

1. **State:** `gh pr view <N> --json headRefOid,mergeable,mergeStateStatus,reviewDecision`.
2. **Diff shape:** every added or removed content line, unindented ones
   included, with the `diff --git` / `---` / `+++` headers left out —
   `gh pr diff <N> | awk '/^diff --git /{h=0} /^@@/{h=1; next} h && /^[+-]/' | sort | uniq -c`.
   The only changed lines should be the tag swap, counted per file usage, so
   `... | grep -vE '^[+-][[:space:]]*(-[[:space:]]+)?uses:'` on the same
   stream must print nothing. (`grep '^[+-] '` misses a change to a top-level
   key such as `name:`, which has no space after the marker.)
3. **Checks:** `gh pr checks <N>`.
4. **Executed?** A green check does not prove the bumped step ran. List the
   successful runs on the head
   (`gh run list --commit <head-sha> --json databaseId,conclusion --jq '.[]|select(.conclusion=="success")|.databaseId'`)
   and grep each one's log for the download line:
   `gh run view <id> --log | grep -m1 -F "Download action repository '<action>@<ref>'"`,
   where `<ref>` is exactly what the `uses:` line pins — the commit SHA for a
   SHA-pinned workflow, not the tag in its comment.
   A hit (with its `SHA:`) proves execution; record the run id.
5. **Not executed in PR CI** (deploy job, image build on base push,
   slash-command workflows): cite a sibling workflow on the base that already
   runs the new version with the same inputs and secrets, and read the
   upstream `action.yml` at the tag —
   `gh api "repos/<action>/contents/action.yml?ref=<ver>" --jq .content | base64 -d | grep -nE 'required|deprecationMessage|using:'`
   — for `required` / `deprecationMessage` on the inputs this repo passes.
   List the step as unverified-until-first-run.
6. **Independence:** if `gh api user --jq .login` is the account that pushed
   the resolution, say the approval is not independent.

Done when: each approved PR's review body carries the pinned SHA and all four
parts, and every not-executed step is named as unverified.

## Step 7: Explain what each bump changes

Users ask this before merging. Separate real fixes from warning cleanup, and
back both with this repository's own logs, not just release notes.

- **Node runtime:** read the base CI's check-run annotations —
  `gh api repos/<owner>/<repo>/check-runs/<job_id>/annotations --jq '.[].message'`.
  GitHub already force-runs Node 20 actions on Node 24 and lists them there
  ("…target Node.js 20 but are being forced to run on Node.js 24: <actions>").
  A pure runtime bump only removes its action from that list; prove it by
  comparing the same job's annotation before and after the merge.
- **Real fixes:** grep the action's own recent run log (`gh run view <id>
  --log`) for symptoms (state not restored, 409 cache conflicts, `No more
  operations left`), then match them to a release-note fix by opening the
  linked upstream issue. Label the match an inference until the first
  post-merge run confirms it.
- **New warnings:** a bump can trade one warning for another (a newly
  deprecated input). Grep a sibling workflow already on the new version for
  `##[warning]` and report the swap.
- List the old-runtime actions that remain after all merges.

Done when: each bump has a fixes / warnings / remaining line, each claim
marked log-backed or inferred.

## Step 8: Merge order for several bumps

One PR per turn — the merge briefing covers one merge. Wait for each merge's
base-branch CI (background `gh run watch <id> --exit-status`) before briefing
the next.

1. First, PRs whose bumped step already executed in PR CI (lowest risk), then
   independent ones (cron-only workflows).
2. Last, PRs whose step runs only in a shared post-merge job (deploy). Order
   those by step position, **later step first**: a broken earlier step (token
   generation) stops the job before a later one (QEMU setup) runs, which would
   leave the later bump unverifiable.
3. Check `on.push.paths` on build workflows. Path-filtered jobs do not run on
   a workflow-only merge — say they stay unverified until the next matching
   push.
4. **Pre-merge base refresh.** If the base gained commits since the last
   merge-in but none touch the PR's files, say so in the briefing headline
   instead of re-running CI. If one does (typically an earlier PR of the same
   batch editing the same workflow), run `gh pr update-branch <N>` — a merge,
   not a rewrite; the approval survives when the ruleset does not dismiss
   stale reviews on push — and brief only after the new head is green.
5. **Overlap the waits.** Start the next PR's `update-branch` and checks watch
   while the previous merge's base CI is still running. A deploy job can
   queue well behind the test jobs, so a long wait is not a hang — read
   `gh run view <id> --json jobs` step statuses before calling it stuck.

Done when: the merge order is written down with the reason for each position,
and every path-filtered or deploy-only step is marked unverified or verified.
