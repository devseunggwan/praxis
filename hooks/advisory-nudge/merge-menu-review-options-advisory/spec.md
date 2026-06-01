# PreToolUse AskUserQuestion Merge-Menu Review-Options Advisory

Supported hosts: all

The hook implementation lives at
`hooks/advisory-nudge/merge-menu-review-options-advisory/impl.py`; the build
generates the dispatcher `hooks/merge-menu-review-options-advisory.sh` that the
platform `hooks.json` invokes. It fires on every
PreToolUse(AskUserQuestion) event and inspects `options[].label`. When the menu
is a **merge-decision gate** (an option label names a merge / squash action) but
offers **no review / debate option**, it emits an advisory asking the agent to
re-author the menu with the quality levers a pre-merge gate should offer:
re-run codex-review-wrap, re-run code-reviewer, or open a critic debate.

### Why this exists

A merge-decision `AskUserQuestion` menu is authored ad-hoc by the agent each
turn — there is no skill or template that generates the option set. So the
levers a user often wants at the last gate before an irreversible merge
(a fresh codex review pass, a code-reviewer pass, a critic debate) are routinely
absent, and the user must request them manually each time.

A PreToolUse hook **cannot inject options** into the rendered menu — it can only
allow, block, or advise. This hook takes the advise path: it detects a
merge-decision menu missing the review/debate levers and nudges the agent to
re-issue the `AskUserQuestion` with those options added.

This is the sibling of `block-manufactured-action-menu`, which catches a merge
menu that is *redundant* (surfaced after the user already said "merge"). This
hook catches the complementary defect: a merge menu that is *incomplete*
(missing the review/debate levers). Both inspect `options[].label` on
PreToolUse(AskUserQuestion); neither blocks by default.

### Context-aware reviewer routing (L2, #562)

