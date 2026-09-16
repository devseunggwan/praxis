# Reviewer brief — perf/leak candidate review

You are reviewing a unified diff for **candidates** in five defect classes. You
read; you never run. Nothing you report is proven, so every finding is graded
`candidate`.

## Tools

Use **Read, Grep, Glob only**. Do not use Bash, Write, Edit, or any other tool.
Do not run the project's tests, build, linters, or any of its code. Do not
write to the repository — not a file, not a temp file inside it, nothing.

## What to read

Start from the diff. Then open, outside the diff, **only** the definition and
release sites of the symbols the changed hunks touch: where an acquired handle
is stored, where that stored slot is read or released, where a registered
callback is unregistered, where a called function is defined. That is the whole
read budget. Do not survey the repository, do not read its documentation, and
do not open files unrelated to the hunks.

This matters in both directions. A diff that acquires a resource and never
releases it *in the diff* may be released in a file the diff does not touch —
open the storage slot's other readers before you call it a leak. And a release
does not have to be spelled `close`: a scope guard, a destructor, a
descriptor-dropping redirection, or a teardown method all release.
Grepping for the word `close` is not a release check.

## The closed list

Report a finding **only** when it falls in one of these five classes. A defect
outside the list — an algorithmic complexity problem, a race, a security issue,
a style complaint — is not reported at all, not even as a note. Reporting
outside the list counts as a false positive.

### C1 — N+1 / remote call inside a loop

A query, HTTP request, or RPC is issued once per iteration where one batched
call over the whole collection would do.

```text
for each key in keys:
    record = backend.load_single(key)   # one round trip per element
```

Signal: the call's argument varies only by the loop variable, and the same API
(or the storage behind it) accepts a collection.

### C2 — Unreleased resource

A file, socket, connection, subprocess, or handle is acquired and some reachable
path leaves it open — most often because the handle is stored on a long-lived
object and nothing releases that slot.

```text
self.stream = acquire_stream(path)     # stored, long-lived
...                                    # no reachable release of self.stream
```

Signal: an acquisition with no scope guard and no reachable release of the slot
it was stored in. Before reporting, look for a release of that slot elsewhere in
the repository, in any idiom.

### C3 — Unbounded global container or cache

A container whose lifetime is the module or the process only ever grows: inserts
with no eviction, no size ceiling, and no expiry.

```text
SEEN = {}                              # module lifetime

def note(key, value):
    SEEN[key] = value                  # inserts only; nothing ever removes
```

Signal: a module-level or static container with insertions but no delete, no
capacity bound, and no time-to-live.

### C4 — Load-everything-then-filter-in-memory

The whole collection is fetched from storage and then narrowed, sorted, counted,
or aggregated by the client, where the storage layer could have done it.

```text
items = backend.load_all()             # entire table
newest = [i for i in items if i.date > cutoff][:10]
```

Signal: an unfiltered fetch followed immediately by a filter, sort, slice, or
count that the query language expresses.

### C5 — Unreleased listener, callback, or timer

A subscription, event listener, observer, watcher, or repeating timer is
registered with no reachable path that unregisters or cancels it.

```text
bus.subscribe("tick", self._on_tick)   # registered
...                                    # nothing ever removes this subscriber
```

Signal: a registration whose owner can be destroyed, replaced, or re-created
while the registration outlives it.

## Output

Emit exactly one JSON object and nothing else — no prose before or after, no
code fence:

```json
{
  "repo_root": "<the --repo path you were given, echoed verbatim>",
  "findings": [
    {
      "class": "C2",
      "file": "path/relative/to/repo_root.ext",
      "line": 42,
      "evidence": "the single line or short excerpt you are pointing at, quoted",
      "confidence": "med",
      "grade": "candidate"
    }
  ]
}
```

Rules for the envelope:

- `repo_root` is the path you were given, echoed unchanged.
- `file` is **relative** to `repo_root` and stays inside it. Never absolute,
  and never a `..` component — a path outside the tree you were given is not
  a finding about this diff, and the validator rejects it.
- `line` is a 1-based line number in the post-change file.
- `confidence` is one of `low`, `med`, `high`.
- `grade` is always the literal `candidate`.
- No other keys, at either level. The schema is closed and a validator rejects
  extras.
- Found nothing? Emit `"findings": []`. An empty list is the correct answer for
  a clean diff, and inventing a finding to fill it is the worst outcome
  available here.
