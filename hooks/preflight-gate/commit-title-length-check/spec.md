# PreToolUse Commit Title Length Check

Supported hosts: all

`hooks/preflight-gate/commit-title-length-check/impl.py` intercepts every AI-authored `git commit`
Bash call and emits `permissionDecision: "ask"` when the first line of the
commit message exceeds the configured maximum (default 50, matching the
*Git Commit & Title Rules — Title: max 50 characters* rule in
[`CONTRIBUTING.md` → Commit conventions](../../../CONTRIBUTING.md#commit-conventions); [`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries)).

## Why a PreToolUse hook instead of a git commit-msg hook

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

## What is warned

| Command shape | Action |
| --- | --- |
| `git commit -m "title"` | ask when `len(title) > 50` |
| `git commit --message "title"` | ask when `len(title) > 50` |
| `git commit -m="title"` | ask when `len(title) > 50` |
| `git commit -am "title"` | ask when `len(title) > 50` |
| `git commit --amend -m "title"` | ask when `len(title) > 50` |
| `git commit -F /path/to/file` | reads first line; ask when over limit |
| `git commit -F -` (stdin) | silent pass (acknowledged limitation) |
| `Merge ...` / `Revert ...` title | silent pass (auto-generated) |
| `git status`, `git push`, etc. | silent pass (not a commit) |

Length counting uses Python `len(str)` which counts Unicode code points — the
correct measure for the 50-char rule in Korean/CJK mixed commit titles.

## Squash-merge path (issue #843)

`git commit` is not the only path a commit title reaches `main` through: `gh
pr merge --squash` combines the PR's title into the squash commit title on
GitHub's side, a shape the `git commit` matcher above cannot see. Two real
squash titles already landed on `main` over the limit — #834 at 61
chars, #835 at 63 chars (both measured excluding any trailing `(#N)`
suffix GitHub appends) — because the gate only ever fired on the branch's own `git commit`
calls, never on the merge that actually produced the title landing on
`main`.

| Command shape | Action |
| --- | --- |
| `gh pr merge <id> --squash` / `-s` | resolve PR title via `gh pr view`; **advisory** (stderr) when over limit |
| `gh pr merge <id> --squash -t "<subject>"` | `-t`/`--subject` value IS the title — no network call, checked directly |
| `gh pr merge <id> --merge` / `--rebase` (no `-s`) | silent — not a squash, title composition differs |
| `gh -R owner/repo pr merge <id> --squash` | `-R`/`--repo` forwarded to the `gh pr view` resolution call |
| `gh api repos/.../pulls/N/merge --method PUT -f merge_method=squash` | resolve PR title via `gh pr view` and emit **advisory** when over limit |
| `gh api repos/.../pulls/N/merge -f merge_method=squash -f commit_title="..."` | check explicit `commit_title` directly, no extra network call |

### Severity: advisory, not `ask`

Unlike the `git commit` path (which blocks via `ask`), the squash-merge path
only ever writes a stderr advisory and always exits 0 — it never blocks the
merge. Two reasons: (1) this path makes a live network call (`gh pr view`)
that the `git commit` path never needed, so a transient API failure or
latency spike must not be able to stall a merge; (2) the issue itself
recommends starting conservative — "1단계는 advisory로 시작 권장" — given the
new failure surface (network dependency) this path introduces relative to
the pure-local `git commit` check.

### `gh api .../pulls/N/merge` coverage

`gh api` merge-path coverage is now included for squash merges.
When `merge_method=squash` is present, the hook checks either explicit
`commit_title` or PR title (via `gh pr view` fallback).

### `-t`/`--subject` override

`gh pr merge` accepts `-t`/`--subject <value>` to set the squash commit
subject directly, overriding the PR title. When present, that value **is**
the title — checked directly, no `gh pr view` call needed (and none of its
fail-open/network-error paths apply).

## Configuration

| Env var | Default | Effect |
| --- | --- | --- |
| `CLAUDE_COMMIT_TITLE_MAX` | `50` | Override the maximum title length |

Setting `CLAUDE_COMMIT_TITLE_MAX=80` allows longer titles (e.g. for repos with
a 72-char convention) without disabling the hook.

## Response

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "Commit title too long: 82 chars (max 50).\nTitle: '...'\nShorten to ≤50 chars, or embed `# title-length:ack` to bypass."
  }
}
```

## Compound cascade advisory (issue #229)

When the ask fires on a compound Bash command containing a state-changing
step (e.g. `mkdir -p /tmp/log && git commit -m "$(cat /tmp/log/very-long-..."`),
the ask reason is suffixed with the shared
`_hook_utils.compound_cascade_hint` text. If the user denies the prompt, the
chained `mkdir`/redirect/download also did not run — retries must materialize
those files first.

## Opt-out marker

Embed `# title-length:ack` anywhere in the command to bypass the check for
known-intentional long titles (e.g. auto-generated merge commits handled by a
script):

