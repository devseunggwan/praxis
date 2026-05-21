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

Retrospect source (Hub #2242 PR #8299, 2026-05-21): the user PR body stated
the cafe24 pattern `COALESCE(..., 'TOSS_SHOPPING')`; a sciomc Stage 5
"sibling-deviant" finding surfaced; the agent auto-committed a flip to
`'Unknown'` without re-reading the PR body or asking; the user redirected
("그냥 cafe24 패턴으로 작성 부탁합니다") and the flip was reverted — costing 2
extra commits, a 2x PR-body rewrite, and reviewer noise.

### What is blocked

All three conditions must hold for a block (exit 2):

1. The Bash command is a content `git commit` — `--amend`, `git merge`,
   `git rebase`, `git cherry-pick`, `git revert`, and `--allow-empty` are
   exempt.
2. The recent transcript tail (last ~200 lines) contains a finding marker:
   `sibling-deviant`, `Stage N 분석/finding/analysis/결과/complete`, `sciomc`,
   `[FINDING:`, `[STAGE_COMPLETE:`, `scientist-agent`, `review finds`,
   `deep-dive`, `cross-validation`, `의미 mismatch`, `의미 충돌`.
3. No consensus re-fetch appears AFTER the most recent finding marker —
   re-fetch is `gh pr view ... --json ... body`, `gh issue view ... --json
   ... body`, `consensus re-fetch`, `re-read PR/issue body`, `user-stated
   design`, or a ratification token.

| Situation | Action |
|-----------|--------|
| `git commit -m "..."` after a `[FINDING:` line, no re-fetch | **BLOCKED** (exit 2) |
| `git commit` after sciomc Stage 5, then `gh pr view N --json body` | **PASS** (re-fetch after finding) |
| `git commit --amend` after a finding | **PASS** (amend exempt) |
| `git revert <sha>` after a finding | **PASS** (non-content op) |
| `git commit -m "fix [user-approved]"` after a finding | **PASS** (ratification token) |
| `git commit` with no finding marker in transcript | **PASS** (no finding context) |

### Escape hatches

- Add `[user-approved]` or `[ratified-by-user]` to the commit message when
  the user explicitly approved the change in this session.
- Set `CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1` for a deliberate one-off bypass.
- Missing / unreadable / oversized (`>50MB`) transcript → silent pass
  (cannot enforce). Malformed stdin → silent fail-open.

### Tests

```bash
bash tests/test_block_sciomc_finding_commit.sh
```

Covers 15 cases: block paths (finding + no re-fetch), silent paths (each
escape hatch, amend / revert exemption, re-fetch after finding, no finding
context), non-Bash tool passthrough, missing `transcript_path`, and malformed
JSON fail-open.
