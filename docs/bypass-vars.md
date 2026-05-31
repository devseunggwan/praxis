# Praxis hook environment-variable registry

A single place to answer **"is this gate active, and how do I tune or disable
it?"** Praxis hooks read a growing set of `PRAXIS_*` (and a few legacy
`CLAUDE_HOOK_BYPASS_*`) environment variables. They fall into four kinds:

- **Opt-out** — disable a gate entirely.
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

## Opt-out (disable a gate)

| Variable | Hook | Effect when set |
|----------|------|-----------------|
| `PRAXIS_HOOK_BYPASS_PROTECTED_PATHS` | `protected-paths-guard` | Skip the sensitive-file write guard |
| `PRAXIS_HOOK_BYPASS_DESTRUCTIVE_BASH` | `destructive-bash-guard` | Skip the destructive-command guard |
| `PRAXIS_HOOK_BYPASS_SKILL_GATE` | `skill-gate-commands` | Skip the skill-gated-command preflight |
| `PRAXIS_HOOK_BYPASS_WORKTREE_GATE` | `worktree-edit-gate` | Skip the worktree-edit preflight |
| `PRAXIS_HOOK_BYPASS_HUB_ENFORCE` | `block-child-repo-issue-create` | Skip the hub-mediated child-repo issue guard |
| `PRAXIS_HOOK_BYPASS_POSTCOMPACT_CONTEXT` | `postcompact-context` | Skip the post-compaction context advisory |
| `PRAXIS_BULK_WRITE_BYPASS` | `bulk-write-memory-checkpoint` | Skip the bulk-write checkpoint advisory |
| `PRAXIS_FALSIFY_GATE_BYPASS` | `pre-output-falsification-gate` | Skip both falsification lanes |
| `PRAXIS_MERGE_CLAIM_BYPASS` | `merge-state-claim-gate` | Skip the merge/PR/issue-state claim gate |
| `PRAXIS_PUSH_VERIFY_BYPASS` | `push-remote-ref-verify` | Skip the post-push remote-ref verification |
| `PRAXIS_PATH_PROBE_SKIP` | `path-probe-gate` | Skip the deep-path write gate |
| `PRAXIS_MD_ESCAPE_SKIP` | `pre-edit-md-escape-advisory` | Skip the markdown-escape advisory |
| `PRAXIS_PBGUARD_SKIP` | `pre-edit-protected-branch-guard` | Skip the protected-branch edit guard |
| `PRAXIS_MOMENTUM_BYPASS` | `momentum-rule-retrieval-gate` | Skip the high-momentum rule nudge |
| `PRAXIS_MOMENTUM_ACK` | `momentum-rule-retrieval-gate` | Acknowledge the nudge for the current action (one-shot) |
| `PRAXIS_VERSION_BUMP_BYPASS` | `version-bump-evidence-check` | Skip the version-bump evidence advisory |
| `PRAXIS_GH_JSON_BYPASS` | `gh-json-validator` | Skip the `gh --json` field validation |
| `PRAXIS_BYPASS_TELEMETRY_DISABLE` | `bypass-telemetry` | Disable bypass-event logging |
| `CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE` | `block-commit-without-codex-review` | Skip the pre-commit codex-review gate (legacy name) |
| `CLAUDE_HOOK_BYPASS_DUP_GATE` | `block-gh-issue-create-without-dup-search` | Skip the issue-dedup-search gate (legacy name) |
| `CLAUDE_HOOK_BYPASS_SCIOMC_GATE` | `block-sciomc-finding-commit` | Skip the sciomc-finding commit gate (legacy name) |

## Strict (escalate advisory → block)

