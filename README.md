# Praxis

Development workflow skills for Claude Code — disciplined, fast, resilient.

> **Note:** Skills may be added, removed, or restructured at any time without prior notice. This is a personal toolbox — not a stable API.

## Skills

> **Invocation**: praxis entries are *skills*, not subagents. Always call them
> via `Skill(skill="praxis:<name>")`. `Agent(subagent_type="praxis:<name>")`
> returns `Agent type not found` — Agent and Skill resolve disjoint namespaces.
> See [RUNTIME_CONSTRAINTS.md §3](RUNTIME_CONSTRAINTS.md) for the mapping table.

> **Trigger keywords** mirror each skill's `SKILL.md` `description` field verbatim
> and are intentionally kept in their source language (some are Korean) so the
> README stays in sync with what actually triggers the skill — do not translate them.

### Discovery

| Skill | Trigger keywords | When to use | Example invocation |
| ------- | ----------------- | ------------- | ------------------- |
| `using-praxis` | `praxis 처음`, `praxis 사용법`, `어떤 skill 부터`, `praxis intro`, `praxis getting started` | To find the right skill when you're new to praxis or unsure which one fits | `/praxis:using-praxis` |
| `writing-praxis-skill` | `new skill`, `write skill`, `add skill`, `skill template`, `skill spec`, `스킬 작성`, `새 스킬` | To author a new SKILL.md or get a skill-structure guide | `/praxis:writing-praxis-skill` |

### Development

| Skill | Trigger keywords | When to use | Example invocation |
| ------- | ----------------- | ------------- | ------------------- |
| `retrospect` | `retrospect`, `what went wrong`, `session review`, `session improvement`, `improve` | To analyze friction patterns / root causes after a session and act on improvements | `/praxis:retrospect` |
| `codex-review-wrap` | `codex review`, `review codex`, `safe review`, `premise verification`, `flip detection`, `sibling cross-check` | To run `/codex:review` safely in multi-worktree setups, with premise verification and flip detection | `/praxis:codex-review-wrap` |
| `debt` | `praxis:debt`, `debt ledger`, `지연 결정`, `deferred decision`, `기술 부채 원장`, `commit trailer audit` | To harvest commit-trailer and compounding-comment deferred-decision markers into a report-only ledger | `/praxis:debt` |
| `surface-enumeration` | `surface enumerate`, `input surface enumeration`, `input parser`, `input validation`, `intent classifier`, `정규식 경계`, `입력 표면 열거` | To enumerate every input variant before implementing a parser/validator/sanitizer so each becomes a required test case | `/praxis:surface-enumeration` |
| `worktree-merge-cleanup` | `merge cleanup`, `post-merge cleanup`, `worktree cleanup`, `delete-branch merge`, `squash-ancestry`, `pre-merge worktree`, `머지 후 정리`, `worktree 정리` | To run `gh pr merge --squash --delete-branch` from the right worktree and clean up afterward (submodule `--force`, squash-ancestry guard, no-`&&`-chain) | `/praxis:worktree-merge-cleanup` |
| `critique` | `plan critique`, `critique this plan`, `review my plan`, `design review`, `poke holes`, `계획 검토`, `설계 반증` | To have an independent reviewer attack a plan before approval — unresolvable references, phase contradictions, criteria with no oracle | `/praxis:critique` |
| `audit` | `evidence audit`, `is this actually verified`, `audit the evidence`, `완료 검증`, `증거 심사`, `검증 충분성` | To grade whether a completion claim is proven, and name the verification that is missing | `/praxis:audit` |
| `trace` | `causal trace`, `root cause`, `why is this failing`, `trace the cause`, `근본 원인`, `원인 추적` | To trace a symptom to its cause through competing hypotheses, each refuted or held on evidence | `/praxis:trace` |
| `interview` | `requirement interview`, `clarify requirements`, `what should I ask`, `deep interview`, `요구사항 정리`, `모호성 도출` | To convert a vague request into blocking questions, implicit premises, and a named acceptance criterion | `/praxis:interview` |

### Discipline

