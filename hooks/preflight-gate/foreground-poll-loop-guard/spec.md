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

## Redirect message

The block message names the alternatives so the caller can self-correct:
`run_in_background: true`, Monitor with an until-loop, `aws cloudformation wait`,
`gh run watch` / `gh pr checks --watch` / `kubectl wait`.

## Env vars

- `PRAXIS_HOOK_BYPASS_POLL_LOOP_GUARD` — set to any non-empty value → bypass (exit 0).

## Fail-open

Malformed stdin JSON, non-Bash tool, empty command, unparseable count/sleep →
exit 0 (pass). The guard never blocks on infrastructure error.
