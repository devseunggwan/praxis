# PostToolUseFailure Same-Failure-Pattern Advisory

Supported hosts: claude

`PostToolUseFailure` is raised by Claude Code only, so the single registration
carries `hosts: ["claude"]`. The `PostToolUse` registration this hook also
carried was removed once the parallel run ended (issue #1337) — history in
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
  entry. `impl.py` still branches on the payload's `hook_event_name`, so a
  `PostToolUse` payload delivered by anything else is still read correctly.
- Matcher: `all tools` — the `hooks/manifest.json` entry carries no
  `matcher` key. An explicit list would drop, at the matcher stage, every
  repeated failure of a tool whose name is not enumerated (MCP tools,
  `WebFetch`, `NotebookEdit`).

## PostToolUseFailure (issue #1337)

### Why

The `PostToolUse` sections below describe a payload that cannot say whether
a Bash command failed. Quoting the #1096 finding they rest on:

> Real Bash `tool_response` payloads carry no exit status and no error field
> — verified as `{stdout, stderr, interrupted, isImage, noOutputExpected}`
> against live session transcripts — so the only failure signals actually
> available for a Bash call are `interrupted` (a killed/timed-out run) and,
> if one is ever present, an explicit `isError`/`is_error`/`status ==
> "error"`/`error` marker.

Issue #1265 then found the failed calls arriving as strings rather than
dicts and opened that road, but the road is an allowlist over undocumented
harness text (`Error:` plus a space), which its own section says will fail
silent the day the text changes.

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

1. `hook_event_name != "PostToolUseFailure"` → the `PostToolUse` path below,
   unchanged. An absent field is the pre-#1337 shape and takes that path.
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

### Why the two events share one pair key

Signature material is the failure text with one leading `Error:` removed
(`_signature_material`). The `PostToolUse` string carries the harness
envelope — `Error: Exit code 1\n(eval):1: == not found` — and the
`PostToolUseFailure` `error` field carries the same lines without it. They
describe one failure, and a session whose failures reached this hook by
alternating events would otherwise hold two counters at 1 and never advise.
The prefix is dropped from the *material* only; the `PostToolUse` failure
decision still reads it. `_BARE_EXIT_CODE_RE` therefore matches the
unwrapped form (`^Exit code \d+$`) and covers both shapes with one pattern.

### Dedupe — one call, two events, one count

While both registrations are live, one tool call can reach the hook twice.
Each counted failure appends its `tool_use_id` to `recent_tool_use_ids` in
the state file (bounded to the last 16, ordered), and an event whose id is
already there returns before the count moves. The check sits inside the
state lock, so two events for one call cannot both read "unseen". The first
event to arrive counts and — from the second occurrence — advises; the
second is silent, so the model's context receives one advisory per failure,
not two. A bounded *window* rather than the single last id: parallel calls
interleave (`A-post, B-post, A-fail`), and a last-id-only check would count
`A` twice. Payloads without a `tool_use_id` (synthetic, pre-#1337 tests)
skip the dedupe and count as before.

### Host filter

The event is documented for Claude Code only, so the manifest entry carries
`hosts: ["claude"]`; Codex and Cursor keep the `PostToolUse` registration
alone. Rule 8 reads a hook's hosts from its **first** registration, which is
the all-hosts `PostToolUse` one — hence `Supported hosts: all` in the header
with the per-event note beneath it.

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
  `PostToolUse` sections below were written around a payload that, per #1096,
  cannot say whether a Bash command failed — the gap #1337 opened this hook's
  second registration to close.
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

`impl.py`'s `tool_response` detection path is now unreachable through any
registration. It is retained, not yet deleted: it is the larger half of this
hook and its own tests, and removing it is a separate change.

## Deciding what counts as a failure (`PostToolUse`)

Everything in this section and the ones that follow — string allowlist,
harness-noise filter, dict markers — applies to the `PostToolUse` path. The
`PostToolUseFailure` path above needs none of it: the event is the verdict.

`tool_response` arrives in two shapes, and **failures arrive as strings**
(issue #1265).

### String payload (issue #1265)

A failed tool call is delivered as a plain string, not a dict. A full survey
of 10,467 Bash `toolUseResult` records across 120 real session transcripts
found 388 strings, **every one** with `tool_result.is_error == True`, and
zero cases of a successful Bash call arriving as a string.

The decision is an allowlist (`_string_failure_text`).

- Starts with `Error:` plus a space → failure (`Error: Exit code N`,
  `Error: Blocked: …`, `Error: Permission …`,
  `Error: PreToolUse:Bash hook error: …`)
- Exactly `User rejected tool use` → failure (no prefix, so matched by name)
- `Error: result (…) exceeds maximum allowed tokens …` is **not** a failure.
  It is the notice of a *successful* call whose result was spilled to a file
  because it was large, and carries `is_error == False` (27 observed, all MCP
  tools). Excluded by name.
- Any other string (the empty string included) is treated as success — the
  list is an allowlist, so a shape never observed as a failure cannot start
  firing on its own.

#### MCP tools: only harness-written strings count as failures (PR #1270)

The hook payload carries **no** failure flag. `is_error` lives on the
transcript's `tool_result` block and never reaches `tool_response`, so the
decision reads shape, not text. Widening the survey to all 14,652
`toolUseResult` records, **MCP is the only tool class** whose *successful*
results arrive as bare strings.

| Class | is_error=False | is_error=True |
| --- | --- | --- |
| Bash | dict 11,792 / str 0 | str 446 |
| other built-in tools | dict 1,135 / str 0 | str 37 |
| MCP | list 1,127 / **str 25** (all oversized-output notices) | str 90 |

On the MCP channel, then, a leading `"Error: "` may be **the tool's own
text** rather than the harness's failure envelope. A tool that succeeded
while returning `Error: no rows found` accumulated state and produced a false
advisory on the second call (case 19t-a). For MCP tools, therefore, only
strings the harness itself wrote count as failures:

- the `Error: PreToolUse:` / `Error: PostToolUse:` hook-error envelope
  (case 19t-b)
- the fixed sentence `User rejected tool use` (case 19t-c)

Bash and the other built-in tools have zero cases of success arriving as a
string, so their `"Error: "`-prefix decision is unchanged (case 19r).

**Cost**: MCP failures that carry the tool's own error text
(`Error: Error: query: …`, `Error: The operation timed out.`, and so on —
about 50 of the 573 observed string failures) no longer produce an advisory.
Narrowing the scope was chosen over growing an exclusion list one phrase at
a time: a phrase list keeps trusting tool text, so the same defect would
recur under a different phrase.

This decision is made **before, and independently of,** the
`tool_name == "Bash"` gate below. That gate exists because a *dict*
payload's `stderr` cannot tell an exit-0 success from a failure (#1042 and
#1096 were both dict + non-empty `stderr` on an exit-0 success), and a string
payload has no `stderr` field to be ambiguous about. With both paths blocked,
the result was silence: **135,030 fires, `decision: pass` 100%**.

### This decision depends on the harness output format — re-measure (issue #1265)

The `Error:`-plus-space prefix and `User rejected tool use` are **not a
documented contract; they are the strings Claude Code actually emits**. If
they change, the whole allowlist misses and the hook returns to
**never firing** — the exact failure this issue set out to fix — and because
the allowlist breaks in the safe direction (no fire), it looks identical the
second time. The ledger shows only rising fire counts with zero advisories.

So **if any string-payload test breaks, or fire counts rise with zero
advisories, re-measure the format first**. The survey below is the one that
produced the numbers above; its first column is tool, `is_error`, first
line.

```bash
python3 - <<'PY'
import collections, glob, json, os
seen, files = set(), sorted(glob.glob(os.path.expanduser("~/.claude*/projects/*/*.jsonl")), key=os.path.getmtime, reverse=True)[:120]
names, shape = {}, collections.Counter()
for f in files:
    for ln in open(f, errors="replace"):
        try: o = json.loads(ln)
        except Exception: continue
        for b in (o.get("message") or {}).get("content") or []:
            if not isinstance(b, dict): continue
            if b.get("type") == "tool_use": names[b["id"]] = b.get("name")
            if b.get("type") == "tool_result" and (o.get("uuid"), b.get("tool_use_id")) not in seen:
                seen.add((o.get("uuid"), b.get("tool_use_id")))
                t = o.get("toolUseResult")
                if isinstance(t, str):
                    shape[(names.get(b.get("tool_use_id")), bool(b.get("is_error")), t.split("\n")[0][:34])] += 1
for (tool, err, head), n in shape.most_common(15):
    print(f"{n:5d}  is_error={str(err):5s} {tool}  {head!r}")
PY
```

Check: (1) do `is_error=True` strings still start with `Error:` plus a
space; (2) have new `is_error=False` strings appeared beyond the
oversized-output notice; (3) if there is a new failure phrase, update
`_STRING_FAILURE_PREFIX` / `_STRING_REJECTION_TEXT` /
`_STRING_OVERSIZED_OUTPUT_RE` and add a fixture to case 19.

### Dict payload

- `isError is True` → failure
- `interrupted is True` → failure
- `exit` present and not the integer 0 → failure
- none of the above, and `error`/`stderr` holds non-empty text → failure
  (`stderr` is judged after the harness-noise filter below)
- a response with only `output`/`stdout` → success

## Harness-noise filter (issue #1042)

The `exit` key this hook assumed is absent from real Bash `tool_response`
payloads. Verified against real session transcripts (`toolUseResult` of
`Bash` tool uses in `~/.claude/projects/.../*.jsonl`), the actual shape is
`{stdout, stderr, interrupted, isImage, noOutputExpected}` — no `exit`, no
`isError`. Every Bash call therefore falls straight through to the
`error`/`stderr` fallback, and this harness resets the shell cwd between
calls while appending `"\nShell cwd was reset to <cwd>"` to `stderr` on every
call, success or failure. That one line is not evidence of failure, but the
fallback could not tell.

- An exit-0 command whose `stderr` held only that line was counted as a
  failure (68 fires in one real session; the last 5 in a row were all exit-0
  commands).
- After normalisation the sentence is the same string regardless of the
  command, so unrelated calls converged on one `(tool_name, signature)` pair
  and one fixed signature (`ede370078f51`) — confirmed as the identical hash
  in 6 independent real-session state files.

`_strip_harness_noise` removes that line (and only that line) from `stderr`
before both the failure decision and the signature computation. Real content
on other lines of the same `stderr` is preserved.

## Signature derivation

For a string payload the string itself is the signature material — it is the
only evidence of failure, and this property is also what keeps two different
string failures from merging into one pair (the shape of issue #1042
defect 2).

A single string, however, may carry no discriminating information at all:
`Error: Exit code N` with no output (6 of the 388 observed) is byte-identical
whichever command died. For that shape only (`_BARE_EXIT_CODE_RE`), the
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

A dict payload extracts its failure-text candidate in this order:

1. `error`
2. `stderr` (after the harness-noise filter)
3. `output`
4. `stdout`

When extraction fails, the empty string is used and the failure key is
adjusted. If `stderr` held only harness noise it becomes empty after the
filter and the search falls through to `output`/`stdout`, so a failure whose
real discriminating information is only in `stdout` (e.g. `interrupted:true`
with noise-only `stderr`) still gets a per-command signature.

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
finish in parallel, PostToolUse runs in a separate process per call, so two
processes can both read a stored count of 1, both write 2, and **both emit an
advisory with the same occurrence number**. Conversely, simultaneous failures
of different pairs can overwrite one another's increment and delay an
advisory by one beat. "The occurrence number equals the real failure count"
is a contract under sequential execution and does not hold in that window
(before issue #1012 the "silent from the third occurrence" contract broke in
the same window).

For the lock that now covers this, see *Concurrency* below.

Output is one line on `stdout` as `hookSpecificOutput.additionalContext`
(the PostToolUse corrective-emission convention in DESIGN.md, same shape as
`builtin-task-postuse`). The `stderr` of an exit-0 PostToolUse hook goes
only to debug logs and never reaches the model, so emitting there would
defeat the retry-loop correction this hook exists for.

```json
{"continue": true, "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "[second-failure-advisory] Failure #<n> of the same error pattern in this session — … (동일한 오류 패턴으로 세션 내 <n>회째 실패가 감지되었습니다. …) … signature=<sig_prefix> Reference: <path?> — …"}}
```

`<n>` is the session-cumulative occurrence (2, 3, 4, …) for that
`(tool_name, signature)` pair. `hookEventName` is `PostToolUseFailure` when
the advisory answers that event (issue #1337).

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
failures, oldest first; it is what keeps a call that reaches both events
from counting twice. Absent in state written before #1337, and read as
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
- an exit-0 Bash call whose `stderr` is only the harness cwd-reset note:
  silent across 5 repeats and no state file is created (issue #1042)
- a genuine identical failure repeated with the same harness noise mixed into
  `stderr`: still advises from the second occurrence (positive control —
  the defect-1 fix did not neuter the hook)
- two different failures with noise-only `stderr` and discriminating
  information only in `stdout` get different signatures and separate
  counters (issue #1042 defect 2)
- output-less `Error: Exit code N` from two different commands: silent
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
- MCP tools: a *successful* text starting with `"Error: "` is silent; the
  hook-error envelope and the rejection sentence still advise; the
  oversized-output notice is silent (case 19t, both directions)
- string payload: the same failure twice → advisory; two different string
  failures → silent (signature separation); repeated `User rejected tool use`
  → advisory; repeated oversized-output notice (`is_error:false`) → silent and
  no state file; whitespace-only string → silent (issue #1265, case 19; every
  fixture is captured verbatim from a real transcript)
- two processes running concurrently: without the lock an increment is lost;
  under the lock the count goes 1→2→3 and two advisories are emitted (2nd
  and 3rd) (`tests/test_hook_state_concurrency.py`)
- `PostToolUseFailure` (issue #1337, case 20): the same Bash `Exit code 1`
  plus `npm ERR!` error twice → advisory on the second, with
  `hookEventName: "PostToolUseFailure"` (20a); `is_interrupt: true` → silent
  and no state file (20b); one `tool_use_id` arriving via `PostToolUse` then
  `PostToolUseFailure` → counted once, and the reverse order too (20c); a
  non-Bash MCP tool's error string twice → advisory (20d); a `PostToolUse`
  success payload with the field present → silent (20e, negative control);
  a `PostToolUse` string failure and a `PostToolUseFailure` for the same
  failure text, different ids → one pair, advisory on the second (20f);
  non-string `error` → silent (20g); a bare `Exit code 1` from two different
  commands → silent, same command → advisory (20h, both directions)
