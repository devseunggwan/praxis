# PreToolUse Memory Distillation Fields Gate

Supported hosts: all

`hooks/preflight-gate/memory-distillation-fields-gate/impl.py` fires on every
PreToolUse event for `Write` and blocks a write to a session memory entry that
would go dark in either of two ways: its frontmatter is missing the three
distillation fields (or carries them at the top level instead of nested under
`metadata:`), or it claims `hookable: true` with a `hookKeywords:` shape the
hint index cannot read.

## Decision predicate

Block when **all** of these hold:

1. `tool_name` is `Write`.
2. `file_path` ends in `.md`, its basename is not `MEMORY.md` /
   `MEMORY-reference.md`, and its directory **is** the resolved memory
   directory (`resolve_memory_dir()` — `PRAXIS_MEMORY_DIR`, else
   `<claude_config_dir>/projects/<slug>/memory`).
3. Either:
   - **the distillation fields** — the written content's YAML frontmatter is
     absent, or any of `recurrence`, `enforcement`, `escalated_to` is missing
     in the two-space-nested form (`^  <field>:`), or any of them appears flat
     at the top level (`^<field>:`); **or**
   - **the hint-index shape** (#1426) — the frontmatter carries
     `hookable: true` and `_lib/_memory_frontmatter.dark_memory_shape()` names
     a shape: `block-list`, `scalar`, `unclosed`, `empty`, or `absent`.

The two are checked in that order, so no write that was already blocked
changes the message it had. A file wrong on both axes reports the distillation
fields.

Anything else passes. Bypass: `PRAXIS_HOOK_BYPASS_MEMORY_FIELDS` set to any
non-empty value.

The directory test is identity (`os.path.samefile`), not a path substring. A
repository file that happens to sit under some `memory/` folder is not governed
by this convention, and a substring match would block it.

## Why this exists

The distillation convention nests three fields under `metadata:` in every
session memory:

- `recurrence` — how many times the pattern recurred while a rule for it
  already existed. The writer raises it; a later grep never infers it.
- `enforcement` — one of `none|hook|skill|claudemd|declined|n-a`.
- `escalated_to` — where the entry was promoted, or `none`.

The promotion queue is a two-stage grep keyed on the nested form:

```bash
grep -l '^  enforcement: none$' *.md | xargs -r grep -l '^  recurrence: [3-9]'
```

An entry missing any of the three, or carrying them flat, never reaches stage
one. It is not rejected — it is **invisible**, while looking perfectly
well-formed on disk. A user-designated top-priority item sat outside the queue
by exactly this route.

Measured on one local corpus of 543 entries: 170 carry all three nested, 368
are missing at least one, 5 have them flat — 373 (68%) invisible to the queue.
Compliance over the last 7 days is 96%, over 30 days 86%, over 90 days 56%, so
this closes a residual leak rather than stemming a flood. Both recent misses
had **none** of the three fields, meaning the convention was skipped wholesale
rather than partially.

## The second check: a hookable memory the index cannot read (#1426)

`memory-hint` builds its index by parsing each entry's frontmatter, and it
returns `None` — dropping the **whole** entry — for every `hookKeywords:` shape
but the single-line bracket list. The entry is not rejected and nothing reports
it: the memory is written, looks well-formed, and never fires again.

| Shape | Example | Indexed |
| ----- | ------- | ------- |
| flat list | `hookKeywords: [git, push]` | yes |
| flat list, trailing comment | `hookKeywords: [git] # why` | yes |
| bracket on the next line | `hookKeywords:` then `[git, push]` on the next line | yes — see below |
| `block-list` | `hookKeywords:` then `- git` on the next line | no |
| `scalar` | `hookKeywords: git` | no |
| `unclosed` | `hookKeywords: [git, push` | no |
| `empty` | `hookKeywords: []` | no |
| `absent` | no `hookKeywords:` key at all | no |

`hookable: false` passes at any shape: nothing indexes the entry, so no shape
can hide it from anything.

**The bracket-on-the-next-line row is load-bearing.** The runtime's own regex
puts `\s*` after the colon, and `\s` spans a newline, so that shape **is**
indexed. Narrowing it would be a behaviour change to `memory-hint` wearing the
shape of a refactor, so the helper preserves it and pins it with a test.

### One predicate, not a fourth copy of it

Three readers need this answer: `memory-hint` (the runtime that drops the
entry), `scripts/check-memory-frontmatter.py` (the after-the-fact lint), and
this gate. It lives in `hooks/_lib/_memory_frontmatter.py` and **the runtime
imports it**, so the gate asks the runtime's own question rather than a
likeness of it.

Issue #1094 is why that matters. The lint had its own hand-kept copy of the
`hookable:` truthiness rule, the copy omitted the inline-comment strip, and
`hookable: true # enabled` read as non-truthy in the lint and truthy at
runtime — so a dark memory went unflagged by the check written to find it.
Adding a fourth copy here would have rebuilt that setup.

`tests/test_memory_frontmatter_lib.py` runs every fixture through the helper
**and** through the real `memory-hint` parser and requires they agree. That
test found a live disagreement in the opposite direction while this change was
being written: the lint reported a next-line bracket as "drops the entire
memory", which was false — the runtime indexes it. The lint now takes its
verdict from the helper and keeps its own per-shape wording.

### Corpus

Every local memory entry — 4452 files across 138 memory directories under
`~/.claude*/projects/*/memory/*.md`, measured with the shipped helper:

```text
   4026  (hookable not true — out of scope)
    396  indexed
     18  block-list
      6  (no frontmatter)
      6  absent

hookable: true entries : 420
  indexed              : 396
  gate would block     : 24  (5.7%)
```

Two notes on reading it. The `absent` shape — `hookable: true` with no
`hookKeywords:` key at all — is 6 of the 24, and a predicate keyed on "the
file has a `hookKeywords` key" cannot see any of them; that is why the gate's
predicate is keyed on `hookable:` instead. And this is a population, not a
backlog: the gate runs at write time, so it does not retro-fix the 24 entries
that already exist.

## Why a block rather than an advisory

The check is a presence test on three literal keys. It has no judgement to get
wrong, and it costs a compliant write nothing. The failure it prevents is
silent and only observable much later, from the absence of something in a queue
nobody re-derives — which is the class of failure an advisory is worst at,
because there is no second signal to make the ignored warning matter.

## Relationship to `scripts/check-memory-frontmatter.py`

That lint checks the *position* of a different field set — the taxonomy fields
`node_type`, `type`, `originSessionId`, `hookable`, `hookKeywords`,
`hookEvents`, `modified` — and never the *presence* of these three. Its own
docstring records that the memory directory is structurally absent in CI, so it
prints N/A and exits 0 there.

They share exactly one check: the `hookKeywords` shape. Both call
`hookkeywords_shape` / `dark_memory_shape` from `_lib/_memory_frontmatter.py`,
so they cannot disagree on whether an entry is dark (see "One predicate" above).
Everything else stays separate — the lint's field-*position* checks on the
taxonomy fields, and this gate's field-*presence* check on the three
distillation fields — and so does when they run: the lint when someone runs it,
the gate on every write.

`bulk-write-memory-checkpoint` also does not cover this. It matches the memory
directory by exact path component `.claude`, so a store under
`.claude-4/projects/<slug>/memory` never matches.

## Fail-open

Unparseable stdin, a non-`Write` tool, an absent `file_path`, a non-string
`content`, and an unresolvable memory directory all exit 0. The gate can only
block on a positively identified memory entry.
