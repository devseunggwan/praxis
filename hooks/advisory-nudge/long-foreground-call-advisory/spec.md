# long-foreground-call-advisory

Supported hosts: all

`hooks/advisory-nudge/long-foreground-call-advisory/impl.py` runs on
`PreToolUse(Bash)`. It writes a stderr advisory when a **foreground** Bash call
declares an explicit `timeout` above the status-briefing threshold. It never
blocks, and it has no bypass env var.

## Why this exists

Issue [#991](https://github.com/devseunggwan/praxis/issues/991): a single long
foreground Bash call removes the *firing point* of the status-briefing rule.
`Decision-Point Briefing → Proactive-execution trigger` forbids 3-5 minutes of
silence, but that trigger can only fire between turns. While one Bash call
blocks, no assistant text can be emitted at all, so the rule becomes physically
unreachable rather than merely violated. The rule body already names the
precondition — *"Secure the firing point before a call expected to exceed the
threshold — this is a precondition on the call, not advice about it"* — and
designates a PreToolUse advisory as the third-generation escalation after two
prose-tier failures (2026-08-05, 2026-08-11).

## The trigger is inverted relative to the issue's proposal

Issue #991 §작업목록 item 1 proposed two candidate signals. Both were measured
and rejected; this hook ships the **inversion** of signal (b).

| Proposed signal | Verdict |
| --- | --- |
| (a) configurable long-running-command pattern list (`scripts/run-tests.sh`, `pytest`, `npm test`, `gh run watch`, `docker build`, bulk clone/fetch) | rejected — 13 fires / 0 true positives on the measured corpus, and it advises *against* `gh run watch`, which `foreground-poll-loop-guard` actively **redirects callers to** |
| (b) `run_in_background` false **and** no `timeout` specified | rejected — backwards (below) |

Signal (b) is backwards because a foreground Bash call that specifies no
`timeout` returns control at the **120000ms (120s = 2.0 min) default**. Measured
directly: a foreground `sleep 300` with no `timeout` field returned control at
exactly 120s. 120s is *below* the 3-5 min briefing threshold, so signal (b)
selects exactly the class of call that **cannot** breach the rule, and exempts
the only class that can — a call carrying an explicit, large `timeout`. The
issue's own 16.1-minute observation requires roughly 966000ms of declared
timeout to be reachable at all.

So the shipped condition keys on **declared intent**: the caller's own
`tool_input.timeout` is the evidence, with no command pattern list, no per-repo
configuration, and no guessing which commands are "expected to be long".

## Detection contract

All of these must hold for the advisory to fire:

1. `tool_name` is `Bash`.
2. `tool_input.run_in_background` is not truthy (`true`, or the strings
   `"true"` / `"1"` / `"yes"`).
3. `tool_input.timeout` is present and parses as a positive number (int, float,
   or numeric string).
4. That timeout is **strictly greater than** `_BRIEFING_THRESHOLD_MS = 180000`
   (3.0 min).

The command text is never read. That is deliberate: keying on declared timeout
instead of command shape is what keeps this hook from contradicting
`foreground-poll-loop-guard` over commands like `gh run watch`.

## Threshold choice

`_BRIEFING_THRESHOLD_MS = 180000` is the **lower edge of the rule's own 3-5 min
window**, so a declared ceiling above it is a declared intent to breach.

This is intentionally *not* the `_FOREGROUND_CEILING_S = 120` constant that
`foreground-poll-loop-guard` uses. That constant is the Bash **default
timeout** — a property of the runtime — whereas 180000ms is a property of the
briefing rule. The two quantities happen to sit near each other and must not be
merged: it is precisely the gap between them (120s default < 180s threshold)
that makes the issue's signal (b) unfireable.

## Enumerated surface

| `tool_input` | Result |
| --- | --- |
| `{command, timeout: 900000}` | advisory (15.0 min) |
| `{command, timeout: 966000}` | advisory (the issue's 16.1 min observation) |
| `{command, timeout: 180001}` | advisory (1ms past the threshold) |
| `{command, timeout: 400000, run_in_background: false}` | advisory |
| `{command: "echo hi", timeout: 900000}` | advisory — command text is not read |
| `{command, timeout: "600000"}` / `300000.0` | advisory (numeric string / float) |
| `{command}` — **no timeout field** | silent — the 120000ms default already returns below the threshold |
| `{command: "gh run watch"}` — no timeout | silent — pattern list (a) is not implemented |
| `{command, timeout: 900000, run_in_background: true}` | silent — that is one of the two escape routes |
| `{command, timeout: 180000}` | silent — at the threshold, not past it |
| `{command, timeout: 120000}` / `60000` | silent |
| `timeout` null / `true` / `"fifteen minutes"` / object / `0` / negative | silent (fail-open) |
| non-Bash tool, malformed JSON, non-dict payload or `tool_input` | silent (fail-open) |

## Advisory text

Two stderr lines. The first states the declared timeout and the threshold in
both ms and minutes, and names the mechanism (no assistant text can be emitted
while one call blocks, so the trigger has no firing point). The second names
**both** escape routes required by issue #991 §작업목록 item 2:

- set `run_in_background: true` — the runtime re-invokes on exit, so the turn
  boundary survives; or
- lower `timeout` below 180000ms to take control back and brief.

## Known limitations

- **Bash-only; the general phenomenon is not.** The longest single-call gap
  measured while investigating #991 was a **non-Bash tool call — `Workflow`, at
  297.4s**. A `PreToolUse(Bash)` hook cannot cover that. Widening the matcher
  was issue #991's option 5 and was not taken here: it multiplies the false-
  positive surface, and the issue explicitly scoped subagent-internal silence
  out. The advisory therefore covers *one* route to the failure, not the class.
- **Declared intent, not real duration.** A call that declares
  `timeout: 900000` but finishes in 4 seconds still receives the advisory. This
  is the hook's entire false-positive surface and is accepted: the timeout
  field is the only pre-execution evidence of intent that exists, and the
  advisory costs one stderr line.
- **A call that blocks for 2 minutes every time is invisible.** Repeated
  default-timeout calls can accumulate past 3 minutes of silence across a turn;
  a PreToolUse hook sees one call at a time and cannot sum them.
- **Auto-backgrounding is out of scope** (issue #991 §범위 밖) — rewriting
  `run_in_background` would change the tool call's return contract, which is
  past the advisory tier.
- **User-wait gaps are not silence.** 9 of the 11 gaps >300s in the measured
  session had zero tool calls; those are normal state, not this hook's target.

## Fail-open contract

| Condition | Behavior |
| --- | --- |
| malformed / non-dict stdin JSON | exit 0, silent |
| `tool_name != "Bash"` | exit 0, silent |
| missing or non-dict `tool_input` | exit 0, silent |
| `run_in_background` truthy | exit 0, silent |
| `timeout` absent, null, non-numeric, boolean, or ≤ 0 | exit 0, silent |
| `timeout` ≤ 180000 | exit 0, silent |
| any uncaught exception | exit 0 via `@fail_open` |

## Registration

`hooks/manifest.json` registers the hook as `advisory-nudge`, `PreToolUse`,
matcher `Bash`, timeout 5 seconds. It declares no env vars, no state paths, and
runs no external commands, so it carries no manifest `mode` block.

## Tests

```bash
bash tests/hooks/advisory-nudge/test_long_foreground_call_advisory.sh
```

The suite pins **both** directions. The silent direction is not decoration: it
pins the issue's own proposed signal (foreground + no timeout) as a
**non**-trigger, which is the whole correction this hook encodes.
