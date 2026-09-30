# PreToolUse Composed Command Gate

Supported hosts: all

`hooks/advisory-nudge/composed-command-gate/impl.py` is a **default-on**
PreToolUse(Bash) advisory that
fires when an external-write body's fenced blocks carry `$` command lines with
no counterpart among this session's Bash calls.

It enforces the clause *"Every `$` block is a transcription, never a
composition"* ([`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries)) at the
publication surface (issue #1117).
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
| T2 `unmatched` | a shell-shaped line whose pipeline does not appear, segment by segment, inside any Bash command this session ran | yes |

Surfaces scanned are the shared ones (`_lib/_external_write_body.py`): `gh
issue|pr comment|create|edit`, `gh pr review` with `--body` / `-b` /
`--body=` / `--body-file` / `-F`, plus the `gh api` comment endpoints
(issue #1265) — a
`POST` / `PATCH` / `PUT` against `repos/{o}/{r}/issues/comments/<id>`,
`issues/<n>/comments`, `pulls/comments/<id>`, `pulls/<n>/comments` or
`pulls/<n>/reviews`, with the body from `-f body=` / `--raw-field`,
`-F body=@<file>` / `--field` (`@` expanded as gh expands it). `--input` makes
the body unknown and nothing is scanned — including beside a `body=` field,
which gh sends as a query parameter rather than merging it into the file's
request body. Every other method or endpoint stays outside: a read, `graphql`,
a workflow dispatch.

That gap was this hook's own motivating case. The session that built it
produced five composed `$` lines and every one of them went out through
`gh api`, because a rev ≥2 anchor is a `PATCH` by comment id and no
`gh <noun> <verb>` form can issue one — so the hook watching for composed
evidence could not see the channel the evidence actually used.

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

The published line is judged on **every segment**, not only the first (#1540).
`cd` segments are dropped on both sides, and trailing `| head` / `| tail`
segments are dropped from the published side only: trimming long output for
the body is honest and cannot change what the earlier segments ran. The line
clears when its remaining segments appear as a **contiguous run** inside the
segments of one executed command, each pair matching as follows:

- the heads are equal;
- the operand counts are equal, and the operands pair up one to one;
- a published operand pairs with an executed one when they are equal, or when
  the executed operand ends with `/` plus the published one
  (`docs/hook/INDEX.md` for a run of `/abs/repo/docs/hook/INDEX.md`).

Segments the executed command has around that run (`; echo "EXIT=$?"`, a
`| head` the body dropped) do not matter. A changed operand anywhere in the
published pipeline does: under the earlier first-segment, 60%-overlap rule,
`git show … | sed -n '125,133p'` cleared against a run of `sed -n '120,145p'`,
and that later segment is exactly where a tidied line hides.

Redirections (`2>&1`, `>/dev/null`, `> out.txt`) are dropped before operands
are counted: they say where output went, not what ran, and a body routinely
omits them.

A `$` line that ends inside an open single or double quote continues to the
line that closes the quote. `$ python3 -c "` opens a script; reading only its
first line compared a bare `python3 -c` against the run and cleared any script
at all.

Only an **odd** run of backslashes continues a Bash line. `foo \\` followed by a
newline is a literal backslash and then a *real* separator, so collapsing it
welds two commands into one — enough to hide a following `gh pr comment` from
the tokenizer's command-start walk, which means the body is never scanned at
all. The join is therefore odd-run-aware on **three** paths: the
surface-detection walk, the segmenting of transcript commands, and the pulling
of `$` lines out of a fenced block. The third matters for a different reason
than the other two — a published `$ transcribed \\` followed by `$ composed`
would weld into one line judged on the *first* command's head, so the composed
line rides in behind the transcribed one and is never examined.

On the transcript side a segment following `||` is **not** recorded as
provenance: `A || B` runs `B` only when `A` failed, so `true || grep ...` would
otherwise register a `grep` that never ran and clear the published line this
gate exists to catch. Flags and the binary are excluded from the operands
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
  A missing or unreadable file and a genuinely empty one are different
  answers — only the last means "this session ran nothing" — so the read is
  `iter_transcript_bounded`, which raises for the first and yields nothing for
  the last in one open, instead of the earlier probe-then-read whose window
  let a file vanish between the two (codex review round 1, P3; issue #1279).

## Transcript scope

The **whole session** is read, not a tail (#1540). A body is written long
after the probe it quotes, and the 400-line tail the hook used to read missed
runs the body really did quote.
Only lines carrying `"tool_use"` or `"tool_result"` are parsed.

The read is capped at **512 MiB** (`SCAN_MAX_BYTES`). The largest local
session measured (140 MB) streamed through the needle filter in 0.23–0.26 s,
so the cap costs about 0.9 s against the hook's 5 s timeout. A transcript over
the cap is treated like an unreadable one: no oracle, T2 silent, T1 still
fires.

## MCP calls as provenance

A `$` line may quote an MCP tool call rather than a shell command. It clears
when it has the shape `name(args)` or `name (args)` and an executed MCP call
matches it:

- the tool's full name, or its last `__`-separated part, equals `name`
  (`lookup` for `mcp__srv__lookup`);
- every quoted value and every `key=value` / `key: value` value in `args`
  appears in that call's JSON-encoded input.

A prose description of a call (`$ (tool, phase=prod) SELECT …`) has neither
shape and stays unmatched: it transcribes nothing. The same never-ran rule
below applies — a blocked MCP call is not provenance. A T1 line that matches
an executed MCP call this way is not reported either.

## Provenance excludes calls that never ran

**Contract.** Silence from arm B means *the published line has a syntactic
counterpart in the command string of a Bash call this session actually
dispatched* — it does **not** mean the published line executed. Two gaps are
deliberate and permanent under this contract: a segment to the right of `&&`
is recorded whether or not the left side succeeded, and a command that ran and
failed is still provenance. This gate detects lines that were **never typed
into a tool call**, which is the defect it was built for; establishing that a
recorded command *succeeded* needs an exit status the transcript schema does
not carry. An author who wants the stronger claim states it themselves — the
hook cannot.

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

- **Honest rewrites that still fire.** #1538's sample left these causes out
  of scope (#1540): an invocation prefix that differs (`pytest` published,
  `python3 -m pytest` run); quoting that changes how the words split; a
  command openly shortened for the body; a command that ran as a string
  argument of another command; a `for` or `echo` loop published as its body;
  a comment after the `$`. Mark such a line `[transcribed]` after checking it.
- **Operands are compared as sets.** A published line that repeats an
  operand, or swaps the order of two, matches a run that has them once or in
  the other order.
- **MCP values are substring-matched** against the call's JSON input, so a
  short value (`1`, `a`) matches almost any call to that tool.
- **Only a `$` followed by a space is a prompt.** The `❯`, `%`, and `>` prompt
  glyphs are not detected — `>` in particular is markdown quoting, and the
  other two are rare enough that admitting them buys little against the
  parsing risk.
- **`&&` right-hand segments stay provenance.** `false && cmd` records `cmd`
  even though it never ran. The two shapes segmenting exists for — `cd /repo &&
  grep ...` and `git fetch && git rebase ...` — really do run both sides, and
  treating the right side as unexecuted would bring back the false positives
  the segmenting removed. `||` carries no such shape, which is why it is
  dropped and `&&` is not. This is the residual false-clear the Contract
  above scopes out: closing it needs a per-call exit status, which the
  transcript schema does not record.
- **Indented (4-space) code blocks are not scanned** — fenced blocks only.
- **An unclosed fence contributes nothing.** A body still mid-composition is
  not evidence anyone can act on, and scanning it would fire on drafts.
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

**Slack / Notion MCP writes are out of scope (#1359).** The
`mcp__.*slack.*|mcp__.*notion.*` registration this hook carried was dropped
on an owner judgement: it hardcoded two vendors into the runtime surface for
a leg that only reaches an installer who has such a server, and no fire was
ever measured on it. The rule itself is unchanged — it still scans `gh` external writes and fires when a
fenced `$` line matches T1 or, against the transcript, T2. The body extractor those writes used
(`_lib/_external_write_body.py`) stays: the opt-in
`external-write-falsify-check` still consumes it.

## Tests

```bash
bash tests/hooks/advisory-nudge/test_composed_command_gate.sh
```

### Replay fixtures

`tests/fixtures/composed-command-gate/replay-1540/NN/` holds the 14 fires
from #1538's sample that reproduced, pseudonymized. Every word outside a short list
of common command names became a consistent alias (`w12_`, whose underscore
keeps a path of aliases from forming the 40-character run
`tests/test_no_live_keys_in_fixtures.sh` flags; digit-only words
became six-digit numbers, so `2>&1` and `1,5p` keep their shape), so a
published line and the run it was or was not copied from keep the same equal
or unequal relation. The body keeps only its fenced blocks; the transcript
keeps the Bash and MCP calls that clear at least one published line, plus the
never-ran results, with the last 450 lines at their original spacing.
The body is stored as `body.txt`, not `.md`, so the markdownlint and
link-check jobs do not lint a pasted PR body as a repository document.

A fixture was accepted only when it produced the same tier list as its real
counterpart under both the base and the new impl. Cases 01, 02, 04, 08 and 14
are silent under the new impl; 10 and 12 are the sample's true positives and
still warn; the others still warn for the out-of-scope causes above.
