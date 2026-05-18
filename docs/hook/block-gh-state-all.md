# PreToolUse gh search --state all Block

Supported hosts: all

`hooks/block-gh-state-all.sh` intercepts every Bash tool call and hard-blocks
the invalid flag combination `gh search <subcmd> ... --state all`.

### Why this exists

`gh issue list` and `gh pr list` accept `--state all`, but `gh search issues`
/ `gh search prs` only accept `--state {open|closed}`. Conflating these
produces `invalid argument "all" for "--state" flag` at runtime. A feedback
memo (`feedback_verify_cli_flags.md`) was tried first but produced 5+
recurrences — structural enforcement replaced the memo.

### What is blocked

The hook uses structural tokenization (`safe_tokenize` → `iter_command_starts` →
`strip_prefix`) so that only live `gh search` invocations are matched. Pattern
references inside quoted strings, commit messages, grep patterns, or echo
arguments are transparent pass-throughs.

| Command | Action |
|---------|--------|
| `gh search issues "q" --state all` | **BLOCKED** (exit 2) |
| `gh search prs "q" --state=all` | **BLOCKED** (exit 2) |
| `gh search repos foo --limit 1 --state all` | **BLOCKED** (exit 2) |
| `FOO=1 gh search issues "q" --state all` | **BLOCKED** (env prefix peeled) |
| `sudo gh search issues "q" --state all` | **BLOCKED** (wrapper peeled) |
| `echo x && gh search issues "q" --state all` | **BLOCKED** (chained segment) |
| `gh issue list --state all` | **PASS** (legitimate usage) |
| `gh pr list --state all` | **PASS** (legitimate usage) |
| `gh search issues "q" --state open` | **PASS** |
| `gh search issues "q"` (no --state) | **PASS** |
| `gh pr create --body "describes --state all"` | **PASS** (body literal) |
| `git commit -m "note --state all impact"` | **PASS** (non-gh command) |
| `grep -- "--state all" docs.md` | **PASS** (grep pattern) |
| `echo "--state all is invalid"` | **PASS** (echo argument) |

### Compound cascade advisory (issue #229)

If the blocked `gh search` segment is part of a compound Bash command that
also contains a state-changing step (e.g. `mkdir -p /tmp/cache && gh search
... --state all`), the stderr block message is suffixed with the shared
`_hook_utils.compound_cascade_hint` text. The cascade reminder makes clear
that the preceding `mkdir`/redirect/download also did not run, so retries
should not assume the partial side-effects landed.

### Workarounds when --state all is needed

- Omit `--state` entirely — `gh search` returns results regardless of state by default.
- Run two calls: `--state open` then `--state closed`, then merge results.

### Tests

```bash
bash hooks/test-block-gh-state-all.sh
```

Covers 29 cases: 10 block paths (including env-prefix, sudo wrapper, chained
segments), 17 pass paths (legitimate gh list, echo/grep/commit/pr-body false-positive
regressions, non-gh commands), non-Bash tool passthrough, and malformed stdin
fail-open.
