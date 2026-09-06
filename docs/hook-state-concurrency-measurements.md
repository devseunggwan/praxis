# Session-state concurrency: the Q0 measurements

The measurement record behind the Q0 column of
[`DESIGN.md` → Session-state concurrency](../DESIGN.md#session-state-concurrency).
DESIGN.md keeps the criterion and the per-hook classification; this file keeps
how each verdict was obtained, so the criterion stays readable and the evidence
stays inspectable. Issues #951, #970, #1017, #1034.

## Method and results (issue #1034)


Issue #970 graded the other six against Q0 only far enough to see that it
moved no other row, and recorded that as follow-up. It was an author
assertion, so #1034 measured it: 100 concurrent pairs of the real impl per
row against one state file, each row paired with a negative control that
removes only the property its exemption rests on. The control is not optional. A 0 with no arm
that can fail it does not distinguish "exempt" from "the harness never reached
the state file" — the trap hit during #1017 verification, where a wrong state
path made both arms read 100/100.

`second-failure-advisory` and `pre-edit-md-escape-advisory` do share a staging
name, and the exemption claimed the lock covers the staging window. It does:
0/100 corrupt under the lock, against 4/100 for `pre-edit-md-escape-advisory`
with the lock neutered. (`second-failure-advisory`'s neutered arm loses an
increment rather than corrupting on this scheduling, which its own case
asserts.) `worktree-prune-snapshot-gate`, `retrospect-active-marker` and
`session-intent` stage through `tempfile.mkstemp`: 0/100 each, against 100/100
with `mkstemp` forced to one shared name. Every one of those pairs was
provably overlapped — both children reported through the post-read barrier —
so the 0s are measurements, not misses.

`postcompact-context` did not hold. (Historical since #1339: the hook now
runs on `SessionStart(compact)`, which fires once per compaction, and keeps
no state file at all — the paragraph below records what the #1034 measurement
found in the `UserPromptSubmit`-era write, not a live row.) Its exemption priced the *consequence*
(one uuid, so a corrupted read costs what a lost one does) and never checked
the *mechanism*: `write_state` truncated and wrote the final name, staging
through nothing at all, which makes the state file its own staging file — the
degenerate case of the shared `<path>.tmp` staging name Q0 describes in DESIGN.md. 5 of 300 unforced pairs
published `{"last_compact_uuid_emitted": "short-uuid"}uuuu…"}`, a short write
over a longer sibling's tail, which `read_state` answers with an empty dict.
Q0's remedy now applies in full: `state_lock` around the read-modify-write,
and `tempfile.mkstemp` staging under it. Re-measured after the fix, 0/300. The
staging name is what carries that — with it in place and the lock neutered,
200 provably overlapping pairs corrupted 0 times and split their surviving
uuid 103/97, the lost update Q3 already prices at one re-injection.
