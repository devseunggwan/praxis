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
Every entry was verified against the hook source files listed.

### `git` — repository state queries

| Hook | Command | Purpose |
|------|---------|---------|
| `pre-gh-pr-create-dedup-gate.py` | `git remote get-url origin` | Resolve the repo owner/name for the dedup search |
| `cross-repo-worktree-preflight.py` | `git -C <cwd> worktree list --porcelain` | Enumerate registered worktrees to detect cross-repo remove |
| `pre-edit-protected-branch-guard.py` | `git rev-parse --show-toplevel` | Locate the git repo root |
| `pre-edit-protected-branch-guard.py` | `git rev-parse --abbrev-ref HEAD` | Read the current branch name |
| `pre-edit-protected-branch-guard.py` | `git status --porcelain` | Check for a dirty working tree |
| `pre-edit-protected-branch-guard.py` | `git log --oneline -3` | Detect recent PR-suffix commits on a clean tree |

### `gh` — GitHub CLI

| Hook | Command | Purpose |
|------|---------|---------|
| `pre-gh-pr-create-dedup-gate.py` | `gh pr list --repo <r> --state all --search <kw> --json ...` | Search existing PRs before creating a new one |

All `git` and `gh` invocations are **read-only**. No hook writes to remote
state. Hooks fail-open (exit 0) when the binary is missing or times out.

## Out of Scope

Praxis invokes third-party CLIs (`gh`, `cmux`, `codex`, `kubectl`, etc.)
using the user's own credentials and environment. Vulnerabilities in those
tools are upstream concerns — report them to their respective maintainers.
Praxis is not responsible for the security of tools it delegates to.
