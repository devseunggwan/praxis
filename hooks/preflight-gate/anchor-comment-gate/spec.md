# Anchor Verification Comment Gate

Supported hosts: all

`hooks/preflight-gate/anchor-comment-gate/impl.py` runs on two events. On
**PreToolUse(Bash)** it blocks a PR verification **anchor** comment whose
structure is incomplete, deciding that from the command's own body text and
nothing else. On **PostToolUse(Bash)** it reads the comment that was actually
published, through the API, and checks its structure again plus the things only
the real target can answer: SHA freshness and diff coverage.

## Why this exists

The anchor is one comment per PR holding that PR's whole verification, edited
in place by comment id. Its rules lived only in prose. In the session that
wrote them, the author shipped two self-contradictions into the rule text
itself — an update command that would silently rewrite the wrong comment, and
a result enum with no cell for a check that could not run. An external reviewer
caught both, after the fact. The failure mode is not ignorance of the rule; it
is the rule not being retrieved at the moment of posting.

## Why two events

The first design did all of it in PreToolUse: decode the `gh` command, find the
body, resolve the target PR, decide. Six rounds of adversarial review found
twenty-five distinct ways that reading fails — an attached shorthand value
(`-Fanchor.md`), a quoted multi-line body the tokenizer shreds, a leading `cd`,
a `GH_REPO=` env prefix, gh's own `{owner}`/`{repo}` placeholders, `--hostname`
before the subcommand, `--input payload.json`. Every finding was real and none
of them was the last one: statically deciding what a shell command will do is
not a bounded problem, and each round's fix widened the parser rather than
closing it.

The split follows what each event can actually know.

| | PreToolUse | PostToolUse |
| --- | --- | --- |
| Input | the command string | the comment URL the command printed |
| Decides | structure, `--edit-last` | structure, SHA freshness, coverage |
| Network | none | `gh api` + `gh pr view` |
| On violation | blocks (exit 2) | reports (exit 2 blocking / context otherwise) |
| Can be fooled by shell syntax | yes — it warns instead | no |

PostToolUse needs no parsing at all: `gh pr comment` prints the new comment's
URL and `gh api` returns a JSON body containing it, and that URL names host,
owner, repo, PR and comment id outright. Whatever the command looked like, the
published comment is the oracle.

The cost is worth stating plainly: PostToolUse cannot prevent the post. A
malformed anchor caught there is already visible, and the remedy is "fix it
now" rather than "you may not" — the anchor is editable, so the exposure is
seconds. A stale SHA, though, was published. PreToolUse blocking the structure
case is what keeps that window small, and it is the case that can be decided
without knowing the target at all.

## PreToolUse — structure only

| Post form | Recognized |
| --- | --- |
| `gh pr comment <pr> --body-file anchor.md` | yes |
| `gh pr comment <pr> --body '### Verification …'` | yes |
| `gh api --method PATCH /repos/{o}/{r}/issues/comments/{id} -F body=@anchor.md` | yes |
| body from stdin (`-F body=@-`) or `--input payload.json` | no — warns, PostToolUse decides |

**Anchor shape decides scope.** Only a body whose first non-empty line starts
with `### Verification` or `### 검증` is an anchor. The one-line update notice
that accompanies every anchor edit posts through the same `gh pr comment`;
blocking it would break the procedure this gate exists to protect. Every other
comment passes untouched.

Every segment of a compound command is checked, not just the first: two anchor
posts joined by `&&` would otherwise let the second through on the strength of
the first being well-formed.

**Two tokenizations, because neither alone sees every post.** `safe_tokenize`
splits on newlines — correct for Bash, where a newline separates commands, but
it shreds a *quoted* multi-line body: an inline `--body '### Verification …'`
spanning lines loses its `gh pr comment` segment entirely. `shlex.split` keeps quoted
newlines but glues shell operators to adjacent words, so it cannot replace the
primary. Both run when the command contains a newline, de-duplicated by body.

