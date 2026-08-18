# PostToolUse PR Thread Resolve Advisory

Supported hosts: all

`hooks/advisory-nudge/pr-thread-resolve-advisory/impl.py` runs on `PostToolUse`
for `Bash` and, after a successful `git push`, looks up the open PR on the
pushed branch and lists its **unresolved review threads**. It emits a **stderr
advisory** (never a block in the default mode) naming the reply-and-resolve
form each thread is owed.

### Why this exists

`Review Comment Reply & Resolve (MUST)` states that a review comment you acted
on gets a reply in its own thread and that thread gets resolved: fixing the code
is half the action, because until the reply lands the reviewer's surface still
reads "open", and to whoever merges an unresolved thread is indistinguishable
from an unhandled one.

That rule had no hook behind it — enforcement strength `E0`, discipline only. In
practice it fired only when a human typed *"if fixed comments, reply and resolve
it"* at the agent, on every single PR (praxis issue #1039). The recurring manual
prompt is the measurement: the rule was not self-firing. This hook moves the
reminder to the moment the fix actually lands — the push that carries it — which
is exactly when that sentence used to be typed.

### Why GraphQL and not `gh pr view`

Inline review threads are a third surface. `gh pr view --json comments,reviews`
returns the conversation timeline and review bodies and does **not** contain
them, so a hook built on it would report "no open findings" on a PR with six
open inline threads. `reviewThreads` over `gh api graphql` is the only oracle
for this claim.

### Two groups, because an open-thread count is not a blocker count

The first comment's body is graded by its [Conventional
Comments](https://conventionalcomments.org/) label:

| First-comment head | Group |
| ------------------ | ----- |
| contains `(blocking)` | needs a reply |
| no recognizable label (bot output, pre-vocabulary thread) | needs a reply |
| contains `(non-blocking)` | for reference |
| starts with `nitpick:` / `note:` | for reference |

`nitpick:` and `note:` threads are unresolved *by design*; grading every open
thread as a blocker is how a merge stalls on a nit. An unlabeled thread is not
auto-demoted — silence about a grade is not a low grade, so a human reads it.

### What is emitted

Advisory text on stderr, exit 0 by default. `PRAXIS_PR_THREAD_ADVISORY_STRICT=1`
exits 2 instead, and only when the needs-a-reply group is non-empty.

| Condition | Result |
| --------- | ------ |
| Push succeeded, open PR on the branch, >=1 unresolved thread | `[pr-thread-resolve-advisory]` advisory listing both groups |
| More than 100 threads on the PR | advisory + explicit truncation note (no silent cap) |
| First 100 threads all resolved, more pages unread | truncation-only advisory — "no unresolved threads" is not a claim this hook can make over a surface it did not finish reading |
| All review threads resolved | silent |
| No open PR for the pushed branch | silent |
| Two or more open PRs on the same head branch name | silent — see *Which PR* below |
| Push segment did not run (`true \|\| git push`) or was rejected | silent (nothing landed) |
| Push landed but the surrounding command failed (`git push && false`) | advisory — the push is on the remote regardless of the command's exit |
| Tool call interrupted | silent |
| Non-`git push` command, or no `git`/`push` substring | silent |
| Push shapes the shared parser skips — see `SKIP_FLAGS` and `resolve_targets` in [`_lib/_git_push_target.py`](../../_lib/_git_push_target.py) for the full set | silent |
| `gh` missing / unauthenticated / rate limited / network error / timeout | silent (fail-open) |
| GraphQL response unparseable or shaped unexpectedly | silent (fail-open) |
| Malformed JSON stdin, non-Bash tool | silent (fail-open) |
| `PRAXIS_PR_THREAD_ADVISORY_BYPASS` set | silent (opt-out) |

### Which push, and which PR

The whole Bash command's exit status answers the wrong question: `true || git
push` exits 0 with no push, and `git push && false` exits non-zero with the
commit already on the remote. The push's own output is what separates them, so
that is what the hook reads. When the response carries no output field at all,
it falls back to the exit status — weaker, but silence there would disable the
hook wherever output is not surfaced. An output field that is present and empty
is not that case: it is evidence the push produced nothing, so nothing ran.

`gh pr list --head` cannot filter by owner (`"<owner>:<branch>" syntax not
supported`), so two forks can hold open PRs on one base repo under the same
branch name. The hook asks for two rows and speaks only when exactly one comes
back; naming the wrong PR's threads is worse than naming none.

The PR URL is matched against `github.com`; a GitHub Enterprise host does not
match and the hook stays silent there. Deliberate — this install targets
github.com, and the fail-open path already covers the miss.

### Untrusted strings

Paths, author logins and comment bodies are written by whoever opened the PR.
Control bytes are stripped from all three before rendering — an ANSI/OSC
sequence reaching the terminal can rewrite the lines above it or hide a link
target behind different text.

A failed thread lookup returns `None`, not an empty list, so a network error can
never be consumed as "nothing unresolved".

### How the branch is resolved

The `git push` argv parsing is shared with `advisory-nudge/push-remote-ref-verify`
via `_lib/_git_push_target.py` (`resolve_push_target`), which honours global
`git -C <dir>`, value-taking flags, `local:remote` refspecs, `HEAD`, and bare
pushes resolved through `@{upstream}`. Anything it cannot parse confidently
returns `None` and this hook stays silent — a wrong branch name would produce a
confidently wrong thread list.

### Environment variables

| Variable | Effect |
| -------- | ------ |
| `PRAXIS_PR_THREAD_ADVISORY_BYPASS` | any non-empty value disables the hook |
| `PRAXIS_PR_THREAD_ADVISORY_STRICT` | `1` -> exit 2 when the needs-a-reply group is non-empty |
| `PRAXIS_PR_THREAD_GH_TIMEOUT` | per-`gh`-call timeout in seconds (default 5) |

Two `gh` calls at 5s each plus interpreter startup stay inside the 20s manifest
budget.

### What this hook does not do

It never replies and never resolves. Both are writes to an external surface and
belong to the human approval path; the hook only surfaces what is open.
