# PreToolUse Foreground Poll-Loop Guard

Supported hosts: all

`hooks/preflight-gate/foreground-poll-loop-guard/impl.py` intercepts every Bash
tool call and blocks foreground poll-loops that will hit the Bash default
120000ms (2-min) timeout and die with SIGTERM (Exit 143) mid-poll.

## Why this exists

A `for/while/until ... sleep N ... done` loop run in the FOREGROUND runs until it
hits the Bash foreground ceiling and is killed, even though the underlying async
op (a cloud deploy, `gh pr checks`, a CI wait) usually succeeds. The runtime block
only fires on a *leading* `sleep N && cmd`; a loop-internal `sleep` slips through.

This is the gen-3 escalation of the `native_async_over_sleep_chain` feedback
family: the pattern recurred 6x in one session (14x Exit 143), 5 of them AFTER the
first Exit 143 was already visible in-transcript — proving the advisory tier
(a memory note + the Bash tool-schema prose) is structurally insufficient. The
guard keys on the LOOP SHAPE (not the polled command — an allowlist is always
incomplete) and REDIRECTS to the native async-wait primitives rather than merely
rejecting.

## What is blocked

Exit 2 when a FOREGROUND Bash call (`run_in_background` is not `true`) contains:

| Command shape | Action |
| --- | --- |
| `for i in $(seq 1 40); do ...; sleep 3; done` (40×3 = 120s ≥ 100) | **BLOCKED** (exit 2) |
| `for i in $(seq 1 20); do gh pr checks; sleep 20; done` | **BLOCKED** |
| `for i in {1..30}; do ...; sleep 5; done` (150s) | **BLOCKED** |
| `while true; do gh pr checks; sleep 20; done` (unbounded) | **BLOCKED** |
| `until <cond>; do sleep 15; done` (unbounded) | **BLOCKED** |
| `for ... sleep 1 (3s); done; while true; do sleep 30; done` | **BLOCKED** (per-loop scoping — a short `for` cannot mask the unbounded `while`) |
| `for i in $(seq 1 5); do ...; sleep 18; done` (90s < 100) | pass (exit 0) |
| same loop with `run_in_background: true` | pass |
| `while read line; do ...; sleep 1; done < f` (line-consumer) | pass |
| `for f in *.py; do ruff check "$f"; done` (no sleep) | pass |
| `sleep 2 && gh pr view` (leading sleep, no loop) | pass (runtime handles) |
| `sleep $INTERVAL` inside a loop (unparseable) | pass (fail-open) |
| `git commit -m "retry while deploy sleeps 30"` (quoted literal) | pass (token-based) |
| `while …; break; done; sleep 200` (sleep outside the loop body) | pass (per-loop scoping) |
| `for … $(seq 1 50) no-sleep; done; for … $(seq 1 2) sleep 10; done` | pass (count and sleep never combined across loops) |
| `echo hi # while true; do sleep 20; done` (comment text) | pass (non-executable) |
| `cat <<'EOF' … while/sleep … EOF` (heredoc body) | pass (non-executable) |
| `while true; do /bin/sleep 20; done` (path-invoked sleep) | **BLOCKED** |
| `for i in {30..1}; do …; sleep 5; done` (descending range, 150s) | **BLOCKED** |
| non-Bash tool | pass |

Detection is token-based (`safe_tokenize`, per the DESIGN.md
structural-tokenization contract): loop keywords, `sleep`, and iteration
counts must appear as standalone command tokens. A quoted literal containing
the words tokenizes to a single string token and can neither trigger a block
nor downgrade an unbounded loop to a short bounded one. Before tokenizing,
comments and heredoc bodies — text bash never executes — are stripped
(over-stripping can only suppress a block, never cause one).

Each loop is judged on its OWN `do…done` body (nesting handled with a
stack). An unbounded `while`/`until` whose body contains a parseable `sleep`
(bare or path-invoked, e.g. `/bin/sleep`) always blocks (no fixed count),
except `while read` / `while IFS= read` line-consumers which terminate on
input, not time. A `for` loop blocks when `iterations × (sum of body sleeps
per iteration) ≥ 100s`; the count is parsed from `seq 1 N` / `seq N` /
`seq A B` (= B−A+1) / `seq A STEP B`, `{A..B}` (either direction), C-style
`((i=A; i<N; i+=S))` (init, comparison operator, and step honored; missing
init or comparison → fail-open), or a literal word list (word count; globs
count as 1 word each — an undercount that only ever passes; `$`/backtick
expansions in the list → unknowable count → fail-open). `sleep` arguments
accept `s`/`m`/`h`/`d` unit suffixes.

Known limitations (intentional): a loop backgrounded at the shell level
(`… done &`) is still blocked — a `&`-job dies when the Bash tool call
returns, so `run_in_background: true` remains the correct redirect. An
unparseable iteration count or `sleep $VAR` fails open (pass).

## Background waiter-chain advisory (issue #1063)

