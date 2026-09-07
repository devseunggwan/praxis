# Praxis hook environment-variable registry

A single place to answer **"is this gate active, and how do I tune or disable
it?"** Praxis hooks read a growing set of `PRAXIS_*` (and a few legacy
`CLAUDE_HOOK_BYPASS_*`) environment variables. They fall into four kinds:

- **Opt-out** — stop a gate from blocking: either disable it entirely (skip before
  any logic) or demote it to advisory (hook still runs and emits a message, but
  returns exit 0 instead of blocking).
- **Strict** — escalate an advisory hook to a hard block (`return 2`).
- **Config** — tune behaviour (regexes, allowlists, modes).
- **Path / test** — relocate state/cache/log files (also used for test isolation).

Defaults: every gate is **on** unless an opt-out var is set; advisory hooks are
**advisory** unless their `*_STRICT` var is set. All hooks fail-open on infra
errors regardless. The `bypass-telemetry` hook records when any opt-out var is
present on a tool call — see [`bypass-telemetry.md`](bypass-telemetry.md).

For the *threat-model boundary* of the token-based guards (what
`eval`/`bash -c` can hide from them), see the **Guard parser boundary** section
in [`../SECURITY.md`](../SECURITY.md).

## Opt-out (disable or demote a gate)

| Variable | Hook | Effect when set |
| ---------- | ------ | ----------------- |
| `PRAXIS_HOOK_BYPASS_PROTECTED_PATHS` | `protected-paths-guard` | Skip the sensitive-file write guard |
| `PRAXIS_HOOK_BYPASS_SETTINGS_PATH` | `settings-path-advisory` | Skip the Claude Code settings-file write advisory |
| `PRAXIS_HOOK_BYPASS_PARALLEL_MUTATION` | `parallel-gated-mutation-gate` | Skip the repeated-mutation check on a resolved parallel batch. Exact value `1` after stripping — `true` / `yes` / `0` leave the gate active |
| `PRAXIS_HOOK_BYPASS_DESTRUCTIVE_BASH` | `destructive-bash-guard` | Skip the destructive-command guard |
| `PRAXIS_HOOK_BYPASS_SKILL_GATE` | `skill-gate-commands` | Skip the skill-gated-command preflight |
| `PRAXIS_HOOK_BYPASS_WORKTREE_GATE` | `worktree-edit-gate` | Skip the worktree-edit preflight |
| `PRAXIS_HOOK_BYPASS_DECISION_CONSISTENCY_GATE` | `write-decision-consistency-gate` | Skip the Decisions-block consistency preflight |
| `PRAXIS_HOOK_BYPASS_WORKTREE_PRUNE_SNAPSHOT` | `worktree-prune-snapshot-gate` | Skip the snapshot-before-prune gate |
| `PRAXIS_HOOK_BYPASS_MERGE_WORKTREE_GATE` | `gh-merge-worktree-precondition` | Skip the `gh pr merge --delete-branch` worktree-conflict precondition check |
| `PRAXIS_HOOK_BYPASS_ANCHOR_GATE` | `anchor-comment-gate` | Skip both the pre-post structure check and the published-anchor re-check |
| `PRAXIS_HOOK_BYPASS_HUB_ENFORCE` | `block-child-repo-issue-create` | Skip the hub-mediated child-repo issue guard |
| `PRAXIS_HOOK_BYPASS_POSTCOMPACT_CONTEXT` | `postcompact-context` | Skip the post-compaction context advisory |
| `PRAXIS_HOOK_BYPASS_POLL_LOOP_GUARD` | `foreground-poll-loop-guard` | Skip the foreground poll-loop ceiling guard |
| `PRAXIS_BULK_WRITE_BYPASS` | `bulk-write-memory-checkpoint` | Skip the bulk-write checkpoint advisory |
| `PRAXIS_FALSIFY_GATE_BYPASS` | `pre-output-falsification-gate` | Skip both falsification lanes |
| `PRAXIS_MERGE_CLAIM_BYPASS` | `merge-state-claim-gate` | Skip the merge/PR/issue-state claim gate |
| `PRAXIS_RUNTIME_CLAIM_BYPASS` | `runtime-state-claim-gate` | Skip the runtime/execution-state claim gate |
| `PRAXIS_HOOK_BYPASS_NEGATIVE_EXISTENCE_GATE` | `negative-existence-verdict-gate` | Skip the negative-existence verdict `Enumerated:`-line gate |
| `PRAXIS_HOOK_BYPASS_ARTIFACT_VERDICT_GATE` | `artifact-verdict-evidence-gate` | Skip the artifact-verdict `Verdict-evidence:`-line gate |
| `PRAXIS_PR_CLAIM_BYPASS` | `pr-claim-mutation-gate` | Skip the PR-claim mutation gate entirely |
| `PRAXIS_ANCHOR_GATE_ADVISORY` | `anchor-comment-gate` | Demote the *blocking* published-anchor findings from exit 2 to exit 0. `advisory` / `unknown` findings already exit 0 via `additionalContext` and are unaffected, as is the PreToolUse structure block. Exact value `1` only, mirroring the retired `PRAXIS_ANCHOR_GATE_STRICT` it replaces |
| `PRAXIS_PR_CLAIM_ADVISORY` | `pr-claim-mutation-gate` | Demote the PR-claim mutation gate from block to advisory (systemMessage, non-blocking); mirrors `PRAXIS_NEGATIVE_EXISTENCE_ADVISORY` |
| `PRAXIS_PR_ANCHOR_BYPASS` | `pr-anchor-existence-gate` | Skip the PR-anchor existence gate entirely |
| `PRAXIS_PR_ANCHOR_ADVISORY` | `pr-anchor-existence-gate` | Pin the PR-anchor existence gate to advisory forever — no escalation to block on repeat fires this session |
| `PRAXIS_PROPOSAL_PREMISE_BYPASS` | `proposal-premise-gate` | Skip the prose-proposal premise advisory |
| `PRAXIS_HOOK_BYPASS_PR_REPORT_DESTINATION_GATE` | `pr-report-destination-gate` | Skip the PR-report-destination advisory (local report not posted to the PR) |
| `PRAXIS_NEGATIVE_EXISTENCE_ADVISORY` | `negative-existence-verdict-gate` | Demote the negative-existence verdict gate from block to advisory (systemMessage, non-blocking). Set to any non-falsey value (`1` / `true` / `yes`); `0` / `false` / empty keep block mode (mirrors `PRAXIS_ASK_END_ADVISORY`) |
| `PRAXIS_PUSH_VERIFY_BYPASS` | `push-remote-ref-verify` | Skip the post-push remote-ref verification |
| `PRAXIS_PR_THREAD_ADVISORY_BYPASS` | `pr-thread-resolve-advisory` | Skip the post-push unresolved-review-thread advisory |
| `PRAXIS_PATH_PROBE_SKIP` | `path-probe-gate` | Skip the deep-path write gate |
| `PRAXIS_UNENFORCED_STEP_SKIP` | `unenforced-step-advisory` | Silence the unenforced-mandatory-step advisory |
| `PRAXIS_EXCLUSION_PROBE_SKIP` | `exclusion-probe-gate` | Skip the unprobed-exclusion-directive content gate |
| `PRAXIS_MD_ESCAPE_SKIP` | `pre-edit-md-escape-advisory` | Skip the markdown-escape advisory |
| `PRAXIS_PBGUARD_SKIP` | `pre-edit-protected-branch-guard` | Skip the protected-branch edit guard |
| `PRAXIS_MOMENTUM_BYPASS` | `momentum-rule-retrieval-gate` | Skip the high-momentum rule nudge |
| `PRAXIS_MOMENTUM_MERGE_ADVISORY` | `momentum-rule-retrieval-gate` | Demote the merge-briefing escalation to advisory (stderr reminder still fires; no `deny` block on an incomplete pre-merge briefing) |
| `PRAXIS_VERSION_BUMP_BYPASS` | `version-bump-evidence-check` | Skip the version-bump evidence advisory |
| `PRAXIS_SKIP_STAGED_FILE_ENUM` | `pre-commit-staged-file-enumeration` | Skip the pre-commit staged-file enumeration advisory |
| `PRAXIS_SKIP_COMMIT_DECOMPOSITION_ADVISORY` | `commit-decomposition-advisory` | Skip the commit-decomposition advisory |
| `PRAXIS_SKIP_MODEL_ROUTING` | `model-routing-advisory` | Skip the delegation `--model` tier-mismatch advisory |
| `PRAXIS_GH_JSON_BYPASS` | `gh-json-validator` | Skip the `gh --json` field validation (env var form). Note: the inline-comment form `# PRAXIS_GH_JSON_BYPASS=skip` also bypasses the hook but is **not** captured by bypass-telemetry |
| `PRAXIS_ASK_END_ADVISORY` | `block-ask-end-option` | Demote the end-option guard to advisory mode (exit 0 + stderr) |
| `PRAXIS_SKIP_COMMIT_FLAG_CHECK` | `verify-commit-flag-override` | Skip the commit-flag override check |
| `PRAXIS_BYPASS_TELEMETRY_DISABLE` | `bypass-telemetry` | Disable bypass-event logging |
| `CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE` | `block-commit-without-codex-review` | Skip the pre-commit codex-review gate (legacy name) |
| `CLAUDE_HOOK_BYPASS_DUP_GATE` | `block-gh-issue-create-without-dup-search` | Skip the issue-dedup-search gate (legacy name) |

