# PreToolUse Worktree Edit Gate

Supported hosts: all

`hooks/preflight-gate/worktree-edit-gate/impl.py` (run in-process by the
`Edit|Write` dispatch group's `hooks/_dispatch.sh`, #1168) fires on every
PreToolUse event for `Edit` and
`Write` tools. It blocks edits to source files when the target repo's HEAD is
on a configured base branch, enforcing the Issue-Driven Worktree Workflow.

## Ambiguity resolution (issue #437)

The original issue listed a 4th condition: "cwd is not registered in
`git worktree list`". However, the Acceptance Criteria state: "Does NOT block:
edits inside a registered worktree on any branch."

The 4th condition and the Acceptance Criteria contradict: a primary checkout
sitting on a base branch IS present in `git worktree list`, and under the
4th-condition reading, would pass the gate (it is registered).

**Resolution chosen: block based on HEAD branch, not worktree-list registration.**

A feature-branch worktree never has HEAD ∈ base-branches, so it passes the
gate naturally — no worktree-list lookup is needed. The gate's real signal is
"editing a source file while the repo is on a base branch" regardless of whether
that checkout is the primary or a named worktree.

This reading matches the stated intent: "Does NOT block: edits inside a
registered worktree on any branch" is naturally satisfied because any correctly
created feature worktree will have HEAD pointing at the feature branch, not
main/dev/prod.

## Why this exists

The *Issue-Driven Worktree Workflow* rule ([`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries))
requires every code change to live in a dedicated issue + branch + worktree.
Existing hooks (`pre-edit-protected-branch-guard`) guard against dirty-tree
or PR-workflow signal situations. This hook adds a complementary gate:
**opt-in, repo-scoped, extension-filtered** blocking to enforce that no source
file in a configured repo is edited while the repo sits on a base branch.

The key difference from `pre-edit-protected-branch-guard`:
- This hook is **opt-in** (default no-op; requires `PRAXIS_WORKTREE_ENFORCED_REPOS`)
- This hook is **extension-aware** (only blocks source files, not markdown/JSON)
- This hook fires on **branch state alone**, without requiring a dirty tree

The opposite defaults are a deliberate **two-tier design** (issue #1159):
the guard is the default-on generic safety net (protected branches only,
deny corroborated by a workflow signal), and this gate is the opt-in strict
tier for repos that explicitly enroll in the full worktree workflow. Firing
on branch state alone is exactly why this gate must stay opt-in under
the issue-#1159 attested-convention principle — without enrollment it
would deny
ordinary on-branch edits in repos that never adopted the convention. See
the matching "Two-tier defaults" section in
`pre-edit-protected-branch-guard/spec.md`.

## Default behavior (no-op)

When `PRAXIS_WORKTREE_ENFORCED_REPOS` is unset or empty, the hook exits 0
immediately and blocks nothing. This is opt-in by design — the hook must not
interfere with projects that have not been explicitly enrolled.

## Block conditions (ALL must be true)

1. `PRAXIS_WORKTREE_ENFORCED_REPOS` is non-empty AND the target file's git
   repo root matches one of the configured identifiers.
2. The file's extension (or compound extension) is in the source extension set.
3. `git rev-parse --abbrev-ref HEAD` returns a branch in the base-branch set.

## What is blocked / passed

| Scenario | Action |
| ---------- | -------- |
| Source file, enforced repo, HEAD on `main` | exit 2 (block) |
| Source file, enforced repo, HEAD on `dev` | exit 2 (block) |
| Source file, enforced repo, HEAD on `prod` | exit 2 (block) |
| Source file, enforced repo, HEAD on feature branch | pass |
| Non-source file (`.md`, `.json`, `.yaml`), enforced repo, base branch | pass |
| Source file, repo NOT in enforced list | pass |
| `PRAXIS_WORKTREE_ENFORCED_REPOS` empty or unset | pass (no-op) |
| `PRAXIS_HOOK_BYPASS_WORKTREE_GATE` set (any value) | pass |
| File not inside a git repo | pass (fail-open) |
| Detached HEAD | pass (fail-open) |
| `git` not installed / subprocess timeout | pass (fail-open) |
| Malformed stdin JSON | pass (fail-open) |
| NotebookEdit tool | pass (not in scope) |
| Bash / Read tools | pass (not in scope) |

## Env vars

| Variable | Default | Description |
| ---------- | --------- | ------------- |
| `PRAXIS_WORKTREE_ENFORCED_REPOS` | _(empty — no-op)_ | Comma-separated repo identifiers. Match by basename (`praxis`) or `org/repo` (`myorg/praxis`). |
| `PRAXIS_WORKTREE_BASE_BRANCHES` | `main,dev,prod` | Comma-separated branch names that trigger the block. |
| `PRAXIS_WORKTREE_SOURCE_EXTENSIONS` | `py,ts,tsx,go,sql` | Comma-separated extensions without leading dot. Compound extensions like `j2.sql` are supported. |
| `PRAXIS_HOOK_BYPASS_WORKTREE_GATE` | _(unset)_ | Set to any non-empty value to bypass for this session. |

## Repo identifier matching

Identifiers in `PRAXIS_WORKTREE_ENFORCED_REPOS` are matched against the
resolved git repo root path:

- **Basename form** (`praxis`) — matches when `os.path.basename(repo_root) == "praxis"`.
- **org/repo form** (`myorg/praxis`) — matches when the last two path
  components of `repo_root` are `myorg/praxis`.

Case-sensitive. Symlinks in the target file path are resolved before lookup.

## Extension matching

Extensions in `PRAXIS_WORKTREE_SOURCE_EXTENSIONS` are matched against the
target file's basename (no leading dot):

- `file.py` → extension `py`
- `file.j2.sql` → first tries compound `j2.sql`, then falls back to `sql`
- `Makefile` (no dot) → not a source file → pass

## Response (block)

Block is signaled via stderr (using the standard `block_message.py` helper)
and exit code 2. Example output:

```
⚠️ WORKTREE-EDIT-GATE blocked

Why: editing a source file on base branch 'main' in 'praxis' — the Issue-Driven Worktree Workflow requires a dedicated issue + branch + worktree before writing code on base branches
Correct path: create a GitHub issue, then create a feature branch + worktree: `git worktree add <path> -b issue-N-description` and work there
Bypass (if truly needed): PRAXIS_HOOK_BYPASS_WORKTREE_GATE=1 with a one-line reason comment explaining why
Reference: CLAUDE.md → Issue-Driven Worktree Workflow (MANDATORY); docs/hook/INDEX.md → worktree-edit-gate

Blocked file: /path/to/repo/src/module.py
Repo: /path/to/repo
```

## Tests

```bash
bash tests/hooks/preflight-gate/test_worktree_edit_gate.sh
```

Uses a real temp git repo (init, commit, branch) to exercise genuine branch
detection — no mocking of git output. Covers all enumerated surface variants:

- Default no config → pass (no-op)
- Source file, enforced repo, `main` branch → block (Edit)
- Source file, enforced repo, `main` branch → block (Write)
- Source file, enforced repo, `dev` branch → block
- Source file, enforced repo, `prod` branch → block
- Non-source file (`.md`) on base branch → pass
- Non-source file (`.json`) on base branch → pass
- Source file on feature branch → pass
- Repo not in enforced list → pass
- Bypass env set → pass
- File not in any git repo → pass (fail-open)
- Detached HEAD → pass (fail-open)
- Edit tool → block; Write tool → block (both covered)
- Custom extensions via `PRAXIS_WORKTREE_SOURCE_EXTENSIONS` → block only configured ext
- Custom base branches via `PRAXIS_WORKTREE_BASE_BRANCHES` → block custom, pass default
- File path with spaces → block (handled correctly via Python string ops)
- Malformed JSON stdin → pass (fail-open)
- `org/repo` form identifier matching → block
