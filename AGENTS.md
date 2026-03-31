# my-skills

Personal workflow skills for Claude Code. Provides behavioral discipline skills that orchestrate OMC agents with enforcement rules.

## Skills

| Skill | Purpose | Triggers |
|-------|---------|----------|
| `brainstorm` | Diamond Model brainstorming — diverge then converge with evaluation | "brainstorm", "아이디어", "what if", "explore options" |
| `finish-branch` | Branch completion lifecycle — merge verify, cleanup, compounding | "cleanup", "finish branch", "worktree cleanup" |
| `verify-completion` | Enforce verification evidence before completion claims | "verify", "verification", "done check" |
| `debug` | Systematic 4-phase debugging with root cause investigation | "debug", "why failing", "root cause" |
| `retrospect` | Session retrospect — scan conversation against CLAUDE.md, find friction root causes, propose and execute improvements | "retrospect", "회고", "삽질 정리", "세션 개선", "what went wrong", "개선해" |

## Design Principle

- **my-skills = discipline / orchestration** (when, what order, why)
- **OMC = execution capability** (ultraqa, debugger, code-reviewer)
- Skills enforce workflow gates; OMC agents do the actual work