| Skill | Trigger keywords | When to use | Example invocation |
| ------- | ----------------- | ------------- | ------------------- |
| `strike` | `/strike`, `/praxis:strike`, `strike 1/2/3`, `삼진` | To explicitly record a rule violation (excludes colloquial uses like "strike a balance") | `/praxis:strike <violation reason>` |
| `strikes` | `/strikes`, `strike status`, `몇 진`, `check strikes` | To check the current session's strike count and recorded violations | `/praxis:strikes` |
| `reset-strikes` | `/reset-strikes`, `strike 초기화`, `clear strikes` | To reset the counter and resume responses after a 3-strike block | `/praxis:reset-strikes` |

### Session Management

| Skill | Trigger keywords | When to use | Example invocation |
| ------- | ----------------- | ------------- | ------------------- |
| `recover-sessions` | `recover`, `session recovery`, `restore sessions`, `power recovery` | To recover sessions after power loss or a tmux crash (tmux backend) | `/praxis:recover-sessions` |
| `cmux-recover-sessions` | `터졌다`, `크래시 복구`, `OOM 복구`, `세션 살려야`, `crash recovery`, `power loss recovery`, `cmux session recovery` | To emergency-recover many cmux sessions after a crash / power loss / OOM (`.jsonl` scan based) | `/praxis:cmux-recover-sessions` |
| `cmux-save-sessions` | `save sessions`, `session save`, `session snapshot`, `cmux save`, `snapshot list` | To save the current cmux session list as JSON for later restore | `/praxis:cmux-save-sessions` |
| `cmux-resume-sessions` | `resume sessions`, `restore from snapshot`, `rehydrate sessions`, `세션 복원`, `스냅샷 복원` | To restore workspaces from a saved snapshot (for crash recovery, use `cmux-recover-sessions`) | `/praxis:cmux-resume-sessions` |
| `cmux-session-manager` | `cmux session`, `session management`, `session cleanup`, `cmux status`, `cmux tidy` | To run routine session cleanup or view a status dashboard | `/praxis:cmux-session-manager` |
| `cmux-delegate` | `delegate`, `cmux delegate`, `new session` | To delegate to an independent session while preserving the current task's context (split review / debugging / implementation) | `/praxis:cmux-delegate` |