When the menu *is* a merge gate missing a review option, the hook tailors
**which** reviewer the advisory recommends to the nature of the change being
merged. It reads the branch's changed file paths (`git diff --name-only
<base>...HEAD`) and maps them to a reviewer family, leading the advisory with
the context-specific recommendation before the generic levers.

This runs **only after** the merge-decision + no-review early-returns, so a
non-merge menu pays **zero** subprocess cost. The diff is path-based (no
diff-content read).

| Priority | Path signal | Recommendation (Hybrid: type + example agent) |
|----------|-------------|-----------------------------------------------|
| 1 (highest) | `auth` / `token` / `secret` / `credential` / `permission` / `oauth` substr, or `.pem` / `.key` suffix | 보안 리뷰 (예: security-reviewer) |
| 2 | `.sql` suffix, or `/sql/` / `migration` substr | 데이터/SQL 리뷰 (예: review-data) |
| 3 | `schema` / `/models/` / `/model/` / `entity` substr, or `.prisma` / `.proto` suffix | 설계 리뷰 (예: review-service-design) |
| 4 | `component` / `/ui/` / `/views/` / `/pages/` substr, or `.tsx` / `.jsx` / `.vue` / `.css` / `.scss` suffix | 디자인/UX 리뷰 (예: designer) |
| — (no match) | anything else | static generic levers (codex-review-wrap / code-reviewer / critic) |

**Multiple matches resolve to a single recommendation** by priority (security >
data > design > ux) — a merge gate surfaces the single most consequential lens,
not a wall of options.

**Hybrid recommendation text** = a portable reviewer *type* ("보안 리뷰") plus a
parenthetical example agent ("예: security-reviewer"). The type conveys intent
on every host; the example is a hint for hosts where that agent exists. This is
why the hook stays `hosts: all` despite naming Claude-ecosystem agents.

**Base ref resolution**: `origin/HEAD` → `origin/main` → `origin/dev` → `main`
→ `dev`, first that resolves. **Fail-open**: a non-git cwd, an unresolved base,
a git error, or a timeout yields the static, family-agnostic advisory — a diff
the hook cannot read never crashes it and never fabricates a routing decision.

**Subprocess budget**: the total git budget (5 base-resolution `rev-parse`
calls at 0.5s each + one `git diff` at 1.5s = 4.0s worst case) is kept safely
under the hook's 5s manifest timeout, so the Python fail-open path always runs
before the hook runner could kill a hung/locked git. Legit git calls return in
milliseconds; the timeouts only bound the pathological stall (NFS, index.lock).

**Self-consistency with suppression**: routed reviewers whose label may lack a
`review`/`reviewer` substring are added to the review-suppression tokens —
`designer` (the UX agent name) and `security` (a security review option phrased
as `security audit` / `security scan` rather than `security-reviewer`). The
others (`review-data`, `review-service-design`, and the `security-reviewer`
example itself) already suppress via `review` / `reviewer`.

**Base-resolution accuracy (multi-base repos)**: `origin/HEAD` (remote default)
is assumed to be the PR base. On a repo where feature branches target a
*non-default* base — e.g. branches target `dev` while the remote default is
`main` — `git diff origin/HEAD...HEAD` can over-include the non-default base's
unique commits, so a docs-only merge menu might be routed to `security`/`data`
because of unrelated paths on `dev`. A fully base-accurate pick would require
per-candidate merge-base comparison (more git calls than the fail-open
subprocess budget allows — there is a direct tension: tighter budget for
fail-open safety vs more calls for base accuracy). The common single-base case
(base = remote default) is optimised; the multi-base non-default case degrades
to a possibly-broad *recommendation*, never a wrong block (advisory carries the
generic levers regardless). If a repo needs base-accurate routing, raise a
follow-up to thread the PR base in explicitly.

**Data-signal tightness**: the data family's substrings are deliberately narrow
(`.sql` suffix, `/sql/`, `migration`). Broader tokens were rejected for
mis-routing non-data paths: `etl` matched `getlist.py`, `/load` matched
`src/loader.py`, and `template` matched FE template directories
(`web/templates/Card.tsx`). A SQL template lacking a `.sql` suffix falls to the
generic advisory — the safe direction, since a mis-routed *recommendation* (not
a block) is the only failure mode.

### What is advised

| Scenario | Action |
|----------|--------|
| Default mode, merge-decision option present, no review/debate option present | exit 0 + advisory stderr |
| `PRAXIS_MERGE_MENU_REVIEW_STRICT=1`, merge option present, no review option present | exit 2 (block) |
| Any tool name other than `AskUserQuestion` | silent pass-through |
| No option label names a merge-decision action | silent pass-through |
| A review/debate option is already present | silent pass-through (menu already complete) |
| Empty / missing options | silent pass-through |
| Missing / malformed payload | silent pass-through (fail-open) |

### Detect patterns

#### Merge-decision trigger tokens (option label)

- Korean (regex with negative lookahead): `머지(?!된|하지)` — excludes the
  meaning-inverting `머지된` / `머지하지` inflections (see Known limitations)
- English (ASCII-letter lookaround): `merge`, `squash`

English tokens use ASCII-letter lookaround (`(?<![a-z])merge(?![a-z])`) so
`merged` / `merger` do not trigger, while the mixed-script label `squash 머지`
still matches via the Korean token (Python's Unicode-aware `\w` treats Hangul
as a word character, so `\b` would not split `merge할까요`-style labels — the
lookaround does).

#### Review / debate option tokens (option label)

- Korean (substring): `리뷰`, `검토`
- English (substring): `codex`, `review`, `reviewer`, `critic`, `designer`

Review-token detection uses **substring** matching (not lookaround) on purpose:
a false positive here only *suppresses* the advisory, which is the safe
direction (never nag when a review option may exist). So `reviewer` inside
`code-reviewer` and `review` inside `codex-review-wrap` both correctly count as
a present review option.

`designer` is in the set for self-consistency with L2 routing: the UX family
recommends the `designer` agent, whose name contains no `review`/`reviewer`
substring, so without this token a menu already offering a `designer` option
would still be (wrongly) nudged. The other routed reviewers (`review-data`,
`security-reviewer`, `review-service-design`) already match `review` /
`reviewer`, so no further tokens are needed. (`designer` is a precise enough
word that `redesign` — which lacks the trailing `er` — does not match it.)

### Mode and env var behavior

| Env var state | Mode | Exit code on match |
|---------------|------|-------------------|
| Neither var set (default) | **Advisory** | 0 + stderr warning |
| `PRAXIS_MERGE_MENU_REVIEW_STRICT=1` | Strict | 2 (block) |

Default is advisory: a merge menu without review options is a *missed
opportunity*, not a violation, and there are legitimate cases (a merge-strategy
chooser `squash / rebase / merge-commit`, or a menu where the user explicitly
declined further review this turn) where the nudge does not apply. Strict mode
exists for users who want the levers always surfaced before any merge gate.

### Known limitations

- A merge-strategy chooser (`squash / rebase / merge-commit`) carries merge
  tokens but is not a go/no-go review gate; in default advisory mode the nudge
  is harmless (informational), but strict mode would block it. Prefer advisory
  mode unless the repo's menus never include strategy choosers.
- Detection is label-only; a review option expressed solely in an option
  *description* (not its label) is not counted as present and may trigger a
  false advisory. Keep review options' intent in the label.
- Review-token suppression is substring-based and runs **only on non-merge
  option labels** — a review lever is a distinct option, not the merge action
  itself. This matters because the suppression set includes broad tokens
  (`security`, `designer`, `review`) that can appear inside a merge label
  describing its change area (`merge security fix`, `merge designer changes`,
  `merge after preview`). If review detection ran on the merge label too, those
  substrings would *defeat* the nudge on exactly the high-stakes merges the
  label describes. Excluding merge labels from review detection fixes that
  inversion (and the older `preview`-inside-a-merge-label false suppression).
  A genuine review option still suppresses because it is a separate, non-merge
  option label (`security audit`, `designer 실행`, `re-run code-reviewer`).
- The Korean merge token `머지` is matched by a regex with a negative lookahead
  (`머지(?!된|하지)`) that excludes two meaning-inverting inflections: `머지된`
  (already-merged, a triage label) and `머지하지` (the `머지하지 말고` "do NOT
  merge" form). The second is a semantic inversion — without the exclusion an
  explicit no-merge label would fire a pre-merge nudge. Real gates still match
  (`머지`, `머지하기` since 하기 ≠ 하지, `머지할까요`, `스쿼시 머지`, `머지 + 정리`).
- The lookahead does not cover every non-gate inflection: a noun-modifier label
  like `머지 충돌 해결` ("resolve merge conflicts") still fires, since `머지` there
  is genuinely merge-related but not a go/no-go gate. Full KO disambiguation is
  out of scope (it leads to unbounded quoting/inflection corner cases); the
  advisory is non-blocking, so an occasional fire on a merge-adjacent label is
  low-harm. Only the two *inverting* inflections above are excluded.

### Parsing guarantees

- Malformed JSON payload → exit 0 (fail-open)
- `tool_name != "AskUserQuestion"` → exit 0
- `tool_input` absent or not a dict → exit 0
- `questions` absent or not a list → exit 0
- `options` absent or not a list in a question → that question skipped
- Empty label set → exit 0
- L2 routing diff unreadable (non-git cwd, base unresolved, git error/timeout)
  → static family-agnostic advisory (routing never crashes the hook)

### Tests

```bash
bash tests/hooks/advisory-nudge/test_merge_menu_review_options.sh
```

Covers: Korean merge token (`머지`) advisory + strict block; English merge
tokens (`merge`, `squash`) advisory; review-option-present suppression
(`codex` / `code-reviewer` / `review` / `critic` / `리뷰` / `검토`); merge-token
absent pass-through; non-AskUserQuestion tool pass-through; malformed payload
fail-open; empty options pass-through; ASCII-lookaround false-positive
avoidance (`merged` / `merger` do not trigger); mixed-script label
(`squash 머지`) trigger; multi-question payload. L2 routing (real git-diff
path): each reviewer family (security / data / design / ux), priority
resolution (security > data, design > ux), `designer` suppression, no-match
static fallback, non-git cwd fail-open, and strict-mode block carrying the
routed reviewer.
