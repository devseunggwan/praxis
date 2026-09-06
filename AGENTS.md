# Praxis

Workflow rules from CLAUDE.md turned into hooks and skills that actually fire.

Skills are orchestrators with pluggable steps; external integrations (issue tracker, PR tool, code review) route via the project's CLAUDE.md, never hardcoded.

## Documentation map

| File                                                               | Purpose                                                                                                                                                 |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`ETHOS.md`](ETHOS.md)                                             | Why praxis exists — the values that gate every skill, hook, and manifest; [Autonomy vs Convention](ETHOS.md#autonomy-vs-convention) boundary table      |
| [`DESIGN.md`](DESIGN.md)                                           | How hooks are built — structural-tokenization, session_id keying, compound-bash-cascade, ordering, and add-a-new-hook flow                              |
| [`ARCHITECTURE.md`](ARCHITECTURE.md)                               | Skill/hook/manifest dependency graph — provider routing, hook index, multi-platform packaging                                                           |
| [`RUNTIME_CONSTRAINTS.md`](RUNTIME_CONSTRAINTS.md)                 | Fixed Claude Code runtime limits every skill must respect                                                                                               |
| [`CONTRIBUTING.md`](CONTRIBUTING.md)                               | Skill/hook contribution conventions, live-runtime verification gate, local development setup                                                            |
| [`docs/spec-store.md`](docs/spec-store.md)                         | Feature-spec convention — specs at `~/.praxis/docs/specs/NNNN-slug.md` (`PRAXIS_HOME`-relocated, outside any checkout); when one is required or skipped |
| [`docs/hook-prune-audit.md`](docs/hook-prune-audit.md)             | Keep/merge/drop verdict per hook, scored from the fire-rate ledger (issue #713)                                                                         |
| [`docs/retrospect-prune-audit.md`](docs/retrospect-prune-audit.md) | Same lens on retrospect's gates/fences/stages, scored from retrospective transcript mining (issue #776)                                                 |

## Prerequisites

| Tier               | What works                                               | Dependencies                            |
| ------------------ | -------------------------------------------------------- | --------------------------------------- |
| **Standalone**     | recover-sessions, strike / strikes / reset-strikes, debt | `gh` CLI, `jq`; `recover-sessions` also needs `tmux`; `debt` needs only `git` |
| **Enhanced**       | + retrospect, codex-review-wrap                          | + oh-my-claudecode                      |
| **Full**           | + all cmux-* skills                                      | + cmux                                  |
| **Multi-provider** | + codex/gemini routing in cmux-delegate                  | + codex-cli, gemini-cli                 |

> `gh` is also a prerequisite of the verification-anchor convention — an anchor
> can be posted without it, but not revised past rev 1. MCP-only procedure:
> [`CONTRIBUTING.md` → Anchor revision without `gh`](CONTRIBUTING.md#anchor-revision-without-gh)
> (issue #1211).

## Skills (18)

> **Invocation**: praxis entries are *skills*, not subagents. Call them
> via `Skill(skill="praxis:<name>")` — `Agent(subagent_type="praxis:<name>")`
> returns `Agent type not found` (rationale: [RUNTIME_CONSTRAINTS.md §3](RUNTIME_CONSTRAINTS.md)).

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
| `strike`        | Declare a rule violation — session-scoped counter, escalating signal (warning → review → Stop-hook block at strike 3) |
| `strikes`       | Show current strike count + recorded violation reasons for the active session                                         |
| `reset-strikes` | Reset the strike counter after a strike-3 block (required to unblock responses)                         |

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

Praxis ships a PreToolUse/PostToolUse/PostToolUseFailure/Stop/SubagentStop/UserPromptSubmit/SessionStart hook suite
that structurally enforces the rules in [`ETHOS.md`](ETHOS.md);
[`DESIGN.md`](DESIGN.md) holds the shared contracts. Per-hook specs live at
[`hooks/<role>/<name>/spec.md`](hooks/), indexed by
[`docs/hook/INDEX.md`](docs/hook/INDEX.md); the generated
[`docs/hook-operating-matrix.md`](docs/hook-operating-matrix.md) lists events,
hosts, and strict/bypass knobs per hook. Host-aware filtering (`hosts`):
[`CONTRIBUTING.md` → Host-aware filtering](CONTRIBUTING.md#host-aware-filtering).

## Provider Routing

`cmux-delegate` routes external CLI workers via `--model <provider>:<model>`;
bare names (`opus`, `sonnet`, `haiku`) always resolve to Claude. Details:
[`ARCHITECTURE.md → Provider Routing`](ARCHITECTURE.md#provider-routing).

## Multi-Platform Packaging

Runtime source (`skills/`, `hooks/`, `scripts/`) is shared; per-platform
manifests (Claude, Codex, Cursor) are generated from canonical metadata.
Details:
[`ARCHITECTURE.md → Multi-Platform Packaging`](ARCHITECTURE.md#multi-platform-packaging).

## Local Development

Clone path, `~/.local/bin` symlink install/verify, CLI-tools table
(`bypass-review`):
[`CONTRIBUTING.md` → Local development](CONTRIBUTING.md#local-development).

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
- **Full-scope PR**: `Closes #N` — GitHub auto-closes the issue on merge.
- **Agent prompts that delegate PR authorship**: do not hardcode `Closes #N` —
  instruct the agent to choose `Closes` vs `Refs` based on whether the PR
  addresses the issue's full scope.
