# PreToolUse git commit Rename Sweep Survivors Block

Supported hosts: claude

`hooks/preflight-gate/block-rename-sweep-survivors/impl.py` intercepts every Bash tool call and
hard-blocks a content `git commit` when the staged diff contains a rename sweep
(≥3 identical 1:1 identifier substitutions) and the old token still exists
in the tracked tree.

### Why this exists

An agent narrates "renamed X → Y across the codebase" but stages only a subset
of the affected files. The old token survives in unstaged files and surfaces as a
partial-fix sibling gap at review round 4–5. By the time a reviewer notices, the
fix has already been cited in a PR description and the author must open a follow-up
PR — inflating the PR count and diluting the rename's atomic commit guarantee.

Structural enforcement at the commit checkpoint (rather than a prose rule in
`CLAUDE.md`) is the only mechanism that reliably fires before the commit ships.
This is the counterpart of `CLAUDE.md §Atomic Commits`: that rule states intent;
this hook enforces it for the rename-sweep case.

### What is blocked

All three conditions must hold for a block (exit 2):

1. The Bash command is a content `git commit` — `--amend` and `--no-edit` are
   exempt; `git merge`, `git rebase`, `git cherry-pick`, and `git revert` are
   not gated (they never produce a new rename sweep).
2. The staged diff (`git diff --cached --unified=0`) contains a rename sweep:
   ≥ `SWEEP_THRESHOLD` (3) line pairs where a single token of length ≥
   `MIN_TOKEN_LEN` (5) was renamed 1:1 (one token removed, one added per line).
3. The old token still exists verbatim in the tracked tree (`git grep -n
   --fixed-strings`), with at least one surviving line not marked exempt.

| Situation | Action |
|-----------|--------|
| `git commit` with ≥3 renames and old token in tree | **BLOCKED** (exit 2) |
| `git commit` with ≥3 renames and all occurrences staged | **PASS** (no survivors) |
| `git commit` with ≥3 renames and all survivors marked exempt | **PASS** (exempt) |
| `git commit` with < 3 renames | **PASS** (below threshold) |
| `git commit --amend` / `git commit --no-edit` | **PASS** (exempt) |
| `PRAXIS_SKIP_RENAME_SWEEP_CHECK=1` | **PASS** (env bypass) |

The hook blocks on the **first** sweep that has survivors and stops scanning — if
multiple sweeps are present, fix or exempt the first, then commit; remaining
sweeps are reported on the next attempt.

### Escape hatches

- **Complete the sweep**: stage the remaining files in the same commit so all
  occurrences are renamed together.
- **Exempt a surviving line**: append `# [rename-sweep-exempt]` to the line. Use
  this for intentionally-kept occurrences (e.g. a string literal that is a public
  API key name, a test fixture referencing the old name, or generated code the
  agent does not own).
- **Split the commit**: if the surviving files are unrelated to the rename context,
  split them into a separate commit so each commit is independently coherent.
- **Env bypass**: set `PRAXIS_SKIP_RENAME_SWEEP_CHECK=1` for a one-off bypass
  (e.g. bulk-rename tooling that resolves survivors in a subsequent automated
  commit). The bypass is intentionally one-shot — it is not persisted.

### Implementation note — buffer-flush diff parser

`git diff --unified=0` emits consecutive-line changes as a **grouped hunk**:

```
-line1
-line2
-line3
+line1
+line2
+line3
```

A simple adjacent-pair algorithm (match each `-` with the immediately following
`+`) counts only one pair for this layout and falls below `SWEEP_THRESHOLD=3`.
The hook uses a **buffer-flush** parser that accumulates removed lines into a
buffer; when the removed-line run ends (a `+` block, a context line, or a
`---`/`+++` file header), the buffer is paired positionally with the accumulated
added lines. Both the alternating layout (`-/+/-/+`) and the grouped layout are
handled — the alternating case flushes a 1-element buffer each time, making it a
strict superset of the adjacent-pair algorithm.

Command detection (`_find_git_commits`) uses `safe_tokenize` + `iter_command_starts`
+ `strip_prefix` from `_hook_utils` so compound commands (`true; git commit`,
`$(git commit)`, `(git commit)`) are all detected. Git global flags that consume
the next token (`-C`, `--git-dir`, etc.) are skipped before the subcommand
position is read.

### Tests

```bash
bash tests/hooks/preflight-gate/test_block_rename_sweep_survivors.sh
```

Covers 11 cases: block paths (sweep ≥ threshold with survivors, grouped hunk
layout with survivors), pass paths (sweep ≥ threshold but all occurrences staged,
grouped hunk with no survivors), below-threshold (< 3 renames), survivors all
marked exempt, not a git commit command, `--amend` exemption, env bypass
(`PRAXIS_SKIP_RENAME_SWEEP_CHECK=1`), non-Bash tool, empty command.
