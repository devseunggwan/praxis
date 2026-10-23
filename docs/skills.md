# Skills

The full skill reference. Each row names the situation a skill handles and the
phrases that trigger it, so it doubles as the starting point if you are new. For
what praxis is and how to install it, see [`README.md`](../README.md).

> **Invocation**: praxis entries are *skills*, not subagents. Always call them
> via `Skill(skill="praxis:<name>")`. `Agent(subagent_type="praxis:<name>")`
> returns `Agent type not found` — Agent and Skill resolve disjoint namespaces.
> See [RUNTIME_CONSTRAINTS.md §3](../RUNTIME_CONSTRAINTS.md) for the mapping table.
>
> **Trigger keywords** mirror each skill's `SKILL.md` `when_to_use` field verbatim
> (`scripts/check-plugin-manifests.py` Rule 13e enforces the mirror, and still reads
> `description` so a clause that has not moved is checked rather than exempt) and are
> intentionally kept in their source language (some are Korean) so this page
> stays in sync with what actually triggers the skill — do not translate them.

To author a new SKILL.md, read [`skill-authoring.md`](skill-authoring.md).

## Development

| Skill | Trigger keywords | When to use | Example invocation |
| ------- | ----------------- | ------------- | ------------------- |
| `retrospect` | `retrospect`, `what went wrong`, `session review`, `session improvement`, `what was the issue`, `improve` | To analyze friction patterns / root causes after a session and act on improvements | `/praxis:retrospect` |
| `codex-review-wrap` | `codex review`, `review codex`, `safe review`, `/codex-review-wrap`, `premise verification`, `flip detection`, `sibling defect`, `sibling cross-check`, `diminishing returns`, `broker reap`, `finding approval`, `적용 승인` | To run `/codex:review` safely in multi-worktree setups, with premise verification and flip detection | `/praxis:codex-review-wrap` |
| `surface-enumeration` | `surface enumerate`, `input surface enumeration`, `input parser`, `input validation`, `intent classifier`, `정규식 경계`, `입력 표면 열거`, `multi-PR shared state`, `convention guide reflection`, `apply lesson` | To enumerate every input variant before implementing a parser/validator/sanitizer so each becomes a required test case | `/praxis:surface-enumeration` |
| `merge-briefing` | `merge briefing`, `pre-merge briefing`, `머지 브리핑`, `머지해도 되나`, `approve merge`, `pre-ask probe`, `merge approval` | To probe all three finding surfaces, grade every finding by its blocking decoration, carry anchor `Unverified` gaps, and surface the six-part briefing before asking for merge approval | `/praxis:merge-briefing` |
| `worktree-merge-cleanup` | `merge cleanup`, `post-merge cleanup`, `worktree cleanup`, `delete-branch merge`, `squash-ancestry`, `pre-merge worktree`, `머지 후 정리`, `worktree 정리`, `PR merged`, `머지했습니다` | To run `gh pr merge --squash --delete-branch` from the right worktree and clean up afterward (submodule `--force`, squash-ancestry guard, no-`&&`-chain), and to clean up after a reported merge (without asking only under a standing approval in the user's instructions) | `/praxis:worktree-merge-cleanup` |
| `perf-leak-review` | `perf review`, `leak review`, `performance defect`, `memory leak`, `성능 결함 점검`, `메모리 누수 점검` | To have a read-only LLM reviewer point at performance-defect and memory-leak candidates in a diff, within a closed list of five classes | `/praxis:perf-leak-review` |
| `tradeoff` | `tradeoff`, `trade-off`, `트레이드오프`, `트레이드 오프` | To weigh the options at an implementation fork — or whether to act on a review finding, and whether it blocks — on four axes, with a provenance grade on every cell | `/praxis:tradeoff` |
| `test-suite-speedup` | `test suite speedup`, `slow test suite`, `slow CI tests`, `CI test time`, `테스트 속도 개선`, `CI 테스트 느림` | To make a slow test suite or CI test job faster — rank the cost on CI, prefer structural fixes, probe parallel safety — and accept it only on a measured CI before/after | `/praxis:test-suite-speedup` |
| `test-suite-consolidation` | `test consolidation`, `copied fixtures`, `duplicate test helpers`, `prune tests`, `unnecessary tests`, `테스트 정리`, `불필요한 테스트` | To move copied test fixtures/helpers into shared locations behind a copy guard, or to prune unnecessary tests within a user-approved scope, with a collected-test-ID diff as the oracle | `/praxis:test-suite-consolidation` |
| `stale-pr-revival` | `revive stale PR`, `stalled PR`, `dependabot PR`, `renovate PR`, `retarget PR`, `묵은 PR 살리기` | To bring a stalled or bot version-bump PR current by merging the base in (never rebase), apply its reviews, and ready the merge briefing | `/praxis:stale-pr-revival` |
| `upstream-bug-report` | `upstream bug report`, `file upstream issue`, `report upstream`, `vet fix PR`, `업스트림 이슈` | To take a third-party defect from local diagnosis to an approved public issue reproduced on pristine upstream, and to vet a contributor's fix PR against it | `/praxis:upstream-bug-report` |

## Discipline

| Skill | Trigger keywords | When to use | Example invocation |
| ------- | ----------------- | ------------- | ------------------- |
| `strike` | `/strike`, `/praxis:strike`, `strike 1/2/3`, `삼진` | To explicitly record a rule violation (excludes colloquial uses like "strike a balance") | `/praxis:strike <violation reason>` |
| `strikes` | `/strikes`, `strike status`, `몇 진`, `check strikes` | To check the current session's strike count and recorded violations | `/praxis:strikes` |
| `reset-strikes` | `/reset-strikes`, `strike 초기화`, `clear strikes` | To reset the counter and resume responses after a 3-strike block | `/praxis:reset-strikes` |

## Session Management

| Skill | Trigger keywords | When to use | Example invocation |
| ------- | ----------------- | ------------- | ------------------- |
| `cmux-recover-sessions` | `터졌다`, `크래시 복구`, `크래시 복원`, `전원 꺼짐 복구`, `OOM 복구`, `세션 살려야`, `recover cmux`, `crash recovery`, `power loss recovery`, `cmux session recovery` | To emergency-recover many cmux sessions after a crash / power loss / OOM (`.jsonl` scan based) | `/praxis:cmux-recover-sessions` |
| `cmux-delegate` | `cmux delegate`, `delegate issue`, `delegate to new session`, `별도 세션`, `세션에 위임`, `별건으로 빼서` | To hand an existing independent issue that surfaced mid-task to its own session, which runs issue→worktree→PR alone (not for splitting the current task) | `/praxis:cmux-delegate` |

> **CLI tools (not skills):** praxis also ships `bypass-review`, a Python CLI with
> no `SKILL.md` of its own — the binary is not invocable as `/praxis:*`. It reads
> the bypass-telemetry and fire-rate event ledgers; run it from a shell (for
> example `bypass-review fire-rate`).
> See [CONTRIBUTING.md → Local development](../CONTRIBUTING.md#local-development) for the full
> list of shipped CLI wrappers.
