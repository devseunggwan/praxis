# Step 5 cross-checks and counters: 5d / 5f / 5h (codex-review-wrap)

Detailed procedure for [`../SKILL.md`](../SKILL.md) Step 5 sub-steps 5d
(sibling cross-check), 5f (diminishing-returns advisory), and 5h
(parent-truncates-child SoT enumeration audit). Execution order is owned
by the spine's *Execution order* list. Sibling references:
[`step5-premise-verification.md`](step5-premise-verification.md) (5a/5b/5c/5g),
[`step5-approval-and-rounds.md`](step5-approval-and-rounds.md) (5e/5i/5j).

## 5d. Cross-check sibling implementations (when applicable)

When the PR under review is a port / parallel hotfix / A/B implementation
of logic that exists in another PR or another repo, each fact-modifying
finding that passed Step 5b verification must additionally be tested
against the sibling implementation.

### 5d-i. Identify sibling implementations

At the **start of Step 5** — immediately after the `rounds_per_region:` counter
update (5f counter, which runs first) and before classifying findings (5a) —
surface:

```
AskUserQuestion: "이 PR이 다른 PR/레포의 port · parallel hotfix · A/B 구현체인가요?
그렇다면 형제 구현체를 알려주세요."
```

Additionally, auto-detect sibling signals:

| Signal source | Detection |
| --- | --- |
| PR body keywords | `Companion`, `Refs #N`, `Mirror of #M`, `Port of`, `Parallel` |
| Commit message citations | References to a sibling PR number (`#N`) in the commit body |
| `git worktree list` | Two conceptually-paired branches (e.g., same issue prefix, `*-shell` / `*-python`) |

If no sibling is identified (user confirms "No", no auto-detect signal fires), skip 5d entirely.

**Record the answer either way.** Write one `sibling-id:` ledger row per
**invocation target** — `sibling={ref[,ref…]}` when siblings were
identified, `sibling=none` when they were not:

```text
sibling-id: target={worktree-path}#{branch} | round={N} | sibling={ref[,ref…] | none}
```

The "none" row matters as much as the positive one: a round re-entered by
5j reads this row instead of re-asking, and a missing row is
indistinguishable from "never asked". Ask on the first round **of this
target**; later rounds against the same target reuse the recorded answer.
A row whose `target=` differs is another PR's answer and must not be
reused — reusing it would skip 5d-i for the second PR of the session and
cross-check it against the first PR's sibling.

### 5d-ii. Apply falsifiable tests to sibling

For each fact-modifying finding that passed Step 5b:

1. Construct the **same falsifiable test** used in 5b (identical input, invocation, or query).
2. Apply it against the sibling implementation (sibling worktree path, sibling repo branch).
3. Record the result in the session ledger (extends the 5c ledger format with `sibling-applied:` rows):

```
sibling-applied: {sibling-repo}#{PR-or-branch} | round={N} | finding={brief-label} | result={same defect | different | does not apply}
```

**Result semantics:**

| Result | Meaning |
| --- | --- |
| `same defect` | Sibling exhibits the identical root-cause failure — sibling PR also needs the fix |
| `different` | Sibling has a variant or no equivalent code path — no cross-fix needed |
| `does not apply` | The finding's context (file, function, identifier) does not exist in the sibling |
| `inaccessible` | Sibling branch/repo could not be reached locally — cross-check skipped; user warned |

### 5d-iii. Propose sibling fix (same defect only)

When `result=same defect`:

1. Draft the equivalent edit for the sibling PR.
2. **Surface to the user before applying** — the sibling PR has its own approval scope separate from the current PR:

```
⚠ 형제 구현체 동일 결함 발견:
   현재 PR: {current-repo}#{current-PR} — finding: {brief-label}
   형제 PR:  {sibling-repo}#{sibling-PR} — 동일 결함 확인 근거: {falsifying test output}

제안된 수정: {draft-edit-summary}
형제 PR에 적용할까요? (이 PR과 별도의 승인이 필요합니다)
```

3. Record the outcome in the ledger:
   - Applied → append `fix-applied: yes` to the `sibling-applied:` row
   - User declined → append `fix-applied: declined` to the `sibling-applied:` row

Do NOT apply any sibling edit without explicit per-PR user approval. Approval for
the current PR does not transfer to the sibling PR.

## 5f. Diminishing-returns advisory — rounds-per-region counter

Repeated rounds on the same file region are a signal that the upstream
surface enumeration is incomplete. This sub-step tracks how many rounds
have touched each region and surfaces a non-blocking advisory when the
count reaches the configured threshold.

### Region label

For each finding, derive a **region label** using the nearest enclosing
context in the source file:

