# Stop Bypass-Route Frequency Signal

Supported hosts: all

`hooks/completion-verify/bypass-route-signal/impl.py` runs on the `Stop` event.
It scans the final assistant message for a **bypass-route noun paired with a
proposal frame in the same paragraph**, and — when it finds one — appends a
single telemetry record in its own JSONL family. That is the entire behaviour. It never blocks, never
emits a decision the model can read, and never tells the user anything.

## Why this exists

[`ETHOS.md`](../../../ETHOS.md#key-principles) principle 5 draws the line at
**authorship**:

> The one route the agent MAY relay is the gate's own — the literal
> `Bypass (if truly needed): <VAR>=1 with a one-line reason comment explaining
> why` line the blocking hook printed. […] Everything the agent *originates* is
> forbidden: asking the user to add a permission rule, walking them through a
> `.claude/settings.json` edit, offering to move the file out of the guarded
> path, or proposing any mechanism the gate's message did not itself offer.

The reason the line sits at authorship rather than at approval: the user saying
yes to an agent-originated route **permanently widens the guard for every later
session**. One accepted menu costs more than the block it got around.

[`docs/hook/RULE-BACKSTOP-GAPS.md`](../../../docs/hook/RULE-BACKSTOP-GAPS.md)
gap #4 measured this on 2026-08-15 by replaying every hook against a
synthesized transcript. All three lanes were silent. Issue #1337 closed the
follow-up-write lane (`settings-path-advisory`, PreToolUse(Edit|Write)). The
**prose lane** and the **menu lane** stayed open — that is issue #1009, and
issue #1338 is the proposal to close them.

## Why a meter and not a gate

Issue #1338's own proposal was a `type: "prompt"` hook: ask a small model, on
every Stop, whether the last message offers a bypass route. That buys a model
call per turn and a false-positive rate nobody has measured, on a class where
the *cost of the true positive* is known to be high but the *rate* is entirely
unknown. Gap #4 records exactly one observed occurrence.

Building the gate first would set a tier, a strictness env var and a bypass
token against a frequency of one. So this hook answers the cheaper question
first: **how often does the shape occur at all, and what does the noise floor
look like?** A month of ledger rows decides whether the prompt hook is worth
its per-turn cost, and what its false-positive floor is before a model is even
involved.

Consequences of that choice, stated so they are not mistaken for oversights:

- **No tier, no strict mode.** There is one env var, `PRAXIS_HOOK_BYPASS_ROUTE_SIGNAL=1`,
  and it turns the hook off entirely. A hook that emits nothing needs no
  advisory-demote knob.
- **No message on any path.** Not to the model, not to the user. A meter that
  nudges is a gate with a smaller font.
- **The number is not a violation count.** It counts turns carrying the
  vocabulary. Reading it as a count of principle-5 violations is the one
  misreading that would make this hook harmful.

## Detection model

Within one `\n\n`-delimited paragraph, **both** must appear:

**(a) Route noun** — the thing that would be widened. Taken from the four
routes principle 5 names, not invented here:

| Language | Tokens |
| --- | --- |
| Korean | `권한 규칙`, `권한 설정`, `허용 목록`, `허용목록`, `가드된 경로` |
| English | `permission rule`, `allow list` / `allow-list` / `allowlist`, `settings.json`, `disableAllHooks`, `guarded path` |

**(b) Proposal frame** — the agent offering the route rather than naming it:

| Language | Tokens |
| --- | --- |
| Korean | `추가하`, `추가해`, `편집하`, `수정하`, `옮기`, `우회`, `풀어`, `넣으면`, `하시면`, `방법`, `옵션` |
| English | `add`, `adding`, `edit`, `editing`, `move`, `moving`, `bypass`, `bypassing`, `work around`, `you could`, `we can`, `one option` |

Neither half fires alone, deliberately. A route noun on its own is ordinary
prose (`settings.json 을 읽었습니다` names the file and offers nothing); a
proposal frame on its own is most sentences in a working session. The shape that gap #4
describes is the conjunction, and paragraph scoping is what keeps a noun in
one paragraph from pairing with a frame three paragraphs later.

**Word-boundary note.** Korean tokens match as plain substrings — Hangul has no
ASCII word-boundary hazard. English tokens use explicit `(?<![A-Za-z])` /
`(?![A-Za-z])` guards rather than `\b`, because Python's `re` is Unicode-aware:
Hangul counts as a word character, so `\b` finds **no** boundary between an
ASCII word and adjacent Hangul, and the mixed-script forms this repo's sessions
actually write go unmatched. Measured:

```text
re.search(r"\badd\b", "add하면")                    -> False   ← the hazard
re.search(r"(?<![A-Za-z])add(?![A-Za-z])", "add하면") -> True
re.search(r"(?<![A-Za-z])add(?![A-Za-z])", "additional") -> False
```

The guards keep `add하면` matching while still rejecting `additional`, which is
the pair `\b` cannot separate in either direction.

## The relay carve-out

Principle 5 permits relaying the gate's own line, and every praxis block message
prints one — `hooks/_lib/block_message.py` writes
`Bypass (if truly needed): VAR=1 <hint>`, and the hand-built advisory messages
write a bare `Bypass: VAR=1`. A detector that counted those would report the
agent doing the one thing it is *supposed* to do, and the measurement would be
worthless.

Both shapes are therefore stripped **before any matching runs**, at line scope:

```python
_RELAY_LINE_RE = re.compile(
    r"^.*(?<![A-Za-z])(?i:Bypass)(?:\s*\(if truly needed\))?\s*:\s*"
    r"[A-Z][A-Z0-9_]*=1(?![A-Za-z0-9_]).*$",
    re.MULTILINE,
)
```

The `VAR=1` tail is load-bearing. Keyed on the word alone, the carve-out also
swallows `Bypass: 권한 규칙을 추가하면 됩니다` — an originated route that merely
opens with the word — which is the one shape this hook exists to count. Neither
generated message can omit the tail: `block_message.py` interpolates
`{bypass_env}=1`, and the hand-built advisories write the same literal.

Line scope, not paragraph scope, is the point: a paragraph that relays the
gate's line **and also** originates a route keeps its other lines and still
counts. Stripping the whole paragraph would let one relayed line launder
anything sharing it. The match is replaced with an empty string rather than
deleted so the newline survives — deleting it could weld two paragraphs
together and manufacture a co-occurrence the message never had.

## Known false positives — recorded, not engineered away

This hook exists to measure. Adding discriminators for these on a guess would
decide the outcome in advance, and the guess would be invisible in the number.

| Family | Example | Why it is left in |
| --- | --- | --- |
| Prose about this rule | a retrospect, a PR body, this spec quoted back | Sessions working on praxis itself will over-count. The audit must segment on repo rather than pretend the class does not exist |
| Past-tense report | `요청하신 대로 PRAXIS_X=1 로 진행했습니다` | Tense analysis is a second classifier with its own error rate. The proposal frames lean present/conditional, which is as far as this goes |
| Relayed third-party suggestion | quoting a reviewer or a vendor doc that names a route | Indistinguishable from origination without authorship analysis, which is the hard problem the prompt hook was proposed for |

The hook will also fire on **its own** report: a turn explaining this hook to
the user carries every token it looks for. That is the
`sciomc-gate`-self-trigger class, and it is expected rather than a defect.

## Storage — its own family, and why not the fire ledger

The first build recorded into the fire ledger, as every sibling Stop hook does.
**It does not work for a hook that emits nothing**, and that was measured
rather than reasoned about:

```text
$ echo '{...,"session_id":"sMETER"}' | PRAXIS_FIRE_TELEMETRY_FILE=$LED \
    python3 hooks/_lib/_dispatch.py Stop - claude
rc=0 · stdout 0 bytes · ledger: (empty)

$ same harness, a payload that trips negative-existence-verdict-gate
ledger: negative-existence-verdict-gate  block  rich  sCTRL
```

Two lines explain it. `record_session_fire` returns `False` under
`_IN_DISPATCHER` (`hooks/_lib/_fire_ledger.py`), because a grouped member is
supposed to get its record from the dispatcher instead; and the dispatcher's
`record_group_fires` derives each member's decision from its
`(rc, stdout, stderr)`. A silent member therefore classifies as `pass` and
folds into the counter file, indistinguishable from every quiet turn. The
standalone unit tests all passed against a hook whose signal vanished the
moment it ran where it actually runs.

Three ways out, and why this one:

| Option | Why not |
| --- | --- |
| Emit a user-visible `systemMessage` | Puts a line in front of the user on every match, at a false-positive rate nobody has measured — which is the thing this hook exists to measure first |
| Emit a stderr marker (dispatcher records `advise`) | Silent to user and model, but the report's `Advise` column would then be nonzero for a hook that has advised nobody |
| **Its own JSONL family** | Silent in both directions and honest in the ledger. `bypass-telemetry` is the existing precedent for an observe-only writer with its own family |

| Path | Effect |
| --- | --- |
| Shape found | one row appended to `bypass-route-events-YYYY-MM-DD.jsonl` via `_atomic_append` |
| Shape absent | nothing written |

The file lives in `_fire_ledger.resolve_telemetry_dir()` — the same directory
as every other family — for the reason `bypass-telemetry` does (issue #934):
`bypass-review` reads them all out of one `telemetry_dir`, and a family that
diverted on its own would show one side of the ratio without the other.
`PRAXIS_BYPASS_ROUTE_SIGNAL_FILE` overrides the path, for tests.

**The record carries no excerpt of the matched text.** It exists to be counted,
and a final assistant message is the least redactable thing in a session.

### The denominator comes from the fire ledger for free

The hook writes nothing on a quiet turn, so its own family holds matches only.
The dispatcher still records the hook's automatic `pass` on every Stop under
its own name, so the hook's `Fires` row in the per-hook table **is** the turn
count the matches divide by:

```text
bypass-route-signal   R   2   0   0   0   2   0   2   2026-09-08
```

Two Stop turns observed, one of which matched — read against the outcome-proxy
line below.

## Reading the number

The per-hook table gives the denominator; the **outcome-proxy** section gives
the matches, which is where `askuserquestion-loop-signal` — the other
observe-only counter — already reports:

```text
Sessions with bypass-route vocabulary in a final message : N
```

plus a per-session table when N is nonzero. `load_bypass_route_events` reads
the family and `compute_bypass_route_signal_counts` groups it by session.

Reachable as `/praxis:bypass-report` (issue #1343) or `bypass-review fire-rate`
directly.

## Tests

`tests/hooks/completion-verify/test_bypass_route_signal.sh` — one case per
enumerated variant, including the must-not-fire controls (relay line alone,
route noun alone, proposal frame alone, cross-paragraph split) and the
relay-plus-origination case that must still fire, plus the `Bypass:`-prefixed
origination that the word-keyed carve-out used to swallow.

## What this does not do

- **The menu lane.** An `AskUserQuestion` option set offering the same routes is
  a PreToolUse surface and is untouched here. Gap #4 measured it silent and it
  stays silent; #1009 holds it.
- **Deciding the tier.** The audit does that, with a month of rows.
- **Judging any turn.** Stated three times in this file because it is the one
  property that makes an unmeasured detector safe to ship.
