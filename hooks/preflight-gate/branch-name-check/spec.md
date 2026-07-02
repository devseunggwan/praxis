# PreToolUse Branch Name Check

Supported hosts: all

`hooks/preflight-gate/branch-name-check/impl.py` intercepts every AI-authored
branch-creation Bash call and emits `permissionDecision: "deny"` (strict, default)
or a stderr advisory (STRICT=0) when the new branch name does not match the
configured naming convention regex.

### Intercepted commands

| Command shape | Trigger |
| --- | --- |
| `git checkout -b <name>` | New branch creation |
| `git checkout --orphan <name>` | New unborn branch creation |
| `git checkout --track origin/<name>` | Implicit local branch from remote ref basename |
| `git switch -c <name>` | New branch creation |
| `git switch --create <name>` | New branch creation (long form of `-c`) |
| `git switch --force-create <name>` | Force new branch creation (long form of `-C`) |
| `git switch --orphan <name>` | New orphan branch creation |
| `git switch --track origin/<name>` | Implicit local branch from remote ref basename |
| `git branch <name> [<start>]` | New branch creation (no checkout) |
| `git worktree add <path> -b <name>` | Worktree + new branch (path-first form) |
| `git worktree add -b <name> <path>` | Worktree + new branch (flag-first form) |
| `git worktree add <path>` (no `-b`) | Implicit branch from `basename(<path>)` |
| `git worktree add --orphan <path>` | Implicit orphan branch from `basename(<path>)` |
| `git checkout <existing>` (no `-b`) | NOT a creation — silent pass |
| `git switch <existing>` (no `-c`) | NOT a creation — silent pass |
| `git branch -d/-D/-m/-M/-r/-a/...` | NOT a creation — silent pass |
| `git branch --format/--sort/--list ...` | NOT a creation — silent pass |
| Other `git` subcommands | Silent pass |

### What is checked

The extracted branch name is tested against:
1. **Whitelist** — always allowed, checked first (default: `main master dev prod staging`)
2. **Regex** — `fullmatch` against the configured pattern

Names passing either check proceed silently. Names failing both are blocked or warned.

### Default regex

```
^(hub|issue)-[0-9]+-(feat|fix|docs|style|refactor|chore|test|perf|ci|build|hotfix)-[a-z0-9-]+$
```

Examples:
- `hub-434-feat-branch-name-check` — PASS
- `issue-12-fix-null-ptr` — PASS
- `feature/my-cool-change` — BLOCK (no issue number, wrong separator)
- `hub-fix-foo` — BLOCK (missing issue number)

### Configuration

| Env var | Default | Effect |
| --------- | --------- | -------- |
| `PRAXIS_BRANCH_NAME_REGEX` | `^(hub\|issue)-[0-9]+-(feat\|fix\|docs\|style\|refactor\|chore\|test\|perf\|ci\|build\|hotfix)-[a-z0-9-]+$` | Override the naming pattern |
| `PRAXIS_BRANCH_NAME_STRICT` | `1` | `1` = deny (block); `0` = advisory (stderr only) |
| `PRAXIS_BRANCH_NAME_WHITELIST` | `main,master,dev,prod,staging` | Comma-separated names always allowed |

### Response (strict mode, default)

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "Branch name 'feature/foo' does not match the required pattern.\n..."
  }
}
```

### Response (advisory mode, STRICT=0)

Emits a one-line warning to stderr. The tool call proceeds.

### Argument-order invariance (worktree add)

`git worktree add` accepts both:
- `git worktree add <path> -b <name>` (path before flag)
- `git worktree add -b <name> <path>` (flag before path)

The hook's token walker finds `-b` regardless of its position in the argument
list, so both forms are correctly intercepted.

### Flag forms handled

- `-b <name>` (separate token, standard)
- `-b<name>` (attached, no space) — extracted via prefix strip
- `-B <name>` / `-B<name>` — same logic (force-create checkout)
- `-c <name>` / `-C <name>` for `git switch`
- `--create <name>` / `--force-create <name>` / `--orphan <name>` for `git switch` (long forms)
- `--orphan <name>` for `git checkout`
- `git branch <name>` — first non-flag positional; suppressed for `-d/-D/-m/-M/-r/-a/--list/-l` flags and value-consuming query flags (`--format`, `--sort`, `--color`, `--abbrev`, `--column`)

### Parsing guarantees

Uses `safe_tokenize` / `iter_command_starts` / `strip_prefix` from `_hook_utils.py`:
- Shell separators (`;`, `&&`, `||`, `|`) split segments — each is checked independently
- Env prefixes and wrapper commands (`sudo`, `env`) are peeled before matching
- Quoted strings protect their contents — `echo "git checkout -b bad"` does not trigger
- Git global flags (`-C`, `-c`, `--git-dir`, etc.) are stripped before the subcommand check

### Fail-open guarantees

- Malformed JSON stdin → exit 0 (pass)
- Regex compilation error (bad `PRAXIS_BRANCH_NAME_REGEX`) → falls back to default pattern
- Non-Bash tool → exit 0 (pass)
- No branch-creation command detected → exit 0 (pass)

### Tests

```bash
bash tests/hooks/preflight-gate/test_branch_name_check.sh
```

Covers: good name (pass), wrong format (block), missing prefix (block), missing
issue number (block), wrong type token (block), whitelisted names (pass), bare
checkout/switch without -b/-c (pass), both worktree-add argument orders,
advisory mode, custom regex env, custom whitelist env, non-creation git commands,
git switch long forms (--create / --orphan), git branch creation and non-creation
flag forms (-d/-D/-m/-M/-r/-a/--list), malformed JSON fail-open.
