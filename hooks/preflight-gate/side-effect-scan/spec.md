# PreToolUse Side-Effect Scan

Supported hosts: all

`hooks/preflight-gate/side-effect-scan/impl.py` intercepts every Bash tool call and flags commands
with collateral side effects before the agent runs them. Goal: prevent the
"primary-effect only" blind spot that has caused unintended merges, unintended
prod deploys, and stray auto-commits from CLIs that write to git internally.

### Detection categories

| Category | Trigger examples | Risk |
| ---------- | ------------------ | ------ |
| `git-commit` | `git commit`, `git merge`, `git rebase`, `git cherry-pick`, `git revert`, `iceberg-schema migrate`, `iceberg-schema promote`, `omc ralph` | Commits to the wrong branch or under the wrong author |
| `git-push` | `git push` | Remote published without intent |
| `gh-merge` | `gh pr merge`, `gh pr create`, `gh workflow run` (including a leading global flag, e.g. `gh --repo o/r pr merge`, `gh -R o/r workflow run`) | Unintended PR state change or workflow dispatch |
| `kubectl-apply` | `kubectl apply`, `kubectl delete`, `kubectl replace`, `kubectl patch` | Shared cluster mutation |

### Response

When any category matches, the hook emits:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "[category] reason..."
  }
}
```

Claude Code surfaces this as a permission prompt so the user can confirm or
redirect before the command executes.

### Prod emphasis

If any token on the command line matches `prod`, `production`,
`--env prod`/`--environment=prod`, the reason is prefixed with a
`⚠️  PROD scope` warning so the reviewer treats it with extra care.

### Compound cascade advisory (issue #229)

When the `ask` is raised on a compound Bash command (`&&`, `||`, `;`, `|`,
newline) that also contains a state-changing step (`mkdir`, `tee`, `cp`/`mv`/
`rm`/`touch`, `> file`, `<<EOF > file`, `curl -o`, `wget -O`), the ask reason
is suffixed with the shared cascade advisory from
`_hook_utils.compound_cascade_hint`. If the user denies the prompt, bash never
runs ANY part of the command — including the side-effects the agent might have
assumed already executed. The advisory reminds the caller to materialize files
with the Write tool first, then issue the side-effect command separately.

Single-command asks do NOT receive the suffix — there is no cascade to warn
about when the rejection covers exactly one effect.

### Opt-out marker

Known-intentional invocations can bypass the hook by embedding the literal
marker anywhere in the command:

```bash
git push origin main  # side-effect:ack
```

Use sparingly — the marker is a deliberate assertion that the side effect is
exactly what the current step requires.

### Parsing guarantees

Commands are tokenized with `shlex.shlex(..., posix=True, punctuation_chars=";|&")`
(not regex), so:

- Quotes (`"`/`'`) protect literal strings from being parsed as commands.
- Shell operators (`;`, `|`, `&`, `&&`, `||`) are always emitted as standalone
  tokens, even when typed without surrounding whitespace — `git push&&echo ok`
  and `echo x|git push origin main` both split cleanly and each segment is
  scanned for command starts.
- Env prefixes (`FOO=1 git push`), wrapper commands (`env`, `sudo`, `nice`,
  `time`, `stdbuf`, `ionice`), and their option flags are peeled from argv
  before matching — including both `--user admin` (separate value) and
  `--user=admin` (embedded), plus bare flags like `env -i`, `sudo -E`,
  `stdbuf -oL`. Nested wrappers (`sudo -E env GIT_TRACE=1 git push`) are
  unwrapped iteratively.
- Shell control-flow keywords (`if`, `then`, `elif`, `else`, `fi`, `while`,
  `until`, `do`, `done`, `for`, `case`, `esac`, `in`, `function`, `!`, `{`,
  `}`) are peeled from the start of each segment so `if true; then git push`,
  `for x in 1; do kubectl apply`, and `if git push; then ...` all reach the
  real executable.
- Newlines in the raw command are treated as command separators so multi-line
  Bash blocks (`echo prep\ngit push origin main` across two lines) get the
  second line scanned as a new segment.
- Subshells (`$(...)`) are opaque to shlex and **not** decomposed — an
  acknowledged limitation; rely on the author to use `# side-effect:ack`
  explicitly if they're running side-effecting code through `$()`.

### Tests

`tests/test_side_effect_scan.sh` covers 54 cases — positive detection across
all categories, prod emphasis, opt-out, shlex-aware evasions,
operator-adjacent one-liners, env/sudo prefix peeling, wrapper option flags
(long/short/equals/bare), nested wrappers, shell control-flow keywords,
newline-separated multi-line commands, GNU `time -f FORMAT` / `-o FILE`
arg-taking flags, non-Bash passthrough, malformed input. Run before editing
the hook:

```bash
./tests/test_side_effect_scan.sh
```
