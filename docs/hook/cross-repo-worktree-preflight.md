# PreToolUse Cross-Repo Worktree Pre-Flight

Supported hosts: all

`hooks/cross-repo-worktree-preflight.sh` intercepts `git worktree remove
<abs-path>` invocations and surfaces a `permissionDecision: "ask"` when the
path is **not** registered in the cwd repo's worktree list.

### Why this exists

`git worktree remove` resolves its path argument against the cwd repo's
`.git/worktrees` registry. When the cwd belongs to repo A but the path lives
under repo B, git aborts with `fatal: '<path>' is not a working tree`. In a
chained command (`git worktree remove X && gh pr merge ...`) the chain halts
mid-flight, leaving a partial post-merge cleanup state.

Memory entry `worktree_context_pre_git_op` already covers the rule, but the
retrieval moment (command-composition) is exactly where the memo fails. This
hook moves enforcement to the Bash boundary (praxis issue #246; incident
laplacetec/laplace-dev-hub#2060 on 2026-05-13).

### What is asked

The hook uses `safe_tokenize → iter_command_starts → strip_prefix` (same
pipeline as sibling hooks) so only live `git` invocations match. Pattern
references inside quoted strings, echo bodies, or comments are transparent
pass-throughs.

| Command | Action |
|---------|--------|
| `git worktree remove /abs/path/owned/by/other-repo` | **ASK** |
| `git worktree remove -f /abs/path/owned/by/other-repo` | **ASK** |
| `git worktree remove --force /abs/path/owned/by/other-repo` | **ASK** |
| `git worktree remove /abs/path/owned/by/other-repo/` | **ASK** (trailing slash normalized) |
| `git worktree remove /abs/path && gh pr merge ...` | **ASK** (chained — applies to the first segment) |
| `git worktree remove ../sibling-wt` from repo A (resolves into repo B) | **ASK** — relative resolved against effective cwd |
| `git worktree remove /abs/path/owned/by/cwd-repo` | **PASS** — target in cwd repo's list |
| `git worktree remove ../cwd-repo-wt` (relative, resolves into cwd repo) | **PASS** — resolved path in cwd repo's list |
| `cd /owning-repo && git worktree remove /owning-repo-wt` | **PASS** — `cd` updates effective cwd before lookup |
| `cd /owning-repo && git worktree remove ../owning-repo-wt` | **PASS** — `cd` + relative both honored |
| `cd $VAR && git worktree remove /cross-repo-wt` | **ASK** — unresolvable `cd` does not update effective cwd |
| `git -C /other-repo worktree remove /abs/path` | **PASS** — explicit `-C` override |
| `git --git-dir=/other/.git worktree remove /abs/path` | **PASS** — explicit override |
| `git --work-tree=/other worktree remove /abs/path` | **ASK** — `--work-tree` alone does NOT switch gitdir; cwd repo's registry still consulted |
| `git --git-dir=/other/.git --work-tree=/other worktree remove /abs/path` | **PASS** — paired form is a real override |
| `git worktree list` / `git worktree add ...` | **PASS** — non-remove subcommand |
| `git status` / non-git command | **PASS** — different command |
| `git worktree remove /abs/path  # worktree-chain:ack` | **PASS** — opt-out |

The path comparison resolves symlinks (`os.path.realpath`) so that
`/tmp/foo` (macOS) compares equal to `/private/tmp/foo` returned by
`git worktree list --porcelain`.

### Response format

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "⚠️  Cross-repo worktree pre-flight: ..."
  }
}
exit 0
```

The reason text lists every worktree currently registered in the cwd repo so
the operator can see which worktree owns the target path and re-issue the
command from the correct cwd.

### Compound cascade advisory (issue #229)

When the parent Bash command is compound (`&&`, `||`, `;`, `|`, newline)
AND contains a state-changing step (`> file`, `mkdir`, `tee`, `cp`/`mv`/`rm`,
`curl -o`), the shared `_hook_utils.compound_cascade_hint` suffix is
appended to the ask reason. The classic shape this guards against is
`git worktree remove X && gh pr merge --squash --delete-branch` where the
operator expects both halves to land but a stale-cwd rejection of the
worktree-remove half cascades to abort the merge silently.

### Opt-out

Append `# worktree-chain:ack` after manually confirming the target path:

```bash
git worktree remove /abs/path/owned/by/other-repo  # worktree-chain:ack
```

The marker disables the pre-flight check for that single invocation. Use
only after `git worktree list` (run in the correct cwd) confirms the path
is the intended target.

### Parsing guarantees (fail-open)

The hook returns exit 0 (transparent pass-through) on every infrastructure
error so it never breaks a Claude Code session:

- malformed JSON stdin
- non-Bash tool invocation
- empty / whitespace-only command
- cwd is not a git repo (`git worktree list` non-zero)
- `git` binary unavailable / 3 s timeout
- `python3` unavailable (the shell wrapper handles this case)

### Relationship to sibling hooks

| Hook | Scope | Overlap |
|------|-------|---------|
| `cross-boundary-preflight` | `gh pr/issue create/edit --repo` | None — different command family |
| `side-effect-scan` | generic collateral side effects (git commit/push, pr merge) | Complementary — side-effect-scan fires after this on the merge half of `worktree-remove && pr-merge` chains |
| `pre-merge-approval-gate` | `gh pr merge` per-PR approval | Complementary — merge-side gate; this hook covers the pre-merge cleanup half |

### Tests

```bash
bash hooks/test-cross-repo-worktree-preflight.sh
```

28 cases: 10 ask paths (cross-repo abs, `-f`, `--force`, trailing slash,
chained, `-c name=value` global flag, `-c $(...)` unquoted command
substitution, relative `../` resolving cross-repo, `cd $VAR` unresolvable,
bare `--work-tree` (not a real override)), 13 pass paths (cwd-owned abs,
cwd-owned relative `../`, opt-out, two real-override forms (`-C`,
`--git-dir`), paired `--git-dir`+`--work-tree`, `cd /owning && remove
abs`, `cd /owning && remove relative`, non-remove subcommand variants,
non-git command, empty, comment-only), 2 worktree non-remove pass-throughs,
3 infrastructure (non-Bash passthrough, malformed JSON fail-open, non-git
cwd fail-open).
