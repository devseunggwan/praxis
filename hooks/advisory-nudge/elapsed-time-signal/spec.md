# Elapsed-Time Signal for Delegated Workers

`hooks/advisory-nudge/elapsed-time-signal/impl.sh` appends one line —
`elapsed 340s / 1200s`, or `elapsed 340s` with no budget — to what the model
receives after each user prompt and each tool result, but only in a session
whose environment carries `PRAXIS_TIME_START_EPOCH`. `cmux-delegate
--time-budget <seconds>` is the one thing in this repository that sets it.
Every other session gets no output.

Supported hosts: claude

## Why this exists (issue #1501)

`cmux-delegate --distribute` starts several workers, and none of them gets
any signal about elapsed time or a budget. `--max-budget-usd` is print-mode
only, so it cannot reach an interactive worker (#1054). Before this hook the
only timeouts in the repository were the hooks' own `timeout` fields.

The Opus 5.5 prompting guide, section *Time signals for multiagent
harnesses*
(<https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5#time-signals-for-multi-agent-harnesses>,
read 2026-09-25), recommends that the harness "add a short line at the end
of each message it sends back to the model giving the elapsed time against
that budget, in seconds, for example `elapsed 340s / 1200s`". Without a
sensible budget it recommends the elapsed time alone plus one system-prompt
sentence. The guide gives two caveats, and both shape this design:

- "The budget is advisory and nothing stops the model at the limit, so if you
  need a hard stop, keep your own timeout." This hook never blocks and never
  stops anything. A hard timeout belongs to the orchestrator, and
  `cmux-delegate` has none today (see *Known limitations*).
- "Under time pressure the model might search and verify a little less." For
  that reason the signal is **opt-in per delegation** and is never a default.

## Transport

| Env var | Set by | Meaning |
| --- | --- | --- |
| `PRAXIS_TIME_START_EPOCH` | `cmux-delegate` wrapper, `$(date +%s)` at launch | On/off switch and the start of the clock |
| `PRAXIS_TIME_BUDGET_S` | `cmux-delegate --time-budget N` | Budget in seconds. `0` or unset means elapsed-only |

The wrapper script sets these as an env prefix on the `claude` command, the
same way `{claude_env}` already carries `CLAUDE_CONFIG_DIR`. Hook processes
inherit the claude process environment. In-repo precedent:
`response-language-nudge` and `postcompact-context` read
`PRAXIS_RESPONSE_LANGUAGE` the same way. The clock starts when the wrapper
runs, not when the skill is invoked, so each `--distribute` worker counts
from its own launch.

## Event choice — evidence

The guide asks for the line on *each message sent back to the model*. In an
agentic worker those messages are tool results plus any prompt a person
types into the pane. The events that carry `additionalContext` in this
repository today:

| Event | In-repo emitters of `hookSpecificOutput.additionalContext` |
| --- | --- |
| `PostToolUse` | `response-language-nudge` (matcher `.*`), `builtin-task-postuse`, `pr-thread-resolve-advisory`, `anchor-comment-gate` |
| `PostToolUseFailure` | `second-failure-advisory` |
| `UserPromptSubmit` | `codex-review-route` |
| `PreToolUse` / `SessionStart` / `SubagentStart` | several; not message-return points |

The hook registers on all three return points:

- `PostToolUse` (matcher `.*`): each successful tool result.
- `PostToolUseFailure`: a failed tool result is also a message back to the
  model, and `PostToolUse` does not fire for it.
- `UserPromptSubmit`: the worker's first prompt, which `cmux-delegate` passes
  as argv, plus anything a person types later. Without this registration the
  model would first see the budget after its first tool call, when it has
  already planned how to split the work.

**Live canary, 2026-09-25, Claude Code 2.1.282, `claude -p … --settings
canary.json --model haiku`:** the settings file registered this hook on
`UserPromptSubmit` and `PostToolUse(.*)`, with `PRAXIS_TIME_START_EPOCH` set
to now − 300 and `PRAXIS_TIME_BUDGET_S=1200`. The prompt asked the model to
run `echo hi` and quote any hook context it received. It quoted
`elapsed 302s / 1200s` for the prompt and `elapsed 305s / 1200s` for the tool
result, two different values, so neither came from a cached fixture. The
fire ledger had two `advise` rows. Negative control: the same command with
both variables unset gave `NONE` / `NONE` and no ledger rows.

Still unmeasured:

- the interactive shape `cmux-delegate` actually launches. The canary ran in
  print mode, including whether an interactive session's first argv prompt
  fires `UserPromptSubmit`. If it does not, the first signal arrives with the
  first tool result.
- the `PostToolUseFailure` registration.
- the elapsed-only `--append-system-prompt` path.

## Output

```json
{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"elapsed 340s / 1200s"}}
```

`hookEventName` echoes the event, which comes from the registration's `args`
(`UserPromptSubmit` / `PostToolUse` / `PostToolUseFailure`), so the hook
needs no JSON parse to answer. The text is exactly the guide's format. There
is no `[hook-name]` tag and no prose, because the guide measured that short
line and not a longer one.

**Elapsed-only mode.** With `--time-budget 0` the line is `elapsed <n>s`.
The guide places its sentence ("Time matters here: do not spend time that can
be avoided, and the earlier a correct result is obtained, the better.") in
the *system prompt*, once. The `cmux-delegate` wrapper therefore passes it
with `--append-system-prompt`, and this hook does not repeat it. The hook
keeps no state, and the sentence stays where the guide measured it.
`claude --help` on 2.1.282 lists `--append-system-prompt` with no "only works
with --print" note, while `--max-budget-usd` carries that note.

## Cost for sessions without the env var

The hook is POSIX `sh`, not Python, because it is registered on every tool
call of every Claude Code session. Its first test is
`[ -n "${PRAXIS_TIME_START_EPOCH:-}" ] || exit 0`, which runs before stdin is
read and before any subprocess starts. Measured on the development container
(N=200, wrapper plus impl, env unset): about 5.5 ms per call. For comparison,
the Python sibling `response-language-nudge` takes about 74 ms per call on the
same env-unset path. Almost all of the remaining cost is the host's own
process spawn.

## Validation — silent on anything malformed

| Input | Result |
| --- | --- |
| `PRAXIS_TIME_START_EPOCH` unset or empty | silent, no ledger record |
| start or budget not a plain decimal (`abc`, `-5`, `1.5`, `1e3`, `1200s`, whitespace) | silent |
| leading zero (`012`), which `sh` arithmetic would read as octal | silent |
| more than 12 digits | silent |
| start later than now | silent. The same host wrote it at launch, so this is malformed, not skew |
| `date +%s` fails | silent |
| argv event not one of the three registered | silent |
| stdin not JSON | still emits. The signal does not depend on the payload; only the ledger record, which needs `session_id`, is skipped |

A wrong number is worse than none, so every doubtful case exits 0 with no
output.

## Fire ledger

`praxis_fire_arm` (issue #848, Rule 18) is armed only on the opted-in path,
after `session_id` is read with `jq`, and the decision is set to `advise`
before the emit. A session without the env var writes nothing to the ledger,
which is what makes the no-op path free. Missing `jq` means no record, but
the signal is still emitted.

## Host filtering

`hosts: ["claude"]`. The transport is the `claude` branch of the
`cmux-delegate` wrapper, and `PostToolUseFailure` is Claude-Code-only
(schema note, #1337). codex and gemini workers are out of scope. Their
wrapper branches do not set the variables, and `--time-budget` on those
providers is reported to the user as not applied.

## Known limitations

| Case | Behaviour |
| --- | --- |
| No hard stop | By design (the guide's caveat). `cmux-delegate` has no orchestrator timeout either, because its claude worker is interactive and fire-and-forget. A hard stop would need a separate design |
| Subagents inside a worker | They inherit the env, so their tool results carry the line too, measured against the worker's clock |
| A child `claude` started from a worker's Bash tool | Inherits the env and would show the signal. `cmux-delegate`'s wrapper unsets both variables before launch, so a nested delegation without `--time-budget` starts clean |
| `--session` (deliver into an existing workspace) | Nothing is launched, so no env can be set. The skill reports the flag as not applied |
| No A/B measurement | Issue #1501 asks for one before adoption. The flag stays opt-in until it exists |

## Tests

```bash
bash tests/hooks/advisory-nudge/test_elapsed_time_signal.sh
```

A `date` shim on `PATH` pins `+%s` and passes every other call through, so
the hook needs no clock-override variable. The cases: env absent (silent, no
ledger record), budget mode (`elapsed 340s / 1200s`), a later clock and
start == now, elapsed-only via `0` and via unset, all three event args plus an
unregistered one, the malformed start/budget table above, a start in the
future, an unparseable payload (still emits, no ledger record), the ledger
record's `advise` / `session_id`, and the generated wrapper forwarding argv.
