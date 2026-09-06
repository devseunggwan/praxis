# PreToolUse Unenforced Mandatory Step Advisory

Supported hosts: claude

`hooks/advisory-nudge/unenforced-step-advisory/impl.py` fires on every
PreToolUse(Bash) event. At four action points it checks whether the MANDATORY
workflow step that is due there was actually performed this session, and when
it was not, writes a stderr advisory naming the step. It never blocks in
default mode.

## Why this exists

Issue #1064. In the 2026-08-20 session (PRs #1058 and #1061), every workflow
step that carried a hook was followed and every MANDATORY step that carried
none was skipped. The discriminator of compliance was the presence of an
enforcer, not the weight of the rule.

The observation, all three counts zero:

```text
oh-my-claudecode:code-reviewer   → session Agent dispatches 0
PR #1058 pre-PR / pre-merge rebase → 0
in-flight PR check               → `gh pr list --state open` 0
```

PR #1058 ran 04:56 branch cut → 06:21 PR → review rounds + 4 commits → 22:52
merge. The pre-merge rebase trigger condition ("a review round finished") did
fire and nothing ran. The merge came out CLEAN, which is the point: **when the
outcome is good, a skipped step never becomes a retrospect candidate.**

A step with no hook also leaves no `is_error` in the transcript, so the
friction-signal scan a retrospect runs is structurally blind to it — this case
was recovered by an external critic, not by the session. What is missing is not
enforcement strength but a **firing point**. An advisory creates one, and
observation can start; whether to escalate any predicate to a block is a
separate decision to be made against measured noise.

## What is advised

| Trigger command | Predicate (fires when ALL hold) | Step named |
| ----------------- | --------------------------------- | ------------ |
| a content commit (`git commit`; a segment carrying `--amend` **as an option** is skipped) | no `oh-my-claudecode:code-reviewer` Agent dispatch this session **and** `praxis:codex-review-wrap` WAS invoked | `oh-my-claudecode:code-reviewer` before commit |
| `gh pr create` | `HEAD..<base>` is non-empty — the branch is behind | pre-PR rebase |
| `gh pr merge` | the same, re-measured at the merge point | pre-merge rebase |
| `git worktree add`, `cmux new-workspace`, `cmux workspace create` | no open-PR enumeration this session | in-flight PR check |
| anything else | silent pass-through | — |

### Why each predicate measures what it measures

**The rebase predicates do not ask whether a rebase ran.** They measure
`git rev-list --count HEAD..<base>` — how many base commits this branch does
not have, which is what the rebase is *for*.

Two failure modes rule out the transcript-absence oracle here, in opposite
directions. It over-fires on a branch cut from `origin/main` minutes ago, where
the rebase is a no-op. And it under-fires far worse: "a rebase happened" is a
sticky session-wide boolean, so rebasing once before opening the PR silences
the merge-time check for the rest of the session however far the base moves
during the review rounds. That is the issue's own scenario — PR #1058 was cut
at 04:56 and merged at 22:52 with review rounds and four commits in between.
The same boolean also counts a rebase that never completed: `false && git
rebase`, one aborted on a conflict, a bare `git rebase --abort`.

Distance-from-base has neither failure mode, because it re-measures the world
rather than remembering an intention. The base is resolved from
`refs/remotes/origin/HEAD`, falling back to the first of `origin/main`,
`origin/master`, `origin/prod` that resolves. Both probes are local reads;
neither touches the network, and these two triggers read the transcript not at
all.

**The review predicate.** The commit point already carries
`block-commit-without-codex-review`, which denies a commit with no
`praxis:codex-review-wrap` pass. Firing this advisory in that same turn adds a
second message to a call that is being blocked anyway — noise with no decision
attached to it. So the advisory requires codex-review-wrap to have *already*
run: the surviving case is exactly the one the issue observed, where the
enforced review happened and the unenforced one did not.

The row `Code review (general, MANDATORY before commit)` in
[`model-routing-advisory/spec.md`](../model-routing-advisory/spec.md) names the
`oh-my-claudecode:code-reviewer` agent, and the row below it names
`praxis:codex-review-wrap`. They are two separate mandatory passes; only the
second has a gate.

