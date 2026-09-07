# PreToolUse Output-Block Falsification Advisory + Ask-Escalation

Supported hosts: all

`hooks/advisory-nudge/output-block-falsify-advisory/impl.py` fires on every `PreToolUse` event
for `AskUserQuestion` and `Bash` tool calls. It detects two surfaces where
a self-authored proposal block is about to be surfaced without a falsification
check and either asks for confirmation or emits an advisory reminder. The
`Bash` surface carries two independent legs: the original bulk-action advisory,
and the blast-radius two-factor `ask` added by issue
[#1010](https://github.com/devseunggwan/praxis/issues/1010).

### Why this exists

The *Output-Block-Level Falsification Gate* rule ([`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries)) instructs:

> Before surfacing a self-authored proposal as a complete output block, run
> an explicit falsification test on its premise. If a concrete invalidating
> link/artifact exists — STOP. Do not surface the proposal.

And **"Self-Falsify Before Recommendation Lock"** adds:

> When labeling an option as `(Recommended)`, design and execute a disconfirming
> test of the recommendation's own premise BEFORE surfacing. State explicitly
> 'if this recommendation is wrong, what observation should be missing?' and
> confirm that observation is in fact missing.

Despite these rules being loaded into context (4+ memory entries accumulated
2026-05-03 through 2026-05-13), the retrieval trigger does not fire at the
specific moment the proposal block is authored. A 2026-05-17 retrospect session
confirmed 4/4 `(Recommended)` surfaces lacked verifiable Falsified: evidence.

Text rules and MEMORY.md entries alone have proven insufficient to prevent
recurrence. A structural hook moves the gate to the tool-call use-site.

References: issue [#221](https://github.com/devseunggwan/praxis/issues/221) (advisory),
[#290](https://github.com/devseunggwan/praxis/issues/290) (T1 ask escalation), [#899](https://github.com/devseunggwan/praxis/issues/899) (T1 deny → ask restore),
[#369](https://github.com/devseunggwan/praxis/issues/369) (T2 confidence-anchoring extension),
[#1010](https://github.com/devseunggwan/praxis/issues/1010) (Bash blast-radius two-factor ask).

### Source rule detail (always-loaded SoT reference)

The author's always-loaded `CLAUDE.md` keeps only the 1-line headline + STOP
imperative for this gate; the full detail lives here as the reference SoT
(issue [#793](https://github.com/devseunggwan/praxis/issues/793) partial-slim —
the headline stays always-loaded to shape pre-tool-call reasoning, the detail
below moves to the hook backing it). This hook structurally enforces the
`AskUserQuestion` and `Bash` subset of the rule; the remaining trigger forms
below stay agent-side retrieval discipline (no PreToolUse surface exists to
intercept assistant prose).

**Triggers** — output forms that require the gate to fire BEFORE the text is surfaced:

- Action item / follow-up / "P2/P3" / "Recommended" labeled proposal in answer trailer
- Hub issue title + body + 24-repo checkbox block
- PR title + body + design table
- `AskUserQuestion` menu option labeled `(Recommended)` or framed as "safe" / "natural" / "safer"
- Design tables or scope summaries presented mid-answer to a question the user did not ask

Of these, the hook structurally catches the `AskUserQuestion` evaluative-option
trigger (T1/T2 in the detection table below) and the `Bash` bulk-action
downstream-consequence trigger. The answer-trailer proposal, Hub issue body, PR
body, and mid-answer design table are surfaced as assistant prose — no
PreToolUse surface exists to intercept them, so they remain agent-retrieval
discipline.

**Mandatory pre-output question** — asked in the agent's internal reasoning, not the user-facing text:

> "Is this proposal's stated objective already addressed by in-flight work, a
> merged PR, an existing artifact, or a parallel proposal in the same session?
> If I had to cite the link that breaks this proposal's necessity, what would
> it be?"

If a concrete invalidating link/artifact exists → **STOP**. Do not surface the
proposal. Report the existing solution instead, in one sentence.

**Externalization** — for high-stakes output (irreversible mutation,
external-surface write, schema change, multi-repo PR, Hub issue body), spawn
`Agent(subagent_type="oh-my-claudecode:critic")` **in parallel** with the
self-falsification step. Brief it with the proposal + the invalidating-link
question and ask it to disprove. Same-cache self-debate inherits the same
anchoring; a separate context window does not. Skip when self-falsification
already produced a concrete invalidating link, or when the output is in the
Out-of-scope set below.

**Out of scope** — single-token answers to direct user questions, mechanical
edits the user requested, reversible exploration commands.

### What is detected

| Tool | Trigger condition | Decision |
| ------ | ----------------- | ---------- |
| `AskUserQuestion` (T1) | Option `label` contains exact `(Recommended)` or `(추천)` AND the question body plus triggering option descriptions do not satisfy the `Falsified:` predicate | `permissionDecision: ask` (ASK_MSG) |
| `AskUserQuestion` (T1) | Option `label` contains exact `(Recommended)` or `(추천)` AND the question body plus triggering option descriptions satisfy the `Falsified:` predicate | Silent pass |
| `AskUserQuestion` (T2, issue #369) | Option `label` OR `description` contains a confidence-anchoring framing token AND the question body plus triggering option descriptions do not satisfy the `Falsified:` predicate | `permissionDecision: ask` (ANCHORING_ASK_MSG) |
| `AskUserQuestion` (T2) | Same as above + the question body plus triggering option descriptions satisfy the `Falsified:` predicate | Silent pass |
| `AskUserQuestion` (T3) | Option `label` contains case-insensitive `(recommended)` only | Dead under new precedence — T2's bare `recommend(ed\|s)?` token catches it first as ask |
| `Bash` (blast radius, issue #1010) | One command segment contains BOTH an irreversible verb AND a shared-surface token (see table below) | `permissionDecision: ask` (BLAST_RADIUS_ASK_MSG) |
| `Bash` | Command matches a bulk-action mutation keyword (see table below) AND no segment satisfies the blast-radius predicate | Advisory stderr |
| Any other tool | — | Silent pass-through |
| Malformed payload / missing field | — | Silent fail-open |

#### AskUserQuestion T1: (Recommended) marker and Falsified: gate

`(Recommended)` and `(추천)` in option labels are the canonical signal for a
self-authored proposal block about to be surfaced. When these exact tokens
(case-sensitive, including parentheses) are detected, the hook checks the
`question` field of each question object, plus every option's own
`description` field (issue #828 — see below; NOT `label`), for a line that
starts with `Falsified:` (exact prefix at line start, as in `Falsified:
checked no existing PR — none found`).

- **`Falsified:` present** → silent pass. The model has provided verifiable
  evidence of a disconfirming test.
- **`Falsified:` absent** → `permissionDecision: ask` with ASK_MSG (soft gate — issue #393
  raised this to `deny`, issue #899 restored it). The scaffold still names the
  missing line, but the decision is acknowledge-and-proceed rather than a forced
  re-author round trip.

  Why the downgrade: #393's premise was systematic non-compliance (retrospect
  2026-05-23 — 3 calls, 0 `Falsified:` lines). A 90-day Claude Code transcript
  census no longer supports it — `Falsified:` lines (221/wk) now outnumber
  `(Recommended)` labels (184/wk), i.e. the template is applied even where the
  hook would not fire. What `deny` still cost was the retry tax: 306 blocks over
  30 days, 48% of all `AskUserQuestion` blocks, up to 14 in a single session.
  That tax displaced questions onto the ungated prose surface (AskUserQuestion
  per 1k assistant turns 9.25 → 6.36; prose-ask/AskUserQuestion 1.71 → 2.08),
  reproducing the very defect the gate exists to catch, where the gate cannot
  see it.

T1's *marker* scan (is `(Recommended)`/`(추천)` present at all) reads
`options[].label` ONLY — description is scanned for the marker by T2
(below). The *evidence* scan (does a `Falsified:` line exist) reads the
question body AND each **triggering** option's own `description` (issue #828)
— never `label`, and never a non-triggering option's description. See the
"Why …" subsections below for why each scoping decision is load-bearing,
not stylistic.

#### AskUserQuestion T1/T2 evidence location: question body OR option description (issue #828)

**Why this exists**: praxis skill `praxis:cmux-delegate` (and this hook
itself) require a `Falsified:` line to satisfy T1/T2 before the ask
decision clears. Before issue #828 the only place `_has_falsified_line`
scanned was `questions[].question` — the single string field carrying the
literal user-facing question text. In practice this pushed the model to
prepend the full falsification rationale (often multi-paragraph, Korean,
multi-line) onto that one field, because the alternative was retrying the
tool call with no way to record the evidence anywhere else the hook would
accept.

That shape — a long, multi-line, non-ASCII (CJK) string inside a single
tool-call argument — matches the trigger conditions reported for a
Claude Code harness bug where tool-call arguments are double-encoded to a
JSON string and rejected as malformed input
(`{"__unparsedToolInput": {...}}` / `InputValidationError`; upstream
anthropics/claude-code
[#69522](https://github.com/anthropics/claude-code/issues/69522),
[#79339](https://github.com/anthropics/claude-code/issues/79339),
[#74800](https://github.com/anthropics/claude-code/issues/74800)). This
hook's own T1/T2 requirement was a **self-inflicted contributor** to that
trigger: every `(Recommended)` call was forced to inflate `question` with
exactly the long/multiline/non-ASCII payload shape the bug is sensitive to.

**Feasibility investigation — moving the check to preceding assistant
prose (rejected, infeasible)**: the original idea was to let the model
write the `Falsified:` line as ordinary assistant text *before* the
`AskUserQuestion` tool call, and have this PreToolUse hook read that
preceding prose from the transcript instead of from `tool_input` at all —
fully removing the evidence from the tool call's JSON payload. This is
infeasible: a `PreToolUse` hook only receives the already-parsed
`tool_input` for the call it is gating; it cannot see the text blocks the
assistant emitted in the same in-flight turn, because those blocks are not
yet flushed to the transcript file when the hook runs (confirmed by this
repo's own prior investigation — see `pre-output-falsification-gate`
Lane A's "A3 INFEASIBILITY NOTE" in `impl.py`, which hit and documented the
identical limitation: "A PreToolUse hook cannot see the assistant turn
being authored — only the tool_input being submitted"). The
`extract_last_assistant_text` / `has_tool_in_turn` helpers in `_lib/_transcript.py`
that could theoretically do this are used only by **Stop** hooks (see
`hooks/completion-verify/*`), which fire after the full turn — including
any pre-tool-call text — has already been persisted. No PreToolUse hook in
this repo reads current-turn assistant prose for this reason.

**What shipped instead**: since the evidence cannot leave `tool_input`
entirely, it is now allowed to live in a smaller, more naturally-scoped
field within `tool_input` — each triggering option's own `description` —
instead of being concatenated into the single shared `question` string.
`_has_falsified_line` already matched a `Falsified:` line by its
`"Falsified: {label}"` prefix regardless of which text it came from, so no
change to that matching logic was needed; only the set of texts fed into it
(`q_texts`) was widened to include each **triggering** option's own
`description` (not `label`, and not every option in the question — see the
two subsections below for why both scoping decisions are load-bearing).
This:

- Keeps `question` short and single-line (just the actual question sentence)
  in the common case, reducing the long+multiline+non-ASCII payload shape.
- Distributes multi-option evidence across N smaller per-option strings
  instead of one large concatenated block.
- Requires no transcript access and no new failure mode — it is a pure
  `tool_input`-local check, same as the pre-#828 contract.
- Is fully backward compatible: a `Falsified:` line in `question` still
  satisfies the gate exactly as before (no regression), so an agent that
  keeps the old convention does not get newly blocked.

The `[trigger-reduction]` sentence appended to both `ASK_MSG` and
`ANCHORING_ASK_MSG` (see Response shape below) tells the model to prefer
the option-`description` placement going forward.

**Why `description` only, never `label` (codex review, in-vivo P2 catch
during this issue's own review pass)**: the first implementation widened
`q_texts` using the existing `collect_option_texts(options)` helper, which
returns label + description per option (it already existed for T2's
anchoring-token scan). That reintroduced a self-referential bypass:
`_has_falsified_line`'s single-triggering-label mode (issue #290 contract
— when `len(triggering_labels) <= 1`, ANY clean `Falsified:`-prefixed line
anywhere in `texts` satisfies the gate) does not care which option a line
came from when there is only one triggering option. A single `(Recommended)`
option whose own **label** was crafted to read
`"Falsified: Option A (Recommended) — probe: fake → fake; premise survives
because fake"` silent-passed the gate it was itself triggering — the
model could satisfy the falsification requirement by writing a
label that merely *looks like* evidence, with zero actual probe run and
zero real observation. Live probe (`impl.py` fed that exact payload) → rc=0,
empty stdout — confirmed silent pass before the fix.

`label` is the same field the `(Recommended)`/`(추천)` marker itself lives
in (T1's marker scan) and the same field T2 scans for anchoring tokens —
it must never also serve as the evidence proving that marker's own premise,
or the gate collapses to "does the label contain a `Falsified:`-shaped
string", which any single-line edit can satisfy trivially. `description`
does not have this problem: T1's marker scan reads `label` only (never
`description`), so a `description` field can carry `Falsified:` evidence
without also being the very marker the evidence is supposed to falsify.
The fix (see `impl.py` `q_texts` construction) collects only `o.get("description")`
per option, never `o.get("label")`. Regression test: "AskUserQuestion:
label itself crafted as a Falsified: line, no description → still T1 gates
(issue #828 codex P2)" in the test suite.

**Why evidence is scoped to TRIGGERING options only, never every option in
the question (codex review round-2, second in-vivo P2 catch)**: the fix
above still added the description of **every** option in the question to
`q_texts` unconditionally — not just the triggering ones. That reopened
the same single-triggering-label-mode gap from a different angle: a
question with exactly one triggering `(Recommended)` option and **no
description on that option at all**, paired with a second, non-triggering
option whose own unrelated description happened to read
`"Falsified: totally unrelated evidence about something else — probe:
n/a → n/a; premise survives because n/a"`, silent-passed — because
single-label mode accepts "any clean `Falsified:` line anywhere in
`texts`" and does not check which option contributed it. The actual
triggering option's premise was never falsified; an unrelated sibling
option's unrelated text satisfied the gate on its behalf. Live probe
(`impl.py` fed that exact 2-option payload) → rc=0, empty stdout —
confirmed silent pass before this second fix.

The fix computes `combined_triggering` (the deduped T1+T2 triggering
label set) **before** collecting description evidence, then only appends
an option's `description` to the evidence pool when that option's own
(normalized) label is a member of `combined_triggering` — mirroring the
existing `"Falsified: {label}"` per-label ownership contract that already
governs multi-label coverage matching, just applied one step earlier (at
evidence-collection time, not only at coverage-checking time). A
non-triggering option's description is never evidence for anything; only
a triggering option's own description can supply its own evidence.
Regression test: "AskUserQuestion: non-triggering option's unrelated
Falsified: description does not cover a different triggering option →
still T1 gates (issue #828 codex round-2 P2)" in the test suite.

**Why matching must key on option identity (index), not normalized label
text (codex review round-3, third in-vivo P2 catch)**: the round-2 fix
still matched evidence ownership via `_normalize_label(label) in
triggering_norm` — a set of normalized label STRINGS. Two distinct options
can normalize to the identical string (e.g. `"Option  A"` with a doubled
internal space and `"Option A"` both collapse to `"Option A"` under
`_normalize_label`'s whitespace-run collapsing). When one such option is
the actual T2-triggering option (via a `safer`-style anchoring token, no
evidence of its own) and the OTHER, non-triggering option happens to share
the same normalized label, that sibling's unrelated `Falsified:`
description was accepted as if it belonged to the triggering option — the
triggering option's own premise was still never falsified. Live probe
(`impl.py` fed `options=[{label:"Option  A", description:"safer path
overall"}, {label:"Option A", description:"Falsified: unrelated ...
premise survives because n/a"}]`) → rc=0, empty stdout — confirmed silent
pass before this third fix.

The fix replaces label-string matching with two new index-keyed helpers,
`_t1_triggering_indices(options)` and `_t2_triggering_indices(options)`,
which record the POSITION of each triggering option within the `options`
list rather than its (normalizable) label text. Two distinct list
positions are never equal, so a same-labeled sibling can no longer donate
its description to a different option's evidence requirement. This
mirrors the round-1 fix's "label must never double as evidence" principle
one layer deeper: round-1 closed the case where a label IS its own
evidence; round-2 closed the case where a DIFFERENT option's description
covers the wrong evidence slot by omission; round-3 closes the case where
label TEXT COLLISION, not omission, misroutes a different option's
description into the wrong slot. The pre-existing label-based
`t1_triggering` / `t2_triggering` / `combined_triggering` machinery used
for messaging (scaffold text, ask reason) is unchanged — only the
evidence-collection step now reads option identity directly. Regression
test: "AskUserQuestion: non-triggering option sharing a normalized label
with the triggering option does not supply its evidence → still T2 ask
(issue #828 codex round-3 P2)" in the test suite.

#### AskUserQuestion T2: confidence-anchoring framing (issue #369)

T1's literal-marker scope leaves two bypass paths observed in practice:

1. **Confidence-anchoring framing without `(Recommended)` marker** — option
   labels/descriptions using `가장 안전한` / `safer` / `자연스러운` / `prefer this`
   etc. carry the same ranking signal but bypass T1.
2. **Description-field placement** — anchoring framing placed in
   `options[].description` (not label) bypasses T1's label-only scan.

T2 closes both: it scans `label` AND `description` for these tokens.

**EN token set** (case-insensitive, ASCII word-boundary lookarounds —
Python's `\b` is Unicode-aware and would misfire on Hangul-adjacent ASCII):

- Single-word: `safer`, `safest`, `clearly`
- Multi-word: `natural fit`, `natural choice`, `obvious choice`, `default to`,
  `default choice`, `prefer this`
- Bare marker: `recommend(?:ed|s)?`

**KO substring set** (plain substring — Hangul has no ASCII boundary issue):

`안전한`, `가장 안전`, `자연스러운`, `당연히`, `분명히`, `추천`, `기본값`

**Satisfaction**: identical to T1 — a `Falsified:` line in question body
silent-passes T2. The model can either remove the anchoring framing or add
the falsification line.

**Precedence**: T1 fires first when both could trigger (literal marker in
label + anchoring in description). T2's `ANCHORING_ASK_MSG` differs from
T1's `ASK_MSG` so downstream parsers can distinguish which tier escalated.

#### Message-visible `Falsified:` predicate (issue #910)

Both T1 and T2 messages expose the exact branch-aware format contract through
the same `FALSIFIED_FORMAT_HINT` text:

> [falsified-format] A clean evidence line starts at column 0. all modes: every
> scaffold-shaped line must have non-empty evidence with no unfilled placeholder.
> single-trigger: at least one clean line with the exact prefix 'Falsified:' is
> required. multi-trigger: within each question, one line per normalized full
> option label, including marker suffixes when present, such as
> '(Recommended)'/'(추천)'; each line must start exactly 'Falsified: {full option
> label}' and the label must be followed by end-of-line or ' — probe: '.

This distinction is load-bearing. With zero or one triggering label,
`_has_falsified_line()` preserves the issue #290 compatibility contract: any
clean column-0 `Falsified:` line is unambiguous and satisfies the question only
when no scaffold-shaped line retains empty or placeholder evidence.
With two or more triggering labels, every normalized full label must be covered
individually. Normalization collapses embedded newlines and whitespace runs;
the full label retains `(Recommended)` / `(추천)`. A prefix match counts only
when the label is immediately followed by end-of-line or the literal
` — probe: ` delimiter, so near-miss text such as `Run now` cannot cover the
triggering label `Run`.
Each question is evaluated independently, so identical normalized labels in
separate questions still require evidence in each question's own text or
triggering option description.

The fixed explanatory prose in both messages is English. The scaffold is the
only content-language exception: it preserves each user-supplied option label,
including localized text, while applying the predicate's whitespace
normalization. Translating a label would make the copy-paste-ready line fail
the full-label predicate it is meant to satisfy.

#### Bash: bulk-action mutation keywords

| Type | Patterns detected |
| ------ | ----------------- |
| English (regex, case-insensitive) | `close\s+all`, `delete\s+all`, `merge\s+all`, `reject\s+all`, `approve\s+all` |
| Korean (substring) | `전부 닫`, `모두 닫`, `전부 삭제`, `모두 삭제`, `전부 머지`, `모두 머지`, `다 머지`, `전부 클로즈`, `모두 클로즈` |

Bulk-action commands often reflect a downstream consequence of a proposal block
whose premise was not falsified ("close all linked issues" after a misframed
proposal). The advisory fires conservatively: only mutation-verb patterns are
matched; read-only commands (`git log --all`, `gh pr list`) do not fire.

#### Bash: blast-radius two-factor predicate (issue #1010)

**The gap, measured before the change.** The only falsification-requirement
discriminators in this hook were `_has_recommended_marker` /
`_t2_matching_labels` on `AskUserQuestion` and `_is_bulk_action_command`'s
five-verb `<verb> all` regex on `Bash`. There was no magnitude, prod-marker or
destructive-verb predicate anywhere, so a real production recursive delete
passed the entire `PreToolUse`/`Bash` group in silence. Verbatim, before:

```text
$ echo '{"tool_name":"Bash","tool_input":{"command":"aws s3 rm s3://prod-data-lake/ --recursive"}}' | impl.py
rc=0   stdout=[]   stderr=[]
$ echo '{"tool_name":"Bash","tool_input":{"command":"kubectl delete namespace production"}}' | impl.py
rc=0   stdout=[]   stderr=[]
$ echo '{"tool_name":"Bash","tool_input":{"command":"psql -c \"DROP TABLE prod.users\""}}' | impl.py
rc=0   stdout=[]   stderr=[]
```

(Positive control from the same run, proving the probe harness was live:
`close all open issues via gh cli` → rc=0 with the bulk advisory on stderr.)

**Definition.** The predicate fires when an **irreversible verb** co-occurs
with a **shared-surface token** *inside the same command segment*. There is
deliberately **no numeric threshold** and no recursive/wildcard weighting: an
object count is not knowable from the command text without executing it, and a
weighting scheme is not verifiable by inspection. A co-occurrence of two named
vocabularies is both testable and auditable, which is why the two-factor form
was chosen.

| Factor | Detected | Where it comes from |
| ------ | -------- | ------------------- |
| Irreversible verb (1a) | `delete`, `drop`, `truncate`, `purge` — ASCII word-boundary lookarounds, case-insensitive, matched per argv token so a verb quoted inside one token (`psql -c "DROP TABLE …"`) still counts | `delete` and `truncate` are `destructive-bash-guard`'s own vocabulary (`find … -delete`, `truncate -s 0`) and `side-effect-scan`'s `kubectl delete`; `drop`/`purge` are the catalog/queue equivalents of the same "content is gone" class |
| Irreversible verb (1b) | Recursive removal — an `rm` token (basename match, any argv position) plus a recursive flag (`-r`, `-R`, bundled `-rf`, `--recursive`) in the same segment | Mirrors `destructive-bash-guard`'s `_is_rm_recursive_force`, minus the force requirement |
| Shared surface | `prod` / `production` (bounded substring, so `/srv/prod/…` and `s3://prod-…` match); `s3://` / `gs://`; `namespace`; a DDL object keyword (`table`/`schema`/`database`) followed by a **dotted, schema-qualified** identifier | `prod`/`production` is `side-effect-scan`'s `PROD_LITERAL_TOKENS`; `namespace` is the surface its `kubectl-apply` reason already tells the caller to re-confirm |

Three scoping decisions are load-bearing, not stylistic:

- **Same-segment co-occurrence.** Detection walks `safe_tokenize` →
  `iter_command_starts` (the DESIGN.md structural-tokenization pipeline), so
  `rm foo.txt && grep -r bar prod_notes.md` does **not** fire — both factors
  are in the command string, but nothing irreversible touches anything shared.
  A whole-string scan would ask on it.
- **`rm` needs the recursive flag, not the force flag.** `-f` suppresses
  prompts; it is not what makes the delete irreversible, and `aws s3 rm
  --recursive` carries no force flag at all. `rm /srv/prod/one-file.txt` stays
  silent.
- **The schema/table surface requires qualification.** An unqualified `drop
  table tmp_rows` against a local scratch DB is precisely the routine case that
  must stay silent; a schema-qualified name (`prod.users`, `analytics.events`)
  means a shared catalog.

**Why `ask` and not the exit-0 stderr channel.** stderr on exit 0 is the
channel `hooks/_lib/_hook_io.py:88-92` documents as *not* model-fed ("stderr
with exit 0 only reaches the debug log"), and issue #874 measured that ADVISE
tier at 42 fires in one session with zero observed effect. `ask` is the only
channel that reaches the model, and it is how the sibling `side-effect-scan`
already gates `kubectl delete` (its `kubectl-apply` category sits at
`TIER_ASK`). The accepted cost is retry tax on a hook that already fires often
— which is the reason the predicate is kept to two named vocabularies rather
than widened.

**No satisfaction path, by decision.** Unlike the `AskUserQuestion` legs (a
`Falsified:` line silent-passes) and unlike `side-effect-scan` (a
`# side-effect:ack` comment short-circuits it), this leg has **no in-command
marker that silences it**. Approving the permission prompt *is* the
acknowledgement; a second, cheaper bypass would make the ask decorative. The
message says so explicitly under the `[no-satisfaction-path]` marker.

**Precedence over the bulk-action advisory.** When a command matches both this
predicate and `_is_bulk_action_command`, only the `ask` is emitted. They are
not two independent findings: the ask reason already carries the falsification
instruction the advisory would repeat, and the advisory's channel does not
reach the model, so emitting both would add a duplicate line to the debug log
and nothing else. One pre-existing test payload moved tiers because of this —
`aws s3 rm s3://bucket/ --recursive # delete all objects` was an advisory case
and is now an ask case; bulk-phrase-only coverage is preserved by a separate
fixture carrying no shared-surface token.

**Sibling-hook interaction (known, not mitigated).** `side-effect-scan` also
asks on `kubectl delete`, and `_dispatch.py:203-207` surfaces only the FIRST
ask on stdout. On a `kubectl delete namespace production` the two hooks
compete and whichever lands first wins the reason string; the decision is
`ask` either way, so no gate is lost, but this hook's reason may not be the one
the model reads. This is the same aggregation constraint issue #932 documents
for the `AskUserQuestion` verb checklist.

**Known false negatives (probed, accepted).** Each was reproduced against the
shipped `impl.py`; all exit 0 with empty stdout and empty stderr:

- `echo "prod" | xargs rm -r` — the surface token and the irreversible verb
  land in different pipeline segments, so same-segment scoping declines. This
  is the cost of the scoping rule that keeps `rm foo.txt && grep -r bar prod`
  quiet; widening to a whole-string scan trades this false negative for a
  false positive on far more common commands.
- `rm -rf "unterminated /srv/prod` — `safe_tokenize` skips a line it cannot
  parse (unmatched quote, runaway heredoc), which is the shared pipeline's
  documented fail-open behaviour, not something this predicate overrides.
- `rm -rf $(cat /srv/prod/list)` **does** fire, but only because the literal
  path inside the substitution carries `prod`. A command whose shared surface
  is only reachable through a variable (`rm -rf "$PROD_DIR"`) is invisible to
  any text-level predicate.

**Deliberate scope boundary — no new `AskUserQuestion` tier.** Blast radius is
added to the `Bash` leg **only**. `menu-mutation-tier-advisory` already asks on
the `AskUserQuestion` surface and its vocabulary is in flight (PR #1016);
adding a second asker there would double-fire on the same menu and make the
interaction unmeasurable. This is a boundary, not an omission — if the
`AskUserQuestion` surface needs blast-radius awareness, it belongs in
`menu-mutation-tier-advisory`'s existing tier table, not as a second gate here.

### Response shape

#### T1 Deny-escalation (AskUserQuestion with exact `(Recommended)` / `(추천)`, no `Falsified:`) — issue #393

**JSON to stdout** (message constant: `ASK_MSG`):

> Issue #682: message upgraded from instance-level ("add Falsified: and retry")
> to template-level — instructs Claude to bake the `Falsified:` line into the
> AskUserQuestion compose template for every future `(Recommended)` call, not
> just fix the current instance. Identified by the `[pre-author-template]` ASCII
> marker in the message body.

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "A '(Recommended)' marker is present, but neither the question body nor the triggering option's description contains clean evidence that satisfies the actual Falsified predicate. [falsified-format] A clean evidence line starts at column 0. all modes: every scaffold-shaped line must have non-empty evidence with no unfilled placeholder. single-trigger: at least one clean line with the exact prefix 'Falsified:' is required. multi-trigger: within each question, one line per normalized full option label, including marker suffixes when present, such as '(Recommended)'/'(추천)'; each line must start exactly 'Falsified: {full option label}' and the label must be followed by end-of-line or ' — probe: '. CLAUDE.md Self-Falsify Before Recommendation Lock rule. [pre-author-template] For this call and every future '(Recommended)' option, include a column-0 'Falsified: <verification result>' line in the AskUserQuestion composition template before invoking the tool; fix the template, not only this instance. 'Falsified:' must begin at column 0 of its own line (a startswith check); text embedded in prose, bullets, or code fences is not detected. [trigger-reduction] Keep the question short and place the 'Falsified:' line in the corresponding '(Recommended)' option's description; both locations are checked."
  }
}
```

#### T2 Ask-escalation (issue #369 — confidence-anchoring framing, no `Falsified:`)

**JSON to stdout** (message constant: `ANCHORING_ASK_MSG`):

> Issue #682: same template-level upgrade as T1. The `[pre-author-template]`
> marker identifies this as template-level guidance (not instance-level).

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "An option label or description contains a confidence-anchoring framing token (safer/safest/natural/obvious/clearly/default/prefer/recommend/안전한/자연스러운/당연히/분명히/추천/기본값), but neither the question body nor the triggering option's description contains clean evidence that satisfies the actual Falsified predicate. [falsified-format] A clean evidence line starts at column 0. all modes: every scaffold-shaped line must have non-empty evidence with no unfilled placeholder. single-trigger: at least one clean line with the exact prefix 'Falsified:' is required. multi-trigger: within each question, one line per normalized full option label, including marker suffixes when present, such as '(Recommended)'/'(추천)'; each line must start exactly 'Falsified: {full option label}' and the label must be followed by end-of-line or ' — probe: '. CLAUDE.md Output-Block-Level Falsification Gate. [pre-author-template] For this call and every future option that uses confidence-anchoring framing, include a column-0 'Falsified: <verification result>' line in the AskUserQuestion composition template before invoking the tool; fix the template, not only this instance. 'Falsified:' must begin at column 0 of its own line (a startswith check); text embedded in prose, bullets, or code fences is not detected. [trigger-reduction] Keep the question short and place the 'Falsified:' line in the corresponding option's description; both locations are checked."
  }
}
```

**Exit code:** `0`.

#### Ready-to-fill `Falsified:` scaffold (issue #787)

Both the T1 and T2 ask `permissionDecisionReason` above end with a
`[scaffold]` marker followed by one copy-paste-ready `Falsified:` line per
triggering option label, parsed straight from the blocked `tool_input`:

```text
[scaffold]
Falsified: <option label> — probe: <command> → <observed>; premise survives because <...>
```

One line is emitted per option that actually carried the marker/token that
tripped the tier (T1: the exact `(Recommended)`/`(추천)` label; T2: the
label or description matching an anchoring token) — so a question with two
triggering options gets two scaffold lines. The label is the only field
this hook can supply with certainty since it is already in the blocked call;
`<command>` / `<observed>` / `<...>` stay as placeholders — running the
actual probe is the intended cost this gate preserves, only the *format
reconstruction* cost is removed. No scaffold section is appended when
`Falsified:` is already present (silent-pass path).

When more than one option in the same question triggers the tier, one
`Falsified:` line is emitted per label. When more than one *question* in
the payload has a violation, labels from every offending question are
aggregated into the same scaffold (not just the first one encountered) —
otherwise fixing only the first question's line would still hit a second
block on retry for the un-addressed question.

#### Verb gate checklist (issue #873)

`Falsified:` is one of seven PreToolUse hooks registered on `AskUserQuestion`:
`block-ask-end-option` blocks unconditionally, `block-manufactured-action-menu`,
`pr-state-refetch-gate` and `merge-menu-review-options-advisory` block only
under their strict env vars, and `pre-output-falsification-gate` and
`memory-hint` never block. An author who
learns only about this one spends a further retry turn on the next, so both
ask paths (T1 and the anchoring path) emit
`verb_gate_checklist("AskUserQuestion")` from `hooks/_lib/block_message.py`,
the single source for the verb → gate mapping (see
[docs/hook/INDEX.md](../../../docs/hook/INDEX.md)).

**The checklist goes out on both channels (issue #932).** Neither one alone
reaches the model in every case:

- **decision JSON** (`_with_verb_checklist`, appended to the ask reason) — this
  is the channel that works for the common case, because this hook's decision
  is an `ask` and the dispatcher returns 0 for an ask
  (`hooks/_lib/_dispatch.py:203-207`). On exit 0 a PreToolUse hook's stderr is
  never fed to the model.
- **stderr** (`_emit_verb_checklist`, called from `_emit_ask` and
  `_emit_t1_ask`) — covers what the reason string cannot. All seven gates run
  in parallel on the same `AskUserQuestion` call, and `_dispatch.py:195-201`
  surfaces only the **first** deny's stdout, so a sibling denying first
  discards this hook's reason. That same deny makes the dispatcher exit 2 —
  exactly when stderr does reach the model.

Issue #873 shipped stderr-only, reasoning from the deny path where stderr
always arrives. The ask path was the majority case and the checklist went
nowhere. Issue #874 records the same exit-0 invisibility for the ADVISE tier
generally — 42 fires in one session, zero observed effect. The ask-message text
is otherwise unchanged.

The checklist names the `Falsified:` token but **indents** it. That is not
cosmetic: the token is only recognised at column 0, so an unindented line here
would let this hook's own output satisfy the predicate it is asking the author
to satisfy. `tests/test_block_message.py::test_verb_checklists_do_not_start_a_column_0_falsified_line`
pins that for every registered verb.

**Anti-bypass guard**: because the scaffold line itself starts with
`Falsified:`, a verbatim copy-paste that never fills in the placeholders
would otherwise satisfy the exact-prefix check with zero probe evidence —
defeating the falsification gate this hook exists to enforce.
`_has_falsified_line` rejects any line containing the literal placeholder
tokens `<command>`, `<observed>`, or `<...>` — and fails CLOSED for the
whole question if even one `Falsified:`-prefixed line still carries a
placeholder, even when a sibling line in the same question is already
filled in correctly (a question can have more than one triggering option,
hence more than one scaffold line — partially filling in only one must not
let the other ride along unchecked).

Placeholder-token rejection alone still leaves a gap: a line can be
**omitted entirely** rather than left with a placeholder, and an omitted
line has no placeholder text for the token check to catch. `_has_falsified_line`
therefore takes a `triggering_labels` parameter — the deduped list of
labels that tripped T1/T2 for that question — and requires that every
label in it is covered by its own matched `Falsified:` line. A question
with 2 triggering options and only 1 `Falsified:` line (the second
silently absent, not placeholder-marked) now fails the coverage check and
still denies. When 0 or 1 labels are triggering, ANY clean line satisfies
the question — preserving the original single-triggering-label contract
(issue #290) unchanged.

**Per-label coverage, not raw count (issue #787 codex round-9 P1):** a
prior version of this gate counted DISTINCT clean lines against
`required_count` without checking which triggering option each line
actually addressed — 2 differently-worded lines both about Option A
satisfied `required_count=2` for a 2-option question while Option B was
never verified at all. Each clean line is now matched to the specific
triggering label it addresses by checking whether it starts with
`"Falsified: {label}"` (the exact shape the scaffold seeds verbatim,
longest labels checked first so a shorter label can't shadow a longer
sibling that has it as a prefix); satisfaction requires every triggering
label to have at least one matched line, not just N distinct lines.

**Scan window (issue #787 codex round-5/6 P2)**: the scaffold seeds each
line's label segment verbatim from the actual option label (`Falsified:
{label} — probe: ...`). If an option's own label text happens to contain a
literal `<command>` / `<observed>` / `<...>` substring, scanning the whole
line for placeholder tokens would flag it as unfilled forever — no amount
of genuinely filling in the probe/observed content could ever satisfy the
gate for that label. The evidence a probe must supply lives after the
`— probe:` marker, so placeholder-token scanning is scoped to that region
only (`_PROBE_MARKER`); the label prefix, which is never evidence-bearing,
is excluded. A line WITHOUT the marker is not scaffold-shaped at all — it
is never scanned for placeholder tokens (round-6 P2 correction: round-5
still fell back to scanning the whole marker-less line, which continued to
falsely perma-block genuine free-form evidence whose own wording happened
to contain one of the three literal token strings without ever using the
`— probe:` phrasing; the actual scaffold copy-paste bypass this guard
exists to close always contains the marker, since it is emitted verbatim
by `_falsified_scaffold`, so exempting marker-less lines cannot reopen it).

**Duplicate-line dedup (issue #787 codex round-6 P2)**: `required_count`
counting (see above) previously counted raw clean lines, so a 2-option
question could be satisfied by pasting the SAME clean `Falsified:` line
twice — `Falsified:` lines are now deduped by exact text before counting,
so an identical duplicate contributes nothing toward `required_count` and
the second triggering option still needs its own distinct line.

**Per-question label scope**: dedup of aggregated scaffold labels applies
only WITHIN a single question (defends against one question listing the
same label twice), never ACROSS questions. `Falsified:` satisfaction is
checked per-question against that question's own text, so if two different
questions happen to present an option with the identical label text, each
still needs its own scaffold line — collapsing them to one would leave the
second question blocked on retry.

**Mixed T1+T2 tier pooling (issue #787 codex round-7 P2)**: a single
question can carry both an exact `(Recommended)` option (T1) and a
separate confidence-anchoring option with no marker at all (T2 — e.g. a
`safer` description). T1 and T2 triggering labels are computed
independently per question (not via `if`/`elif`) and pooled into one
combined `required_count` — the prior `if`/`elif` skipped the T2 check
entirely once T1 matched for the question, so a retry that only supplied
evidence for the T1 option silent-passed with the T2 option's claim never
verified. T1 still decides the final message precedence (unchanged);
only the per-question evidence requirement changed.

T2's bare `recommend(?:ed|s)?` pattern also matches the literal word
inside `(Recommended)`, so an option carrying the exact T1 marker
incidentally also matches T2's check. Labels already present in
`t1_triggering` are excluded from `t2_triggering` before pooling — without
this, a pure-T1 2-option question would double-count each label into both
`t1_labels` and `t2_labels`, producing duplicate scaffold lines in the
final gate message for a case with no genuine T2-only option at all.

**Real-delimiter anchoring + whitespace-normalized dedup (issue #787 codex
round-8 P2, two findings):**

1. **Label-embedded marker permablock.** The scaffold's own `— probe:`
   delimiter is always the LAST occurrence of that substring in the line —
   it is appended once, after the label, at scaffold-generation time. An
   option label that itself happens to contain the literal `— probe:`
   substring (e.g. a label quoting another probe's shape) previously
   shifted the scan to that earlier, label-internal occurrence via
   `line.find(_PROBE_MARKER)` (leftmost match), pulling trailing label
   text — and any placeholder token it contained — into `evidence_region`.
   That permanently hard-denied the line even after genuine evidence was
   filled in after the real, rightmost delimiter, with no way to ever
   satisfy the gate for that label. `_has_falsified_line` now uses
   `line.rfind(_PROBE_MARKER)` to anchor on the real, last-inserted
   delimiter instead.
2. **Whitespace-only duplicate bypass.** Exact-text dedup (round-6 P2)
   treated two lines differing only by whitespace (e.g. one trailing
   space, or a doubled internal space) as DISTINCT strings. Pasting the
   same real evidence line twice with a whitespace-only variation on the
   second copy satisfied `required_count=2` for a 2-option question while
   the second triggering option still had zero real evidence. Lines are
   now whitespace-normalized (`" ".join(line.split())` — strips
   leading/trailing whitespace and collapses internal runs to one space)
   before entering the dedup set, so a whitespace-only "duplicate" still
   collapses to the same clean line and contributes nothing toward
   `required_count`.

**Label-anchored coverage + delimiter matching (issue #787 codex round-9,
two findings):**

1. **Count-only verification silent-pass (P1).** The round-4/6/8 contract
   verified a multi-option question by counting distinct clean lines
   against `required_count` without ever checking which triggering option
   each line actually addressed. Two differently-worded lines both
   starting with `Falsified: Option A ...` satisfied `required_count=2`
   for a 2-option question while `Option B` was never verified — the
   count was right, the coverage was wrong. `_has_falsified_line` no
   longer takes a bare count; it takes the `triggering_labels` list
   directly and matches each clean line to a specific label via its
   `"Falsified: {label}"` prefix (longest labels checked first, so a
   label that is a prefix of a longer sibling label can't shadow it).
   Satisfaction now requires every triggering label to be covered by its
   own matched line, closing the coverage gap the raw count could not see.
2. **Evidence-embedded marker bypass (P2).** Round-8's `rfind()` fix
   assumed the scaffold's real `— probe:` delimiter is always the
   RIGHTMOST occurrence in the line, since it's inserted once, after the
   label, at scaffold-generation time. That assumption breaks when the
   EVIDENCE text itself (legitimately placed after the real delimiter)
   contains a second literal `— probe:` substring — `rfind()` then
   anchors on that spurious, later occurrence, pushing an actual unfilled
   placeholder sitting before it (but still inside the real evidence
   region) outside the scanned window. Now that each line is matched to
   its triggering label first, the delimiter is located via
   `line.find(_PROBE_MARKER, label_end)` — the FIRST `— probe:`
   occurrence strictly after the matched label's own text — which is
   structurally always the real, scaffold-inserted delimiter regardless
   of how many more `— probe:`-shaped substrings appear later in the
   evidence. This also still closes round-8's original label-embedded-
   marker case, since the scan now starts after the whole label prefix
   rather than depending on which occurrence is textually first or last.
   A line matching no known triggering label (true free-form text, or
   referencing an option outside the current label set) falls back to the
   prior `rfind()` behavior, since there is no label boundary to anchor
   on for that line.

**Label-boundary false match (issue #787 codex round-10 P2):** round-9's
`line.startswith(f"Falsified: {label}")` matched a label as a bare
substring prefix, not a whole-token prefix. With triggering labels
`["Run", "Other"]`, a line reading `Falsified: Runtime behavior verified
— probe: ...` matched the `"Run"` prefix (since `"Runtime"` starts with
`"Run"`) and credited `"Run"` as covered even though the line never
actually addresses that option — the real `"Run"` claim stayed
unverified while the multi-option coverage gate silently passed. A label
match now also requires a boundary immediately after the prefix: either
the line ends there, or the next character is non-alphanumeric (the
scaffold's own `" — probe: "` continuation, or any other non-identifier-
continuing delimiter). `"Runtime"` no longer satisfies `"Run"` because
`"t"` is alphanumeric. This surface is most reachable for bare (no
`(Recommended)`/`(추천)` suffix) T2 labels, since a label ending in the
closing paren of an exact marker already has an inherently safe boundary
in practice.

**Newline-in-label scaffold splitting (issue #787 codex round-11 P2):**
`_falsified_scaffold` interpolates each triggering option's raw label
verbatim into a single f-string line (`f"Falsified: {label} — probe:
..."`). If the label itself contains a literal newline, that single
logical line prints as two PHYSICAL lines in the ask message. A
verbatim copy-paste of that unfilled scaffold then has its `Falsified:`
prefix on the first physical line and its placeholder-bearing evidence
(`<command>`, `<observed>`, `<...>`) on the second — which does not start
with `Falsified:` and is therefore invisible to `_has_falsified_line`'s
per-physical-line scan, silently passing an evidence-free question.
Labels are now whitespace-normalized (`" ".join(label.split())` —
identical to the round-8 whitespace-normalization idiom, collapsing any
embedded newlines/whitespace runs to single spaces) at the single point
they enter the triggering pipeline (`_t1_matching_labels` /
`_t2_matching_labels`), before either scaffold generation or
`_has_falsified_line` matching consumes them — guaranteeing every
generated scaffold stays exactly one physical line per label regardless
of what the raw option label contains.

**Near-miss label prefix (issue #787 codex round-12 P2):** round-10's
non-alphanumeric boundary was still too permissive — a genuinely
DIFFERENT, unrelated evidence line that happens to share the triggering
label as its own prefix, separated by a space, also clears a
non-alphanumeric boundary check. With triggering labels `["Merge
(Recommended)", "Run"]`, a line reading `Falsified: Run now — probe:
...` (evidence that is really about a different `"Run now"` option, not
the triggering `"Run"`) matched `"Run"`'s prefix with `" "` as the
boundary, wrongly crediting `"Run"` as covered while the real `"Run"`
claim stayed unverified. The only boundary that reliably means "the
label ends here and evidence begins" is the scaffold's own delimiter: a
label match now counts only when the line ends exactly at the label
(remainder empty) or the remainder starts with the literal
`_PROBE_MARKER` string (`" — probe: "`) — not merely any non-alphanumeric
character.

**Empty scaffold evidence (PR #796 CodeRabbit review):** the `— probe:`
marker being present was treated as sufficient — a line reading
`Falsified: <label> — probe:` with nothing (or only whitespace) after the
marker satisfied the placeholder-token scan, because `any(token in "" for
token in _SCAFFOLD_PLACEHOLDER_TOKENS)` is vacuously `False` on an empty
`evidence_region`. Deleting everything after the marker, or moving the
placeholder tokens onto the next physical line, silently passed a
zero-content line. An empty or whitespace-only `evidence_region` is now
treated identically to an unfilled placeholder
(`not evidence_region.strip() or any(...)`).

**Missing-session_id telemetry undercounting (PR #796 CodeRabbit review):**
`_record_block_telemetry` early-returned before the RICH write whenever
`session_id` was absent, by design ("cannot attribute to a session") — but
this dropped the T1/T2 decision from `aggregate_fires()`'s
`fires`/`block`/`ask` totals entirely, which is worse than the original
coarse-duplicate bug the function exists to prevent (that bug mislabeled
the decision as `pass`; this one made it vanish from the count). Verified
`record_session_fire` already coerces a non-str `session_id` to `""`, and
`aggregate_fires` only adds a session to its per-session set when it is a
non-empty string — so the RICH record is now written unconditionally
(`session_id=""` when missing), preserving the correct `fires`/`block`/`ask`
counts without polluting per-session aggregation. Coarse-duplicate
suppression also moved to run unconditionally, as the function's first
statement, so it fires even on the missing-`session_id` path.

#### Bash blast-radius ask (issue #1010)

**JSON to stdout** (message constant: `BLAST_RADIUS_ASK_MSG`). `{verbs}` and
`{surfaces}` are filled with the matched vocabulary from the first satisfying
segment, so the reason names *which* two factors tripped it:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "[blast-radius] This command pairs an irreversible verb (rm -r) with a shared-surface token (prod, object-store URI) in one command segment — the two-factor blast-radius predicate (issue #1010). Run the output-block falsification gate on the plan that produced this command BEFORE running it: is its objective already addressed by in-flight work, a merged PR, or a parallel proposal in this session? If yes — STOP and cite the invalidating link instead of running the command. If the premise survives, confirm the exact target scope (bucket/prefix, namespace, schema-qualified table, path) is the one you intend — an irreversible verb on a surface someone else reads has no undo. [no-satisfaction-path] No in-command marker silences this gate; approving the permission prompt is the acknowledgement."
  }
}
```

**Exit code:** `0`.

#### Advisory (Bash bulk-action only)

Note: case-insensitive `(recommended)` alone previously emitted advisory
stderr; under issue #369 it is now caught by T2 (ask) before the
case-insensitive fallback fires. The advisory path remains for Bash
bulk-action keywords only.

**Advisory message** (emitted to stderr, never stdout):

```
[output-block-falsify-advisory] Surfacing a recommendation/bulk-action
proposal? Run the output-block falsification gate first: is the proposal's
premise already addressed by in-flight work, a merged PR, or a parallel
proposal in this session? If yes — STOP and cite the invalidating link
instead of surfacing the proposal.
```

**Exit code:** `0`.

### Block-event telemetry (issue #787)

Every T1 / T2 ask decision also appends one RICH record to the shared
fire-ledger (`hooks/_lib/_fire_ledger.py`, `record_session_fire`) — hook
`output-block-falsify-advisory`, role `advisory-nudge`, `decision` = `ask`
for both tiers since issue #899 (T1 recorded `block` while it emitted
`deny`), `tool` = `AskUserQuestion`, `session_id` from the hook
payload. Skipped when `session_id` is missing or empty (cannot attribute).

The Bash blast-radius ask (issue #1010) records the same way with `tool` =
`Bash`. `_record_block_telemetry` takes `tool` as a third parameter defaulting
to `AskUserQuestion`, so the two surfaces stay separable in
`aggregate_fires()` — they have different retry-tax profiles and a tier
decision for one must not be re-scored from the other's fire count.

This exists because this hook signals its decision via a stdout JSON
`permissionDecision`, not exit code 2 — so the universal `@fail_open` coarse
recorder (which only inspects the return code) always logs `pass` for these
calls. Before issue #787, cross-session block-rate measurement had to fall
back to grepping session transcripts for the block message string, which
over-counts sessions that merely quote the message in a retrospect report.
The RICH record gives an exact per-session count instead.

Storage/opt-out follow `_fire_ledger`'s existing contract: default
`~/.praxis/telemetry/fire-events-YYYY-MM-DD.jsonl`, override via
`PRAXIS_FIRE_TELEMETRY_FILE`, disable via `PRAXIS_FIRE_TELEMETRY_DISABLE=1`.

### Parsing guarantees

| Condition | Behavior |
| ----------- | ---------- |
| Malformed / missing stdin JSON | exit 0 (silent pass) |
| `tool_name` not `AskUserQuestion` or `Bash` | exit 0 (silent pass) |
| Missing `questions` / `options` / `command` fields | exit 0 (silent pass) |
| `python3` unavailable | exit 0 (shell shim guards) |
| Hook `.py` file missing | exit 0 (shell shim guards) |
| Any uncaught exception | exit 0 (silent pass, no crash) |

The hook uses no external dependencies (no PyYAML, no third-party packages).
All parsing is done with the Python standard library only.

### Tests

```bash
bash tests/hooks/advisory-nudge/test_output_block_falsify_advisory.sh
```

**Harness isolation (issue #787 codex round-6 P2)**: every `run_case`
invocation calls the hook directly, and the T1/T2 ask cases each write
a RICH telemetry record. The script exports a throwaway
`PRAXIS_FIRE_TELEMETRY_FILE` default at the top so these dozens of test
invocations never land in the real `~/.praxis/telemetry/` store — the
telemetry-content-checking cases further down still override the var
per-call to their own `TEL_DIR` paths, which wins for that one invocation.
Separately, the `pass` expectation in `run_case` now checks stdout is empty
in addition to stderr — an ask decision also exits 0 with empty
stderr (it signals via stdout JSON, not stderr), so a stderr-only check
could not actually distinguish a real silent pass from an undetected
the ask decision.

Covers 100 cases (97 pre-#910 + 3 new message-contract assertions):

**T1 ask-escalation (AskUserQuestion, issue #290 / #393 / #899):**

- Option label `(Recommended)` + no `Falsified:` in question body → `permissionDecision: ask` (ASK_MSG)
- Option label `(추천)` + no `Falsified:` → `permissionDecision: ask`
- Option label `(Recommended)` + `Falsified:` line present → silent pass
- Non-recommended option labels → silent pass

**Description-field evidence location (issue #828):**

- Option label `(Recommended)` + `Falsified:` line in that option's own `description` (not `question`) → silent pass, `question` stays short
- 2 triggering `(Recommended)` options, only one covered via `description`, the other has no `Falsified:` line anywhere → still gates (`ask`) (per-label coverage still enforced across mixed sources)
- T2 anchoring token (`safer`) + `Falsified:` line in the same option's `description` → `ask` → silent pass once evidence supplied
- Single `(Recommended)` option with no `description` at all, whose own `label` is crafted to read as a clean `Falsified:` line → still gates (`ask`) (regression for the self-referential label-as-evidence bypass caught by codex review — see spec detail above)
- Single triggering `(Recommended)` option with no `description` of its own, paired with a different non-triggering option whose unrelated `description` reads a clean `Falsified:` line → still gates (`ask`) (regression for the cross-option unrelated-description bypass caught by codex review round-2 — see spec detail above)

**T2 ask-escalation (AskUserQuestion, issue #369):**

- KO `가장 안전한` in `options[].description` + no `Falsified:` → `ask` (ANCHORING_ASK_MSG) — in-vivo regression from the session that motivated issue #369
- EN `safer` / `safest` / `prefer this` / `obvious choice` in label or description → `ask`
- KO `자연스러운` / `안전한` / `당연히` in description → `ask`
- Mixed Hangul/ASCII (`prefer this 옵션`) → `ask` — word-boundary regression
- `(Recommended)` in description-only (no marker in label) → `ask` (replaces former false-positive-guard pass)
- T2 anchoring + `Falsified:` line → silent pass
- T1 precedence: literal `(Recommended)` + anchoring description → `ask` with ASK_MSG (T1 wins, issue #393)

**T2 negative cases (must not fire):**

- `preferential treatment` (no token in set) → pass
- Bare `safe` (only `safer`/`safest` in set) → pass
- `unsafer` (word-boundary regression) → pass

**Pass (AskUserQuestion):**
- Option labels without any marker → silent pass
- Empty options → silent pass

**Advisory (Bash):**
- `merge all`, `close all`, `delete all` (English) → advisory emitted
- Korean: `모두 삭제`, `전부 머지`, `다 머지` → advisory emitted

**Pass (Bash):**
- `git status`, `gh pr list --state open` (read-only) → silent pass
- `git log --all` (--all flag, no mutation verb) → silent pass
- `disclose all`, `enclose all` (word-boundary regression) → silent pass

**Blast-radius two-factor predicate (issue #1010)** — both directions are
pinned; a one-directional suite would let the opposite error in (the predicate
silently stops firing on prod deletes, or it creeps outward and starts asking
on `rm -rf node_modules`).

*Must ask (each returned rc=0 with empty stdout AND empty stderr before #1010):*

- `aws s3 rm s3://prod-data-lake/ --recursive` → ask (`rm -r` + prod + object-store URI)
- `gsutil -m rm -r gs://artifacts-archive/` → ask (`rm -r` + object-store URI, no prod token needed)
- `kubectl delete namespace production` → ask (`delete` + namespace)
- `psql -c "DROP TABLE prod.users"` → ask (`drop` + schema-qualified object) — verb and surface both inside one quoted argv token
- `psql -c "TRUNCATE TABLE analytics.events"` → ask (`truncate` + schema-qualified object, no prod token)
- `rm -rf /srv/prod/uploads` → ask (`rm -r` + prod inside a path)
- `aws sqs purge-queue --queue-url https://sqs/prod-jobs` → ask (`purge` + prod)
- `aws s3 rm s3://bucket/ --recursive # delete all objects` → ask, not advisory (blast radius supersedes the bulk tier)

*Must stay silent:*

- `rm -rf node_modules`, `rm -r build/` → silent pass (the two routine-local controls the decision named explicitly)
- `sqlite3 /tmp/scratch.db "drop table tmp_rows"` → silent pass (verb alone; unqualified table name is not a shared surface)
- `truncate -s 0 /tmp/build.log` → silent pass (verb alone)
- `aws s3 ls s3://prod-data-lake/`, `kubectl get pods --namespace production` → silent pass (surface alone; reading prod is not a blast radius)
- `rm foo.txt && grep -r bar prod_notes.md` → silent pass (segment-scoping regression — both factors in the string, different segments)
- `rm -rf ./products/cache`, `rm -rf ./reproduce-case` → silent pass (`prod` word-boundary regression)
- `rm /srv/prod/one-file.txt` → silent pass (`rm` without a recursive flag)
- `rm -- -r /srv/prod/weird` → silent pass (POSIX `--`: `-r` is a filename, not the flag)
- `gh issue list | xargs -n1 gh issue close  # delete all stale entries` → advisory, not ask (bulk tier preserved when no shared surface is present)

**Edge:**
- Malformed JSON stdin → exit 0, silent pass
- Empty payload → exit 0, silent pass
- Unknown tool name → exit 0, silent pass
- Non-string command (int / null) → exit 0, silent pass

**Template-level message cases (issue #682):**

- `(Recommended)` + no `Falsified:` → gate message contains `pre-author-template` ASCII marker
- confidence-anchoring (`safer`) + no `Falsified:` → ask message contains `pre-author-template` ASCII marker
- `(Recommended)` + column-0 `Falsified:` present → silent pass (regression — template-level message change must not break satisfaction)

**Exact predicate message cases (issue #910):**

- T1 with 2 triggering labels and generic free-form evidence → still asks, and the reason states the multi-trigger full-label contract
- T1 with 2 triggering labels whose evidence omits `(Recommended)` → still asks, and the reason states that marker suffixes are part of the full label
- T2 with 2 triggering labels and near-miss label prefixes → still asks, and the reason states the end-of-line / exact scaffold-delimiter boundary

**Ready-to-fill scaffold cases (issue #787):**

- T1 gate message embeds `Falsified: <label>` seeded from the triggering option label
- T1 gate message contains the `[scaffold]` marker
- T2 ask message embeds `Falsified: <label>` seeded from the anchoring-token-carrying label
- 2 options both carrying `(Recommended)` → 2 scaffold `Falsified:` lines (one per label)
- `(Recommended)` + `Falsified:` already present → no scaffold needed (silent pass, regression)

**Block-event telemetry cases (issue #787):**

- T1 ask → 1 RICH fire-ledger record with `decision=ask`, correct `hook`/`role`/`tool`/`session_id`
- T1 ask → the automatic COARSE `pass` duplicate is suppressed (1 total line in the telemetry file, not 2)
- T2 ask → 1 RICH fire-ledger record with `decision=ask`
- T2 ask → COARSE duplicate suppressed (1 total line)
- Silent pass → no RICH record written
- Missing `session_id` → the ask decision still fires on stdout, but no RICH record (cannot attribute)

**Multi-question aggregation cases (issue #787 codex round-1 P2 fix):**

- 2 separate questions, each with its own T1 violation, no `Falsified:` in either → both labels appear in the ask scaffold (not just the first)
- 2 separate questions, each with its own T2-only (anchoring) violation → both labels appear in the ask scaffold

**Anti-bypass placeholder-rejection cases (issue #787 codex round-2/3/4/5/6/7/8/9/10/11/12 + PR #796 CodeRabbit review fixes):**

- T1: copying the unfilled scaffold line verbatim into the question body does NOT satisfy the gate → still gates
- T2: same unfilled-copy-paste check → still ask
- T1: scaffold line with all placeholders replaced by real probe output → silent pass (regression — the fix must not make legitimate filled-in evidence unsatisfiable)
- T1: 1 of 2 scaffold lines filled in, the other still carries a placeholder → still gates (round-3 P1 — a sibling clean line must not let an unfilled one ride along)
- T1: both scaffold lines for a 2-option question fully filled in → silent pass (regression)
- 2 different questions presenting the identical triggering label → 2 separate scaffold lines, not collapsed by dedup (round-3 P2)
- T1: 1 of 2 triggering options has a `Falsified:` line, the other is omitted entirely (no placeholder left anywhere) → still gates (round-4 P1 — placeholder-token rejection alone cannot catch a deleted line; fixed via `required_count`-based counting)
- T1: single triggering label with its one clean `Falsified:` line → silent pass (regression — `required_count` defaults to 1, preserving the original single-label contract)
- T1: option label literally contains `<command>`, evidence past `— probe:` genuinely filled in → silent pass (round-5 P2 — the label prefix is never evidence-bearing, so it must not be scanned for placeholder tokens)
- T1: option label literally contains `<command>` AND the evidence past `— probe:` is still unfilled → still gates (regression — round-5's narrowed scan window must not disable the guard itself)
- T1: legacy free-form line with no `— probe:` marker whose own wording contains `<command>` → silent pass (round-6 P2 — round-5 still scanned marker-less lines whole; marker-less lines are excluded from placeholder scanning entirely since they are not scaffold-shaped)
- T1: same clean `Falsified:` line pasted twice for a 2-option question → still gates (round-6 P2 — raw line-count could be satisfied by duplicating one option's evidence; lines are now deduped by exact text before counting)
- T1: 2 genuinely distinct clean lines for 2 options → silent pass (regression — dedup must reject only exact-text duplicates, not require exact label-text matching)
- Mixed T1+T2 in one question, only the T1 option's evidence provided → still gates (round-7 P2 — the prior `if`/`elif` skipped the T2 check entirely once T1 matched, so the T2 option's claim never entered `required_count`)
- Mixed T1+T2 in one question, both options' evidence provided → silent pass (regression)
- Pure-T1 2-option question → scaffold shows each label exactly once, no duplication (regression — round-7's self-catch: T2's bare `recommend` pattern also matches inside `(Recommended)`, so T1-covered labels must be excluded from `t2_triggering` or they double-count into both `t1_labels` and `t2_labels`)
- T1: option label itself embeds the `— probe:` delimiter substring plus a placeholder token, real evidence supplied after the actual (rightmost) delimiter → silent pass (round-8 P2 — `find()`'s leftmost match anchored on the label-internal marker instead of the real, last-inserted one, pulling the label's own placeholder text into `evidence_region` and permanently hard-gating; fixed via `rfind()`)
- T1: same real evidence line pasted twice for a 2-option question with only a whitespace difference (internal double-space) on the second copy → still gates (round-8 P2 — exact-text dedup treated the two as distinct, satisfying `required_count=2` while the second option had zero real evidence; fixed via whitespace normalization before dedup)
- T1: 2 differently-worded `Falsified:` lines, both prefixed with the SAME triggering label, the other triggering label never addressed → still gates (round-9 P1 — raw distinct-line count satisfied `required_count=2` without checking which option each line actually addressed; fixed via per-label prefix matching and coverage)
- T1: 2 triggering options, each addressed by its own distinctly-worded `Falsified:` line → silent pass (regression — the round-9 P1 fix maps lines to labels by prefix, it does not require identical wording or more than one line per label)
- T1: evidence text (after the real `— probe:` delimiter) itself contains a second `— probe:` substring, with an unfilled `<command>` placeholder sitting before that spurious second marker → still gates (round-9 P2 — `rfind()` anchored on the spurious, later marker instead of the real one, pushing the leading placeholder outside the scanned region; fixed via `find()` seeded right after the matched label prefix)
- T1: same evidence-embeds-a-second-marker shape, but with the evidence genuinely filled in (no placeholder anywhere) → silent pass (regression — the round-9 P2 fix narrows the anchor point, it does not forbid legitimate evidence from containing the word sequence `— probe:` again)
- T1+T2: a bare (no-marker) triggering label is a literal string-prefix of unrelated evidence text on a sibling line (e.g. `"Rerun"` inside `"Rerunning without confirmation"`) → still gates (round-10 P2 — `startswith()` matched the label as a bare substring prefix without a boundary check, crediting an option that line never actually addresses)
- T1+T2: same bare-label-is-a-prefix shape, but the label is genuinely addressed by its own whole-token line → silent pass (regression — the round-10 fix requires a boundary after the label, it does not forbid the label from ever matching)
- An option label contains a literal embedded newline → the gate message's scaffold hint still renders as exactly one physical `Falsified:` line (round-11 P2 — the raw label is normalized before scaffold generation, so the artifact this whole guard depends on can no longer split across lines)
- Same newline-bearing label, the (now-guaranteed single-line) scaffold copied verbatim and left unfilled → still gates (regression — the fix must not make the placeholder guard unreachable for labels that originally contained a newline)
- A triggering label (`"Run"`) is the string-prefix of a genuinely different, unrelated evidence line's own text (`"Run now"`) separated by a space → still gates (round-12 P2 — round-10's non-alphanumeric boundary check was too permissive; the boundary must be the exact scaffold delimiter or end-of-line, not any non-alphanumeric character)
- Same near-miss shape, but the triggering label is genuinely addressed by its own line ending exactly at the scaffold's delimiter → silent pass (regression — the round-12 fix must not forbid the label from ever matching)
- T1: the `— probe:` marker is present, but nothing (or only whitespace) follows it on the same line → still gates (PR #796 CodeRabbit review — an empty `evidence_region` vacuously satisfied the placeholder-token scan since `any()` over an empty string is `False`)
- Missing `session_id` → the RICH telemetry record is still written, with `session_id=""`, and the COARSE "pass" duplicate remains suppressed (PR #796 CodeRabbit review — the prior early-return dropped the decision from `aggregate_fires()`'s totals entirely rather than merely mislabeling it)
