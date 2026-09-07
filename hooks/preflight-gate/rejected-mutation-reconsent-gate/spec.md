# PreToolUse Rejected-Mutation Re-Consent Gate

Supported hosts: all

Reference: [Autonomy vs Convention — ETHOS.md](../../../ETHOS.md#autonomy-vs-convention)

`hooks/preflight-gate/rejected-mutation-reconsent-gate/impl.py` asks for fresh
per-action approval when a Bash command **or a worker dispatch** targets
something the user already refused in this session (issues #1007, #1035).

### Why this exists

Observed sequence:

1. The agent asks whether to delete the remaining ~295M objects. The user
   **rejects** the `AskUserQuestion`.
2. The next user message is a two-word instruction about running two
   workstreams in parallel. It names no deletion scope.
3. The agent launches the deletion worker.
4. A command classifier denied it. **The classifier was the only thing that
   stopped it — not judgement.**

A rejection is a standing NO for that mutation, and nothing re-read it before
the next utterance was consumed as consent. The governing rules already exist
(`one utterance = one mutation`, per-action approval); both carry recurrence in
the memory ledger with `enforcement: none`, so this is the third remedy on the
same pattern and the first structural one.

### Trigger

All three conditions, in this order (the cheapest first — the transcript is
never read unless the command itself qualifies):

| # | Condition | Source |
| --- | --- | --- |
| 1 | The pending tool call names a **literal identifier** (`s3://…`, `gs://…`, or a table named after `DROP` / `TRUNCATE` / `DELETE FROM`) in a **destructive position** — per surface, see below | `tool_input` |
| 2 | An earlier **`AskUserQuestion` was structurally rejected** this session | `transcript_path` via `_transcript.scan_user_rejections` |
| 3 | The rejected question names the **same normalized identifier** | set intersection |

All three → `permissionDecision: "ask"`, quoting the rejected question verbatim.

### The two surfaces (issue #1035)

Conditions 2 and 3 are identical across surfaces. Only condition 1 differs.

| Surface | Matcher | Where the identifier is read | Destructive marker required? |
| --- | --- | --- | --- |
| **Bash** | `PreToolUse(Bash)` | `tool_input.command`, per command segment | **Yes** for URIs — the segment must carry `rm` / `rb` / `delete` / `--delete` / … . SQL tables carry their verb inside the pattern |
| **Dispatch — tool** | `PreToolUse(Agent\|Task)` | `tool_input.prompt` + `tool_input.description` | **No** |
| **Dispatch — nested in Bash** | `PreToolUse(Bash)` | any command segment whose binary is a worker launcher (`cmux`, `claude`, `codex`, `gemini`), read as prose | **No** |

**Why the dispatch surface exists at all.** The originating incident fired
here, not on Bash: the deletion scope lived in a **worker prompt**, which the
Bash matcher never sees. That was recorded as limitation 1 of this spec and
deferred to a separate issue; #1035 is that issue.

**Why no destructive marker is required there.** A prompt is prose. `argv`
position and marker tokens carry no reliable meaning inside it, and the whole
reason the surface exists is that intent is hidden in prose — a prompt reading
"clean up the 2024 archive" carries the scope with no destructive token in
argv-position at all. What keeps the ask rare is condition 3, which is
unchanged: the identifier still has to be one the user **already refused** in a
structurally rejected question. The predicate stops at literal substring
matching of the same closed list; there is **no semantic reading of the
prompt**, which limitation 3 below names as the failure mode to avoid.

The nested-in-Bash shape matters because `safe_tokenize` collapses a quoted
prompt (`cmux … --command "claude -p '…'"`) into one token, so the
destructive-marker path above cannot see into it.

**Resolving the launcher takes three normalizations**, and skipping any one is
a silent bypass rather than a narrower gate — all three were found by Codex
round 1 against the first draft, which compared `argv[0]` literally:

| Written as | Raw `argv[0]` | Resolved by |
| --- | --- | --- |
| `WS=$(cmux …)` / `` WS=`cmux …` `` | `WS=$(cmux` | `_hook_utils.iter_command_texts` — `safe_tokenize` coalesces a substitution run into ONE token, so the inner command has no command start in the outer text at all |
| `env CMUX_QUIET=1 cmux …`, `CMUX_QUIET=1 cmux …`, `sudo claude …` | `env` / `CMUX_QUIET=1` / `sudo` | `_hook_utils.strip_prefix` |
| `/usr/local/bin/cmux …`, `(cmux …` | the path / grouped form | `_hook_utils._command_spec_key` basename comparison (the `_is_gh_binary` convention, issue #1099) |

The recursion follows **active** substitutions only: inside single quotes `$(`
starts no process, so `printf '%s' '$(cmux …)'` prints a string and stays
silent. The launcher set is the provider list `cmux-delegate` can actually
route to (ARCHITECTURE.md → Provider Routing), so `gemini -p "<prompt>"` counts
exactly as `claude -p` does. A URI sitting in some unrelated command's argument
still does not become a dispatch — the binary has to be a launcher.

The ask's wording names the surface (`this worker dispatch targets …` vs
`this command targets …`): an operator reading "command" while looking at an
`Agent` call would go hunting through argv for a target that lives in a prompt.

`Agent` is the tool name this repo's sibling `fan-out-scope-gate` matches;
`Task` is the same call under the name other hosts use, and both are registered
so a host rename cannot silently disarm the surface.

### What counts as a rejection — structural only

`_lib/_transcript.py::scan_user_rejections` is the shared enumerator (also
consumed by retrospect pre-scan lane 6 / `retrospect-mix-check` Gate-12,
issue #1013). A record qualifies only when **three independent markers agree**:

| Marker | Field |
| --- | --- |
| Denial kind | top-level `toolDenialKind == "user-rejected"` |
| Error flag | the `tool_result` block's `is_error: true` |
| Fixed sentence | the runtime's `"The user doesn't want to proceed with this tool use…"` |

The tool name and input are not on the rejection record; they are resolved
through the uuid index — `sourceToolAssistantUUID` → the assistant record's
`uuid` → the `tool_use` block whose `id` equals the rejection's `tool_use_id`.

**Option-label text is never classified.** A user who picks an "아니오 / No"
option without the runtime recording a denial is invisible to this gate. That is
deliberate: natural-language refusal detection at a permission boundary is
exactly what DESIGN.md's structural-tokenization rule keeps out of hooks, and an
ask tier is not a licence to guess. The matching cost is stated rather than
hidden — see *Known limitations* below.

### What counts as an overlap — one literal shared identifier

Normalization, per identifier class:

| Class | Normalized form | Rationale |
| --- | --- | --- |
| `s3://` / `gs://` | scheme + bucket lowercased, key path case preserved, trailing `/` dropped | bucket names are case-insensitive by rule; object keys are not, so folding them would equate two genuinely different prefixes |
| SQL table | `table:` + dotted name, quoting stripped, fully lowercased | unquoted SQL identifiers are case-insensitive |

There is **no verb-class fallback** ("both are deletions") and **no prefix
containment**: `s3://b/raw` does not match `s3://b/raw/2024`. A verb-class rule
would fire on every unrelated `rm` after any rejection, and an ask that fires on
everything is an ask nobody reads.

On the **command** side a URI is extracted only from a command segment that also
carries a destructive marker (`rm`, `rb`, `mv`, `delete`, `purge`, `--delete`,
…), so `aws s3 ls s3://b/raw/2024/` after a rejected delete of that prefix stays
silent. Segmentation reuses `safe_tokenize → iter_command_starts`, so one half of
a `&&` cannot inherit the other half's destructive verb. On the **question**
side no such requirement applies — the question is about the risky action by
construction. SQL identifiers carry their destructive verb inside the pattern.

### Matrix

| Pending command | Prior rejected question | Verdict |
| --- | --- | --- |
| `aws s3 rm s3://acme/raw/2024/ --recursive` | names `s3://acme/raw/2024/` | **ASK** |
| `aws s3 rm s3://acme/raw/2024 --recursive` | names `s3://acme/raw/2024/` | **ASK** — trailing `/` normalized |
| `gsutil -m rm -r gs://acme/raw/2024/` | names `gs://acme/raw/2024/` | **ASK** |
| `psql -c "TRUNCATE TABLE prod.events_raw"` | names `DROP TABLE prod.events_raw` | **ASK** |
| `aws s3 ls s3://other/ && aws s3 rm s3://acme/raw/2024/ -r` | names `s3://acme/raw/2024/` | **ASK** — destructive segment |
| `aws s3 rm s3://acme/raw/2025/ --recursive` | names `s3://acme/raw/2024/` | PASS — different prefix |
| `aws s3 rm s3://acme/raw/ --recursive` | names `s3://acme/raw/2024/` | PASS — no containment matching |
| `aws s3 ls s3://acme/raw/2024/` | names `s3://acme/raw/2024/` | PASS — read-only |
| `aws s3 rm s3://acme/raw/2024/ --recursive` | rejection joined to a **Bash** tool_use | PASS — only a rejected approval question is a standing NO |
| `aws s3 rm s3://acme/raw/2024/ --recursive` | no rejection in the transcript | PASS |
| `psql -c "DROP TABLE events_raw"` | names `DROP TABLE prod.events_raw` | PASS — qualified ≠ unqualified |
| `aws s3 rm s3://acme/raw/2024/ --recursive` | transcript past `REJECTION_SCAN_MAX_BYTES` | **ASK** — indeterminate, see below |

Dispatch surface, same three conditions:

| Pending dispatch | Prior rejected question | Verdict |
| --- | --- | --- |
| `Task(prompt="remove every object under s3://acme/raw/2024/")` | names `s3://acme/raw/2024/` | **ASK** |
| `Agent(prompt=…)` — same text | names `s3://acme/raw/2024/` | **ASK** — host tool-name variant |
| `Task(description="purge s3://acme/raw/2024/")` | names `s3://acme/raw/2024/` | **ASK** — `description` is read too |
| `Task(prompt="run TRUNCATE TABLE prod.events_raw")` | names `DROP TABLE prod.events_raw` | **ASK** |
| `cmux create wk --command "claude -p 'delete … s3://acme/raw/2024/ …'"` | names `s3://acme/raw/2024/` | **ASK** — nested worker prompt |
| `Task(prompt="… s3://acme/raw/2025/ …")` | names `s3://acme/raw/2024/` | PASS — different prefix |
| `Task(prompt="implement issue #1035 and open the PR")` | names `s3://acme/raw/2024/` | PASS — no identifier |
| `Task(prompt="… s3://acme/raw/2024/ …")` | no rejection in the transcript | PASS |
| `aws s3api head-object … s3://acme/raw/2024/` | names `s3://acme/raw/2024/` | PASS — the binary is not a launcher |
| `WS=$(cmux new-workspace --command "claude -p '… s3://acme/raw/2024/ …'")` | names `s3://acme/raw/2024/` | **ASK** — active substitution |
| `env CMUX_QUIET=1 cmux …` / `sudo claude -p …` / `/usr/local/bin/cmux …` | names `s3://acme/raw/2024/` | **ASK** — wrapper / assignment / path form |
| `gemini -p "… s3://acme/raw/2024/ …"` | names `s3://acme/raw/2024/` | **ASK** |
| `printf '%s' '$(cmux … s3://acme/raw/2024/ …)'` | names `s3://acme/raw/2024/` | PASS — single-quoted, starts no process |

### Response format

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "⚠️  Re-consent required: this command targets something you already refused. …"
  }
}
exit 0
```

The reason carries the shared target(s) and the rejected question **verbatim** —
a re-consent prompt that does not say what was refused cannot be answered.

### Tier: ask, not deny

The gate makes a claim about *consent state*, not about the command's
correctness, and consent state is exactly what the user is authoritative on.
Approving the ask **is** the fresh per-action approval the gate demands, so
there is no bypass marker and none is needed — an agent-attachable bypass would
let the same "adjacent utterance = approval" reading that caused the incident
re-enter one layer down (ETHOS → *No agent-attachable bypass for high-stakes
gates*).

### Over the byte bound: ask (issue #1231)

`scan_user_rejections` answers `None` when it did not reach the end of the
transcript within `REJECTION_SCAN_MAX_BYTES` (20 MiB) of new bytes this call —
the scan did not finish, which is not the same fact as "no rejection".
Condition 2 becomes **unanswerable**, and this gate resolves that to **ask**.

The scan is resumable: a cursor under `~/.praxis/cache/`
(`scan-rejected-mutation-reconsent-gate-root-<session_id>.json`, swept with
the cache TTL and spared for the live session) holds the byte offset of the
last complete record read, the rejections found so far and the recent
`tool_use` blocks they resolve against, so each call reads only the bytes
appended since the previous one. The bound is therefore a budget per call
rather than a ceiling on the session: a transcript past it asks for the few
destructive commands it takes to catch up — the cursor advances by one budget
each time — and then costs its delta. A payload without a `session_id` scans
without persistence, under the same budget.

Fail-closed here rather than open, for one reason: the blindness is not evenly
distributed. The bound is reached only by long sessions, and a long session is
where a standing refusal has had time to accumulate and be forgotten — so
failing open would silence the gate in exactly the sessions it was written for.
The reach stays narrow because condition 1 is evaluated first: a command with no
literal destructive identifier never reaches the transcript at all. And the tier
is ask, so a wrong ask costs one keypress.

A **missing or unreadable** transcript is not this case — it answers `[]` and the
gate stays silent. An absent file is no evidence that a session history exists;
a file past the bound is direct evidence that one does.

### Known limitations (accepted, not oversights)

1. **The dispatch surface is covered, but was never verified against the
   originating incident** (issue #1035). The incident's firing point was an
   agent/worker dispatch — the deletion scope lived in a worker prompt — and
   #1035 extended the matcher there. What could *not* be done is the
   verification #1035 asked for: **the incident's transcript does not exist.**
   All 1,022 transcripts under the local Claude Code projects directory were
   scanned with this hook's own extractors; 35 structurally rejected
   `AskUserQuestion` records were found (so the scanner is alive), and **0 of
   them name any closed-list identifier**, so no rejection→dispatch pair from
   the incident — or resembling it — is reachable. Issue #1007's closing comment
   had already recorded the same fact ("the only evidence is one anecdote,
   and there is no transcript of it" — original: "현재 근거는 일화 1건이고 그
   전사도 없다"). The suite therefore pins the behaviour on **synthetic fixtures built
   from the incident's reported shape**, both directions against the same
   transcript. That is mechanism reproduction, not verification against the
   real artifact, and it is stated here rather than implied.
2. **A refusal expressed only as an option label is invisible.** See *structural
   only* above.
3. **The identifier classes are a closed list** (`s3://`, `gs://`, SQL
   `DROP`/`TRUNCATE`/`DELETE FROM`). A rejected Kubernetes namespace, BigQuery
   dataset, or filesystem path is not matched. Adding a class is additive; each
   one needs a normalization rule of its own, and guessing normalization is how
   a literal-identifier gate turns into a fuzzy one.
