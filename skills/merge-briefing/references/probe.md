# Step 1: Probe all three surfaces

Full procedure for [`SKILL.md` Step 1](../SKILL.md#step-1-probe-all-three-surfaces).

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

In a fork clone, `--repo` must name the fork on every probe: a bare `gh pr`
call resolves against the parent repo (see `worktree-merge-cleanup` →
*Pre-call checklist*).

If CI is pending or failing, **stop here** — do not ask yet. A briefing whose
verification rows are still moving is asking the user to approve a guess.
The same holds for a review bot still running on the current head: a draft
marked ready, a push, or an `update-branch` re-triggers it, and its summary
comment (e.g. Codex `Code Review | Running | <sha>`) is the state to read. Wait
for it in the background (pending-check count and the bot's `Running` count
both 0, counted only once the head's checks are listed, 60s interval, stated
ceiling) and end the turn; a finding it posts
after the briefing is a re-brief on a new head. A review run you or the user
dispatched after an ask went out (a remote/closed-loop reviewer that can push
fix commits) **withdraws that ask**: say the merge question is off until the
run converges, close any relayed copy of it, and do not repeat the ask in later
turns while the run is live. On convergence, check whether the head moved
before anything else.

**Read which commit each human approval covers** before writing "approved":
`reviewDecision: APPROVED` survives later pushes. Group the reviews by
reviewer and take each one's **latest** `APPROVED` review (an earlier approval
is superseded by a re-approval; a later `CHANGES_REQUESTED` or `DISMISSED`
from the same reviewer means no current approval). Compare that review's
`commit_id` with `headRefOid` and list the non-merge commits after it
(`git log --no-merges <commit_id>..<head>`) in part 5 as "not reviewed by
<human>" (see the [failure-modes](failure-modes.md) row for the command).

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
evidence for "no semantic overlap", quoted in part 5. A local `merge-tree`
exit `0` is **not** evidence that GitHub can merge: re-read `mergeable` from
`gh pr view` in the same turn and let it win. Local git fast-forwards a
submodule gitlink both sides moved (it has the submodule objects); GitHub
cannot, and reports `CONFLICTING` / `DIRTY`. When the base bumped a submodule
the PR also pins, check `git -C <submodule> merge-base --is-ancestor <PR pin>
<base pin>`; if the base pin descends from the PR's, merge `origin/<base>` into
the branch, keep the base pointer, diff the files the PR relies on between the
two pins, rerun the related tests, and push (no rewrite).

When the user asks "did you resolve the conflict / check the comments?",
re-probe live (`gh pr view --json mergeable,mergeStateStatus,headRefOid`,
unresolved threads, comments since your last read) before answering — the
base keeps moving under an open PR, and an answer from your last probe can be
wrong by the time it is read. When the pre-merge
rebase trigger fires (3+ base commits, a review round finished), bring the
base in with `gh pr update-branch <N>` (a merge commit, never a rewrite),
which restarts CI and the bot review: wait for both on the new head, re-rev
the anchor with the head that moved (when only the base moved, prove it by
equal patch-ids, `git diff $(git merge-base <old> origin/<base>) <old> | git
patch-id --verbatim` vs `git diff origin/<base>...<new> | git patch-id --verbatim`
(`--verbatim` keeps whitespace, which `--stable` strips),
quote the id in the rev's History and mark the rows not re-measured; a
`git diff <old> <new> -- <PR paths>` is non-empty after any base merge that
touched those paths, so it cannot show this), then re-run Steps 1–3 against the new
head and rebuild the briefing before asking — the findings, merge state, and
approval coverage collected earlier describe the old head.
When the base dismisses stale approvals on push (a dismissal event follows
every push on this PR), `update-branch` also costs a fresh human approval. If
the base commits touch none of the PR's paths (`git diff --stat
HEAD...origin/<base> -- <PR paths> <paths that consume them>` empty), put "merge without update-branch" vs "update-branch, then
re-approval" in the briefing as the user's choice, with that diff as the
evidence, instead of updating on your own; when it is not empty, update the
branch first.