**Attached shorthand values** (`-banchor`, `-Fanchor.md`) are split before
parsing — pflag accepts them, and matching only the bare token would let the
anchor through unexamined. **Global flags before the subcommand** are consumed
with their arguments, so `gh --hostname ghe.example api …` resolves to `api`
rather than to the hostname value. A `~/anchor.md` body-file is expanded and
relative paths resolve against the segment's own working directory (the
payload's `cwd`, updated by a plain leading `cd`); subshell and `pushd` scoping
are *not* modelled — a half-right emulation would be worse than the stated
limit, and PostToolUse covers what it misses.

Nothing here reaches the network. No repo, no host, no PR number is resolved:
the checks are exactly those decidable from the body, which is why widening the
parser is no longer the answer to a miss.

### Structure — five required fields, in one of two dialects

The field *names* come in two dialects. `en` is what the rule prescribes. `ko`
is the shape this gate originally required, kept for one reason: anchors
published under it are edited in place and explicitly not retrofitted, so
rejecting their dialect would lock their own later revisions out of the
procedure this gate protects.

| Field | `en` — detected by | `ko` — detected by |
| --- | --- | --- |
| SHA+rev heading | `### Verification — \`<sha>\` (rev N)` **on the first non-empty line** | `### 검증 — \`<sha>\` (rev N)`, same position |
| claim table | at least one `\| <n> \|` row | same |
| unverified toggle | `<summary>` contains `Unverified` | contains `미검증` |
| per-row evidence toggle | `<summary>` starts `Evidence <n> —`, for every table row `n` | starts `<n>.` |
| history toggle | `<summary>` contains `History` | contains `갱신 이력` |

**One body, one dialect.** The dialect is chosen by the heading's own keyword
and then every other field is read in it, so a body that takes its heading from
one and its toggles from the other reports the missing fields of the dialect it
opened in. Letting the two mix would make "the labels are a fixed field name"
unenforceable in exactly the case where an author is drifting between them.

A body opening with neither keyword is not an anchor at all and is not checked
(see **Anchor shape decides scope** above) — the dialect question only arises
once the gate is already in scope. When a body in scope has no usable heading,
findings are reported in `en`, since that is the dialect a new anchor should
have been written in.

**Drift is the failure this table exists to prevent, and it has happened once
in the other direction**: the rule pinned the `Evidence <#> —` prefix while
this gate still required `<n>.`, so a correctly-written anchor was blocked and
the author had the choice of violating the rule or bypassing the gate. When the
rule's field names change, this table changes with it.

A toggle must be a real `<details>…</details>`: a bare `<summary>` renders as
plain text, and one quoted inside a code fence is an example — neither is
something a reviewer can open, so neither satisfies a required field. Row
numbers are read only from the region before the first toggle, fences stripped:
a `| 200 | response | ok |` line pasted as evidence would otherwise register as
claim row 200 and demand a toggle that should not exist.

**`--edit-last` on an anchor body** — `gh pr comment --edit-last` edits *the
last comment of the current user*, not a named one. Since every anchor edit is
paired with a one-line notice, from revision 2 the last comment is the notice:
`--edit-last` rewrites the notice and leaves the anchor stale. That is the
defect this hook was built for, so the flag is rejected on an anchor body and
the block names the comment-id `PATCH` form instead.

**Undecodable post → warn, never silent.** A body read from stdin, sent via
`--input`, or living in a file the same command rewrites before posting, cannot
be known beforehand. It is not blocked — the hook cannot tell an anchor from an
ordinary comment there, so blocking would fire on every comment built that way.
It is not silent either: stderr says the pre-check was skipped, because silence
reads as "checked and clean", which is the one thing it is not.

## PostToolUse — the published comment

**Every** comment URL in the tool output is followed, not the first: one
compound command can publish two anchors, and PreToolUse already checks each
segment. Each URL names host, owner, repo, PR and comment id; `gh api
…/issues/comments/{id} --jq .body` fetches the body; a body that is not an
anchor is dropped.

