# cwd-relative-exec-advisory

Supported hosts: all

`hooks/advisory-nudge/cwd-relative-exec-advisory/impl.py` runs on
`PreToolUse(Bash)`. It fires a soft `ask` gate when a Bash command executes a
**relative path** (a `./`/`../`/bare `dir/file` command, or a relative
`--body-file`/`-F` value) with **no preceding `cd`/`pushd`** in the same
command, while `git worktree list` shows **2+ registered worktrees**.

## Why this exists

Memory `verify worktree context before git-state CLI invocation` (originated
2026-04-30, keyword-optimized 2026-05-11) already documents the session-cwd
reset trap: cwd resets to the default worktree (usually `main`) between Bash
calls. It has recurred regardless:

- 2026-05-11 praxis PR #175 merge — 4+ variants (PRs #170/#171/#172/#173)
- 2026-07-24, this repo — `./scripts/run-tests.sh` run without a preceding
  `cd`; the session cwd had reset to `main`, so tests silently ran against
  the wrong worktree — 5 recurrences in one session, and 4 concurrent
  background runs collided writing the same log file.

### Why the existing memory didn't fire — two gaps

1. **Keyword mismatch.** The memory's `hookKeywords` are `[gh, "gh pr merge",
   "gh pr", codex, "/codex:review", "git worktree"]`. `./scripts/
   run-tests.sh > /tmp/x.log 2>&1` matches none of them (verified: grep -F
   all 6 keywords → 0 matches) — it's a relative-path *script execution*, not
   a git-state CLI invocation, so it sits outside the memory's declared
   coverage.
2. **stderr channel.** Even a matching memory-hint fire writes
   `[memory:hookable]` to stderr, which the model never sees (same structural
   gap documented by the #841 canary). A hookable memory match alone would
   not have surfaced this to the model.

This hook is a coverage extension, not a `bash-worktree-existence-advisory`
duplicate: that sibling catches a `cd <path>` target that does not exist. This
one catches the inverse — a relative-path execution with **no** `cd` at all,
where a `cd` was arguably needed to disambiguate which of 2+ worktrees the
call targets.

## Channel — `ask`, not stderr advisory

The issue's own root-cause analysis is that a stderr advisory reproduces the
exact invisibility gap that let this recur (memory-hint's `[memory:hookable]`
line never reaches the model). This hook instead emits a PreToolUse
`permissionDecision: ask` — a soft gate the model can acknowledge and proceed
past, mirroring `output-block-falsify-advisory`'s T2 tier. Severity is MED
(most misfires target the agent's own worktree, not data loss), so a hard
`deny` is not used.

## What is detected

Both conditions must hold:

1. **`git worktree list --porcelain` reports 2+ `worktree` lines.** Fewer
   than 2 → no ambiguity, silent. Command failure (non-git cwd, timeout,
   missing binary) → silent (cannot confirm ambiguity either way).
2. **A relative-path execution with no preceding `cd`/`pushd` segment in the
   same command.** Segments are scanned in order (`tokenize_with_roles`,
   split on `&&`/`||`/`;`/newline); a `cd`/`pushd` segment before the
   relative-exec segment suppresses the finding for everything after it.
   Detected forms:
   - The segment's `COMMAND` token contains `/` (so `./script`, `../script`,
     and bare `dir/file.sh` all count — bash treats any command-name
     containing `/` as a path, not a `PATH` search).
   - A `--body-file`/`-F` `FLAG_VALUE` under the `gh` command whose value is
     relative (the issue's concrete `--body-file rel/path` example).

## Silent cases (enumerated input surface)

| Input | Why silent |
| --- | --- |
| `/abs/path/script.sh` | absolute — cwd-independent |
| `~/script.sh`, `~user/script.sh` | tilde expansion — hook `$HOME` may differ from the agent's |
| `$VAR/script.sh`, `$(cmd)/script.sh` | unresolvable at hook time, no ambiguity claim can be made |
| `cd /wt && ./script.sh` | explicit `cd` precedes the relative exec in the same command |
| `cd /wt && git status && ./script.sh` | `cd` still precedes, even across multiple segments |
| `./script.sh` with 1 registered worktree | no ambiguity — only one place it could run |
| `ls -la`, `git status` (bare command, no `/`) | not a path — normal `PATH`-searched command |
| `# cwd-advisory:ack` present anywhere in the command | explicit opt-out |
| non-Bash tool call | out of scope |
| malformed JSON stdin | fail-open |

## Fail-open contract

| Condition | Behavior |
| --- | --- |
| Malformed / missing stdin JSON | exit 0, silent |
| `tool_name != "Bash"` | exit 0, silent |
| Empty command | exit 0, silent |
| `git worktree list` failure/timeout | exit 0, silent (no ambiguity claim) |
| Any uncaught exception | exit 0 (`@fail_open`) |

## Registration

Deferred to phase integration (this PR ships `impl.py` + `spec.md` + tests
only — no `hooks/manifest.json` entry, no generated `hooks.json`, no
`docs/hook/INDEX.md` / `ARCHITECTURE.md` update). Intended registration:
`PreToolUse`, matcher `Bash`, role `advisory-nudge`.

## Honest limitation

The finding is presence-only: it does not resolve whether the relative path
actually points at the wrong worktree, only that the command *could*, given
2+ worktrees and no in-command `cd`. It is intentionally conservative — a
false negative (silent on a `cd`-qualified command that still targets the
wrong worktree because the `cd` target itself was wrong) is out of scope;
that case is `bash-worktree-existence-advisory`'s job.

## Tests

```bash
bash tests/hooks/advisory-nudge/test_cwd_relative_exec_advisory.sh
```
