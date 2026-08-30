# Appendices: error handling and limitations (codex-review-wrap)

The full error-handling table and the limitations list for
[`../SKILL.md`](../SKILL.md). Consult the table whenever a step hits one
of the listed situations; the limitations are known gaps, not defects.

## Error Handling

| Situation | Action |
| ----------- | -------- |
| PR state is CLOSED or MERGED | ABORT: "PR is {state} — review aborted. Re-open or target a different PR." |
| `git worktree list` fails (not a git repo) | Abort: "git worktree list 실패 — git 저장소인지 확인하세요." |
| All worktrees are bare | Treat as Case A (single effective target) using cwd |
| User selects "취소" | Abort silently with one-line message |
| `installed_plugins.json` missing or codex entry absent | Offer alternatives via `AskUserQuestion` (Step 4a) |
| Resolved `codex-companion.mjs` path does not exist | Offer alternatives via `AskUserQuestion` (Step 4a) |
| Premise check (Step 5b) disproves a finding | Skip the edit; reply to Codex with the falsifying evidence |
| Flip detected (Step 5c) | Halt; surface both rounds to the user; do not apply either side without explicit direction |
| Sibling identified but branch/repo not accessible locally | Skip 5d for that sibling; record `sibling-applied: ... \| result=inaccessible` in ledger; warn user to check out the branch |
| Sibling auto-detected but user confirms "not a port" | Skip 5d entirely; still write `sibling-id: … \| sibling=none` so a 5j re-entry does not re-ask |
| `PRAXIS_DIMINISHING_RETURNS_N` is set but not a positive integer | Use default (4); do not error |
| Region label cannot be determined (binary file, empty file) | Use the file path alone as the region label |
| Critic negative claim emitted without `Probe:` citation (5g) | Halt the finding; prompt the critic to re-run with probe citation before surfacing |
| Probe command for 5g returns unexpected output or exits with an error code that signals a command failure (e.g. exit=2 "command not found", permission denied) — distinct from `grep` exit=1 (no match), which is the expected signal for verified absence | Surface probe failure to the user; do not auto-retract the claim — let the user decide |
| Approval gate (5i) — user cancels a batch or gives no answer | Stop applying for the round; report applied / undecided findings; no further edits |
| Approval gate (5i) — round produced zero applicable findings | Skip 5i; state "no findings to approve" in one line |
| Approval gate (5i) — user declines the follow-up issue approach review | Keep the `deferred:` rows with `issue=pending`; create nothing |
| Approval gate (5i) — non-interactive run (`claude -p`, background) where `AskUserQuestion` cannot reach a user | Apply nothing; record every finding that survived 5b/5c as `deferred:` (evidence-rejected and flip-halted findings stay out, same as the interactive path) and report the full list for a later interactive round |
| Round-continuation gate (5j) — user cancels, or `AskUserQuestion` returns no answer (it blocks, so this means cancellation or a tool error) | Do not re-enter; proceed to Step 6. Record `decision=stop` |
| Round-continuation gate (5j) — next round resolves to `branch` and the applied edits are uncommitted | Offer to commit them; if declined, skip the gate and proceed to Step 6 — a base-pinned review of round N+1 would not see the edits |
| Round-continuation gate (5j) — next round resolves to `working-tree` and 5e already committed this round's edits | Do not offer a commit. Either re-enter with `--base <ref>` so the commit is in the target, or skip the gate — never fire while telling the user the edits are invisible to round N+1 |
| SoT audit (5h) — sibling document not locatable | Emit unresolved advisory; do not block review completion |
| SoT audit (5h) — parent citation site ambiguous (multiple tables at same heading) | Use all tables at the heading as candidate SoT sources; report each comparison separately |
| Reaper (Step 6) — running broker has no readable `broker.log` | Idle is indeterminate → broker is KEPT (logged as `SKIP ... no logFile`); never reaped on a guess |
| Reaper (Step 6) — `CLAUDE_PLUGIN_ROOT` unset (skill run outside plugin context) | Resolve the script via the installed-plugins manifest, same as Step 4a; if still unresolved, skip Step 6 with a one-line note — reaping is best-effort hygiene, not a gate |
| Reaper (Step 6) — agent considers `pkill -f codex` / `pkill node` | Forbidden: aborts sibling sessions' in-flight reviews. Use only the reaper's per-broker idle gate |

## Limitations

