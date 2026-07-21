# Stop PR-Report-Destination Gate

Supported hosts: all

Non-blocking Stop hook. Fires when a session did PR-bound verification/review
work, wrote the results to a **local report file**, and never posted them to
the PR — nudging the agent to share the results on the PR before it stops.

- **Event:** Stop
- **Role:** completion-verify
- **Default tier:** advisory (non-blocking)
- **Bypass:** `PRAXIS_HOOK_BYPASS_PR_REPORT_DESTINATION_GATE=1`

## Why this exists

A `/insights` pass over a month of sessions surfaced a recurring miss: the
agent ran PR verification/review, wrote the findings to `/tmp/…` or
`.omc/plans/…`, and never posted them to the PR. The user had to ask "did you
share this?" — the results existed but sat where nobody downstream would see
them.

A prose CLAUDE.md rule ("report to the PR, not just a local file") was drafted
for this and rejected: the same rule text was already loaded in earlier
sessions and still not followed. That is the **Loaded ≠ Retrieved** failure —
a rule in context is not enforced unless retrieved at the decision moment, and
a long always-loaded file dilutes retrieval further. The sibling
completion-verify gates exist for exactly this reason: prompt layer fails,
hook layer succeeds. So this guard lives at execution time.

## What is detected

Detection is **per-PR**, not a global boolean, so it survives multi-PR
sessions. The whole transcript is scanned (not just the current turn) because
the report write and the PR context are often many turns apart.

### Context PRs — the work is bound to these

- `gh pr view|create|diff|checks|checkout|edit|ready|merge <N>` (number or `…/pull/<N>` URL) — every occurrence in a compound command
- any `github.com/<owner>/<repo>/pull/<N>` URL in narrative (assistant/user) prose
- a PR URL in tool output only when that result carries exactly one distinct PR (a create/view success), never a multi-PR listing (`gh pr list`)

### Posted PRs — a successful post targeted these

- `gh pr comment|review <N>` — every occurrence in a compound command
- POST `gh api …/pulls/<N>/{comments,reviews}` — POST only, see guards below

### Report-like local `.md` write

A `Write`/`Edit` whose `file_path` ends in `.md` and either sits under a
scratch dir (`/tmp/`, `scratchpad/`, `.omc/plans/`) or has a
review/verification basename (`review`, `verif`, `verdict`, `report`,
`impact`, `검증`, `리뷰`).

## The trigger — report written AND a context PR went unreported

The hook fires when a report-like local `.md` was written **and**
`contextPRs − postedPRs ≠ ∅` (at least one PR the session worked on received
no successful post).

## Correctness guards

- **Explicit HTTP method wins for `gh api`.** An explicit `--method`/`-X`
  value decides POST-ness, so `--method GET …/reviews -f page=1` is a
  documented GET (query params), NOT a post; only when no method is given does
  a body field (`-f` / `-F` / `--field` / `--raw-field` / `--input`) imply
  POST. (`gh api` defaults to GET — verified via `gh api --help`.)
- **Failed posts do not count.** A `gh pr comment`/`gh pr review` whose
  `tool_result` is `is_error` is excluded, matched by tool_use `id` ↔
  tool_result `tool_use_id`. Otherwise a 403/network failure would silence the
  advisory that is precisely the point.
- **Posting to an unrelated PR does not clear the current PR.** Per-PR set
  arithmetic keeps a `gh pr comment 456` from marking context PR 123 reported.
- **Compound commands contribute every PR.** `_pr_nums` uses finditer, so
  `gh pr view 10 && gh pr view 11` records both — not just the first.
- **Tool output URLs are gated to avoid listing floods.** A PR URL in a
  tool result counts as context only when that result carries exactly one
  distinct PR (a create/view success); a multi-PR listing (`gh pr list`) is
  ignored. Narrative (assistant/user) prose contributes all URLs.
- **Fires once per session.** The whole-transcript scan re-derives the same
  condition on every Stop; a session-fire dedup (`count_session_fires`/
  `record_session_fire`, issue #805) suppresses the repeat while an unreported
  context PR persists. When `session_id` is absent (or telemetry disabled) the
  dedup degrades to no-op — the advisory may repeat, but is never wrong.

## Honest limitations

Each is a bounded false signal — the hook is advisory-only, so the cost of any
one is a single ignorable `systemMessage` line.

- A genuinely private scratch `.md` (never meant for a PR) still trips the
  advisory when a context PR exists; the message says to ignore it for
  private scratch/draft files.
- A post is counted whenever a successful `comment`/`review` targets the PR —
  the post's **content** is not correlated with the report file. Posting a
  "review starting" comment then leaving the final report unposted still
  suppresses the advisory (false negative).
- PRs are keyed by **number only**, so two repos sharing a number
  (`org/a#178` vs `org/b#178`) collide within one session — a bare
  `gh pr view 178` carries no owner/repo, so a full owner/repo key is not
  recoverable.
- The posted-PR extraction reads the number as the token directly following
  the `comment`/`review` subcommand, so a flag before the positional
  (`gh pr comment -b "…" 123`) is not matched. Relaxing the regex to scan the
  whole command risks matching a number inside a flag value
  (`--body "closes 999"`).

## Tiers

| Tier | Env | Behavior |
| --- | --- | --- |
| Default | — | advisory (non-blocking `systemMessage` JSON on stdout) |
| Bypass | `PRAXIS_HOOK_BYPASS_PR_REPORT_DESTINATION_GATE=1` | full bypass, exit 0 |

There is no block tier: the "is this local file a PR report?" signal is
heuristic, so a hard block is not affordable.

## Response shape

Non-blocking Stop advisory via `emit_stop_advisory` — a `systemMessage` JSON
surfaced to the next turn. The message names the unreported PR(s), lists up to
3 report files, restates the rule (post to the PR, still gated by the Layer-3
external-write falsification checks), and notes the scratch/draft carve-out
and bypass env.

## Fail-open contract

- Malformed / missing stdin JSON → exit 0
- Missing / unreadable / empty transcript → exit 0
- `stop_hook_active=true` → exit 0 (re-entrancy guard)
- Any uncaught exception → exit 0 (`@fail_open`)

## Tests

`test_impl.py` covers, via synthetic transcripts:

- context PR + report write, no post → **fires**
- successful `gh pr comment` to the same PR → silent
- GET `gh api …/reviews` only → **fires** (read, not a post)
- POST `gh api …/reviews` → silent
- failed post (`is_error`) → **fires**
- post to a different PR → **fires** (current PR still unreported)
- no PR context → silent
- no report file → silent
- malformed stdin / missing transcript → exit 0 (fail-open)
