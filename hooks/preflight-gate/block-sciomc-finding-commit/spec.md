# PreToolUse git commit After Finding Block

Supported hosts: all

`hooks/block-sciomc-finding-commit.sh` intercepts every Bash tool call and
hard-blocks a content `git commit` issued after a sciomc / reviewer finding
when no user-design consensus re-fetch happened in between.

### Why this exists

User-stated design (PR body, issue body, direct utterance) is RATIFIED. AI
analysis findings (sciomc Stage N, deep-dive, review finds, scientist agent
output) are DRAFTS — they must be surfaced to the user, never auto-applied by
flipping the design via a direct commit.

Four converging memory rules already encode this root-cause family
(falsify-before-lock, self-authored option scope, recommendation lock,
consensus re-fetch before lock) and two CLAUDE.md promotions were made
(Output-Block-Level Falsification Gate / Self-Falsify Before Recommendation
Lock). Per the prompt-layer retrieval failure threshold, a 3+ generation
recurrence requires structural enforcement rather than another memo — this
hook enforces the gate at the commit checkpoint.

Retrospect pattern (praxis issue #374): the user's PR body stated a
sibling-convention literal in a SQL `COALESCE(...)` expression; a sciomc
Stage 5 "sibling-deviant" finding surfaced; the agent auto-committed a
flip of the literal without re-reading the PR body or asking; the user
redirected back to the originally-stated design and the flip was reverted
— costing extra commits, a PR-body rewrite, and reviewer-timeline noise.

### What is blocked

All three conditions must hold for a block (exit 2):

1. The Bash command is a content `git commit`. Only `--amend` and the
   non-content ops (`git merge`, `git rebase`, `git cherry-pick`,
   `git revert`) are exempt. `--allow-empty` / `--allow-empty-message` are
   **not** exempt — they only permit an empty commit/message and do not stop
   staged content from riding along, so a finding-gated content commit cannot
   slip through behind them. Commits wrapped in a subshell/group (`(git …)`),
   command substitution (`$(git …)` / `` `git …` ``), or chained behind a
   space-less separator (`true;git commit …`) are detected too.
2. The recent transcript tail (last ~200 lines) contains a sciomc structured
   output marker: `[FINDING:`, `[STAGE_COMPLETE:<digit>]`, `[CONFLICTS:`,
   `sibling-deviant`, `의미 mismatch`, `의미 충돌`. Loose prose terms
   (`sciomc`, `scientist-agent`, `deep-dive`, `cross-validation`, bare
   `Stage N analysis`) are **not** markers — they generated false-positives
   on normal workflow discussion.
3. No consensus re-fetch appears AFTER the most recent finding marker —
   re-fetch is `gh pr view ... --json ... body`, `gh issue view ... --json
   ... body`, `consensus re-fetch`, `re-read PR/issue body`, `user-stated
   design`, or a ratification token.

| Situation | Action |
|-----------|--------|
| `git commit -m "..."` after a `[FINDING:` line, no re-fetch | **BLOCKED** (exit 2) |
| `git commit -m "..."` after a `[CONFLICTS: ...]` line, no re-fetch | **BLOCKED** (exit 2) |
| `git commit -m "..."` after a `[STAGE_COMPLETE:2]` line, no re-fetch | **BLOCKED** (exit 2) |
| `git commit -m "..."` after `sibling-deviant` prose, no re-fetch | **BLOCKED** (exit 2) |
| `git commit -m "..."` after `의미 mismatch` / `의미 충돌`, no re-fetch | **BLOCKED** (exit 2) |
| `git commit` after a marker, then `gh pr view N --json body` | **PASS** (re-fetch after finding) |
| `git commit --amend` after a finding | **PASS** (amend exempt) |
| `git commit --allow-empty -m x` (staged content) after a finding | **BLOCKED** (allow-empty not exempt) |
| `git revert <sha>` after a finding | **PASS** (non-content op) |
| `git commit -m "fix [user-approved]"` after a finding | **PASS** (ratification token) |
| `git commit` with no finding marker in transcript | **PASS** (no finding context) |
| `git commit` after bare `sciomc` / `deep-dive` / `cross-validation` prose | **PASS** (not a marker — FP prevention) |
| `git commit` after bare `scientist-agent` / `Stage N analysis` prose | **PASS** (not a marker — FP prevention) |

### Limitations (threat model: accidental, not adversarial)

The gate targets **accidental momentum commits** (`git commit -m x` after a
review finding), not an adversary crafting a bypass. Command detection covers
plain, grouped (`(git …)`), separator-chained (`true;git commit`), clustered
(`-am`), and command-substitution forms — including quoted (`echo "$(git
commit …)"`), nested (`$(… $(git commit) …)`), escaped-paren, and single-quote
literals (correctly NOT flagged). It does **not** fully replicate bash's parser:
a content commit hidden behind quote state *inside* a `$(…)` span
(e.g. `echo "$(printf ')' ; git commit -m x)"`) can still slip through, because
the substitution scanner does not track quoting within the span. This is an
adversarial construction, not an accidental one — and a deliberate bypass is
already a first-class feature (`[user-approved]` token / `CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1`).
Full shell-grammar parsing (e.g. a `bashlex` dependency) is intentionally out of
scope.

### Escape hatches

- Add `[user-approved]` or `[ratified-by-user]` to the commit message when
  the user explicitly approved the change in this session.
- Set `CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1` for a deliberate one-off bypass.
- Missing / unreadable / oversized (`>50MB`) transcript → silent pass
  (cannot enforce). Malformed stdin → silent fail-open.

### Tests

```bash
bash tests/hooks/preflight-gate/test_block_sciomc_finding_commit.sh
```

Covers 53 cases:
- **Block paths** (22): finding + no re-fetch, `--allow-empty*` with staged
  content, grouped / command-substitution / nested / separator-chained commits,
  `-m --amend` value; new in #429: `[CONFLICTS:]`, `[STAGE_COMPLETE:2]`,
  `sibling-deviant`, `의미 mismatch`, `의미 충돌` markers each individually;
  also `의미 충돌이` / `의미 mismatch가` (Hangul particle attachment forms),
  and `[CONFLICTS: user-stated design ...]` (marker payload must not
  self-satisfy the consensus re-fetch check).
- **Silent paths** (24): each escape hatch, amend / revert exemption,
  `commit-tree` plumbing, quoted-literal `git commit`, single-quoted
  substitution, `git --help/--version commit` terminal options, re-fetch after
  finding, no finding context.
- **FP-regression paths** (6, new in #429): bare `sciomc`, `scientist-agent`,
  `deep-dive`, `cross-validation`, `Stage N analysis` prose, and
  `[STAGE_COMPLETE:` without digit — none of these should block.
- **Non-Bash tool passthrough**, missing `transcript_path`, malformed JSON
  fail-open (1 each).
