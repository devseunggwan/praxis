# Changelog

All notable changes to praxis are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [7.1.0] - 2026-07-03

4 PRs since 7.0.0. Minor release. Adds the `debt` skill and the 3
outcome-proxy telemetry signals scoped out of #710/#737 for lack of a
telemetry source at the time — `bypass-review fire-rate`'s Outcome Proxy
section now reports `external_write_revert_count`, `rework_commit_count`,
and `reclarification_loop_count` alongside the existing `strike_count`.

### Added

- `debt`: new report-only skill — deferred-decision ledger unioning commit-trailer
  markers (`Not-tested:`, `Confidence: low`, `Rejected:`, `Directive:`,
  `Scope-risk:`) from `git log --grep` with tree compounding comments
  (`# [PR #N]`) from `grep`. Groups hits, tags markers with no stated revisit
  condition as `no-trigger`, and never modifies any file. (#711)
- `destructive-bash-guard`: detects `git revert`, `gh pr close`, `gh issue
  reopen` command patterns and logs an `external_write_revert_count`
  outcome-proxy signal to the fire-ledger (command-pattern detection only,
  not state-reversal proof). (#737, #739)
- `bypass-review`: `rework_commit_count` outcome-proxy signal — correlates
  git commits to fire-event sessions via a `Session-Id:` commit trailer
  (exact match, manual convention) with a 15-minute timestamp-heuristic
  fallback for commits lacking the trailer, mirroring `bypass_count`'s
  exact-map + heuristic-fallback structure. Trailer coverage is
  forward-only (not retroactive); both limitations are documented in
  `OUTCOME PROXY LIMITATIONS`. (#737, #741, PR #742)
- `askuserquestion-loop-signal`: new `PostToolUse(AskUserQuestion)` hook
  appending a `reclarification_loop_count` outcome-proxy signal per call.
  Uses a coarse per-session call-count proxy (≥2, no topic clustering) to
  avoid adding fields to the shared fire-ledger record schema; the
  same-topic accuracy limitation (false positives on multi-topic sessions,
  false negatives across session/compaction boundaries) is documented in
  `OUTCOME PROXY LIMITATIONS`. Adds `record_session_fire()` to
  `hooks/_lib/_fire_ledger.py` as a RICH single-event writer for standalone
  hooks needing real `session_id` attribution outside the Bash dispatch
  group. (#737, #740, PR #743)

## [7.0.0] - 2026-06-28

Major release. Removes the `cmux-browser` skill, which has migrated to the
cmux repository (`manaflow-ai/cmux`) where it lives closer to the cmux app it
automates. Removing a public skill + its installed CLI is a breaking change
for consumers (`/praxis:cmux-browser` and the `~/.local/bin/cmux-browser`
command both disappear), so this is a semver major per the project's
convention for skill removals.

### Removed (BREAKING)

- `cmux-browser`: skill and its pass-through CLI wrapper removed from praxis.
  The cmux-side documentation skill is maintained in `manaflow-ai/cmux`; the
  praxis wrapper (`~/.local/bin/cmux-browser`, selector-error usage hints) is
  dropped because the cmux skill invokes the native `cmux browser` CLI
  directly. The session-management cmux-* skills (`cmux-delegate`,
  `cmux-recover-sessions`, `cmux-resume-sessions`, `cmux-save-sessions`,
  `cmux-session-manager`) are unaffected. (#726)

  > **Upgrade note:** if you installed praxis CLIs via `scripts/install.sh` on
  > a prior version, `~/.local/bin/cmux-browser` is now a dangling symlink.
  > Remove it manually (`rm ~/.local/bin/cmux-browser`); `scripts/verify-symlinks.sh`
  > will also flag it as `DANGLING`.

## [6.3.3] - 2026-06-25

Patch release. Hardens retrospect suppression-ledger handling and adds the
externalized critic re-scan audit trail.

### Added

- `retrospect`: conditional externalized critic re-scan tier after the
  MEMORY.md repeat scan, with `critic_diff:` recorded in the Stage 3
  suppression ledger whether the tier runs or is skipped (#702, PR #704)

### Fixed

- `retrospect-mix-check` Stop hook: Gate-8 now requires the `critic_diff:`
  ledger line alongside `worst_agent_failure:` and `self_adversarial:`, so
  Stage 3 cannot silently omit the conditional critic tier outcome (#702,
  PR #704)
- `retrospect`: Gate-8 self-incrimination and ledger-laundering hardening for
  suppression-ledger reports (#700, #703)

## [6.3.1] - 2026-06-16

Patch release. Closes the retrospect Stage-3 fence-omission bypass (#666).

### Fixed

- `retrospect-mix-check` Stop hook: a free-form / localized Stage 3 report that omitted the `<!-- retrospect:distribution begin -->` fence evaded all identifier checks, so the hook exited 0 and every gate (Gate-1..7, incl. the post-compaction Gate-7) silently no-op'd — "the gate exists but does not fire", one level deeper than "rule exists ≠ retrieval". The gate now anchors on a session-scoped *retrospect-active marker* (set at skill-invocation time, format-independent) and blocks on `marker AND table-shaped AND no-fence AND not-Stage-4`, so the fence can no longer be omitted to bypass the gates. Prose-only pre-Stage-3 clarification stops still pass through (#666, PR #667)

### Added

- `retrospect-active-marker` preflight hook (multi-event: `PreToolUse(Skill)` + `UserPromptSubmit`): maintains a session-scoped marker recording that a retrospect Stage 3 report is owed in the current turn, independent of the agent's output format. Foundation for the #666 gate above (#666, PR #667)

## [6.3.0] - 2026-06-11

17 PRs since 6.2.0. Minor release. Headline changes: a reachability gate for
applied-on-branch claims (#661), the `readonly-verify-deferral-gate` Stop hook
(#642), a post-compaction receipt gate for retrospect (#639), and automated
GitHub Releases from CHANGELOG (#631). Plus the #647 audit follow-ups
(stop-hook JSON signal unification, fail-open coverage, transcript-utils
consolidation) and CI supply-chain hardening.

### Added

- `merge-state-claim-gate` hook: `applied` claim kind — a reachability gate for "X is applied on branch B" claims in the final message. General state queries no longer release an applied claim; only reachability evidence (`git merge-base --is-ancestor`, a same-command `--json` state+baseRefName query, `git branch --contains`) does. Companion `external-write-falsify-check` Check 4 requires a reachability probe for same-line branch+applied claims in external write bodies (strict mode via `PRAXIS_APPLIED_CLAIM_STRICT=1`) (#656, PR #661)
- `block-personal-asset-leak` hook: second opt-in marker class — personal-repo `<owner>/<repo>` references, opt-in via `PRAXIS_PERSONAL_REPO_OWNERS` (unset keeps the existing dotfiles-only behavior). Matcher surface extended `Bash` → `Write|Edit|Bash` with lazy fail-open target-repo discrimination and a dotted-hostname guard against worktree-path false positives (#658, PR #659)
- `readonly-verify-deferral-gate` hook (Stop, advisory): detects the anti-pattern of *offering* to run a read-only verification ("should I check?", "진행할까요?") instead of just running it and pasting the result — the inverse of the sibling `completion-signal-gate` (#642)
- `retrospect`: Gate-7 post-compaction receipt gate — a session-level Stop-hook structural check that turns the Stage 2 "compaction + readable transcript" prose MUST into a machine-checkable receipt, after the salient-window default recurred in two independent sessions (#600, PR #639)
- `ci`: automated GitHub Releases from CHANGELOG — `.github/workflows/release.yml` on `v*` tag push / `workflow_dispatch` builds the release body via the new shared `scripts/extract-changelog-section.sh` (13-case fixture test suite included) (#631)
- `tests`: behavior test suite for `scripts/install.sh` / `scripts/verify-symlinks.sh` — 35 cases covering the detection/conflict branches the happy-path smoke never reaches (#647, PR #660)

### Changed

- `hooks`: Stop-hook signal mechanism unified on stdout JSON. The 3 python advisory hooks move from stderr+exit (effectively invisible to the user at exit 0) to `systemMessage` / `{decision: "block"}` JSON — per-hook block behavior unchanged, advisory visibility improved (#647, PR #657)
- `hooks`: transcript JSONL scan logic hoisted into a single `hooks/_lib/_transcript.py` SoT (public API 7 functions + 1 constant); 9 hooks converted, ~430 duplicated lines removed (#643, PR #652)
- `hooks`: `@fail_open` applied to the 9 standalone-executed hooks that lacked it; dispatch-covered vs standalone classification documented in DESIGN.md; new `check-plugin-manifests` Rule 15 invariant prevents regression (#645, PR #653)
- `retrospect`: SKILL.md split into `references/` (1,592 → 1,283 lines) — normative body retained in place, report template / worked examples moved to reference docs (#646, PR #654)
- `docs`: ARCHITECTURE.md gains an up-front "Architectural shape" section naming the 4 wiring patterns with anchor links (#648, PR #649); README prose converted to English with CLI docs synced (#637)
- `ci`: GitHub Actions pinned to commit SHAs (#633); `github/codeql-action` 3.36.2 → 4.36.2 (#635); `gitleaks/gitleaks-action` 2.3.9 → 3.0.0 (#634)

### Fixed

- `retrospect`: Stage 1.5 Signal-4 index byte threshold lowered below the observed host load budget (`PRAXIS_RETROSPECT_INDEX_BYTE_THRESHOLD` default 30720 → 24000) and an event-driven trigger added — an observed host truncation warning now fires Signal 4 regardless of the numeric thresholds (#651)
- audit LOW batch (#647, PR #655) — `session-intent` hook state write made atomic via `tempfile.mkstemp` + `os.replace` (H7); over-general bare `cmux-delegate` triggers `"delegate"`/`"new session"` narrowed to 5 compound phrases (S1); retrospect frontmatter delegate agent names qualified to `oh-my-claudecode:` canonical form (S4); advisory-nudge "Never block" INDEX.md wording corrected to state the 2 exceptions (H1)

## [6.2.0] - 2026-06-05

24 PRs since 6.1.3. Minor release. Headline changes: the single-process hook
dispatch runner (ADR-0002) collapses the `(PreToolUse, Bash)` hook group into one
process, and `block-pr-without-precommit-evidence` gains a `--body-file`
path-not-found diagnostic. Plus retrospect / falsify-gate fixes and CI hardening.

### Added

- `block-pr-without-precommit-evidence` hook: a distinct `--body-file not found` diagnostic. When `--body-file` names a path absent on disk (a relative path resolves against the hook's own cwd, not the PR worktree) and no inline body is present, the hook now emits a path-not-found message advising an absolute path instead of the misleading generic token-missing block (#608, PR #624)

### Changed

- `hooks`: single-process dispatch runner (ADR-0002). The `(PreToolUse, Bash)` hook group now executes through one `hooks/_dispatch.sh` → `_dispatch.py` process instead of N separate wrapper invocations, cutting per-Bash-call fork overhead. Each dispatched hook's behavior is unchanged. ADR (#614), runner (#615), build wiring (#616), finalize (#619), orphaned dispatch-only wrapper removal (#618, PR #620)
- `check-plugin-manifests` CI: two new invariants — opt-in wrapper byte-identity (Rule 6c) guarding `external-write-falsify-check.sh` against stale/missing drift (#605, PR #621), and docs/hook redirect-stub parity (Rule 14) requiring a byte-identical stub per hook dir and blocking orphans (#606, PR #622)
- `hooks`: shared-`_lib` consolidation — option-text collection (#601) and git-argv parsing (#597) hoisted into `_lib` for cross-hook reuse
- `skills`: `bypass-review` demoted from skill to CLI tool (no `SKILL.md`; not invocable as `/praxis:*`) (#583); in-body Triggers bullets retired in favor of frontmatter (#593); CLI script list single-sourced (#581)
- `ci`: workflows consolidated and triggers deduped (#592); reviewdog inline static-review actions added (#588)

### Fixed

- `output-block-falsify-advisory` hook: the deny/ask messages now state the line-start (`startswith`) requirement, so a `Falsified:` placed mid-line no longer triggers the same opaque ×2 block (#598, PR #623)
- `retrospect`: friction pre-scan now full-enumerates a readable transcript even when a compaction summary is present, rather than defaulting to the summary's salient narrative; the verbal-summary fallback is restricted to a genuinely unreachable transcript (#600, PR #625)
- `block-rename-sweep-survivors` hook: the survivor-scan subprocess call now has a timeout (#609)
- `hooks`: scope-confirm log routed to the host-neutral `praxis_home` state dir (#612)
- `skills`: `restore-sessions` trigger deduped (#589); `cmux-resume` Iron Law corrected (#586)
- `docs`: `codex-review-wrap` citations corrected (#610); the missing `[6.1.2]` changelog entry backfilled (#611)

### Security

- `ci`: credential persistence disabled in `actions/checkout`, so the `GITHUB_TOKEN` is no longer left in the runner git config after checkout (#596)

## [6.1.3] - 2026-06-04

### Added
- `retrospect`: 5th deterministic pre-scan lane `self_correction` — detects a mistake the agent caught and self-corrected mid-session (same intent + changed oracle/target/basis + prior result *wrong* not *errored*), the event the narrative pre-scan is most likely to self-servingly omit. Signature scan → per-candidate LLM judge → genuine self-corrections promote to friction events (`origin: self_correction`); every judged drop is recorded in a `self_correction` ledger, and the ledger fence is emitted even on the 0-friction early-exit path. The fence lives outside the `retrospect:distribution` boundary, so the Stop-hook parser is unaffected (#576)
- `retrospect`: three honest-labeling improvements rooted in "form-compliance crowding out intent" — (1) Stage 2 pre-agent artifact probe (read a directly-readable artifact before the MANDATORY tracer call and fold the confirmed fact into the briefing, or record `probe skipped:`), (2) per-finding behavioral-label falsification (a dual-nature finding narrowed to a lone `behavioral` label must carry a `behavioral-label-justify:` line or list all categories), and (3) a Stage 1.5 carry-forward `probe-unrunnable` branch (retain-by-default + bounded-drop escape via `PRAXIS_RETROSPECT_UNRUNNABLE_DROP_CYCLES`, default 3). Prose-only; Stop-hook parser unaffected (#577)

### Fixed
- `block-sciomc-finding-commit` hook: the finding-marker scan now parses the transcript **role-aware** (JSONL per-entry) instead of grepping the raw tail as text. The marker corpus is restricted to assistant message `text` blocks and `Agent`/`Task` subagent tool-results; markers inside user turns, system-reminder blocks, or `Read`/`Skill` tool-results that merely *load* a SKILL.md documenting the token schema no longer self-trip the gate. The consensus-refetch check still scans the full ordered stream after the finding, so a `gh pr view … --json body` recorded as an assistant Bash tool_use still satisfies the gate (#573, PR #575)
- `retrospect`: Stage 1.5 hygiene cursor now guards against multi-session lost-update. A new **Cursor write mandate** re-reads the on-disk `.omc/state/retrospect-hygiene-cursor.json` before persisting and, when a sibling session advanced the cursor since this session's entry read, union-merges the `note` carry-forward + scan trail + batch pointer instead of plain-overwriting. Previously two interleaved `/retrospect` runs clobbered each other's findings — the second session's Write silently dropped the first session's carry-forward, breaking the Stage 1.5 carry-forward guarantee under concurrency (#568)

## [6.1.2] - 2026-06-02

2 PRs since 6.1.1. Fix-only release — no new hooks or skills.

### Fixed
- `retrospect`: Stage 1.5 hygiene cursor now guards against multi-session lost-update. A new **Cursor write mandate** re-reads the on-disk `.omc/state/retrospect-hygiene-cursor.json` before persisting and, when a sibling session advanced the cursor since this session's entry read, union-merges the `note` carry-forward + scan trail + batch pointer instead of plain-overwriting. Previously two interleaved `/retrospect` runs clobbered each other's findings — the second session's Write silently dropped the first session's carry-forward, breaking the Stage 1.5 carry-forward guarantee under concurrency (#568, PR #569)
- `commit-title-format-check` hook: `release:` prefix now whitelisted for `gh pr create` titles only. The dev→prod release PR convention `release: Production Deploy (YYYY-MM-DD)` was blocked because `release` is not a Conventional Commits type and the pattern enforces a lowercase description. The whitelist is scoped to `gh pr create` — `git commit -m "release: ..."` and `gh issue create --title "release: ..."` still block (Conventional Commits enforced) (#570, PR #571)

## [6.1.1] - 2026-06-02

### Added
- `block-personal-asset-leak` hook: PreToolUse(Bash) advisory that scans `gh issue/pr create|comment|edit|review` bodies for an absolute home-dotfiles path (`/Users/<name>/.claude/...`, `/home/<name>/.config/...`) and nudges to use the portable `~/` form or remove it — a deterministic backstop for the literal personal-asset path-leak form (semantic surfacing, MCP writes, reverse-direction, tilde, and `/projects/` worktree paths are out of scope). Every body flag is scanned, relative `--body-file` resolves against the payload cwd, and `--body "$BODY"` heredoc-variable bodies are resolved before the scan. Advisory by default; `PRAXIS_PERSONAL_LEAK_STRICT=1` escalates to a block (#565)

### Changed
- `merge-menu-review-options-advisory` hook: context-aware reviewer routing (L2) — when a merge-decision menu lacks a review option, the advisory now tailors which reviewer it recommends to the change's nature (security > data > design > ux priority) by reading the branch diff, with nearest-fork-point base resolution so routing is correct on multi-base repos. Fail-open to the static generic levers; subprocess budget capped under the manifest timeout (#564)

## [6.1.0] - 2026-06-01

### Added
- `merge-state-claim-gate` hook: Stop advisory when the final assistant message asserts a completed merge/PR/issue/worktree state (EN/KR) but no fresh `gh pr|issue view/list/merge` or GitHub-MCP pull_request/issue read appears in the recent transcript — escalates the repeatedly-hallucinated merge-state-claim family from memory to a structural gate. Fail-open, `PRAXIS_MERGE_CLAIM_BYPASS` / `PRAXIS_MERGE_CLAIM_STRICT` (#503)
- `push-remote-ref-verify` hook: PostToolUse(Bash) advisory after `git push` when the remote branch tip did not advance to the pushed SHA — guards the rotating-endpoint silent-divergence failure where a second push in a session reaches a different proxy endpoint, prints `* [new branch]`, exits 0, but never lands on the intended remote. Fail-open, `PRAXIS_PUSH_VERIFY_BYPASS` / `PRAXIS_PUSH_VERIFY_STRICT` (#539)
- `pre-output-falsification-gate` hook: advisory when an AskUserQuestion (Recommended)/evaluative option is surfaced under recent negative evidence without a disconfirming probe phrase in the question body, and when a read-only status command (status/get/list) repeats ≥3× in a session (#487)
- `retrospect`: Stage 2 multi-oracle completeness gate (Gate-6) + Stage 1.5 oracle-annotation signal 5 — stored-value falsification requires same-oracle confirmation; different-oracle results emit a separate cohort-shift finding (#489)

### Changed
- `docs`: added a single hook environment-variable registry (`docs/bypass-vars.md`) cataloguing all `PRAXIS_*` / `CLAUDE_HOOK_BYPASS_*` vars by kind (opt-out / strict / config / path-test) with their owning hook, plus a **Guard Parser Boundary** section in `SECURITY.md` documenting that the token-based guards do not decode interpreter strings (`eval`/`bash -c`/`sh -c`/`python -c`/`find -exec`) — explicit threat-model boundary instead of a regex arms race (partial of #500; long-term parser-fragility tracked separately)
- `hooks`: durable cross-session state now defaults to the host-neutral `~/.praxis/state` instead of the Claude-nested `~/.claude/state/praxis` (strike counter, phantom-path markers, and the strike state read by `postcompact-context`). `PRAXIS_STATE_DIR` still overrides the base (back-compat); strike-counter migrates existing state across once on first run, and the readers fall back to the legacy location. New `_paths` helpers (`praxis_state_dir`/`praxis_cache_dir`/`legacy_state_dir`) + layout doc `docs/runtime-state-layout.md`. Volatile `${TMPDIR}/praxis-*` caches are swept in a follow-up (partial of #527)
- `hooks`: swept the remaining 20 `_main_inner`/`main()` hand-rolled fail-open wrappers onto the shared `@fail_open` decorator (`hooks/_lib/_hook_runtime.py`), so all blocking/advisory Python hooks now use a single fail-open pattern. No behavior change; each hook's copied fail-open test block is replaced by a `main.__wrapped__` structural assertion (behaviour is verified once in `tests/test_hook_runtime.sh`) (#526)

## [6.0.3] - 2026-05-29

1 PR since 6.0.2. Hook false-positive fix only — no skill or hook-behavior additions.

### Fixed
- `hook`: `pre-edit-protected-branch-guard` now skips gitignored paths (`git check-ignore`). Gitignored files (runtime state under `.omc/state/`, build artifacts, caches) can never be committed/PR'd, so the Issue-Driven Worktree Workflow the guard enforces is categorically inapplicable — blocking them was a false positive. `.omc/plans/` was already exempt but the sibling `.omc/state/` was not; the `check-ignore` rule generalizes beyond hardcoded paths (#493)

## [6.0.2] - 2026-05-29

1 PR since 6.0.1. Packaging fix only — no skill or hook behavior changes.

### Fixed
- `manifest`: the generated Claude `plugin.json` declared only `skills`, so Claude Code registered no hooks and the entire suite stayed dormant while skills loaded. Added the missing `hooks` field (`cursor`/`opencode` already had it; regression from the ADR-0001 Phase 2 layout move) (#491)

## [6.0.1] - 2026-05-29

12 PRs since 6.0.0. All additive (new always-on/advisory hooks, session-management refinements) or internal (refactor, docs) — no breaking changes, no removed skills or hooks.

### Added
- `inject-post-compact-session-context` hook: re-injects session context after a context-compaction event (#482)
- destructive-bash-command guard hook (#478)
- sensitive-credential-file write guard hook (#477)
- advisory nudge for `&&`-chained inspection commands (#476)
- `cmux-resume`: hostname mismatch gate (#474)
- `cmux-delegate`: handoff synthesis (#462)
- skill-surface freeze gate (`scripts/`) (#473)

### Changed
- `recover-sessions`: summary-based display name (#475)
- `hooks`: extract shared emit-decision helper (#471)

### Fixed
- `retrospect`: mandate cursor read + carry-forward in Stage 1.5 (#485)

### Docs
- `retrospect`: clarify rule-violation boundary for `dismissed_candidates` (#484); specify Stage 2.7 scope window for trigger detection (#483)

## [6.0.0] - 2026-05-27

19 PRs since 5.2.0. The headline is the ADR-0001 hook-layout migration (phases 1–3): every hook moved into a role-dir collocation layout (`hooks/<role>/<name>/{impl,spec}`) with manifest-driven generation of the per-platform `hooks.json`. On top of that, several new always-on enforcement gates (commit-title / branch-name / codex-review-on-commit / worktree-edit / child-repo-issue) and the bypass-telemetry suite (Phase 1 hook + Phase 2 review CLI) change commit/PR/write-time behavior for all users — semver **major** for the structural reorganization and the new gating surface.

### Added
- `block-commit-without-codex-review` hook (PreToolUse(Bash), claude-host): hard-blocks content `git commit` when `praxis:codex-review-wrap` has not been invoked this session; escape via `[skip-codex-review]` token or `CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE=1` (#425, #426)
- `bulk-write-memory-checkpoint` advisory hook (PreToolUse(Edit|Write|NotebookEdit)): nudges a memory re-read on the 2nd write to a SoT path (vault/, wiki/, .claude/, skills/, AGENTS.md/CLAUDE.md); always exits 0, bypass via `PRAXIS_BULK_WRITE_BYPASS=1` (#443)
- `bypass-review` telemetry review CLI (Phase 2): read-only aggregation over the bypass-telemetry JSONL — groups by tool, surfaces most-bypassed rules, highlights bypass-then-error events (#456)
- `bypass-telemetry` PostToolUse hook (Phase 1): records bypass env-var usage to `~/.praxis/telemetry/` (names only, values redacted) (#454)
- `skill-gate` hook for external commands (#453)
- `worktree-edit-gate` hook (#452)
- child-repo issue-creation block hook (#451)
- branch-naming convention enforcement hook (#450)
- commit-title format enforcement hook (#449)
- autonomy-vs-convention doc template (#447)

### Changed
- ADR-0001 hook-layout migration: phase 1 test/wrapper prep (#424), phase 2 role layout + manifest (#432), phase 3 spec collocation (#435)
- standardized hook block-message format (#444)
- `retrospect`: 0-friction audit-trail enforcement (#446)

### Fixed
- `block-commit-without-codex-review`: harden the command parser — close grouped (`(git …)`), command-substitution (`$(git …)`), and separator-chained (`true;git …`) bypasses (#455)
- `block-sciomc-finding-commit`: harden finding detection (#448) and close commit bypasses (#445)

### Docs
- recorded the ADR-0001 phase 3 merge in `docs/adr/0001` §7 (#442)

## [5.2.0] - 2026-05-26

18 PRs since 5.1.0 — 9 feat plus fixes, refactors, tests, and docs. All additive — semver minor.

### Added
- `block-pr-without-precommit-evidence` hook (PreToolUse(Bash)): blocks PR creation when no pre-commit verification evidence exists in the session (#414)
- completion-signal retrieval gate hook: forces rule retrieval when a completion signal is emitted (#399)
- `gh --json` PreToolUse validator hook + accompanying test suite (#397 #410)
- label-existence verifier hook: confirms `gh pr`/`gh issue` label values actually exist in the target repo before the call (#388)
- `cross-check-hook-index-and-hosts` script: cross-validates the hook index against per-hook `hosts` classification (#416)
- `retrospect`: path-probe gate on the write path (#398); size-threshold signal added to the Stage 1.5 hygiene pass (#390)
- `codex-review-wrap`: Source-of-Truth (SoT) audit step (#396)
- Recommended-marker tier upgraded from `ask` to `deny` (#394)

### Changed
- `gh-json-validator`: bypass env-var naming aligned with sibling hooks (#411)
- dup-search gate: extract the search topic before running the overlap match (#389)
- rule 2 scope narrowed/namespaced to the praxis cwd (#409)

### Fixed
- Removed the redundant `hosts` array from all-host hooks (#408 #417)

### Docs
- Corrected `Supported hosts` to `claude, codex` for two gated hooks (#418)
- Indexed 7 previously-missing hooks in ARCHITECTURE (#407 #415)

## [5.1.0] - 2026-05-21

2 PreToolUse(Bash) blocking hooks from the Hub #2242 retrospect — additive, semver minor.

### Added
- `block-sciomc-finding-commit` hook (PreToolUse(Bash)): blocks `git commit` (not amend/merge/revert/cherry-pick/--allow-empty) when transcript tail contains sciomc/reviewer finding markers (`sibling-deviant`, `Stage N analysis/finding/complete`, `[FINDING:`, `[STAGE_COMPLETE:`, `scientist-agent`, `deep-dive`, `cross-validation`, `의미 mismatch`) AND no `gh pr|issue view ... --json body` or explicit ratification token was emitted AFTER the most recent finding. Escape hatches: `[user-approved]`/`[ratified-by-user]` token in commit message, `CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1` env var. Backs the "User-stated design is RATIFIED; AI analysis findings are DRAFTS" rule (#374 #383)
- `block-gh-issue-create-without-dup-search` hook (PreToolUse(Bash)): blocks `gh issue create` when no prior `gh search issues` / `gh issue list` / `gh issue view` exists in the same session transcript, OR when prior searches exist but no extracted keyword from `--title` overlaps with any prior search args. Escape hatches: `[dup-checked]`/`[no-search-needed]` token in title, personal-repo carve-out (`--repo devseunggwan/*`), `CLAUDE_HOOK_BYPASS_DUP_GATE=1` env var. Backs CLAUDE.md "GitHub Issue Hygiene" (#374 #383)

## [5.0.0] - 2026-05-21

3 hook removals labeled BREAKING force a semver major bump; also 2 feat + a repo-wide identifier sweep.

### Added
- `block-manufactured-action-menu` hook: affirmative-form option-label markers (`그대로 진행`, `execute now`, `as instructed`) extend the question-form set so a clarification menu surfaced after an explicit directive is caught from the option-label side; `execute` / `run it` / `implement` added to the command-signal set as the directives that pair with them (#377 #379)
- `retrospect`: Stage 4 Action 6 (hook code) creates a dedicated worktree when the hook target repo is on a protected branch, so the inline write is not blocked by `pre-edit-protected-branch-guard` (#375 #380)

### Removed (BREAKING)
- `trino-describe-first` hook + paired `-pre`/`-post` shims + spec + tests (Trino MCP-specific gate; not generic enough for upstream praxis)
- `trino-catalog-gate` hook + paired `-post` shim + spec + tests (Trino MCP-specific catalog gate)
- `cross-repo-worktree-preflight` hook + shim + spec + sibling test (org-specific worktree mismatch detector)

### Changed
- Repo-wide identifier sweep: `laplace-*` / `hubctl` / `windmill` / `signoz` / `channeltalk` / `airflow` / `laplacetec/` removed from hook code, docs, SKILL.md examples, and test fixtures
- `cross-boundary-preflight` advisory text: internal-identifier example list genericized
- `tests/test_retrospect_routing.sh`: `PRAXIS_RETROSPECT_FORBIDDEN_PATTERNS` env var lets forks extend the banned list without forking the test (#376)

## [4.1.0] - 2026-05-21

5 feat + 1 refactor + 1 fix + 1 docs accumulated since 4.0.0. All additive — semver minor.

### Added
- `output-block-falsify-advisory` hook: T2 confidence-anchoring framing detection — scans option `label` OR `description` for EN tokens (`safer`/`safest`/`clearly`/`natural fit|choice`/`obvious choice`/`default to|choice`/`prefer this`/bare `recommend(?:ed|s)?`) and KO substrings (`안전한`/`가장 안전`/`자연스러운`/`당연히`/`분명히`/`추천`/`기본값`) alongside the existing literal `(Recommended)` / `(추천)` marker check; same `Falsified:` line satisfaction; emits distinct `ANCHORING_ASK_MSG` so downstream parsers can distinguish which tier escalated; description-only `(Recommended)` and lowercase `(recommended)` now ask-escalate (intentional upgrade) (#369 #371)
- `memory-hint` hook: event coverage extended from `PreToolUse(Bash)` only to `Bash | Edit | Write | NotebookEdit | AskUserQuestion`; per-memory `hookEvents` frontmatter opt-in (default `[Bash]` preserves prior behavior); ASCII-keyword split pattern in mixed Hangul/ASCII text (#358 #361)
- `retrospect`: Stage 1.5 hygiene + Stage 2.7 audit pass (#365 #366); pre-scan checklist + per-finding ledger (#363 #370); hookable contract integration with `memory-hint` (#356 #360)

### Changed
- `momentum-rule-retrieval-gate` hook: dynamic memory load via `momentum: [merge|dispatch|force-push]` frontmatter on individual memory files; hardcoded memory cites removed in favor of trigger-family-based opt-in; static force-push fallback retained so empty memory dir still emits the actionable rule line (#359 #362)

### Fixed
- `retrospect` Action 3: symlinked global `~/.claude/CLAUDE.md` targets are now detected via `realpath` and routed through the staging file → `AskUserQuestion` 3-option (apply / 수정 / 보류) approval path; project-local `AGENTS.md` continues to use direct `Edit` (#367)

### Docs
- `docs/hook/memory-hint.md` cross-linked from `retrospect` SKILL.md Stage 4 Action 1 so reviewers see the hookable contract at the memory-write call site (#368)

## [4.0.0] - 2026-05-18

Milestone release: 4 new PreToolUse/PostToolUse hooks, codex-review-wrap critic pre-lock probe gate, retrospect Gate-5 mandate, and 4-round codex review refinements on hook batch (#347 #348 #349 #350 #351). User-directed major bump (no breaking API changes; cumulative additions since 3.17.0 warrant milestone marker).

### Added
- `bash-worktree-existence-advisory` hook: pre-Bash advisory for `cd`/`pushd`/`(cd ...)` to missing worktree paths; heredoc fused-token forms, pushd ±N stack index, subshell-local cwd tracking, trailing `)` strip (#322 #337 #347)
- `trino-catalog-gate` hook: PreToolUse 3-part SQL catalog gate (`catalog.schema.table`); items 1-3, 6-9 refinements + dead constant removal (#321 #336 #350)
- `external-write-path-existence-check` hook: advisory for `gh issue/pr` body files referencing repo paths that do not exist on disk; inline-code path extraction, `_is_phantom` os.sep prefix fix, first-token split, `#fragment`/`?query` strip, fenced-block guard (#324 #335 #348)
- `jq-config-empty-dict-advisory` hook: advisory for jq commands targeting empty/missing config dicts; `-n`/`--null-input` multi-path handling, `_SUBST_NULL_INPUT_RE` operand-aware scrub, combined-short flag `-rn`/`-nr`, token boundary lookbehind/lookahead, broken-symlink lexists (#338 #351)
- `momentum-rule-retrieval-gate` hook: pre-dispatch/pre-merge momentum gate (#326)
- `version-bump-evidence-check` hook: changelog evidence requirement before posting external version-bump issues/PRs (#327)
- `codex-review-wrap`: Step 5f spec refinements (#339); Step 5g critic pre-lock probe gate with negative-claim enumeration + probe citation format + worked examples F1/F2 (#346 #349); diminishing-returns advisory at N rounds; grep exit=1 vs exit=2 error-table clarification
- `retrospect`: Gate-5 mandate for step 7 scan (#325); gate-4 verdict wire to mix-check (#317); falsify-before-recommended label check (#233)
- `Issue & PR Conventions` section in CLAUDE.md: partial-scope PR `Refs #N` vs full-scope `Closes #N` (#352)

### Changed
- `pre-edit-protected-branch-guard` hook: detect PR-workflow repo via recent commit `(#N)` suffix signal before write-protect (#239)
- `external-write-falsify-check` hook: structural tokenization migration

### Fixed
- `bash-worktree-existence-advisory`: subshell cwd leak, spaced subshell form, pushd cwd leak (R1-R4 codex review fixups under #347)
- `external-write-path-existence-check`: fenced-block PR body sample false-positives, NUL-binary detection, lstrip `./` quirk (R1-R4 codex review fixups under #348)
- `jq-config-empty-dict-advisory`: `--arg name -n` value-operand handling, `_scan_subst_for_config_paths` -n missing path (R1-R4 codex review fixups under #351)
- `trino-catalog-gate`: `_CATALOG_REF_NC` unused constant removed (#350 codex round 1)

### Docs
- `CLAUDE.md` / `AGENTS.md` disambig: project vs global references in `docs/hook/` and skills (#334)

## [3.17.0] - 2026-05-16

### Added
- `pre-edit-md-escape-advisory` hook: warns on Edit of `.md` files with escape-sensitive tokens without a prior Read (#238)
- `output-block-falsify-advisory` hook: nudges output-block falsification gate before surfacing `(Recommended)` options (#225)
- `pre-gh-pr-create-dedup-gate` hook: runs `gh pr list --search` before `gh pr create` to surface duplicates (#240)
- `advisory-wrapper-signature-verify` hook: warns before writing wrapper code with delegation patterns (#243)
- `block-manufactured-action-menu` hook: warns when AskUserQuestion surfaces a proceed-menu after a command-intent signal (#244)
- Shared compound-Bash cascade advisory across all block hooks (#244)
- `retrospect`: falsify-before-recommended-label check (#233)

### Changed
- `pre-edit-protected-branch-guard` hook: detect PR-workflow repo before protecting write (#239)

### Fixed
- `block-ask-end-option` hook: bare Korean end-tokens in option labels (#241)
- `codex-review-wrap`: forbid `Skill("codex:review")` probe in Step 4 (#242)
- `block-pr-without-caller-evidence` hook: reads body-file for caller evidence (#226)
- `builtin-task-postuse` hook: scope task-postuse counter per call (#223)

## [3.16.0] - 2026-05-13

### Added
- `block-manufactured-action-menu` hook: block AskUserQuestion proceed-menus after command-intent (#215)
- `external-api-literal-trigger` hook: advisory for ALL_CAPS enum candidates and 3-part SQL identifiers without prior retrieval (#216)

## [3.15.0] - 2026-05-13

### Added
- `block-ask-end-option` hook: detects indirect session-end phrasing (#213)
- `RUNTIME_CONSTRAINTS.md`: runtime constraints gate for skill authoring (#212)
- `retrospect`: tool output completeness gate (#211)

## [3.14.0] - 2026-05-12

### Added
- `pre-edit-protected-branch-guard` hook: block Edit/Write on protected branches when dirty or after PR-workflow commit (#204)

## [3.13.0] - 2026-05-12

### Added
- `cross-boundary-preflight` hook: block heredoc in `gh pr/issue create`; checklist on cross-repo `--repo` writes (#205)

## [3.12.0] - 2026-05-12

### Added
- `external-write-falsify-check` hook: author-exempt detection for unverified identifiers in mapping tables (#207)
- `codex-review-wrap`: sibling-defect cross-check step (#203)

## [3.11.0] - 2026-05-12

### Added
- `verify-commit-flag-override` hook: deny `git commit` with hook/signing override flags (#194)
- `retrospect`: backing-repo gate and recommended-label red flag (#206)

### Changed
- Hook specs split into individual `docs/hook/*.md` files (#196)

## [3.10.0] - 2026-05-11

### Added
- `trino-describe-first` hook: require `DESCRIBE <table>` before Trino MCP query references (#189)
- `block-ask-end-option` hook: warn on mechanically surfaced end options in AskUserQuestion (#193)

## [3.9.0] - 2026-05-11

### Added
- `session-intent` hook: session-scope intent-pivot gate for `gh` mutating commands (#190)

## [3.8.0] - 2026-05-11

### Added
- `gh-flag-verify` hook: validate `gh` CLI flag-subcommand combinations (#191)

## [3.7.0] - 2026-05-11

### Added
- `pre-merge-approval-gate` hook: surface per-PR approval prompt for `gh pr merge` in direct sessions (#187)

## [3.6.0] - 2026-05-11

### Added
- `commit-title-length-check` hook: enforce 50-character commit title limit (#186)

## [3.5.1] - 2026-05-11

### Added
- `external-write-falsify-check` hook: nested MCP body and positional `gh` body detection (#179)

## [3.5.0] - 2026-05-11

### Added
- `external-write-falsify-check` hook: advisory opt-in hook for hypothesis-stage text before external writes (#175)

## [3.4.0] - 2026-05-11

### Added
- `retrospect`: Gate-3 evidence robustness audit in Stage 2.5 (#172)

## [3.3.0] - 2026-05-09

### Added
- `retrospect`: explicit backing-repo gate before Stage 4 issue creation (#171)

## [3.2.0] - 2026-05-09

### Added
- `codex-review-wrap`: premise verification and flip detection across review rounds (#170)
- `codex-review-wrap`: fallback when codex-companion is unavailable (#166)

## [3.1.1] - 2026-05-08

### Fixed
- `codex-review-wrap`: use direct Node invocation instead of shell wrapper (#164)

## [3.1.0] - 2026-05-07

### Added
- `block-pr-without-caller-evidence` hook: gate `gh pr create` on caller-chain evidence in PR body (#159)

## [3.0.0] - 2026-05-06

### Added
- `codex-review-route` hook: warn on `/codex:review` in multi-worktree repos (#152)
- `memory-hint` hook: surface hookable memory entries by keyword at decision time (#150)

### Removed
- `debug` skill removed (#157)
- `turbo-complete`, `turbo-setup`, `turbo-deliver`, `cmux-orchestrator` skills removed (#155)

## [2.11.0] - 2026-04-30

### Added
- `retrospect`: memory-bias gate with 4-layer reinforcement (#147)

## [2.10.1] - 2026-04-29

### Changed
- `retrospect`: resolves backing repo from skill file location (#145)

## [2.10.0] - 2026-04-29

### Added
- `completion-verify` hook: require same-turn Bash verification evidence before completion claims (#144)

## [2.9.0] - 2026-04-28

### Added
- `codex-review-wrap` skill: worktree-aware wrapper for `/codex:review` with multi-worktree disambiguation (#141)

## [2.8.1] - 2026-04-27

### Added
- `cmux-browser` skill and CLI wrapper with SPA hydration wait protocol (#133)

### Fixed
- `strike`: scope state directory to praxis-owned path (#137)

## [2.8.0] - 2026-04-27

### Fixed
- `builtin-task-postuse` hook: correct false agent-spawn signal for built-in task tools (#135)

## [2.7.0] - 2026-04-26

### Added
- `block-gh-state-all` hook: hard-block invalid `gh search --state all` flag combination (#132)

## [2.6.1] - 2026-04-24

### Fixed
- Plugin packaging: drop `hooks` override to avoid duplicate auto-load (#125)

## [2.6.0] - 2026-04-24

### Added
- Multi-platform packaging with generated manifests; build and check scripts (#123)

## [2.5.0] - 2026-04-24

### Added
- `side-effect-scan` hook: pre-Bash scan for mutating commands (`git commit/push`, `gh pr merge/create`) (#122)

### Fixed
- `cmux-orchestrator`: harden codex result parsing (#121)

## [2.4.1] - 2026-04-24

### Added
- `turbo-setup`: next-step branching guide (#93)
- `strike`: gate 3/3 reset on reflection and persuasion (#105)

### Changed
- Routing: unify provider regex style across all skills (#120)

### Fixed
- `cmux-orchestrator`: replace `grep -oP` with macOS-compatible patterns (#112)

## [2.4.0] - 2026-04-21

### Added
- `strike` / `strikes` / `reset-strikes` skills: session-scoped three-strike discipline with Stop hook block (#103)

## [2.3.3] - 2026-04-16

### Added
- Auto-register `completion-verify` Stop hook via `plugin.json` (#101)

## [2.3.2] - 2026-04-16

### Added
- `turbo-setup`: auto-open cmux workspace after worktree creation (#95)
- `retrospect`: tool friction pass and upstream feedback action (#88)

### Fixed
- CLI: document codex exec write permissions (#94)

## [2.3.1] - 2026-04-14

### Added
- Multi-provider routing: route tasks to codex, gemini, or claude by keyword (#81)
- `cmux-delegate` v2: account, session, and distribute modes (#59)
- `cmux-delegate`: `--permission-mode` argument (#61)
- `recover`: show session UUID in list output (#74)
- `recover`: surface filter reasons in output (#75)
- `recover`: deduplicate conversation chains (#73)
- `retrospect`: surface multi-action improvement proposals (#86)
- CLI symlink install + verify script (#76)

### Fixed
- `recover`: prefer internal timestamp over mtime (#72)
- `recover`: robust `/exit` detection via user-only tail (#71)
- `retrospect`: deduplicate memory entries before creating (#80)

## [2.3.0] - 2026-04-09

### Added
- `retrospect`: escalation logic and mandatory agent calls (#50)

### Changed
- Consolidated workflow into `turbo-completion` skill (#55)

### Removed
- `brainstorm` skill removed (#53)

## [2.2.0] - 2026-04-09

### Added
- `cmux-delegate` skill: delegate tasks to independent cmux sessions (#48)

## [2.1.0] - 2026-04-08

### Added
- `turbo-implement` skill (#44)

### Changed
- All skills made project-agnostic (#46)
- Merged `finish-branch` into `turbo-deliver`

## [2.0.0] - 2026-04-08

### Changed
- Project renamed from `my-skills` to `praxis`; all references updated (#40)

## [1.4.0] - 2026-04-08

### Added
- `cmux-save-sessions` and `cmux-resume-sessions` skills (#39)

## [1.3.0] - 2026-03-31

### Added
- `retrospect` skill: session retrospect with friction analysis (#37)

### Fixed
- `cmux-recover-sessions`: workspace creation and plain mode (#32)

## [1.2.0] - 2026-03-27

### Added
- `cmux-session-manager` skill: daily session lifecycle management (#28)

### Changed
- `recover-sessions-cmux` renamed to `cmux-recover-sessions` (#30)

## [1.1.0] - 2026-03-26

### Added
- `recover-sessions` skill: bulk session recovery after power loss (#18)
- `cmux-recover-sessions` skill: cmux-backed session recovery (#20)
- Unified workflow skills: turbo-setup, turbo-deliver, cmux-orchestrator (#13, #24)
- `pr-dev-to-prod` skill: release PR from dev to prod (#3)
- Plugin-based architecture for install-claude-stack (#7)

### Changed
- Shared scan module extracted from skills (#26)

### Fixed
- `finish-branch`: reorder compounding before merge (#16)

[Unreleased]: https://github.com/devseunggwan/praxis/compare/v3.17.0...HEAD
[3.17.0]: https://github.com/devseunggwan/praxis/compare/v3.16.0...v3.17.0
[3.16.0]: https://github.com/devseunggwan/praxis/compare/v3.15.0...v3.16.0
[3.15.0]: https://github.com/devseunggwan/praxis/compare/v3.14.0...v3.15.0
[3.14.0]: https://github.com/devseunggwan/praxis/compare/v3.13.0...v3.14.0
[3.13.0]: https://github.com/devseunggwan/praxis/compare/v3.12.0...v3.13.0
[3.12.0]: https://github.com/devseunggwan/praxis/compare/v3.11.0...v3.12.0
[3.11.0]: https://github.com/devseunggwan/praxis/compare/v3.10.0...v3.11.0
[3.10.0]: https://github.com/devseunggwan/praxis/compare/v3.9.0...v3.10.0
[3.9.0]: https://github.com/devseunggwan/praxis/compare/v3.8.0...v3.9.0
[3.8.0]: https://github.com/devseunggwan/praxis/compare/v3.7.0...v3.8.0
[3.7.0]: https://github.com/devseunggwan/praxis/compare/v3.6.0...v3.7.0
[3.6.0]: https://github.com/devseunggwan/praxis/compare/v3.5.1...v3.6.0
[3.5.1]: https://github.com/devseunggwan/praxis/compare/v3.5.0...v3.5.1
[3.5.0]: https://github.com/devseunggwan/praxis/compare/v3.4.0...v3.5.0
[3.4.0]: https://github.com/devseunggwan/praxis/compare/v3.3.0...v3.4.0
[3.3.0]: https://github.com/devseunggwan/praxis/compare/v3.2.0...v3.3.0
[3.2.0]: https://github.com/devseunggwan/praxis/compare/v3.1.1...v3.2.0
[3.1.1]: https://github.com/devseunggwan/praxis/compare/v3.1.0...v3.1.1
[3.1.0]: https://github.com/devseunggwan/praxis/compare/v3.0.0...v3.1.0
[3.0.0]: https://github.com/devseunggwan/praxis/compare/v2.11.0...v3.0.0
[2.11.0]: https://github.com/devseunggwan/praxis/compare/v2.10.1...v2.11.0
[2.10.1]: https://github.com/devseunggwan/praxis/compare/v2.10.0...v2.10.1
[2.10.0]: https://github.com/devseunggwan/praxis/compare/v2.9.0...v2.10.0
[2.9.0]: https://github.com/devseunggwan/praxis/compare/v2.8.1...v2.9.0
[2.8.1]: https://github.com/devseunggwan/praxis/compare/v2.8.0...v2.8.1
[2.8.0]: https://github.com/devseunggwan/praxis/compare/v2.7.0...v2.8.0
[2.7.0]: https://github.com/devseunggwan/praxis/compare/v2.6.1...v2.7.0
[2.6.1]: https://github.com/devseunggwan/praxis/compare/v2.6.0...v2.6.1
[2.6.0]: https://github.com/devseunggwan/praxis/compare/v2.5.0...v2.6.0
[2.5.0]: https://github.com/devseunggwan/praxis/compare/v2.4.1...v2.5.0
[2.4.1]: https://github.com/devseunggwan/praxis/compare/v2.4.0...v2.4.1
[2.4.0]: https://github.com/devseunggwan/praxis/compare/v2.3.3...v2.4.0
[2.3.3]: https://github.com/devseunggwan/praxis/compare/v2.3.2...v2.3.3
[2.3.2]: https://github.com/devseunggwan/praxis/compare/v2.3.1...v2.3.2
[2.3.1]: https://github.com/devseunggwan/praxis/compare/v2.3.0...v2.3.1
[2.3.0]: https://github.com/devseunggwan/praxis/compare/v2.2.0...v2.3.0
[2.2.0]: https://github.com/devseunggwan/praxis/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/devseunggwan/praxis/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/devseunggwan/praxis/compare/v1.4.0...v2.0.0
[1.4.0]: https://github.com/devseunggwan/praxis/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/devseunggwan/praxis/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/devseunggwan/praxis/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/devseunggwan/praxis/releases/tag/v1.1.0
