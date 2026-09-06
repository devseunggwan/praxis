# PreToolUse Pre-Commit Staged-File Enumeration Advisory

Supported hosts: claude

`hooks/advisory-nudge/pre-commit-staged-file-enumeration/impl.py` intercepts
`Bash` tool calls containing a fresh `git commit` and emits a **stderr
advisory** (never a block) listing the staged file **additions** that were not
created through a `Write` / `Edit` / `NotebookEdit` tool call this session.

## Why this exists

The `Write` / `Edit` tools carry a read-before-write guard and are tracked by
the harness. A file produced by a shell heredoc, a `> file` redirect, or an
external script bypasses that tracking entirely — and if it lands in the index
(`git add .`, `git add <dir>`, or an explicit `git add`), it rides into the
commit without the agent ever consciously "seeing" it. AGENTS.md
(`Bash Redirect on Existing Path` / `Atomic Commits`) and memory
`feedback_pre_commit_staged_file_enumeration` (hookable: commit) capture the
rule, but prompt-layer retrieval at commit time keeps failing. This hook fires
on the `git commit` step itself.

## Precision — why transcript-based, not "list every addition"

Surfacing *every* staged addition would fire on the normal case (files the
agent wrote via `Write`) and become noise the agent learns to ignore. Instead
the hook subtracts the set of `Write` / `Edit` / `NotebookEdit` target paths
recorded in this session's transcript (compared by `os.path.realpath`, so a
repo-relative staged path and an absolute tool `file_path` — and a symlink and
its target — compare equal) from the staged additions, and surfaces only the
remainder: files that entered staging through some other path.

## What is emitted

The hook writes advisory text to stderr and exits 0. Tool execution is never
blocked.