**A failed post is read after its ids, never instead of them.** Both ref
sources run first — the comment URL in `tool_response` and, failing that, the
`…/comments/<id>` endpoint in the command — and only when neither yields
anything do `exit`, `isError` and `interrupted` decide. That order is what a
compound command forces: `gh pr comment ...; false` exits 1 with a real comment
URL still in the output, and `gh api --silent --method PATCH …; false` carries
its target in the command while printing nothing at all. Either way the anchor
was published, so a failure-first check would skip it.
With nothing recoverable, the failure means nothing was published — a
`gh pr comment` that died on auth or the network — and the URL-loss branch
below would otherwise find the anchor body in the *command* and report an
anchor that does not exist, a second error stacked on the one already on
screen.

**A post that prints nothing is not a pass.** `gh api --silent`, a `--jq` that
projects the URL away, and `> /dev/null` all publish while leaving no URL to
follow — and since freshness is checked *only* here, silence there would mean a
stale SHA ships unremarked. A comment-id `PATCH` carries its target in the
endpoint literal, so the id is recovered from the command and the PR number
from the comment's own `issue_url`. A `gh pr comment` that printed nothing has
no id anywhere, and is reported as unverified rather than passed.

- **Structure** — the same five fields, re-checked against what was actually
  published. This is the authoritative pass: it sees the body after every
  shell expansion, heredoc and file write.
- **Freshness** — the heading's SHA must prefix-match the PR's current
  `headRefOid`. An anchor pinned to a commit that is no longer HEAD asserts
  evidence for code that is not there, which the rule set calls worse than no
  anchor at all.
- **Coverage** — files in the branch diff that no table row mentions are
  reported at the `advisory` tier. File-to-claim is not 1:1 (one row can cover
  several files; a rename touches two paths under one claim), so the tier says
  so rather than the finding being suppressed. The
  comparison point is the PR's own `baseRefName`, which arrives on the same
  `gh pr view` as the head SHA — a PR targeting `dev` measured against `main`
  would report base-only files as uncovered. No base, or any git failure,
  yields no advisory rather than a false one.

### Three tiers, two channels

Every finding carries a tier, and the tier decides which channel carries it.

| Tier | What it covers | What the wording asks for |
| --- | --- | --- |
| `blocking` | a missing required field, a confirmed stale SHA | this anchor violates the rule — fix it now |
| `advisory` | changed files no table row mentions | consider it; it may be a false positive |
| `unknown` | lookup failed, PR unresolved, no comment URL in the output | the check **did not run** — this is not a pass |

`blocking` exits 2. The other two are written to
`hookSpecificOutput.additionalContext` and exit 0.

Both of those reach the model; what separates them is what exit 2 *means* one
layer up. `_dispatch.run_group` returns 2 for the whole group and
`_fire_ledger.classify_decision` records `block`, so a coverage hint or a `gh`
timeout sent that way is filed beside a rule violation and the fire-rate audit
that scores this hook can no longer tell them apart. `additionalContext` is
the channel `second-failure-advisory` and `builtin-task-postuse` already use
on this event for exactly that reason.

What exit 0 does *not* buy is silence: a hook that exits 0 has its **stderr**
routed to the debug log where Claude never reads it, which is why none of
these findings goes there. `PRAXIS_ANCHOR_GATE_ADVISORY=1` demotes the
blocking branch to exit 0 as well.

The fix instruction rides with the exit-2 branch only — "fix the comment now"
is the wrong thing to say about a check that could not run.

`unknown` exists because the previous shape put "the SHA does not match" and
"the SHA could not be read" in one list. Raising the exit code on that list
would have reported a `gh` outage as a rule violation, and leaving it at 0 let
an unrun check read as a clean one. Separating the tier keeps both honest: a
lookup failure reaches the model through `additionalContext`, and still says
which of the two happened.

