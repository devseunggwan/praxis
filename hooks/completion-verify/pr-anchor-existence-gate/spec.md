# Stop PR-Anchor-Existence Gate

Supported hosts: all

Stop hook. Fires when `gh pr create` succeeded this session and the resulting
PR received no verification anchor comment — the FIRST Stop where the
condition holds is an advisory nudge, the SECOND and every later Stop where it
still holds is a block.

- **Event:** Stop
- **Role:** completion-verify
- **Default tier:** advisory once per session, then block
- **Bypass:** `PRAXIS_PR_ANCHOR_BYPASS=1`
- **Force advisory (never escalate to block):** `PRAXIS_PR_ANCHOR_ADVISORY=1`

## Why this exists

The PR-creation gates check an anchor's *quality*: `anchor-comment-gate`
(#947, fixed by #996) checks shape, SHA freshness, and per-row evidence — but
only once an anchor is ABOUT to be posted. No other hook that touches
`gh pr create` requires that an anchor exist in the first place, so a session
can open PR after PR and leave every one without a Post-PR Empirical
Verification anchor comment while nothing fires; the user finds out when they
ask. (The motivating session did exactly that across four PRs, #1083-#1086.)
This gate closes exactly that gap: **existence**, not quality — see
[`anchor-comment-gate/spec.md`](../../preflight-gate/anchor-comment-gate/spec.md)
for the shape/freshness checks this gate deliberately does not duplicate.

## What is detected

Whole-transcript scan (mirrors `pr-report-destination-gate`, #832): the `gh pr
create` and the anchor post are routinely many turns apart, so a bounded tail
would miss the create.

The scan is **resumable** (#1237): the reduction it keeps (pending creates and
posts, their result status, the PR URLs a create returned) is persisted with a
byte offset in the session-keyed cache entry
`cache/stop-scan-pr-anchor-existence-gate-<session_id>.json`, and each Stop
parses only the bytes appended since. The cursor is dropped — full re-scan —
when the transcript is a different inode, has shrunk, or the offset no longer
sits on a line boundary; a payload without `session_id` scans in full.

### Created PRs — non-draft `gh pr create` successes

- `gh pr create` without `--draft`/`-d` **in that invocation's own segment** of
  a (possibly compound) command — a draft flag on one call in
  `gh pr create --draft && gh pr comment 9` never leaks into a sibling call.
- A failed create (`tool_result.is_error`) does not count.
- The PR number is read from the tool_result's URL; a result carrying zero or
  more than one `pull/<N>` URL leaves the create unresolved (skipped, not
  guessed).

### Posted PRs — a successful anchor-shaped post targeted these

- `gh pr comment <N>` (or a `…/pull/<N>` URL target) — the target must be the
  token directly after the subcommand; a flag before the positional
  (`gh pr comment -b "…" 123`) is an accepted miss, same trade-off as
  `pr-report-destination-gate`.
- A write-method `gh api` call (`--method`/`-X` explicit, or a body field
  when no method is given — `gh api` defaults to GET, verified via
  `gh api --help`) against `issues/<N>/comments` or `pulls/<N>/comments`.
- A failed post does not count.

### Anchor revisions — `issues/comments/<id>` (issue #1250)

The convention revises an anchor in place by comment id, so from rev 2 on
every update is `gh api -X PATCH .../issues/comments/<id>`. That path carries a
comment id and no PR number, so the PR it belongs to cannot be recovered from
the command.

A confirmed revision therefore clears the gate **only when exactly one created
PR is still missing an anchor and the revision came after that PR was
created** — attribution by elimination. Two or more leaves it ambiguous and
every one of them is reported, because guessing would clear a PR that
genuinely has no anchor. The ordering rule is not theoretical: replayed over
the local transcripts, the gate's own development session revised three
earlier PRs' anchors and then opened this PR, and without it that create would
have been cleared by a revision that predates it. `PATCH` and `PUT` join `POST` as
write methods for this path; `DELETE` never carries an anchor.

### Invocation boundaries

Invocations are separated by an unquoted control operator **or an unquoted
newline** — two commands on two lines are two invocations. A newline inside
quotes, after a `\` continuation, or inside a heredoc body separates nothing.
The heredoc exclusion costs recall to buy precision: an anchor body written
through `<<'EOF'` routinely carries transcribed `gh pr comment` / `gh api`
lines that never ran, and crediting one would silence the gate on a PR with no
anchor at all.

Leading `VAR=value` assignments are stripped from a segment before the
`gh …` prefix check, so `SP=/tmp/x gh pr comment 81` is recognized.

## The trigger — a created PR with no post

Fires when `createdPRs − postedPRs ≠ ∅`.

## Escalation — deliberate deviation from sibling gates (issue #1113)

No sibling `completion-verify` gate escalates within a single session; each
one is statically advisory, statically blocking, or advisory-by-default with a
global `_STRICT_ENV` override. This gate is different on purpose: the issue
explicitly asked for one nudge before a block, because a hard block on the
very first Stop after `gh pr create` would punish the ordinary case where the
create just happened and the agent is still mid-turn on something else.

- The FIRST Stop this session where `createdPRs − postedPRs ≠ ∅` holds emits
  an **advisory**.
- The SECOND and every later Stop where it **still** holds emits a **block**
  (`{"decision": "block", ...}`).
- `count_session_fires(hook, session_id, DECISION_ADVISE)` (issue #805's
  read path) answers "have I already advised this session" — reusing a
  primitive its siblings use for *suppression* to do *escalation* instead.
- `PRAXIS_PR_ANCHOR_ADVISORY=1` pins the gate to advisory forever (no
  escalation) for a session that wants the nudge without the block.

## Correctness guards

- **Draft segmentation.** `_PR_CREATE_SEGMENT_RE` isolates each `gh pr create`
  invocation's own argument text (up to the next `&&`/`;`/`|` or end of
  string) before checking for `--draft`/`-d`, so draft-ness never leaks
  across two `gh pr create` calls — or into an unrelated `gh pr comment` call
  — in the same compound command.
- **Failed calls do not count**, on either side: a failed create never adds a
  PR to `createdPRs`, and a failed comment never adds one to `postedPRs`.
  Matched by tool_use `id` <-> tool_result `tool_use_id`.
- **Explicit HTTP method wins for `gh api`, resolved PER INVOCATION.**
  `_GH_API_SEGMENT_RE` isolates each `gh api` call's own segment before
  pairing method with target — `gh api GET .../issues/178/comments && gh api
  --method POST .../issues/179/comments` credits the POST to #179 only, never
  to #178 (a whole-command scan would either miss the real POST or wrongly
  clear the GET-only PR — a Codex review finding on this PR).
  `--method GET … -f page=1` is a documented GET (query params), NOT a post,
  same rule as `pr-report-destination-gate`.
- **A create result must carry exactly one PR URL.** Zero or multiple URLs
  leave that create unresolved rather than guessed at.
- **Report reduction, not retention.** Every block is reduced to the PR
  numbers / draft-ness it carries on arrival; the tool_use block itself is
  dropped immediately (same rationale as issue #1076 for
  `pr-report-destination-gate`).

## Honest limitations

- **Draft-to-ready transitions are out of scope.** A PR created with
  `--draft` and later marked ready via `gh pr ready <N>` is never added to
  `createdPRs` — the draft check runs once, at create time, on the create
  command's own flags. Requiring an anchor once a draft goes ready is a
  reasonable follow-up but is not what issue #1113 asked for.
- **A bare `gh pr comment` with no explicit number** (targeting the current
  branch's PR via git context) is not recognized as a post to that PR's
  number — same accepted miss as `pr-report-destination-gate`.
- **PRs are keyed by number only** — two repos sharing a number in one
  session collide, same limitation as `pr-report-destination-gate`.
- **An anchor revision is attributed by elimination, not by identity.** A
  session that revises some *other* PR's anchor while leaving its own single
  created PR unanchored reads as anchored. Closing this needs a comment-id →
  PR resolution the command line does not carry.
- **Escalation state is per-session, not per-PR.** A session with two
  unanchored PRs advises once for the pair, then blocks for the pair — it does
  not track "have I advised about #1084 specifically."
- **`is_error` is per Bash tool_use, not per subcommand.** A `gh pr comment
  178 --body x; true` sees the whole call's exit status, not `gh pr
  comment`'s own — a failed comment tailed by a succeeding no-op command
  reads as `is_error=false` and is credited as posted (and, symmetrically, a
  successful post tailed by a failing command can read as `is_error=true` and
  be discarded). This is not fixable at this hook's layer: the Claude Code
  transcript's `tool_result` carries one exit status for the whole Bash
  invocation, and every sibling `completion-verify` gate that correlates
  tool_use/tool_result shares this same granularity (a Codex review finding
  on this PR, confirmed structural rather than a local bug).

## Tiers

| Tier | Env | Behavior |
| --- | --- | --- |
| 1st fire this session | — | advisory (`systemMessage` JSON, non-blocking) |
| 2nd+ fire this session | — | block (`{"decision": "block", ...}`) |
| Force advisory | `PRAXIS_PR_ANCHOR_ADVISORY=1` | always advisory, never escalates |
| Bypass | `PRAXIS_PR_ANCHOR_BYPASS=1` | full bypass, exit 0 |

## Fail-open contract

- Malformed / missing stdin JSON → exit 0
- Missing / unreadable / empty transcript → exit 0
- `stop_hook_active=true` → exit 0 (re-entrancy guard)
- Any uncaught exception → exit 0 (`@fail_open`)

## Tests

`tests/hooks/completion-verify/test_pr_anchor_existence_gate.sh` covers, via
synthetic transcripts:

- `gh pr create` (non-draft) success, no post → 1st Stop advises, 2nd Stop blocks
- `gh pr create` success + `gh pr comment <N>` to the same PR → silent
- the post on a second line of the same command → silent (newline boundary)
- the post prefixed by a `VAR=value` assignment → silent
- a `gh pr comment` line inside a heredoc body only → still fires
- `gh api -X PATCH .../issues/comments/<id>` after the create, one unanchored PR → silent
- the same PATCH *before* the create → still fires
- the same PATCH with two unanchored PRs → still fires for both
- no `gh pr create` in the session → silent
- `gh pr create --draft` success, no post → silent
- failed `gh pr create` (`is_error`) → silent
- `gh pr comment` on an unrelated PR → still fires (current PR unanchored)
- `PRAXIS_PR_ANCHOR_ADVISORY=1` with a repeat fire → stays advisory
- `PRAXIS_PR_ANCHOR_BYPASS=1` → exit 0, no output
- malformed stdin / missing transcript → exit 0 (fail-open)
