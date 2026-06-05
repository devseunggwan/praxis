# PreToolUse External-Write Falsify Check (opt-in)

Supported hosts: all

`hooks/external-write-falsify-check.py` is an **opt-in** PreToolUse advisory
that warns before posting hypothesis-stage text to external surfaces (PR
comments, issue bodies, Slack messages, Notion pages). It enforces the
global `~/.claude/CLAUDE.md` rule `External-Surface Write Requires Falsification`
(retraction-cost / downstream-reader-training framing).

### Why this exists — and why opt-in

The four production praxis hooks (`block-gh-state-all`, `side-effect-scan`,
`memory-hint`, `codex-review-route`) each followed the canonical adoption
path: feedback-memo → ≥5 recurrences → structural hook. The
`External-Surface Write Requires Falsification` rule does not yet have
that recurrence trail (zero memory entries, zero issues at adoption time
— see issue #173). Shipping default-on would skip the established
evidence bar; shipping with the code unavailable would discard already-
written infrastructure (245 LOC + 151 LOC tests, ported `_hook_utils`
patterns).

Compromise: the code lands in `main`, **but `hooks/hooks.json` does not
register it**. Users who want the advisory enable it explicitly. This
preserves the option without changing default behavior, and gives
evidence collection a defined opt-in cohort instead of forcing the
question.

### What is warned

| Tool call shape | Condition | Advisory |
|----------------|-----------|----------|
| `gh issue comment --body <text>` | body contains hypothesis marker | Check 1 |
| `gh pr comment -b <text>` | body contains hypothesis marker | Check 1 |
| `gh pr review --comment --body <text>` (or `--approve` / `--request-changes`) | body contains hypothesis marker | Check 1 |
| `gh issue create --body-file <path>` | body contains hypothesis marker (file contents read) | Check 1 |
| `gh pr edit -F <path>` | body contains hypothesis marker | Check 1 |
| `mcp__*slack*__*send*` / `*post*message*` | body field contains hypothesis marker | Check 1 |
| `mcp__*notion*__*create_page*` / `*update_page*` | text fields contain hypothesis marker | Check 1 |
| `Write` to staging path (`/tmp/*-issue-*.md`, `/tmp/*-pr-*.md`, `.omc/plans/*.md`) | cluster-approval language in last 5 user messages | Check 3 |
| `gh issue list` / `gh search issues` / Read tool | — | passthrough silent |

Hypothesis markers (whole-segment substring match): English 16 —
`might`, `could be`, `could fail`, `could break`, `potentially`,
`potential `, `appears to`, `seems to`, `likely `, `suspected`,
`hypothesis`, `is failing`, `is broken`, `may have`, `may be `; Korean 6 —
`가설`, `추정`, `추측`, `가능성`, `의심됨`, `의심된다`.

### Response

```text
REMINDER (External-Surface Write Falsification): hypothesis markers
detected in body. Verify each factual claim with executed evidence
before posting...
```

Default mode emits the reminder to stderr and **exits 0** (advisory,
not block). Set `PRAXIS_EXTERNAL_WRITE_STRICT=1` to convert into a hard
block (exit 2) — useful in CI or session-pinned workflows where you
want the gate to fire on the user's behalf.

### How to enable

Add an entry to your `~/.claude/settings.json` or `.claude/settings.json`
under `hooks.PreToolUse`. Include `Write` in the matcher to enable
cluster-approval staging detection (Check 3):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Write|mcp__.*slack.*|mcp__.*notion.*",
        "hooks": [
          { "type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/external-write-falsify-check.sh" }
        ]
      }
    ]
  }
}
```

For strict mode (hard block):

```bash
export PRAXIS_EXTERNAL_WRITE_STRICT=1   # Check 1: hypothesis markers
export PRAXIS_AUTHOR_EXEMPT_STRICT=1    # Check 2: author-exempt identifiers
export PRAXIS_CLUSTER_APPROVAL_STRICT=1 # Check 3: cluster-approval staging
```

Each env var controls its own check independently. All accept the **literal
value `1` only** — `true` / `yes` / `on` do NOT activate strict mode (defaults
to advisory).

Restart Claude Code after adding the entry.

### Heuristic limits

The marker check is purely lexical. It cannot tell internal-team-DM
Slack from a customer-facing channel, nor can it tell a verified-fact
"could break" (an evidenced consequence) from a hypothesis "could break"
(an unverified guess). The global `~/.claude/CLAUDE.md` rule's `Applies to` / `Does NOT
apply to` carveouts are NOT replicable in marker detection — the user
remains responsible for interpreting the reminder in context.

Known specific gaps (acknowledged; preconditions for any future
default-on flip — see follow-up tracking issue):

- **`likely` / `potential ` markers prone to false positives.** Phrases
  like "Most likely cause: stale cache" (a verified RCA write-up) or
  "Potential customers list: 5 brands" (business term) trip the warning.
- **Literal `\n` inside a quoted `--body` value splits the body.** The
  shared `_hook_utils.safe_tokenize` treats literal `\n` characters as
  command separators inside quoted strings. Use `--body-file` or a
  heredoc when content contains newlines and you want the full body
  scanned as one unit.
- **`--body-file -` / `-F -` (stdin) silent-passes.** `gh` accepts `-` as
  the file path placeholder for stdin (`gh issue create -F -`). The hook
  treats `-` as a literal file path; `open("-")` fails and the body is
  recorded as empty, so any hypothesis content streamed via stdin is not
  scanned. Use `--body-file <real-path>` when you want the body scanned.

### Author-exempt detection (issue #183)

A separate advisory fires when the body contains **claim shapes** —
mapping table rows or code blocks with unverified technical identifiers —
and **no verification call** is found in the recent transcript.

This catches the pattern: the agent authors a label vocabulary table,
a column-list example, or a CLI flag reference without ever running
`gh label list`, `DESCRIBE`, or `<binary> --help` first.

#### What is detected

| Claim shape | Identifier patterns |
|-------------|---------------------|
| Markdown table row (`\| … \|`) | `--cli-flag`, `type:label`, `` `backtick-id` `` |
| Any language code block (` ``` `) | All of the above + `snake_case` column names, `schema.table` |

