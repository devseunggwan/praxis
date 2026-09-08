---
name: bypass-report
description: Report praxis bypass-telemetry and hook fire-rate from the local event ledgers, using the shipped bypass-review CLI.
when_to_use: Use when the user types "/praxis:bypass-report", or asks about "bypass telemetry", "fire rate", "hook fire rate", "never-fired hooks", "bypass 원장", "훅 발화율".
disable-model-invocation: true
verified-against-runtime: true
runtime-verified-at: 2026-09-08
runtime-verified-note: "CLI round-trip only, no /praxis: dispatch (installed build is 7.14.0) — bypass-review (7.16.0 checkout, no --version flag): `bypass --days 7` printed `Total events : 0` and exited 0; `fire-rate --days 7` printed `Hooks fired : 99 (12 coarse, 40 mixed)` with per-hook rows"
---

# Praxis Bypass & Fire-Rate Report

## Overview

The bypass-telemetry hook and the fire ledger write two JSONL families, and
praxis ships one CLI that reads them. Until this
skill existed the CLI's only entry point was a `~/.local/bin` symlink that a
person had to type, so the ledgers accumulated with no reachable reader — the
data behind `docs/hook-prune-audit.md` (#713) included.

**Where those files live is not a single path.** The ledger writers resolve it
in precedence order (`hooks/_lib/_fire_ledger.py`): a `PRAXIS_*_TELEMETRY_FILE`
override first, then `<checkout>/.praxis-dev-telemetry/` when the module sits
inside a git checkout (#934), and only otherwise `$PRAXIS_HOME/telemetry`. So a
praxis dev reads a different directory than a plugin user. Never name a
directory from this list in the report — the CLI prints the one it actually
read on its `Source :` line, and that line is the answer.

**Core principle:** this skill is a viewer. It runs one read-only command and
reports what came back; it never edits the ledgers and never decides what the
numbers mean.

`disable-model-invocation: true` is deliberate and carried over from #582,
which demoted this tool out of the skill surface precisely so the model could
not reach for it on its own. The restored path is the user's `/praxis:*` call
and nothing else.

## When to Use

- The user types `/praxis:bypass-report`.
- The user asks which hooks never fire, how often a gate is bypassed, or wants
  the fire-rate roster behind a prune audit.
- A hook-pruning or hook-audit task needs the ledger numbers rather than an
  estimate.

## Process

### Step 1: Pick the mode from what was asked

The CLI has two report modes and they read different ledger families. Choosing
wrong produces a valid-looking empty report, so decide before running:

| The user asked about | Mode | Ledger family |
| --- | --- | --- |
| bypass tokens, `PRAXIS_*_BYPASS`, who bypassed what | `bypass` (default) | `bypass-events-*` |
| fire counts, never-fired hooks, block/ask/advise rates | `fire-rate` | `fire-events-*` and `fire-counts-*` |

Default the window to the CLI's own 7 days unless the user named one.

### Step 2: Run the CLI

Invoke it through the plugin root with the Bash tool. Pass the mode as the
positional argument; omit it for the default `bypass` report.

```bash
"${CLAUDE_PLUGIN_ROOT:?praxis plugin root not set — run via the installed plugin or export CLAUDE_PLUGIN_ROOT}/skills/bypass-review/bypass-review" fire-rate --days 7
```

Useful flags, all read-only: `-d N` window in days, `--errors-only` to keep
only events whose tool result was an error, `--per-hook FAMILY` for one
family's breakdown, `--dir PATH` to point at another telemetry tree.

### Step 3: Report the output verbatim

Present the CLI's stdout as it came. The column layout carries the meaning —
the `G` column's `R`/`M`/`C` is the record's granularity, and a coarse row
cannot answer questions a rich row can. Summarising it away hides that.

Add at most a one-line reading above the block, in the user's language, saying
what the numbers show. Do not rank hooks, propose prunes, or draw conclusions
the user did not ask for; that is `docs/hook-prune-audit.md`'s job, not this
skill's.

### Step 4: Say when the window is empty

An empty report is a real answer and it looks exactly like a broken query, so
distinguish them. `Total events : 0` with `exit 0` means the window held no
events. If the user expected some, say the window and the source directory the
report printed, and offer a longer `-d` before concluding anything is wrong.

## Error Handling

| Error | Recovery |
| --- | --- |
| `CLAUDE_PLUGIN_ROOT` unset — the `:?` guard aborts with `praxis plugin root not set` | Resolve it from the installed-plugins manifest: `jq -r '.plugins["praxis@praxis"][0].installPath // empty' "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/installed_plugins.json"`, export it, re-run. Still unresolved → report the failure verbatim and stop |
| Telemetry directory missing | Report the path the CLI printed. It is created by the first hook write, so an absent directory means no hook has recorded yet — not a failure |
| `fire-rate` roster incomplete | The never-fired roster comes from `hooks/manifest.json`, resolved relative to the CLI's own de-symlinked path. Outside a checkout, pass `--manifest PATH` |
| Report is empty and the user expected data | Step 4. Do not retry the same window; widen `-d` or check `--dir` |

## Non-goals

- No mutation. Nothing here writes, rotates, or prunes a ledger — that
  housekeeping belongs to the hooks that own the files.
- No verdicts. This skill does not decide a hook should be dropped or a bypass
  was wrong.
- No model-initiated invocation. See the frontmatter note above.

## Limitations

- Reads only the local machine's ledgers. There is no aggregation across
  hosts, so a fire count is one machine's.
- Granularity is a property of the record, not of this skill: a coarse row
  carries no `session_id` or tool, so per-session questions cannot be answered
  from one.
