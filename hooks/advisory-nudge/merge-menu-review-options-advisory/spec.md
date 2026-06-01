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

- Korean (substring): `머지`
- English (ASCII-letter lookaround): `merge`, `squash`

English tokens use ASCII-letter lookaround (`(?<![a-z])merge(?![a-z])`) so
`merged` / `merger` do not trigger, while the mixed-script label `squash 머지`
still matches via the Korean token (Python's Unicode-aware `\w` treats Hangul
as a word character, so `\b` would not split `merge할까요`-style labels — the
lookaround does).

#### Review / debate option tokens (option label)

- Korean (substring): `리뷰`, `검토`
- English (substring): `codex`, `review`, `reviewer`, `critic`

Review-token detection uses **substring** matching (not lookaround) on purpose:
a false positive here only *suppresses* the advisory, which is the safe
direction (never nag when a review option may exist). So `reviewer` inside
`code-reviewer` and `review` inside `codex-review-wrap` both correctly count as
a present review option.

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
- Review-token suppression is substring-based, so an unrelated option label
  containing `review` as a substring suppresses the nudge. The notable case is
  `preview` (e.g. `preview diff`, `preview changes`), which contains `review`
  and silences the advisory even though it offers no review pass. This is the
  *safe* failure direction (false suppression never nags) and is accepted by
  design; if it bites in practice, name the preview option without the literal
  `review` substring.
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
(`squash 머지`) trigger; multi-question payload.