Non-technical table cells (prose words without these patterns) do not
trigger the check.

#### Verification trail

Recent Bash commands (last 400 JSONL lines of the transcript) are
scanned for:

- `gh label list` — satisfies label-name claims
- `<binary> --help` / `gh <sub> --help` — satisfies CLI flag claims
- `DESCRIBE <table>` / `SHOW COLUMNS` — satisfies column/table claims

If any of these is found, the advisory is suppressed.

#### Advisory text

```text
REMINDER (External-Surface Write / Author-Exempt): body contains
mapping table or code-block identifiers ({identifiers}) with no
verification call found in recent transcript.
Own-authored labels, columns, and flags are in scope — run
gh label list / DESCRIBE / <binary> --help before publishing.
Set PRAXIS_AUTHOR_EXEMPT_STRICT=1 to convert this advisory into a
hard block (exit 2).
```

Default: advisory (exit 0). Set `PRAXIS_AUTHOR_EXEMPT_STRICT=1` for
hard block (exit 2). The `PRAXIS_EXTERNAL_WRITE_STRICT` variable
controls only the hypothesis-marker check (Check 1).

#### Known limits

- Transcript reading requires `transcript_path` in the hook payload.
  If the field is absent or the file is unreadable, the check
  fails-open (advisory never fires for verification trail — the
  claim-shape advisory still fires based on identifier detection only).
- Code blocks are detected by triple-backtick delimiters with any
  language tag. Indented code blocks (4-space) are not scanned.
- `snake_case` detection inside code blocks may produce false positives
  on common environment-variable names or two-word prose identifiers.

### Parsing guarantees

Inherited from `_hook_utils.safe_tokenize` (same primitive as
`side-effect-scan` and `block-gh-state-all`):

- Quoted strings, comments, and `echo` arguments do not match markers.
- Env prefixes (`FOO=1 gh ...`), wrapper commands (`sudo`, `env`,
  `time`), shell control-flow keywords are peeled before scanning.
