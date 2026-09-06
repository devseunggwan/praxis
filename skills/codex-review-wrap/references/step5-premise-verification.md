# Step 5 verification gates: 5a / 5b / 5c / 5g (codex-review-wrap)

Detailed procedure for [`../SKILL.md`](../SKILL.md) Step 5 sub-steps 5a
(classification), 5b (premise verification), 5c (flip detection and the
session ledger), and 5g (critic pre-lock probe check). Execution order is
owned by the spine's *Execution order* list. Sibling references:
[`step5-siblings-and-regions.md`](step5-siblings-and-regions.md) (5d/5f/5h),
[`step5-approval-and-rounds.md`](step5-approval-and-rounds.md) (5e/5i/5j).

## 5a. Classify each finding

| Type | Examples | Premise check required |
| ------ | ---------- | ------------------------ |
| **Fact-modifying** | WHERE / filter logic, catalog / schema / table / column names, CLI flag or option references, API endpoint / signature, version or SDK identifiers, **string literals used as identifiers** (provider keys, env names, lookup tokens) | **YES** |
| **Structural** | Code organization, function decomposition, file layout, renames of code symbols only (variables, functions, types) when no string literal is touched | No |
| **Stylistic** | Comments, formatting, lint-style suggestions | No |

A finding is **fact-modifying** if accepting it would change a value the
running system reads or matches against (filter predicate, identifier
lookup, CLI invocation, network call, string-keyed lookup). Anything
else is structural or stylistic. When in doubt, treat the finding as
fact-modifying — false positives cost one extra verification call;
false negatives cause the exact flip-oscillation this gate prevents.

## 5b. Verify the premise before applying fact-modifying findings

For each fact-modifying finding, run one independent check that would
**falsify** the underlying premise. Capture the verification output and
keep it for 5d. If the verification disproves the premise, do NOT apply
the finding — reply to Codex (or surface to the user) with the result.

### Verification methods by finding type

This table is the canonical reference for the AC #3 documentation
requirement; lift it when authoring related skills.

| Finding type | Verification method |
| -------------- | --------------------- |
| WHERE clause / filter logic | Run the query with and without the filter; compare row counts against the rationale |
| Catalog / schema / table name | `SHOW CATALOGS` / `SHOW SCHEMAS` / `SHOW TABLES` (or equivalent MCP / Trino / live-env query) |
| Column name | `DESCRIBE <table>` against the live env |
| CLI flag / option | `<binary> --help` and a real dry-run invocation — naming-pattern intuition is **not** verification |
| API endpoint / signature | Hit the live endpoint, read the official docs, or grep the SDK source |
| Version / SDK identifier | Resolve via Context7 or the official changelog — never trust training data |

### Recursive premise (one level only)

