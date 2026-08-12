# PreToolUse AskUserQuestion Menu Mutation-Tier Advisory

Supported hosts: all

The hook implementation lives at
`hooks/advisory-nudge/menu-mutation-tier-advisory/impl.py`; the build generates
the dispatcher `hooks/menu-mutation-tier-advisory.sh` that the platform
`hooks.json` invokes. It fires on every PreToolUse(AskUserQuestion) event and
inspects `options[].label` + `options[].description`. When a question's
*candidate* options sit in the mutating blast-radius tier and none names a
non-mutating alternative, it emits an advisory asking the agent to add one
low-blast option — or to state, on a `Safe-tier-unavailable:` line in the
question body, why prod is the only path.

### Why this exists

A menu fixes the axis; the user picks a point on it. When the whole axis mutates
shared state, the user has chosen **how much**, never **whether** — so the
consent is not intact, because the safe tier was never a candidate.

**2026-08-11 incident (issue #963).** A prod DAG-trigger approval menu offered
`두 소스 다 트리거 (Recommended)` / `한 건만 먼저` / `다음 정기 실행에 맡김`. All
three targeted the live customer tenant. `preview` / `dev` / sandbox were never
enumerated, and the 118 lines preceding the approval request mentioned preview
zero times — even though the project's own rules said Airflow e2e verification
runs on preview, and the same session later ran that class of verification on
preview without incident. The approved run made a prod Iceberg table permanently
unqueryable; the user had to `DROP TABLE` by hand.

This is the third axis on `AskUserQuestion`, alongside two siblings that inspect
the same payload for different defects:

| Hook | Axis | Question it asks |
| ------ | ------ | ------------------ |
| `block-manufactured-action-menu` | manufacture | Is this menu *redundant* — did the user already give the directive? |
| `merge-menu-review-options-advisory` | completeness | Does a merge gate offer a *review lever*? |
| `menu-mutation-tier-advisory` (this) | blast radius | Do the options span more than one *mutation tier*? |

**Why a new hook rather than an axis on an existing one.** The tier predicate is
independent of both siblings' predicates and cannot reuse their control flow.
`block-manufactured-action-menu` early-returns on `_has_manufactured_marker`
before anything else runs, and the incident menu carries no manufactured marker
at all — so the tier axis would have to be bolted in *ahead* of that gate,
sharing only the label extractor while needing none of its transcript read. It
is also a `preflight-gate` that can block, whereas this axis must never block by
default (prod genuinely is the only path sometimes; what is owed then is a
stated reason, not a denial). `merge-menu-review-options-advisory` is the closer
precedent and is followed structurally: same event and matcher, its own
directory, payload-only detection, advisory default with opt-in strict.

### Abandonment is not a tier

The issue left one question open: does an option that *does nothing*
(`다음 정기 실행에 맡김`, `하지 않음`, `대기`, `skip`) count as the non-mutating
tier? **It does not**, and the incident is why. That menu had such an option and
it did not function as a tier choice — it was the choice to give up on verifying,
not a cheaper way to verify. Counting it as safe would make this hook silent on
the exact menu it exists for.

So abandonment is its own class, and it cuts both ways:

- it does **not** suppress the advisory (the remaining candidates are still
  uniformly mutating, and the user still has no safe way to verify);
- it does **not** count as a candidate either, so a binary go/no-go menu
  (`squash 머지` / `대기`) leaves exactly one real candidate and does not fire —
  that menu is a genuine "whether", which is what this hook wants to preserve.

The issue's draft signal list placed `하지 않음` under low-blast. That is the one
place the implementation deliberately diverges from it; the open question is the
issue asking for exactly this call. `보고만` stays low-blast: a report-only pass
is a real verification that touches nothing.

### Fire predicate

Evaluated **per question**, and the hook fires if any question triggers. Pooling
questions would let one question's `preview` option cover another question's
all-prod menu.

For one question's option set, in the order the early-returns run:

0. the question body carries no `Safe-tier-unavailable:` line (see below);
1. **abandonment options are dropped first** — they are neither a candidate nor
   a safe tier, so neither question is asked of them;
2. no remaining candidate is **low-blast** — carries a low-blast signal *and*
   names no high-blast target. Any one such candidate suppresses;
3. at least **2 candidates** remain;
4. at least **1 candidate** carries a **mutation** signal.

Step 1 runs before step 2 because the two classes overlap: `skip this run and
review later` carries an abandonment token **and** the low-blast `review`.
Reading low-blast first let that single option suppress an otherwise all-prod
menu — abandonment being neither class means it has to be removed before either
question is asked of it.

**Divergence from the issue's wording, stated deliberately.** The issue says
*every* option must carry a mutation signal. That is not decidable lexically: a
scope-reduction option (`한 건만 먼저`) inherits its verb from a sibling option
and carries no token of its own, so an all-must-match predicate is silent on the
incident menu in the issue's own background section. The implemented predicate
takes the mutation signal from at least one candidate and treats the *absence of
any low-blast option* as the defect — which is the property the issue's headline
argument is actually about.

### Stating that no safe tier exists — `Safe-tier-unavailable:`

The advisory asks for one of two things: add a low-blast option, **or** say why
one is impossible. Without a way to record the second, the hook has no
satisfying path and strict mode becomes a gate nothing can pass except an extra
option — so the second is in-band:

```text
Safe-tier-unavailable: preview 클러스터에 이 소스의 커넥터가 없다
```

The shape mirrors the sibling `output-block-falsify-advisory`'s `Falsified:`
marker exactly: the literal prefix at **column 0 of its own line** in the
question body (`question` or `header`), matched with `startswith`, with
non-empty text after the colon. A mid-line mention, a bullet, a fenced block, or
an empty marker does not count — which is also what makes the reason visible to
the person the menu is for, rather than a token that only satisfies a hook.

The body is read as prose first: fenced blocks (backtick or tilde, any fence
length) and HTML comments are removed before the marker is looked for, and an
unterminated fence swallows the rest of the body. Without that, documenting the
marker suppresses the advisory that asks for it — a fenced example's content
sits at column 0 exactly like a real marker. A marker following a *closed* fence
still counts; the fixtures pin both directions.

Read **per question**: a reason on question 1 does not cover an all-prod
question 2.

### Detect patterns

Each option's `label` and `description` are joined and classified — the issue's
SOT says the signal may live in either ("옵션 라벨/설명에서 mutation 티어 신호를
추출"), and an elliptical label routinely carries its target only in the
description.

English tokens use **ASCII-letter lookaround** (`(?<![a-z])prod(?![a-z])`), the
repo's whole-token mechanism: `\b` would not split `prod실행`, because Python's
Unicode-aware `\w` treats Hangul as a word character. So `merged` / `merger` /
`deployment` do not match `merge` / `deploy`, while `prod 트리거` and `dry-run`
do. Korean tokens are **substring**-matched, the convention both siblings on this
event use (CJK has no ASCII word boundary); collision is controlled by preferring
multi-character phrases over bare verbs — `실행` is deliberately absent, since it
occurs inside the abandonment label `다음 정기 실행에 맡김`.

#### Tier 1 — mutation / high blast radius

Split into **targets** and **verbs**, because a low-blast token means different
things beside each (see the suppression rule below).

**Tier 1a — high-blast targets** (the shared surface itself):

- English (lookaround): `prod`, `production`
- Korean (substring): `프로덕션`, `실 고객`, `실고객`, `실제 고객`, `운영 환경`,
  `운영환경`

**Tier 1b — mutation verbs** (changes shared state, wherever it points):

- English (lookaround): `deploy`, `trigger`, `merge`, `delete`, `drop`,
  `truncate`, `apply`, `backfill`, `overwrite`, `publish`, `push`, `send`,
  `notify`, `broadcast`, `announce`, `submit`
- Korean (substring): `트리거`, `머지`, `삭제`, `배포`, `적용`, `덮어쓰`, `백필`,
  `전송`, `발송`, `공지`, `게시`

The external-write verbs (`send` / `notify` / `전송` / `공지`) are Tier 1
because a Slack post or an email is a shared-state mutation with no rollback —
a menu offering only `Send Slack now` / `Send email instead` is this same defect
in different vocabulary. They stay verb-anchored: the nouns (`slack`, `email`)
also occur in read-only options ("read the Slack thread") and would misfire.

#### Tier 2 — non-mutating / low blast radius (suppresses)

- English (lookaround): `preview`, `dev`, `development`, `staging`, `sandbox`,
  `dry-run`, `dryrun`, `dry run`, `local`, `mock`, `simulate`, `simulation`,
  `read-only`, `readonly`, `report-only`, `plan-only`, `no-op`, `noop`,
  `review`, `reviewer`, `codex`, `critic`, `audit`, `inspect`
- Korean (substring): `프리뷰`, `미리보기`, `개발 환경`, `개발환경`, `개발 서버`,
  `개발계`, `샌드박스`, `스테이징`, `드라이런`, `보고만`, `조회만`, `읽기 전용`,
  `읽기전용`, `시뮬레이션`, `로컬`, `리뷰`, `검토`, `점검`

**A low-blast token only suppresses when the option names no high-blast
target.** A compound option — `Dry-run then deploy to prod`,
`preview 확인 후 prod 트리거` — names the safe step and then the prod one, and
ends on the shared surface either way; one such option would otherwise silence
the advisory for the entire menu, which is this hook's central failure mode
rather than a corner case. A mutation *verb* does **not** disqualify an option:
`deploy to dev first` is a mutating verb aimed at a safe surface, which is
precisely the alternative the hook asks for. Only a Tier 1a **target** does.
`development` is listed explicitly because the lookaround is whole-token and
`dev` does not match inside it.

Review / inspection levers are in this tier on purpose: re-running a review
verifies without touching the shared surface, which is precisely the cheaper
alternative this hook asks the menu to carry. `reviewer` / `codex` / `critic` are
listed alongside `review` because the lookaround is whole-token: `review` does
not match inside `code-reviewer`. A false positive in this set only
*silences* the nudge — the safe direction, the same rationale
`merge-menu-review-options-advisory` records for its own suppression set.

#### Tier 0 — abandonment (neither suppresses nor counts as a candidate)

- English (lookaround): `later`, `skip`, `defer`, `postpone`, `do nothing`,
  `nothing`, `cancel`, `hold`, `wait`, `none`
- Korean (substring): `다음 정기`, `다음 주기`, `나중에`, `하지 않`, `안 함`,
  `안함`, `대기`, `보류`, `취소`, `건너뛰`, `미루`, `아무것도`, `그대로 둬`,
  `그대로 둔`

### What is advised

| Scenario | Action |
| ---------- | -------- |
| Default mode, a question's candidates are all-mutating with no low-blast option | exit 0 + advisory stderr |
| `PRAXIS_MENU_MUTATION_TIER_STRICT=1`, same condition | exit 2 (block) |
| Any tool name other than `AskUserQuestion` | silent pass-through |
| A non-abandonment candidate carries a low-blast signal and names no high-blast target | silent pass-through (a safe tier is on the menu) |
| The question body carries a `Safe-tier-unavailable: <reason>` line | silent pass-through for that question (reason stated) |
| Fewer than 2 candidates after abandonment options are dropped | silent pass-through (binary go/no-go) |
| No candidate carries a mutation signal | silent pass-through (not a tier-relevant menu) |
| Empty / missing options | silent pass-through |
| Missing / malformed payload | silent pass-through (fail-open) |

### Mode and env var behavior

| Env var state | Mode | Exit code on match |
| --------------- | ------ | ------------------- |
| Unset (default) | **Advisory** | 0 + stderr warning |
| `PRAXIS_MENU_MUTATION_TIER_STRICT=1` | Strict | 2 (block) |

Only the exact value `1` (after `.strip()`) activates strict, matching the
dominant codebase convention (`destructive-bash-guard`,
`protected-paths-guard`, `merge-menu-review-options-advisory`). `0` / `false` /
`no` / `true` all stay advisory.

Default is advisory by design, and this is the hook's central posture: prod is
sometimes genuinely the only path, and what is owed at that point is not a
block but a stated reason. A denial there would only teach the agent to route
around the gate.

### Known limitations

- **Merge-strategy choosers fire.** `squash 머지` / `rebase 머지` / `대기` has two
  mutating candidates and no low-blast option, so it nudges even though all
  three strategies are equally prod. The sibling
  `merge-menu-review-options-advisory` documents the same limitation on the same
  menu shape; in advisory mode the nudge is informational, and adding a review
  lever (which that sibling asks for anyway) suppresses this one too.
- **Detection is lexical.** An option that is non-mutating for a reason no token
  names (`another team's staging cluster, called by its hostname`) is invisible,
  and the advisory fires. The nudge asks for one line of reasoning either way, so
  a false fire costs one sentence.
- **`production` matches its adjectival use.** `production-ready` matches the
  lookaround (`-` is not an ASCII letter). `block-manufactured-action-menu`
  omitted the long form for exactly this reason; it is kept here because the
  issue names it explicitly and the failure mode is a false *advisory*, not a
  false block.
- **Abandonment classification is lexical too.** An abandonment option phrased
  without any of the Tier 0 tokens counts as a candidate, which can turn a
  binary go/no-go into an apparent two-candidate menu and fire the advisory.

### Parsing guarantees

- Malformed JSON payload → exit 0 (fail-open)
- `tool_name != "AskUserQuestion"` → exit 0
- `tool_input` absent or not a dict → exit 0
- `questions` absent or not a list → exit 0
- `options` absent or not a list in a question → that question skipped
- An option that is not a dict, or has neither a string label nor a string
  description → that option skipped
- Empty option set → exit 0

### Tests

```bash
bash tests/hooks/advisory-nudge/test_menu_mutation_tier.sh
```

Covers: the 2026-08-11 incident menu replayed verbatim (advisory + strict
block); English / Korean / mixed-script all-mutating menus; mutation signal
carried only in the description; suppression by each low-blast family (preview,
dev, sandbox, dry-run, `보고만`, `조회만`, review lever, `리뷰`); non-tier menus
(doc-tone chooser, plan chooser); whole-token false-positive avoidance
(`merged` / `merger` / `deployment` do not count as mutation); the open-question
pins in both directions (an abandonment option does not suppress; a binary
go/no-go with an abandonment option does not fire; the same shape with a real
low-blast option in that slot passes); the Codex round-1 regressions in both
directions (a compound `dry-run → prod` option does not suppress, in advisory
and strict mode; a mutation verb at a safe target still does; `development`
recognised as low-blast; an external-write-only menu fires); the CodeRabbit
round-1 regressions (an option matching abandonment *and* low-blast does not
suppress, in advisory and strict mode, EN and KO; a stated
`Safe-tier-unavailable:` line suppresses in both modes; a mid-line or empty
marker does not; a reason on question 1 does not cover question 2); strict-env value
contract (`1` blocks,
`0` / `false` / `no` / `true` stay advisory); per-question isolation (a safe
second question does not cover an all-prod first one); malformed-payload
fail-open across seven shapes; and a self-application regression that feeds the
hook **this spec.md** as its option descriptions and asserts silence — a lexical
detector exercised only against fixtures its author shaped around the predicate
hides FP↔FN cancellation, so a payload dense with all three token sets in
ordinary prose is the cheapest independent check that the hook is not simply
trigger-happy.