`run_in_background: true` passes the block table above by design, and always
will — it is the redirect the block message hands out. It is also where the
blocked behaviour moved. In the 2026-08-20 session that opened this issue the
foreground `sleep N && tail` form was denied 4 times, each retry changing only
the duration; the behaviour then moved to chaining short background calls
across turns and the block count went to 0. The cost: 146 of 500 Bash calls
(29%) spent waiting on one test suite, 40 waiting calls in the densest 4-minute
window, and 9 waiters the harness killed two hours later. The first call had
already been the correct one — `run_in_background` with an `until` loop. The
agent did not wait for the notification it had itself armed.

**What a waiter is.** A background Bash call whose whole job is to elapse: its
command contains a parseable `sleep` in command position. Launching the awaited
work (`bash scripts/run-tests.sh > /tmp/out 2>&1`) carries no `sleep` and is
never recorded — this lane is about waiting on work, not doing it.

**What "same target" is keyed on: the sleep-normalized command signature.** The
three candidates the issue names are not equally usable.

| Candidate | Why not / why |
| --- | --- |
| Job id | Names the *waiter*, not what it waits on — and does not exist yet at PreToolUse time |
| Output file path | Present only when the waiter redirects or reads a file; `until gh pr checks; do sleep 20; done` would key on nothing |
| **Command signature** | The only key every waiting call has — **chosen** |

Normalization drops each `sleep`'s argument token and nothing else, then hashes
the remaining tokens. That is the narrowest rule that closes the observed gap:
the retries differed from each other *only* in the duration (240 → 595), so
`sleep 240 && tail -50 /tmp/suite.log` and `sleep 595 && tail -50 /tmp/suite.log`
collapse to one key. Dropping bare numerals in general would fold
`gh run view 123` and `gh run view 456` — two genuinely different targets whose
sole discriminator is a bare number — and turn the guard into one that fires on
every second background call.

**Armed window.** A recorded waiter counts as live for its own sleep total; past
that it has returned and re-arming is the correct call, not a duplicate. A
*loop-shaped* waiter has no computable end — an `until` loop's per-iteration
sleep says nothing about when it returns — so that shape uses a fixed default
(900s, `PRAXIS_POLL_LOOP_WAITER_TTL`). A duplicate does not refresh the existing
entry: refreshing would push the window forward indefinitely and make the
reported age of the original waiter a lie.

| Situation (all `run_in_background: true`) | Action |
| --- | --- |
| First waiter for a target | pass, recorded (exit 0, silent) |
| Second waiter, same target, only the `sleep` duration changed | **advisory** (exit 0, `additionalContext` + stderr) |
| Second waiter, same target, identical loop-shaped command | **advisory** |
| Concurrent waits on different files / different run ids | pass (silent) |
| Background call with no `sleep` at all, twice | pass (silent) |
| Re-arm after the previous waiter's window elapsed | pass (silent) |
| Same loop-shaped command in the FOREGROUND | **BLOCKED** (exit 2, unchanged) |

**Advisory, never a block.** The block message names `run_in_background: true`
as the correct path; denying it would contradict the escape the guard itself
hands out, and a genuinely-needed second waiter must stay reachable. The
message names the already-armed waiter, its age and remaining window, and
`TaskStop` as the way to reap the pile-up.

**Delivery channel: `additionalContext` AND stderr.** A PreToolUse hook's stderr
is fed to the model only when the dispatcher exits 2 — the deny path
([`docs/hook/INDEX.md`](../../../docs/hook/INDEX.md)). On this lane's exit-0 path
stderr reaches the debug log and nothing else, so a stderr-only advisory is inert
for the agent it is written for. The same text is therefore also written as
`hookSpecificOutput.additionalContext`, the one PreToolUse channel that does
reach the model; `_dispatch.run_group` forwards and merges those once deny and
ask have both missed. stderr is kept because `_fire_ledger.classify_decision`
derives `advise` from stderr — dropping it would reclassify every fire of this
lane as `pass` and erase the metric. Shape mirrors `pipefail-advisory`:
`hookEventName: PreToolUse`, no top-level `continue`.

Known limitation, intentional: PreToolUse cannot see whether the earlier
background job has already exited or been `TaskStop`ed, so a waiter reaped
early still counts as armed until its window elapses. The cost of that false
positive is one advisory line on a call that proceeds.