> **CLI tools (not skills):** praxis also ships `bypass-review`, a shell wrapper
> with no `SKILL.md` — it is **not** invocable as `/praxis:*` and is absent from
> the skills above. It inspects the review bypass-telemetry event logs.
> See [AGENTS.md → Local Development](AGENTS.md#local-development) for the full
> list of shipped CLI wrappers.

## Hooks

Praxis ships a set of PreToolUse / PostToolUse / Stop / UserPromptSubmit hooks
that structurally enforce rules captured in CLAUDE.md (e.g. side-effect
acknowledgment, completion-evidence requirement, protected-branch edit guard,
manufactured action-menu detection). Hooks fail-open on infrastructure errors
and never break Claude Code — they only nudge or block specific patterns.

See [ARCHITECTURE.md → Hook index](ARCHITECTURE.md#hook-index) for the full
list and per-hook spec links (specs live at `hooks/<role>/<name>/spec.md`;
the [`docs/hook/INDEX.md`](docs/hook/INDEX.md) index links to them), and
[DESIGN.md → Hook Design Contracts](DESIGN.md#hook-design-contracts) for the
shared design contracts every hook follows. For a generated summary of hook
roles, events, host filters, and strict/bypass knobs, see the
[`Hook Operating Matrix`](docs/hook-operating-matrix.md).

## Prerequisites

Most skills delegate to external agents or session managers. Install the dependencies that match your usage tier.

| Dependency | Required for | Install |
| ------------ | ------------- | --------- |
| **gh CLI** | Standalone (`recover-sessions`), strike skills, PR/issue ops | `brew install gh` |
| **jq** | Strike skills (session-scoped counter parsing) | `brew install jq` |
| **oh-my-claudecode** | Agent delegation (tracer, analyst, ultraqa, code-reviewer) | `omc install` |
| **cmux** | Session management skills (cmux-*) | Mac app installer |
| **codex-cli, gemini-cli** | Multi-provider routing in `cmux-delegate` | per upstream docs |

### Compatibility Tiers

| Tier | What works | What you need |
| ------ | ----------- | --------------- |
| **Standalone** | recover-sessions, strike / strikes / reset-strikes, debt | `gh` CLI, `jq`; `debt` needs only `git` |
| **Enhanced** | + retrospect, codex-review-wrap | + oh-my-claudecode |
| **Full** | + all cmux-* skills | + cmux |
| **Multi-provider** | + codex/gemini routing in cmux-delegate | + codex-cli, gemini-cli |

> Skills in higher tiers fall back to manual/built-in alternatives when their dependencies are missing, but with reduced functionality.

## Provider Routing

Skills that dispatch external CLI workers (`cmux-delegate`) can route tasks
to multiple AI providers via a unified `--model` flag using
`<provider>:<model>` notation (e.g. `claude:opus`, `codex:o3`,
`gemini:flash`). Bare names (`opus`, `sonnet`, `haiku`) always resolve to
Claude — full backward compatibility. When only `claude` is installed,
the system behaves exactly as before — no errors, no degradation.

See [ARCHITECTURE.md → Provider Routing](ARCHITECTURE.md#provider-routing) for
the full task-type / complexity routing matrix and fallback policy.

## Installation

Praxis ships a single runtime (`skills/`, `hooks/`, `scripts/`) with
platform-specific packaging adapters generated from a canonical source in
`manifests/`. Three install surfaces are supported.

### Claude Code — plugin (recommended)

```bash
/plugin marketplace add https://github.com/devseunggwan/praxis
/plugin install praxis
```

Claude Code reads `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`
directly from the repo root.

### Codex — marketplace + plugin

```bash
# Register the local marketplace (points at this repo's .agents/plugins/marketplace.json)
codex marketplace add https://github.com/devseunggwan/praxis
codex plugin install praxis
```

Codex reads `.agents/plugins/marketplace.json` as the marketplace root and
`plugins/praxis/.codex-plugin/plugin.json` as the plugin root. The `skills/`,
`hooks/`, and `scripts/` directories inside `plugins/praxis/` are symlinks
into the repo-root runtime — there is no source duplication.

### Direct skill install (fallback)

When the plugin surface isn't available:

```bash
git clone https://github.com/devseunggwan/praxis.git ~/projects/praxis
claude skill add ~/projects/praxis/skills/<skill-name>
```

## Packaging internals

Platform manifests are generated, not hand-edited. The canonical source is
`manifests/plugin.base.json` (common metadata) plus one file per platform
under `manifests/platforms/`.

```bash
# Regenerate every platform manifest + adapter shell symlinks
./scripts/build-plugin-manifests.py

# Verify committed manifests match the canonical source (CI / pre-merge)
./scripts/check-plugin-manifests.py
```

Generated artifacts are committed:

- `.claude-plugin/plugin.json`
- `.claude-plugin/marketplace.json`
- `.agents/plugins/marketplace.json`
- `plugins/praxis/.codex-plugin/plugin.json`
- `plugins/praxis/{skills,hooks,scripts}` (symlinks into repo root)

To add a new platform, drop a `manifests/platforms/<name>.json` file listing
its outputs and run the build script — no changes to skills, hooks, or
existing platforms required.

## Local Development

This repository should live at **`~/projects/praxis`**. CLI tools shipped by
skills (e.g. `cmux-recover-sessions`, `claude-recover`, `cmux-save-sessions`)
are symlinked from `~/.local/bin` into this clone, so patches you commit here
land in the version that actually runs at the shell.

```bash
# Install / refresh CLI symlinks (idempotent)
./scripts/install.sh

# Verify symlinks point at this clone (CI / SessionStart hook)
./scripts/verify-symlinks.sh
```

See [AGENTS.md → Local Development](AGENTS.md#local-development) for the full
list of shipped CLI wrappers and drift-recovery rationale.

## Security & Privacy

- [SECURITY.md](SECURITY.md) — vulnerability reporting and supported versions
- [PRIVACY.md](PRIVACY.md) — what praxis reads, executes, and never transmits

## License

MIT License
