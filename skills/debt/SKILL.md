---
name: debt
description: >
  Deferred-decision ledger — harvests commit trailers (`Not-tested:`,
  `Confidence: low`, `Rejected:`, `Directive:`, `Scope-risk:`) and tree
  compounding comments (`# [PR #N]`) into a single report-only ledger so a
  '나중에' marker doesn't silently become '영원히 안 함'.
when_to_use: >
  Triggers on "praxis:debt", "debt ledger", "지연 결정", "deferred decision",
  "기술 부채 원장", "commit trailer audit".
verified-against-runtime: true
runtime-verified-at: 2026-07-01
runtime-verified-note: "git 2.x log -E --grep union (124 hits on praxis main) + git grep -nE (4 hits, tracked-only) — confirmed the issue's compounding-comment example (PR 179) resolves at hooks/advisory-nudge/external-write-falsify-check/impl.py:183; per-alternative colon placement verified to match Confidence: low without a Codex-review round-trip regression"
---

# Praxis Debt

## Overview

praxis already seeds two lightweight deferred-decision markers: commit
trailers (`Not-tested:`, `Confidence: low`, `Rejected:`, `Directive:`,
`Scope-risk:` — see `CLAUDE.md` → Commit Trailers) and compounding comments
(`# [PR #N]` — see `CLAUDE.md` → Compounding). Neither has a harvesting
skill, so a marker written today has no mechanism forcing a future look —
"나중에" quietly becomes "영원히 안 함". `praxis:debt` closes that gap by
harvesting both marker sources into one ledger on demand.

**Core principle:** this skill is report-only. It reads git history and the
tree; it never writes, edits, commits, or creates issues/PRs.

**The two markers live in different places** — a naive tree-only grep (enough
for a tool whose markers are all code comments) misses every commit trailer, because trailers live in commit *messages*, not in tracked
files. This skill unions `git log --grep` (source 1) with a tree `grep`
(source 2) so neither source is silently dropped.

## When to Use

- Periodic debt review — "what did we defer and never revisit?"
- Before a release, to surface `Not-tested:` gaps that may still be open
- When onboarding to a file/module, to see what compounding comments
  (`# [PR #N]`) explain non-obvious decisions nearby

## Process

### Step 1: Harvest commit-trailer markers (source 1 — git history)

```bash
git log -E --grep='(Not-tested:|Confidence: low|Rejected:|Directive:|Scope-risk:)' --format='%H'
```

The colon is attached to each alternative individually, not appended after
the whole group — `Confidence: low` has no trailing colon in the real
trailer, so `(...):` as a single suffix silently drops every
`Confidence: low` hit.

The SHA list alone is not the ledger — pull the trailer lines themselves for
each returned SHA:

```bash
git show -s --format='%h %s' <sha>
git show -s --format='%B' <sha> | grep -E '^(Not-tested:|Confidence: low|Rejected:|Directive:|Scope-risk:)'
```

Default `git log` scope is the current branch's ancestry (HEAD) — sufficient
for praxis's single-trunk (`main`) history. Do not add `--all` unless the
user explicitly asks for cross-branch/tag coverage; it changes the count and
can pull in abandoned branches.

### Step 2: Harvest tree compounding-comment markers (source 2 — tracked files)

```bash
git grep -nE '# \[PR #[0-9]+\]'
```

`git grep` scopes to tracked files only (no `--untracked` flag) — a plain
`grep -rn ... .` would also sweep untracked scratch files, caches, and
gitignored build artifacts in a dirty worktree, polluting the ledger with
markers that were never committed.

### Step 3: Classify each hit

For every marker (both sources), produce one ledger row:

```text
<source> — <what was deferred>. <ceiling>. <revisit trigger>
```

- **source**: commit trailer → `<short-sha> (<trailer-type>)`; tree comment
  → `<file>:<line>`
- **what was deferred**: the trailer value or comment text, verbatim
  (truncate only if it spans multiple lines — keep the first sentence)
- **ceiling**: the stated boundary/scope of the deferral if the text names
  one (`Scope-risk: narrow/moderate/broad`, an explicit "until X" / "holds
  as long as Y" clause, a named follow-up issue). If the text states no
  boundary, write `unbounded`.
- **revisit trigger**: a concrete condition or event named in the text that
  should prompt someone to look again (a linked issue number, "when X
  ships", "once N happens", "revisit after Y"). If none is stated, tag the
  row `no-trigger` — do not invent one.

### Step 4: Group and report

Group rows under two headers, `## Commit Trailers` and `## Compounding
Comments`. Within each, list rows in reverse-chronological / file order.
After both groups, print a one-line summary: total marker count, and how
many are tagged `no-trigger` (the count that matters most — these are the
markers most likely to rot).

If Step 1 and Step 2 both return zero hits, report exactly: `Clean ledger.`

## Error Handling

| Error | Recovery |
| ------- | ---------- |
| Not inside a git repository | Abort — this skill requires `git log` |
| One source is empty, the other has hits | Report only the non-empty source; an empty source is not an error |
| A commit trailer line is malformed (e.g. missing colon) | Skip it — do not guess intent |

## Limitations

- Read-only: this skill does not create issues, PRs, or edit any file. If a
  ledger row warrants action, that is a separate, explicitly-approved next
  step — surface it, don't act on it.
- `git log` default scope is the current branch only — see Step 1.
- Trigger detection is a text-pattern judgment call, not a formal parser;
  borderline cases should be tagged conservatively (`no-trigger` over a
  guessed trigger).
