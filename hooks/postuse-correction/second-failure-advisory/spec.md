# PostToolUseFailure Same-Failure-Pattern Advisory

Supported hosts: claude

`PostToolUseFailure` is raised by Claude Code only, so the single
registration carries `hosts: ["claude"]`. The `PostToolUse` registration this
hook also carried was removed once the parallel run ended, and the code that
read its payload was deleted after it — history in
[Registration history](#registration-history-issue-1337) below.

`hooks/second-failure-advisory` is an advisory hook registered on
`PostToolUseFailure`. When the same `tool_name + error_signature` pair fails
repeatedly within one session, it emits an advisory through
`hookSpecificOutput.additionalContext` on stdout from the second failure
onward, to cut the pattern of retrying an identical failure indefinitely
without analysing the cause.

## Why this exists

Some tool calls kept failing with the same error for the same cause, yet each
failure was treated as new: the user missed that an intervention was needed
and the session went straight into a retry loop. That pattern was split out
into its own issue.

`second-failure-advisory` runs on lightweight tracking only, so it never
blocks tool execution (always exit 0) and acts purely as an advisory that
announces the repeat.

## Covered surface

- Event: `PostToolUseFailure` (`claude` only, issue #1337). One manifest
  entry. `impl.py` reads the payload's `hook_event_name` and returns on
  anything else, an absent field included.
- Matcher: `all tools` — the `hooks/manifest.json` entry carries no
  `matcher` key. An explicit list would drop, at the matcher stage, every
  repeated failure of a tool whose name is not enumerated (MCP tools,
  `WebFetch`, `NotebookEdit`).

## PostToolUseFailure (issue #1337)

### Why

The hook was first registered on `PostToolUse`, whose payload cannot say
whether a Bash command failed. Quoting the #1096 finding that path rested on:

> Real Bash `tool_response` payloads carry no exit status and no error field
> — verified as `{stdout, stderr, interrupted, isImage, noOutputExpected}`
> against live session transcripts — so the only failure signals actually
> available for a Bash call are `interrupted` (a killed/timed-out run) and,
> if one is ever present, an explicit `isError`/`is_error`/`status ==
> "error"`/`error` marker.

Issue #1265 then found the failed calls arriving as strings rather than
dicts and opened that road, but the road was an allowlist over undocumented
harness text (`Error:` plus a space) that would fail silent the day the text
changed.

The harness ships an event for exactly this case. Per the Claude Code hooks
reference (verified 2026-09-06), `PostToolUseFailure` "runs when a tool that
started executing fails" and delivers, on top of the usual
`tool_name`/`tool_input`/`tool_use_id`, a top-level `error` string — "for
Bash, the first line is `Exit code N`, then interleaved output" — plus an
optional `is_interrupt` (true when the failure reached Claude Code as an
abort) and an optional `duration_ms`. It cannot block, it can return
`hookSpecificOutput.additionalContext` under `hookEventName:
"PostToolUseFailure"`, it matches on tool name like `PreToolUse`, and it
does **not** fire for permission denials or schema-validation rejections
(those never started executing). The event's arrival is the failure verdict,
so this path applies no allowlist to the text: every non-interrupted
`PostToolUseFailure` counts, MCP tools included. Where the string path had
to stop trusting an MCP tool's own `Error:` text (PR #1270), here the
harness has already ruled.

### Decision, in order

1. `hook_event_name != "PostToolUseFailure"` → not a failure report.
   Silent, no state written. An absent or malformed field lands here: a
   payload that does not name its event cannot be answered under one, since
   the harness matches the reply's `hookEventName` against what it delivered.
2. `is_interrupt: true` → **not a failure of the command**. The run was
   aborted before it could fail on its own; counting it would advise on the
   user's interruptions. Silent, no state written.
3. `error` not a string → unknown shape, fail-open, silent.
4. Otherwise `error` (stripped) is the failure text. For Bash that is the
   `Exit code N` line with the command's output under it; for any other tool
   it is whatever the harness reported. The text seeds the signature exactly
   as a string `tool_response` does, and a bare `Exit code N` with nothing
   under it takes the command digest (`_command_discriminator`) so two
   commands dying the same way stay on separate pairs.

### Why the `Error:` envelope is stripped

Signature material is the failure text with one leading `Error:` removed
(`_signature_material`). The event's `error` field does not carry that
envelope — for Bash it opens `Exit code 1` — but a tool's own message can,
and one failure written both ways must land on one pair key rather than hold
two counters at 1 and never advise. `_BARE_EXIT_CODE_RE` therefore matches
the unwrapped form (`^Exit code \d+$`).

### Dedupe — one call, one count

One tool call can reach the hook more than once. Each counted failure
appends its `tool_use_id` to `recent_tool_use_ids` in the state file
(bounded to the last 16, ordered), and an event whose id is already there
returns before the count moves. The check sits inside the state lock, so two
deliveries of one call cannot both read "unseen". The first counts and —
from the second occurrence — advises; the redelivery is silent, so the
model's context receives one advisory per failure, not two. A bounded
*window* rather than the single last id: parallel calls interleave (`A`,
`B`, `A` again), and a last-id-only check would count `A` twice. Payloads
without a `tool_use_id` skip the dedupe and count as before.

### Host filter

The event is documented for Claude Code only, so the manifest entry carries
`hosts: ["claude"]`, and Codex and Cursor register this hook not at all.
Rule 8 reads a hook's hosts from its first registration, which is now that
same entry — hence `Supported hosts: claude` in the header.

### Emitted event name

The advisory echoes the incoming event as `hookEventName`. The harness
accepts a hook reply only under the event it delivered, so a `PostToolUse`
name on a `PostToolUseFailure` reply would be discarded.

### Registration history (issue #1337)

The hook was registered on `PostToolUse` first, and gained the
`PostToolUseFailure` entry alongside it so the failure event could run in
parallel for one release. The `PostToolUse` entry was removed at the end of
that run. What the removal rests on, and what it does not:

- **`PostToolUseFailure` is the event for a failed call.** The hooks
  reference's lifecycle table gives `PostToolUse` as "after a tool call
  succeeds" and `PostToolUseFailure` as "after a tool call fails". The
  `PostToolUse` path was built around a payload that, per #1096, cannot say
  whether a Bash command failed — the gap #1337 opened this hook's second
  registration to close.
- **The dedupe made the parallel run cost-free and its end lossless.** Both
  events counted one call once, keyed on `tool_use_id`, so removing one entry
  cannot change the count of any failure the remaining event sees.
- **Permission denials were never this hook's lane.** `User rejected tool use`
  reaches praxis through the *transcript* — `toolUseResult` on a user entry,
  which `rejected-mutation-reconsent-gate` reads — not through a `PostToolUse`
  payload. Removing the entry does not narrow denial handling.
- **Not measured.** No ledger from a host that raises `PostToolUseFailure` was
  available when the entry was removed, so the parallel run's stated purpose —
  comparing real delivery of the two events — was not carried out. The removal
  rests on the reference and on the dedupe argument above, not on observation.
  Live verification is tracked separately.

The `tool_response` detection path was deleted after the registration, in
its own change (issue #1366 item 3): the classifier, the string allowlist and
the harness-noise filter went with it, along with the 16 test cases that
exercised them. What survived is the signature path — normalisation, the
command discriminator, the bare exit-code rule, Reference extraction — which
every `PostToolUseFailure` still walks, and whose cases moved onto the event.
The sections those deletions emptied are gone from this spec; what they
recorded about #1042, #1096 and #1265 stays above, in this history.

## Signature derivation

The `error` text is the signature material — it is the only evidence of
failure the payload carries, and that is also what keeps two different
failures from merging into one pair (the shape of issue #1042 defect 2).

A single text, however, may carry no discriminating information at all:
`Exit code N` with no output under it (6 of the 388 observed on the string
surface this rule was measured on) is byte-identical whichever command
died. For that shape only (`_BARE_EXIT_CODE_RE`), the
`command` from `tool_input` is folded into the key as a **separate digest**
(`_command_discriminator`). It is a field of the same payload being judged,
so the signature does not depend on external state. Result: two different
commands no longer merge into one pair, while the same command failing the
same way twice still produces an advisory (cases 19g–19j). Failure text that
carries real content is already discriminated and is left alone.

**Why the digest is separate**: appending the command to the signature
*text* would send it through `_normalize_signature`, where `cat /tmp/a` and
`cat /tmp/b` both become `cat <path>` — the discriminator is absorbed by
normalisation and an unrelated second failure produces a false advisory.
Normalisation exists to merge *genuinely equivalent* errors, so it is not
weakened; only the command is hashed outside it.

Digest rules:

- **Only leading and trailing whitespace is trimmed** (case 19n). Internal
  whitespace is **not** collapsed — in a shell, whitespace is syntax, not
  decoration: a newline separates two commands (`false\nfalse` ≠
  `false false`) and a run of spaces inside quotes is part of the argument
  (`test 'a  b' = x` ≠ `test 'a b' = x`). Collapsing it merged different
  commands into one hash and reproduced exactly the collision the digest was
  meant to prevent (PR #1270, case 19s). The cost runs the other way — the
  same command retyped with different spacing now gets its own key and its
  second occurrence is silent. That is a **missed advisory, not a false one**,
  and that match was not worth a discriminator that cannot discriminate.
- Case is **not** folded — `cat A` and `cat a` are different files.
- The command is truncated at `_MAX_SIGNATURE_LEN` (4096 characters) before
  hashing; two commands that differ only past 4096 characters share a key
  (case 19o).
- When `command` is absent or whitespace-only, no digest is appended and the
  key is byte-identical to the previous scheme (cases 19l/19m/19r).

The final key material is `f"{tool_name}\0{normalized}"`, with
`\0{command_digest}` appended only when the condition above holds.

### Cardinality

Adding the digest to the key **increases** the number of keys in the state
file — previously every bare-exit-code failure shared one key; now each
distinct command has its own. Measured: 1,000 distinct commands produce
1,000 keys and a 61,045-byte state file (61 bytes per key). There is no cap
and no eviction; per-session files are cleaned by the shared 7-day TTL in
`hooks/_lib/_paths.py`, so accumulation is bounded to one session.

An `error` that is empty after stripping normalises to `<empty>` and keys on
that, so a failure the harness reported with no text still counts.

To estimate "the same failure", the following tokens are normalised:

- path tokens → `<path>`
  - Unix-like `/...`
  - Windows `C:\...`
- UUIDs → `<uuid>`
- hex strings of 16 or more digits → `<hash>`
- timestamps → `<ts>`
- `*id*` patterns → `<id>`

After normalisation the text is lower-cased and length-capped. The final
signature is `sha1(f"{tool_name}\\0{normalized_signature}")`.

## Counting semantics — session-cumulative, not consecutive

The counter is a **session-cumulative** value per `(tool_name, signature)`
pair. A success or a different failure in between does not reset it, and
the advisory fires from the pair's second failure onward. The behaviour
issue #944 specified was "count `(tool_name, error_signature)` per session
and advise on the second occurrence".

Because the trigger is "the N-th identical failure in this session", not "N
consecutive failures", neither the message nor this document uses the word
*consecutive*.

## Third and later occurrences keep advising (issue #1012)

Previously the hook fired only on the `prior_count == 1` boundary, so a
session that kept repeating the same failure received the advisory
**exactly once** and then silence. To a model reading the transcript that
silence is indistinguishable from "the loop was noticed and accepted". The
measured pattern that justified this hook in the first place was a long
repeat (a poll-loop family recurring 6 times in one session, 5 of them after
the first corrective signal was already in the transcript), so the boundary
cut off exactly the stretch where the signal was needed most.

It now fires on every occurrence from the second onward and carries the
occurrence number (`{n}회째`) in the message: the signal accumulates in the
worst stretch instead of disappearing from it. The silent first occurrence
is unchanged — one failure is not yet a loop.

## Output behaviour

An advisory is emitted when:

- the failure is the second or later for the same `session_id` and
  `(tool_name, signature)` pair
- i.e. the prior count is 1 or more (`occurrence = prior_count + 1 >= 2`)

The advisory is emitted only after the state write (atomic replace via
`os.replace`) succeeds. If the write fails the counter is not persisted and
the same advisory could fire again on the next failure, so a write failure
is handled as silence.

Only the replace (rename) is atomic; the read → increment → write → emit
sequence is not serialised across processes. When tool calls in one session
finish in parallel, the hook runs in a separate process per call, so two
processes can both read a stored count of 1, both write 2, and **both emit an
advisory with the same occurrence number**. Conversely, simultaneous failures
of different pairs can overwrite one another's increment and delay an
advisory by one beat. "The occurrence number equals the real failure count"
is a contract under sequential execution and does not hold in that window
(before issue #1012 the "silent from the third occurrence" contract broke in
the same window).

For the lock that now covers this, see *Concurrency* below.

Output is one line on `stdout` as `hookSpecificOutput.additionalContext`
(the post-tool corrective-emission convention in DESIGN.md, same shape as
`builtin-task-postuse`). The `stderr` of an exit-0 post-tool hook goes only
to debug logs and never reaches the model, so emitting there would defeat the
retry-loop correction this hook exists for.

```json
{"continue": true, "hookSpecificOutput": {"hookEventName": "PostToolUseFailure", "additionalContext": "[second-failure-advisory] Failure #<n> of the same error pattern in this session — … (동일한 오류 패턴으로 세션 내 <n>회째 실패가 감지되었습니다. …) … signature=<sig_prefix> Reference: <path?> — …"}}
```

`<n>` is the session-cumulative occurrence (2, 3, 4, …) for that
`(tool_name, signature)` pair. `hookEventName` echoes the incoming event and
is always `PostToolUseFailure`, since that is the only event `main()` accepts
— a reply naming any other is discarded by the harness on arrival.

`reference` is extracted first from a `Reference:` label, a `hooks/...` path,
or a `*spec.md` path in the failure text, and otherwise from
`tool_input.file_path/path/target`. When a path is found, the advisory also
instructs the model to read that file and restate the blocking predicate in
one line before retrying; without a path, only the restatement is required.

The following cases are silent (fail-open) and exit 0:

- malformed stdin / non-JSON input
- no `session_id`
- no `tool_name`
- a successful response
- the first failure
- a state write failure (state-file I/O error)
- a `PostToolUseFailure` with `is_interrupt: true`, or with a non-string
  `error` (issue #1337)
- an event whose `tool_use_id` this session already counted (issue #1337)

## State

Base file:

`<cache>/second-failure-advisory-<session_id>.json`

`PRAXIS_SECOND_FAILURE_ADVISORY_FILE`, when set, takes precedence (for tests
and isolation).

Example format:

```json
{
  "schema_version": 1,
  "failures": {
    "Bash|deadbeef": 2
  },
  "recent_tool_use_ids": ["toolu_01A", "toolu_01B"]
}
```

`recent_tool_use_ids` (issue #1337) holds the ids of the last 16 counted
failures, oldest first; it is what keeps a call redelivered under the same id
from counting twice. It was added while two registrations were live and one
call could arrive as either event; with one left, the same window covers a
repeat of the one event. Absent in state written before #1337, and read as
empty.

## Concurrency (issue #951)

The count update (read → modify → `os.replace`) is serialised with
`_lib/_state_lock.state_lock`. Because the advisory fires on the
`prior_count == 1` boundary, two processes sharing a `session_id` that read
the same count would both cross it and fire twice (the unverified item in
#950), and a lost increment is never recovered by any later event. The
criterion and the per-hook classification are in
[`DESIGN.md → Session-state concurrency`](../../../DESIGN.md#session-state-concurrency).

A failed lock acquisition degrades to the pre-lock behaviour; it never turns
the hook into a block (the `@fail_open` contract).

## Privacy

- The raw error text is never stored; only the hash of the normalised
  signature is kept, to limit leakage of sensitive log content.

## Tests

Run:

```bash
bash tests/hooks/postuse-correction/test_second_failure_advisory.sh
python3 -m pytest tests/test_hook_state_concurrency.py
```

Required coverage:

- 1 failure: no advisory (two-way control — catches a regression to
  "always fire")
- 2 failures (same signature): advisory emitted
- 3rd, 4th, 5th occurrence (same pair): advisory keeps firing, message
  carries the occurrence number (issue #1012)
- same signature but different `tool_name`: no advisory
- 2 failures differing only in path/hash/timestamp: advisory emitted
- a success or a different failure in between: the pair's second occurrence
  still advises
- state write failure: silent
- repeated success responses with only `stdout`/`output`: silent
- a `Reference:` path in the failure text appears in the advisory and in the
  restatement instruction
- non-failure / malformed input: fail-open
- output-less `Exit code N` from two different commands: silent
  (signature-collision guard); the same command failing the same way twice:
  still advises (issue #1265, cases 19g/19h — the latter is the former's
  control)
- two commands differing only in a path (`cat /tmp/a` / `cat /tmp/b`): silent
  — normalisation must not absorb the discriminator; control is the same
  command twice → advisory (cases 19i/19j)
- key behaviour for absent command, whitespace-only command, the 4096
  boundary, unicode, and non-Bash tools (cases 19k–19r), plus a non-bare
  failure still merging under normalisation (case 19q — the control showing
  the fix was not bought by weakening the normaliser)
- two commands differing in shell-significant internal whitespace (newline,
  tab, a run of spaces inside quotes, NBSP): different keys → silent; the
  same command twice still advises (case 19s, both directions)
- the same failure text twice → advisory; two different failure texts →
  silent (signature separation) (case 19; every fixture is captured verbatim
  from a real transcript, with the `Error:` envelope the string surface
  carried and the event's `error` field does not)
- two processes running concurrently: without the lock an increment is lost;
  under the lock the count goes 1→2→3 and two advisories are emitted (2nd
  and 3rd) (`tests/test_hook_state_concurrency.py`)
- `PostToolUseFailure` (issue #1337, case 20): the same Bash `Exit code 1`
  plus `npm ERR!` error twice → advisory on the second, with
  `hookEventName: "PostToolUseFailure"` (20a); `is_interrupt: true` → silent
  and no state file (20b); one `tool_use_id` delivered twice → counted once
  (20c); a non-Bash MCP tool's error string twice → advisory (20d); a
  `PostToolUse` payload → silent, no state (20e — the control on the event
  guard, and the only case left that sends the retired event); non-string
  `error` → silent (20g); a bare `Exit code 1` from two different commands →
  silent, same command → advisory (20h, both directions)
