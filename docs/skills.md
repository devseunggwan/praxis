# Skills

The full skill reference. For what praxis is and how to install it, see
[`README.md`](../README.md); if you are new and want a starting point rather
than a catalogue, run `/praxis:using-praxis`.

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

## Discovery

| Skill | Trigger keywords | When to use | Example invocation |
| ------- | ----------------- | ------------- | ------------------- |
| `using-praxis` | `praxis 처음`, `praxis 사용법`, `어떤 skill 부터`, `praxis intro`, `praxis getting started` | To find the right skill when you're new to praxis or unsure which one fits | `/praxis:using-praxis` |
| `writing-praxis-skill` | `new praxis skill`, `write praxis skill`, `add praxis skill`, `skill template`, `praxis skill spec`, `스킬 작성`, `새 스킬` | To author a new SKILL.md or get a skill-structure guide | `/praxis:writing-praxis-skill` |

## Development

| Skill | Trigger keywords | When to use | Example invocation |
| ------- | ----------------- | ------------- | ------------------- |
| `retrospect` | `retrospect`, `what went wrong`, `session review`, `session improvement`, `what was the issue`, `improve` | To analyze friction patterns / root causes after a session and act on improvements | `/praxis:retrospect` |
| `codex-review-wrap` | `codex review`, `review codex`, `safe review`, `/codex-review-wrap`, `premise verification`, `flip detection`, `sibling defect`, `sibling cross-check`, `diminishing returns`, `broker reap`, `finding approval`, `적용 승인` | To run `/codex:review` safely in multi-worktree setups, with premise verification and flip detection | `/praxis:codex-review-wrap` |
| `debt` | `praxis:debt`, `debt ledger`, `지연 결정`, `deferred decision`, `기술 부채 원장`, `commit trailer audit` | To harvest commit-trailer and compounding-comment deferred-decision markers into a report-only ledger | `/praxis:debt` |
| `surface-enumeration` | `surface enumerate`, `input surface enumeration`, `input parser`, `input validation`, `intent classifier`, `정규식 경계`, `입력 표면 열거`, `multi-PR shared state`, `convention guide reflection`, `apply lesson` | To enumerate every input variant before implementing a parser/validator/sanitizer so each becomes a required test case | `/praxis:surface-enumeration` |
| `spec-drift` | `spec drift`, `spec-drift`, `스펙 드리프트`, `미구현 요구`, `unmet requirement`, `what does this spec still need`, `requirement status` | To report which requirements in the `~/.praxis/docs/specs/` store the current tree does not yet satisfy, by running each one's `Verify:` command | `/praxis:spec-drift` |
| `merge-briefing` | `merge briefing`, `pre-merge briefing`, `머지 브리핑`, `머지해도 되나`, `approve merge`, `pre-ask probe`, `merge approval` | To probe all three finding surfaces, grade every finding by its blocking decoration, carry anchor `Unverified` gaps, and surface the six-part briefing before asking for merge approval | `/praxis:merge-briefing` |
| `worktree-merge-cleanup` | `merge cleanup`, `post-merge cleanup`, `worktree cleanup`, `delete-branch merge`, `squash-ancestry`, `pre-merge worktree`, `머지 후 정리`, `worktree 정리` | To run `gh pr merge --squash --delete-branch` from the right worktree and clean up afterward (submodule `--force`, squash-ancestry guard, no-`&&`-chain) | `/praxis:worktree-merge-cleanup` |

## Discipline

| Skill | Trigger keywords | When to use | Example invocation |
| ------- | ----------------- | ------------- | ------------------- |
| `strike` | `/strike`, `/praxis:strike`, `strike 1/2/3`, `삼진` | To explicitly record a rule violation (excludes colloquial uses like "strike a balance") | `/praxis:strike <violation reason>` |
| `strikes` | `/strikes`, `strike status`, `몇 진`, `check strikes` | To check the current session's strike count and recorded violations | `/praxis:strikes` |
| `reset-strikes` | `/reset-strikes`, `strike 초기화`, `clear strikes` | To reset the counter and resume responses after a 3-strike block | `/praxis:reset-strikes` |

## Session Management

| Skill | Trigger keywords | When to use | Example invocation |
| ------- | ----------------- | ------------- | ------------------- |
| `recover-sessions` | `recover`, `session recovery`, `restore sessions`, `power recovery` | To recover sessions after power loss or a tmux crash (tmux backend) | `/praxis:recover-sessions` |
| `cmux-recover-sessions` | `터졌다`, `크래시 복구`, `크래시 복원`, `전원 꺼짐 복구`, `OOM 복구`, `세션 살려야`, `recover cmux`, `crash recovery`, `power loss recovery`, `cmux session recovery` | To emergency-recover many cmux sessions after a crash / power loss / OOM (`.jsonl` scan based) | `/praxis:cmux-recover-sessions` |
| `cmux-save-sessions` | `save sessions`, `session save`, `session snapshot`, `cmux save`, `list snapshots`, `snapshot list` | To save the current cmux session list as JSON for later restore | `/praxis:cmux-save-sessions` |
| `cmux-resume-sessions` | `resume sessions`, `session resume`, `session restore`, `cmux resume`, `restore from snapshot`, `rehydrate sessions`, `세션 복원`, `스냅샷 복구`, `스냅샷 복원` | To restore workspaces from a saved snapshot (for crash recovery, use `cmux-recover-sessions`) | `/praxis:cmux-resume-sessions` |
| `cmux-session-manager` | `cmux session`, `session management`, `session cleanup`, `cmux status`, `cmux cleanup`, `cmux tidy` | To run routine session cleanup or view a status dashboard | `/praxis:cmux-session-manager` |
| `cmux-delegate` | `cmux delegate`, `delegate issue`, `delegate to new session`, `별도 세션`, `세션에 위임`, `별건으로 빼서` | To hand an existing independent issue that surfaced mid-task to its own session, which runs issue→worktree→PR alone (not for splitting the current task) | `/praxis:cmux-delegate` |

> **CLI tools (not skills):** praxis also ships `bypass-review`, a shell wrapper
> with no `SKILL.md` — it is **not** invocable as `/praxis:*` and is absent from
> the skills above. It inspects the review bypass-telemetry event logs.
> See [CONTRIBUTING.md → Local development](../CONTRIBUTING.md#local-development) for the full
> list of shipped CLI wrappers.
