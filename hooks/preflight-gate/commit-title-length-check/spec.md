# PreToolUse Commit Title Length Check

Supported hosts: all

`hooks/commit-title-length-check.py` intercepts every AI-authored `git commit`
Bash call and emits `permissionDecision: "ask"` when the first line of the
commit message exceeds the configured maximum (default 50, matching the global
global `~/.claude/CLAUDE.md` "Git Commit & Title Rules — Title: max 50 characters" rule).

### Why a PreToolUse hook instead of a git commit-msg hook

The issue body suggests a commit-msg hook because that is the natural insertion
point. However, the praxis distribution model ships Claude Code hooks (loaded
via `hooks.json`), not git-side hooks.

A git commit-msg hook would require installation into every repo's `.git/hooks/`
directory — an out-of-band setup step that is easy to miss, not portable across
worktrees, and breaks when a repo is freshly cloned. A PreToolUse hook fires
centrally for every AI-authored Bash call in any repo/worktree, with no per-repo
setup required.

Trade-off: the hook only catches AI-authored commits (not manual shell commits),
which is exactly the population that produced the silent violations described in
issue #177.

### What is warned

| Command shape                    | Action                                |
| -------------------------------- | ------------------------------------- |
| `git commit -m "title"`          | ask when `len(title) > 50`            |
| `git commit --message "title"`   | ask when `len(title) > 50`            |
| `git commit -m="title"`          | ask when `len(title) > 50`            |
| `git commit -am "title"`         | ask when `len(title) > 50`            |
| `git commit --amend -m "title"`  | ask when `len(title) > 50`            |
| `git commit -F /path/to/file`    | reads first line; ask when over limit |
| `git commit -F -` (stdin)        | silent pass (acknowledged limitation) |
| `Merge ...` / `Revert ...` title | silent pass (auto-generated)          |
| `git status`, `git push`, etc.   | silent pass (not a commit)            |

Length counting uses Python `len(str)` which counts Unicode code points — the
correct measure for the 50-char rule in Korean/CJK mixed commit titles.

### Configuration

| Env var                   | Default | Effect                            |
| ------------------------- | ------- | --------------------------------- |
| `CLAUDE_COMMIT_TITLE_MAX` | `50`    | Override the maximum title length |

Setting `CLAUDE_COMMIT_TITLE_MAX=80` allows longer titles (e.g. for repos with
a 72-char convention) without disabling the hook.

### Response

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "Commit title too long: 82 chars (max 50).\nTitle: '...'\nShorten to ≤50 chars, or embed `# title-length:ack` to bypass."
  }
}
```

### Compound cascade advisory (issue #229)

When the ask fires on a compound Bash command containing a state-changing
step (e.g. `mkdir -p /tmp/log && git commit -m "$(cat /tmp/log/very-long-..."`),
the ask reason is suffixed with the shared
`_hook_utils.compound_cascade_hint` text. If the user denies the prompt, the
chained `mkdir`/redirect/download also did not run — retries must materialize
those files first.

### Opt-out marker

Embed `# title-length:ack` anywhere in the command to bypass the check for
known-intentional long titles (e.g. auto-generated merge commits handled by a
script):

```bash
git commit -m "Merge remote-tracking branch 'origin/main' into feature/long-name"  # title-length:ack
```

### Parsing guarantees

Inherits `safe_tokenize` / `iter_command_starts` / `strip_prefix` from
`_hook_utils.py` (same primitive as the sibling hooks):

- Shell operators (`;`, `&&`, `||`, `|`) split command segments — a chained
  `git fetch && git commit -m "long"` correctly reaches the `git commit` segment.
- Env prefixes (`GIT_AUTHOR_NAME=x git commit -m "title"`), wrapper commands
  (`sudo`, `env`), and shell control-flow keywords are peeled before matching.
- Quoted strings protect their contents — `echo "git commit -m 'fake'"` does
  not trigger the hook because `echo` is argv[0] of that segment.
- Second `-m` flag is body, not title — `git commit -m "short" -m "long body"`
  only checks the first `-m` value.
- Subshells (`$(...)`) are opaque — acknowledged limitation shared with all
  sibling hooks.
- **Literal newline inside a single quoted `-m` value bypasses the check.**
  `git commit -m "Title<newline>Body"` (where `<newline>` is an unescaped LF
  character inside the quoted string) is split by `_hook_utils.safe_tokenize`'s
  newline-aware preprocessor before shlex sees the opening quote, leaving an
  unmatched-quote fragment that gets dropped. Use `-m "title" -m "body"` or
  heredoc-assigned variables for multi-line commit messages — both extract
  the title correctly. This is a documented limitation of the shared
  tokenizer (see `_hook_utils.py` docstring), preserved to keep
  newline-separated multi-command detection intact for sibling hooks.

### Tests

```bash
bash tests/test_commit_title_length_check.sh
```

Covers 47 cases: boundary (50 chars), under (49 chars), long via `-m` /
`--message` / `-m=value` / `--amend` / `-am`, Korean 51-code-point title,
Hub #1912 regression (82-char title), Merge/Revert skip, body-in-second-m
protection, chained command, `CLAUDE_COMMIT_TITLE_MAX` override (both
directions), `-F` file (short and long), `-F -` stdin pass-through, opt-out
marker, echo false-positive guard, non-Bash tool passthrough, malformed JSON
fail-open, plus regression coverage for `git -C <dir>` global flags,
attached-form `-m"value"`, `-S<keyid>` whitelist (must not be misparsed as
combined `-m`), and `-C <dir>` + relative `-F <file>` resolution including
stacked `-C` flags.
