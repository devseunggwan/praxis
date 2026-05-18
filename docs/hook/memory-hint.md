# PreToolUse Memory Hint

Supported hosts: all

`hooks/memory-hint.py` fires on every Bash tool call and emits stderr lines
referencing user-scoped memory files whose YAML frontmatter declares
`hookable: true` plus a matching `hookKeywords: [...]` token. The signal is
purely attention-shifting for the LLM's next reasoning step — the hook
**never blocks**, **never asks**, **always exits 0**.

### Why this exists

Some friction patterns can be matched with structural enforcement (e.g.
`block-gh-state-all`, `gh-flag-verify`). Others — like "verify the CLI flag
before using it" — only show up as soft-textual rules in memory files. Without a hook,
Claude only retrieves those memories *after* a failure, not while
constructing the tool call. This hook surfaces them at decision-construction
time so the LLM has the option to reconsider before executing.

Reference: issue [#139](https://github.com/devseunggwan/praxis/issues/139).

### Frontmatter contract

Memory authors opt in by adding two fields to their existing frontmatter:

```yaml
---
name: my-memory
description: Short rule statement
type: feedback
hookable: true                           # NEW — opt-in switch
hookKeywords: [kubectl, hubctl, gh]      # NEW — match tokens (whole-token)
---
```

| Field | Required | Notes |
|-------|----------|-------|
| `hookable` | yes | scalar; `true` / `True` / `TRUE` / `yes` / `Yes` enable. Anything else (or missing) → not indexed. |
| `hookKeywords` | yes | flat single-line list, e.g. `[a, b, "c d"]`. Scalar form (`hookKeywords: kubectl`) is **rejected** — the memory is silently skipped. |
| `description` | no | optional; emitted after em-dash when present. Without it, the stderr line is just `[memory:hookable] {filename}`. |
| `type` | no | unrelated taxonomy field; the parser ignores it. `type` ≠ `hookable`. |

### Hook execution model

Per Anthropic docs (https://code.claude.com/docs/en/hooks), PreToolUse hooks
run **in parallel**. Array position in `hooks.json` is presentational, not a
priority gate. When multiple hooks return decisions, precedence is
`deny > defer > ask > allow`. `memory-hint` only emits stderr (no
`permissionDecision`) so it co-fires with the blocking hooks — the user may
see a hint line *alongside* a block from `block-gh-state-all` (exit 2) or an
`ask` from `side-effect-scan`. Co-firing is intentional: the hint can
clarify *why* a block fired.

### Matching semantics

- **Whole-token equality, case-sensitive.** `hookKeywords: [kubectl]` matches
  the bare token `kubectl` but NOT `Kubectl`, `KUBECTL`, or `kubectl-prod`.
  Authors who want case-insensitive matching list the casings explicitly.
- Quoted **multi-word** strings are preserved as a single token (shlex
  behavior). `echo "use kubectl"` parses tokens as `[echo, use kubectl]`
  (the quotes are stripped, the spaces stay) — the keyword `kubectl` does
  NOT match the multi-word token. This is the false-positive guard.
- Quoted **single-word** strings are NOT protected — shlex strips the quotes
  and the bare word is matched normally. `gh search issues "kubectl"` ends
  up as `[gh, search, issues, kubectl]` and matches the `kubectl` keyword.
- Comment-prefixed lines (`# kubectl get`) DO match. `safe_tokenize` runs
  with `commenters = ""` so `#` is just another token; `kubectl` ends up as
  argv[1] of the segment and is matched. Niche case; accepted v1 behavior.

### Discovery and fail-safe

Memory directory resolution order:
1. `PRAXIS_MEMORY_DIR` env var (when set + points to an existing directory)
2. fallback `~/.claude/projects/{slugified-cwd}/memory/` — slugify rule:
   replace `/` with `-` on the absolute cwd
3. neither resolves → exit 0 silently (no fallback attempt, no error)

Five exit-0 fail-safe paths:
- python3 missing (shell wrapper guard)
- malformed JSON stdin
- non-Bash tool
- empty command
- memory directory missing or unreadable

### YAML parser limits

Pure-regex parser, no PyYAML dependency. Supported shapes: flat scalar
`hookable`, flat single-line list `hookKeywords` (e.g. `[a, b, "c d"]`),
optional `description`. Multi-line / flow-mapping / anchored YAML forms are
NOT supported — any parse error skips that memory, never the hook.

### Tests

```bash
bash tests/test_memory_hint.sh
```

Covers 21 cases: hit/silent core paths, frontmatter gates, noise cap, mtime
ordering, discovery fail-safes, malformed inputs, AC-21 (no description),
AC-22 (scalar rejection), AC-23 (case sensitivity).
