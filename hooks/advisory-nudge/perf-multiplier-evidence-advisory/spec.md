# perf-multiplier-evidence-advisory

Supported hosts: all

`hooks/advisory-nudge/perf-multiplier-evidence-advisory/impl.py` runs on
`PreToolUse(Bash)`. It fires a stderr advisory (never blocks) when a
performance multiplier notation or a lever verdict appears in the body of a
`gh issue|pr create|comment` invocation with NO adjacent controlled-timing
artifact in the same body text.

## Why this exists — gen-2 recurrence

Memory `feedback_perf_deliverable_requires_controlled_magnitude` (created at
the 71st retrospect, 2026-07-17) recurred at the shortest interval on
record — 6 days later, at the 74th retrospect (2026-07-23/24, ordercycle
precompute 49m task). Memory-only remediation had already failed once.

**Recurrence pattern:** performance optimization levers were sized from
proxy/arithmetic estimates (grid row-count x microseconds,
cardinality-correlation, dim string-width) and carried to a near-deliverable
(a draft GitHub issue) with NO controlled measurement. The controlled
measurement tool (DuckDB CLI) was reachable the entire session — it was only
run after the user asked "did a quantitative test actually happen?" At that
point ALL THREE estimates were overturned:

| lever | pre-measurement estimate | controlled measurement | error kind |
| --- | --- | --- | --- |
| dim integer encoding | ~1.76x | **3.06x** | magnitude 74% under-estimated |
| grid `NOT MATERIALIZED` | proposed as an improvement lever | **0.73x regression** | **sign error** — a win was proposed on a change that was actually a loss |
| memory 14->8GB | candidate lever | no change | non-lever |

The `NOT MATERIALIZED` case is the important one: it is not a magnitude
miss, it is a **sign** miss. Without measurement, a change that makes
performance WORSE would have shipped as an "improvement".

## Root cause

tracer + analyst convergence: both failure axes (magnitude inaccuracy and
lever sign error) share one root cause — the corrective action is identical
(measure first) — so one gate covers both rather than splitting into two
rules.

**Root discriminator:** the upstream investigation sub-agents were all
dispatched READ-ONLY (`조사, 코드 수정 금지`), so a proxy estimate was the
only possible output — the sub-agent layer performed correctly given its
constraints. The gap is the MAIN LOOP laundering that read-only proxy
estimate into a deliverable with no controlled-measurement checkpoint. The
gate therefore fires at the **output-block / deliverable-write boundary**,
not at sub-agent dispatch time — hence a PreToolUse hook on the deliverable
write itself (`gh issue|pr create|comment`), mirroring
`output-block-falsify-advisory` / `secret-print-redaction-advisory`.