An anchor that turns out to be an ordinary issue comment is simply not an
anchor by the heading-prefix test. A lookup failure says why rather than
inventing a verdict — the tier carries the uncertainty, so nothing has to be
guessed to keep the report readable.

## Budget

The dispatcher runs all members of one `(event, matcher)` group **sequentially
in one process** on a single budget equal to the *max* member timeout
(`scripts/build-plugin-manifests.py:_dispatcher_node`). That is why the network
work sits on the PostToolUse side: the PreToolUse group is the hot path on
every Bash call and this hook now spends no I/O there at all. The PostToolUse
lookups share a hard 15s deadline, re-measured before every subprocess (8s cap
per `gh` call, 3s per `git` call, each clamped to what is left).

## Prerequisite — `gh`, for revision specifically

The convention this gate protects is *one comment per PR, edited in place by
comment id*. Creating an anchor needs only some way to post a comment; **every
later revision needs `gh`**, because the update is a `PATCH` against the
comment id:

```text
gh api --method PATCH /repos/{owner}/{repo}/issues/comments/<id> -F body=@anchor.md
```

A session whose only GitHub surface is the MCP server can create an anchor and
then cannot revise it. Issue bodies and PR bodies each have an update tool;
comment bodies have none, while GitHub REST itself exposes the `PATCH`. So the
limit is what the host surfaces, not the API — and where it was measured the
absence was **upstream, not a gap in one deployment**: enumerating
`github/github-mcp-server`'s own tool list returned create and reply for issue
and PR comments and no update, with Discussions' `discussion_comment_write`
(which *does* carry an `update` mode) caught by the same enumeration as its
positive control.

**Scope of that measurement — `github/github-mcp-server`, version unknown,
enumerated 2026-09-04.** No version was recorded when the enumeration ran, and
none could be reconstructed afterwards from this repo or this machine: the only
GitHub MCP entry configured locally is `@modelcontextprotocol/server-github`, a
different server, pulled unpinned through `npx -y`, and no
`github-mcp-server` binary is installed. So this is one reading of one server
on one date, while the file header above says `Supported hosts: all`.

