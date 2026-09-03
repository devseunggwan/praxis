# PreToolUse Composed Command Gate

Supported hosts: all
Requires: slack-or-notion-mcp (mcp matcher entry only — the Bash entry carries no requirement)

`hooks/composed-command-gate.sh` is a **default-on** PreToolUse advisory that
fires when an external-write body's fenced blocks carry `$` command lines with
no counterpart among this session's Bash calls.

It enforces the global `~/.claude/CLAUDE.md` clause *"Every `$` block is a
transcription, never a composition"* at the publication surface (issue #1117).
The failure it targets is specific: the **output is genuine and only the
command line above it was composed** — a probe run three or four times, with
the version the author *meant* to run written above the output of a different
run. Nothing about the pasted output betrays this, which is why re-reading
one's own body never catches it.

## Boundary against the adjacent hooks

| Hook | Sees fenced blocks? | Asks what? |
| --- | --- | --- |
| `source-citation-probe-gate` | **no** — strips them in preprocessing | was the cited `file:line` read this session? |
| `external-write-falsify-check` (Check 2, opt-in) | yes | is this *identifier* (CLI flag, label, `schema.table`) verified? |
| `anchor-comment-gate` | n/a | does the posted anchor have the right shape, SHA, diff coverage? |
| this hook | yes | was this `$` line **executed**? |

The `$` line's own provenance was uncovered by all three.

## What is detected

The body is split into fenced blocks (` ``` ` or `~~~`, 3+ delimiters, up to 3
leading spaces; a closing fence needs the same character, at least the opening
run length, and no trailing content — so a shorter run inside a longer fence is
content). Inside those blocks, a **prompt line** is `^\s*\$ +\S` — the space
after `$` is load-bearing, separating a shell prompt from `$VAR` / `$(...)`
expansions that start a line of pasted output. A trailing `\` joins the next
line into one command.

| Tier | Shape | Needs the transcript? |
| --- | --- | --- |
| T1 `non-shell` | the head token is function-call syntax — `$ safe_tokenize('...')` — where a binary name belongs. Nothing shaped like this runs at a shell, so it cannot have been transcribed from anything | no |
| T2 `unmatched` | a shell-shaped line whose head binary + operand overlap finds no counterpart in the recent transcript's Bash commands | yes |

Surfaces scanned are the shared ones (`_lib/_external_write_body.py`): `gh
issue|pr comment|create|edit`, `gh pr review` with `--body` / `-b` /
`--body=` / `--body-file` / `-F`, plus the Slack/Notion MCP nested
container/leaf shapes.

## Matching — how a line clears T2

Both sides are **segmented** identically: `\`-continuations joined, then split
on `&&`, `||`, `|`, `;`, and newlines; each segment has its `FOO=1` / `env
FOO=1` prefix peeled, quote characters dropped, and is split on whitespace.
Each segment yields a **head** (the binary's basename) and a set of
**operands** — every token that is neither the head nor a `-flag`.

Segmenting is what keeps this hook usable: a transcript command is routinely a
compound (`cd /repo && grep ...`, a newline-separated `cd` then the work, `env
FOO=1 grep ...`, a pipe into `head`), and normalizing the whole string leaves
`cd` or `env` as the head so the `grep` that actually ran matches nothing. A
genuinely transcribed line would then read as composed — the hook's dominant
output would be false positives (codex review round 1, P2).

The published line is judged on its **primary segment** — the first whose head
is not `cd`. It clears when some transcript segment shares that head **and**
covers at least **60%** of its operands.

Only an **odd** run of backslashes continues a Bash line. `foo \\` followed by a
newline is a literal backslash and then a *real* separator, so collapsing it
welds two commands into one — enough to hide a following `gh pr comment` from
the tokenizer's command-start walk, which means the body is never scanned at
all. The join is therefore odd-run-aware on both the surface-detection path and
the segmenting path (CodeRabbit round, #1261).

On the transcript side a segment following `||` is **not** recorded as
provenance: `A || B` runs `B` only when `A` failed, so `true || grep ...` would
otherwise register a `grep` that never ran and clear the published line this
gate exists to catch. Flags and the binary are excluded from the ratio
deliberately: those are what two unrelated invocations of the same binary
share, so counting them makes a swapped search term look like a match. What
discriminates one `grep` run from another is what it was pointed at.

A line never reaches T2 at all when any of these holds:

- it carries an **unexpanded variable or angle-bracket placeholder**
  (`$TOKEN`, `${TOKEN}`, `<CUSTOMER_ID>`). The redaction rule tells authors to
  move a secret into an env var and rerun, or substitute a placeholder and say
  so — firing on those would punish the honest path.
- the line carries `[transcribed]`, or its **opening fence** does
  (` ``` [transcribed] `), which clears the whole block.
