# Changelog

All notable changes to praxis are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `block-sciomc-finding-commit` hook (PreToolUse(Bash)): blocks `git commit` (not amend/merge/revert/cherry-pick/--allow-empty) when transcript tail contains sciomc/reviewer finding markers (`sibling-deviant`, `Stage N analysis/finding/complete`, `[FINDING:`, `[STAGE_COMPLETE:`, `scientist-agent`, `deep-dive`, `cross-validation`, `의미 mismatch`) AND no `gh pr|issue view ... --json body` or explicit ratification token was emitted AFTER the most recent finding. Escape hatches: `[user-approved]`/`[ratified-by-user]` token in commit message, `CLAUDE_HOOK_BYPASS_SCIOMC_GATE=1` env var. Backs the "User-stated design is RATIFIED; AI analysis findings are DRAFTS" rule (#374)
- `block-gh-issue-create-without-dup-search` hook (PreToolUse(Bash)): blocks `gh issue create` when no prior `gh search issues` / `gh issue list` / `gh issue view` exists in the same session transcript, OR when prior searches exist but no extracted keyword from `--title` overlaps with any prior search args. Escape hatches: `[dup-checked]`/`[no-search-needed]` token in title, personal-repo carve-out (`--repo devseunggwan/*`), `CLAUDE_HOOK_BYPASS_DUP_GATE=1` env var. Backs CLAUDE.md "GitHub Issue Hygiene" (#374)

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
