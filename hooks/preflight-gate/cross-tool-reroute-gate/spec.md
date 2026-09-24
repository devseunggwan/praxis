# PreToolUse Cross-Tool Reroute Gate

Supported hosts: all

Reference: [Autonomy vs Convention — ETHOS.md](../../../ETHOS.md#autonomy-vs-convention)

`hooks/preflight-gate/cross-tool-reroute-gate/impl.py` asks before a call
reaches a target that a hook already blocked earlier in the session, through a
**different tool** than the one that was blocked (issue #1485).

## Why this exists

Observed sequence:

1. A describe-before-query gate blocked a SQL query on one query tool.
2. The describe the gate asked for ran on that tool and failed as an ordinary
   tool result.
3. The agent queried the same table through a second query tool the gate does
   not watch. Nothing stopped it.

A hook block is a statement about a target, not about a tool. Every existing
repeat-block signal (`block_message`'s repeat notice, the second-block rule) is
keyed on the same tool, so switching tools reset all of them.

## Trigger

All four, the cheapest first:

| # | Condition | Source |
| --- | --- | --- |
| 1 | The pending call names a target (see *Targets*) | `tool_input` |
| 2 | An earlier call this session was denied with `toolDenialKind: "permission-rule"` and `is_error: true`, and that call named targets | `transcript_path`, resumable scan |
| 3 | The pending call is a **different tool family** and shares at least one target with the blocked call | set intersection |
| 4 | No call with the pending tool on that target has run since the block (see *Lifting*) | same scan |

All four → `permissionDecision: "ask"`. The reason names the target and quotes
the original block's text, so the operator sees what was refused and why.

## Targets

A closed list, compared literally after normalization.

| Kind | Read from | Normalization |
| --- | --- | --- |
| SQL table | the identifier after `FROM` / `JOIN` / `INTO` / `UPDATE` / `DESCRIBE` / `TABLE`, in text that **executes a query**: an MCP input's `sql` / `query` / `statement` field, or a Bash command that runs a SQL client in command position (`trino`, `psql`, `mysql`, `duckdb`, `sqlite3`, `clickhouse`, `bq`, `snowsql`) | lowercased, quotes stripped |
| File path | the path of a blocked Edit / Write / NotebookEdit | absolute, exact |

A pending call reaches a blocked path when it is an Edit / Write / NotebookEdit
on that exact path, or a Bash command that **writes** it: a redirect, `tee`,
`touch`, `sed -i`, `cp` / `mv` / `rm`, or a write-mode `open(...)`.

Deliberately excluded, each because it produced false positives on the corpus
replay below:

- **Prose and code.** A commit body saying "from the", a PR body quoting
  `$ trino` output, a markdown table cell, and a `from x import y` line name
  words after SQL keywords without querying anything.
- **Writing a file that mentions a table.** Recording a query in an anchor is
  not running it.
- **Reads of a blocked file.** `wc -c`, `grep`, `open(path).read()`.
- **Unqualified names.** `tbl` does not match `cat.sch.tbl`: guessing which
  qualified name an unqualified one resolves to turns a literal gate into a
  fuzzy one.
- **Paths from a blocked Bash call.** A Bash block is usually about the
  command's shape (a glob, a flag, a title), not the files it mentions.

## Same tool family is silent

Retrying the blocked tool with a corrected call is the recovery the block asks
for. Edit, Write and NotebookEdit count as one family, because gates register
them under one matcher: switching among them reaches the same gate again. MCP
tools are compared by full name, so a second tool on the same server is a
different tool — that is the originating incident.

## Lifting

The block is **never** lifted by a later call on the original tool succeeding.
In the incident the describe the gate asked for *ran* and failed as an ordinary
result, so "a later call was not denied" would have lifted the block one call
before the reroute.

What lifts it, per (block, new tool) pair, is a call with that new tool on that
target that actually ran — the operator approved this very ask once. A
rejected ask leaves the pair armed. The lift is derived from the transcript,
so no state file can drift from what happened.

## Fail-open

Every read failure is silent: missing transcript, read error, or a scan that
has not caught up (`scan_transcript_resumable` returns `complete=False`).
Unlike `rejected-mutation-reconsent-gate`, the reach here is every SQL query
and every file edit, so failing closed would ask on routine calls.

## Tier

`ask`, with no bypass marker. Approving the ask is the operator's decision to
take the new route; an agent-attachable token would let the agent make it.

## Recurrence evidence

Replay of the reducer over every local Claude Code transcript
(`~/.claude/projects/*/*.jsonl`, 886 files), evaluating the gate at each tool
call against the state folded from the records before it:

| Revision | Fires | Sessions with a fire | What the fires were |
| --- | --- | --- | --- |
| First draft (SQL keywords read from any text, path matched as a substring) | 238 | 108 | Mostly prose: `from the`, `into a`, Python imports, anchors that mention a table |
| SQL read only from query fields and command-position clients; one file-edit family; Bash must write the path | 6 | 5 | All six are reroutes (below) |

The six, each read against the blocked call's reason and the pending call's
input:

| Blocked | Rerouted through | Target |
| --- | --- | --- |
| MCP query tool (describe-before-query gate) | a second MCP query tool on another server | the table (the originating incident) |
| MCP query tool (describe-before-query gate) | another MCP tool on the same server | the table |
| MCP query tool (describe-before-query gate) ×2 | the SQL client CLI in Bash | the table |
| Write (protected-branch guard) ×2 | `touch` in Bash | the file |

The replay script is not committed: it imports this `impl.py` and folds the
transcript line by line through `reduce_event`, calling `find_reroute` before
each tool call.

## Known limitations

1. A SQL client driven from an inline script (`import trino` in a heredoc) is
   not a query surface; neither is a path held in a shell variable. Both are
   misses, not false positives.
2. The gate cannot know which tools the blocking hook watches. A block from a
   hook that also watches the new tool asks once where the second hook would
   have blocked anyway.
3. Only the 20 most recent blocks with targets are kept.
