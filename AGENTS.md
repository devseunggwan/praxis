# Praxis

Development workflow skills for Claude Code — disciplined, fast, resilient.

## Skills

| Skill | Purpose | Triggers |
|-------|---------|----------|
| `turbo-setup` | Compound setup — issue + plan + branch + worktree + deps in one step | "setup", "turbo-setup", "quick start" |
| `turbo-deliver` | Compound delivery — auto-detects PR state for full pipeline or merge-only mode | "deliver", "turbo-deliver", "finish up", "cleanup", "finish branch" |
| `verify-completion` | Enforce verification evidence before completion claims | "verify", "verification", "done check" |
| `debug` | Systematic 4-phase debugging with root cause investigation | "debug", "why failing", "root cause" |
| `brainstorm` | Diamond Model brainstorming — diverge then converge with evaluation | "brainstorm", "ideate", "what if", "explore options" |
| `retrospect` | Session retrospect — find friction root causes, propose improvements | "retrospect", "what went wrong", "session review" |
| `recover-sessions` | Bulk recover Claude Code sessions after power loss (tmux) | "recover", "session recovery", "power recovery" |
| `cmux-recover-sessions` | Bulk recover sessions after crash (cmux) | "recover cmux", "cmux session recovery" |
| `cmux-save-sessions` | Save cmux session list as JSON snapshot | "save sessions", "cmux save" |
| `cmux-resume-sessions` | Restore cmux workspaces from JSON snapshot | "resume sessions", "cmux resume" |
| `cmux-session-manager` | cmux session lifecycle — status, cleanup, init, report | "cmux status", "cmux cleanup" |
| `cmux-orchestrator` | Dispatch and supervise parallel Claude Code workers | "orchestrate", "dispatch", "cmux workers" |

## Design Principle

- **Praxis = discipline / orchestration** (when, what order, why)
- **OMC = execution capability** (ultraqa, debugger, code-reviewer)
- Skills enforce workflow gates; OMC agents do the actual work