**The in-flight predicate.** An open-PR enumeration is `gh pr list` or
`gh search prs` with no `--state`, or with `open` / `all`. An explicit
`merged` or `closed` state does not clear it — that is the exact miss the issue
records, where the session's first `gh pr list` was `--state merged`. Both the
long and short spellings are read (`--state`, `--state=`, `-s`): reading only
the long one lets `gh pr list -s merged` clear the very predicate that miss
motivated. The message reports the live `git worktree list` count, so the
sibling worktrees that were on screen and unread become a number.

## Detection

1. `tool_name == "Bash"` — every other tool exits 0 silently.
2. The command is tokenized with `_hook_utils.safe_tokenize` and walked with
   `iter_command_starts`, so a trigger chained behind a separator or wrapped in
   an env prefix is still classified. `strip_prefix` peels `env FOO=1`, `sudo`,
   and shell keywords, so no environment-variable prefix exempts a trigger.
   Global options that consume the **next** token are skipped with their value
   (`git -C <path>`, `git -c k=v`, `gh -R owner/repo`, `gh --hostname`) — drop
   only the flag and its value lands where the subcommand should be, and the
   match fails silently. `gh`'s are inherited cobra flags valid in any position,
   so they are skipped between the group and the verb too (`gh pr -R o/r
   merge`). `--help` / `--version` run no subcommand at all and classify as
   nothing.
