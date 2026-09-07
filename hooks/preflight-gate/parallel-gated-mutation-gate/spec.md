# PostToolBatch Parallel Gated Mutation Gate

Supported hosts: claude (the `PostToolBatch` event exists only in Claude Code)

`hooks/preflight-gate/parallel-gated-mutation-gate/impl.py` fires on
`PostToolBatch`, the event Claude Code raises once after a batch of parallel
tool calls resolves and before the next model call. It keys every `gh` write
in the batch by `(noun, verb, target)` and blocks (exit 2) when one key
appears twice or more.

### Why this exists

praxis recorded this gap and then closed it as unhookable. The memory entry
`gated mutation parallel batch` reads, verbatim: "same-turn은 transcript-tail
훅으로 못잡음·행동규칙만 유효" — same-turn batches cannot be caught by a
transcript-tail hook, so only the behavioural rule applies.

That verdict was correct for the events praxis had. `PreToolUse` fires once per
tool call and the payload carries no sibling calls, so the second `gh issue
create` in a batch looks exactly like a lone one. Every `Stop`-family gate runs
after the turn, by which point both issues exist and the model has already
moved on. There was no event positioned between "the batch resolved" and "the
model reads the result".

`PostToolBatch` is that position. The measured harm it addresses is praxis
issues #98/#99 — one intent, two `gh issue create` calls emitted in a single
parallel batch, two issues created, both needing manual closure.

### What is gated

Every `gh` write is reduced to `(noun, verb, targets)`. Two invocations collide
when they share a `(noun, verb)` **and** their target sets intersect — or when
both sets are empty, which is what a creation looks like.

| Batch | Verdict | Why |
| ------- | --------- | ----- |
| `gh issue create …` × 2 | **block** | Both target sets empty. A creation names no target, so two are indistinguishable by intent — the #98/#99 shape |
| `gh issue edit 23 34` + `gh issue edit 34 23` | **block** | Sets intersect. Argument order is not identity |
| `gh issue edit <issue URL for 42>` + `gh issue edit 42` | **block** | One identity. The URL names a repository and the bare number names none, and an unknown scope is compatible with every scope |
| `gh issue edit <URL for org-a/repo-a#42>` + `<URL for org-b/repo-b#42>` | pass | Different repositories. Issue 42 of one repo is not issue 42 of another |
| `gh issue edit --repo o/r 1 …` + `gh issue edit --repo o/r 2 …` | pass | Targets sit after the persistent flag and are still read |
| `gh pr comment 12 …` + `gh pr comment 34 …` | pass | Disjoint targets. Commenting on two PRs at once is ordinary parallel work |
| `gh issue create …` + `gh pr create …` | pass | Different nouns. One intent legitimately opens an issue and its PR |
| `gh issue create … && gh issue create …` in ONE call | **block** | The same duplicate shape written serially. The walk covers every command start |
| `gh issue list` × 2 | pass | `list` is in this noun's read-only set |
| non-Bash entries (Read, Write, Agent …) | pass | Only Bash entries carry a shell command to classify |

The gate does **not** attempt the other recorded variant — a mutation batched
with a consumer that depends on its success. That variant's recorded outcome
was a cancelled batch, i.e. no landed harm, and telling a genuine consumer from
an unrelated sibling needs intent the payload does not carry.

### Three classification rules, each of them a repaired silent pass

An adversarial review round ran executable probes against the first
implementation and found three ways a real mutation went unclassified. Each
failed in the direction that makes a gate worthless: nothing fired, and the
output was identical to a clean batch.

1. **Persistent flags precede the noun.** `gh` accepts both
   `gh --repo o/r issue create` and `gh issue --repo o/r create`, so fixing on
   `argv[1]`/`argv[2]` missed both forms. `_split_gh` skips flags — and the
   value of the one persistent flag that takes one, `--repo`/`-R` — wherever
   they appear.
2. **`-h` is not reliably a help flag.** `gh pr merge 123 --subject -h
   --squash` passes `-h` as the *subject value*, and the first version's
   `is_help_invocation` call dropped that real merge. Only the unambiguous
   long `--help` exempts a call now. This trades a possible false positive
   (two `… -h` calls blocked) for removing a false negative, and the false
   positive is the safe direction: it is visible, whereas a hand-kept
   per-subcommand value-flag table drifts silently.
