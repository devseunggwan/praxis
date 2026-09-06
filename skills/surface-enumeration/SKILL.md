---
name: surface-enumeration
description: >
  Pre-implementation input-surface enumeration — before writing a
  parser/validator/sanitizer/classifier, enumerate every input variant so each
  becomes a required test case.
when_to_use: >
  Triggers on "surface enumerate", "input surface enumeration", "input parser",
  "input validation", "intent classifier", "정규식 경계", "입력 표면 열거",
  "multi-PR shared state", "convention guide reflection", "apply lesson".
  Do NOT activate on "surface a question", "user-facing surface", or merely
  explaining an existing parser.
verified-against-runtime: true
runtime-verified-at: 2026-07-14
runtime-verified-note: "python3 3.14.5 — SKILL.md Step 4 regex examples executed live: re.search(r'\\bpush\\b', 'push할까요?') → False (Unicode \\w suppresses the ASCII word boundary between 'h' and '할'), re.search(r'(?<![a-z])push(?![a-z])', 'push할까요?') → True; confirms the mixed-text word-boundary guidance in Step 2."
---

# surface-enumeration

## Overview

Code that validates, classifies, routes, sanitizes, or interprets variable input
fails one missed variant at a time. A reviewer finds case A, you fix it, the next
round finds case B, and so on — each round is a full round-trip because the input
surface was never enumerated up front. This skill front-loads that enumeration:
list the variants **before** implementing, turn each into a test case, and re-run
the full list after every fix.

**Core principle:** enumerate the input surface before writing the handler — a
variant you did not list is a variant you did not test.

## When to Use

- Implementing anything that inspects variable input: SQL/shell/markup parsers,
  sanitizers, auth/validation checks, intent/command classifiers, tokenizers,
  routers, regex matchers.
- A reviewer (Codex / BugBot / human) just found a missed input case — before
  fixing only that case, enumerate the whole surface so the next round is empty.
- Designing a mock/fixture for input-handling code (each enumerated variant is a
  fixture case, including a must-fail case).
- Dispatching N independent PRs / worktrees in parallel, or authoring a
  lesson-reflection / convention-guideline update — enumerate the shared-file
  collision surface, or the source guideline's own §1~§N matrix, before starting.

Skip for code with a fixed, closed input (a function called only with an enum you
control) or a single-token mechanical transform with no interpretation.

## Process

### Step 1: Classify the input-handling code

Name what the code does to the input — this selects which variant families apply:

| Code class | Example | Highest-risk families |
| ---------- | ------- | --------------------- |
| Security / validation | SQL filter, sanitizer, auth check | keyword-in-literal, marker-in-comment, multi-statement, quote mismatch |
| NLP / signal detection | intent classifier, command-signal hook | affirmation, **negation**, **status/question**, continuation-without-headline, substring-vs-word-boundary |
| Regex over mixed text | Korean+English command strings | ASCII word-boundary in Unicode (`\b` is Unicode-aware) |
| Text / grammar parser | shell/SQL/markup tokenizer | comment/heredoc stripping, quoting variants, command-position-only keywords, direction-sensitive ranges, path-invoked binaries, dynamic expansions |
| Multi-PR / shared state | parallel PR dispatch, multi-worktree | shared-file collision (`hooks.json`, `CLAUDE.md`/`AGENTS.md` + symlink targets, `marketplace.json`, `plugin.json`, README/docs hook-index), first-merge-wins rebase |
| Convention-guideline reflection | lesson-reflection PR, skill update from source-PR findings | oracle = the guideline's own §1~§N matrix (not the surfaced finding list), lint-bypass silent-merge |
| Bulk / stateful | 100+ item loop, multi-run write | connection lifecycle, per-item isolation, idempotency (see limitations) |

### Step 2: Enumerate the surface

Walk the checklist and write down each variant that applies. Do not stop at the
"obvious" ones — the missed variant is by definition not obvious.

- **Escape / quoting**: single vs double quote — in SQL `'--'` is a string
  literal while `"--"` is a quoted identifier, so they take different parse
  paths even though the enclosed value is identical; also escaped quote,
  backslash, nested quotes.
