# PreToolUse Source-Citation Probe Gate

Supported hosts: all

`hooks/source-citation-probe-gate.sh` is a **default-on** PreToolUse advisory
that fires when an external-write body (PR/issue bodies and comments, Slack
messages, Notion pages) cites **source facts** — `file:line` references,
exact call syntax, or test-semantics claims — with **no read-probe** found in
the recent transcript or in the body itself.

It enforces the global `~/.claude/CLAUDE.md` Information Accuracy rule's
"checkmark = citation" clause at the external-write surface: a source-fact
citation written from recall (not from a Read / `grep -n` executed this
session) is a fabrication-shaped claim, and publishing it trains downstream
readers (review bots, teammates) on potentially wrong specifics (issue #830).

Unlike `external-write-falsify-check` (opt-in — its hypothesis-marker rule
lacked a recurrence trail at adoption time), this hook is
**manifest-registered (default-on)**: the recall-prone-citation pattern has
an established recurrence trail and the clearing arm (any Read/grep of the
cited file) keeps the false-positive cost low.

### What is detected

Detection runs on the extracted write body after two preprocessing steps:
URLs are stripped (`\w+://\S+`), then fenced code blocks (paired ` ``` `)
are removed — code samples are `external-write-falsify-check`'s
author-exempt Check 2 territory.

| Tier | Citation shape                | Pattern                                                                                                     |
| ---- | ----------------------------- | ----------------------------------------------------------------------------------------------------------- |
| T1   | `file:line` reference         | `impl.py:42`, `hooks/foo/impl.py:42`, anchor form `impl.py#L42`; extension must start with a letter; TLD denylist {com, org, net, io, co, ai, dev} on the extension |
| T2   | exact call syntax             | inside **inline code spans (single backticks) only**: `name(...)` whose argument list contains `.` or `[` — the weakest detector by design |
| T3   | test-semantics claim          | `test*` token + `assert*` / `raise(s)` / `expect*` within an 80-char same-sentence window (case-insensitive) |

Surfaces scanned (same extraction as `external-write-falsify-check` — gh
argv walk + MCP nested container/leaf walk, 2nd copy per repo 2-copy
convention):

- `gh issue|pr comment|create|edit`, `gh pr review` with `--body` / `-b` /
  `--body=` / `--body-file` / `-F` (file contents read best-effort)
- `mcp__*slack*__*send|post|update*`, `mcp__*notion*__*create|update*page*` /
  `*append*block*` (nested `children[].paragraph.rich_text[].text.content` /
  `blocks[].text.text` shapes gated to recognized container/leaf entry points)

### Clearing — "probe basis present" (per citation)

**Arm A (in-body):**

- `[verified]` token on the citation's own line, OR
- a filled-in `Probe: <command> → <output>` line anywhere in the body.
  Anti-bypass ported from `output-block-falsify-advisory` (PR #796): a
  Probe line still carrying scaffold placeholders (`<command>`,
  `<observed>`, `<...>`, `<output>`) or with empty evidence after the
  arrow (`→` / `->`) does NOT clear.

**Arm B (transcript, last 400 JSONL lines):**

- T1 clears when a `Read` tool_use file_path basename equals the cited
  basename, or a read-tool Bash command (`grep` / `rg` / `sed` / `cat` /
  `head` / `tail` / `awk` / `nl`) contains it.
- T2 clears when the called function name appears in any transcript Bash
  command or Read file_path.
- T3 clears on a `pytest` run or a `test_*` file basename in a transcript
  Bash command / Read.

### Response

```text
REMINDER (External-Surface Write / Source-Citation Probe): body cites
source facts ({up to 3 samples}) with no read-probe found in the recent
transcript.
file:line, exact call syntax, and test-semantics claims are recall-prone —
re-read the cited site (Read / grep -n) before publishing, then cite inline
(`Probe: <command> → <output>`) or append `[verified]` on the citation line
after checking.
This gate only sees external-write bodies — free conversational prose is
NOT covered (known limit).
Set PRAXIS_SOURCE_CITATION_STRICT=1 to convert this advisory into a hard
block (exit 2).
```

Default mode emits the reminder to stderr and **exits 0** (advisory, not
block). Set `PRAXIS_SOURCE_CITATION_STRICT=1` to convert into a hard block
(exit 2). The env var accepts the **literal value `1` only** — `true` /
`yes` / `on` do NOT activate strict mode.

### Known limits

- **Conversational prose is not covered.** A PreToolUse hook only sees tool
  inputs — a recall-fabricated `file:line` in the assistant's free prose
  response never passes through this gate. This is an accepted structural
  limit of the enforcement point (issue #830 AC).
- **Heredoc / stdin (`--body-file -`) / relative-path body-file silently
  fail-open.** `gh ... --body-file -` streams via stdin; a heredoc body is
  not visible in the argv; a relative `--body-file` path resolves against
  the hook's cwd, not the worktree. All three record an empty body — no
  scan. Use an absolute-path `--body-file` when you want the body scanned.
- **Literal `\n` inside a quoted `--body` value splits the body.** The
  shared `_hook_utils.safe_tokenize` treats literal `\n` characters as
  command separators inside quoted strings (same limit as
  `external-write-falsify-check`).
- **Fenced code blocks are excluded** from all tiers — identifiers inside
  ` ``` ` blocks are `external-write-falsify-check`'s author-exempt Check 2
  territory. Unclosed fences leave their content scanned (accepted).
- **Scheme-less `domain:port` outside the TLD denylist may false-positive.**
  `internal.corp:8080` has extension `corp` (not in the denylist) and a
  digit run after the colon — it matches T1. The denylist covers the common
  public TLDs only.
- **T2 is the weakest detector.** Call syntax without a `.` / `[` in the
  argument list (`foo(x)`) is deliberately not matched, and a matching
  function name anywhere in any transcript Bash command clears it — recall
  is bounded, precision is prioritized.

### Parsing guarantees

Inherited from `_hook_utils.safe_tokenize` (same primitive as
`external-write-falsify-check` / `side-effect-scan`): quoted strings,
comments, and `echo` arguments do not match; env prefixes (`FOO=1 gh ...`)
and wrapper commands are peeled; subshells (`$(...)`) are opaque to shlex.
Malformed stdin JSON, missing `transcript_path`, and unreadable transcript
files all fail-open (exit 0, no output).

### Tests

```bash
bash tests/hooks/advisory-nudge/test_source_citation_probe_gate.sh
```