| File type | Region label |
| ----------- | ------------- |
| Markdown (`.md`, `.mdx`) | Nearest enclosing heading text at any level (`#` through `######`); H4+ headings are valid region labels because praxis skill bodies use `####` and `#####` for sub-steps (this skill's own body did before the references/ split), and excluding them would leave deeply-nested findings unlabelled |
| Code (`.py`, `.ts`, `.sh`, `.js`, …) | Enclosing function / class / method symbol name |
| Plain text / unknown | The file path alone (no region suffix) |

Region label format: `{file}:{region}` — e.g.
`skills/codex-review-wrap/SKILL.md:Step 5` or
`hooks/codex-review-route.sh:parse_prompt`.

**Same-file collision tiebreaker**: when two distinct occurrences of the same
symbol name appear in one file (e.g., two functions both named `parse_prompt`),
append the 1-based occurrence index: `{file}:{region}:{occurrence}` (e.g.,
`hooks/codex-review-route.sh:parse_prompt:1` and
`hooks/codex-review-route.sh:parse_prompt:2`). Occurrence order follows
top-to-bottom source order. This suffix is added only when a collision exists —
unique names keep the plain `{file}:{region}` form.

### Counter update (every round)

At the **start of Step 5** — this is the **first action**, before the sibling
identification question (5d-i) and before classifying findings (5a) — append
one `rounds_per_region:` entry to the session ledger for each distinct
`{file}:{region}` pair touched by this round's findings:

```
rounds_per_region: {file}:{region} | round={N} | cumulative={C}
```

`cumulative` is the total number of rounds (including this one) that
have touched `{file}:{region}` in the current session.

### Advisory threshold

Read the threshold `threshold` from the environment at the **start of each
round** (during the counter-update step above). It is deliberately **not**
called `N`: 5c uses `N` for the session-wide round number, and reusing the
letter here would let `cumulative = threshold + 1` be misread as
`cumulative = current round + 1`. The read mechanism follows the same
convention as other `PRAXIS_*` env vars in this codebase — a Bash
parameter expansion with a default:

```bash
threshold=${PRAXIS_DIMINISHING_RETURNS_N:-4}
```

In Python hook contexts, the equivalent is
`int(os.environ.get("PRAXIS_DIMINISHING_RETURNS_N", "4"))` (consistent with
the `os.environ.get("PRAXIS_EXTERNAL_WRITE_STRICT")` pattern at
`hooks/advisory-nudge/external-write-falsify-check/impl.py:578` and the
`os.environ.get("PRAXIS_AUTHOR_EXEMPT_STRICT")` pattern at
`hooks/advisory-nudge/external-write-falsify-check/impl.py:591`).

**Mid-session change semantics**: the env var is read fresh at each round's
counter-update step; it is not cached at session start. If
`PRAXIS_DIMINISHING_RETURNS_N` changes mid-session (e.g., changed from 4 to 2
between round 3 and round 4), the new value takes effect from round 4 onward.
Prior rounds use the value that was in effect at their own round start — there
is no retroactive adjustment to already-recorded ledger entries.

When `cumulative` reaches `threshold + 1` (i.e., the session is starting
its `(threshold+1)`-th round on the same region), surface the following advisory
**once per `{file}:{region}` per session** — immediately after emitting
the `rounds_per_region:` ledger entry, before proceeding to 5a
classification:

```
Advisory: this is round {cumulative} on {file}:{region}. Findings to date suggest
the underlying surface enumeration may be incomplete. Consider pausing
to re-enumerate cases up-front before continuing.
```

The advisory is **informational only** — it does not block, does not
require user confirmation, and does not prevent edits from being applied.
Do not re-surface the advisory on subsequent rounds (round `N+2`,
`threshold+3`, …) for the same region — emit it exactly once at
`cumulative = threshold+1`.

### Interaction with flip detection (5c)

The rounds-per-region counter increments independently of flip detection.
A flip halt (5c) does not reset or suppress the counter. If a flip is
halted mid-round, the `rounds_per_region:` entry for that round is still
recorded (counter increments) but the advisory suppression rule still
applies (emit only at `cumulative = threshold+1`, not again on later rounds).

## 5h. Parent-truncates-child SoT enumeration audit <!-- [#395] -->

When the PR diff touches a parent document that inline-transcribes
enumerations owned by a sibling SoT (another skill body, a test definition
table, a routing matrix, a prerequisite list), truncation of that
enumeration is a systematic failure mode that round-by-round external review
catches one missing row at a time. This step collapses that N-round sequence
into a single pre-merge sweep.

Run this step **once per round, after every approved finding has been
applied** — findings the user left at `미적용` or `후속이슈` do not hold it
back — before the round ends. ("Round" is the unit defined at the top of
Step 5: one invocation of Codex review. Since 5j can re-enter Step 4
inside a single skill invocation, "per invocation" would be ambiguous.)

**Do not re-synthesize a finding the user already declined.** Before
emitting, scan the ledger for a `rejected:` row on the same
`{file}:{region}` that carries `reason: user:`, the `[#395]` marker,
**and the same cited sibling SoT** (`{sibling_file}:{sibling_section}`);
if one exists, stay silent for that one citation. Without this check a
declined synthesized finding is re-emitted every round — 5h is a
stateless sweep, so it cannot remember on its own — and 5c halts the
round as a re-proposal of a user-declined finding, every round.

The key needs all three parts because each one alone over- or
under-suppresses:

- `{file}:{region}` alone silences real truncations in that heading for
  the rest of the session — region labels are coarse (nearest enclosing
  heading), and one heading routinely cites more than one sibling SoT.
- adding `[#395]` separates a declined 5h finding from a declined Codex
  finding, since `rejected:` rows carry no provenance field — but two 5h
  findings under the same heading still collapse onto one key.
- adding the sibling SoT identity is what keeps those two apart, so
  declining the `Test definitions` truncation leaves the `Phase
  applicability` truncation under the same heading free to re-emit.

### Trigger — does the parent doc cite a sibling SoT?

Scan the parent document for any of the following citation signals:

| Citation signal | Examples |
| --- | --- |
| Another skill name referenced | `codex-review-wrap`, `cmux-delegate`, `retrospect` |
| Enumerated test IDs | `Test 1`, `Test 7`, `Test N-M` |
| Enumerated phase IDs | `Phase 0`, `Phase 1a`, `Phase 2` |
| Named enum cited | `kind:`, `state:`, `result=` with a listed subset |
| Matrix row reference | `row of <matrix-name>`, `routing matrix`, `prerequisite rows` |
| Prerequisite claim | `after phase N`, `requires step M`, `depends on step X` |

If **no** citation signal is found, skip 5h entirely — emit nothing.

### Audit procedure (per cited SoT)

For each citation signal found:

1. **Locate the source SoT** — find the table / enum / list / prerequisite
   block that owns the enumeration in the sibling document. Use `Read` on
   the sibling skill file or the referenced doc section.

2. **Count source rows** — record `source_count` = the number of distinct
   items in the sibling SoT.

3. **Count parent-transcribed rows** — record `parent_count` = the number
   of items the parent body cites at the same citation site.

4. **Compare** — compute the set difference: `missing = source_items − parent_items`.
   If `missing` is non-empty, the parent truncates or diverges from the sibling
   SoT. Emit a synthesized finding (see format below). If `missing` is empty,
   record match and skip to the next signal.
   If the sibling SoT cannot be located, emit an unresolved advisory (see
   below).

   > **Why set difference, not count equality**: count equality passes when
   > `parent` drops one source item and adds one stale/extra item simultaneously
   > (counts match but content diverges). Set difference catches this case.

### Synthesized finding format

```
⚠ SoT truncation detected — [#395]:
  Parent: {parent_file}:{heading_or_line}
  Sibling SoT: {sibling_file}:{heading_or_section}
  Source rows: {source_count}  |  Parent-cited rows: {parent_count}
  Missing: {list of missing items, derived from set difference}

Proposed resolution: extend the parent citation to include all {source_count}
items, OR replace the inline transcription with a reference link to the
sibling SoT so the parent can never drift again.
```

Carry the `[#395]` marker **and the cited sibling SoT** into the ledger.
When 5i returns `미적용` or `후속이슈` for a synthesized finding, the
resulting `rejected:` row's `reason:` field starts with `user:` and the
row includes both — that triple is the key the re-synthesis check above
reads:

```text
rejected: {parent_file}:{heading} | round={N} | [#395] SoT truncation vs {sibling_file}:{sibling_section} | reason: user: declined (round N)
```

The synthesized finding is treated as a **structural** finding (not
fact-modifying): it describes an omission in documentation, not a runtime
predicate. Apply Step 5a classification accordingly — it does not require a
premise check via 5b, but **does** flow through the normal apply/commit cycle
(5c flip detection, 5i approval). Being structural, it takes no
`Premise-Verified:` trailer — 5e is the single rule for trailer scope.

### Unresolved advisory (sibling SoT not locatable)

When the citation signal is present but the referenced sibling SoT cannot
be located (skill file not found, section heading has changed, external doc
URL):

```
Advisory — SoT reference unresolved [#395]:
  Parent: {parent_file}:{heading_or_line}
  Citation: "{verbatim citation text}"
  Could not locate sibling SoT — manual check recommended before merge.
```

This advisory is non-blocking; it does not prevent the review from completing.

### Authoring guidance (prevention, not just detection)

When authoring or editing a parent skill body, prefer **reference links**
over **inline transcription** whenever the source of truth lives in a sibling
document:

- **Prefer**: `See [Test definitions in child-skill](./child/SKILL.md#test-definitions).`
- **Avoid**: re-listing `Test 1`, `Test 2`, ..., `Test N` verbatim in the
  parent body.

Inline transcriptions are permissible only when the cited enumeration is
stable and small (≤ 3 items). For enumerations of 4+ items, or enumerations
expected to grow, use a reference link — each transcription site becomes a
permanent drift risk.

### Interaction with step 5b

The SoT audit is distinct from the per-finding premise verification in 5b:

- **5b** fires on Codex/BugBot findings after they are surfaced and classifies
  each finding's underlying factual claim.
- **5h** is a proactive sweep on the parent document itself, independent of
  whether Codex reported a SoT-related finding. It catches truncations that
  external review has not yet surfaced — the same root cause that would have
  produced the (threshold+1)-th round finding.

The two steps are complementary: 5b prevents bad fixes; 5h prevents missed
enumerations from reaching the next reviewer.
