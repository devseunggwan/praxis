# PreToolUse gh search --state all Block

Supported hosts: all

`hooks/preflight-gate/block-gh-state-all/impl.py` intercepts every Bash tool call and hard-blocks
the invalid flag combination `gh search <subcmd> ... --state all`.

### Why this exists

`gh issue list` and `gh pr list` accept `--state all`, but `gh search issues`
/ `gh search prs` only accept `--state {open|closed}`. Conflating these
produces `invalid argument "all" for "--state" flag` at runtime. A feedback
memo (`feedback_verify_cli_flags.md`) was tried first but produced 5+
recurrences — structural enforcement replaced the memo.

### What is blocked

The hook uses role-aware structural tokenization (`_hook_utils.tokenize_with_roles`,
issue #263) so that only live `gh search` invocations are matched. Pattern
references inside quoted strings, commit messages, grep patterns, or echo
arguments are transparent pass-throughs.

| Command | Action |
| --------- | -------- |
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

### Rewrite arm — `PRAXIS_BLOCK_GH_STATE_ALL_REWRITE=1` (issue #1334)

Off by default. Exported as `1` (surrounding whitespace is stripped before
the comparison), the hook stops blocking this case and
instead hands the harness the corrected command through
`hookSpecificOutput.updatedInput`, letting the call proceed. Nothing else about
the detection changes — a command this hook did not block is still untouched.

The fix is deterministic, which is what makes it eligible: `--state all` is
invalid for `gh search`, and omitting `--state` returns every state, so there
is exactly one correction and no judgement to make.

Two guards bound it, and both fall back to the ordinary block:

| Guard | Why |
| ----- | --- |
| single segment only | `--state all` is valid for `gh issue list`, so removing it textually across `gh search … && gh issue list --state all` would break the half that was correct |
| token-level readback | the removal is textual (the tokenizer keeps no offsets), then certified: the result must re-tokenize to the original tokens minus exactly the `--state` / `all` pair, and must no longer trip the detector |

A command carrying a backslash line continuation also keeps the block: the
tokenizer normalizes it away, and a correction must change exactly the one
thing it claims to change.

The corrected call is announced on the same object, as
`additionalContext` — `original -> corrected`. Not decoration: the transcript's
`tool_use` record keeps the command the model wrote (measured on Claude Code
2.1.266), so an unannounced rewrite is invisible to the actor, to a reviewer
reading the transcript, and to the praxis hooks that scan prior calls.

Each firing is recorded in the fire ledger with decision `rewrite`
(`_fire_ledger.DECISION_REWRITE`), which is what the promotion-to-default
decision is measured on.

### Tests

```bash
bash tests/hooks/preflight-gate/test_block_gh_state_all.sh
pytest tests/hooks/preflight-gate/test_block_gh_state_all_rewrite.py
```

The shell file covers 29 cases: 10 block paths (including env-prefix, sudo
wrapper, chained segments), 17 pass paths (legitimate gh list,
echo/grep/commit/pr-body false-positive regressions, non-gh commands), non-Bash
tool passthrough, and malformed stdin fail-open.

The pytest file covers only what the arm adds: every spelling and position of
the flag, the arm switch off / `0` / a non-`1` truthy value, the two guards
above, and four lookalikes the arm must leave alone.
