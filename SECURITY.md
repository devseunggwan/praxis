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
|----------|----------------|-------------------|
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
|------|---------|---------|
| `hooks/preflight-gate/pre-gh-pr-create-dedup-gate/impl.py` | `git remote get-url origin` | Resolve the repo owner/name for the dedup search |
| `hooks/preflight-gate/pre-edit-protected-branch-guard/impl.py` | `git rev-parse --show-toplevel` | Locate the git repo root |
| `hooks/preflight-gate/pre-edit-protected-branch-guard/impl.py` | `git rev-parse --abbrev-ref HEAD` | Read the current branch name |
| `hooks/preflight-gate/pre-edit-protected-branch-guard/impl.py` | `git status --porcelain` | Check for a dirty working tree |
| `hooks/preflight-gate/pre-edit-protected-branch-guard/impl.py` | `git log --oneline -3` | Detect recent PR-suffix commits on a clean tree |

### `gh` — GitHub CLI

| Hook | Command | Purpose |
|------|---------|---------|
| `hooks/preflight-gate/pre-gh-pr-create-dedup-gate/impl.py` | `gh pr list --repo <r> --state all --search <kw> --json ...` | Search existing PRs before creating a new one |
| `hooks/preflight-gate/pr-state-refetch-gate/impl.py` | `gh pr view <N> --json state,mergeStateStatus` | Re-fetch live PR state before a PR-state-contingent AskUserQuestion |

All `git` and `gh` invocations are **read-only**. No hook writes to remote
state. Hooks fail-open (exit 0) when the binary is missing or times out.

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
