---
name: using-praxis
description: >
  Onboarding entry point for new praxis users — introduces the 4 skill
  categories, maps common scenarios to the right skill, and explains the
  hook system.
when_to_use: >
  Triggers on "praxis 처음", "praxis 사용법", "어떤 skill 부터",
  "praxis intro", "praxis getting started".
---

# Using Praxis

Welcome to praxis — a set of Claude Code skills for disciplined, fast,
resilient development workflow.

## Skill Categories

### Development

Tools for code quality and review workflow.

| Skill                    | When to call                                                                             |
| ------------------------ | ---------------------------------------------------------------------------------------- |
| `retrospect`             | End of session — find friction root causes and create lasting fixes                      |
| `codex-review-wrap`      | Before running `/codex:review` in a multi-worktree repo                                  |
| `surface-enumeration`    | Before writing a parser/validator/sanitizer — every input variant becomes a test case    |
| `spec-drift`             | Wondering what a spec still needs — runs each requirement's `Verify:` command, reports   |
| `debt`                   | Audit deferred markers (commit trailers, `# [PR #N]`) before "later" becomes "never"     |
| `merge-briefing`         | Before asking to merge a PR — probe all three finding surfaces, grade, brief, then ask   |
| `worktree-merge-cleanup` | Merging from a worktree and cleaning up after — right call site, safe teardown           |

### Discipline

Session-scoped rule-violation tracking.

| Skill           | When to call                                        |
| --------------- | --------------------------------------------------- |
| `strike`        | Record a rule violation (`/praxis:strike <reason>`) |
| `strikes`       | Check current strike count and recorded violations  |
| `reset-strikes` | Reset after a 3rd-strike block                      |

### Session Management

Recover, save, and orchestrate Claude Code sessions.

| Skill                   | When to call                                                |
| ----------------------- | ----------------------------------------------------------- |
| `cmux-recover-sessions` | Sessions crashed / power loss (cmux backend)                |
| `recover-sessions`      | Sessions crashed / power loss (tmux backend)                |
| `cmux-save-sessions`    | Save current session layout as a JSON snapshot              |
| `cmux-resume-sessions`  | Restore a previously saved snapshot                         |
| `cmux-session-manager`  | Daily status dashboard, cleanup, reorganize                 |
| `cmux-delegate`         | Give an independent issue its own session with full context |

### Discovery

| Skill                  | When to call                                                          |
| ---------------------- | --------------------------------------------------------------------- |
| `using-praxis`         | First-time orientation — you are here                                 |
| `writing-praxis-skill` | Authoring a new praxis skill — template, SRP, trigger keyword design  |

## Common Scenarios

| Situation                                                              | Skill to call                                               |
| ---------------------------------------------------------------------- | ----------------------------------------------------------- |
| "Claude Code sessions died after a crash or power-off"                 | `cmux-recover-sessions` (cmux) or `recover-sessions` (tmux) |
| "I want to record that a workflow rule was broken"                     | `strike`                                                    |
| "There are too many Codex review comments — where to start?"           | `codex-review-wrap`                                         |
| "Is this PR ready to merge? What's still open on it?"                  | `merge-briefing`                                            |
| "The PR merged — remove the worktree and branch safely"                | `worktree-merge-cleanup`                                    |
| "I'm about to write a parser / validator / input classifier"           | `surface-enumeration`                                       |
| "Which requirements in the spec store are still unimplemented?"        | `spec-drift`                                                |
| "What did we mark 'later' in commit trailers and never revisit?"       | `debt`                                                      |
| "I want to add a new skill to praxis"                                  | `writing-praxis-skill`                                      |

## Hook System

Praxis ships hooks that enforce workflow rules ([`ETHOS.md` → Rules praxis
carries](../../ETHOS.md#rules-praxis-carries)) structurally at the tool
level (PreToolUse / PostToolUse / Stop / UserPromptSubmit). They fail-open
on infrastructure errors — Claude Code never breaks, but violating patterns
are blocked or warned before they land.

Full hook index: [`docs/hook/INDEX.md`](../../docs/hook/INDEX.md) — links
to per-hook specs at `hooks/<role>/<name>/spec.md`.

## Prerequisites

| Tier               | What works                                               | What you need                           |
| ------------------ | -------------------------------------------------------- | --------------------------------------- |
| **Standalone**     | recover-sessions, strike / strikes / reset-strikes, debt | `gh` CLI, `jq`; `recover-sessions` also needs `tmux`; `debt` needs only `git` |
| **Enhanced**       | + retrospect, codex-review-wrap                          | + oh-my-claudecode                      |
| **Full**           | + all cmux-* skills                                      | + cmux                                  |
| **Multi-provider** | + codex/gemini routing in cmux-delegate                  | + codex-cli, gemini-cli                 |