| Variable | Hook |
|----------|------|
| `PRAXIS_PROTECTED_PATHS_STRICT` | `protected-paths-guard` |
| `PRAXIS_DESTRUCTIVE_BASH_STRICT` | `destructive-bash-guard` |
| `PRAXIS_PATH_PROBE_STRICT` | `path-probe-gate` |
| `PRAXIS_PHANTOM_PATH_STRICT` | `external-write-path-existence-check` |
| `PRAXIS_MERGE_CLAIM_STRICT` | `merge-state-claim-gate` |
| `PRAXIS_PUSH_VERIFY_STRICT` | `push-remote-ref-verify` |
| `PRAXIS_MOMENTUM_STRICT` | `momentum-rule-retrieval-gate` |
| `PRAXIS_VERSION_BUMP_STRICT` | `version-bump-evidence-check` |
| `PRAXIS_COMMIT_TITLE_FORMAT_STRICT` | `commit-title-format-check` |
| `PRAXIS_BRANCH_NAME_STRICT` | `branch-name-check` |
| `PRAXIS_ASK_END_STRICT` | `block-ask-end-option` |
| `PRAXIS_BLOCK_MANUFACTURED_MENU_STRICT` | `block-manufactured-action-menu` |
| `PRAXIS_EXTERNAL_WRITE_STRICT` | `external-write-falsify-check` |
| `PRAXIS_AUTHOR_EXEMPT_STRICT` | `external-write-falsify-check` |
| `PRAXIS_CLUSTER_APPROVAL_STRICT` | `external-write-falsify-check` |

## Config (tune behaviour)

| Variable | Hook | Effect |
|----------|------|--------|
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
| `PRAXIS_ASK_END_ADVISORY` | `block-ask-end-option` | Force advisory mode (opposite of strict) |
| `PRAXIS_MD_ESCAPE_MODE` | `pre-edit-md-escape-advisory` | Select advisory vs block mode |
| `PRAXIS_INTENT_PIVOT_MODE` | `session-intent` | Pivot-detection mode |
| `PRAXIS_SKIP_COMMIT_FLAG_CHECK` | `verify-commit-flag-override` | Skip the commit-flag override check |

## Path / test (relocate state, caches, logs)

| Variable | Default | Hook(s) |
|----------|---------|---------|
| `PRAXIS_HOME` | `~/.praxis` | shared (`_paths.py`) — relocates the whole runtime tree |
| `PRAXIS_STATE_DIR` | `~/.praxis/state` | shared — durable state base (strike-counter, phantom-path, postcompact read) |
| `PRAXIS_HOOK_ERROR_LOG` | `~/.praxis/logs/hook-errors.jsonl` | shared (`@fail_open`) |
| `PRAXIS_HOOK_ERROR_STDERR` | unset | shared — also print swallowed-exception note to stderr |
| `PRAXIS_BYPASS_TELEMETRY_FILE` | `~/.praxis/telemetry/bypass-events-<date>.jsonl` | `bypass-telemetry` |
| `PRAXIS_MEMORY_DIR` | memory store dir | `memory-hint`, `momentum-rule-retrieval-gate` |
| `PRAXIS_GH_LABEL_CACHE_PATH` | XDG cache | `gh-label-verify` |
| `PRAXIS_GH_LABEL_CACHE_TTL_SEC` | `300` | `gh-label-verify` |
| `PRAXIS_POSTCOMPACT_CONTEXT_FILE` | `${TMPDIR}/praxis-postcompact-context-<sid>.json` | `postcompact-context` |
| `PRAXIS_POSTCOMPACT_TAIL_LINES` | `100` | `postcompact-context` |
| `PRAXIS_SESSION_INTENT_FILE` | `${TMPDIR}/praxis-session-intent-<sid>.json` | `session-intent` |
| `PRAXIS_MD_READ_HISTORY_FILE` | `${TMPDIR}/praxis-md-read-history-<sid>.json` | `pre-edit-md-escape-advisory` |
| `PRAXIS_PBGUARD_TEST_*` | unset | `pre-edit-protected-branch-guard` — test-only injection (branch/status/repo-root/ignored/log) |

The volatile `${TMPDIR}/praxis-*` cache paths above are slated to move under
`~/.praxis/cache` in the #527 follow-up; their per-file override vars will
continue to work. See [`runtime-state-layout.md`](runtime-state-layout.md).

## Maintaining this registry

When you add a hook env var, add a row here in the same PR. To list every var
referenced by the hooks at any time:

```bash
grep -rhoE "PRAXIS_[A-Z0-9_]+|CLAUDE_HOOK_BYPASS_[A-Z0-9_]+" hooks/ | sort -u
```