- Subshells (`$(...)`) are opaque to shlex — not decomposed (same
  acknowledged limitation as the sibling hooks).

### Cluster-approval detection (issue #276)

A third advisory fires when:

1. **Tool call**: `Write` tool targeting a staging path
   - `/tmp/*-issue-*.md`
   - `/tmp/*-pr-*.md`
   - `.omc/plans/*.md`
2. **Transcript signal**: cluster-approval language appears in any of the
   last 5 user messages in the transcript.

This catches the global `~/.claude/CLAUDE.md` `No Approval Transfer Across Companion PRs`
violation pattern: a user approves multiple tasks in bulk ("all 4 together",
"1+3 같이"), and the agent begins writing per-child staging files without
per-action AskUserQuestion surfacing.

#### Cluster-approval patterns (English)

- `\b\d+ buckets? together\b` — e.g., "4 buckets together"
- `\ball \d+ as separate\b` — e.g., "all 4 as separate"
- `\bas approved above\b`
- `\bcluster (i|we) approved\b`
- `\ball \d+\b.{0,50}\bapprov\w*` — e.g., "all 4 approved", "all 4 go ahead and approv*"

#### Cluster-approval patterns (Korean)

- `\d+개\s*모두` — e.g., "4개 모두"
- `\d+\s*\+\s*\d+\s*같이` — e.g., "1+3 같이"
- `모두\s*승인`

#### Advisory text

```text
REMINDER (External-Surface Write / Cluster-Approval): cluster-approval
language detected in a recent user message.
Cluster approvals ("all N together", "1+3 같이", "as approved above")
do NOT auto-transfer to per-action staging writes.
Each child mutation (issue draft, PR body) requires its own explicit
per-action surfacing via AskUserQuestion.
Surface a dedicated AskUserQuestion for this specific action before
writing to a staging path.
Set PRAXIS_CLUSTER_APPROVAL_STRICT=1 to convert this advisory into a
hard block (exit 2).
```

Default: advisory (exit 0). Set `PRAXIS_CLUSTER_APPROVAL_STRICT=1` for
hard block (exit 2).

#### Known limits

- Requires `transcript_path` in the hook payload and a readable transcript
  file. If absent, the check fails-open (no advisory).
- Only the last 5 user messages (within the last 400 JSONL lines) are
  scanned — cluster approval signaled longer ago may not be detected.
- Staging path matching is suffix-based (`$` anchor). Paths that embed
  the pattern mid-string are not matched.

### Tests

```bash
bash tests/test_external_write_falsify_check.sh
```

Covers 34 cases across the warn / silent / strict-block dimensions:
`gh` write subcommands (`comment`, `create`, `edit`, `review`) with each
body flag form (`--body`, `-b`, `--body-file`, `-F`, `--body=value`),
MCP slack / notion writes including nested shapes (Notion
`children[].paragraph.rich_text[].text.content`, Slack
`blocks[].text.text`) gated to recognized container/leaf entry points so
that property metadata (`properties.{name}.title[].text.content`) does
not surface as body, Korean marker, verified-claim silent paths,
non-write commands (`gh list` / `gh search`), chained Bash writes,
strict env toggle, malformed-JSON fail-open, 3 author-exempt cases
(mapping table without verification, mapping table with transcript
`gh label list`, bash code block with column name), and 6 cluster-approval
cases (EN pattern + staging path, KO pattern + staging path, EN pattern +
non-staging path, staging path without cluster-approval language, strict
mode block, no-transcript fail-open).

### Evidence-trail follow-up

Memory entry for this rule + recurrence tracking will be filed as a
separate issue. The decision to flip default-on (or to roll back this
opt-in hook entirely) is gated on that trail.

Code-level preconditions for any future default-on flip are tracked in
issue #174. P2 (MCP nested-body extraction, gated to recognized
container/leaf entry points) has shipped. P3 (positional `gh` body
detection) was dropped after `gh --help` confirmed positional body is
not a supported gh CLI shape (`gh issue comment` accepts a single
positional, rejecting `<num> <body>` with `accepts 1 arg(s)`). P1
(false-positive frequency data accumulation) remains open and gates
the default-on flip.
