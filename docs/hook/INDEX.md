# Hook Index (categorized)

Praxis hooks grouped by enforcement role. For the full per-hook spec, follow
each link. For the flat tabular listing (with event column) and the generated
role/event/host/env-var summary, see the
[Hook Operating Matrix](../hook-operating-matrix.md);
[ARCHITECTURE.md → Hook index](../../ARCHITECTURE.md#hook-index) points at both
documents rather than repeating them. For the rules that govern a
user-facing surface but are **not** (yet) hooked, ranked by user-facing cost, see
[Rule backstop gaps](RULE-BACKSTOP-GAPS.md).

Cross-cutting: every preflight-gate block message shares one five-field format —
see [block-message-format](block-message-format.md).

Several gates fire on the **same** action verb. Where they do, the first block
on that verb enumerates every gate the verb owes, so the requirements are
satisfiable in one authoring pass instead of one retry turn per gate
(issue #873). The verb → gate mapping is code, not prose — `_VERB_CHECKLISTS`
in [`hooks/_lib/block_message.py`](../../hooks/_lib/block_message.py) is the
single source, reached via `verb_gate_checklist(verb)`:

| Verb | Gates enumerated on the first block | Emitted by |
| ------ | ------------------------------------- | ------------ |
| `gh pr create` | block-pr-without-caller-evidence, block-pr-without-precommit-evidence, + related-gate pointers (block-commit-without-codex-review, output-block-falsify-advisory) | both pr-body gates |
| `gh pr merge` | momentum-rule-retrieval-gate, pre-merge-approval-gate, side-effect-scan, + conditional (gh-merge-worktree-precondition on `--delete-branch`, commit-title-length-check on `--squash`, pipefail-advisory when piped, session-intent on an undeclared mutation pivot, skill-gate-commands when opted in) | momentum-rule-retrieval-gate |
| `AskUserQuestion` | output-block-falsify-advisory, block-ask-end-option, + conditional (block-manufactured-action-menu, pr-state-refetch-gate, merge-menu-review-options-advisory, menu-mutation-tier-advisory) and advisory-only (pre-output-falsification-gate, memory-hint) | output-block-falsify-advisory |

Rows are **host-scoped** (issue #1245). Several enumerated gates carry a `hosts`
whitelist while the hook printing the checklist does not, so the unfiltered text
asked for tokens no installed gate requires and offered bypasses that do not
exist on that platform — the `gh pr create` pointer to the `claude`-only
`block-commit-without-codex-review` is the shipped instance. `verb_gate_checklist`
takes the running host (`hooks/_lib/_hosts.py → runtime_host()`, read from the
dispatcher's argv) and drops every `← <hook>` row the manifest does not install
there. An unresolvable or unknown host keeps every row: naming an absent gate
wastes a reader's time, while dropping a present one hides the next hard block.

The emitting hook writes the checklist to **both** its decision reason and
stderr, because neither channel alone reaches the model in every case
(issue #932):

- `hooks/_lib/_dispatch.py:195-201` surfaces only the **first** deny's stdout
  decision, and these gates run in parallel on the same command — so a
  checklist carried only in the reason string is dropped exactly when a sibling
  gate denies first, the multi-gate case the checklist exists for;
- stderr is forwarded for every hook, but a PreToolUse hook's stderr is fed to
  the model only when the dispatcher exits 2 — the deny path (`:201`), never
  the ask path (`:207 return 0`).

Issue #873 shipped stderr-only, and the `AskUserQuestion` checklist — which
travels the exit-0 ask path — went nowhere.

A checklist that under-enumerates reproduces the defect it exists to fix, and
reads as authoritative while doing it — the first draft of the
`AskUserQuestion` entry named 3 of the 7 registered hooks.
`tests/test_block_message.py::test_ask_and_merge_checklists_match_the_hook_registry`
pins the entry against `hooks/manifest.json` so it cannot silently fall behind.

`git commit` and `gh issue create` also carry several gates each and are not
covered yet — adding a verb means adding its registry entry *and* wiring one
hook to emit it, so an unwired entry is never added on its own.

---

## preflight-gate

Pre-tool blocking / ask-user gates. Run **before** the tool executes; can
`deny`, `ask`, or `defer`. State-changing focus — these are the hard-stop or
confirmation-prompt layer.

| Hook | Trigger | Purpose |
| ------ | --------- | --------- |
| [block-gh-state-all](../../hooks/preflight-gate/block-gh-state-all/spec.md) | PreToolUse | Hard-block invalid `gh search ... --state all` flag combo |
| [approval-premise-reread-gate](../../hooks/preflight-gate/approval-premise-reread-gate/spec.md) | PreToolUse | Ask before an irreversible production call whose approval premise may have dissolved since it was granted — fires on a mutating Bash/MCP call carrying a production phase marker, and is satisfied by a `# approval-premise:ack <premise re-read>` attestation rather than a bypass token (issue #1043) |
| [block-unmatched-glob](../../hooks/preflight-gate/block-unmatched-glob/spec.md) | PreToolUse | Hard-block a command whose unquoted glob matches nothing — zsh aborts it before it runs |
| [gh-flag-verify](../../hooks/preflight-gate/gh-flag-verify/spec.md) | PreToolUse | Block `gh <subcmd>` calls with flags not in the subcommand's accepted set |
| [gh-json-validator](../../hooks/preflight-gate/gh-json-validator/spec.md) | PreToolUse | Block `gh <subcmd> --json <fields>` calls whose field names are not in the subcommand's valid JSON projection — issue #391 |
| [gh-label-verify](../../hooks/preflight-gate/gh-label-verify/spec.md) | PreToolUse | Block `gh (issue\|pr) (create\|edit)` calls whose `--label` values are absent from the target repo's label set — issue #385 |
| [foreground-poll-loop-guard](../../hooks/preflight-gate/foreground-poll-loop-guard/spec.md) | PreToolUse | Block foreground Bash poll-loops (`for/while/until … sleep`) that would hit the 120s ceiling (Exit 143); redirects to native async-wait primitives — issue #745 |
| [block-ask-end-option](../../hooks/preflight-gate/block-ask-end-option/spec.md) | PreToolUse | Block `AskUserQuestion` options carrying end-option markers when no stop signal present |
| [block-manufactured-action-menu](../../hooks/preflight-gate/block-manufactured-action-menu/spec.md) | PreToolUse | Warn or block when `AskUserQuestion` surfaces a "shall we proceed?" menu after user already issued a command-intent signal |
| [block-pr-without-caller-evidence](../../hooks/preflight-gate/block-pr-without-caller-evidence/spec.md) | PreToolUse | Block `gh pr create` unless the PR body contains a `Caller chain verified:` line (advisory on shipped defaults; deny via `PRAXIS_PR_EVIDENCE_STRICT` — #1186) |
| [block-pr-without-precommit-evidence](../../hooks/preflight-gate/block-pr-without-precommit-evidence/spec.md) | PreToolUse | Block `gh pr create` unless the PR body declares pre-commit state (`Pre-commit verified:` / `verified by CI` / `n/a (reason)`); `--repo` is not a bypass (advisory on shipped defaults; deny via `PRAXIS_PR_EVIDENCE_STRICT` — #1186) |
| [side-effect-scan](../../hooks/preflight-gate/side-effect-scan/spec.md) | PreToolUse | Ask before commands with collateral side effects (`git commit/push`, `gh pr merge/create`, `kubectl apply`) |
| [commit-title-length-check](../../hooks/preflight-gate/commit-title-length-check/spec.md) | PreToolUse | Ask when `git commit` title exceeds 50 chars |
| [commit-title-format-check](../../hooks/preflight-gate/commit-title-format-check/spec.md) | PreToolUse | Block `git commit`, `gh pr create`, `gh issue create` when title does not match Conventional Commits format |
| [commit-message-paren-check](../../hooks/preflight-gate/commit-message-paren-check/spec.md) | PreToolUse | Block `git commit` when a message line opens a pseudo-scope release-please's parser cannot close, which silently drops the commit from the CHANGELOG |
| [branch-name-check](../../hooks/preflight-gate/branch-name-check/spec.md) | PreToolUse | Block branch creation (`checkout -b`, `switch -c`, `worktree add -b`) when the new branch name does not match the configured regex |
| [pre-merge-approval-gate](../../hooks/preflight-gate/pre-merge-approval-gate/spec.md) | PreToolUse | Surface per-PR approval prompt for `gh pr merge` in direct sessions |
| [fan-out-scope-gate](../../hooks/preflight-gate/fan-out-scope-gate/spec.md) | PreToolUse | Surface an approval prompt from the 2nd delegation target created in one turn |
| [gh-merge-worktree-precondition](../../hooks/preflight-gate/gh-merge-worktree-precondition/spec.md) | PreToolUse | Block `gh pr merge --delete-branch` when the PR's live head branch is still checked out in another `git worktree` (issue #798) |
| [anchor-comment-gate](../../hooks/preflight-gate/anchor-comment-gate/spec.md) | PreToolUse + PostToolUse | Block a PR verification anchor comment (`### 검증 …`) missing one of its five required fields, decided from the body alone with no network; after the post, read the published comment back through the API and re-check it plus SHA freshness and diff coverage (issue #947) |
| [worktree-prune-snapshot-gate](../../hooks/preflight-gate/worktree-prune-snapshot-gate/spec.md) | PreToolUse | Block a bare `git worktree prune` when no `git worktree list --porcelain` snapshot was taken in the session — prune is repository-wide, so without the snapshot a removed sibling registration is unrecoverable evidence-wise (#870) |
| [pr-state-refetch-gate](../../hooks/preflight-gate/pr-state-refetch-gate/spec.md) | PreToolUse | Warn or block when `AskUserQuestion` names a PR number + merge-intent keyword whose live `gh pr view` state is already MERGED/CLOSED — issue #719 |
| [cross-boundary-preflight](../../hooks/preflight-gate/cross-boundary-preflight/spec.md) | PreToolUse | Block heredoc body in `gh pr/issue create`; ask with four-point checklist on cross-repo `--repo` writes |
| [rejected-mutation-reconsent-gate](../../hooks/preflight-gate/rejected-mutation-reconsent-gate/spec.md) | PreToolUse | Ask again before a Bash mutation or a worker dispatch whose target (`s3://`/`gs://` prefix, SQL table) the user already refused in a rejected `AskUserQuestion` (#1007, #1035) |
| [pre-edit-protected-branch-guard](../../hooks/preflight-gate/pre-edit-protected-branch-guard/spec.md) | PreToolUse | Block Edit/Write/NotebookEdit on protected branches (main/dev/prod/master) outside the expected worktree workflow |
| [worktree-edit-gate](../../hooks/preflight-gate/worktree-edit-gate/spec.md) | PreToolUse | Block Edit/Write on source files when the repo HEAD is on a base branch — opt-in via `PRAXIS_WORKTREE_ENFORCED_REPOS`; default no-op (issue #437) |
| [write-decision-consistency-gate](../../hooks/preflight-gate/write-decision-consistency-gate/spec.md) | PreToolUse | Block a Write/Edit whose Decisions block also states a constraint, unless a `Consistency:` line says they do not contradict (issue #905) |
| [pre-gh-pr-create-dedup-gate](../../hooks/preflight-gate/pre-gh-pr-create-dedup-gate/spec.md) | PreToolUse | Run `gh pr list --search` before `gh pr create`; hard-block on duplicate or repo-resolution failure |
| [verify-commit-flag-override](../../hooks/preflight-gate/verify-commit-flag-override/spec.md) | PreToolUse | Deny `git commit` invocations that override hooks / signing without env verification |
| [block-commit-without-codex-review](../../hooks/preflight-gate/block-commit-without-codex-review/spec.md) | PreToolUse | Block content `git commit` when `praxis:codex-review-wrap` has not been invoked this session (deny when codex is on PATH or `PRAXIS_CODEX_REVIEW_STRICT` set, advisory otherwise — #1187) |
| [block-rename-sweep-survivors](../../hooks/preflight-gate/block-rename-sweep-survivors/spec.md) | PreToolUse | Block `git commit` when a rename sweep (≥3 identical 1:1 renames) has surviving occurrences in the tracked tree |
| [block-gh-issue-create-without-dup-search](../../hooks/preflight-gate/block-gh-issue-create-without-dup-search/spec.md) | PreToolUse | Block `gh issue create` when no prior duplicate search overlaps the new issue's title keywords |
| [block-child-repo-issue-create](../../hooks/preflight-gate/block-child-repo-issue-create/spec.md) | PreToolUse | Block `gh issue create` on hub-mediated org child repos; redirects agent to the hub creation skill (opt-in via `PRAXIS_HUB_MEDIATED_ORGS`) |
| [skill-gate-commands](../../hooks/preflight-gate/skill-gate-commands/spec.md) | PreToolUse | Block configured external-mutation commands (`gh pr create`, `gh pr merge`, `git push origin`) when the required skill has not been invoked this session; NO-OP by default; opt-in via `PRAXIS_SKILL_GATED_COMMANDS` (issue #438) |
| [session-intent](../../hooks/preflight-gate/session-intent/spec.md) | UserPromptSubmit + PreToolUse | Gate read-intent → mutation-pivot session drift on `gh` mutating commands |
| [retrospect-active-marker](../../hooks/preflight-gate/retrospect-active-marker/spec.md) | PreToolUse(Skill) + UserPromptSubmit | Record a session-scoped retrospect-active marker so the Stop gate can detect Stage-3 fence omission (#666) |

---

## advisory-nudge

Pre-tool stderr hints. **Default: never block** — emit reminders for recurrent
patterns so the agent can self-correct. Documented exceptions escalate beyond
stderr: hooks marked "opt-in strict" in the table emit `ask`/`deny`/block only
when their strict-mode env var is set, and `output-block-falsify-advisory`
emits `ask` — without any env gate — when a `(Recommended)`/anchoring option
appears without an accompanying `Falsified:` line (issue #393 made the T1
tier a hard deny; #899 restored it to `ask`).
Fail-open on infrastructure errors by design.

| Hook | Trigger | Purpose |
| ------ | --------- | --------- |
| [momentum-rule-retrieval-gate](../../hooks/advisory-nudge/momentum-rule-retrieval-gate/spec.md) | PreToolUse | Advisory nudge at high-momentum action points (`gh pr merge`, `cmux new-workspace`, `git push --force`) — surfaces relevant CLAUDE.md rules + memory entries to prevent "Loaded ≠ Retrieved" failures |
| [cli-flag-incompat-advisory](../../hooks/advisory-nudge/cli-flag-incompat-advisory/spec.md) | PreToolUse | Advisory nudge for known mode-incompatible flag combos (`git merge-tree --name-only` 3-arg form, `kubectl --use-protocol-buffers`) |
| [inspection-chain-advisory](../../hooks/advisory-nudge/inspection-chain-advisory/spec.md) | PreToolUse | Advisory nudge when 2+ inspection-only commands are chained with `&&` (non-match exit silently drops downstream probes) — issue #469 |
| [pipefail-advisory](../../hooks/advisory-nudge/pipefail-advisory/spec.md) | PreToolUse | Advisory nudge when a mutating `git`/`gh` command is piped into `tail`/`head`/`grep` without `set -o pipefail` (non-zero exit masked by the sink's own exit 0) — issue #788 |
| [fallback-negative-warn](../../hooks/advisory-nudge/fallback-negative-warn/spec.md) | PreToolUse | Advisory nudge when a suppressed-stderr `\|\|` fallback (`2>/dev/null ... \|\| echo "(no session)"`) prints negative-verdict vocabulary — command failure and true-negative collapse into the same string — issue #893 |
| [secret-print-redaction-advisory](../../hooks/advisory-nudge/secret-print-redaction-advisory/spec.md) | PreToolUse | Advisory nudge when a live Bash command or an agent-authored script (Write/Edit/heredoc/echo-append) both fetches a secret (`hubctl token fetch`, `aws secretsmanager get-secret-value`, `vault kv get`, ...) and routes the value to stdout unmasked (2-signal AND gate; masked/digest output and bare interactive fetches stay silent) — issue #827 |
| [destructive-bash-guard](../../hooks/advisory-nudge/destructive-bash-guard/spec.md) | PreToolUse | Advisory (or strict-mode `ask`) before destructive bash (`rm -rf`, `sudo`/`doas`, `dd`, `mkfs`, `chmod -R 777`, block-device redirects, `git clean -f`/`reset --hard`, `find -delete`, `truncate -s 0`, `shred`, fork bomb) — issue #463 |
| [protected-paths-guard](../../hooks/advisory-nudge/protected-paths-guard/spec.md) | PreToolUse | Advisory (or strict-mode block) on Edit/Write/NotebookEdit calls targeting sensitive files (`.env`, private keys, `.ssh/`, `credentials`, `.netrc`, `.npmrc`) — issue #464 |
| [settings-path-advisory](../../hooks/advisory-nudge/settings-path-advisory/spec.md) | PreToolUse | Advisory (or strict-mode block) on Edit/Write calls targeting a Claude Code settings file (`.claude/settings.json`, `.claude/settings.local.json`, `managed-settings.json`) — names the permission/hook keys the write carries so the agent states who asked for the change; the follow-up-write lane of RULE-BACKSTOP-GAPS gap #4 — issue #1337 |
| [memory-hint](../../hooks/advisory-nudge/memory-hint/spec.md) | PreToolUse | Surface hookable memory entries by keyword at decision-construction time |
| [external-write-falsify-check](../../hooks/advisory-nudge/external-write-falsify-check/spec.md) | PreToolUse (opt-in) | Warn before posting hypothesis-stage text or unverified applied-on-branch claims (#656) to PR / issue / Slack / Notion |
| [external-api-literal-trigger](../../hooks/advisory-nudge/external-api-literal-trigger/spec.md) | PreToolUse | Advisory nudge when ALL_CAPS enum candidates or 3-part SQL identifiers are written without prior retrieval verification |
| [output-block-falsify-advisory](../../hooks/advisory-nudge/output-block-falsify-advisory/spec.md) | PreToolUse | Output-block falsification gate before surfacing `(Recommended)` options or bulk-action commands — T1 (exact `(Recommended)` marker without `Falsified:` line) and T2 (confidence-anchoring tokens) both emit `ask` (T1 was a hard deny until #899); on `Bash`, a blast-radius two-factor predicate (irreversible verb + shared-surface token in one command segment) also emits `ask` (#1010) |
| [source-citation-probe-gate](../../hooks/advisory-nudge/source-citation-probe-gate/spec.md) | PreToolUse | Advisory when an external-write body (gh / Slack / Notion) cites source facts (file:line, inline-code call syntax, test-semantics claims) with no read-probe in the recent transcript and no in-body `Probe:` / `[verified]` basis — issue #830 |
| [composed-command-gate](../../hooks/advisory-nudge/composed-command-gate/spec.md) | PreToolUse | Advisory when an external-write body's fenced blocks carry `$` command lines with no counterpart among this session's Bash calls — the shape where the pasted output is genuine and only the command line above it was composed; clears on head-binary + 60% operand overlap, on env-var/placeholder substitution, or on `[transcribed]` — issue #1117 |
| [caller-probe-gate](../../hooks/advisory-nudge/caller-probe-gate/spec.md) | PreToolUse | Advisory when an external-write body asserts a code defect (fix( title / bug label / defect token) while citing code, with no call-site search in the recent transcript and no in-body `Caller-probe:` basis — a Read of the cited file deliberately does not clear — issue #906 |
| [unenforced-step-advisory](../../hooks/advisory-nudge/unenforced-step-advisory/spec.md) | PreToolUse | Advisory naming the MANDATORY workflow step that has no gate of its own, at the action that precedes it — code-reviewer dispatch before a content commit, pre-PR / pre-merge rebase (only when `HEAD..<base>` is non-empty), open-PR enumeration before `git worktree add` / cmux dispatch; keyed on transcript absence, never blocks in default mode — issue #1064 |
| [advisory-wrapper-signature-verify](../../hooks/advisory-nudge/advisory-wrapper-signature-verify/spec.md) | PreToolUse | Advisory nudge to verify wrapped function signatures before writing wrapper/client code |
| [jq-config-empty-dict-advisory](../../hooks/advisory-nudge/jq-config-empty-dict-advisory/spec.md) | PreToolUse | Advisory nudge when `jq` reads a config file (settings.json, hooks.json, ~/.claude/*.json, ~/.codex/*.json) that is empty or invalid JSON |
| [bash-worktree-existence-advisory](../../hooks/advisory-nudge/bash-worktree-existence-advisory/spec.md) | PreToolUse | Advisory nudge when `cd <path>` targets a path that does not exist on disk |
| [cwd-relative-exec-advisory](../../hooks/advisory-nudge/cwd-relative-exec-advisory/spec.md) | PreToolUse | Soft `ask` when a Bash command runs a relative path (or relative `--body-file`) with no preceding `cd`/`pushd` while 2+ worktrees are registered — session cwd resets between calls (#852) |
| [pytest-direct-exec-advisory](../../hooks/advisory-nudge/pytest-direct-exec-advisory/spec.md) | PreToolUse | Advisory when `python`/`python3` directly executes a pytest-shaped file and recommends module execution so pytest performs collection (#909) |
| [long-foreground-call-advisory](../../hooks/advisory-nudge/long-foreground-call-advisory/spec.md) | PreToolUse | Advisory when a foreground Bash call declares an explicit `timeout` above the 180000ms status-briefing threshold; names both escape routes — `run_in_background: true` or a shorter timeout (#991) |
| [perf-multiplier-evidence-advisory](../../hooks/advisory-nudge/perf-multiplier-evidence-advisory/spec.md) | PreToolUse | Advisory when a performance multiplier or lever verdict appears in a `gh issue\|pr create/comment` body with no adjacent controlled-timing artifact (#850) |
| [n1-quantitative-claim-advisory](../../hooks/advisory-nudge/n1-quantitative-claim-advisory/spec.md) | PreToolUse | Advisory when a `gh issue\|pr create/comment` body states a sample-dependent quantitative claim (percentile / central tendency, or a `PASS`-attached measurement) with no sample size above 1, or a verdict-attached count with none of its three run-condition fields (command / collection scope / where it ran) (#949) |
| [pre-commit-staged-file-enumeration](../../hooks/advisory-nudge/pre-commit-staged-file-enumeration/spec.md) | PreToolUse | Advisory nudge before `git commit` listing staged file additions not created via Write/Edit/MultiEdit/NotebookEdit this session (heredoc / `> file` / external-script output caught before it rides into the commit) — issue #784 |
| [commit-decomposition-advisory](../../hooks/advisory-nudge/commit-decomposition-advisory/spec.md) | PreToolUse | Advisory nudge before `git commit` when the message itself says the change is more than one commit — 3+ body bullets, or a title type the body disagrees with (issue #971) |
| [comment-yap-advisory](../../hooks/advisory-nudge/comment-yap-advisory/spec.md) | PreToolUse | Advisory nudge on a Write/Edit whose authored text carries a **long and unanchored** comment run — a ≥12-line preamble over a ≤3-line declaration, or a ≥25-line run below 1 anchor per 10 lines. Volume alone never fires: a run citing issues, paths, identifiers, flags, examples or measured figures stays silent (a real 68-line sibling docstring is pinned as a negative), as do license/generated headers, commented-out code, unmapped extensions, and docstrings under the preamble detector — issue #1141 |
| [model-routing-advisory](../../hooks/advisory-nudge/model-routing-advisory/spec.md) | PreToolUse | Advisory nudge when a Bash delegation's `--model` names a **bare Claude tier** (`haiku`/`sonnet`/`opus`) mismatching the tier implied by task keywords (`find → haiku`, `implement → sonnet`, `architect/security → opus`) — the complexity→model phase only; silent for `codex:`/`gemini:`/full model IDs. Full routing tree lives in this hook's spec, so the always-loaded routing prose can shrink to a pointer — issue #786 |
| [push-remote-ref-verify](../../hooks/advisory-nudge/push-remote-ref-verify/spec.md) | PostToolUse | Advisory after `git push` when the remote branch tip did not advance to the pushed SHA (rotating-endpoint silent-divergence guard) |
| [pr-thread-resolve-advisory](../../hooks/advisory-nudge/pr-thread-resolve-advisory/spec.md) | PostToolUse | Advisory after `git push` listing the open PR's unresolved review threads, split into needs-a-reply and for-reference by their Conventional Comments label |
| [codex-review-route](../../hooks/advisory-nudge/codex-review-route/spec.md) | UserPromptSubmit | Warn when `/codex:review` runs in a multi-worktree repo (cwd mismatch risk) |
| [postcompact-context](../../hooks/advisory-nudge/postcompact-context/spec.md) | SessionStart (`compact`) | Inject session_id / cwd / branch / active PR / strike state as `additionalContext` on the session start Claude Code raises after a compaction — the event is the trigger, no transcript scan or dedup state — issues #472, #1339 |
| [external-write-path-existence-check](../../hooks/advisory-nudge/external-write-path-existence-check/spec.md) | PreToolUse | Advisory nudge when a `gh issue/pr` body file contains markdown links to repo paths that do not exist |
| [block-personal-asset-leak](../../hooks/advisory-nudge/block-personal-asset-leak/spec.md) | PreToolUse | Advisory nudge (opt-in strict: block) on absolute home-dotfiles paths in `gh issue/pr` bodies, plus — opt-in via `PRAXIS_PERSONAL_REPO_OWNERS` — personal-repo references (`<owner>/<repo>(#N)?`) toward non-personal targets on gh bodies and Write/Edit content; semantic leaks out of scope — issues #563, #658 |
| [path-probe-gate](../../hooks/advisory-nudge/path-probe-gate/spec.md) | PreToolUse | Advisory nudge (opt-in strict: deny) when Write/Edit/NotebookEdit targets a nested worktree path whose parent has not been enumerated this session |
| [exclusion-probe-gate](../../hooks/advisory-nudge/exclusion-probe-gate/spec.md) | PreToolUse | Advisory nudge (opt-in strict: deny) when Write/Edit content embeds a self-authored exclusion directive (`do NOT add`, `deliberately excluded`, `의도적 제외`) alongside an uncited verification claim (`verified via`, `확인함`) with no `Probe:` citation nearby — structural backstop for the Author-exempt verification trap; normative-doc/test paths and fenced/quoted regions excluded (issue #807) |
| [version-bump-evidence-check](../../hooks/advisory-nudge/version-bump-evidence-check/spec.md) | PreToolUse | Advisory nudge (opt-in strict) when `gh issue/pr` body describes an external version bump with no changelog URL, Fetched: line, or cross-reference matrix |
| [count-assertion-verify](../../hooks/advisory-nudge/count-assertion-verify/spec.md) | PreToolUse | Advisory nudge when `grep -c` with alternation (`\|` BRE or `\|` ERE/PCRE) runs without per-arm verification; prevents citing inflated alternation counts — issue #277 |
| [bulk-write-memory-checkpoint](../../hooks/advisory-nudge/bulk-write-memory-checkpoint/spec.md) | PreToolUse | Advisory nudge when bulk-writing to SOT-flagged paths (vault/, wiki/, .claude/, skills/, AGENTS.md/CLAUDE.md companions) — reminds to checkpoint memory before the write loop to prevent "Loaded ≠ Retrieved" failures — issue #443 |
| [pre-output-falsification-gate](../../hooks/advisory-nudge/pre-output-falsification-gate/spec.md) | PreToolUse | Advisory on `AskUserQuestion` when an evaluative/`(Recommended)` option is surfaced under recent negative evidence without a disconfirming-probe phrase in the question body (Lane A), and on `Bash` when a read-only status command (status/get/list) repeats ≥3× in a session (Lane B / B-i) — issue #487 |
| [merge-menu-review-options-advisory](../../hooks/advisory-nudge/merge-menu-review-options-advisory/spec.md) | PreToolUse | Advisory (opt-in strict) on `AskUserQuestion` when a merge-decision menu (option label names a merge/squash action) offers no review/debate option (codex-review-wrap / code-reviewer / critic) — issue #560 |
| [menu-mutation-tier-advisory](../../hooks/advisory-nudge/menu-mutation-tier-advisory/spec.md) | PreToolUse | Advisory (opt-in strict) on `AskUserQuestion` when a question keeps ≥2 candidates after abandonment options are dropped, ≥1 mutates shared state, and none names a non-mutating alternative (preview / dev / sandbox / dry-run / review / `보고만`) — an abandonment option (`다음 정기 실행에 맡김`) is neither a candidate nor a tier; a `Safe-tier-unavailable:` line in the question body suppresses it — issue #963 |

---

## postuse-correction

After-tool-execution hooks. Fire **after** a tool completes; emit corrective
context, patch false positives, or record tracking state for paired gates.

| Hook | Trigger | Purpose |
| ------ | --------- | --------- |
| [builtin-task-postuse](../../hooks/postuse-correction/builtin-task-postuse/spec.md) | PostToolUse | Correct upstream "agent spawn" false positives on `TaskCreate` / `TaskUpdate` / etc. |
| [pre-edit-md-escape-advisory](../../hooks/postuse-correction/pre-edit-md-escape-advisory/spec.md) | PreToolUse(Edit) (`pre-edit-md-escape-advisory-pre`) + PostToolUse(Read) (`pre-edit-md-escape-advisory-post`) | Advisory nudge when Edit on a `.md` file carries escape-sensitive tokens without a recorded Read in the session |
| [second-failure-advisory](../../hooks/postuse-correction/second-failure-advisory/spec.md) | PostToolUse + PostToolUseFailure (claude only, issue #1337) | Advisory on a repeated `(tool_name, signature)` failure pattern's second occurrence in the same session to reduce unbounded re-try loops; the failure event is what lets a failed Bash command count at all, and one call reaching both events is counted once by `tool_use_id` |
| [bypass-telemetry](../../hooks/postuse-correction/bypass-telemetry/spec.md) | PostToolUse(Bash) | Observe-only: log bypass-env usage (`CLAUDE_HOOK_BYPASS_*` / `PRAXIS_*BYPASS*`) to daily JSONL — never blocks (issue #441 Phase 1) |
| [askuserquestion-loop-signal](../../hooks/postuse-correction/askuserquestion-loop-signal/spec.md) | PostToolUse(AskUserQuestion) | Observe-only: append one fire-ledger record per `AskUserQuestion` call — coarse per-session call-count proxy for the "re-clarification loop" outcome-proxy signal, never blocks (issue #740) |

---

## completion-verify

Stop hooks that gate **completion claims** before the assistant response is
finalized. Run sequentially: `completion-verify` → `retrospect-mix-check` →
`completion-signal-gate` → `readonly-verify-deferral-gate` →
`merge-state-claim-gate` → `runtime-state-claim-gate` →
`negative-existence-verdict-gate` → `artifact-verdict-evidence-gate` →
`pr-report-destination-gate` → `pr-claim-mutation-gate` →
`pr-anchor-existence-gate` → `proposal-premise-gate` → `strike-counter stop`.
Also includes session-lifecycle enforcement.

Signal convention (issue #647 H3): every hook in this role emits stdout JSON —
advisory tier `{"systemMessage": ...}` (shown to the user, does not block),
block tier `{"decision": "block", "reason": ...}` (blocks the stop; reason is
fed to the model). Exit code is 0 in both tiers; stderr is never the signal
channel.

| Hook | Trigger | Purpose |
| ------ | --------- | --------- |
| [completion-verify](../../hooks/completion-verify/completion-verify/spec.md) | Stop | Block "done / 완료" claims without same-turn Bash verification evidence |
| [retrospect-mix-check](../../hooks/completion-verify/retrospect-mix-check/spec.md) | Stop | Block retrospect Stage 3 outputs that default findings to memory-only |
| [completion-signal-gate](../../hooks/completion-verify/completion-signal-gate/spec.md) | Stop | Advisory nudge when completion-signal phrase appears without evidence-block; also flags cross-plugin slash commands (Event 2) |
| [readonly-verify-deferral-gate](../../hooks/completion-verify/readonly-verify-deferral-gate/spec.md) | Stop | Advisory when the last turn offers to run a read-only verification (SELECT/kubectl get/git status/--dry-run) instead of running it; mutation carve-out + read-already-run suppressor |
| [merge-state-claim-gate](../../hooks/completion-verify/merge-state-claim-gate/spec.md) | Stop | Advisory when the final message asserts a merge/PR/issue/worktree state change without a fresh `gh`/GitHub-MCP state query — applied-on-branch claims additionally require reachability evidence (#656) |
| [runtime-state-claim-gate](../../hooks/completion-verify/runtime-state-claim-gate/spec.md) | Stop | Advisory when the final message asserts a runtime/execution state ("X is running in Y" / "로컬은 건드리지 않습니다") with no probe tool_use in the current turn — launch success does not reveal where something runs (#809) |
| [artifact-verdict-evidence-gate](../../hooks/completion-verify/artifact-verdict-evidence-gate/spec.md) | Stop | Advise when the final message surfaces a positive artifact verdict (삭제 후보/중복/통합 대상/superseded) as a candidate list without an adjacent `Verdict-evidence:` line (#862) |
| [negative-existence-verdict-gate](../../hooks/completion-verify/negative-existence-verdict-gate/spec.md) | Stop | Block when the final message surfaces a negative-existence verdict (없습니다/does not exist) under a registered decision framing (게이트 결과/게이트 판정/AC #) without an `Enumerated:` line in the same paragraph (#804) |
| [pr-report-destination-gate](../../hooks/completion-verify/pr-report-destination-gate/spec.md) | Stop | Advisory when a session wrote a review/verification local `.md` (/tmp/.omc/plans/report-named) for a PR it worked on (`gh pr view/create`/PR URL) but never posted it there (`gh pr comment/review`); per-PR correlation, GET `gh api` and failed posts excluded (#832) |
| [pr-claim-mutation-gate](../../hooks/completion-verify/pr-claim-mutation-gate/spec.md) | Stop | Block when the final message claims a PR/review comment was processed (처리했/반영했/resolved) with no *successful* PR-surface mutation in the current turn — read-only `gh api` listings, `--dry-run` rehearsals, echoed commands and failed calls all fail to clear it; advisory-demote via `PRAXIS_PR_CLAIM_ADVISORY` (#868) |
| [pr-anchor-existence-gate](../../hooks/completion-verify/pr-anchor-existence-gate/spec.md) | Stop | Advisory on the 1st Stop, block on the 2nd+ when a successful non-draft `gh pr create` this session received no verification-anchor post (`gh pr comment` / write `gh api .../{issues,pulls}/<N>/comments`) — existence only, not the anchor's shape (that's `anchor-comment-gate`); bypass `PRAXIS_PR_ANCHOR_BYPASS`, pin-advisory `PRAXIS_PR_ANCHOR_ADVISORY` (#1113) |
| [proposal-premise-gate](../../hooks/completion-verify/proposal-premise-gate/spec.md) | Stop | Advisory when a prose proposal block rests on code-checkable premises that were never probed in the current turn — prose proposals have no PreToolUse surface, so the Stop lane is the only firing point (#846) |
| [strike-counter](../../hooks/completion-verify/strike-counter/spec.md) | SessionStart + UserPromptSubmit + Stop | Session-scoped three-strike discipline — hard-blocks at strike 3, requires reflection before reset |