## Strict (escalate advisory → block)

| Variable | Hook | Note |
| ---------- | ------ | ------ |
| `PRAXIS_PROTECTED_PATHS_STRICT` | `protected-paths-guard` | |
| `PRAXIS_SETTINGS_PATH_STRICT` | `settings-path-advisory` | Exact value `1` only |
| `PRAXIS_DESTRUCTIVE_BASH_STRICT` | `destructive-bash-guard` | |
| `PRAXIS_PERSONAL_LEAK_STRICT` | `block-personal-asset-leak` | Exact value `1` only, unstripped — surrounding whitespace keeps it advisory |
| `PRAXIS_PATH_PROBE_STRICT` | `path-probe-gate` | |
| `PRAXIS_ARTIFACT_VERDICT_STRICT` | `artifact-verdict-evidence-gate` | Promote the artifact-verdict gate from advisory to block. Any non-falsey value (`1` / `true` / `yes`); `0` / `false` / empty keep advisory |
| `PRAXIS_EXCLUSION_PROBE_STRICT` | `exclusion-probe-gate` | |
| `PRAXIS_PHANTOM_PATH_STRICT` | `external-write-path-existence-check` | |
| `PRAXIS_MERGE_CLAIM_STRICT` | `merge-state-claim-gate` | |
| `PRAXIS_RUNTIME_CLAIM_STRICT` | `runtime-state-claim-gate` | |
| `PRAXIS_PUSH_VERIFY_STRICT` | `push-remote-ref-verify` | |
| `PRAXIS_PR_THREAD_ADVISORY_STRICT` | `pr-thread-resolve-advisory` | Exit 2 only when a needs-a-reply thread is open |
| `PRAXIS_MOMENTUM_STRICT` | `momentum-rule-retrieval-gate` | |
| `PRAXIS_MOMENTUM_ACK` | `momentum-rule-retrieval-gate` | Strict-mode unlock token — unblocks one invocation when `PRAXIS_MOMENTUM_STRICT=1`; no effect in advisory mode |
| `PRAXIS_VERSION_BUMP_STRICT` | `version-bump-evidence-check` | |
| `PRAXIS_COMMIT_TITLE_FORMAT_STRICT` | `commit-title-format-check` | |
| `PRAXIS_COMMIT_PAREN_STRICT` | `commit-message-paren-check` | |
| `PRAXIS_BRANCH_NAME_STRICT` | `branch-name-check` | |
| `PRAXIS_CODEX_REVIEW_STRICT` | `block-commit-without-codex-review` | Pins the deny when codex is not on PATH; `0` forces advisory even when detected (#1187) |
| `PRAXIS_PR_EVIDENCE_STRICT` | `block-pr-without-caller-evidence`, `block-pr-without-precommit-evidence` | Shared by both PR-marker gates (#1186); truthy = deny, unset/empty/`0` = advisory |
| `PRAXIS_ASK_END_STRICT` | `block-ask-end-option` | |
| `PRAXIS_BLOCK_MANUFACTURED_MENU_STRICT` | `block-manufactured-action-menu` | |
| `PRAXIS_MENU_MUTATION_TIER_STRICT` | `menu-mutation-tier-advisory` | Exact value `1` only — `true` / `yes` / `no` / `0` stay advisory |
| `PRAXIS_MERGE_MENU_REVIEW_STRICT` | `merge-menu-review-options-advisory` | Exact value `1` after stripping — `true` / `yes` / `0` stay advisory |
| `PRAXIS_PR_STATE_REFETCH_STRICT` | `pr-state-refetch-gate` | |
| `PRAXIS_EXTERNAL_WRITE_STRICT` | `external-write-falsify-check` | |
| `PRAXIS_AUTHOR_EXEMPT_STRICT` | `external-write-falsify-check` | |
| `PRAXIS_CLUSTER_APPROVAL_STRICT` | `external-write-falsify-check` | |
| `PRAXIS_SOURCE_CITATION_STRICT` | `source-citation-probe-gate` | |
| `PRAXIS_COMPOSED_COMMAND_STRICT` | `composed-command-gate` | |
| `PRAXIS_CALLER_PROBE_STRICT` | `caller-probe-gate` | |
| `PRAXIS_UNENFORCED_STEP_STRICT` | `unenforced-step-advisory` | |

## Config (tune behaviour)

| Variable | Hook | Effect |
| ---------- | ------ | -------- |
| `PRAXIS_PROTECTED_BRANCHES` | `pre-edit-protected-branch-guard` | Override the protected-branch list |
| `PRAXIS_PBGUARD_BLOCK_DOCS` | `pre-edit-protected-branch-guard` | Also gate docs edits |
| `PRAXIS_PBGUARD_SKIP_PR_CHECK` | `pre-edit-protected-branch-guard` | Skip the PR-existence check portion |
| `PRAXIS_ISSUE_TRACKER_URL` | `pre-edit-protected-branch-guard` | Issue-tracker URL used in guidance |
| `PRAXIS_BRANCH_NAME_REGEX` | `branch-name-check` | Override the allowed branch-name regex |
| `PRAXIS_BRANCH_NAME_WHITELIST` | `branch-name-check` | Allowlist of exempt branch names |
| `PRAXIS_COMMIT_TITLE_ALLOWED_TYPES` | `commit-title-format-check` | Allowed conventional-commit types |
| `PRAXIS_SKILL_GATED_COMMANDS` | `skill-gate-commands` | Commands that require a skill invocation |
| `PRAXIS_HUB_MEDIATED_ORGS` | `block-child-repo-issue-create` | Orgs whose child-repo issues route through the hub |
| `PRAXIS_WORKTREE_ENFORCED_REPOS` | `worktree-edit-gate` | Repos where the worktree workflow is enforced |
| `PRAXIS_WORKTREE_BASE_BRANCHES` | `worktree-edit-gate` | Base branches treated as "not a worktree" |
| `PRAXIS_WORKTREE_SOURCE_EXTENSIONS` | `worktree-edit-gate` | File extensions the gate applies to |
| `PRAXIS_MD_ESCAPE_MODE` | `pre-edit-md-escape-advisory` | Select advisory vs block mode |
| `PRAXIS_INTENT_PIVOT_MODE` | `session-intent` | Pivot-detection mode |
| `PRAXIS_PIPEFAIL_ADVISORY_CONTEXT` | `pipefail-advisory` | Issue #874 ADVISE-channel experiment arm switch. Also emits the advisory as `hookSpecificOutput.additionalContext`, the one PreToolUse channel that reaches the model; the hook still exits 0, so this is not a strict-mode escalation. Stderr is kept in both arms — `_fire_ledger` classifies `advise` from stderr. Exact value `1` only, mirroring `PRAXIS_ANCHOR_GATE_ADVISORY` |

## Path / test (relocate state, caches, logs)

| Variable | Default | Hook(s) |
| ---------- | --------- | --------- |
| `PRAXIS_HOME` | `~/.praxis` | shared (`_paths.py`) — relocates the whole runtime tree |
| `PRAXIS_STATE_DIR` | `~/.praxis/state` | shared — durable state base (strike-counter, external-write-path-existence-check, postcompact read) |
| `PRAXIS_HOOK_ERROR_LOG` | `~/.praxis/logs/hook-errors.jsonl` | shared (`@fail_open`) |
| `PRAXIS_HOOK_ERROR_STDERR` | unset | shared — also print swallowed-exception note to stderr |
| `PRAXIS_HOOK_ERROR_LOG_MAX_BYTES` | `5242880` | shared (`@fail_open`) — error-log rotation cap in bytes, `0` disables (#1282) |
| `PRAXIS_BYPASS_TELEMETRY_FILE` | `~/.praxis/telemetry/bypass-events-<date>.jsonl` | `bypass-telemetry` |
| `PRAXIS_MEMORY_DIR` | memory store dir | `memory-hint`, `momentum-rule-retrieval-gate` |
| `PRAXIS_GH_LABEL_CACHE_PATH` | `~/.praxis/cache/gh-label-cache.json` | `gh-label-verify` |
| `PRAXIS_GH_LABEL_CACHE_TTL_SEC` | `300` | `gh-label-verify` |
| `PRAXIS_SESSION_INTENT_FILE` | `${TMPDIR}/praxis-session-intent-<sid>.json` | `session-intent` |
| `PRAXIS_WORKTREE_PRUNE_SNAPSHOT_FILE` | `${TMPDIR}/praxis-worktree-prune-snapshot-<sid>.json` | `worktree-prune-snapshot-gate` |
| `PRAXIS_MD_READ_HISTORY_FILE` | `${TMPDIR}/praxis-md-read-history-<sid>.json` | `pre-edit-md-escape-advisory` |
| `PRAXIS_PBGUARD_TEST_*` | unset | `pre-edit-protected-branch-guard` — test-only injection (branch/status/repo-root/ignored/log) |

The volatile `${TMPDIR}/praxis-*` cache paths above are slated to move under
`~/.praxis/cache` in the #527 follow-up; their per-file override vars will
continue to work. See [`runtime-state-layout.md`](runtime-state-layout.md).

## Maintaining this registry

This registry is a **human-readable view**. The canonical source for a hook's
strict env, bypass/opt-out env, and state/path vars is the per-hook `mode` block
in [`hooks/manifest.json`](../hooks/manifest.json). `scripts/check-plugin-manifests.py`
(Rule 17) cross-checks the Strict / Opt-out / Path-test tables here against that
`mode` metadata in both directions, so a value documented here without a matching
manifest entry — or vice versa — fails CI.

When you add a hook env var, add it to the hook's manifest `mode` block AND a row
here in the same PR (the **Config** section is doc-only — those vars tune
behaviour rather than switch mode, so they carry no manifest `mode` field). To
list every var referenced by the hooks at any time:

```bash
grep -rhoE "PRAXIS_[A-Z0-9_]+|CLAUDE_HOOK_BYPASS_[A-Z0-9_]+" hooks/ | sort -u
```