- **Comment / marker position**: keyword inside a string literal; comment marker
  inside a literal; literal marker inside a comment.
- **Multi-statement / separator**: semicolon-joined statements, newline-joined.
- **Negation forms**: `don't proceed`, `진행하지 마`, `not X` — a signal detector
  that only matches the affirmative form misfires on the negation.
- **Status / question forms**: `진행 상황을 알려줘`, `should I push?` — a question
  about X is not a command to do X.
- **Continuation without the headline token**: `계속해` ("keep going") vs
  `계속 진행` ("continue proceeding") — the token you keyed on may be absent.
- **Substring vs word boundary**: `Product plan` vs `production`, `prod` vs
  `prod-only`. In Korean+English mixed text Python `re.\w` is Unicode-aware, so
  `\bpush\b` does NOT separate `push` from `할까요`; use `(?<![a-z])foo(?![a-z])`.
- **Non-executable text**: comments, heredoc/string bodies that must be stripped
  before parsing; command-position-only reserved words (`echo done` does not
  close a loop); direction-sensitive ranges (`{30..1}`); path-invoked binaries
  (`/bin/sleep` vs bare `sleep`); dynamic expansions (`$(...)`, `$VAR`) needing
  explicit fail-open.
- **Encoding / container / integrity**: alternate encodings, wrapped/nested
  container formats, missing field, truncated/corrupt payload.

Cover **both** dimensions: semantic coverage (count/threshold math) does NOT
substitute for syntactic coverage (the variants above), and vice versa. Scope
each construct independently — never merge tokens across sibling constructs into
one verdict.

Two enumeration surfaces are process-level rather than string-input — they apply
when the surface being enumerated is a set of PRs or a source guideline, not a
value being parsed:

- **Multi-PR / multi-worktree shared state**: when dispatching independent PRs
  in parallel, enumerate every file each PR touches that a sibling also touches —
  `hooks.json`, `AGENTS.md`/`CLAUDE.md` (and their symlink targets),
  `marketplace.json`, `plugin.json`, hook-index tables in `README`/`docs/`. First
  PR to merge wins; each sibling then needs a rebase.
- **Convention-guideline reflection artifacts**: when authoring a "lessons from
  PR X about guideline G" artifact (lesson-reflection PR, skill update reflecting
  upstream/source-PR findings), the enumeration oracle is **G itself — its §1~§N
  matrix** — not the externally-surfaced finding list (BugBot, Codex,
  sibling-conflict, mode dispatch). Sweep §1~§N against every commit in the
  source PR, or lint-bypass violations (deprecated import, hardcoded literals,
  `any` typing, missing i18n) silent-merge. Trigger phrases: "apply lesson",
  "skill update", "convention guide update".

### Step 3: Promote each variant to a test case

Every enumerated variant becomes a required test case, including at least one
**must-fail** case (input that should be rejected → assert the error/raise, not a
silent default branch). For mocks/fixtures, cite field names from vendor docs or
a working baseline — never mirror them from the code under test (that produces a
tautological pass).

### Step 4: Verify each test input actually reproduces its variant

An input that does not reproduce the intended variant gives a false pass. Confirm
before relying on it:

```bash
# word-boundary claim in mixed text — verify the regex actually behaves as assumed
python3 -c "import re; print(bool(re.search(r'\bpush\b', 'push할까요?')))"
# SQL marker — '--' (string literal) parses differently than "--" (identifier)
```

### Step 5: Re-run the full list after every fix

When a reviewer finds a new case, add it to the enumeration and re-run the
**entire** list — not just the one new test. "Unit tests pass" ≠ "surface
covered". The goal is a review round that discovers nothing new.

## Limitations

- Enumeration is a discipline, not a solver — it lists variant *families*; the
  concrete variants still require domain knowledge of the input format.
- Bulk/stateful concerns (connection lifecycle, per-item failure isolation,
  reconnect policy, partial-progress checkpointing, write-path idempotency for
  repeated runs) are named here but their full treatment is a separate concern —
  enumerate them explicitly when the code mutates an external system in a loop.
- Does not enforce at the tool layer; it is knowledge injection. A structural
  gate (e.g. a commit-time hook) is a complementary, separate mechanism.