**State.** `<PRAXIS_HOME>/cache/poll-loop-waiters-<session_id>.json` via
`resolve_cache_file` (ppid fallback when the payload carries no `session_id`),
a `{signature: {at, armed, cmd}}` map, staged through a per-process `.tmp` name
and published with `os.replace`. Expired entries are pruned on write.
Deliberately **unlocked** under the DESIGN.md
[session-state-concurrency](../../../DESIGN.md#session-state-concurrency)
criterion: the per-process staging name settles Q0, no threshold reads the
state (the test is "a live entry exists", not an exact count — Q1), and no gate
decides block-vs-pass from it (Q2). A lost update costs one advisory that does
not fire — Q3, no lock.

## Redirect message

The block message names the alternatives so the caller can self-correct:
`run_in_background: true`, Monitor with an until-loop, `aws cloudformation wait`,
`gh run watch` / `gh pr checks --watch` / `kubectl wait`.

`Reference:` is this file, named by ABSOLUTE path resolved from the package root
(`impl.py`'s own `__file__`), never a cwd-relative string. Until issue #1012 it
named `docs/hook/foreground-poll-loop-guard.md` — a 132-byte redirect stub whose
entire content is "Moved to …" — and named it repo-relatively, so it resolved to
nothing whenever the agent was working outside a praxis checkout.

## Read-gate escalation (issue #1012)

A block answered with the same loop shape is a block that was not read. From the
(N+1)-th block of THIS guard in the same session (N = 2 by default) the message
escalates: `Why:` gains a `READ-GATE:` clause naming the running block count, and
`Correct path:` leads with "Read `<abs spec.md>` FIRST — a poll-loop retry stays
denied until that Read is recorded in this session".

| Situation (all on a command the guard already blocks) | Message |
| --- | --- |
| 1st / 2nd block of the session | base block |
| 3rd+ block, this spec not Read this session | **escalated** (`READ-GATE`) |
| 3rd+ block, this spec Read this session | base block |
| 3rd+ block, some *other* `.md` Read | **escalated** (read-set is path-keyed) |
| 3rd+ block, reference path does not resolve | base block (**fail-open**) |
| payload carries no `session_id` | base block |
| `run_in_background: true` while escalation is armed | pass (exit 0) |

Two mechanisms are REUSED rather than rebuilt — a second counter or a second
read-history would only be a second thing to drift:

- **Prior-block count** — `_lib/_fire_ledger.count_session_fires(hook,
  session_id, decision="block")`. The dispatcher already writes one RICH
  `(session_id, hook, decision)` row per group member per Bash call, and it
  writes it AFTER the member's `main()`, so the value read here is the count of
  PRIOR blocks, not including the in-flight one. Standalone (non-dispatched)
  invocation produces only COARSE rows (`session_id: ""`), which
  `count_session_fires` filters out — so the escalation is a dispatched-runtime
  behavior, and a lone `python3 impl.py` probe never sees it.
- **Read-set** — the session history `pre-edit-md-escape-advisory` maintains. Its
  PostToolUse(Read) leg records EVERY `.md` path Read in the session, spec.md
  included. That module is loaded lazily by file location (its module is also
  named `impl`) and only from the escalation branch, which runs solely on a
  command that is already being blocked, so the hot Bash path never pays for it.

Three properties keep the escalation from pointing at nothing or deadlocking:
the referent is this spec (not the redirect stub); the path is absolute and
package-root-anchored, so the agent that DID follow the reference is recognized
wherever it is working; and an unresolvable referent fails OPEN to the base
block. A gate that cannot find its own spec must not add a requirement nobody
can satisfy.

The escalation only ever rewrites the MESSAGE of a command this guard was
already going to block. It can never turn a pass into a deny.

## Env vars

- `PRAXIS_HOOK_BYPASS_POLL_LOOP_GUARD` — set to any non-empty value → bypass (exit 0).
- `PRAXIS_POLL_LOOP_GUARD_SPEC` — override the path the read-gate points at
  (tests; an unresolvable value exercises the fail-open escape).
- `PRAXIS_POLL_LOOP_READ_GATE_AFTER` — prior session blocks required before the
  read-gate escalates (default `2` → the 3rd block escalates). Unparseable →
  default.
- `PRAXIS_POLL_LOOP_WAITER_TTL` — seconds a loop-shaped background waiter counts
  as armed (default `900`). Unparseable or ≤ 0 → default.

## Fail-open

Malformed stdin JSON, non-Bash tool, empty command, unparseable count/sleep →
exit 0 (pass). The guard never blocks on infrastructure error. The waiter-chain
lane is exit 0 on every path, so an unreadable or corrupt registry costs an
advisory that does not fire, never a denied call. Within the block
path, the read-gate escalation additionally fails open (base block, no Read
demanded) on a missing `session_id`, an unresolvable reference path, or any
error reading the fire ledger or the read-set.

## Tests

```bash
bash tests/hooks/preflight-gate/test_foreground_poll_loop_guard.sh
```

Read-gate coverage is deliberately two-directional — a one-sided fixture would
let the opposite error in. Fire direction: nth un-Read block escalates; an
unrelated `.md` Read does not satisfy it; a count written by the REAL dispatcher
escalates (the case that fails if `record_group_fires`' row shape ever drifts
from `count_session_fires`' filter). Silence direction: below threshold, after a
Read of this spec, unresolvable reference, missing `session_id`, and
`run_in_background: true` while armed.

Waiter-chain coverage is two-directional for the same reason. Fire: a
duration-only retry, an identical loop-shaped relaunch. Silence: the FIRST
waiter, waits on a different file, waits on a different run id (the pair that
separates this change from "fires on every second background call"), a
background call that does no waiting, an elapsed armed window, and the same
loop-shaped command in the foreground — still exit 2.

The suite exports a temp `PRAXIS_HOME`: the guard now records state on the
background path, so without it the pre-existing `run_in_background: true`
fixtures write registry files into the developer's real `~/.praxis/cache`.
