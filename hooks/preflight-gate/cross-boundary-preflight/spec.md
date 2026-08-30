# PreToolUse Cross-Boundary Pre-Flight

Supported hosts: all

Reference: [Autonomy vs Convention — ETHOS.md](../../../ETHOS.md#autonomy-vs-convention)

`hooks/preflight-gate/cross-boundary-preflight/impl.py` intercepts every Bash tool call and
fires on three cross-boundary patterns before the command executes.

### Why this exists

Five documented session failures share one meta-pattern: a rule existed in
context but had no execution-time retrieval trigger at the action boundary.
Memory entries for each pattern were written after each incident, but the
same violations recurred on the next relevant session. This hook replaces
the memo with a structural gate that fires at the command boundary (praxis
issue #199).

The three patterns covered:

| Pattern               | Trigger                                                              | Action                                                                                                         |
| --------------------- | -------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `HEREDOC_BODY`        | `<<` token in same segment as `gh pr/issue create`                   | **Hard block** (exit 2) — suggests `--body-file`                                                               |
| `CROSS_REPO_WRITE`    | `--repo/-R` flag in `gh pr/issue create/comment/edit` (any owner)    | **Ask** — surfaces four-point checklist                                                                        |
| `IMPLICIT_REPO_WRITE` | same subcommands with **no** `--repo/-R`, target resolvable for `gh` | **Ask** — same checklist, naming the effective repo and the selector that chose it; silent if nothing resolves |

### What is blocked / asked

The hook uses `safe_tokenize → iter_command_starts → strip_prefix` (same
pipeline as sibling hooks) so only live `gh` invocations match. Pattern
references inside quoted arguments, echo/grep/commit bodies, or preceding
variable assignments are transparent pass-throughs.

#### HEREDOC_BODY — hard block (exit 2)

| Command                                                      | Action                                  |
| ------------------------------------------------------------ | --------------------------------------- |
| `gh issue create --title "t" <<EOF`                          | **BLOCKED**                             |
| `gh pr create --title "t" <<'EOF'`                           | **BLOCKED**                             |
| `gh pr create --title "t" <<-EOF`                            | **BLOCKED**                             |
| `gh --repo x issue create --title "t" <<EOF`                 | **BLOCKED**                             |
| `BODY=$(cat <<EOF\n...\nEOF\n)\ngh pr create --body "$BODY"` | **PASS** — heredoc in different segment |
| `cat <<EOF > /tmp/f.txt`                                     | **PASS** — non-gh command               |

Why heredoc is blocked: `shlex` tokenization does not read heredoc content,
so the `block-pr-without-caller-evidence` hook and `external-write-falsify-check`
hook (opt-in, off by default) both see an empty body. Caller-chain evidence
and falsification checks are bypassed silently.

Correct pattern: `Write tool → /tmp/body.md` then `--body-file /tmp/body.md`.

#### CROSS_REPO_WRITE — ask (permissionDecision: "ask")

| Command                                                            | Action                              |
| ------------------------------------------------------------------ | ----------------------------------- |
| `gh pr create --repo owner/repo --title "t" --body-file /tmp/b.md` | **ASK**                             |
| `gh issue create --repo owner/repo --title "t"`                    | **ASK**                             |
| `gh issue comment 42 --repo owner/repo --body "..."`               | **ASK**                             |
| `gh -R owner/repo pr create --title "t" --body-file /tmp/b.md`     | **ASK**                             |
| `gh issue list --repo owner/repo`                                  | **PASS** — read-only subcommand     |
| `gh pr list --repo owner/repo`                                     | **PASS** — read-only subcommand     |
| `gh issue create --repo <own-org>/repo --title "t"`                | **ASK** — ownership is no exemption |
| `gh pr create --repo x --title "t" # cross-boundary:ack`           | **PASS** — opt-out                  |

The checklist surfaced for `pr create` (four points):

1. **Per-action authorization gate** — explicit approval for THIS specific
   action, for **every** `--repo` target including a repo the user or their
   own org owns
2. **Caller chain verified** — PR body must contain `Caller chain verified: <source>` (pr create only)
3. **Body delivery format** — `--body-file`, no heredoc
4. **Language & content rules** — English only, no internal identifiers

The checklist for `issue create / comment / edit` skips item ② (no caller-chain
requirement on issues).

#### Ownership is not an exemption (issue #993)

The ASK has always fired on every `--repo` write, but item ① was labelled
"§External-repo write" only — read as "external repos only", which let own-org
writes be acked as out of scope. Item ① now states the rule the checklist
actually enforces: a public repo owned by the user or their own org is a
cross-boundary write and needs the same per-action prior approval as a
third-party repo. The verdict and the wording now agree, and they agree with
retrospect Stage 2.5 Gate-4, where own-org membership likewise stopped being
an exemption — there the write is unescalated only when the backing repo is
own-org **and** declared `repo_visibility: private|internal`. This hook cannot
observe visibility from the command line, so it asks in every case; a repo
confirmed own-org-and-private is what the `# cross-boundary:ack` marker is
for.

#### IMPLICIT_REPO_WRITE — ask (permissionDecision: "ask")

Same subcommand set as `CROSS_REPO_WRITE`, no `--repo`/`-R` flag. The target
repo is resolved from the checkout the command runs in and named in the
checklist header, so the user can see what they are approving.

| Command                                                       | Action                                                    |
| ------------------------------------------------------------- | --------------------------------------------------------- |
| `gh pr create --title "t" --body "Caller chain verified: ok"` | **ASK** — target resolved from the checkout               |
| `gh issue create --title "t"`                                 | **ASK**                                                   |
| `gh issue comment 42 --body "..."`                            | **ASK**                                                   |
| `gh issue edit 7 --title "updated"`                           | **ASK**                                                   |
| `gh pr comment 5 --body "..."`                                | **ASK**                                                   |
| `gh pr edit 5 --title "updated"`                              | **ASK**                                                   |
| `cd <worktree> && gh issue create --title "t"`                | **ASK** — resolved in the `cd` target                     |
| `GH_REPO=other/repo gh issue create --title "t"`              | **ASK** — names `other/repo`, never the checkout's origin |
| `gh issue create --title "t"` with `GH_REPO` exported         | **ASK** — same, read from the hook's environment          |
| `GH_REPO= gh issue create --title "t"` (explicitly cleared)   | **ASK** — falls back to the checkout, as gh does          |
| `gh issue create --title "t"` after `gh repo set-default X`   | **ASK** — names `X`, never `origin`                       |
| `gh issue create --title "t"` in a fork with an `upstream`    | **ASK** — names `upstream`, which gh prefers              |
| `gh issue create --title "t"` with an unparseable selector    | **ASK** — target named `UNRESOLVED`                       |
| `gh issue create --title "t"` in a non-checkout cwd           | **PASS** — target unresolvable, fail-open                 |
| `gh issue create --title "t"` with no remotes at all          | **PASS** — target unresolvable, fail-open                 |
| `gh issue list --state open`                                  | **PASS** — read-only subcommand                           |
| `gh issue create --title "t" # cross-boundary:ack`            | **PASS** — opt-out                                        |
| `gh issue create --title "t" <<EOF`                           | **BLOCKED** — heredoc arm runs ahead of this one          |

##### Resolving the effective target (issue #1149)

`origin` is not `gh`'s answer to "which repo is this repo-less write for",
and treating it as the answer is an authorization defect rather than an
imprecision: the checklist would name one repo while the write lands in
another, so the user approves a target they were never shown (CodeRabbit
CWE-863 / Incorrect Authorization on PR #1149 — the same class as the two
defects this arm has already been corrected for).

The hook therefore mirrors `gh`'s own precedence, read out of `cli/cli`:

| #   | Selector                   | Where `gh` reads it                                            | Where the hook reads it                                                               |
| --- | -------------------------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| 1   | `--repo` / `-R`            | `cmdutil.EnableRepoOverride`                                   | the command itself — this is the `CROSS_REPO_WRITE` arm                               |
| 2   | `GH_REPO`                  | `cmdutil.OverrideBaseRepoFunc` → `os.Getenv("GH_REPO")`        | the `GH_REPO=` assignment prefixed to this segment, else the hook process environment |
| 3   | `gh repo set-default`      | `Remotes.ResolvedRemote()` over `remote.<name>.gh-resolved`    | one local `git config` read of that same key                                          |
| 4   | first remote in gh's order | `context.remoteNameSortScore` — upstream, github, origin, rest | the same ordering over the remotes in the checkout's config                           |

Selector 3's value is `base` when the chosen repo *is* that remote's own
repo, and a literal `OWNER/REPO` otherwise — the two shapes
`gh repo set-default` writes (`setdefault.go`) and `displayRemoteRepoName`
reads back. Both are honoured.

Selector 4 is why "resolve `origin`" was wrong even with no environment
variable and no default set: in a fork checkout carrying an `upstream`
remote, `gh` scores `upstream` above `origin` and writes there.

When a selector is **present but unparseable** — a `GH_REPO` that is not a
repository name, a `gh-resolved` value this hook cannot read — the checklist
says `UNRESOLVED` and names no repo at all. It never falls back to `origin`:
a selector that exists means a target exists, and the one thing the hook must
not do is name the repo that selector overrode.

Resolution stays **local only**: a single
`git -C <cwd> config --local --get-regexp '^remote\..*\.(url|gh-resolved)$'`
with a 2s timeout, whose output supplies every remote URL *and* every
`gh-resolved` marker at once. URLs are parsed to `owner/repo` by the same
shapes the sibling `pre-gh-pr-create-dedup-gate` accepts (widened to GitHub
Enterprise hosts). No `gh api`, no `gh repo view`, no `gh repo set-default
--view`, no network call of any kind — the hook's manifest timeout is 5
seconds for the whole process, and shelling out to `gh` from the hook that
gates `gh` invites recursion besides.

One exec, and one per *command*, not per segment: the read is memoized by
directory, so `gh issue create … && gh issue create …` still spawns `git`
exactly once. `--local` is deliberate on both counts — it exits 128 outside
a repository (keeping "not a checkout" distinguishable from "checkout with no
remotes") and it cannot let a stray `remote.*` in `~/.gitconfig` name a repo
for a directory that is not a checkout. A `GH_REPO` hit short-circuits ahead
of the read, so that path spawns nothing at all.

The working directory comes from the PreToolUse payload's `cwd` field, with
an `os.getcwd()` fallback — the same read every sibling that needs a
directory performs (`gh-label-verify`, `anchor-comment-gate`,
`path-probe-gate`). A leading bare `cd <path>` segment updates it before the
later segments are inspected, so `cd <worktree> && gh issue create ...`
resolves in the worktree rather than in the session's cwd. `GH_REPO`
outranks the working directory entirely, so it also outranks a `cd` this
hook cannot model (below).

#### Why the repo-less path is gated too (issue #1148)

Before #1148 the ask keyed on the `--repo`/`-R` flag alone. In one and the
same checkout, `gh issue create --repo devseunggwan/praxis --title "t"`
asked and `gh issue create --title "t"` was silent — same write, same target
repo, different treatment decided entirely by flag style. That asymmetry was
spec'd, not accidental: this document previously listed
`gh pr create --title "t" --body "Caller chain verified: ok"` as **PASS** —
no `--repo`.

The policy #993/#1024 settled leaves no room for it. Per-action approval is
owed to a public-repo write *even inside your own org*; "did not pass the
flag" is not one of the exemptions, because omitting `--repo` does not make
the write local — `gh` simply infers the target from the checkout and writes
to the same remote surface. The justification this document already gives for
asking on **every** `--repo` write applies verbatim: the hook cannot observe
visibility from the command line, so it asks in every case, and a repo
confirmed own-org-and-private is what the `# cross-boundary:ack` marker is
for. The repo-less arm therefore covers the whole of `GH_WRITE_SUBCOMMANDS`,
exactly as the `--repo` arm does — narrowing it to `create` would have traded
one arbitrary asymmetry for another.

The fail-open condition is the one place the two arms differ, and it is
forced by what each arm can know. The `--repo` arm reads its target out of
the command; the repo-less arm has to resolve it. When **nothing** resolves —
cwd is not a git checkout, no remotes at all, git binary missing, subprocess
error, timeout, or a remote URL that yields no `owner/repo` slug — the hook
exits 0 **silently**, per its standing fail-open contract. Silence beats
naming the wrong repo in an approval prompt.

Note where that stops and `UNRESOLVED` starts: "nothing resolves" is silence,
"something resolves but cannot be read" is an ask. The difference is whether
a target is known to exist. No remotes means `gh` has nothing to write to
either; a `GH_REPO` full of junk means `gh` has a target and only this hook
cannot name it — the one case where an approval prompt naming no repo beats
both silence and a guess.

##### `--help` is not a write (both arms)

A segment carrying `--help` / `-h` never reaches either ask arm:
`_has_help_flag(seg)` returns `True` and the loop moves on to the next
segment. `gh` prints usage and exits without touching a remote, so gating it
surfaces an approval prompt for a question.

The exclusion is a single check placed between Check 1 and Check 2, so it
covers both arms at once. That matters because the defect is older than the
repo-less arm: `gh pr create --repo o/r --help` has asked since before
issue #1148, and adding a narrower exclusion to only the new arm would
rebuild the flag-style asymmetry that issue removed.

Two properties of *where* and *how* it is checked are load-bearing:

- **It runs after the heredoc hard block, never before it.**
  `_gh_write_subcommand` deliberately does **not** filter usage queries; it
  answers only "is this segment a gh write subcommand", and Check 1 depends
  on it staying that broad. Were the exclusion moved into the detector,
  `gh issue create --help <<EOF` would stop being a write subcommand at all
  and the heredoc body would slip past a block that exists precisely because
  heredoc bodies are invisible to the caller-chain and falsification hooks.
  The ordering makes `--help` unusable as a smuggling flag.
- **It keys on the token ROLE, not the text.** `_has_help_flag` matches only
  tokens the role API typed as `FLAG`. Matching text anywhere in the segment
  would let a flag *value* disable the gate: `--title "-h"` is a
  `FLAG_VALUE`, and a title of `-h` would otherwise silence an ask on a
  `--repo victim/repo` write.

The sibling `pre-gh-pr-create-dedup-gate` puts the same exclusion inside its
detector instead (`_is_pr_create`, `impl.py:98`). It can: it has no heredoc
hard block whose breadth the detector must preserve.

| Command | Action |
| ------- | ------ |
| `gh pr create --help` | **PASS** — usage query |
| `gh issue create -h` | **PASS** — usage query |
| `gh pr create --repo owner/repo --help` | **PASS** — usage query (was ASK before #1148) |
| `gh issue create --title "help the parser"` | **ASK** — the exclusion keys on the flag, not the word |

##### Known limitations

- **An unmodeled `cd` asks with an unresolved target rather than guessing.**
  A `cd` needing shell expansion (`$VAR`, `~`, globs), a bare `cd`, and a
  subshell `(cd x && gh …)` all leave the destination unknowable here. The
  hook does not fall back to the outer `cwd` for them: the repo-less arm asks
  with the target named `UNRESOLVED`, because resolving from a directory the
  write may never run in authorizes it against the wrong repo — and when that
  outer directory is not a checkout at all, the old fallback went silent
  entirely, which is a fail-open on an authorization decision. The cost is a
  prompt on `cd "$WORKTREE" && gh issue create …`, the dominant idiom in this
  repo's own worktree skills; that is the intended trade. `GH_REPO` is the
  exception: it fixes the target without reference to any checkout, so when it
  is set the destination of an unmodeled `cd` cannot change the answer and the
  ask names the real repo instead of `UNRESOLVED`. The `UNRESOLVED` ask uses
  the repo-less header (`gh issue create` (no --repo flag)), not the flag
  header — the latter renders the target inside the quoted command and so
  printed `gh issue create --repo UNRESOLVED — …`, quoting a flag the command
  never carried.
- **Why a subshell cannot be modeled.** `(cd <worktree> && gh issue create ...)`
  tokenizes its command word as `(cd`, and a subshell's directory change does
  not persist past `)` — but a `gh` call *inside* the same subshell does run
  under it. Telling those apart needs per-subshell cwd state the segment
  machinery does not carry, so the form is classified unmodelable and takes
  the `UNRESOLVED` ask above. `cd <literal> && ...` outside a subshell is
  still tracked normally.
- **A `cd` whose target does not exist is not followed.** `cd /nope ; gh
  issue create …` leaves bash in the original checkout, so following the
  target blindly would resolve `origin` somewhere nonexistent and silence the
  gate on a write that really does land here. The directory is stat-checked
  and the move is skipped when it is absent.
- **Non-GitHub remotes are unresolvable** and therefore silent — a local-path
  or non-GitHub remote yields no `owner/repo` slug. The `github`
  marker is matched in the **host** only: `https://gitlab.com/github/tools/repo.git`,
  a clone under a directory named `github`, and `file:///srv/git/github-mirror/o/r.git`
  all resolve to nothing rather than to a plausible-looking slug.
- **`GH_REPO` is read from the hook's own process environment, not from the
  payload.** The PreToolUse payload carries `tool_name`, `tool_input`, `cwd`,
  `session_id`, `transcript_path` and `hook_event_name` — the whole field set
  every hook in this repo reads. It does **not** carry the environment the
  command will run under, so there is no authoritative copy to read. The
  hook uses the two sources that do exist: the `GH_REPO=` assignment prefixed
  to the command itself (exact, and the form that most often appears in a
  one-off redirect), and its own environment, which it inherits from the same
  Claude Code process that spawns the Bash tool — so an exported `GH_REPO`
  reaches both. The gap is a value exported *inside the persistent Bash
  session* (`export GH_REPO=x` in an earlier tool call): that shell's
  environment is not the hook's, so the hook will not see it and will name the
  checkout's target instead. This under-detects; it cannot invent a redirect
  that is not there.
- **`gh repo set-default` is read as git config, not by asking `gh`.** The key
  is `remote.<name>.gh-resolved`, which is what `gh` writes
  (`git.Client.SetRemoteResolution`) and reads (`Remotes.ResolvedRemote`).
  Running `gh repo set-default --view` instead would be a subprocess into the
  binary this hook gates, and `gh` reaches the network on far less.
- **Remote URLs are read from git config, so `url.<base>.insteadOf` rewrites
  are not applied.** `gh` enumerates remotes with `git remote -v`, which does
  apply them; a single `git config --get-regexp` returns the raw configured
  URL. Both directions of the resulting divergence are safe-side: a rewrite
  that maps a non-GitHub alias *onto* GitHub leaves the hook silent, and one
  that maps a GitHub URL onto another host makes it ask about a repo `gh`
  would not write to. Neither names a repo while the write lands elsewhere,
  which is the failure that matters. Taking the second git exec to close it
  would double the cost this hook pays on every Bash call in the session.
- **`gh`'s interactive network resolution is out of scope.** With several
  remotes, no `GH_REPO` and no default set, `gh` may query the API to resolve
  a repository network and — when more than one repo comes back — refuse to
  act until `gh repo set-default` is run. The hook names the first remote in
  gh's preference order, which is what `gh` uses in the non-interactive case
  it is actually running in. Closing this properly needs the network, and the
  hook has a 5s budget and no network by design.

### Response format

**HEREDOC_BODY:**

```text
stderr: "❌ BLOCKED: heredoc (`<<`) in `gh pr/issue create` ..."
exit 2
```

**CROSS_REPO_WRITE / IMPLICIT_REPO_WRITE:**

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "⚠️  Cross-boundary pre-flight: ..."
  }
}
exit 0
```

### Compound cascade advisory (issue #229)

All three response paths (HEREDOC_BODY block, CROSS_REPO_WRITE ask,
IMPLICIT_REPO_WRITE ask) append the
shared `_hook_utils.compound_cascade_hint` suffix when the parent Bash command
is compound AND contains a state-changing step (`> file`, `mkdir`, `tee`,
`cp`/`mv`/`rm`, `curl -o`). The classic shape is
`cat <<EOF > /tmp/body.md && gh pr create --body-file /tmp/body.md` — the
heredoc redirect side-effect is aborted together with the gh call, leaving the
agent's retry with `No such file or directory`. The hint instructs the caller
to use the Write tool to materialize the body file first, then issue gh in a
separate Bash call. Single-command rejections do not receive the suffix.

### Opt-out

Known-intentional cross-repo writes can bypass the ASK gate by appending
the opt-out marker to the command. The marker silences both ask arms
identically — the repo-less arm is not a separate escape hatch:

```bash
gh pr create --repo owner/repo --title "t" --body-file /tmp/b.md  # cross-boundary:ack
gh pr create --title "t" --body-file /tmp/b.md                    # cross-boundary:ack
```

Use only after manually verifying all four checklist items. Place the marker
in the **shell command portion only** — on the same `gh` line or after the
heredoc terminator, never inside the heredoc body. The heredoc body becomes
the published artifact; a marker placed inside it leaks verbatim into the
issue/PR text on the remote surface.

The marker has no effect on HEREDOC_BODY — that pattern is always blocked
regardless. When the `Write tool → --body-file` path is blocked by another
guard, the block message (stderr) now lists the marker as a 3rd option with
placement guidance.

### Relationship to sibling hooks

| Hook                               | Scope                                                | Overlap                                                                                                                                    |
| ---------------------------------- | ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `block-gh-state-all`               | `gh search --state all`                              | None — different subcommand                                                                                                                |
| `block-pr-without-caller-evidence` | `gh pr create` body missing `Caller chain verified:` | Complementary — this hook fires first as a pre-flight; sibling fires if body is present but missing the line                               |
| `pre-merge-approval-gate`          | `gh pr merge`                                        | None — different subcommand                                                                                                                |
| `side-effect-scan`                 | `gh pr create` (gh-merge category)                   | Complementary — side-effect-scan fires first with a generic "remote trigger" ask; this hook fires with a targeted cross-boundary checklist |

### Tests

```bash
bash tests/hooks/preflight-gate/test_cross_boundary_preflight.sh
```

Covers 85 cases: heredoc block paths (originals + F2 regression), cross-repo
ask paths (shorthand flags, chained commands, equals forms, F1 regression,
own-org targets per #993), repo-less ask paths (all eight `GH_WRITE_SUBCOMMANDS`
pairs, path-prefixed binary, chained command, `cd <checkout> && gh …` per
issue #1148), ask-detail checks (caller chain item present/absent by subcommand,
ownership-is-no-exemption wording per #993, resolved repo named and
flag-style-is-no-exemption wording per #1148), a block-msg content check
(ack-placement bullet present in stderr), the F2 false-positive guard, pass
paths (read-only including an own-org read-only #993 control that must stay
silent, non-gh, opt-out on both ask arms, variable-heredoc, and the #1148
fail-open controls: non-checkout cwd, no-`origin` checkout, missing cwd,
`cd` into a non-checkout, `gh` named inside echo/commit/grep bodies),
cascade-hint present/absent pair, and infrastructure (non-Bash passthrough,
malformed JSON fail-open, `@fail_open` wrapping).

Every case carries an explicit payload `cwd` pointing at one of four
throwaway checkout fixtures (`resolvable` with a github.com `origin`,
`no-origin`, `not-a-repo`, and `gitlab-github-path` whose remote host is not
GitHub but whose path contains `github`), so the repo-less arm's verdict never
depends on where the suite is invoked from.
