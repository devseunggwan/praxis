# Praxis

Development workflow skills for Claude Code — disciplined, fast, resilient.

> **Note:** Skills may be added, removed, or restructured at any time without prior notice. This is a personal toolbox — not a stable API.

## Skills

> **Invocation**: praxis entries are *skills*, not subagents. Always call them
> via `Skill(skill="praxis:<name>")`. `Agent(subagent_type="praxis:<name>")`
> returns `Agent type not found` — Agent and Skill resolve disjoint namespaces.
> See [RUNTIME_CONSTRAINTS.md §3](RUNTIME_CONSTRAINTS.md) for the mapping table.

### Discovery

| Skill | Trigger keywords | When to use | Example invocation |
|-------|-----------------|-------------|-------------------|
| `using-praxis` | `praxis 처음`, `praxis 사용법`, `어떤 skill 부터`, `praxis intro`, `praxis getting started` | 처음 praxis를 접하거나 상황에 맞는 skill을 모를 때 | `/praxis:using-praxis` |
| `writing-praxis-skill` | `new skill`, `write skill`, `add skill`, `skill template`, `skill spec`, `스킬 작성`, `새 스킬` | 새 SKILL.md를 작성하거나 skill 구조 가이드가 필요할 때 | `/praxis:writing-praxis-skill` |

### Development

| Skill | Trigger keywords | When to use | Example invocation |
|-------|-----------------|-------------|-------------------|
| `retrospect` | `retrospect`, `what went wrong`, `session review`, `session improvement`, `improve` | 세션 마무리 후 마찰 패턴·근본 원인을 분석하고 개선안을 실행할 때 | `/praxis:retrospect` |
| `codex-review-wrap` | `codex review`, `review codex`, `safe review`, `premise verification`, `flip detection`, `sibling cross-check` | 다중 worktree 환경에서 `/codex:review`를 전제 검증·flip 탐지와 함께 안전하게 실행할 때 | `/praxis:codex-review-wrap` |

### Discipline

| Skill | Trigger keywords | When to use | Example invocation |
|-------|-----------------|-------------|-------------------|
| `strike` | `/strike`, `/praxis:strike`, `strike 1/2/3`, `삼진` | 규칙 위반 발생 시 명시적으로 기록할 때 (colloquial "strike a balance" 등은 제외) | `/praxis:strike <위반 이유>` |
| `strikes` | `/strikes`, `strike status`, `몇 진`, `check strikes` | 현재 세션의 strike 횟수와 위반 내역을 확인할 때 | `/praxis:strikes` |
| `reset-strikes` | `/reset-strikes`, `strike 초기화`, `clear strikes` | 3진 블록 후 카운터를 초기화해 응답을 재개할 때 | `/praxis:reset-strikes` |

### Session Management

| Skill | Trigger keywords | When to use | Example invocation |
|-------|-----------------|-------------|-------------------|
| `recover-sessions` | `recover`, `session recovery`, `restore sessions`, `power recovery` | 전원 차단·tmux 크래시 후 세션을 복구할 때 (tmux 백엔드) | `/praxis:recover-sessions` |
| `cmux-recover-sessions` | `터졌다`, `크래시 복구`, `OOM 복구`, `세션 살려야`, `crash recovery`, `power loss recovery`, `cmux session recovery` | crash·power loss·OOM 후 cmux 세션 다수를 긴급 복구할 때 (`.jsonl` 스캔 기반) | `/praxis:cmux-recover-sessions` |
| `cmux-save-sessions` | `save sessions`, `session save`, `session snapshot`, `cmux save`, `snapshot list` | 현재 cmux 세션 목록을 JSON으로 저장해 나중에 복원할 때 | `/praxis:cmux-save-sessions` |
| `cmux-resume-sessions` | `resume sessions`, `restore from snapshot`, `rehydrate sessions`, `세션 복원`, `스냅샷 복원` | 이전에 저장한 스냅샷으로 워크스페이스를 복원할 때 (크래시 복구는 `cmux-recover-sessions`) | `/praxis:cmux-resume-sessions` |
| `cmux-session-manager` | `cmux session`, `session management`, `session cleanup`, `cmux status`, `cmux tidy` | 일상적인 세션 정리·상태 대시보드가 필요할 때 | `/praxis:cmux-session-manager` |
| `cmux-delegate` | `delegate`, `cmux delegate`, `new session` | 현재 작업의 맥락을 보존한 채 독립 세션에 위임할 때 (리뷰·디버깅·구현 분리) | `/praxis:cmux-delegate` |
| `cmux-browser` | `cmux browser`, `cmux 브라우저` | cmux browser CLI로 SPA hydration 대기 포함 E2E 테스트를 실행할 때 | `/praxis:cmux-browser` |

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
shared design contracts every hook follows.

## Prerequisites

Most skills delegate to external agents or session managers. Install the dependencies that match your usage tier.

| Dependency | Required for | Install |
|------------|-------------|---------|
| **gh CLI** | Standalone (`recover-sessions`), strike skills, PR/issue ops | `brew install gh` |
| **jq** | Strike skills (session-scoped counter parsing) | `brew install jq` |
| **oh-my-claudecode** | Agent delegation (tracer, analyst, ultraqa, code-reviewer) | `omc install` |
| **cmux** | Session management skills (cmux-*) | Mac app installer |
| **codex-cli, gemini-cli** | Multi-provider routing in `cmux-delegate` | per upstream docs |

### Compatibility Tiers

| Tier | What works | What you need |
|------|-----------|---------------|
| **Standalone** | recover-sessions, strike / strikes / reset-strikes | `gh` CLI, `jq` |
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
skills (e.g. `cmux-recover-sessions`, `claude-recover`, `cmux-save-sessions`,
`cmux-browser`) are symlinked from `~/.local/bin` into this clone, so patches
you commit here land in the version that actually runs at the shell.

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
