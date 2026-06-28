---
name: using-praxis
description: >
  Onboarding entry point for new praxis users — introduces the 4 skill
  categories, maps common scenarios to the right skill, and explains the
  hook system. Triggers on "praxis 처음", "praxis 사용법", "어떤 skill 부터",
  "praxis intro", "praxis getting started".
---

# Using Praxis

Welcome to praxis — a set of Claude Code skills for disciplined, fast,
resilient development workflow.

## Skill Categories

### Development
Tools for code quality and review workflow.

| Skill | When to call |
|-------|-------------|
| `retrospect` | End of session — find friction root causes and create lasting fixes |
| `codex-review-wrap` | Before running `/codex:review` in a multi-worktree repo |

### Discipline
Session-scoped rule-violation tracking.

| Skill | When to call |
|-------|-------------|
| `strike` | Record a rule violation (`/praxis:strike <reason>`) |
| `strikes` | Check current strike count and recorded violations |
| `reset-strikes` | Reset after a 3rd-strike block |

### Session Management
Recover, save, and orchestrate Claude Code sessions.

| Skill | When to call |
|-------|-------------|
| `cmux-recover-sessions` | Sessions crashed / power loss (cmux backend) |
| `recover-sessions` | Sessions crashed / power loss (tmux backend) |
| `cmux-save-sessions` | Save current session layout as a JSON snapshot |
| `cmux-resume-sessions` | Restore a previously saved snapshot |
| `cmux-session-manager` | Daily status dashboard, cleanup, reorganize |
| `cmux-delegate` | Delegate a task to an independent session with full context |

### Discovery (this skill)

| Skill | When to call |
|-------|-------------|
| `using-praxis` | First-time orientation — you are here |

## Common Scenarios

| Situation | Skill to call |
|-----------|--------------|
| "Claude Code sessions died after a crash or power-off" | `cmux-recover-sessions` (cmux) or `recover-sessions` (tmux) |
| "I want to record that a global `~/.claude/CLAUDE.md` rule was broken" | `strike` |
| "There are too many Codex review comments — where to start?" | `codex-review-wrap` |

## Hook System

Praxis ships hooks that enforce global `~/.claude/CLAUDE.md` rules structurally at the tool
level (PreToolUse / PostToolUse / Stop / UserPromptSubmit). They fail-open
on infrastructure errors — Claude Code never breaks, but violating patterns
are blocked or warned before they land.

Full hook index: [`docs/hook/INDEX.md`](../../docs/hook/INDEX.md) — links
to per-hook specs at `hooks/<role>/<name>/spec.md`.

## Prerequisites

| Tier | Skills available | Dependencies |
|------|-----------------|--------------|
| **Standalone** | `recover-sessions`, `strike`, `strikes`, `reset-strikes` | `gh` CLI, `jq` |
| **Enhanced** | + `retrospect`, `codex-review-wrap` | + oh-my-claudecode |
| **Full** | + all `cmux-*` skills | + cmux |