```bash
git commit -m "Merge remote-tracking branch 'origin/main' into feature/long-name"  # title-length:ack
```

The same marker suppresses the squash-merge advisory (checked once, before
either path runs):

```bash
gh pr merge 42 --squash  # title-length:ack
```

## Parsing guarantees

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
  sibling hooks. A title that OPENS one is measured only when the raw command
  shows it came from a single-quoted run, where the shell substitutes nothing.
  The match is by value, so when the same text appears both single- and
  double-quoted (`echo '$(x)'; git commit -m "$(x)"`) neither run can be
  attributed to the title and the gate stays silent (issue #1036).
- **Literal newline inside a single quoted `-m` value bypasses the check.**
  `git commit -m "Title<newline>Body"` (where `<newline>` is an unescaped LF
  character inside the quoted string) is split by `_hook_utils.safe_tokenize`'s
  newline-aware preprocessor before shlex sees the opening quote, leaving an
  unmatched-quote fragment that gets dropped. Use `-m "title" -m "body"` or
  heredoc-assigned variables for multi-line commit messages — both extract
  the title correctly. This is a documented limitation of the shared
  tokenizer (see `_hook_utils.py` docstring), preserved to keep
  newline-separated multi-command detection intact for sibling hooks.

## Fail-open (squash-merge path)

`gh pr view` failure (auth error, timeout, non-zero exit, empty output) ->
silent pass, same fail-open contract as `gh-merge-worktree-precondition`'s
own `gh pr view` call. The advisory only fires on a positively-resolved,
over-limit title.

## Tests

```bash
bash tests/hooks/preflight-gate/test_commit_title_length_check.sh
```

Covers 61 cases: boundary (50 chars), under (49 chars), long via `-m` /
`--message` / `-m=value` / `--amend` / `-am`, Korean 51-code-point title,
Hub #1912 regression (82-char title), Merge/Revert skip, body-in-second-m
protection, chained command, `CLAUDE_COMMIT_TITLE_MAX` override (both
directions), `-F` file (short and long), `-F -` stdin pass-through, opt-out
marker, echo false-positive guard, non-Bash tool passthrough, malformed JSON
fail-open, plus regression coverage for `git -C <dir>` global flags,
attached-form `-m"value"`, `-S<keyid>` whitelist (must not be misparsed as
combined `-m`), and `-C <dir>` + relative `-F <file>` resolution including
stacked `-C` flags — **plus** the squash-merge path (issue #843): long/short
PR title via a faked `gh pr view`, `-s` short flag, `-t`/`--subject`
override (long and short, no `gh` call made), `gh pr view` error -> fail-open,
`-R owner/repo` global-flag forwarding, opt-out marker suppressing the
squash advisory, `gh api .../pulls/N/merge` over-limit with `merge_method=squash`
(resolved via `gh pr view`) and explicit `-f commit_title=...`, `--merge`/`--rebase`
(no `-s`) staying silent even with a long title, and an unrelated `gh pr view`
command staying silent.
