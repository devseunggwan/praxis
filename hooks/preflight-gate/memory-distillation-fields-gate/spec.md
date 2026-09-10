# PreToolUse Memory Distillation Fields Gate

Supported hosts: all

`hooks/preflight-gate/memory-distillation-fields-gate/impl.py` fires on every
PreToolUse event for `Write` and blocks a write to a session memory entry whose
frontmatter is missing the three distillation fields, or carries them at the
top level instead of nested under `metadata:`.

## Decision predicate

Block when **all** of these hold:

1. `tool_name` is `Write`.
2. `file_path` ends in `.md`, its basename is not `MEMORY.md` /
   `MEMORY-reference.md`, and its directory **is** the resolved memory
   directory (`resolve_memory_dir()` — `PRAXIS_MEMORY_DIR`, else
   `<claude_config_dir>/projects/<slug>/memory`).
3. The written content's YAML frontmatter is absent, or any of `recurrence`,
   `enforcement`, `escalated_to` is missing in the two-space-nested form
   (`^  <field>:`), or any of them appears flat at the top level
   (`^<field>:`).

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
prints N/A and exits 0 there. The two do not overlap: neither the field set nor
the property being asserted is shared.

`bulk-write-memory-checkpoint` also does not cover this. It matches the memory
directory by exact path component `.claude`, so a store under
`.claude-4/projects/<slug>/memory` never matches.

## Fail-open

Unparseable stdin, a non-`Write` tool, an absent `file_path`, a non-string
`content`, and an unresolvable memory directory all exit 0. The gate can only
block on a positively identified memory entry.
