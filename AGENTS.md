# Praxis

Workflow rules from CLAUDE.md turned into hooks and skills that actually fire.

Each skill is an orchestrator with pluggable steps. External integrations (issue tracker, PR tool, code review) are routed via the project's CLAUDE.md — no hardcoded dependencies.

## Documentation map

| File                                                               | Purpose                                                                                                                                                                |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`ETHOS.md`](ETHOS.md)                                             | Why praxis exists — values and principles that gate every skill, hook, and manifest; includes [Autonomy vs Convention](ETHOS.md#autonomy-vs-convention) boundary table |
| [`DESIGN.md`](DESIGN.md)                                           | How hooks are built — structural-tokenization, session_id keying, compound-bash-cascade, ordering, and add-a-new-hook flow                                             |
| [`ARCHITECTURE.md`](ARCHITECTURE.md)                               | Skill ↔ hook ↔ manifest dependency graph — provider routing, hook index, multi-platform packaging                                                                      |
| [`RUNTIME_CONSTRAINTS.md`](RUNTIME_CONSTRAINTS.md)                 | Fixed Claude Code runtime limits every skill must respect                                                                                                              |
| [`CONTRIBUTING.md`](CONTRIBUTING.md)                               | Skill and hook contribution conventions, live-runtime verification gate                                                                                                |
| [`docs/spec-store.md`](docs/spec-store.md)                         | Feature-spec convention — design docs at `~/.praxis/docs/specs/NNNN-slug.md` (outside any checkout, `PRAXIS_HOME`-relocated), when one is required and when skipped    |
| [`docs/hook-prune-audit.md`](docs/hook-prune-audit.md)             | Evidence-based keep/merge/drop verdict per hook, scored from the fire-rate ledger (issue #713)                                                                         |
| [`docs/retrospect-prune-audit.md`](docs/retrospect-prune-audit.md) | Same lens on the retrospect skill's gates/fences/stages, scored from retrospective transcript mining (issue #776)                                                      |

## Prerequisites

| Tier               | What works                                               | Dependencies                            |
| ------------------ | -------------------------------------------------------- | --------------------------------------- |
| **Standalone**     | recover-sessions, strike / strikes / reset-strikes, debt | `gh` CLI, `jq`; `debt` needs only `git` |
| **Enhanced**       | + retrospect, codex-review-wrap                          | + oh-my-claudecode                      |
| **Full**           | + all cmux-* skills                                      | + cmux                                  |
| **Multi-provider** | + codex/gemini routing in cmux-delegate                  | + codex-cli, gemini-cli                 |

> **`gh` is also a prerequisite of the verification-anchor convention**, and
> for revision specifically. Creating an anchor needs only a way to post a
> comment; editing one in place is a `PATCH` against its comment id, which a
> session whose only GitHub surface is the MCP server cannot issue — comment
> bodies are add-only there, while issue and PR bodies are not. Past rev 1 the
> anchor rule is unsatisfiable in such a session; say so on the PR and carry
> the gaps in the PR body or merge commit rather than posting a second anchor.
> See [`hooks/preflight-gate/anchor-comment-gate/spec.md`](hooks/preflight-gate/anchor-comment-gate/spec.md#prerequisite--gh-for-revision-specifically)
> and issue #1211.

## Skills (18)

> **Invocation**: praxis entries are *skills*, not subagents. Always call them
> via `Skill(skill="praxis:<name>")` — `Agent(subagent_type="praxis:<name>")`
> returns `Agent type not found`. See [RUNTIME_CONSTRAINTS.md §3](RUNTIME_CONSTRAINTS.md)
> for the full rationale and Agent-vs-Skill mapping table.

### Discovery

| Skill                  | Purpose                                                                                             |
| ---------------------- | --------------------------------------------------------------------------------------------------- |
| `using-praxis`         | Onboarding entry point — maps scenarios to the right skill for new praxis users                     |
| `writing-praxis-skill` | Guide for authoring a new SKILL.md — template, SRP, trigger keyword design, frontmatter conventions |

### Development

| Skill                    | Purpose                                                                                                                                                                                                  |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `retrospect`             | Session retrospect — find friction root causes, propose improvements                                                                                                                                     |
| `codex-review-wrap`      | Worktree-aware wrapper for `/codex:review` — forces explicit target selection, premise-verification gate, flip detection across rounds                                                                   |
| `debt`                   | Deferred-decision ledger — unions commit-trailer markers (`Not-tested:`, `Confidence: low`, `Rejected:`, `Directive:`, `Scope-risk:`) with tree compounding comments (`# [PR #N]`); report-only          |
| `surface-enumeration`    | Pre-implementation input-surface enumeration — enumerate every input variant before writing a parser/validator/sanitizer/classifier so each becomes a required test case                                 |
| `spec-drift`             | Spec↔code drift report — runs each spec-store requirement's `Verify:` command and reports `implemented` / `missing` / `UNKNOWN`; prose backticks are never executed; report-only                         |
| `merge-briefing`         | On-demand home for the pre-merge approval procedure — three-surface probe, grading findings by blocking decoration, carrying anchor gaps, six-part approve-ask; chains into `worktree-merge-cleanup`     |
| `worktree-merge-cleanup` | On-demand home for the pre-merge worktree precondition + unified post-merge cleanup sequence — base-worktree call site, submodule `--force` caveat, squash-ancestry stale-HEAD guard, no-`&&`-chain rule |

### Discipline

| Skill           | Purpose                                                                                                               |
| --------------- | --------------------------------------------------------------------------------------------------------------------- |
| `strike`        | Declare a rule violation — session-scoped counter, escalating signal (1진 warning → 2진 review → 3진 Stop-hook block) |
| `strikes`       | Show current strike count + recorded violation reasons for the active session                                         |
| `reset-strikes` | Reset the session strike counter to 0 after a 3진 block (required to unblock responses)                               |

### Session Management

| Skill                   | Purpose                                                               |
| ----------------------- | --------------------------------------------------------------------- |
| `cmux-save-sessions`    | Save cmux session list as JSON snapshot                               |
| `cmux-resume-sessions`  | Restore cmux workspaces from JSON snapshot                            |
| `cmux-recover-sessions` | Bulk recover sessions after crash (cmux backend)                      |
| `recover-sessions`      | Bulk recover sessions after power loss (tmux backend)                 |
| `cmux-session-manager`  | Daily session lifecycle — status dashboard, cleanup, reorganize       |
| `cmux-delegate`         | Give an independent issue its own session with auto-collected context |

## Hooks

Praxis ships a PreToolUse / PostToolUse / Stop / UserPromptSubmit hook suite
that structurally enforces the rules captured in [`ETHOS.md`](ETHOS.md).
See [`DESIGN.md`](DESIGN.md) for the shared design contracts (structural
tokenization, fail-open, session_id keying, compound-bash cascade) and
[`ARCHITECTURE.md → Hook index`](ARCHITECTURE.md#hook-index) for the full
list. Per-hook specs live at [`hooks/<role>/<name>/spec.md`](hooks/); the
[`docs/hook/INDEX.md`](docs/hook/INDEX.md) index links to them.

Hooks support host-aware filtering via an optional `hosts` field on each hook
entry in `hooks/manifest.json`. When `hosts` is absent the hook is included for
all platforms (default). When present it must be an array of host identifiers
(`"claude"`, `"codex"`, etc.) — the hook is written only to the platform whose
`host_id` in `manifests/platforms/<name>.json` appears in that list. The build
script (`scripts/build-plugin-manifests.py`) reads this field and writes a
platform-specific `hooks.json` for each platform under its plugin directory.
Each per-hook `spec.md` carries a `Supported hosts:` line that documents the
classification; `scripts/check-plugin-manifests.py` verifies that every hook
entry has a corresponding spec file.

## Provider Routing

Skills that dispatch external CLI workers (`cmux-delegate`) can route tasks
to multiple AI providers via a unified `--model` flag using
`<provider>:<model>` notation. Bare names (`opus`, `sonnet`, `haiku`) always
resolve to Claude — full backward compatibility. See
[`ARCHITECTURE.md → Provider Routing`](ARCHITECTURE.md#provider-routing) for
the CLI spec, model notation, task-type routing matrix, fallback policy, and
resolution algorithm.

## Multi-Platform Packaging

Runtime source (`skills/`, `hooks/`, `scripts/`) is shared; per-platform
manifests (Claude, Codex, Cursor, Gemini, OpenCode) are generated from
canonical metadata. See
[`ARCHITECTURE.md → Multi-Platform Packaging`](ARCHITECTURE.md#multi-platform-packaging)
for the source files, generated outputs, and add-a-new-platform flow.

## Local Development

### Canonical clone path

This repository should live at **`~/projects/praxis`**. The CLI tools shipped
by skills (e.g. `cmux-recover-sessions`, `claude-recover`, `cmux-save-sessions`)
are symlinked from `~/.local/bin` into this clone, so patches you commit here
land in the version that actually runs at the shell. Keeping a
second clone under a legacy name risks `~/.local/bin` symlinks pointing at stale
code — a real failure mode previously hit during recover-sessions debugging.

After every `git pull` or worktree operation, run `./scripts/verify-symlinks.sh` to confirm all `~/.local/bin` entries still resolve to this clone.

### CLI tools (not skills)

These are shell wrappers installed via `scripts/install.sh` into `~/.local/bin`.
They are not AI skills — they have no `SKILL.md` and cannot be invoked as `/praxis:*`.

| Binary          | Source                               | Purpose                                                                                                                                                     |
| --------------- | ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bypass-review` | `skills/bypass-review/bypass-review` | Review bypass-telemetry event logs written by the bypass-telemetry hook — aggregate and inspect JSONL records (no `SKILL.md`; not invocable as `/praxis:*`) |

### Install / refresh CLI symlinks

```bash
# From inside this clone:
./scripts/install.sh
```

Idempotent. Existing valid links are left alone; missing or drifted ones
are corrected. Re-run after pulls or after adding a new CLI script.

### Verify symlinks point at this clone

```bash
./scripts/verify-symlinks.sh
```

Exits non-zero on drift, so it can be wired into CI or a SessionStart hook
to catch "patch landed in the wrong clone" before it bites a future session.

## Issue & PR Conventions

- **PR comment retrieval scope**: When asked to check PR comments,
  inspect all three surfaces: (1) Conversation comments (`gh pr view --json
  comments`), (2) inline review comments/discussions (`gh api
  repos/{owner}/{repo}/pulls/<PR>/comments --paginate` or the GraphQL review
  thread equivalent), and (3) review bodies (`gh api
  repos/{owner}/{repo}/pulls/<PR>/reviews --paginate -q '.[].body'`) — bots
  like CodeRabbit place nitpick/actionable feedback in collapsible
  review-body sections, not line comments; scan for
  `Nitpick|Actionable|outside diff|🧹`. Links containing `#discussion_r...`
  are inline review comments, not Conversation comments; do not report "no
  comments" or "only one comment" until all three surfaces have been checked.
- **Partial-scope PR**: When a PR addresses only a subset of an issue's body
  (e.g., "items 1-3 implemented, P-redesign deferred to follow-up"), use
  `Refs #N` (or `Addresses #N (items X, Y; Z deferred)`) in the PR body
  **instead of** `Closes #N`. GitHub's `Closes` keyword auto-closes the issue
  on merge regardless of deferred items inside the issue body, orphaning their
  tracking thread.
- **Full-scope PR**: `Closes #N` per global CLAUDE.md (Issue & PR Rules).
- **Agent prompts that delegate PR authorship**: do not hardcode `Closes #N` —
  instruct the agent to choose `Closes` vs `Refs` based on whether the PR
  addresses the issue's full scope.
