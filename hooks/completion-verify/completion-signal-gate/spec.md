# Stop Hook Completion-Signal Retrieval Gate

Supported hosts: all

`hooks/completion-verify/completion-signal-gate/impl.py` fires on every `Stop` event and emits an
advisory (stdout `{"systemMessage": ...}` JSON) when the last assistant turn contains a completion-signal
phrase without an evidence-block indicator in the same turn, or when a
cross-plugin slash command is surfaced in the wrong repo context.

### Why this exists

The 2026-05-23 retrospect session identified `effective_repeat=6` for the
Loaded≠Retrieved family: completion-signal phrases ("실질적 수정은 없습니다
... 머지하셔도 무방합니다", "done", "all set") were authored without
same-turn verification evidence despite 5+ MEMORY.md entries accumulating
for the root cause. Two concrete events triggered this hook:

**Event 1 — Premature completion claim**: After codex-review-wrap ran on
PRs #388/#389/#390, the assistant reported "실질적 수정은 없습니다.
머지하셔도 무방합니다." for PR #389/#390 without running per-PR verification.
User push-back required re-running per-PR tests (15/15 PASS, 24→27/27).
`completion-verify.sh` did not catch this because its `CLAIM_PATTERNS`
require a narrower terminal-position match; this hook catches the broader
claim vocabulary.

**Event 2 — Cross-plugin slash command surfacing**: While working in the
praxis repo, the assistant surfaced `/release` (a laplace-dev-hub skill) as
an option. The flat `available-skills` namespace across multiple loaded
plugins means foreign-namespace commands get suggested without repo-context
filtering. This sub-rule emits an advisory when either form appears in
praxis cwd output:

1. A namespaced `/plugin:command` whose plugin prefix is a known foreign
   namespace (`laplace-dev-hub:release`, `oh-my-claudecode:ralph`, ...).
2. A **bare** `/command` whose slug is in the curated `_KNOWN_FOREIGN_SKILLS`
   set — the original Event 2 trigger (`/release` with no prefix). Scope is
   intentionally narrow to avoid false positives on `/bin`, `/usr`, and
   unrelated nouns; only high-confidence foreign skill slugs are listed.

See also: `completion-verify.sh` (hard-block Stop hook for narrower
completion-claim patterns), `hooks/advisory-nudge/output-block-falsify-advisory/impl.py` (PreToolUse
advisory for `(Recommended)` proposals).

References: issue [#392](https://github.com/devseunggwan/praxis/issues/392).

### What is detected

#### Rule 1 — completion-signal without evidence-block

When the last assistant turn's text contains a completion-signal phrase AND
no evidence-block indicator is present in the same turn, an advisory is emitted.

#### Rule 1b — GO verdict with unresolved-gap markers

When a GO / merge-readiness phrase appears in the same turn with an
unresolved-gap marker, an advisory is emitted.

This flags mixed outputs such as "ready to merge" and "미해소 항목 있음" appearing
together. The hook exits 0 and never blocks — it surfaces the contradiction so
the turn either drops the GO verdict or resolves the gap.

Negated GO phrases (`not ready to merge`, `머지 가능하지 않습니다`,
`문제 없음이 아닙니다`) are gap reports, not verdicts, and do not fire Rule 1b.
The English guard reuses the issue-#515 negation window preceding the match;
the Korean guard scans the following window and additionally recognises the
predicate-negation forms (`아닙`, `아니다`, `아니라`, `아님`) that the
completion-verb marker set does not cover.

**Both guards are adjacent-window only** — they read the 12 characters around
the match and nothing else, so a negation carried by a *different sentence*
never reaches them. `아니요. 지금 내놓으면 안 됩니다.` followed 838 characters
later by the table heading `PR 머지 가능 상태` is a real turn that fired: the
refusal is a full sentence, the GO match is an attribute name, and no
adjacent-window guard can connect them.

A separate guard covers that shape. When the turn's **first token** is a
standalone refusal interjection (`아니요` / `아니오` / `아뇨` / `No` / `Nope` /
`Not yet`), the verdict is NO-GO end to end and Rule 1b does not fire; every
later GO token is a label or a quotation of the phrase being withdrawn.

**The guard is deliberately narrow in both position and vocabulary, and
widening either reintroduces a false negative.**

- *Position* — the interjection must open the turn, not merely appear early.
- *Vocabulary* — predicate negations (`안 됩니다`, `아닙니다`) are excluded. They
  negate whatever clause they sit in, so `이 방법은 안 됩니다만 저 방법은 됩니다.
  머지 가능합니다. 다만 미해소 갭 1건` — a genuine contradiction — dies with them.
- *English symmetry* — a leading bare `no` is a determiner far more often than
  a verdict. `No blockers. 머지 가능 — 실환경 unverified` is precisely what Rule 1b
  exists to catch, and it has been silenced twice: once by the round that
  shipped Rule 1b, and once by the first draft of this guard (`^(no|nope|not
  yet)\b`). A positive control caught the second one.

That asymmetry is the reason the controls exist: **a false positive shows up in
the fire count and a false negative does not.** Widening the markers makes the
corpus number look better while the rule quietly stops working, so the six
positive controls in `test_rule1b_partial_negation_is_not_a_refusal` must be
re-run before any change to the marker set.

**Sample size** — the guard is validated at N=2 on the retention side. The
corpus holds two genuine fires (one unambiguous, one graded a weak positive by
the 2026-08-03 probe) and both survive the guard. But there are **zero**
observed outputs that open with a refusal interjection *and* carry a genuine
GO-over-gap contradiction, so the false-negative rate is unmeasured rather than
zero. If another genuine fire is observed, re-validate this guard against it.

**Rejected alternative** — treating `머지 가능 상태` as a noun phrase and
suppressing on the `상태` suffix. It catches the turn above, but the 2026-08-03
probe graded `4건 모두 머지 가능 상태입니다` a weak true positive, and the two are
character-identical. There is no lexical separator.

A gap the turn has already **graded** (`머지 차단 아님`, `non-blocking`,
`블로커 없음`) still fires. This is deliberate and measured, not an oversight:
over the local transcript corpus, suppressing graded gaps removes 15 of the 41
fires and takes **both** genuine fires with them, because each reports its PR's
own `mergeStateStatus: BLOCKED` in its body. The corresponding fixtures
in `tests/hooks/completion-verify/test_completion_signal_gate.py` pin the
graded-gap shape as **firing** for that reason.

Evidence does **not** suppress Rule 1b. A cited `$ command → output` line
answers "was anything verified"; an unresolved-gap marker in the same turn
states that something was *not*. Rule 1's evidence gate therefore does not
carry over — the contradiction stands regardless of evidence.

**Completion-signal phrases (EN, case-insensitive, ASCII word-boundary):**

| Phrase | Example |
| -------- | --------- |
| `no fixes needed` | "No fixes needed here." |
| `ready to merge` | "This PR is ready to merge." |
| `all set` | "All set." |
| `done` | "Implementation done." |
| `complete` | "Review complete." |

**Completion-signal phrases (KR, substring/regex):**

| Pattern | Example |
| --------- | --------- |
| `실질적 수정.*없` | "실질적 수정은 없습니다." |
| `머지하셔도` | "머지하셔도 무방합니다." |
| `완료\b` | "작업 완료." |
| `결함 없음` | "결함 없음." |
| `이상 없음` | "이상 없음." |

**Negation / progressive guard (issue #515):** a completion phrase under
negation or in a not-yet-complete status form does NOT count as a completion
signal — the assistant is reporting incompletion, not claiming done. English
matches are disqualified when a negation token (`not`, `n't`, `no`,
`never`, `without`, `yet to`, `isn't`, `won't`, `can't`, `cannot`, …) appears
in the 24 chars preceding the phrase ("not done yet", "this is not complete",
"not ready to merge"); progressive `-ing` forms are already excluded by the
ASCII word-boundary lookarounds ("completing" ≠ "complete"). The short markers
are stored with a trailing space (`"not "`, `"no "`) so that "notable" and
"nose" do not read as negations; the list above omits it for legibility.
Korean matches are disqualified when a negation form trails the token within
12 chars
(`되지 않`, `지 않`, `안 됨`, `안 돼`, `안 된`, `못 …`) or when `아직` precedes
it — so `완료되지 않았습니다`, `완료 안 됨`, `아직 완료 전입니다`, and
`완료하지 못했습니다` no longer trigger the advisory.

**Evidence-block indicators (any of these suppresses the advisory):**

| Indicator | What counts |
| ----------- | ------------- |
| Bash tool call | Any `tool_use` with `name == "Bash"` in the current turn |
| Read tool call | Any `tool_use` with `name == "Read"` in the current turn |
| Cited output | A `$ command → output` line in the assistant message |

**Unresolved-gap markers:**

| Marker | Meaning |
| --- | --- |
| `미해소` | unresolved item exists |
| `갭` | explicitly marked gap |
| `⚠` | warning marker |
| `검증 증거 부재` | evidence is explicitly missing |
| `not verified` / `unverified` | unresolved verification state (EN) |

A marker that is itself reported as resolved does not count — `미해소 항목 없음`,
`갭 없음`, `unverified: none` state the *absence* of a gap, and matching the
marker as a bare substring would turn a clean GO output into a contradiction.
The 10 chars following each marker are scanned for a resolution form (`없음`,
`없습니다`, `해소됨`, `none`, `resolved`, `cleared`) before the marker counts. The
English forms are stored with a leading space so they only match as a separate
word.

#### Rule 2 — plugin-context anchoring (Event 2)

Fires in either of two forms when the cwd's active plugin is `praxis`
(detected via `.claude-plugin/marketplace.json` or git remote slug):

1. **Namespaced form**: `/namespace:command` where the namespace is one of the
   known foreign plugins (`laplace-dev-hub`, `oh-my-claudecode`, `omc`,
   `codex`, `scheduler`, `gemini`, `laplace-wiki`).
2. **Bare form**: `/command` (no namespace) where the slug is in
   `_KNOWN_FOREIGN_SKILLS`. Conservative curated set scoped to slugs that
   are unambiguously foreign — `release`, `hub-bulk-release`, `hub-scan-issues`,
   `dev-to-prod-pr`. Add to the set in `hooks/completion-verify/completion-signal-gate/impl.py` when new
   high-confidence foreign skill names emerge; do not include ambiguous words.

### Response

Both rules emit a single stdout `{"systemMessage": ...}` JSON object per
invocation (messages joined with a newline when both rules fire; issue #647
H3 standardized the completion-verify role on stdout JSON — stderr with exit
0 only reached the debug log). No `decision` field is written to
stdout. The hook always exits 0 — it never blocks.

**Rule 1 advisory (systemMessage body):**

```text
[praxis:completion-signal-gate] completion-signal phrase detected in last turn without an evidence-block (Bash tool result, Read tool call, or cited '$ command → output' line).
[praxis:completion-signal-gate] Rule: CLAUDE.md 'Verification Before Completion' — run a real verify command (test/lint/build/probe) and paste its output BEFORE declaring completion.
[praxis:completion-signal-gate] Trigger: matched completion-signal token in last assistant turn. Add evidence or remove the completion phrase to suppress this advisory.
```

**Rule 1b advisory (systemMessage body):**

```text
[praxis:completion-signal-gate] go-verdict phrase detected together with unresolved-gap marker in last assistant turn.
[praxis:completion-signal-gate] Rule: CLAUDE.md 'Output-Block Falsification' — do not claim GO/merge readiness while unresolved gap markers are present in the same output.
[praxis:completion-signal-gate] Trigger: both a go-verdict phrase and unresolved-gap marker coexist in one turn.
```

**Rule 2 advisory (systemMessage body):**

```text
[praxis:completion-signal-gate] cross-plugin slash command(s) /laplace-dev-hub:close-hub-issue surfaced while cwd plugin is 'praxis'.
[praxis:completion-signal-gate] Rule: CLAUDE.md 'Plugin-context anchoring' — do not surface skill commands from foreign plugin namespaces. Verify you are working in the correct repo/plugin context before recommending slash commands.
```

**Exit code:** `0` in all cases.

### Tier: advisory (v1)

v1 is advisory-only — no `permissionDecision` JSON, no blocking. The advisory
fires as a `system-reminder` that Claude sees as additional context in the next
turn.

**Tier promotion path (deferred — follow-up issue):**

Once false-positive rates are measured over 1+ week of real sessions:

1. **ask tier**: add `permissionDecision: ask` JSON to stdout when Rule 1
   triggers. Claude must acknowledge before the Stop completes.
2. **block tier**: upgrade to `decision: block` (matching `completion-verify.sh`
   response shape). Appropriate only after ask tier validates low false-positive
   rate.

To promote, update `hooks/completion-verify/completion-signal-gate/impl.py` to emit:

```json
{"decision": "block", "reason": "..."}
```

to stdout (not stderr) and re-run `scripts/build-plugin-manifests.py`.
No change to `hooks/manifest.json` entry is required for tier promotion.

### Parsing guarantees

| Condition | Behavior |
| ----------- | ---------- |
| Malformed / missing stdin JSON | exit 0 (silent pass) |
| `stop_hook_active` is true | exit 0 (silent pass, re-entry guard) |
| Missing / unreadable `transcript_path` | exit 0 (silent pass) |
| Empty transcript file | exit 0 (silent pass) |
| No assistant messages in current turn | exit 0 (silent pass) |
| `python3` unavailable | exit 0 (shell shim guards) |
| Hook `.py` file missing | exit 0 (shell shim guards) |
| Any uncaught exception | exit 0 (silent pass, no crash) |

The hook uses no external dependencies (stdlib only: `json`, `os`, `re`,
`subprocess`, `sys`, `pathlib`).

### Relationship to `completion-verify.sh`

`completion-verify.sh` enforces a hard block when specific terminal-position
completion claims appear without same-turn Bash+evidence+paste (L1+L2+L3
triple gate). This hook is complementary:

- **Broader phrase vocabulary** — catches `ready to merge`, `no fixes needed`,
  `이상 없음`, `결함 없음` that `completion-verify.sh` does not match.
- **Weaker evidence gate** — any Bash or Read tool call suppresses this hook;
  `completion-verify.sh` additionally requires the evidence span to be pasted
  verbatim in the message (L2).
- **Advisory vs block** — this hook emits a `systemMessage` advisory only; the sibling
  hard-blocks. Both fire on the same Stop event, independently.

### SubagentStop (issue #1337)

The hook is also registered on `SubagentStop`, Claude-only (`hosts:
["claude"]`), which fires when a subagent finishes and "use[s] the same
decision control format as Stop hooks" (hooks reference, read 2026-09-06). A
subagent that reports completion is making the same claim on the same surface;
until this registration, nothing graded it.

Two payload differences decide what the hook reads, both handled by
`hooks/_lib/_transcript.py` (`resolve_stop_transcript`, `load_stop_turn`,
`stop_last_assistant_text`) so the three registered gates cannot drift:

| Field | On `Stop` | On `SubagentStop` |
| ------- | ----------- | ------------------- |
| `transcript_path` | the session's | the **parent** session's — reading it here grades the wrong conversation |
| `agent_transcript_path` | absent | the subagent's own, in a nested `subagents/` folder — the **only** transcript this hook will read on the event |
| `last_assistant_message` | final text, ahead of the lagging transcript | same, for the subagent |

`isSidechain` markers are dropped while the agent transcript is parsed: every
event in a per-agent file belongs to that agent, and the filter that keeps a
subagent's events out of the MAIN transcript would otherwise empty the turn
and pass every subagent silently.

**No fallback to the parent.** When the agent transcript is missing or
unreadable — not yet flushed, or a payload that carries no such key — the hook
reads *nothing* and passes. Falling back to `transcript_path` was the first
draft and it reinstated the defect this registration removes: the turn came
from the parent while the claim came from the subagent's
`last_assistant_message`, so a subagent that ran nothing and merely repeated a
number from the parent's output cleared the evidence check against evidence it
never produced. Grading one conversation's claim with another's evidence is
worse than not grading it.

Unchanged by the registration: `stop_hook_active` still ends the re-entrant
loop, and an unreadable transcript or an empty turn still passes. A claim is
never graded against evidence this run did not read.

Not measured live — see [`RUNTIME_CONSTRAINTS.md` entry 7](../../../RUNTIME_CONSTRAINTS.md).

### Tests

```bash
python3 -m pytest tests/hooks/completion-verify/test_completion_signal_gate.py -q
```

Covers 52 cases:

**Rule 1 trigger (15 phrases — EN + KR):**

- EN: `no fixes needed`, `ready to merge`, `all set`, `done`, `complete`
  (case-insensitive variants included)
- KR: `실질적 수정은 없습니다. 머지하셔도 무방합니다.`, `머지하셔도 됩니다`,
  `완료`, `결함 없음`, `이상 없음`

**Rule 1 suppression (3 evidence-block types):**

- Bash tool call in turn → suppressed
- Read tool call in turn → suppressed
- Cited `$ command → output` line → suppressed

**Rule 1b trigger (3 cases):**

- `보내도 됩니다` + `미해소`
- `문제 없음` + `검증 증거 부재`
- `ready to merge` + `not verified`

**Rule 1b silence (5 cases):**

- gap marker alone, no GO verdict → silent
- negated GO verdicts (`not ready to merge`, `ready to merge가 아닙니다`,
  `문제 없음이 아닙니다`) with a gap marker → silent
- GO verdict with cited output but **no** gap marker → silent (Rule 1 path)
- turn opening with a refusal interjection, GO token later as a status label
  → silent
- turn opening with a refusal interjection that quotes its own withdrawn GO
  phrase → silent

**Rule 1b corpus fixtures** (real turns, identifiers replaced):

- ungraded GO verdict followed by an unresolved gap → fires (regression guard;
  the unambiguous genuine fire in the measured corpus)
- GO verdict over an explicitly graded gap, two variants → fires
  (characterization — see the false-positive boundary above for why it is not
  suppressed)
- clause-scoped negation that is not a refusal → fires (six positive controls
  for the leading-refusal guard: `No blockers.` / `No further work needed.` /
  `ready to merge — 아직 …` on the English side, `안 됩니다만` / `아닙니다` /
  `안됩니다만` on the Korean side)

Cited evidence alongside a gap marker does **not** silence Rule 1b; a
regression case asserts it still fires.

**False-positive cross-checks (5 normal-completion samples):**

- FP1: Bash + evidence signal → no advisory
- FP2: No completion phrase → no advisory
- FP3: Read tool + completion phrase → suppressed
- FP4: Bash lint-clean → no advisory
- FP5: Mid-task assistant message (no completion phrase) → no advisory

**Event 1 reproduction:**

- Exact issue quote "실질적 수정은 없습니다. 머지하셔도 무방합니다." → advisory

**Rule 2:**

- Foreign `/laplace-dev-hub:close-hub-issue` in praxis cwd → advisory

**Fail-safe paths (4):**

- Malformed JSON stdin → exit 0
- `stop_hook_active: true` → exit 0
- Missing `transcript_path` → exit 0
- Empty transcript → exit 0

**Internal unit tests:**

- `_has_completion_signal`: 15 parametrized cases (true/false, EN/KR, word-boundary)
- `_has_evidence_block`: 4 parametrized cases