Read the procedure below as **conditional, never as a standing assumption**: it
applies to a session once a comment `update` has been confirmed absent from
that session's **active** tool list. Confirm it the same way the claim above
was made — enumerate the tools the session actually has, look for a
comment-update mode, and use the **PR-body update** mode as the positive
control that the enumeration can return a write mode at all. That control is
not interchangeable with an issue-body update or a Discussions comment write:
steps 3–4 record the gap *in the PR body*, so a session holding one of those
but no PR-body update cannot run them, and a positive control drawn from one
would certify a surface the procedure never uses. If a comment `update` **is**
present, the anchor is revisable on that host: revise it in place and none of
the procedure applies. If the PR-body update is absent too, steps 3–4 do not
apply either — go straight to step 5 and carry the delta in the merge commit,
which needs no host tool. Where both are absent, waiting for the tool to
arrive is not a plan (#1211).

The same absence removes the PostToolUse re-check, for a second and independent
reason. That step reads the published comment back and re-checks structure
keyed on `<summary>`; the MCP read path strips `<details>` / `<summary>` from a
**comment or PR body** while keeping their inner text, so a well-formed anchor
read back over it reports as missing every toggle. **On that path the re-check
has no substitute** — not a weaker signal but no signal, because every anchor
reads as missing every toggle whether or not it is well-formed. Reading through
`gh api`, as this hook does, is what keeps the check meaningful.

Worth stating precisely, because the obvious reading is wrong: this is not the
server mangling markup, and nothing is lost at the write. A posted anchor read
back through `gh api .../issues/comments/<id>` holds its `<details>` and
`<summary>` pairs intact with zero entity escapes, and **file contents come
back with both tags intact** over MCP too — `get_file_contents` on this very
spec returns them verbatim. The transform applies to the body text a commenter
authors and not to the files a reader is meant to trust, which is the
prompt-injection boundary, not a defect. Treat it as policy to work with rather
than a bug to route around.

### Procedure for a gh-less session

The rule is satisfiable at rev 1 and unsatisfiable past it, so the procedure
splits there. Do not fake a revision by posting a second anchor: a second
anchor-shaped comment makes the id-recovery lookup in
[Coupled constant](#coupled-constant) ambiguous, which is the failure that
stays invisible until someone needs it.

1. **Post rev 1 normally.** Creation needs only a way to post a comment, so
   the full five-field structure still applies — write it in full.
2. **Grade the PostToolUse re-check `unknown`, never a pass.** It did not run;
   the tier table above already says that an unrun check is not a pass.
3. **When rev 2 would be due, say so in the PR body**: which `<sha>` the
   anchor is stamped at, which one HEAD is now at, and that the host has no
   comment-update surface. A reader who does not know this reads a stale
   anchor as a current one. Not a comment of its own — this channel allows
   exactly one top-level comment besides the anchor, the one-line
   `Verification updated — <sha> rev N · …` notice that pairs with an edit,
   and here there is no edit to pair one with. The body is also the only one
   of the two surfaces this host can still correct afterwards.
4. **Refresh that record on every later HEAD change that would have been a
   revision — not only the first one.** One note naming one HEAD goes stale on
   the next push, and then *both* surfaces are behind: the anchor, and the note
   that exists to explain the anchor. A reader then cannot tell what is
   unverified at the current HEAD, which is the whole point of the note. So
   edit the PR body again each time, carrying the current HEAD `<sha>` and the
   `Unverified` delta **accumulated since rev 1**, not only the delta since the
   last push. The PR body has an update path on this surface — that is why the
   record lives there, and why refreshing costs one body edit per push. If you
   will not keep it refreshed, then stop pushing until the anchor can be
   revised: a note that names an old HEAD is worse than no further commits.
5. **Carry the delta where it survives** — the PR body kept current per step 4,
   or the merge commit, both of which *do* have an update path on the MCP
   surface. The anchor's `Unverified` gaps go with it, so `merge-briefing`
   Step 3 still has something to read.
6. **Leave the stale anchor in place.** It stays the one comment the
   id-recovery lookup resolves to; the pointer in steps 3–4 is what makes it
   honest.

Issue #1211 holds the open question of whether to define a richer fallback
than this.

## Bypass

Two forms, both requiring an explicit act, and both honoured on either event:

- `PRAXIS_HOOK_BYPASS_ANCHOR_GATE=1` in the environment (both events)
- `PRAXIS_ANCHOR_GATE_ADVISORY=1` — PostToolUse only, exact value `1`; demotes
  the exit to 0, which on this event means the findings go unread
- `# anchor-gate: <reason>` as a **trailing shell comment** on the command's
  last line, outside quotes — an anchor whose evidence block quotes this
  marker (a test transcript, this spec) must not waive the gate on itself

The reason is the point. An offline or otherwise unverifiable post becomes a
recorded decision instead of an accident — so a bare `# anchor-gate:` with no
text after it is **not** a bypass. Waiving the gate without stating why waives
the audit trail the waiver exists to leave.

## Coupled constant

The heading prefix is both what opens the anchor and what the id-recovery
lookup matches on, so it has to accept every dialect the gate does:

```bash
gh api /repos/{owner}/{repo}/issues/<pr>/comments \
  --jq '[.[]|select(.body|startswith("### Verification") or
                    (.body|startswith("### 검증")))][0].id'
```

Changing one without the other leaves the anchor unfindable after its id is
lost. `_ANCHOR_PREFIXES` in `impl.py` and that jq are the two sites — a dialect
added to one and not the other makes exactly the anchors written in it
unrecoverable, which is the failure that is invisible until someone needs it.
