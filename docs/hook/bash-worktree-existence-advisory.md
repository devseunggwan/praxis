# PreToolUse Bash Worktree Existence Advisory

Supported hosts: all

`hooks/bash-worktree-existence-advisory.sh` intercepts `Bash` tool calls
containing a `cd <path>` command and emits a **stderr advisory** (never a
block) when the target path does not exist on disk.

### Why this exists

Claude Code sessions frequently use `cd <worktree-path> && <cmd>` chains to
scope work to a specific git worktree. When the worktree directory was removed
externally (e.g., `git worktree remove` in a prior session) or the path was
mistyped, the failure happens inside the Bash tool and produces a cryptic shell
error — not the clear "you are targeting a missing worktree" message that would
let the agent self-correct. Memory entry `worktree_context_pre_git_op` covers
the rule, but retrieval at command-composition time keeps failing. This hook
fires earlier than `cross-repo-worktree-preflight` (which handles the remove
operation) by checking the `cd` step itself (praxis issue #322).

### What is emitted

The hook writes advisory text to stderr and exits 0. Tool execution is never
blocked.

| Condition | Message |
|-----------|---------|
| `cd <path>` where path does not exist | `[worktree-missing] <path> does not exist — check 'git worktree list' before retry` |
| `cd <path>` where path exists | silent (no output) |
| Bare `cd` (no argument) | silent |
| `cd $VAR` / `cd $(...)` | silent (variable/substitution expansion, unresolvable) |
| `cd -` | silent (previous dir, unresolvable) |
| `cd ~` / `cd ~/foo` / `cd ~user/foo` | silent (tilde expansion; hook process `$HOME` may differ from agent effective `$HOME`) |
| `pushd /path` | silent (out of scope Phase 1) |
| `(cd /path && ...)` subshell | silent (out of scope Phase 1) |
| heredoc body containing `cd /path` | silent (heredoc body not parsed as commands) |
| Opt-out marker `# worktree-advisory:ack` | silent (all advisories suppressed) |
| Non-Bash tool name | silent (fail-open) |
| Malformed JSON stdin | silent (fail-open) |
| `git` binary unavailable / timeout | silent (fail-open) |

### Scope (Phase 1)

Only direct `cd <path>` commands are detected. `pushd`, subshell form
`(cd /path && ...)`, and `cd` inside heredoc bodies are out of scope for
this phase to avoid parser complexity. A follow-up issue tracks Phase 2
expansion to cover these forms.

### Path detection

The hook segments the Bash command using `tokenize_with_roles` (same pipeline
as sibling advisory hooks) and inspects each segment for a leading `cd`
command. For compound commands (`cd <path> && <cmd>`) the `cd` segment is
found first; the resolved path is used as the effective cwd for subsequent
segments.

Relative paths are resolved against the hook process's cwd (or the preceding
segment's resolved path in a compound chain). Symlinks are canonicalized via
`os.path.realpath` so that `/tmp/foo` compares equal to `/private/tmp/foo` in
the `git worktree list --porcelain` output on macOS.

### Deduplication

Per-session marker files in:
```
${TMPDIR:-/tmp}/praxis-bash-worktree-advisory/<session_id>.<path-hash>.<state>
```
suppress repeat advisories for the same (session, path, state) triple.
The `<state>` component (`missing`) ensures that if a path is later created,
the old marker does not suppress a fresh advisory (or silence) for the new
state. Stale marker files are cleaned up by the OS tmp purge policy; no
explicit cleanup is performed.

### Opt-out

Append `# worktree-advisory:ack` anywhere in the command to suppress all
advisories for that invocation:

```bash
cd /some/path  # worktree-advisory:ack
```

Use only after confirming that the path context is intentional (e.g.,
creating a new directory that does not yet exist).

### Relationship to sibling hooks

| Hook | Scope | Overlap |
|------|-------|---------|
| `cross-repo-worktree-preflight` | `git worktree remove <path>` cross-repo mismatch | Complementary — this hook fires on the `cd` step *before* any git operation; the sibling fires on the remove step |
| `cross-boundary-preflight` | `gh` write subcommands across `--repo` boundary | None — different command family |
| `side-effect-scan` | collateral side effects (`git commit/push`, `gh pr merge`) | Complementary — fires on downstream mutation, not worktree navigation |

### Parsing guarantees (fail-open)

The hook returns exit 0 on every infrastructure error:

- malformed JSON stdin
- non-Bash tool invocation
- empty / whitespace-only command
- `python3` unavailable (the shell wrapper handles this)
- any uncaught exception in the inner logic

### Tests

```bash
bash tests/test_bash_worktree_existence_advisory.sh
```

21 cases covering: missing path advisory (direct and compound command),
existing path silent (including registered worktree), tilde expansion forms
(`~`, `~/foo`, `~user/foo`), pushd/subshell out-of-scope silent,
heredoc body silent, dedupe state-transition (first fires, second deduped,
different session fires independently), infrastructure fail-open (non-Bash
tool name, malformed JSON, bare cd, `$VAR`, `cd -`, opt-out marker, empty
command).