| Condition | Result |
| --- | --- |
| `git commit` with a staged addition NOT written via Write/Edit/NotebookEdit | `[staged-enum]` advisory listing the unseen additions (first 10 + `... +N more`) |
| `git commit` where every staged addition maps to a Write/Edit/NotebookEdit target | silent |
| No staged additions (`git diff --cached --diff-filter=A` empty) | silent |
| Staged modifications / renames / deletions only (no `A` status) | silent (only additions are inspected) |
| `git commit --dry-run` / `--help` / `-h` | silent (commit nothing) |
| `git commit --amend` with unseen staged additions | **inspected** — `--amend` folds staged additions into the replacement commit, so they must still be surfaced |
| `git merge` / `git rebase` / `git cherry-pick` / `git revert` | silent (not the `commit` subcommand) |
| `git commit-tree` | silent (single token `commit-tree` ≠ `commit`) |
| `echo "git commit"` / `git log --grep="git commit"` | silent (no real `git commit` invocation) |
| `/usr/bin/git commit` / `foo && git commit` | inspected (path-prefixed binary and compound chains are detected) |
| `git -C <dir> commit` | detected, but the staged-diff is read from the **hook process cwd**, not `<dir>` — a cross-repo `-C` target is not honored (matches the sibling hooks' cwd-scoped git queries). Same-repo `-C subdir` shares one index, so results are identical |
| `transcript_path` missing / unreadable / oversized (> 50 MiB) | silent (fail-open — cannot compute the seen-set) |
| Not inside a git work tree, or any git subprocess fails/times out | silent (fail-open) |
| Opt-out marker `# [staged-enum-ack]` anywhere in the command | silent |
| `PRAXIS_SKIP_STAGED_FILE_ENUM=1` in the environment | silent |
| Non-Bash tool / malformed JSON stdin / empty command | silent (fail-open) |

The seen-set scan streams the transcript and parses only the lines that carry
a `tool_use` block or an `is_error` result — the two record kinds it reads —
instead of loading the file and parsing every line (issue #1312). The 50 MiB
bound is counted on the bytes actually read.

## `git commit` detection

Command tokenization uses the shared `_hook_utils` pipeline (`safe_tokenize`,
`iter_command_starts`, `strip_prefix`) — the same detection surface as
`block-rename-sweep-survivors`. The binary is matched bare or path-prefixed
(`git`, `/usr/bin/git`), git global value flags (`-C`, `-c`, `--git-dir`, …)
are skipped to find the subcommand position, terminal global flags (`--help`,
`--version`) short-circuit, and only the `commit` subcommand — absent
`--amend` — is treated as a fresh content commit.

## Seen-set (transcript scan)

The session transcript (`transcript_path` from the hook payload) is read once
(bounded at 50 MiB). Every `tool_use` block whose `name` is `Write` / `Edit` /
`MultiEdit` (`input.file_path`) or `NotebookEdit` (`input.notebook_path`)
contributes its canonical path to the seen-set. A staged addition whose
canonical path is absent from that set is surfaced.

**Successful uses only.** A file tool whose `tool_result` carries
`is_error: true` (a PreToolUse-rejected or failed Write) did not create the
file — so its target must not count as seen, or a file later created via bash at
that same path would be wrongly suppressed. `tool_use` blocks are correlated to
their `tool_result` by `id` ↔ `tool_use_id`; a use whose id appears in the
failed set is dropped from the seen-set.

**Canonical path, not full realpath.** Paths are compared via
`os.path.join(os.path.realpath(dirname), basename)` — the parent directory is
canonicalized (neutralizing repo-relative-vs-absolute and symlinked-parent
differences) but the final component is preserved. Full `realpath` would resolve
a symlink's final component too, making a newly-staged symlink `alias → target`
compare equal to a Write on `target` and thus wrongly suppress the symlink
addition.

## Limitation — subagent writes

Only the root session transcript is scanned. A file written by a Task/Agent
subagent (recorded in a sibling `<session>/subagents/agent-*.jsonl`) is not in
the root seen-set and may therefore be surfaced. Because the hook only ever
nudges (exit 0, never a block), this conservative false-surface costs the agent
one extra glance, never a blocked commit. Root-only scanning keeps the hook
simple; a subagent-aware scan (as in `block-commit-without-codex-review`) can
be added later if the false-surface rate proves noisy.

## Limitation — single-call create + stage + commit

The hook reads the index at PreToolUse time, **before** the Bash command runs.
So a command that creates, stages, and commits in one shot —
`printf x > f && git add f && git commit -m y` — has an empty
`git diff --cached --diff-filter=A` at scan time (the `git add` has not executed
yet), and nothing is surfaced. The hook targets the dominant flow where
`git commit` is a discrete Bash call after the file was created/staged in an
earlier step, where the index already holds the addition.

Recovering the in-call staging set would require parsing `git add`'s pathspec
arguments (globs, `.`, `-A`, `--pathspec-from-file`, …). That was deliberately
rejected as a fragile shell-argument parser (memory
`feedback_shell_parser_diminishing_returns`): the boundary is documented here
rather than chased across quoting corners. Run `git commit` as its own step to
get the check.

## Detection boundaries (shared tokenizer)

`git commit` detection uses the shared `safe_tokenize` / `iter_command_starts`
pipeline, so it inherits that tokenizer's boundaries — the same ones the
blocking sibling `block-rename-sweep-survivors._find_git_commits` has. Verified
by probing the sibling on identical inputs (all three match this hook exactly):

| Input | Result | Consequence |
| --- | --- | --- |
| `result=$(git commit -m x)` (commit inside an assignment's command substitution) | **not detected** | a commit whose stdout is captured into a shell variable is missed (false-negative) — even the blocking sibling misses it |
| `git commit --only <path>` / `git commit -- <pathspec>` / `git commit <path>` (partial commit) | detected, but **all** staged additions are listed | additions the pathspec excludes are over-surfaced (advisory noise) |
| `cat > f <<'EOF'` … `git commit` … `EOF` (commit literal inside a heredoc body) | **not detected** (fixed in #985) | heredoc bodies are blanked in `_hook_utils.safe_tokenize`, so data no longer reads as a command — this row is kept as the record of what changed |

These are accepted boundaries, not per-hook defects: the detection idiom is
shared across the git-commit hook family, so fixing them belongs in
`_hook_utils` (affecting every consumer uniformly), not in this hook where it
would diverge from the siblings. Two are advisory over-surfaces (noise on an
exit-0 nudge); the assignment-substitution miss is a rare pattern an agent
almost never uses for `git commit`. Recovering the partial-commit pathspec set
would require a fragile git-argument parser — deliberately rejected (memory
`feedback_shell_parser_diminishing_returns`).

## Relationship to sibling hooks

| Hook | Scope | Overlap |
| --- | --- | --- |
| `block-commit-without-codex-review` | blocks commit lacking a codex-review-wrap pass | None — different precondition; shares the `git commit` detection idiom |
| `block-rename-sweep-survivors` | blocks commit with incomplete rename sweeps | None — different staged-content analysis; shares command detection |
| `side-effect-scan` | flags collateral mutations (`git commit/push`, `gh pr merge`) | Complementary — this hook inspects *what is staged*, not the mutation itself |

## Parsing guarantees (fail-open)

The hook returns exit 0 on every path — advisory or infrastructure error:
malformed JSON stdin, non-Bash tool, empty command, git unavailable/timeout,
unreadable transcript, or any uncaught exception (wrapped by `@fail_open`).

## Tests

```bash
bash tests/hooks/advisory-nudge/test_pre_commit_staged_file_enumeration.sh
```

Covers: bash-created staged addition surfaced; Write/Edit/MultiEdit-created
addition silent; mixed (only the bash-created one surfaced); `--amend` with
staged additions surfaced / clean-index `--amend` silent; `--dry-run` / `--help`
silent; symlink-to-seen-target surfaced; failed-Write target surfaced;
modification-only silent; `git commit-tree` / quoted-literal silent; compound
`&&`, `-c` global flag, and path-prefixed `git` detected; opt-out marker and env
bypass silent; fail-open (non-Bash, malformed JSON, empty command, missing
transcript).

The documented boundaries in "Limitations" / "Detection boundaries" are pinned
by regression tests: single-call create+add+commit is silent; `--only` partial
commit over-lists the excluded addition; a `git commit` literal in a heredoc
body is silent (#985); `result=$(git commit)` is not detected. These pin current
behavior so a future tokenizer change surfaces here.
