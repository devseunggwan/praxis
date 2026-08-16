# PreToolUse Rejected-Mutation Re-Consent Gate

Supported hosts: all

Reference: [Autonomy vs Convention — ETHOS.md](../../../ETHOS.md#autonomy-vs-convention)

`hooks/preflight-gate/rejected-mutation-reconsent-gate/impl.py` asks for fresh
per-action approval when a Bash command targets something the user already
refused in this session (issue #1007).

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
| 1 | The pending Bash command carries a **destructive marker** AND a **literal identifier** (`s3://…`, `gs://…`, or a table named after `DROP` / `TRUNCATE` / `DELETE FROM`) | `tool_input.command` |
| 2 | An earlier **`AskUserQuestion` was structurally rejected** this session | `transcript_path` via `_transcript.scan_user_rejections` |
| 3 | The rejected question names the **same normalized identifier** | set intersection |

All three → `permissionDecision: "ask"`, quoting the rejected question verbatim.

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

### Known limitations (accepted, not oversights)

1. **Bash-only would NOT have intercepted the originating incident.** The
   incident's firing point was an **agent/worker dispatch**, not a Bash command
   — the deletion scope lived in a worker prompt, which this matcher never sees.
   The maintainer accepted that: the Bash surface is the one where the target is
   a literal identifier the gate can compare without judgement, and the dispatch
   surface is deferred to a separate issue. This hook therefore closes the
   *re-issue by shell command* path, not the path the observed failure took.
2. **A refusal expressed only as an option label is invisible.** See *structural
   only* above.
3. **The identifier classes are a closed list** (`s3://`, `gs://`, SQL
   `DROP`/`TRUNCATE`/`DELETE FROM`). A rejected Kubernetes namespace, BigQuery
   dataset, or filesystem path is not matched. Adding a class is additive; each
   one needs a normalization rule of its own, and guessing normalization is how
   a literal-identifier gate turns into a fuzzy one.
4. **The scan is bounded** at 20 MB of transcript and the 20 most recent
   rejections (`_transcript.REJECTION_SCAN_MAX_BYTES` /
   `REJECTION_SCAN_MAX_RECORDS`). Beyond either bound the gate degrades to
   silence, never to a block. Measured cost on a real 1.9 MB / 659-event
   transcript: **0.015 s**, against the hook's 5 s budget.

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

Covers 25 cases: seven ask paths (same prefix, trailing-slash and case
normalization, `gs://`, SQL DROP→DROP and DROP→TRUNCATE, compound-segment), an
ask-detail check (verbatim question + shared target in the reason), ten silent
controls (different prefix, sibling prefix, different bucket, no rejection,
non-`AskUserQuestion` source, two read-only commands, different table,
unqualified table, no identifier), a structural-marker control (a rejection
record lacking `is_error` must not count), the cascade-hint present/absent pair,
and infrastructure (non-Bash passthrough, malformed JSON, unreadable
transcript, `@fail_open` wrapping).
