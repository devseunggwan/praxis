# PreToolUse Pre-Edit Protected-Branch Guard

Supported hosts: all

`hooks/preflight-gate/pre-edit-protected-branch-guard/impl.py` fires on every PreToolUse event
for `Edit`, `Write`, and `NotebookEdit` tools. It has two independent deny
paths on protected branches:

1. **Dirty-tree path** — working tree is dirty and the edit target is not
   yet part of the dirty diff (no associated issue-driven worktree).
2. **PR-workflow path** (issue #231) — working tree is clean but recent
   commits show a `(#NNN)` PR-suffix, signaling the repo uses a PR workflow.
   Direct edits on protected branches violate that workflow.

### Why this exists

The *Issue-Driven Worktree Workflow* rule ([`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries))
requires every code change to live in a dedicated issue + branch + worktree.
Existing hooks (e.g. `block-pr-without-caller-evidence`) catch violations at
PR creation time — but the window between "first file edit" and "PR creation"
was unguarded. Work could start directly on `main` / `dev` / `prod`, only
getting caught after the diff was already committed (requiring a stash →
issue → worktree → stash-pop → commit → PR recovery cycle).

This hook closes the timing gap by firing **at the moment a file is first
opened for editing**, before any character is written.

### Relationship to existing hooks (dual-gate)

| Hook                               | Gate timing        | Signal                                   |
| ---------------------------------- | ------------------ | ---------------------------------------- |
| `pre-edit-protected-branch-guard`  | Before first write | Dirty branch + new target file           |
| `block-pr-without-caller-evidence` | At `gh pr create`  | Missing caller-chain evidence in PR body |

The two gates are complementary, not redundant. This hook prevents the
mistake from starting; the PR gate is a backstop for cases this hook doesn't
cover (e.g. starting work on an unprotected branch then renaming it to main).

### Two-tier defaults with `worktree-edit-gate` (deliberate — issue #1159)

`worktree-edit-gate` enforces the same Issue-Driven Worktree rule but ships
**opt-in** (inert without `PRAXIS_WORKTREE_ENFORCED_REPOS`), while this hook
ships **default-on**. That asymmetry is a recorded design decision, not
drift. The #1159 attested-convention principle demotes convention gates to
advisory on shipped defaults; this hook is the principle's one deliberate
exception because its trigger is not a repo-local convention: editing
directly on `main`/`master`/`dev`/`prod` is a hazard in most repositories,
the deny fires only when an independent workflow signal corroborates it
(dirty tree or recent `(#NNN)` PR-suffix commits, last 3 inspected — a
solo repo with no PR history and a clean tree passes silently), and `PRAXIS_PBGUARD_SKIP=1` /
`PRAXIS_PROTECTED_BRANCHES` remain the documented escapes. The two tiers:

| Tier                     | Hook                              | Default    | Scope                                                                |
| ------------------------ | --------------------------------- | ---------- | -------------------------------------------------------------------- |
| Generic safety net       | `pre-edit-protected-branch-guard` | **on**     | any repo, protected branches only, corroborated by a workflow signal |
| Strict worktree workflow | `worktree-edit-gate`              | **opt-in** | enrolled repos only, branch state alone, extension-filtered          |

### What is blocked

| Scenario                                                                         | Action                                                                  |
| -------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Protected branch + dirty tree + NEW file target                                  | `permissionDecision: "deny"` (dirty-tree path)                          |
| Protected branch + clean tree + recent commits show `(#NNN)` PR-suffix           | `permissionDecision: "deny"` (PR-workflow path, issue #231)             |
| Protected branch + dirty tree + file ALREADY in diff (in-flight)                 | silent pass-through                                                     |
| Protected branch + clean tree + no PR-suffix in recent commits                   | silent pass-through                                                     |
| Non-protected branch (`feature/…`, `issue-N-…`) + dirty tree                     | silent pass-through                                                     |
| Non-protected branch + PR-suffix in log                                          | silent pass-through (guard limited to protected branches)               |
| Edit target in `/tmp/` (no repo root found)                                      | silent pass-through (fail-open)                                         |
| Edit target in `.omc/plans/` or `.claude/projects/`                              | silent pass-through (planning artifact)                                 |
| Edit target is gitignored (`git check-ignore` matches)                           | silent pass-through (uncommittable → worktree workflow N/A, issue #493) |
| Edit target is README/CHANGELOG/docs file (unless `PRAXIS_PBGUARD_BLOCK_DOCS=1`) | silent pass-through (docs skip)                                         |
| Edit target inside `CLAUDE_PLUGIN_ROOT` (praxis plugin self-edit)                | silent pass-through                                                     |
| `PRAXIS_PBGUARD_SKIP=1` set in environment                                       | silent pass-through                                                     |
| `PRAXIS_PBGUARD_SKIP_PR_CHECK=1` set in environment                              | dirty-tree path still active; PR-workflow path skipped                  |
| `git` not installed / subprocess timeout                                         | silent pass-through (fail-open)                                         |
| Malformed stdin JSON                                                             | silent pass-through (fail-open)                                         |
| Detached HEAD                                                                    | silent pass-through (fail-open)                                         |

### Trigger conditions

**Dirty-tree deny path (ALL must be true):**

1. `tool_name ∈ {Edit, Write, NotebookEdit}`
2. No skip rule matches (see table above)
3. `git rev-parse --abbrev-ref HEAD` returns a protected branch name
   (detached HEAD → fail-open, no block)
4. `git status --porcelain` is non-empty (dirty working tree)
5. The edit target's repo-relative path is NOT present in the dirty-file set

**PR-workflow deny path (ALL must be true):**

1. `tool_name ∈ {Edit, Write, NotebookEdit}`
2. No skip rule matches (see table above); `PRAXIS_PBGUARD_SKIP_PR_CHECK` is not `1`
3. `git rev-parse --abbrev-ref HEAD` returns a protected branch name
   (detached HEAD → fail-open, no block)
4. `git status --porcelain` is empty (clean working tree)
5. `git log --oneline -3` has at least one line matching `\(#\d+\)\s*$`
   (zero commits or git failure → empty output → no signal → no block)

### Protected branches

Default set: `main`, `dev`, `prod`, `master`.

Override via:
- `PRAXIS_PROTECTED_BRANCHES=release,stable` (comma-separated env var)
- `.claude/hook-config.json` key `"protected_branches": ["release", "stable"]`

### Issue tracker URL

The block message includes an issue tracker URL. Configure via:
- `PRAXIS_ISSUE_TRACKER_URL=https://github.com/yourorg/yourrepo/issues/new`
- `.claude/hook-config.json` key `"issue_tracker_url": "https://…"`

Default (if not set): a placeholder string instructing the user to configure.

### `.claude/hook-config.json` example

```json
{
  "issue_tracker_url": "https://github.com/yourorg/yourrepo/issues/new",
  "protected_branches": ["main", "dev", "prod"]
}
```

### Registration in consuming project's `.claude/settings.json`

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|NotebookEdit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/_dispatch.sh PreToolUse 'Edit|NotebookEdit|Write' claude",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

The hook is also registered in the praxis plugin's own `hooks/manifest.json` and
fires automatically when the plugin is loaded.

### Response (deny)

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "[pre-edit:protected-branch-guard] Edit blocked: you are on protected branch 'main' with a dirty working tree..."
  }
}
```

### Parsing guarantees

- `tool_input.file_path` (Edit/Write) or `tool_input.notebook_path` (NotebookEdit)
  is read as-is — no shell tokenization. Paths with spaces are handled
  correctly because Python's `os.path` functions operate on raw strings.
- Git commands are run via `subprocess.run` with `cwd` derived from the edit
  target's directory. The hook never touches a shell; no injection risk.
- All `subprocess.run` calls use `timeout=5`. A slow git (network mount, cold
  index) will time out and fail-open rather than block Claude Code.
- Rename entries in `git status --porcelain` (`new -> old` and `\t`-separated
  forms) are parsed: both old and new paths are added to the dirty-file set.

### Test overrides (for CI / unit tests without a real repo)

| Env var                                   | Effect                                                                                                           |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `PRAXIS_PBGUARD_TEST_REPO_ROOT=<path>`    | Override `get_repo_root`. Use `"NONE"` to simulate not-in-a-repo.                                                |
| `PRAXIS_PBGUARD_TEST_BRANCH=<name>`       | Override current branch. Use `"HEAD"` to simulate detached HEAD.                                                 |
| `PRAXIS_PBGUARD_TEST_STATUS=<porcelain>`  | Override `git status --porcelain` output. Empty string = clean tree.                                             |
| `PRAXIS_PBGUARD_TEST_LOG=<oneline>`       | Override `git log --oneline -3` output. Empty string = no commits / no PR signal.                                |
| `PRAXIS_PBGUARD_TEST_IGNORED=<rel,paths>` | Override `git check-ignore`. Comma-separated repo-relative paths treated as gitignored. Not set = call real git. |

### Tests

```bash
bash tests/hooks/preflight-gate/test_pre_edit_protected_branch_guard.sh
```

Covers:
- **DENY paths (dirty-tree)**: dirty + protected + new target (Edit/Write/NotebookEdit),
  all four default protected branches, docs target with `PRAXIS_PBGUARD_BLOCK_DOCS=1`,
  custom protected branch list via `PRAXIS_PROTECTED_BRANCHES`.
- **DENY paths (PR-workflow, issue #231)**: clean + protected + `(#NNN)` suffix
  in all three log lines, only one of three lines matches, Write tool on clean tree,
  docs target with `PRAXIS_PBGUARD_BLOCK_DOCS=1`.
- **PASS paths**: clean tree + no PR signal, log without any `(#NNN)` suffix,
  non-protected branch (even with PR signal), `PRAXIS_PBGUARD_SKIP_PR_CHECK=1`
  (PR check only), `PRAXIS_PBGUARD_SKIP=1` with PR signal, README.md on clean
  tree with PR signal (docs skip wins), edit target already in dirty diff
  (in-flight continuation), untracked file in status (counts as in-flight),
  `/tmp/` target (fail-open: not in repo), `.omc/plans/` artifact,
  `.claude/projects/` memory file, gitignored target on both dirty-tree and
  PR-workflow paths + non-ignored target still denies when an unrelated path is
  ignored (issue #493), README.md / CHANGELOG.md / `docs/` directory
  (docs skip), `PRAXIS_PBGUARD_SKIP=1`, non-scoped tool (Bash, Read),
  `PRAXIS_PBGUARD_TEST_REPO_ROOT=NONE` (not a git repo), detached HEAD,
  malformed stdin, empty `file_path`.
