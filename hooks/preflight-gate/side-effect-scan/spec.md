# PreToolUse Side-Effect Scan

Supported hosts: all

`hooks/preflight-gate/side-effect-scan/impl.py` intercepts every Bash tool call and flags commands
with collateral side effects before the agent runs them. Goal: prevent the
"primary-effect only" blind spot that has caused unintended merges, unintended
prod deploys, and stray auto-commits from CLIs that write to git internally.

### Detection categories

| Category | Tier | Trigger examples | Risk |
| ---------- | ------ | ------------------ | ------ |
| `git-commit` | **advise** | `git commit`, `git merge`, `git rebase`, `git cherry-pick`, `git revert` | Commits to the wrong branch or under the wrong author |
| `wrapper-commit` | ask | `iceberg-schema migrate`, `iceberg-schema promote`, `omc ralph` | A commit (or catalog write) made inside another process, where no `git commit` gate can see it |
| `git-push` | ask | `git push` | Remote published without intent |
| `gh-merge` | ask | `gh pr merge`, `gh pr create`, `gh workflow run` (including a leading global flag, e.g. `gh --repo o/r pr merge`, `gh -R o/r workflow run`) | Unintended PR state change or workflow dispatch |
| `kubectl-apply` | ask | `kubectl apply`, `kubectl delete`, `kubectl replace`, `kubectl patch` | Shared cluster mutation |

### Tiers — why `git-commit` advises instead of asking (issue #874)

The observation: one 2026-07-27 session (`5d46110f`) spent **23 of its 37 total
ask prompts** on this single hook — 62%. An approval gate that becomes habitual
stops functioning as a gate, so the volume itself is the defect.

`git-commit` is the category that absorbs the reduction, and the only one:

1. **It is the only category whose action is local-only and fully reversible.**
   `git push`, `gh pr merge` / `gh pr create` / `gh workflow run` and
   `kubectl apply` all publish to state another party can already be reading.
   A commit is recoverable with `git reset` / `git commit --amend` from the
   same shell, before anything leaves the machine.
2. **It is the only category already covered in depth.** Seven sibling
   `PreToolUse(Bash)` hooks gate a `git commit` argv on their own — enumerated
   from `hooks/manifest.json` and each verified to key on the `commit`
   subcommand:

   | Sibling hook | What it gates |
   | -------------- | --------------- |
   | `block-commit-without-codex-review` | commit before the review step |
   | `block-sciomc-finding-commit` | committing a findings artifact |
   | `commit-title-format-check` | Conventional Commits title format |
   | `commit-title-length-check` | title length |
   | `verify-commit-flag-override` | `-n` / `--no-verify` flag override |
   | `commit-decomposition-advisory` | oversized single commit |
   | `pre-commit-staged-file-enumeration` | staging without enumerating files |

   Five of the seven are the checklist `verify-commit-flag-override` already
   prints on its own deny (issue #941). By contrast **no** sibling hook gates
   `kubectl apply` at all.

**`wrapper-commit` is a deliberate narrowing.** `iceberg-schema
migrate|promote` and `omc ralph` used to carry the `git-commit` label;
issue #874's demotion does not follow them down, because both halves of the
rationale fail for them. The seven sibling gates match a literal `git commit`
argv, so a commit made *inside* a wrapper process is invisible to every one of
them, and `iceberg-schema promote` is a catalog operation rather than a local
one. They keep asking, under their own category name.

### Response

**ASK tier** — any matched category at tier `ask`:

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

A *mixed* match keeps the ask **and every matched category in the reason** —
`git commit -am x && git push` is unchanged from its pre-#874 text, both
`[git-commit]` and `[git-push]`. The demotion can therefore never quiet a
command that also touches shared state.

**ADVISE tier** — the match consists solely of `advise`-tier categories:

```text
stderr: "[side-effect-scan] [git-commit] local git state mutation — … 의도한 실행이면 …"
exit 0, no stdout
```

Two properties of that channel are load-bearing and must not be "simplified":

- `_dispatch.run_group` forwards every member's **stderr** unconditionally
  (`hooks/_lib/_dispatch.py:196-199`) but forwards member *stdout* only when it
  carries an ask/deny marker (`:207-218`). stdout is not an option here.
- `_fire_ledger.classify_decision` derives `advise` from non-empty stderr
  (`hooks/_lib/_fire_ledger.py:118-119`). Writing nothing would record the fire
  as `pass` and make the demotion invisible to the ledger this tier decision
  will be re-scored from.

The advisory keeps the PROD prefix and the `# side-effect:ack` pointer (the
marker still short-circuits the hook) but **omits the compound cascade hint** —
that text describes what a *denied* decision does to the rest of a chain, and
an advisory cannot be denied.

### Prod emphasis

If any token on the command line matches `prod`, `production`,
`--env prod`/`--environment=prod`, the reason is prefixed with a
`⚠️  PROD scope` warning so the reviewer treats it with extra care. The prefix
rides whichever channel the tier selected — a prod-scoped `git commit` still
carries it, on stderr.

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
- Heredoc bodies are blanked before that split (issue #985): the body is data,
  so `git commit -m "$(cat <<'EOF' … EOF)"` no longer surfaces the categories
  its *message text* happens to name. The operator line and everything after
  the terminator are still scanned.
- `--help` / `-h` invocations surface nothing — `gh pr merge --help` prints
  usage and triggers no remote action (issue #985). A help flag sitting in a
  value position (`--subject -h`) is a value, not a help request.
- Newlines in the raw command are treated as command separators so multi-line
  Bash blocks (`echo prep\ngit push origin main` across two lines) get the
  second line scanned as a new segment.
- Subshells (`$(...)`) are opaque to shlex and **not** decomposed — an
  acknowledged limitation; rely on the author to use `# side-effect:ack`
  explicitly if they're running side-effecting code through `$()`.

### Tests

`tests/hooks/preflight-gate/test_side_effect_scan.sh` covers 82 cases —
positive detection across all categories, the #874 tier boundary in both
directions (git verbs advise; `wrapper-commit` / push / gh / kubectl still ask;
a mixed commit+push match stays an ask naming both categories; the advisory
carries no cascade hint), prod emphasis on both channels, opt-out, shlex-aware
evasions, operator-adjacent one-liners, env/sudo prefix peeling, wrapper option
flags (long/short/equals/bare), nested wrappers, shell control-flow keywords,
newline-separated multi-line commands, GNU `time -f FORMAT` / `-o FILE`
arg-taking flags, non-Bash passthrough, malformed input.

`pass` asserts silence on **both** streams, not just the absence of an ask —
otherwise an advisory leaking onto `git status` would go unnoticed now that the
hook writes to stderr at all. Run before editing the hook:

```bash
bash tests/hooks/preflight-gate/test_side_effect_scan.sh
```