4. **The scan is bounded** at 20 MB of new transcript per call and the 20
   most recent rejections (`_transcript.REJECTION_SCAN_MAX_BYTES` /
   `REJECTION_SCAN_MAX_RECORDS`), and a rejection resolves only against the
   32 most recent `tool_use` blocks (`REJECTION_RECENT_TOOL_USES`) — the
   distance between a call and its refusal is the number of parallel calls
   in that one turn, so an older one is unresolvable and reported with an
   empty `tool_name`. Beyond the byte budget the gate asks (see above);
   beyond the record bound it degrades to silence, never to a block.
   Measured cost on a real 1.9 MB / 659-event transcript: **0.015 s**,
   against the hook's 5 s budget.

### Compound cascade advisory (issue #229)

The ask path appends the shared `_hook_utils.compound_cascade_hint` suffix when
the parent Bash command is compound AND contains a state-changing step.
Single-command asks receive no suffix.

### Relationship to sibling hooks

| Hook | Scope | Overlap |
| --- | --- | --- |
| `side-effect-scan` | generic collateral-side-effect verbs | Complementary — that gate asks about the verb class; this one asks about a specific target the user already refused |
| `session-intent` | undeclared mutation pivot within a session | Complementary — intent declaration vs. standing refusal |
| `block-ask-end-option` | `AskUserQuestion` option shape | Upstream of the same event; this hook reads the *rejection* of such a question, not its authoring |

