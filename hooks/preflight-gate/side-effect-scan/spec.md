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
| `wrapper-commit` | ask | commands from `PRAXIS_WRAPPER_COMMIT_CMDS` (e.g. `iceberg-schema migrate\|promote, omc ralph`); shipped default empty → category silent until configured (#1157) | A commit (or catalog write) made inside another process, where no `git commit` gate can see it |
| `git-push` | ask | `git push` | Remote published without intent |
| `gh-merge` | ask | `gh pr merge`, `gh pr create`, `gh workflow run` (including a leading global flag, e.g. `gh --repo o/r pr merge`, `gh -R o/r workflow run`) | Unintended PR state change or workflow dispatch |
| `kubectl-apply` | ask | `kubectl apply`, `kubectl delete`, `kubectl replace`, `kubectl patch` | Shared cluster mutation |

### Tiers — why `git-commit` advises instead of asking (issue #874)

The observation: one 2026-07-27 session (`5d46110f`) spent **23 of its 37 total
ask prompts** on this single hook — 62%. An approval gate that becomes habitual
stops functioning as a gate, so the volume itself is the defect.

`git-commit` is the category that absorbs the reduction, and the ground is
reversibility alone. Issue #874 argued it on two halves; issue #1153 removed
the second ("sibling hooks already cover this argv") from the argument, because
that half is a property of the installation rather than of the command — see
[The sibling gates are context, not ground](#the-sibling-gates-are-context-not-ground)
below.

**What makes a `git-commit`-category command reversible, concretely.** The
category covers `git commit`, `git merge`, `git rebase`, `git cherry-pick` and
`git revert`. None of them moves a remote ref: no network call is made, and
nothing outside this machine can observe the result. Git records the
pre-command HEAD before each of them — `ORIG_HEAD` for merge / rebase /
cherry-pick / revert, `HEAD@{1}` in the reflog for a plain commit — and
`gc.reflogExpire` keeps reflog entries for 90 days by default (`man
git-config`, verified on git 2.50.1). So `git reset --hard ORIG_HEAD` (merge /
rebase / cherry-pick / revert) or `git reset --hard HEAD@{1}` (a plain commit)
puts the previous state back from the same shell, with no second party to
notify. `git commit --amend` is not on that list: it replaces the commit rather
than restoring the state before it, so it edits the mistake instead of undoing
it. The claim is checkable in
one command: `git reflog -n 2` after any of these prints the state to return
to.

**What the recovery does and does not restore.** It is a *ref* recovery, and
three conditions bound it — state them rather than let "reversible" be read as
unconditional:

- **The tracked working tree and index move too.** Merge / rebase /
  cherry-pick / revert update them, not only `.git` — `git-merge(1)`: "your
  HEAD, index, and working tree are updated to it."
- **The undo itself can destroy work.** `git-reset(1)` on `--hard`: "Resets
  the index and working tree. Any changes to tracked files in the working tree
  since <commit> are discarded." Uncommitted work is not in the reflog, so the
  recipe above restores the ref while losing it.
- **A repository can attach arbitrary side effects.** `pre-commit`,
  `post-commit` and `post-merge` (`githooks(5)`) run whatever that checkout
  installs, including a network call — so "no network call is made" is a
  property of plain git, not a guarantee about every repository.

None of the three reaches publication, which is why the tier holds: the ADVISE
ground is *no remote ref moved and no second party has seen it*, never *nothing
outside `.git` changed*. A reader who needs the stronger property will not find
it here.

**Where reversibility stops is publication, and that boundary carries its own
ask.** Once the commit leaves the machine another party can already be reading
it, and no local undo reaches what they have seen. That transition is
`git push` — a *separate* category in the table above, held at `ask`. Because
`side-effect-scan` carries no `hosts` key, that ask ships on every platform
that installs hooks, and `push-remote-ref-verify`
(`hooks/advisory-nudge/push-remote-ref-verify/spec.md`, `PostToolUse(Bash)`,
likewise unrestricted by host) checks the far side afterwards. A mixed match
keeps the ask and names every matched category (see [Response](#response)), so
`git commit -am x && git push` was never quieted by the demotion.

**No other category can make this argument.** `git push`, `gh pr merge` /
`gh pr create` / `gh workflow run`, `kubectl apply` and the `wrapper-commit`
CLIs all publish to state another party can already be reading, so there is no
local undo to point at. That asymmetry is the whole tier split, and it holds on
every host because it is a property of the commands, not of which hooks happen
to be installed beside them.

#### The sibling gates are context, not ground

The enumeration below is measured, still true, and still worth checking — but
the ADVISE grade does not rest on it. A grade that ships to every host cannot
stand on a premise that varies per host, and this one does.

Eight sibling `PreToolUse(Bash)` hooks gate a `git commit` argv on their own —
derived from the `"gates": ["git-commit"]` field each carries in
`hooks/manifest.json`, and each verified by reading its detector to key on the
`commit` subcommand:

| Sibling hook | Hosts | What it gates |
| -------------- | ------- | --------------- |
| `block-commit-without-codex-review` | `claude` | commit before the review step |
| `block-rename-sweep-survivors` | `claude` | a rename sweep with surviving occurrences |
| `commit-decomposition-advisory` | `claude` | oversized single commit |
| `commit-message-paren-check` | all | a message line release-please's parser rejects |
| `commit-title-format-check` | all | Conventional Commits title format |
| `commit-title-length-check` | all | title length |
| `pre-commit-staged-file-enumeration` | `claude` | staging without enumerating files |
| `verify-commit-flag-override` | all | `-n` / `--no-verify` flag override |

Four of the eight siblings are the checklist `verify-commit-flag-override`
already prints on its own deny (issue #941). By contrast **no** sibling hook
gates `kubectl apply` at all.

The manifest field is the single source of truth for that list;
`scripts/check-sibling-commit-gates.py` re-derives it on every
`scripts/run-tests.sh` run and fails on a name, a `hosts` cell or a count
that drifted from it in either direction (issue #1127). The list is
hand-curated — membership means "this hook's detector keys on the `commit`
subcommand", which no string scan can decide (`pipefail-advisory` names the
subcommand but only fires on a pipeline; `branch-name-check` mentions
commits while gating branch creation) — but once curated it is copied
nowhere by hand again.

#### The sibling set is per-host — which is why it is context

`side-effect-scan` carries no `hosts` key, so it, and the ADVISE tier with it,
ships to every platform that installs hooks. The `Hosts` column above is not
decoration: `build-plugin-manifests.py` applies that whitelist when it writes
each platform's `hooks.json`, and the generated entry point is
`_dispatch.sh PreToolUse Bash <host>`, so
`_dispatch.group_members("PreToolUse", "Bash", host)` re-applies it at runtime.
The sibling set is therefore a per-host set:

| Host | Sibling commit gates | Also in the deny checklist |
| ------ | ---------------------- | ---------------------------- |
| `claude` | 8 | 4 |
| `codex` | 4 | 2 |
| `cursor` | 4 | 2 |

Both columns are re-derived per host by `scripts/check-sibling-commit-gates.py`
— every hook-installing platform must have a row, and a row for a platform that
installs no hooks is itself drift.

Outside `claude` the survivors are `commit-title-format-check`,
`commit-title-length-check` and `verify-commit-flag-override`. Two of those gate
only the *shape* of the commit title; the third fires only when a `-n` /
`--no-verify`-class override is present. None of them asks whether the commit
was intended at all. The siblings that carry that weight — the codex-review
gate, the rename-sweep survivor gate, the staged-file enumeration and the
decomposition advisory — are `claude`-only, as the `Hosts` column records. The
same whitelist also thins `verify-commit-flag-override`'s own printed deny
checklist: some of its rows name hooks the host does not install, which is what
the `Also in the deny checklist` column counts per host.

That gap is what disqualified the coverage half as a justification
(issue #1153). The measurement it was argued alongside — 62% of one session's asks —
was taken on `claude` too, so both parts of the retired half describe the
author's own host. Nothing above changes the grade: `git-commit` stays at
ADVISE on every host, because the reversibility ground it now rests on is a
property of `git commit` / `merge` / `rebase` / `cherry-pick` / `revert`
themselves and does not vary with the installation. **The runtime is
unchanged** — issue #1153 rewrote the stated reason, not the tier.

What the enumeration is still for: drift detection. Issue #1127's checker
fails when a prose surface claims coverage the manifest does not ship, on any
host — so the tables above stay an accurate picture of what each platform
installs, without being the reason `git-commit` advises.

**`wrapper-commit` is a deliberate narrowing.** The wrapper CLIs used to
carry the `git-commit` label; the demotion does not follow them down, because
the reversibility ground fails for them. What a wrapper process does inside
itself is not readable from the argv the hook sees: `iceberg-schema promote`
writes a shared catalog, not this checkout's `.git`, so there is no
`git reset` that undoes it. The eight sibling gates match a literal
`git commit` argv and never see it either. They keep asking, under their own
category name.

**Which wrapper CLIs exist is installer-specific**, so since #1157 the
pattern list comes from `PRAXIS_WRAPPER_COMMIT_CMDS`: comma-separated
`<cmd> <sub>[|<sub>...]` entries (e.g.
`iceberg-schema migrate|promote, omc ralph` — the author-toolchain names
that used to ship hardcoded here). Malformed entries are dropped silently —
a typo must not widen into a broad match. The shipped default is empty, so
the category is inert until configured.

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
