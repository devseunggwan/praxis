# Rule backstop gaps

Which prompt-layer MUST/MANDATORY rules govern a **discrete, interceptable
user-facing surface** yet currently lack a praxis hook backstop — ranked by
user-facing cost. This is the human-authored complement to the auto-generated
[Hook Operating Matrix](../hook-operating-matrix.md) (what *is* hooked); this
file records what is *not* (yet) hooked and why it matters.

## Why this exists

A `praxis:retrospect` over one working session enumerated 22 `is_error` tool
results and found ~12 prompt-layer rule/convention retrievals that failed within
that single session. Every rule that **had** a hook backstop was caught by the
hook (e.g. `block-commit-without-codex-review` stopped an unreviewed commit; the
falsification gate fired twice on a `(Recommended)` option missing a
`Falsified:` line; `branch-name-check` and `mcp-describe-gate` — the latter since removed — each
fired). The
one rule **without** a backstop — a next-step `AskUserQuestion` surfaced on a
**stale PR-state premise** (the PR had already been merged) — reached the user,
who had to reject it. The enforcement layer is doing the heavy lifting for
prompt-layer self-discipline that fails at high frequency in a single session;
this gap list ranks where to extend it next (issue #709).

## Method

Cross-reference of two inventories:

- **What is hooked** — `hooks/manifest.json` + each hook's `spec.md` (58 hooks
  at the time of this cross-reference; the manifest registered 81 as of
  2026-08-03 and 95 as of 2026-09-05 — the gap list below has not been
  re-derived against either newer roster, so treat it as scoped to the 58).
