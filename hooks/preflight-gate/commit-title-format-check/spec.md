# PreToolUse Commit Title Format Check

Supported hosts: all

`hooks/preflight-gate/commit-title-format-check/impl.py` intercepts every AI-authored `git commit`,
`gh pr create`, and `gh issue create` Bash call and blocks (or warns in advisory
mode) when the title does not match the Conventional Commits format required by the
*Git Commit & Title Rules* ([`CONTRIBUTING.md` → Commit conventions](../../../CONTRIBUTING.md#commit-conventions);
[`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries)).

### Expected format

```
<type>[(<scope>)]: <description>
```

| Part          | Constraint                                                          |
| ------------- | ------------------------------------------------------------------- |
| `type`        | One of `feat\|fix\|docs\|style\|refactor\|test\|chore`              |
| `scope`       | Optional; lowercase alphanumeric + hyphens, enclosed in parentheses |
| `description` | Must start with a lowercase ASCII letter `[a-z]`                    |

Examples:

- `feat(auth): add token refresh`
- `fix: resolve null pointer`
- `docs(readme): update examples`

### What is blocked

| Command shape                                             | Action                                                                                  |
| --------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `git commit -m "bad title"`                               | block (exit 2)                                                                          |
| `git commit --message "bad title"`                        | block                                                                                   |
| `git commit -m="bad title"`                               | block                                                                                   |
| `git commit -am "bad title"`                              | block                                                                                   |
| `git commit --amend -m "bad title"`                       | block                                                                                   |
| `gh pr create --title "bad title"`                        | block                                                                                   |
| `gh pr create --title="bad title"`                        | block                                                                                   |
| `gh pr create -t "bad title"`                             | block                                                                                   |
| `gh issue create --title "bad title"`                     | block                                                                                   |
| `gh issue create -t "bad title"`                          | block                                                                                   |
| `Merge branch ...` / `Merge pull request ...`             | silent pass (whitelisted)                                                               |
| `Revert "..."` title                                      | silent pass (whitelisted)                                                               |
| `fixup! ...` title                                        | silent pass (whitelisted)                                                               |
| `squash! ...` title                                       | silent pass (whitelisted)                                                               |
| `gh pr create --title "release: Production Deploy (...)"` | silent pass (release PR convention — **`gh pr create` only**, capitalized desc allowed) |
| `git commit -m "release: ..."`                            | block (release exemption is `gh pr create` only)                                        |
| `gh issue create --title "release: ..."`                  | block (release exemption is `gh pr create` only)                                        |
| `git status`, `git push`, etc.                            | silent pass (not a commit or PR/issue create)                                           |
| `gh pr list`, `gh issue view`, etc.                       | silent pass (not create)                                                                |

### Invalid title examples (blocked)

| Title                 | Reason                                        |
| --------------------- | --------------------------------------------- |
| `updated stuff`       | No type prefix                                |
| `feat: Add something` | Description starts with uppercase `A`         |
| `feat: 한글로 시작`   | Description starts with a non-ASCII character |
| `feat scope: desc`    | Scope not enclosed in parentheses             |
| `<type>(scope): desc` | Unreplaced `<type>` placeholder               |
| `type(scope): desc`   | `type` not in the allowed types list          |

### Configuration

| Env var                             | Default                                                 | Effect                                                                     |
| ----------------------------------- | ------------------------------------------------------- | -------------------------------------------------------------------------- |
| `PRAXIS_COMMIT_TITLE_FORMAT_STRICT` | `1`                                                     | `1` → block (exit 2); `0` → advisory mode (exit 0, message to stderr only) |
| `PRAXIS_COMMIT_TITLE_ALLOWED_TYPES` | `feat,fix,docs,refactor,chore,test,perf,ci,build,style` | Comma-separated override for the allowed type list                         |

### Response (strict mode)

Writes to **stderr** using the standard `block_message.format_block` format
and exits with code 2 (deny):

```
⚠️ COMMIT-TITLE-FORMAT-CHECK blocked

Why: title does not match Conventional Commits format — Title: 'updated stuff'
     Expected: <type>[(<scope>)]: <lowercase-description>
     Types: feat|fix|docs|refactor|chore|test|perf|ci|build|style
     Whitelisted: Merge branch, Merge pull request, Revert "...", fixup!, squash!
Correct path: use: feat(scope): description  or  fix: short description
              (description must start with a lowercase letter)
Bypass (if truly needed): PRAXIS_COMMIT_TITLE_FORMAT_STRICT=0 to switch to advisory mode
Reference: CLAUDE.md → Git Commit & Title Rules
```

### Advisory mode (STRICT=0)

When `PRAXIS_COMMIT_TITLE_FORMAT_STRICT=0`, the hook emits the same diagnostic
message to `stderr` but exits 0, allowing the command to proceed.

### Compound cascade advisory (issue #229)

When the block fires on a compound Bash command containing a state-changing step
(e.g. `mkdir -p /tmp && git commit -m "bad title"`), the block reason is suffixed
with the shared `_hook_utils.compound_cascade_hint` text. If the user denies the
prompt, the chained `mkdir`/redirect/download also did not run — retries must
materialize those files first.

### Parsing guarantees

Inherits `safe_tokenize` / `iter_command_starts` / `strip_prefix` from
`_hook_utils.py` (same primitives as sibling hooks):

- Shell operators (`;`, `&&`, `||`, `|`) split command segments — a chained
  `git fetch && git commit -m "bad"` correctly reaches the `git commit` segment.
- Env prefixes (`GIT_AUTHOR_NAME=x git commit -m "title"`), wrapper commands
  (`sudo`, `env`), and shell control-flow keywords are peeled before matching.
- Quoted strings protect their contents — `echo "git commit -m 'fake'"` does
  not trigger the hook because `echo` is argv[0] of that segment.
- A title that OPENS a substitution (`$(`, `` ` ``) is graded only when the raw
  command shows it came from a single-quoted run, where the shell substitutes
  nothing. `-m "$(cat …)"` is unknowable at hook time and stays silent. The
  match is by value, so when the same text appears both single- and
  double-quoted (`echo '$(x)'; git commit -m "$(x)"`) neither run can be
  attributed to the title and the gate stays silent (issue #1036).
- Backslash-newline continuations are collapsed before tokenization —
  multi-line invocations like `git commit \\\n  -m "..."` parse correctly.
- Only the FIRST `-m` / `--message` value is checked (title); subsequent `-m`
  values are body paragraphs and are ignored.
- `git -C <dir>` global flags are stripped; relative `-F <file>` paths resolve
  against the `-C` base directory.
- `git commit --amend -m "bad"` is **NOT** exempt from format checking (only
  the whitelist prefixes are exempt — amend still requires valid format).

### Tests

```bash
bash tests/hooks/preflight-gate/test_commit_title_format_check.sh
```

Covers all enumerated surface cases: valid format passes, missing type, uppercase
description, Korean description char, unreplaced placeholder, scope without
parens, whitelisted merge/revert/fixup/squash, `release:` PR-only exemption
(`gh pr create` passes; commit + `gh issue create` still block), `gh pr create
--title` block, `gh issue create --title` block, advisory mode exit code, custom allowed-types,
`-m VALUE` / `-mMSG` / `--message=` extraction variants, non-commit bash
commands, malformed JSON fail-open.
