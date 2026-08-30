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

| Pattern                | Trigger                                                                       | Action                                                              |
| ---------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| `HEREDOC_BODY`         | `<<` token in same segment as `gh pr/issue create`                            | **Hard block** (exit 2) — suggests `--body-file`                    |
| `CROSS_REPO_WRITE`     | `--repo/-R` flag in `gh pr/issue create/comment/edit` (any owner)             | **Ask** — surfaces four-point checklist                             |
| `IMPLICIT_REPO_WRITE`  | same subcommands with **no** `--repo/-R`, target resolvable from the checkout | **Ask** — same checklist, naming the resolved repo; silent if unresolvable |

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
hook both see an empty body. Caller-chain evidence and falsification checks
are bypassed silently.

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

| Command                                                       | Action                                              |
| ------------------------------------------------------------- | --------------------------------------------------- |
| `gh pr create --title "t" --body "Caller chain verified: ok"` | **ASK** — target resolved from the checkout         |
| `gh issue create --title "t"`                                 | **ASK**                                             |
| `gh issue comment 42 --body "..."`                            | **ASK**                                             |
| `gh issue edit 7 --title "updated"`                           | **ASK**                                             |
| `gh pr comment 5 --body "..."`                                | **ASK**                                             |
| `gh pr edit 5 --title "updated"`                              | **ASK**                                             |
| `cd <worktree> && gh issue create --title "t"`                | **ASK** — resolved in the `cd` target               |
| `gh issue create --title "t"` in a non-checkout cwd           | **PASS** — target unresolvable, fail-open           |
| `gh issue create --title "t"` with no `origin` remote         | **PASS** — target unresolvable, fail-open           |
| `gh issue list --state open`                                  | **PASS** — read-only subcommand                     |
| `gh issue create --title "t" # cross-boundary:ack`            | **PASS** — opt-out                                  |
| `gh issue create --title "t" <<EOF`                           | **BLOCKED** — heredoc arm runs ahead of this one    |

Target resolution is **local only**: `git -C <cwd> remote get-url origin`,
2s timeout, parsed to `owner/repo` by the same URL shapes the sibling
`pre-gh-pr-create-dedup-gate` accepts (widened to GitHub Enterprise hosts).
No `gh api`, no `gh repo view`, no network call of any kind — the hook's
manifest timeout is 5 seconds for the whole process.

The working directory comes from the PreToolUse payload's `cwd` field, with
an `os.getcwd()` fallback — the same read every sibling that needs a
directory performs (`gh-label-verify`, `anchor-comment-gate`,
`path-probe-gate`). A leading bare `cd <path>` segment updates it before the
later segments are inspected, so `cd <worktree> && gh issue create ...`
resolves `origin` in the worktree rather than in the session's cwd.

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
the command; the repo-less arm has to resolve it. When resolution fails —
cwd is not a git checkout, no `origin` remote, git binary missing,
subprocess error, timeout, or an origin URL that yields no `owner/repo`
slug — the hook exits 0 **silently**, per its standing fail-open contract.
Silence beats naming the wrong repo in an approval prompt.

##### `--help` is not a write (both arms)

A segment carrying `--help` / `-h` never reaches either ask arm:
`_gh_write_subcommand` returns `None` for it. `gh` prints usage and exits
without touching a remote, so gating it surfaces an approval prompt for a
question.

The exclusion sits in the shared detector rather than in either arm because
the defect is older than the repo-less arm: `gh pr create --repo o/r --help`
has asked since before #1148, and this hook is the one place where adding a
narrower exclusion to only the new arm would rebuild the flag-style asymmetry
#1148 exists to remove. The sibling `pre-gh-pr-create-dedup-gate` carries the
same exclusion (`impl.py:98`).

| Command | Action |
| ------- | ------ |
| `gh pr create --help` | **PASS** — usage query |
| `gh issue create -h` | **PASS** — usage query |
| `gh pr create --repo owner/repo --help` | **PASS** — usage query (was ASK before #1148) |
| `gh issue create --title "help the parser"` | **ASK** — the exclusion keys on the flag, not the word |

##### Known limitations

- **Subshell `cd` is not tracked.** `(cd <worktree> && gh issue create ...)`
  tokenizes the command word as `(cd`, and a subshell's directory change does
  not persist past `)`, so honouring it would need per-subshell cwd state the
  segment machinery does not carry. The write is resolved against the
  payload's `cwd` instead; if that is a checkout the ask still fires, but the
  repo named may be the outer one. `cd <path> && ...` outside a subshell is
  tracked, and `cd` targets that need shell expansion (`$VAR`, `~`, globs,
  `cd -`) are ignored rather than guessed. Ignoring means the payload `cwd`
  is kept, so the ask still fires — but on a `cd "$WORKTREE" && gh …`, the
  dominant idiom in this repo's own worktree skills, the repo it names is the
  outer one. Treat the named target as the resolver's best guess, not proof.
- **A `cd` whose target does not exist is not followed.** `cd /nope ; gh
  issue create …` leaves bash in the original checkout, so following the
  target blindly would resolve `origin` somewhere nonexistent and silence the
  gate on a write that really does land here. The directory is stat-checked
  and the move is skipped when it is absent.
- **Non-GitHub `origin` remotes are unresolvable** and therefore silent — a
  local-path or non-GitHub remote yields no `owner/repo` slug. The `github`
  marker is matched in the **host** only: `https://gitlab.com/github/tools/repo.git`,
  a clone under a directory named `github`, and `file:///srv/git/github-mirror/o/r.git`
  all resolve to nothing rather than to a plausible-looking slug.
- **`GH_REPO` and `gh repo set-default` are not consulted.** Either can point
  `gh` at a repo other than the one `origin` names; the hook reports what
  `origin` says. The environment variable is not visible in the Bash payload,
  and `set-default` state lives in git config the hook deliberately does not
  reinterpret.

### Response format

**HEREDOC_BODY:**
```
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
#1148), ask-detail checks (caller chain item present/absent by subcommand,
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