Reference: issue [#850](https://github.com/devseunggwan/praxis/issues/850).

## What is detected

Both conditions must hold, scanned over the SAME body text — and "the same
body text" means the body of ONE invocation (see
[Body-text extraction](#body-text-extraction)):

### (1) Perf multiplier notation OR lever verdict

- Multiplier: `3x` / `3.06x` / `3×` (digit + `x`/`×`), `1.76배` (Korean
  multiplier suffix), a timing pair `49m -> 12m` / `49분 → 12분`
  (unit-tagged number, arrow, unit-tagged number), the prose form `from 49m
  to 12m`, or a percentage tied to a direction word within a 24-char window
  either side (`faster`/`slower`/`regression`/`improvement`/`빠름`/`느림`/
  `향상`/`저하`) — a bare `%` with no direction word does not count (avoids
  firing on unrelated percentages like disk usage).
- Lever verdict: KO substrings `채택` / `개선` / `역효과`, or the EN word
  `lever` (word-boundary, case-insensitive).

### (2) NO controlled-timing artifact in the same body

Pass condition (silences the whole scan for this invocation): a cited
`$ command -> output` line (mirrors `completion-signal-gate`'s
`_CITED_OUTPUT_RE` convention), or a `wall-clock:` / `elapsed` / `real Nm`
timing marker, anywhere in the body text. This hook does not verify the
artifact actually measures the SAME lever the multiplier claims — that
adequacy judgment is out of scope, matching the sibling gates' presence-only
enforcement (`negative-existence-verdict-gate`'s "Enumerated:" line is the
same design: presence, not adequacy).

## Body-text extraction

### Each body is bound to its own invocation (issue #973)

One Bash command can chain several `gh` calls. The command is tokenized, split
into per-command segments at the shell separators (`;`, `&&`, `||`, `|`, `&`,
and a newline outside quotes), and each segment is tested independently: is
argv[0] the `gh` binary, does it carry an `issue`/`pr` object followed by a
`create`/`comment` verb, and what body does THAT segment post. The scan then
runs per body, and fires on the first body that claims a multiplier or a lever
verdict without its own timing artifact.

This is structural tokenization, not a regex over the raw command text
(DESIGN.md, "Design mechanisms shared by all hooks"). The earlier regex shape
matched `gh (issue|pr) (create|comment)` anywhere in the command string and
then returned the FIRST `--body` in the flat argv, which gave three wrong
answers — all three are now pinned as regression cases R1–R4 in the test file:

| Command shape | Pre-fix | Correct |
| --- | --- | --- |
| `gh issue comment 1 --body "elapsed 12.0s" && gh pr comment 2 --body "5x faster"` | silent — body 1's artifact was the only body read | advisory: body 2 claims a multiplier with no artifact of its own |
| `curl -X POST --body "harmless" … && gh issue create --body "5x faster"` | silent — `curl`'s body was donated to the `gh` scan | advisory: only the `gh` segment's body counts |
| `echo "run gh issue create later" > note.txt && printf %s --body "5x faster"` | advisory — the trigger words matched inside a quoted argument | silent: no deliverable write exists |

**Evidence does not cross invocations.** A timing artifact cited by one `gh`
call measures nothing about a multiplier posted by another, so it silences only
its own body.

### Newlines vs multi-line bodies

An unquoted newline is a command separator and is rewritten to `;` before
tokenizing; a newline INSIDE a quoted body is left alone. The shared
`safe_tokenize` cannot be used here for exactly this reason — it pre-splits on
every raw newline, so a quoted multi-line body is cut mid-string, each fragment
then fails to parse on the unmatched quote, and the skip-on-`ValueError` arm
drops them all. Measured: `gh pr comment 1 --body "### Verification\n3x speedup
expected"` tokenizes to `[';']` under `safe_tokenize`, losing the invocation
entirely. Multi-line bodies are this hook's primary input, so the token pass
uses `shlex.split` and the command breaks it would otherwise swallow are
restored by the unquoted-newline rewrite. Command-boundary splitting still uses
the shared `iter_command_starts` / `strip_prefix` primitives.

### Flag forms

- `--body "..."` / `-b "..."` / `--body=...` — inline value, read directly
  from the parsed argv.
- `--body-file <path>` / `-F <path>` / `--body-file=<path>` — the file is
  read from disk (relative paths resolve against the hook process's cwd).
  An unreadable/missing target silently yields no body text for THIS
  invocation (under-firing accepted over hard-failing the hook on a disk
  read error).

The invocation itself is recognized through a path-prefixed binary
(`/usr/bin/gh`) and through `gh`'s own global flags sitting between the binary
and its subcommand (`gh --repo owner/name pr comment …`).

## Scope — deliberately narrow

`PreToolUse(Bash)` only, `gh issue|pr create|comment` only. A prose
"synthesis block" surfaced with no tool call at all (a perf claim written
directly into chat, never posted via `gh`) is out of scope here — that is
the `Stop`-lane concern already covered by `proposal-premise-gate`
(issue #846) for code-checkable premises in general. One gate per surface
(YAGNI) rather than one hook trying to cover both PreToolUse and Stop.

## Silent cases

| Input | Why silent |
| --- | --- |
| `gh issue list`, `gh pr view` (no create/comment) | not a deliverable write |
| body with `3x` but also a `$ hyperfine ./bench -> wall-clock: 12.4s` line | timing artifact present |
| body with a bare `73%` and no direction word nearby | no direction word — likely an unrelated percentage |
| body with `lever` discussed but no multiplier and no verdict token | condition (1) not met |
| `--body-file` target does not exist on disk | no body text extractable |
| a chain where EVERY `gh` body carries its own timing artifact | each body satisfies the pass condition independently |
| `gh issue create` appearing only inside a quoted string (`echo "… gh issue create …"`) | no `gh` invocation is actually run |
| a non-`gh` command's `--body` (e.g. `curl --body …`) with no `gh` deliverable in the command | the body belongs to no deliverable write |
| non-Bash tool call | out of scope |
| malformed JSON stdin | fail-open |

## Fail-open contract

| Condition | Behavior |
| --- | --- |
| Malformed / missing stdin JSON | exit 0, silent |
| `tool_name != "Bash"` | exit 0, silent |
| No `gh issue\|pr create\|comment` invocation in any command segment | exit 0, silent |
| Unbalanced quotes (tokenization raises) | exit 0, silent — no bodies extracted |
| `--body-file` unreadable | exit 0, silent (for that invocation) |
| Any uncaught exception | exit 0 (`@fail_open`) |

Advisory only — writes to stderr, exits 0. Never blocks.

## Registration

Deferred to phase integration (this PR ships `impl.py` + `spec.md` + tests
only — no `hooks/manifest.json` entry, no generated `hooks.json`, no
`docs/hook/INDEX.md` / `ARCHITECTURE.md` update). Intended registration:
`PreToolUse`, matcher `Bash`, role `advisory-nudge`.

## Tests

```bash
bash tests/hooks/advisory-nudge/test_perf_multiplier_evidence_advisory.sh
```
