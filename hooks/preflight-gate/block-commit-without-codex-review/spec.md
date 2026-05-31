# PreToolUse git commit Without codex-review-wrap Block

Supported hosts: claude

`hooks/block-commit-without-codex-review.sh` intercepts every Bash tool call and
hard-blocks a content `git commit` when `praxis:codex-review-wrap` has not been
invoked anywhere in the current session.

### Why this exists

The global workflow (`AGENTS.md` `Deliver` table, devseunggwan/ai-dotfiles#93)
lists `praxis:codex-review-wrap` as a second mandatory independent review pass
before commit — an independent Codex pass after `omc:code-reviewer` that catches
defects a single reviewer misses. Prose alone is unreliable (prompt-layer
retrieval failure); per the established escalation pattern, structural
enforcement at the commit checkpoint backs the rule.

This is the inverse of [`block-sciomc-finding-commit`](../block-sciomc-finding-commit/spec.md):
that hook blocks on the **presence** of a finding marker; this one blocks on the
**absence** of the required skill invocation.

### What is blocked

Both conditions must hold for a block (exit 2):

1. The Bash command is a content `git commit` — `--amend`, `git merge`,
   `git rebase`, `git cherry-pick`, and `git revert` are exempt. `--allow-empty`
   / `--allow-empty-message` are **not** exempt: they only permit an empty
   commit / message and do not prevent staged content from being committed, so
   a content commit using them is still gated (use the skip token or env bypass
   for an intentional empty CI-trigger commit).
2. The session transcript contains no `Skill` tool_use with
   `input.skill == "praxis:codex-review-wrap"` and no `/praxis:codex-review-wrap`
   slash-command invocation (a prose mention such as "should I run
   /praxis:codex-review-wrap?" does not count — the match is line-anchored).

| Situation | Action |
|-----------|--------|
| `git commit -m "..."` with no codex-review-wrap invocation this session | **BLOCKED** (exit 2) |
| `git commit` after a `Skill(praxis:codex-review-wrap)` tool_use | **PASS** (review ran) |
| `git commit` after a `/praxis:codex-review-wrap` slash command | **PASS** (review ran) |
| `git commit --amend` / `git revert` / `git merge` | **PASS** (non-content / exempt) |
| `git commit -m "docs [skip-codex-review]"` | **PASS** (skip token) |

Granularity is session-level: one codex-review-wrap invocation satisfies all
subsequent commits in the same session (whole-transcript scan), matching the
other commit / PR review gates.

### Escape hatches

- Add a `[skip-codex-review]` token to the commit `-m` / `--message` message
  for a deliberate skip (e.g. a trivial docs / typo change). A token elsewhere
  in a compound command does not count — for `-F file` / heredoc commits use
  the env bypass instead.
- Set `CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE=1` for a one-off bypass.
- Missing / unreadable / oversized (`>50MB`) transcript → silent pass (cannot
  enforce). Malformed stdin or an unparseable command (unbalanced quotes) →
  silent fail-open.

### Implementation note — token-level classification

Detection operates on `shlex` tokens, not the raw command string. This avoids
three unsound matches a raw-string regex would make: `--amend` inside a `-m`
message would falsely exempt a content commit; `git commit-tree` would falsely
match `commit` via a `\b` boundary; and `echo "git commit"` /
`git log --grep="git commit"` would falsely trip the gate on a non-commit
command. A `git commit` invocation is recognised only as a token-level
`git` → `commit` adjacency.

The tokenizer uses `shlex.shlex(punctuation_chars=True)` (ported from
`block-sciomc-finding-commit` PR #445) which splits shell operators (`;`, `|`,
`(`, `)`) into their own tokens even without surrounding spaces. A hybrid
two-layer approach is used:

1. **Direct tokenisation** detects plain commits, grouped commits `(git commit
   …)`, unquoted command-substitution `$(git commit …)`, and separator-chained
   forms `true;git commit …`.
2. **Span scan** (`_extract_substitutions`) detects commits inside
   double-quoted command-substitution spans `"$(git commit …)"` that `shlex`
   folds into a single token.

Single-quoted text `'$(git commit …)'` is treated as a literal string (bash
does not execute it) — correctly ignored.

### Tests

```bash
bash tests/hooks/preflight-gate/test_block_commit_without_codex_review.sh
```

Covers 47 cases: block paths (no invocation, wrong skill, `-F` body, prose-only
slash mention), pass paths (Skill tool_use, slash command, garbage-line
resilience), exemptions (amend / allow-empty / merge / revert / rebase /
cherry-pick), escape hatches (`-m` skip token incl. joined / `--message=`
forms, env bypass), token-level edge cases (`git commit-tree` plumbing,
`echo "git commit"`, `git log --grep`, `--amend` inside the message, skip
token outside the message via `;` / `&&`, commit after `&&`),
hardened-parser bypass forms (grouped `(git commit …)`, unquoted substitution
`$(git commit …)`, no-space separator `true;git commit …`, nested substitution,
quoted substitution, single-quoted literal pass, double-quoted literal pass,
terminal options `--help`/`--version`), out-of-scope (non-Bash tool, `git push`
/ `git status`), and fail-open (no `transcript_path`, nonexistent path,
malformed stdin, unparseable command).