3. **A mutation allowlist fails open on every verb it has not heard of.**
   `gh pr revert`, `gh pr review`, `gh issue transfer` were all missing from
   it. Classification is inverted now: for a known noun the READ-ONLY verbs
   are enumerated (from the installed `gh <noun> --help`, 2026-09-07) and
   everything else counts as a mutation, so a verb a future `gh` release adds
   defaults to *gated* rather than to *ignored*.

Scope is `issue`, `pr`, `release`. Other nouns (`repo`, `api`, `gist`,
`secret`) are not classified and a duplicate there passes. Widening the noun
set changes this hook's threat model and belongs in its own issue rather than
in a silent constant edit.

### Repository scope, and why an unknown one is a wildcard

A second review round found three **false positives** — the opposite direction
to the three above, and just as wrong. `gh issue edit https://…/org-a/repo-a/issues/42`
and the same URL under `org-b/repo-b` were blocked as one record, because the
number alone was the identity. And `gh issue edit --repo o/r 1` collected no
target at all, since the walk stopped at the first flag, so two edits of
different issues looked like two untargeted mutations.

Both are fixed by carrying the repository: a URL yields its `owner/repo`, an
explicit `--repo`/`-R` yields its value, and target collection now steps over
that flag instead of halting on it. It still halts at any *other* flag, because
an unknown flag's value cannot be told from a positional and reading it as a
target would invent an identity.

A call that names no repository resolves to whatever the working directory
points at, which the payload does not carry. That unknown is treated as
compatible with **every** repository rather than with none — otherwise a batch
mixing a URL with a bare number, the very duplicate this gate exists for, would
pass. Two *explicitly different* repositories are the only pair that cannot
collide.

### Why exit 2 when the mutations already ran

The block prevents nothing — both calls completed before the event fired. What
it buys is reconciliation *before the model continues*: the next turn starts
from "two issues exist, one was intended" rather than from a transcript in
which the duplicate scrolled past unremarked. Exit 2 is what puts the message
in front of the model on this event.

### Payload field names are measured, not quoted

The published hooks reference names the batch array `tools` and its entries
`tool_output` / `error`. The runtime sends `tool_calls`, with entries keyed
`tool_input` / `tool_name` / `tool_response` / `tool_use_id` (measured
2026-09-07 on Claude Code 2.1.263 via an isolated `claude -p --settings <file>`
canary). A hook reading the documented name gets an empty list and passes
silently, so `_BATCH_FIELD` pins the real name with the divergence recorded
beside it. See `RUNTIME_CONSTRAINTS.md`.

### Response format

Block: the standard five-field message via `block_message.emit_block`, naming
the repeated command and its count, then exit 2. Clean batch: no output,
exit 0.

### Opt-out

`PRAXIS_HOOK_BYPASS_PARALLEL_MUTATION=1` skips the gate entirely.

### Relationship to sibling hooks

- `cross-boundary-preflight` owns `GH_WRITE_SUBCOMMANDS`, an allowlist of
  write pairs, for repo-boundary classification on `PreToolUse`. This hook
  deliberately does **not** reuse it: an allowlist is the shape that failed
  here (rule 3 above), and this gate inverts to a read-only exclusion. The two
  answer different questions — "is this a cross-boundary write?" versus "did
  this batch repeat one mutation?" — so sharing the constant would couple them
  wrongly even setting the `sys.path` limit aside.
- `second-failure-advisory` is the other per-hook Claude-only registration and
  was the convention source for the manifest entry shape (`hosts`, `timeout`,
  matcher-less event).

### Tests

`tests/hooks/preflight-gate/test_parallel_gated_mutation_gate.sh` — 26 cases.
Duplicate creations block; distinct targets, distinct nouns, reads, a single
creation and disjoint edit sets pass; the serial-in-one-call form blocks; a
malformed payload fails open; and the documented-but-wrong `tools` field name
does not silently satisfy the gate. Seven cases pin the three repaired silent
passes above (persistent flag before the noun and between noun and verb, target
set reordering, URL-versus-number identity, `-h` as a flag value, `pr revert`,
`issue transfer`), each paired with a passing control. Six more pin the three
repaired false positives (equal numbers in different repositories in both the
URL and `--repo` spellings, targets after the persistent flag, creations in
different repositories), each paired with a blocking control so the fix cannot
be mistaken for the gate simply going quiet.