If the verification command itself depends on a fact, falsify that
prerequisite first — but cap recursion at **one level**. Example: a
verification SQL `SELECT col_a FROM t WHERE join_key = ?` assumes
`join_key` exists; run `DESCRIBE t` once before running the SELECT.
Do not recurse further (don't verify that DESCRIBE itself works) —
once is enough. Premise-falsification before public claim — the
*External-Surface Write Requires Falsification* rule
([`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries)).

## 5c. Flip detection — halt A→B→A oscillation

Maintain a per-session ledger across all rounds in the same session.
The ledger has **nine record shapes** — `applied`/`rejected` (flip
detection input), `sibling-applied` (Step 5d cross-check),
`rounds_per_region` (Step 5f diminishing-returns), `deferred` (Step 5i
follow-up issue), `sibling-id` (Step 5d-i answer, reused across rounds),
`review-path` (Step 4a reviewer selection, reused across rounds),
`round-started` (one per round, unconditional), `round-continued`
(Step 5j gate decision) — all must be tracked because a finding rejected
in round N can re-appear in round N+M and would otherwise look novel:

```
round-started:    target={worktree-path}#{branch} | round={N}
applied:          {file}:{line-or-region} | round={N} | {value-before} → {value-after}
rejected:         {file}:{line-or-region} | round={N} | {value-before} → {value-after} | reason: {falsifying evidence}
sibling-applied:  {sibling-repo}#{PR-or-branch} | round={N} | finding={brief-label} | result={same defect | different | does not apply}
rounds_per_region: {file}:{region} | round={N} | cumulative={C}
deferred:         {file}:{line-or-region} | round={N} | finding={brief-label} | issue={URL or "pending"}
sibling-id:       target={worktree-path}#{branch} | round={N} | sibling={ref[,ref…] | none}
review-path:      target={worktree-path}#{branch} | round={N} | path={codex-companion | code-reviewer | manual}
round-continued:  target={worktree-path}#{branch} | from={N} | applied={C} | decision={continue | stop | other} | to={N+1 | —}
```

`target=` is `{worktree-path}#{branch}` — both fields confirmed in Step 3,
because neither identifies the PR on its own: one worktree can be switched
between branches, and one branch can be checked out at a different path in
a later session. `target=` is what keeps the per-invocation answers
(`sibling-id:`, `review-path:`) from leaking: the ledger is
per-**session**, and the Invocation Model ([`../SKILL.md`](../SKILL.md)) has one session run N
sequential invocations for N PRs, so "the session's first round" is not
the same thing as "this PR's first round". A row is reusable only when
both fields of its `target=` equal the current invocation's; otherwise ask
again.

`sibling-id:` is written once per **invocation target** by 5d-i —
**including when the answer is "no sibling"** (`sibling=none`), because a
missing row is what would otherwise force 5d-i to re-ask on every
re-entered round. `round-continued:` records one 5j decision; `to=` is
filled only when `decision=continue` and is `—` otherwise, so a stopped
round never leaves a round number that was never started. The `→` glyph
stays reserved for value transitions — round transitions use
`from=`/`to=`.

**Round number `N`** is derived, not stored separately:
`N = (the highest round= value in the ledger) + 1`, and `N=1` when the
ledger holds no `round=` field at all. Read only the dedicated
`| round={integer} |` field of a **recognized record shape** — never a
`round=` that happens to occur inside free-form content. Several fields
are free-form: `rejected:` carries `reason:` text (falsifying evidence, or
a `user:` sentence), `sibling-applied:` and `deferred:` carry a
`finding={brief-label}`, and any of them may quote a URL. A `round=999`
sitting in one of those would otherwise advance the round counter to 1000
without a round having started. This is monotone by
construction, so it stays correct where a `round-continued:`-anchored
formula does not: a `decision=stop` row carries `to=—` and would re-issue
its own `from=` on the next invocation in the same session, and a round
that applied nothing writes no `round-continued:` row at all. Round
numbers are session-wide and never reset per target — they order the flip
ledger, which is also session-wide.

**Write `round-started:` as the very first ledger action of every round**,
ahead of the 5f counter update, and unconditionally — before it is known
whether the round will produce a finding. Every other row is conditional:
a round where Codex returns nothing and 5h synthesizes nothing writes no
`rounds_per_region:`, no `applied:`, and no `round-continued:` row, so
without `round-started:` that round leaves the ledger's highest `round=`
untouched and the next round is handed the same `N`.

**Cumulative round count `R`** — the number of rounds run against **the
current target**, which is what a user reading the 5j question needs.
`R = count of round-started: rows whose target= matches this invocation's`.
It is not interchangeable with `N`: `N` is session-wide and never resets,
so on the second PR of a session `N` already counts the first PR's rounds.

Before applying any new edit, scan records whose prefix token is exactly
**`applied:`** or **`rejected:`** (NOT `sibling-applied:`,
`rounds_per_region:`, `deferred:`, `sibling-id:`, `review-path:`,
`round-started:`, or `round-continued:`) in the ledger.
A flip fires when:

1. **Applied flip** — the new edit would revert a previously-applied
   change (`applied: A → B` then new proposal `B → A` on the same region).
2. **Re-proposal of rejected** — a finding that was already rejected
   in an earlier round is being proposed again with the same value
   transition (`rejected: A → B` then new proposal `A → B` again).

In either case, STOP and surface to the user:

```
⚠ Flip detected: {file}:{region}
   Round N {applied|rejected}: {A} → {B}
   Round N+M now suggests:     {B} → {A}    (or same A → B for re-proposal)
Both findings cannot be simultaneously correct.
Resolve before applying further edits.
```

**Evidence rejection vs user decision.** A `rejected:` row whose reason
starts with `user:` (written by 5i for a `미적용` / `후속이슈` answer) records
a *decision*, not a disproved premise — the finding may well be correct. When
the colliding row is one of those, do not claim a factual contradiction; use
this message instead:

```text
⚠ Re-proposal of a user-declined finding: {file}:{region}
   Round N: user chose {미적용|후속이슈} — {A} → {B}
   Round N+M proposes the same change again.
Confirm before re-applying; the earlier answer was a decision, not a
disproved premise.
```

Do not apply either side of a flip without explicit user direction. The
ledger lives in the assistant's working memory for the session only —
flip detection is inherently same-session and does not require
cross-session persistence.

## 5g. Critic pre-lock probe check

Before a critic finding that contains any of the negative-claim forms below
is surfaced to the user, the critic **must** run an independent live probe
at the assertion site and include the result in the same message body.

### Negative-claim trigger forms

The gate fires when the critic's output (or any finding it forwards)
contains one of the following patterns — in English or Korean:

| English form | Korean form |
| --- | --- |
| "X is fabricated" | "X 는 fabricated" |
| "X does not exist" | "X 는 존재하지 않음" / "X 는 없음" |
| "X is unused" | "X 는 사용되지 않음" |
| "X has no runtime effect" | "X 는 runtime effect 가 없음" |
| "X is missing from {file/scope}" | "X 는 {file/scope} 에 없음" |

The list is illustrative, not exhaustive. Any claim whose logical content
is "X does not exist in the codebase / in this file / in this scope" falls
within the gate — regardless of exact phrasing.

### Mandatory probe citation format

Every negative claim that falls within the gate must include, in the same
message body at the assertion site:

```
Probe: <command> → <one-line output>
```

Examples:

```
Probe: grep -n PRAXIS_ASK_END_STRICT hooks/preflight-gate/block-ask-end-option/impl.py → 452: strict_env = os.environ.get("PRAXIS_ASK_END_STRICT", "")
Probe: grep -rn "col_b" schemas/my_table.sql → (no output — col_b absent)
Probe: grep -n "def run_query" src/client.py → (no output — symbol not defined)
```

The probe command must be the **actual command**, not a description of
what was done. "I already read this file earlier in the session" is **not**
a valid substitute — re-run at the negative-claim emit site.

### Absence-of-evidence vs evidence-of-absence

When the probe returns non-empty output that contradicts the negative claim,
the critic must **retract** the claim before surfacing the finding:

```
Retracted: original claim "PRAXIS_ASK_END_STRICT is fabricated"
Probe: grep -n PRAXIS_ASK_END_STRICT hooks/preflight-gate/block-ask-end-option/impl.py →
  28: Deprecated: PRAXIS_ASK_END_STRICT=1 is still respected when explicitly set
  452: strict_env = os.environ.get("PRAXIS_ASK_END_STRICT", "")
Finding: PRAXIS_ASK_END_STRICT exists — claim withdrawn.
```

When the probe returns empty output (absence confirmed), cite the empty
result explicitly so readers can distinguish verified absence from unchecked:

```
Probe: grep -rn "col_b" schemas/ → (no output — col_b absent in schemas/)
```

### Worked examples

**F1 — git boolean-flag fix (PR #344 round-2, author failure caught by round-3 critic)**

Critic finding that needed a probe before surfacing:
> "`--literal-pathspecs` and `--super-prefix` are boolean flags and cannot
> take a value argument."

Required probe citation:
```
Probe: man git | grep -A2 '\-\-literal-pathspecs' → --literal-pathspecs: Treat pathspecs literally. [no value argument]
Probe: man git | grep -A2 '\-\-super-prefix' → --super-prefix=<path>: [takes a value — NOT a boolean flag]
```

The second probe disproves the grouped claim for `--super-prefix`. Without
these probes, the critic's "both are boolean" claim would have been surfaced
unchecked and the force-push bug would not have been caught in round 3.

**F2 — `PRAXIS_ASK_END_STRICT` fabrication claim (PR #341 round-1, critic failure)**

Critic finding that was surfaced without a probe:
> "`PRAXIS_ASK_END_STRICT` is a fabricated precedent — it does not exist
> in hooks/*.py."

Required probe citation (missing in round-1):
```
Probe: grep -n PRAXIS_ASK_END_STRICT hooks/preflight-gate/block-ask-end-option/impl.py → 452: strict_env = os.environ.get("PRAXIS_ASK_END_STRICT", "")
```

The probe would have immediately falsified the claim (the variable appears
at lines 28, 30, 31, 417, 451, 452, 457). Because the probe was skipped, the round-2
fix agent had to discover and correct the critic's error inline — a
preventable extra round-trip.

### Critic prompt template requirement

When codex-companion or the review model emits critic output, the system
prompt or review-invocation context **must** include the following
requirement block so the gate is enforced at generation time, not only
at post-processing time:

> **Measured limitation — `codex@openai-codex 1.0.6`** (2026-08-15): on this
> version, `review --help` answers that `/codex:review` maps to the built-in
> reviewer and does **not** support custom focus text, so the template
> **cannot be injected at generation time** — the gate has to be enforced by
> the caller on the returned findings (halt any negative claim lacking a
> `Probe:` citation, per the trigger forms above, before surfacing it). Do
> **not** use `adversarial-review --help` as a cheap capability probe either:
> on 1.0.6 that subcommand does not handle `--help` and **starts a real
> review** that has to be stopped. Generation-time injection applies when the
> reviewer accepts a context string — e.g. the `oh-my-claudecode:code-reviewer`
> fallback — or a companion version that re-grows custom focus text. Full
> measurement history: [`verification-log.md`](verification-log.md).

---

```
CRITIC PRE-LOCK PROBE GATE (mandatory)

Before surfacing any of the following negative claim forms, run an
independent live probe at the assertion site and include the result
inline in the same message:

  - "X is fabricated"
  - "X does not exist" / "X 는 없음" / "X 는 존재하지 않음"
  - "X is unused" / "X 는 사용되지 않음"
  - "X has no runtime effect" / "X 는 runtime effect 가 없음"
  - "X is missing from {file/scope}" / "X 는 {file/scope} 에 없음"

Required inline citation format:
  Probe: <command> → <one-line output>

Example:
  Probe: grep -n PRAXIS_ASK_END_STRICT hooks/preflight-gate/block-ask-end-option/impl.py → 452: strict_env = os.environ.get("PRAXIS_ASK_END_STRICT", "")

"I already read this file earlier" is NOT a valid substitute — re-run
the probe at the negative-claim emit point. If the probe disproves the
claim, retract the claim before surfacing the finding.
```

---

The template block above must appear verbatim (or equivalent) in any
context string passed to codex-companion's review invocation — when the
version in use accepts one (see the measured limitation above; on
`codex@openai-codex 1.0.6` there is no such context string, and the gate is
enforced caller-side instead). When `oh-my-claudecode:code-reviewer` is used
as the Step 4a fallback, surface this requirement block as the first item in
the reviewer's context.
