# Stop Merge-State Claim Gate

Supported hosts: all

`hooks/completion-verify/merge-state-claim-gate/impl.py` runs on the `Stop`
event. It scans the **final assistant message** for a completed merge / PR /
issue / worktree state assertion and, when no fresh state query appears in the
recent transcript, emits a **stdout `{"systemMessage": ...}` JSON advisory**.

## Why this exists

A praxis ultrawork session (#487/#489) hallucinated review/merge state four
times in one session: "PR #495/#497 created, merged, issue closed, worktree
cleaned" — none of which had happened (the PR create was hook-blocked; the cited
numbers were unrelated worktrees). The behavioural remedy lived in memory only,
and the Iron Law "REPEATED PATTERN + MEMORY = FAILED REMEDY -> ESCALATE" was
crossed (issue #503).

PreToolUse hooks cannot see in-flight assistant text (issue #487 finding A3), so
they cannot gate a *claim*. The Stop hook is the exact complement: it sees the
final assistant output and can cross-check it against what the session actually
did.

## What is emitted

Advisory by default — exit 0 + stdout `{"systemMessage": ...}` JSON (issue #647
H3; the old exit-0 stderr form only reached the debug log). The model has
already stopped, so the note reaches the user (transcript-visible; not fed to
the model). `PRAXIS_MERGE_CLAIM_STRICT=1`
escalates to a `{"decision": "block", "reason": ...}` JSON, which re-prompts
the model to verify before stopping.

| Condition | Result |
| --- | --- |
| Final message asserts a completed merge/PR/issue/worktree state AND no fresh state query in the recent transcript | `[merge-state-claim-gate]` advisory |
| Same, but a fresh `gh pr\|issue …` command or GitHub MCP pull_request/issue/merge tool is present in the recent transcript | silent (claim is backed) |
| Final message asserts an **applied-on-branch** state (`applied`/`deployed`/`적용됨`/`배포됨` …) AND no **reachability probe** in the recent transcript — a generic state query does NOT clear this kind (#656) | `[merge-state-claim-gate]` advisory with reachability guidance |
| Applied-on-branch claim WITH a reachability probe (`git merge-base --is-ancestor`, `--json state,baseRefName` query, `git branch --contains`) in the recent transcript | silent (claim is backed) |
| Final message asserts a **still-open / unchanged / no-loss** state for a specific `#N` (`PR #864 는 여전히 OPEN`, `no commits were lost from PR #864`) AND no fresh `gh pr\|issue` query FOR THAT NUMBER (#869) | `[merge-state-claim-gate]` advisory |
| Same unchanged claim, cleared by a `gh pr\|issue view <N>` or GitHub MCP call referencing THAT SAME number | silent (claim is backed) |
| Same unchanged claim, a query for a DIFFERENT `#M` is present — does NOT clear | `[merge-state-claim-gate]` advisory (per-number specificity) |
| Unchanged claim with no `#N` on the line (`the branch still has no open PR`) | silent (deliberate narrowing — #869 scope, see below) |
| Final message has no such claim | silent |
| **Completed-state** claim line is negated (`not`, `yet`, `아직`, `않`, …) — persistence claims (`has not been merged`, `유실 없음`) are covered by the unchanged rows above, not silenced here | silent |
| Future intent only (`I'll create a PR`, `ready to merge`) | silent (completion tokens are past/perfective) |
| `stop_hook_active` is true | silent (re-entry loop guard) |
| Missing/unreadable transcript, malformed stdin | silent (fail-open) |
| `PRAXIS_MERGE_CLAIM_BYPASS` set | silent (opt-out) |

## Claim detection

A claim requires, **on the same line**, both:

- a **subject** token — `PR`, `pull request`, `MR`, `issue`, `이슈`,
  `worktree`, `워크트리`, or `#<number>`; and
- a **completed-state** token — `merged` / `머지했|머지됐|머지됨…`,
  `created`/`opened` / `생성했|만들었|올렸`, `closed` / `닫았|종료했`,
  `removed`/`cleaned`/`deleted` / `정리했|삭제했|제거했`,
  `applied`/`deployed`/`landed`/`blocked since` /
  `적용됐|배포됨|반영됨|차단됨…` (`released` deliberately excluded —
  lock/memory-release prose false-positives).

The **`applied` kind** additionally accepts a **branch/ref subject**
(`dev`, `prod`, `main`, `master`, `release`, `branch`, `브랜치`) — an
applied-on-branch claim ("dev에 적용됨", "deployed to prod") often names only
the target ref, never a PR number (#656). Branch tokens use Hangul-safe
lookarounds, not `\b` (same trap as the `\bPR\b` fix).

Localizing the match to one line cuts false positives on long final messages,
and the past/perfective completion tokens exclude future intent. A negation
token anywhere on the line suppresses the claim. The `applied` kind uses a
narrower negation set with `without` dropped — "Deployed to prod without
incident" is a genuine claim and must still fire (`fail` stays; the
applied-claim-next-to-failing-prose miss is an accepted, documented trade-off).

## Evidence detection

The recent transcript (last 80 events) is scanned for an assistant `tool_use`
that is either:

- a `Bash` command matching `gh … (pr|issue) <subcommand>` (e.g.
  `gh pr view`, `gh issue list`, `gh pr merge`), or
- a GitHub MCP tool whose name matches `pull_request` / `issue` / `merge` /
  `pr_` (e.g. `mcp__github__pull_request_read`, `merge_pull_request`).

Either form is treated as a fresh state query that backs the claim. Both the
`gh` CLI and the GitHub MCP server are covered so the gate behaves correctly in
local and remote-execution environments.

**Reachability evidence (`applied` kind only, #656).** A state query clears
`merged`/`created`/`closed`/`cleaned` but NOT `applied` — the 2026-05-15
incident ran exactly `gh pr view --json state` and still mis-released 3
changes because the merged PR's base was a feature branch (stacked PR). The
`applied` kind is cleared only by a Bash command matching:

- `git merge-base --is-ancestor` (commit-based reachability),
- a `--json` query carrying **both** `state` and `baseRefName` in the same
  command (`gh pr view --json state,baseRefName` — the `--json` context is
  required, so a bare `grep baseRefName` of source code does not clear; and a
  baseRefName-only query never confirms the merge state), or
- `git branch --contains` (short `-r` and long `--merged` intervening flags
  both tolerated).

This is deliberately CLI-only: an MCP `pull_request_read` *returns*
`baseRefName` but does not prove the field was consulted. The regex is
mirrored in `hooks/advisory-nudge/external-write-falsify-check` (2 copies —
DRY extraction deferred to a 3rd consumer per repo convention).

## Negative-polarity persistence claims (issue #869)

2026-07-27 session retrospect finding #2 (HIGH). Across several turns, the
assistant repeated "PR #864 는 여전히 OPEN, 커밋 유실 없음" (still open, no
commits lost) — a pre-compaction snapshot. The PR had actually merged as
`9ac8fa3` and that commit was the base of the in-progress work. Re-query
count over the affected turns: zero.

`_NEGATION_RE` originally treated ANY negated line as safe to silence (a
deliberate false-positive-avoidance choice). But "여전히 OPEN" / "유실 없음"
were never even reaching that check — `_CLAIM_KINDS`' vocabulary
(`merged`/`created`/`closed`/`cleaned`/`applied`) has no token for "OPEN" or
"no loss" at all, so the line was invisible to the hook, negation aside.

A second, independent claim path (`detect_unchanged_claims` /
`_UNCHANGED_STATE_RE`) now recognizes persistence-of-state assertions —
`여전히 OPEN`, `그대로`, `유실 없음`, `남아있`, `변경 없`, EN `still open`,
`no commits lost`, `hasn't been merged`, `remains open` — and, mirroring
`runtime-state-claim-gate`'s "isolation" kind, does **not** run the generic
negation suppressor: the negative surface form (`없음`, `not merged`) IS the
claim, not evidence that the assistant is reporting incompletion.

**Scope narrowing (deliberate, per issue).** This path fires ONLY when a
`#N` token co-occurs with the persistence vocabulary on the SAME LINE. A
numberless line ("the branch still has no open PR", "여전히 열려
있습니다") is left to the existing (silent) generic-negation path — widening
to numberless lines was explicitly rejected to avoid reopening the original
false-positive concern that motivated blanket negation suppression.

**Per-number evidence (`has_fresh_query_for_number`).** Unlike the
positive-kind evidence check (any recent `gh pr|issue` query clears any
positive kind), an unchanged claim about `#864` is cleared ONLY by a query
that itself references `864` — a `gh pr view 111 --json state` call sitting
in the same window does NOT clear a stale claim about `#864`. This mirrors
the applied-kind's stricter-evidence precedent (#656): a looser generic
check would have let the incident's exact stale re-assertion pass had any
unrelated `gh pr view` call happened to appear nearby.

The reference must be **positional and read-only**, not a substring of the
command text:

| Evidence shape | Clears `#864`? |
| --- | --- |
| `gh pr view 864 --json state` / `gh issue view 864` / `gh -R o/r pr view 864` | yes |
| `gh pr view https://github.com/o/r/pull/864` | yes (URL target ends in `/864`) |
| MCP read (`pull_request_read`, `get_issue`, …) with `pullNumber`/`issue_number` = `864` | yes |
| `gh pr view 1864` | no — digits must be a whole number, not a longer one's prefix |
| `gh pr view 111 --repo org/project-864` | no — the digits sit in a slug, not at the target position |
| MCP read with `owner: team864`, `pullNumber: 111` | no — only PR/issue *number* fields count |
| `gh pr merge 864 --squash`, MCP `merge_pull_request` | no — a mutation does not report the post-mutation state the claim asserts |

**Known gap (deliberate).** A combined line ("PR #864 는 OPEN이고 커밋 유실
없음") is cleared as a whole by a state-only read, even though `--json state`
cannot witness a force-push that dropped commits. Requiring commit/head
history evidence for the no-loss subtype specifically would change the
gate's contract beyond issue #869's scope; it is left to a follow-up.

## Relationship to sibling hooks

| Hook | Scope | Overlap |
| --- | --- | --- |
| `completion-signal-gate` | Stop advisory on completion phrases without an evidence block | Complementary — generic "done" claims vs. specific merge/PR state claims |
| `block-pr-without-caller-evidence` / `block-pr-without-precommit-evidence` | PreToolUse gate on PR *creation* | None — those gate the action; this gates the *assertion* of state |

## Parsing guarantees (fail-open)

Returns exit 0 on every infrastructure error — malformed stdin, missing/unreadable
transcript, and any uncaught exception (via the shared `@fail_open` decorator in
`hooks/_lib/_hook_runtime.py`). It never blocks a normal Stop in the default mode.

## SubagentStop (issue #1337)

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

## Tests

```bash
bash tests/hooks/completion-verify/test_merge_state_claim_gate.sh
```

50 cases: English/Korean claim without evidence (advisory), claim with `gh`
evidence / GitHub MCP evidence (silent), neutral message (silent), negated claim
(silent), future intent (silent), strict mode (decision: block), bypass (silent),
`stop_hook_active` loop guard (silent), worktree-cleanup claim (advisory),
missing transcript (fail-open), malformed JSON (fail-open), plus the #656
applied-kind family: EN/KO applied claims without evidence (advisory),
**state-only `gh pr view --json state` does NOT clear applied** (advisory — the
incident shape), merge-base / baseRefName evidence (silent), negated applied
(silent), branch-token-only and applied-token-only lines (silent), mixed
merged+applied with state-only evidence (advisory carrying reachability
guidance), applied strict mode (decision: block), and 4 review-fix
regressions: `grep baseRefName` does not clear, long-flag
`branch --merged --contains` clears, `without incident` does not suppress,
`released the lock` prose stays silent, baseRefName-only query does not
clear (codex P2), applied-only advisory drops the (false) generic
"no fresh state query" sentence when a state query exists (CodeRabbit).

Plus 11 negative-polarity persistence cases (#869): motivating incident
verbatim (KR still-open + no-loss, zero re-query → advisory), cleared by a
matching-number `gh pr view 864` / GitHub MCP `pullNumber: 864` query
(silent, 2 cases), NOT cleared by a different-number query (advisory — the
per-number specificity guard), EN "has not been merged" without/with a
matching query (advisory / silent), "no commits lost" phrasing (advisory),
two numberless claims EN/KR that stay silent (deliberate scope narrowing),
a mixed message where a generic query clears a "merged" claim but does NOT
clear an unchanged claim for a different number (advisory — number
specificity), and strict mode on an unchanged-only claim (decision: block).
