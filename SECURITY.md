# Security Policy

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Report via GitHub Security Advisory:
**[devseunggwan/praxis/security/advisories/new](https://github.com/devseunggwan/praxis/security/advisories/new)**

Include:
- Description of the vulnerability and its impact
- Steps to reproduce
- Any suggested fix (optional)

### Disclosure Timeline

| Severity | Target response | Public disclosure |
| ---------- | ---------------- | ------------------- |
| Critical | 48 hours | After patch is released |
| High | 7 days | 30 days after report |
| Medium / Low | 30 days | 90 days after report |

Expedited disclosure is available for actively exploited vulnerabilities.

## Supported Versions

Only the latest version on the `main` branch receives security patches.
Prior versions are best-effort only — upgrade to `main` to receive fixes.

## Hook External-Command Allowlist

Praxis hooks invoke the following external commands during normal operation.
Every entry was verified against the hook source files listed. This table is a
**human-readable view** of the per-hook `external_commands` field in each hook's
`mode` block in [`hooks/manifest.json`](hooks/manifest.json);
`scripts/check-plugin-manifests.py` (Rule 17) cross-checks the two so neither can
drift.

### `git` — repository state queries

| Hook | Command | Purpose |
| ------ | --------- | --------- |
| `hooks/preflight-gate/pre-gh-pr-create-dedup-gate/impl.py` | `git remote get-url origin` | Resolve the repo owner/name for the dedup search |
| `hooks/preflight-gate/pre-edit-protected-branch-guard/impl.py` | `git rev-parse --show-toplevel` | Locate the git repo root |
| `hooks/preflight-gate/pre-edit-protected-branch-guard/impl.py` | `git rev-parse --abbrev-ref HEAD` | Read the current branch name |
| `hooks/preflight-gate/pre-edit-protected-branch-guard/impl.py` | `git status --porcelain` | Check for a dirty working tree |
| `hooks/preflight-gate/pre-edit-protected-branch-guard/impl.py` | `git log --oneline -3` | Detect recent PR-suffix commits on a clean tree |
| `hooks/preflight-gate/gh-merge-worktree-precondition/impl.py` | `git worktree list --porcelain` | Check whether a PR's head branch is checked out in another worktree before `gh pr merge --delete-branch` |
| `hooks/preflight-gate/anchor-comment-gate/impl.py` | `git merge-base origin/<base> HEAD` | Find the PR's fork point so the coverage advisory measures against its own base |
| `hooks/preflight-gate/anchor-comment-gate/impl.py` | `git diff --name-only <merge-base> HEAD` | List changed files to flag ones no verification-anchor table row mentions (advisory only) |
| `hooks/advisory-nudge/codex-review-route/impl.py` | `git worktree list --porcelain` | Count active non-bare worktrees before a bare `/codex:review` so the multi-worktree advisory can redirect to `codex-review-wrap` |
| `hooks/advisory-nudge/codex-review-route/impl.py` | `git rev-parse --abbrev-ref HEAD` | Read the current branch name to look up its PR state |

### `gh` — GitHub CLI

| Hook                                                          | Command                                                                 | Purpose                                                                                                    |
|---------------------------------------------------------------|-------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------|
| `hooks/preflight-gate/pre-gh-pr-create-dedup-gate/impl.py`    | `gh pr list --repo <r> --state all --search <kw> --json ...`            | Search existing PRs before creating a new one                                                              |
| `hooks/preflight-gate/pr-state-refetch-gate/impl.py`          | `gh pr view <N> --json state,mergeStateStatus`                          | Re-fetch live PR state before a PR-state-contingent AskUserQuestion                                        |
| `hooks/preflight-gate/gh-merge-worktree-precondition/impl.py` | `gh pr view <identifier> --json headRefName -q .headRefName`            | Resolve a PR's live head branch before checking it against `git worktree list`                             |
| `hooks/preflight-gate/anchor-comment-gate/impl.py`            | `gh api /repos/{owner}/{repo}/issues/comments/{id} --jq .body`          | Read back the verification anchor that was just published, from the comment URL the command itself printed |
| `hooks/preflight-gate/anchor-comment-gate/impl.py`            | `gh api /repos/{owner}/{repo}/issues/comments/{id} --jq .issue_url`     | Resolve which PR a comment belongs to when the post printed no URL to follow                               |
| `hooks/preflight-gate/anchor-comment-gate/impl.py`            | `gh pr view <number> --repo <r> --json headRefOid,baseRefName --jq ...` | Resolve the PR's live head SHA (stale-anchor check) and its base branch (coverage advisory) in one call    |
| `hooks/advisory-nudge/codex-review-route/impl.py`             | `gh pr view <branch> --json state --jq .state`                          | Warn when the current branch's PR is CLOSED or MERGED before a codex review runs against it                |

### `zsh` — glob-expansion probes

| Hook | Command | Purpose |
| ------ | --------- | --------- |
| `hooks/preflight-gate/cross-boundary-preflight/impl.py` | `git -C <cwd> config --local --get-regexp <remote url / gh-resolved>` | Resolve the repository a repo-less `gh` write would target — reads the checkout's own remotes and any `gh repo set-default` marker, so the approval prompt names the repo that receives the write rather than merely `origin`. Local config read only; no network and no `gh` invocation |
| `hooks/preflight-gate/block-unmatched-glob/impl.py` | `zsh -lc setopt` | Confirm the executing shell actually aborts on an unmatched glob before the gate can block |
| `hooks/preflight-gate/block-unmatched-glob/impl.py` | `zsh -f -c 'setopt nomatch; : <pattern>'` | Ask zsh whether a candidate pattern expands; `:` is a no-op builtin, so expansion is the only effect |

The probe replays **one word at a time**, never the user's command. `-f` skips
startup files, and any word containing `(` — zsh glob qualifiers can carry code
(`*(e:'cmd':)`) — is excluded from probing entirely, so no command body from the
inspected input is ever executed. Both probes are bounded by a 2s per-call and
3s total budget.

All `git`, `gh`, and `zsh` invocations are **read-only**. No hook writes to
remote state. Hooks fail-open (exit 0) when the binary is missing or times out.

## Guard Parser Boundary

Several praxis preflight guards (`destructive-bash-guard`, the commit/push
gates, `skill-gate-commands`, the `gh`-flag guards, …) inspect the **literal
command tokens** of a Bash invocation via a shared structural tokenizer. This is
a deliberate, bounded threat model — the guards are correctness/discipline
nudges, **not a sandbox**:

- **In scope:** literal commands and their flags, including compound chains
  (`&&`, `;`, `|`), env-var prefixes, common wrappers (`env`, `sudo`, `time`),
  subshell / command-substitution wrappers, and bundled short flags.
- **Out of scope:** a command **hidden inside an interpreter string** is not
  decoded. `eval "rm -rf …"`, `bash -c "…"`, `sh -c "…"`, `python -c "…"`, and
  `find … -exec rm …` pass the token guards because the dangerous token exists
  only *inside* a quoted string the tokenizer treats as one opaque argument.

This is an inherent limit of literal-token parsing, not a bug. The answer is not
a regex arms race (which `feedback_shell_parser_diminishing_returns` records as
unbounded) but explicit documentation of the boundary. **Do not rely on these
guards as a security control against an adversary** — they exist to catch
*accidental* footguns. Anything that must not run should be prevented by the
runtime permission layer, not by a hook.

Every gate can be disabled or escalated via environment variables — see the
[hook environment-variable registry](docs/bypass-vars.md).

## Out of Scope

Praxis invokes third-party CLIs (`gh`, `cmux`, `codex`, `kubectl`, etc.)
using the user's own credentials and environment. Vulnerabilities in those
tools are upstream concerns — report them to their respective maintainers.
Praxis is not responsible for the security of tools it delegates to.
