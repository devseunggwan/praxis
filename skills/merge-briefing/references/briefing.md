# Step 4: Compose the six-part briefing

Full procedure for [`SKILL.md` Step 4](../SKILL.md#step-4-compose-the-six-part-briefing).

Six parts, in this order (this list is the canonical shape; it was originally
derived from the author's dotfiles PR-workflow template):

1. **What changed** — open with one plain sentence of what the PR does and
   what changes for the user if it merges (and if it does not), then the
   scope (files, logical changes), not the issue title again. A part 1 made
   of counts and internal shorthand gets "what does it do?" back instead of
   an answer, which resets the approval window and costs a full re-brief.
2. **What was verified** — real output, with the command cited, and the
   **deepest real surface** each claim reached, named per PR: an end-to-end
   run of the changed path that wrote and read back its output (preview or
   staging DAG run, real endpoint call), a read-only run on production data
   (query mode, no writes), or local/unit tests only. Say which surface the
   change's own write path or user-visible effect was last exercised on, and
   whether that measurement was taken on the current head. Users ask "was
   each one really verified?" before approving a multi-PR set; a green CI
   plus mock-graded anchor rows reads as verified until they ask. When the
   gap is closable before merge (a preview run), offer that as the
   recommended option rather than deferring it to "first prod run".
   A live run of a *neighbouring* path is not the fix verified: when the PR
   fixes a specific failure, name whether the live run hit **that** failure
   (same endpoint, same status) or a stand-in (another error the same code
   catches), and when the change wraps every caller (a preload, a shared
   client, a global handler), whether a normal *successful* run went through
   it.) A subagent's report that it ran a live smoke is not this evidence:
   re-run that live path yourself on the briefed head before the ask, or
   grade it unverified in part 3. If either is mock-only, lead part 2 with that, and offer the live run
   (with the credential or approval it needs) as the recommended option
   before the merge ask; the user asks "실검증 했나요?" otherwise
3. **What was NOT verified** — skipped, deferred, blocked, CI-pending
4. **Risk / blast radius** — who breaks if this is wrong
5. **Open items** — every finding Step 2 graded and left open, on all three
   surfaces; the gaps from Step 3
6. **Explicit ask** — `Approve merge?` on its own line, in the text, with the
   `AskUserQuestion` call of Step 5 right after it (the line stays even though
   the tool asks, because it is the item the gate scores)

Part 3 is the one under pressure, and it is the reason the briefing exists.

When an unverified item is the very step the merge sets off (a post-merge
deploy, an asset build the CI never ran, a multi-platform build you ran on one
platform), it is a risk, not a footnote: put it in part 4 with what breaks if
that step fails and who else it blocks (later deploys of the same stack). For a
PR whose value is bookkeeping (version pins, catalogs, docs), part 4 also says
in one line what happens if it is **not** merged — the user asks that before
approving a low-stakes PR that still triggers a deploy.

A part 4 claim that the PR's files "do not reach the production image" or
"do not change prod behaviour" is a negative claim over every build that can
ship them, not over the one Dockerfile you opened. Enumerate them first
(`git ls-files '*Dockerfile*'` plus the deploy workflow's `file:` / `context:`
lines), grep each for a `COPY` of the changed paths **and** for `COPY . .`
(then read its `.dockerignore`), and for each image that does carry the files,
`git grep` the runtime sources for imports of the specific changed files. State
the scope in the sentence ("explorer image copies `mcp/src` but imports only
`a.ts`, `b.ts`, which this PR does not touch"). A wrong "not in the image" line
found after the merge is corrected in the next report, at the top.

For a PR that changes a shared schema or contract (a table column, an API
field, a config key) and applies itself on merge, part 4 traces the paths
before the user has to ask "can this break anything?": every writer (does it
write by name and null-fill a column it does not select, or positionally),
every reader (does a downstream mart or consumer gain the column, or already
have it), and any guard already merged for the same change class. Quote the
line that settles each path, and say which path you inferred rather than read.

**Pre-answer the three "why" questions in the briefing itself.** A user
deciding from a phone (an external decision board relayed to the session)
answers a merge ask with a question instead of a pick when the briefing leaves one open, and each such
round closes the ask and costs a full re-ask on a fresh probe. Put one short
line each in parts 1 and 4:

- **Why each new component must exist** (part 1): for every resource, bucket,
  endpoint, or dependency the PR adds, the constraint that forces it (quote the
  vendor doc line) and what the change looks like without it. A component that
  only serves an optional extra is a scope choice; surface it as one before
  the merge ask, not inside it.
- **Why the problem arose** (part 1): the commit that introduced the behaviour
  being fixed (`git log -S/-G` on the symbol, then the PR's own commit list:
  a squash hides the commit that changed direction) and the reason it gave;
  say "no decision record found" with the scan scope when none exists.
- **Security** (part 4, for anything touching auth, credentials, trust, or
  tenant isolation): the threat the mechanism defends against, why it still
  holds after the change (cite code lines and the existing regression tests
  that pin it), and the vendor doc line for the intended order. Put the same
  analysis in the PR body, since that is where rationale lives.

Part 3 anti-patterns:
"완료했습니다, 머지할까요?" with no evidence; a skipped check
demoted to a trailing aside; reporting at merge time what should have surfaced
at PR-ready time.

**Close part 3 items by observing, not by reading.** When the user asks you to
check the unverified items, go item by item and run the cheapest probe that
settles each by observation: the real function on a scratch fixture, the OS
asked who holds the file or lock (`lsof`), the store queried. Correlating log
timestamps with a process start, or quoting a source comment, is inference and
leaves the item open. An item only the unbuilt change can settle stays in part
3, naming the probe that will close it and when. An action the brief forbids
(a config change, a restart) stays forbidden: name it as the blocker.

**Calibrate the length.** A typo, comment-only, or single-line config PR gets
two lines — the bar is "enough context to decide", not "long report".