3. Only the facts the matched trigger consumes are collected, and the scan
   stops as soon as they are settled. The `review` trigger needs a
   review-agent dispatch and a codex-review-wrap Skill call; `in-flight` needs
   an open-PR enumeration; the two rebase triggers need nothing from the
   transcript and never open it. This is a cost decision, not a style one:
   tokenizing every Bash command of a 46MB session costs 1.8s, and all
   PreToolUse(Bash) members share one dispatch deadline, where one slow member
   starves every later one. Reading is streamed via
   `_transcript.iter_transcript`, never materialized, and only lines carrying
   the quoted name of a tool the trigger's facts read (`"Agent"` / `"Task"` /
   `"Skill"` for `review`, `"Bash"` for `in-flight`) are parsed at all (issue
   #1278): every fact lives in a `tool_use` block with one of those names, so
   a line without it is rejected before `json.loads`. That matters most in
   the session the advisory is for — one where no review ran, so nothing
   ever settles and the scan reaches EOF on every commit.
4. Subagent transcripts (`<session-dir>/subagents/agent-*.jsonl`) are scanned
   alongside the root one. An Agent dispatch made *inside* a Task-dispatched
   subagent is recorded only in that subagent's own JSONL, so a root-only scan
   under-reports work that actually happened — the same blindness
   `block-commit-without-codex-review` documents for issue #730. A subagent
   file the resumable scan has not caught up with yet keeps the advisory
   silent (its unread tail may still settle the fact); an unreadable one is
   skipped.

The predicates key on an **absence** in the transcript rather than on the
presence of a marker, so the advisory cannot be cleared by adopting whatever
vocabulary a marker scan would look for. The clearing evidence is the work
itself.

## Modes and bypass

| Setting | Effect |
| --------- | -------- |
| default | stderr advisory, exit 0 — the call proceeds |
| `PRAXIS_UNENFORCED_STEP_STRICT=1` | a fire becomes a hard block (exit 2) |
| `PRAXIS_UNENFORCED_STEP_SKIP=1` | silent, always |

Both env vars are read from the hook process's own environment, so an inline
command prefix does not reach them — set them in the host's session/settings
env, the same contract the sibling gates document.

## Fail-open conditions

Each returns 0 with no output, per the ETHOS fail-open invariant:

- malformed payload, non-Bash tool, empty command
- unparseable command (unbalanced quotes → `safe_tokenize` yields nothing)
- missing or unreadable transcript, or one whose scan has not caught up yet
  (more than 50 MB of unread bytes this call)
- the rebase predicates when the base ref cannot be resolved or
  `HEAD..<base>` cannot be counted — a detached or unborn HEAD, no origin, git
  unavailable, or no probe budget left. An unmeasured premise produces silence,
  not a nudge.

Every `git` probe an invocation spawns shares one `shared_probe_deadline`, so
their sum is bounded by the hook's manifest timeout instead of each reading the
budget alone and overrunning the group.

## Noise measurement

Issue #1064's verification plan step 3 requires the advisory be run through one
complete normal flow and the fire count reported. Measured by replaying all
four trigger commands against the implementing session's own live transcript in
its real worktree: **1 fire across the 4 trigger points.**

The three silent ones are silent for the documented reasons — the branch was
cut from a freshly-fetched `origin/main` and sat 0 commits behind it, so both
rebase predicates measured no distance, and the review predicate deferred to
`block-commit-without-codex-review`. The one fire was the in-flight predicate,
and it was correct: that session had no open-PR enumeration of its own.

### A false clear this measurement caught

An earlier revision extracted command-substitution spans from the **raw**
command text. Three of that session's own fixture-writing commands quoted
`gh pr list --state open` inside a heredoc body, and the scan read the quoted
text as an executed enumeration — the in-flight predicate went silent for the
rest of the session. A heredoc body is data the shell hands to another program,
never commands it runs, so spans are now taken from the heredoc-stripped text.
Without re-running the measurement after each change the fire count would have
read `0/4` and been reported as low noise, when it was a scan defect.

### Scan cost

Only the facts the matched trigger consumes are collected, and the scan stops
once they are settled. On a 47.9MB transcript: `review` 0.200s, `pre-pr`
0.000s (reads nothing), `in-flight` 0.005s. Collecting all four facts
unconditionally cost 1.77s on the same file — every PreToolUse(Bash) member
shares one dispatch deadline, so one slow member starves every later one.

Those numbers are the settling case. When nothing settles the scan reads to
EOF, and there the parse is the cost: 42,000 `json.loads` on a 36 MB session
was 0.44s per commit. Parsing only the lines that name a tool the trigger's
facts read (#1278) keeps the unsettled walk at the substring-scan floor.

The walk is also resumable: each trigger keeps a cursor per transcript file
under `~/.praxis/cache/`
(`scan-unenforced-step-advisory-<trigger>-<file>-<session_id>.json`, swept
with the cache TTL and spared for the live session) holding the byte offset
of the last complete record read and the facts settled so far. The session
the advisory exists for — one where no review ever ran, so nothing settles —
is exactly the one that used to read to EOF on every commit; it now reads
only the bytes appended since the previous commit. The 50 MB bound is a
budget per call, not a ceiling on the session: a transcript past it stays
silent for the few commits it takes to catch up, then costs its delta. A
payload without a `session_id` scans without persistence, under the same
budget.

### Known limitation — delegated sessions

The in-flight fire above is a true positive with a caveat worth stating. The
session was dispatched by `cmux-delegate`, and the open-PR enumeration had been
performed by the **delegating** session, whose transcript is a different file.
A subagent's work is visible (its JSONL sits under this session's own
`subagents/`); a delegating session's is not, because a cmux dispatch starts a
peer session, not a child. So a delegated worker that inherits a completed
in-flight check in its handoff will still see this advisory once at its first
`git worktree add`. That is one message, at exit 0, in a session where the
check was in fact performed elsewhere — the cost of keeping the predicate keyed
on evidence this session can actually read, rather than on a handoff claim it
cannot verify.

### Not measured — repeat firing

There is no per-session dedup: a session that skips a step and then runs six
commits gets the message six times. The measurement above replays each trigger
once, so it cannot see that axis at all. Left deliberately: dedup state would
have to survive a compaction to be correct, and the right threshold is a
question for measured data rather than a guess. If the repeat volume proves to
be the dominant cost, that is the first knob to turn.

## Tests

`tests/hooks/advisory-nudge/test_unenforced_step_advisory.sh` — synthesizes
PreToolUse payloads against a temporary git repo and asserts advise / silent /
strict-block for each of the four predicates, plus the fail-open conditions.