- Does not modify `/codex:review` itself — users who call it directly still get the old behaviour
- Subshell `cd` does not persist after skill execution — cwd is not mutated in the parent session
- The Step 5 ledger is per-session only — flips that span session boundaries are not detected
- Premise classification (5a) is heuristic; when in doubt, treat the finding as fact-modifying
- Step 5d sibling cross-check requires the sibling branch to be locally accessible — remote-only PRs need a manual `git worktree add` before cross-check can run
- Sibling auto-detection from `git worktree list` uses branch-name heuristics (shared prefix, `*-shell` / `*-python` suffixes) and may produce false positives on unrelated paired branches; user confirmation at 5d-i overrides the auto-detect signal
- The rounds-per-region counter (5f) is per-session only — counts do not carry across session boundaries
- Region label extraction (5f) is heuristic: the nearest enclosing heading / symbol is determined from the finding context Codex provides; findings with no file attribution use the file path alone
- The advisory threshold `PRAXIS_DIMINISHING_RETURNS_N` applies uniformly across all regions; per-region tuning is not supported
- Step 5g negative-claim detection is pattern-based; highly paraphrased negative claims that do not match the trigger forms may slip through — when in doubt, treat a claim as negative and require a probe
- The critic prompt template (5g) can be injected at generation time only when the reviewer accepts a context string — `codex@openai-codex 1.0.6`'s built-in reviewer does not (see 5g's measured limitation), so there the gate is enforced caller-side on the returned findings; when using the `oh-my-claudecode:code-reviewer` fallback (Step 4a), the template must be manually prepended to the reviewer's context on every round
- Step 5h SoT audit detects truncation only for inline-transcribed enumerations; reference-link citations (sibling SoT referenced but not transcribed) are inherently safe and are not audited
- Step 5h citation-signal scanning is keyword-based; enumerations that use non-standard labels (e.g., custom matrix row identifiers) may not be detected — when authoring, prefer the standard labels listed in the trigger table
- Step 5h requires the sibling SoT document to be locally readable; remote-only or external URLs are flagged as unresolved advisories and require manual verification
- Step 5i requires an interactive session — `AskUserQuestion` has no reachable user under `claude -p` or a background worker, so those runs apply nothing and defer every finding
- Step 5i costs one `AskUserQuestion` round-trip per 4 findings; a round with many stylistic findings trades throughput for control, by design
- Step 5i decisions live in the same per-session ledger as 5c — a finding declined in one session is not remembered in the next
- Step 5j imposes no maximum round count. Three paths end the loop: the user chooses Step 6, a round applies zero edits (`{C}` = 0, counting 5h synthesized edits — Codex returning zero findings is not on its own one of them), or the run is non-interactive. A 5c flip halt is not one of them — a resolved flip can still be applied; an unresolved one converges on the zero-edits path
- Step 5j never fires in a non-interactive run, but not by a rule of its own: `claude -p` and background workers apply nothing (5i), so the round reaches the gate with zero applied edits and fails the fire condition. Fire condition (c) re-runs the interactivity check anyway, so the guard does not depend on 5i keeping that behaviour
- Step 5j extends the review phase for as long as the loop runs, and Step 6 reaps brokers only at phase end — so each round's broker stays resident until the loop finishes. A long loop holds more brokers than a single-round invocation
- The ledger lives in session working memory, so the longer a 5j loop runs the more rounds compete for that context. Flip detection degrades *within* a session as round count grows — the existing cross-session caveat does not cover this
- Step 5j's fire condition (b) rests on `resolveReviewTarget` and `collectReviewContext` as measured against `codex@openai-codex 1.0.6`; a change to how either picks or assembles a scope would need re-measuring. What (b) requires is membership of the applied edits in the next round's actual diff — the scope name is not a proxy for it, since `--scope auto` resolves to `working-tree` whenever *any* path is dirty and a commit made this round then falls outside the target
- Step 6 reaper is macOS-only (launchd reparenting + `/var/folders` sessionDirs); it is a no-op on other platforms
- Step 6 idle detection uses `broker.log` mtime as an activity proxy — a broker mid-operation that stays silent longer than `--max-age` could be misjudged idle and reaped; the cost is a benign respawn on the next codex call, never a correctness break
- Step 6 phase-end reaping keeps the broker count below the compressor threshold but does not reclaim every running orphan; the session-independent launchd job (`LAUNCHD.md`) is what reaps orphans whose owning session is already gone
