# Stop Merge-State Claim Gate

Supported hosts: all

`hooks/completion-verify/merge-state-claim-gate/impl.py` runs on the `Stop`
event. It scans the **final assistant message** for a completed merge / PR /
issue / worktree state assertion and, when no fresh state query appears in the
recent transcript, emits a **stdout `{"systemMessage": ...}` JSON advisory**.

### Why this exists

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

### What is emitted

Advisory by default — exit 0 + stdout `{"systemMessage": ...}` JSON (issue #647
H3; the old exit-0 stderr form only reached the debug log). The model has
already stopped, so the note reaches the user (transcript-visible; not fed to
the model). `PRAXIS_MERGE_CLAIM_STRICT=1`
escalates to a `{"decision": "block", "reason": ...}` JSON, which re-prompts
the model to verify before stopping.

| Condition                                                                                                                                                                                                      | Result                                                         |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| Final message asserts a completed merge/PR/issue/worktree state AND no fresh state query in the recent transcript                                                                                              | `[merge-state-claim-gate]` advisory                            |
| Same, but a fresh `gh pr\|issue …` command or GitHub MCP pull_request/issue/merge tool is present in the recent transcript                                                                                     | silent (claim is backed)                                       |
| Final message asserts an **applied-on-branch** state (`applied`/`deployed`/`적용됨`/`배포됨` …) AND no **reachability probe** in the recent transcript — a generic state query does NOT clear this kind (#656) | `[merge-state-claim-gate]` advisory with reachability guidance |
| Applied-on-branch claim WITH a reachability probe (`git merge-base --is-ancestor`, `--json state,baseRefName` query, `git branch --contains`) in the recent transcript                                         | silent (claim is backed)                                       |
| Final message has no such claim                                                                                                                                                                                | silent                                                         |
| Claim line is negated (`not`, `yet`, `아직`, `않`, …)                                                                                                                                                          | silent                                                         |
| Future intent only (`I'll create a PR`, `ready to merge`)                                                                                                                                                      | silent (completion tokens are past/perfective)                 |
| `stop_hook_active` is true                                                                                                                                                                                     | silent (re-entry loop guard)                                   |
| Missing/unreadable transcript, malformed stdin                                                                                                                                                                 | silent (fail-open)                                             |
| `PRAXIS_MERGE_CLAIM_BYPASS` set                                                                                                                                                                                | silent (opt-out)                                               |

### Claim detection

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

### Evidence detection

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

### Relationship to sibling hooks

| Hook                                                                       | Scope                                                         | Overlap                                                                  |
| -------------------------------------------------------------------------- | ------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `completion-signal-gate`                                                   | Stop advisory on completion phrases without an evidence block | Complementary — generic "done" claims vs. specific merge/PR state claims |
| `block-pr-without-caller-evidence` / `block-pr-without-precommit-evidence` | PreToolUse gate on PR *creation*                              | None — those gate the action; this gates the *assertion* of state        |

### Parsing guarantees (fail-open)

Returns exit 0 on every infrastructure error — malformed stdin, missing/unreadable
transcript, and any uncaught exception (via the shared `@fail_open` decorator in
`hooks/_lib/_hook_runtime.py`). It never blocks a normal Stop in the default mode.

### Tests

```bash
bash tests/hooks/completion-verify/test_merge_state_claim_gate.sh
```

39 cases: English/Korean claim without evidence (advisory), claim with `gh`
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
