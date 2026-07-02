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
`Falsified:` line; `branch-name-check` and `mcp-describe-gate` each fired). The
one rule **without** a backstop — a next-step `AskUserQuestion` surfaced on a
**stale PR-state premise** (the PR had already been merged) — reached the user,
who had to reject it. The enforcement layer is doing the heavy lifting for
prompt-layer self-discipline that fails at high frequency in a single session;
this gap list ranks where to extend it next (issue #709).

## Method

Cross-reference of two inventories:

- **What is hooked** — `hooks/manifest.json` + each hook's `spec.md` (58 hooks,
  ~20 deduplicated backstopped rule-concepts).
- **What the ruleset requires** — the prompt-layer MUST / MANDATORY / "no
  exceptions" rules in the global agent ruleset and [`ETHOS.md`](../../ETHOS.md).

Filter: keep only rules whose violation surfaces on a **discrete, interceptable
user-facing surface** — `AskUserQuestion`, a `gh pr|issue` body, a merge-approval
ask, a commit. Diffuse reasoning disciplines (e.g. "separate symptoms from root
cause", "multi-perspective analysis") are out of scope because no PreToolUse /
Stop hook can structurally intercept them.

Each row below was verified against the actual hook source at authoring time
(probe cited inline), not from recall.

## Gap table (ranked by user-facing cost)

| # | Rule | User-facing surface | Why no current hook catches it | Cost |
| --- | ------ | --------------------- | -------------------------------- | ------ |
| 1 | PR-state-contingent next-step question on a **stale premise** | `AskUserQuestion` surfacing a "what next?" option set whose options assume a PR is still open/unmerged | No hook re-fetches **live** PR state before the question is surfaced. `merge-state-claim-gate` runs on `Stop` and scans only the *final assistant message* for a completed merge/PR claim — it is post-hoc and does not see a mid-turn `AskUserQuestion`. `output-block-falsify-advisory` carries a static reminder that a premise may already be addressed by a merged PR, but it makes **no `gh` call** on any path, and its `AskUserQuestion` trigger fires only on a `(Recommended)`/anchoring token — a neutral "what next?" menu does not trip it, and even when it does it cannot verify the premise. So a stale-premise next-step reaches the user. | **HIGH** (reached the user this session) |
| 2 | `Closes #N` / `Fixes #N` wrapped in backticks in a PR body | `gh pr create --body` text | GitHub's auto-close parser ignores a closing keyword inside a code span, leaving the issue OPEN after merge (silent orphan). No hook scans the PR body for a backtick-wrapped closing keyword. | **MED** (silent — surfaces only later when the issue is found still open) |
| 3 | Commit trailers mandatory on behavior-change commits (`Confidence:` / `Not-tested:`) | `git commit` message body | No hook checks for the presence of the required trailers on a behavior-change commit. | **LOW-MED** (degrades future audit grep, not an immediate user-facing miss) |

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
- Gaps **#2** and **#3** are surfaced here but **not yet issue-tracked** — open
  them if/when the cost is judged worth a dedicated hook.