- **no readable transcript** is available. Arm B then has no oracle, and an
  advisory would carry no information. T1 still fires — it needs none.
  `tail_lines` returns `[]` for a missing file, an unreadable one, and a
  genuinely empty one alike; only the last means "this session ran nothing",
  so readability is probed separately rather than inferred from the empty
  list (codex review round 1, P3).

## Provenance excludes calls that never ran

A transcript Bash `tool_use` is not proof of execution. A hook-blocked call, a
harness-refused one, and a user-denied one all appear as ordinary `tool_use`
blocks; only their `tool_result` says otherwise. Each `tool_use` is therefore
correlated with its result by id, and a result carrying `<tool_use_error>`,
`PreToolUse:`, or the fixed refusal sentence (`_transcript.REJECTION_PHRASE`)
drops that command from the provenance set.

Without this, a command the author *attempted* and never executed clears the
very line this gate exists to catch (codex review round 1, P1). A command that
ran and merely exited non-zero returns its own stderr instead of these
markers, so no legitimate probe is stripped. A `tool_use` carrying no id
cannot be correlated and is kept — dropping it would silently shrink the
provenance set.

## Response

```text
REMINDER (External-Surface Write / Composed Command Line): the body's fenced
blocks carry `$` command lines with no counterpart in this session's Bash
calls ({up to 3 samples, tier-prefixed}).
Every `$` block is a transcription, never a composition — copy the command
line from the invocation that produced the output you pasted. Output being
genuine does not make the line above it genuine: the dangerous case is a probe
run several times where the pasted line is the version you meant to run.
If the mismatch is legitimate — you moved a literal into an env var and reran,
or substituted a placeholder and said so — that shape already clears;
otherwise rerun and paste what actually ran, or mark the line `[transcribed]`
after checking it against the call it came from.
Set PRAXIS_COMPOSED_COMMAND_STRICT=1 to convert this advisory into a hard
block (exit 2).
```

Default mode writes the reminder to stderr and **exits 0**. Set
`PRAXIS_COMPOSED_COMMAND_STRICT=1` — the **literal value `1` only** — for a
hard block (exit 2).

## Known limits

Recall is deliberately low. An advisory that fires on honest bodies gets
ignored, and an ignored hook is worse than an absent one; the issue's own
direction was to start under-firing and raise later on measured fire data.

- **One differing operand does not fire.** At the 60% threshold, `gh pr view
  9999 --json headRefOid` clears against a transcript `gh pr view 1255 --json
  headRefOid` (3 of 4 operands match). Catching a single swapped identifier
  would need a threshold that fires on ordinary pipe-and-path drift.
- **Only a `$` followed by a space is a prompt.** The `❯`, `%`, and `>` prompt
  glyphs are not detected — `>` in particular is markdown quoting, and the
  other two are rare enough that admitting them buys little against the
  parsing risk.
- **`&&` right-hand segments stay provenance.** `false && cmd` records `cmd`
  even though it never ran. The two shapes segmenting exists for — `cd /repo &&
  grep ...` and `git fetch && git rebase ...` — really do run both sides, and
  treating the right side as unexecuted would bring back the false positives
  the segmenting removed. `||` carries no such shape, which is why it is
  dropped and `&&` is not.
- **Indented (4-space) code blocks are not scanned** — fenced blocks only.
- **An unclosed fence contributes nothing.** A body still mid-composition is
  not evidence anyone can act on, and scanning it would fire on drafts.
- **Only the last 400 transcript JSONL lines** are read (shared
  `TRANSCRIPT_SCAN_LINES`). A command run much earlier in a long session reads
  as unmatched. Mark it `[transcribed]` when that happens.
- **A `$` line inside a heredoc body or a quoted string** inside a fenced
  block is treated as a prompt line like any other.
- **Conversational prose is not covered.** A PreToolUse hook only sees tool
  inputs — the same composed block pasted into a chat reply never passes
  through this gate. Same structural limit as `source-citation-probe-gate`.
- **Heredoc / stdin (`--body-file -`) / relative-path body-file fail open.**
  Inherited from `_lib/_external_write_body.py`; use an absolute-path
  `--body-file` when you want the body scanned.

## Parsing guarantees

Inherited from `_hook_utils.safe_tokenize`: quoted strings, comments, and
`echo` arguments do not match; env prefixes and wrapper commands are peeled;
subshells are opaque to shlex. Malformed stdin JSON, a missing
`transcript_path`, and an unreadable transcript all fail open (exit 0, no
output).

## Tests

```bash
bash tests/hooks/advisory-nudge/test_composed_command_gate.sh
```