- **What the ruleset requires** — the prompt-layer MUST / MANDATORY / "no
  exceptions" rules in the author's always-loaded agent ruleset (outside this
  repo; the rules it names are summarised in
  [`ETHOS.md` → Rules praxis carries](../../ETHOS.md#rules-praxis-carries)).

Filter: keep only rules whose violation surfaces on a **discrete, interceptable
user-facing surface** — `AskUserQuestion`, a `gh pr|issue` body, a merge-approval
ask, a commit. Diffuse reasoning disciplines (e.g. "separate symptoms from root
cause", "multi-perspective analysis") are out of scope because no PreToolUse /
Stop hook can structurally intercept them.

Each row below was verified against the actual hook source at authoring time
(probe cited inline), not from recall.

Rows **#1–#3** are scoped to the 58-hook roster above. Row **#4** was derived
separately, against the roster live on 2026-08-15 (96 manifest entries / 86
distinct hooks), by executing every hook on the three surfaces it names rather
than by re-reading specs — so its silence is measured, not inferred.
The #1–#3 scope caveat is unchanged by that. Row **#5** was derived the same way against the `7.12.0` roster (13 `Stop`, 8 PreToolUse(AskUserQuestion)), so it carries neither the #1–#3 nor the #4 scope.

## Gap table (ranked by user-facing cost)

| # | Rule | User-facing surface | Why no current hook catches it | Cost |
| --- | ------ | --------------------- | -------------------------------- | ------ |
| 1 | PR-state-contingent next-step question on a **stale premise** | `AskUserQuestion` surfacing a "what next?" option set whose options assume a PR is still open/unmerged | No hook re-fetches **live** PR state before the question is surfaced. `merge-state-claim-gate` runs on `Stop` and scans only the *final assistant message* for a completed merge/PR claim — it is post-hoc and does not see a mid-turn `AskUserQuestion`. `output-block-falsify-advisory` carries a static reminder that a premise may already be addressed by a merged PR, but it makes **no `gh` call** on any path, and its `AskUserQuestion` trigger fires only on a `(Recommended)`/anchoring token — a neutral "what next?" menu does not trip it, and even when it does it cannot verify the premise. So a stale-premise next-step reaches the user. | **HIGH** (reached the user this session) |
| 2 | `Closes #N` / `Fixes #N` wrapped in backticks in a PR body | `gh pr create --body` text | GitHub's auto-close parser ignores a closing keyword inside a code span, leaving the issue OPEN after merge (silent orphan). No hook scans the PR body for a backtick-wrapped closing keyword. | **MED** (silent — surfaces only later when the issue is found still open) |
| 3 | Commit trailers mandatory on behavior-change commits (`Confidence:` / `Not-tested:`) | `git commit` message body | No hook checks for the presence of the required trailers on a behavior-change commit. | **LOW-MED** (degrades future audit grep, not an immediate user-facing miss) |
| 4 | Agent **originates** a route around a hook block and delegates it to the user ([`ETHOS.md`](../../ETHOS.md#key-principles) principle 5) | A "the hook blocked this — here's how we can get past it" menu, in assistant prose or as an `AskUserQuestion` option set (add a permission rule to `.claude/settings.json`, move the file out of the guarded path, …), plus the `Write`/`Edit` to `.claude/settings.json` that follows the user's pick | Measured 2026-08-15, not assumed — each hook replayed with its own `hooks/manifest.json` invocation against a synthesized transcript / `tool_input`. Silent on **all three** lanes. **Prose lane**: all 12 `Stop` hooks pass it; `completion-verify`, `completion-signal-gate` and `merge-state-claim-gate` key on completion/merge **claims**, and no Stop hook scans a final message for offered bypass routes. **Menu lane**: all 8 PreToolUse `AskUserQuestion` hooks — plus the 1 PostToolUse one — pass the same menu as an option set; `output-block-falsify-advisory` needs a `(Recommended)`/anchoring token, `menu-mutation-tier-advisory` needs a mutating candidate with no safe tier, `merge-menu-review-options-advisory` needs a merge verb, and a bypass menu carries none of the three. **Follow-up-write lane**: `protected-paths-guard` is silent on a `Write` to `.claude/settings.json` — its protected set is credential-shaped (`.env`, `*.pem`, `.ssh/` …) — and no other `Edit\|Write` hook matches a settings path (the only other `settings.json` matcher, `jq-config-empty-dict-advisory`, is a PreToolUse(Bash) **read** advisory). Controls fire on the same harness in each lane: unevidenced merge claim → `completion-verify` + `completion-signal-gate` + `merge-state-claim-gate`; `(Recommended)` with no `Falsified:` → `output-block-falsify-advisory` (+2); `Write` to `.env` → `protected-paths-guard`. | **HIGH** (reached the user this session — and unlike #1, where acceptance cost one rejection, acceptance here **permanently widens the guard** for every later session) |
| 5 | A menu offering **design options for a choice an artifact already settles** — a registry, a project convention, a sibling implementation (*Look Up the Answer Before You Offer a Menu*, [`ETHOS.md`](../../ETHOS.md#rules-praxis-carries)) | An `AskUserQuestion` option set naming two or more approaches, or the same menu written in assistant prose | Measured on `7.12.0`, by replaying every hook the generated `.claude-plugin/hooks/hooks.json` registers for each lane. **Prose lane**: all 13 `Stop` hooks silent — the claim gates (`completion-signal-gate`, `merge-state-claim-gate`, `runtime-state-claim-gate`, `negative-existence-verdict-gate`, `artifact-verdict-evidence-gate`, `proposal-premise-gate`, `pr-claim-mutation-gate`) each key on an *assertion* in the final message, and an offered choice asserts nothing. **Menu lane**: all 8 PreToolUse(AskUserQuestion) hooks silent, including the two that exist for menus — `block-manufactured-action-menu` needs a question-form or affirmative **action** marker in an option label (`진행할까요` / `proceed` / `go ahead` / `Execute now`), and a design menu's labels name approaches rather than actions; `output-block-falsify-advisory` needs a `(Recommended)`/anchoring token a neutral two-approach menu does not carry. Controls fire on the same harness in both lanes: unevidenced merge claim → `merge-state-claim-gate`; `(Recommended)` with no `Falsified:` line → `block-manufactured-action-menu` + `output-block-falsify-advisory`. | **MED** (the answer the user gives back is worse than the recorded one, because they answer from memory while the source is on disk; and where the options differ in whether they skip a mandatory step, the pick reads as consent to the skip) |

Row numbers are stable identifiers — issues reference them — so a new row is
appended rather than renumbered in. The **Cost** column, not the row order,
carries the ranking: #4 is a HIGH-cost row sitting after the LOW-MED #3.

## Covered — not gaps (recorded to prevent over-claiming)

- **No Approval Transfer Across Companion PRs** — *covered*. `pre-merge-approval-gate`
  fires on **every** `gh pr merge`, so each sibling/successor merge re-triggers
  the approval gate independently; approval cannot silently transfer.
- **Read-before-write on `Edit`/`Write`** — *covered by the builtin*. The Claude
  Code builtin read-before-write guard fired 6× in the source session, i.e. it
  *does* catch the case every time; it is builtin-only with no praxis nudge, but
  there is no user-facing miss to backstop. Adding a praxis-layer duplicate would
  be redundant. (The `Bash` redirect variant — `>` / `tee` / heredoc on an
  existing path — is the one the builtin does *not* see; that intent is already
  carried by the ruleset's "Bash Redirect on Existing Path Requires Read-First".)

## Tracked follow-ups

- **Gap #1 → direction 2** ([#719](https://github.com/devseunggwan/praxis/issues/719)):
  a hook that re-fetches live PR state before surfacing a PR-state-contingent
  next-step `AskUserQuestion`; if the PR is already merged/closed, act directly
  instead of asking. A lock-boundary re-fetch analogous to the existing
  pre-merge-approval probe, extended beyond the merge-approval surface.
- **Retrospect Stage 2 hardening → direction 3** ([#720](https://github.com/devseunggwan/praxis/issues/720)):
  hard-require reading each `is_error` result **body** rather than assuming its
  category — the source retrospect committed exactly this failure (the
  recursive-retrospect anti-pattern it is meant to catch). This is a
  skill-internal enumeration weakness, not a hook-backstop gap, but is tracked
  here because the same session surfaced it.
- **Gap #4, follow-up-write lane → `settings-path-advisory`** ([#1337](https://github.com/devseunggwan/praxis/issues/1337) item 3):
  the one lane of #4 that is a tool call, not prose. An `Edit`/`Write` to
  `.claude/settings.json`, `.claude/settings.local.json` or a
  `managed-settings.json` now gets a PreToolUse advisory (stderr +
  `additionalContext`) naming the permission/hook keys the write carries and
  asking the agent to state, in the response, who asked for the change. The
  hook cannot decide authorship from the payload — the row above stays
  measured as written for the prose and menu lanes, which this does not
  touch. `ConfigChange` was not used: a blocked change there surfaces no
  message to the user or to Claude (hooks reference, read 2026-09-06).
- **Gap #4, prose and menu lanes → prose containment, deliberately no hook** ([#1009](https://github.com/devseunggwan/praxis/issues/1009)):
  the decided fix is [`ETHOS.md`](../../ETHOS.md#key-principles) principle 5,
  not a new hook. A structural detector would have to separate a relayed
  `Bypass (if truly needed): …` line — which principle 5 explicitly permits,
  because it is praxis' own designed escape hatch — from an agent-originated
  route that merely quotes similar words, and the two are textually
  indistinguishable in prose. Recorded here as an open backstop gap so the
  prose clause is not mistaken for enforcement.
- Gaps **#2** and **#3** are surfaced here but **not yet issue-tracked** — open
  them if/when the cost is judged worth a dedicated hook.
- Gap **#5** is deliberately unhooked. `#1009` measured this same axis and chose a
  ruleset clause over a gate; the clause landed, and this row is the measured
  record that the clause is not enforcement.