### Tests

```bash
bash tests/hooks/preflight-gate/test_rejected_mutation_reconsent_gate.sh
python3 -m pytest tests/test_transcript.py -q   # shared scanner contract
```

Covers 52 cases.

**Bash surface (25).** Seven ask paths (same prefix, trailing-slash and case
normalization, `gs://`, SQL DROP→DROP and DROP→TRUNCATE, compound-segment), an
ask-detail check (verbatim question + shared target in the reason), ten silent
controls (different prefix, sibling prefix, different bucket, no rejection,
non-`AskUserQuestion` source, two read-only commands, different table,
unqualified table, no identifier), a structural-marker control (a rejection
record lacking `is_error` must not count), the byte-bound quartet, the
cascade-hint present/absent pair, and infrastructure (non-Bash passthrough,
malformed JSON, unreadable transcript, `@fail_open` wrapping).

**Dispatch surface (20, issue #1035).** Five ask paths (`Task` prompt, `Agent`
tool-name variant, `description`-only, SQL DROP→TRUNCATE, `cmux`-nested prompt),
six silent controls, a subject-wording check, and eight launcher-normalization
cases: seven ask forms (`$( … )`, backtick, `env VAR=1`, a bare assignment, an
absolute path, `sudo`, `gemini -p`) plus the single-quoted-literal control that
must stay silent.

The controls are the half that carries the weight, and they run against the
**same transcript and the same rejection** as the ask paths — only the target
differs. A gate wired to ask on every dispatch would pass all five ask cases
and fail all six controls, which is the only way "it caught the incident" and
"it catches everything" can be told apart. The six: a dispatch on a different
prefix, one naming no identifier at all, one naming a different table, the same
prompt with no prior rejection, the same prompt with the rejection joined to a
non-`AskUserQuestion` tool, and a `cmux`-nested prompt on a different prefix.
A seventh pins the launcher boundary — `aws s3api head-object … s3://…` names
the rejected prefix in an ordinary argument and stays silent, because `argv[0]`
is not a launcher.
