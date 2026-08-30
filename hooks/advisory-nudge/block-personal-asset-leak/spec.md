# PreToolUse Personal-Asset Leak Advisory

Supported hosts: all

The hook implementation lives at
`hooks/advisory-nudge/block-personal-asset-leak/impl.py`; its matcher is
`Write|Edit|Bash`, so the build emits a standalone per-hook wrapper
(`hooks/block-personal-asset-leak.sh`) rather than registering it under the
`(PreToolUse, Bash)` dispatch group it belonged to before issue #658. It fires
on PreToolUse(Bash|Write|Edit) and emits a stderr advisory on two
personal-asset marker classes:

1. **Absolute home-dotfiles path** (`/Users/<name>/.claude/...`,
   `/home/<name>/.config/...`) in a `gh` write body — always active.
2. **Personal-repo reference** (`<owner>/<repo>`, `<owner>/<repo>#N`) heading
   toward a non-personal write target — opt-in via
   `PRAXIS_PERSONAL_REPO_OWNERS` (comma-separated owner handles; unset =
   class 2 and the Write/Edit surface are fully inactive, restoring the
   pre-#658 dotfiles-only behavior).

### Why this exists

The global rule **No Personal-Asset Surface** is the top-priority entry in the
user's CLAUDE.md, yet it is repeatedly violated under task momentum — the
instruction and memory layers have proven insufficient (praxis issue #563). The
CLAUDE.md "Loaded ≠ Retrieved" doctrine prescribes structural escalation (a
hook) for a rule that recurs despite being loaded. This hook is that escalation
— but only for the part of the rule a deterministic gate can actually enforce.

### Scope and non-goals (read before extending)

This hook is a **crude, deterministic backstop for ONE leak form**: an absolute
machine path into a hidden home-config directory pasted into a public/shared
write. That path leaks the OS username and local machine layout, and once
published carries a retraction cost.

It deliberately does **not** attempt the more damaging *semantic* leak form the
global rule targets: unsolicited routing recommendations toward personal assets
("X plugin fits better", "matches the sibling pattern", "create the issue in
X"). Those are legitimate words in a legitimate sentence — no regex can separate
a leak from a benign mention. That form remains an **agent-side retrieval
discipline** problem; this hook must not be cited as solving it. Treating the
hook as full coverage would create false confidence and is itself a regression
of the rule.

This narrow scope (advisory, dotfiles-absolute-path only, single-direction,
always-fire, gh-write surface only) was chosen explicitly to keep the hook's
side-effect footprint below its benefit — a noisy block on the very repo it
ships from would cost more than the literal leak it prevents.

**MCP writes (slack/notion) are out of scope.** praxis has no wired
MCP-matcher entry in `hooks/manifest.json`, and a `hosts: all` MCP matcher's
behavior is unverified across the 5 target platforms (claude / codex / cursor /
gemini / opencode). Rather than ship an unverified matcher or a dead MCP code
path, MCP coverage is deferred to a follow-up that first establishes an
MCP-matcher convention. The gh issue/PR body is the dominant external-write
surface the agent uses, so the backstop's value holds without MCP.

### What is detected

| Surface                                                                                                            | Active when                       | Trigger                                                                                                           |
| ------------------------------------------------------------------------------------------------------------------ | --------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Bash `gh issue/pr create\|comment\|edit`, `gh pr review` with a body flag (`--body` / `-b` / `--body-file` / `-F`) | always                            | body contains an absolute home-dotfiles path (class 1)                                                            |
| same gh surface                                                                                                    | `PRAXIS_PERSONAL_REPO_OWNERS` set | body contains a personal-repo reference AND the write target is not a personal repo (class 2)                     |
| Write `content` / Edit `new_string`                                                                                | `PRAXIS_PERSONAL_REPO_OWNERS` set | content contains either marker class AND `file_path` lands in a team-surface worktree (see target discrimination) |

The class-1 marker regex is `(?:/Users|/home)/[^/\s<>]+/\.[A-Za-z0-9._-]+` — an
absolute `/Users` or `/home` prefix, a username segment (excluding `<` / `>` so
documentation placeholders do not fire), then a **dot-prefixed** directory.

The class-2 marker regex is
`(?<![A-Za-z0-9._-])(?:<owner>|…)/[A-Za-z0-9._-]+(?:#[0-9]+)?` (case-
insensitive) — the declared owner handle followed by a repo slug and an
optional issue number. The lookbehind rejects a word-ish char immediately
before the owner (`xtestowner/foo` does not match). A slash-preceded match is
kept only when the text before the slash ends in a dotted hostname, so URL
forms (`github.com/<owner>/<repo>#9`) are caught while filesystem path
segments (`/Users/<owner>/projects/...`) are NOT — with the owner handle equal
to the OS username, worktree paths would otherwise trip the advisory.

### Target discrimination (class 2 only)

Class 2 fires only when the write heads toward a surface the declared owner
does NOT control — referencing your own repo inside your own repo is normal;
referencing it in a team surface violates the global "Personal repo content
isolation" rule. Resolution is lazy (git subprocess runs only after a marker
is found) and **fail-open** (any resolution failure → silent):

| Surface    | Target resolution                                                               | Exempt (silent) when                                                                                                                                |
| ---------- | ------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Bash gh    | `--repo`/`-R` flag owner; else `git remote get-url origin` of the payload `cwd` | target owner ∈ `PRAXIS_PERSONAL_REPO_OWNERS`, or target unresolvable                                                                                |
| Write/Edit | `git remote get-url origin` of `file_path`'s nearest existing ancestor          | origin owner ∈ owners, no git repo / no origin remote, or `git check-ignore` matches `file_path` (gitignored scratch areas like `.omc/plans/` pass) |

Body-extraction robustness (each closed a real FN found in review):

- **Every** body flag in a gh command is scanned, not just the first — a
  duplicate `--body a --body b` has its leak caught regardless of which value
  gh ultimately publishes.
- `--body-file` / `-F` values are read from disk (best effort); a **relative**
  body-file path is resolved against the payload `cwd` (the Bash tool's cwd),
  not the hook process's own cwd, so the staged file is found and scanned.
- A `--body "$BODY"` whose value is a heredoc variable assigned earlier in the
  same command (`BODY=$(cat <<EOF … EOF)`) is resolved to the heredoc body
  before scanning (mirrors `block-pr-without-caller-evidence`). The
  `cross-boundary-preflight` hook hard-blocks a heredoc placed in the *same*
  segment as the gh call; the separate-segment assignment form reaches this
  hook, so resolving it closes the gap on the most common authored body path.

### False-positive boundary (what is NOT flagged)

| Input                                                                      | Flagged? | Why                                                                                                                                                                                    |
| -------------------------------------------------------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/Users/alice/.claude/settings.json`                                       | **yes**  | absolute path + hidden dir → leaks username + layout                                                                                                                                   |
| `/home/bob/.config/foo`                                                    | **yes**  | same, Linux home                                                                                                                                                                       |
| `~/.claude/settings.json`                                                  | **no**   | tilde form is home-relative, exposes no username — it is the *recommended replacement* the advisory suggests                                                                           |
| `/Users/alice/projects/praxis/hooks`                                       | **no**   | no dot-prefixed segment after the username → not a dotfiles path; worktree paths are out of scope by user decision                                                                     |
| `/Users/<name>/.claude/settings.json`                                      | **no**   | the username segment excludes `<` / `>` (`[^/\s<>]+`), so documentation placeholders — which praxis specs and PR bodies write constantly — do not fire; only a concrete username leaks |
| `block-gh-state-all`, `pre-merge-approval-gate` (praxis hook names)        | **no**   | hook filenames are not matched; this is what keeps praxis's own PR/issue bodies (which discuss hooks constantly) from being nagged                                                     |
| `xtestowner/foo`, `nottestowner/bar` (owner as suffix of a longer handle)  | **no**   | class-2 lookbehind rejects a word-ish char before the owner — only the exact declared handle matches                                                                                   |
| `/Users/testowner/projects/praxis` (worktree path, owner == OS username)   | **no**   | a slash-preceded class-2 match requires a dotted hostname before the slash — filesystem path segments are not repo references                                                          |
| `testowner/scratchpad#209` written INTO a repo owned by `testowner`        | **no**   | class 2 is target-discriminated — own-repo writes are exempt                                                                                                                           |
| `testowner/scratchpad#209` written into a gitignored path (`.omc/plans/…`) | **no**   | gitignored scratch areas are not a published team surface                                                                                                                              |

Known false-negatives (accepted within the narrow charter):

- A dotfiles path supplied **as the `-F` / `--body-file` filename** (`gh issue
  create -F /Users/alice/.claude/body.md`) is read as a *file path*, not scanned
  as a marker — the hook reads that file's *contents* and scans those. A literal
  dotfiles path in the body-file argument therefore goes undetected. This is the
  documented "read body-file contents (best effort)" behavior.
- `gh` invoked by absolute path is now caught via `_is_gh_binary` (`/usr/bin/gh`,
  `./gh`, subshell-wrapped forms) — this closed the prior exact-match gap.

The dot-dir requirement is the load-bearing FP guard: it is precisely what lets
praxis discuss its own `~/.claude` config and `/Users/.../projects/...` worktrees
in PR bodies without tripping the advisory.

### Direction and firing

Single-direction (personal-asset → external write). Class 1 (dotfiles path) is
always-fire and does not branch on the write target — an absolute dotfiles
path is undesirable in any published body, including praxis's own, and the
marker is narrow enough that always-firing stays low-FP. Class 2 (personal-repo
reference) IS target-discriminated (see above) — without that branch the hook
would nag every write inside the user's own personal repos, which is exactly
the noise profile issue #658 was opened to avoid. Reverse-direction leak (org
identifiers leaking into a personal-repo write) remains **out of scope** for
this hook.

### Mode and env var behavior

| Env var state                                    | Effect                                                                                            |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| `PRAXIS_PERSONAL_LEAK_STRICT` unset (default)    | **Advisory** — exit 0 + stderr warning on match                                                   |
| `PRAXIS_PERSONAL_LEAK_STRICT=1`                  | Strict — exit 2 (block) on match, both classes                                                    |
| `PRAXIS_PERSONAL_REPO_OWNERS` unset (default)    | Class 2 + Write/Edit surface **inactive** — behavior identical to the pre-#658 dotfiles-only hook |
| `PRAXIS_PERSONAL_REPO_OWNERS=<owner>[,<owner>…]` | Class 2 active for the listed owner handles (case-insensitive); Write/Edit surface active         |

The owners env var mirrors the `worktree-edit-gate` /
`PRAXIS_WORKTREE_ENFORCED_REPOS` opt-in pattern: shipped users see zero
behavior change until they declare their own personal owners.

Default is advisory: a dotfiles path in a body is a *missed cleanup*, not always
a confidentiality breach, and blocking on the ship-from repo would be a standing
tax. Strict mode is for users who want the path scrubbed before any external
write lands.

### Relationship to sibling hooks

| Hook                           | Scope                                                                                               | Overlap                                                                                                                                                                      |
| ------------------------------ | --------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `external-write-falsify-check` | hypothesis-marker / author-exempt-identifier scan on gh/MCP write surfaces (opt-in, off by default) | Complementary — same gh body-extraction logic (this hook copies the gh detection; 2nd occurrence, no shared-lib extraction yet per DRY-on-3rd), different marker class       |
| `cross-boundary-preflight`     | `--repo` cross-repo write ASK + heredoc block                                                       | Complementary — that hook surfaces a "no internal identifiers" *reminder* on cross-repo writes; this hook deterministically scans the body for one concrete identifier class |

### Parsing guarantees

- Malformed JSON payload → exit 0 (fail-open)
- `tool_name` other than `Bash` / `Write` / `Edit` → exit 0
- `Write` / `Edit` with `PRAXIS_PERSONAL_REPO_OWNERS` unset → exit 0
- Bash command with no gh external-write subcommand → exit 0
- gh write subcommand with no body flag → exit 0
- `--body-file` pointing at an unreadable path → treated as empty body → exit 0
- Body/content with no marker of either class → exit 0
- Class-2 target unresolvable (no git repo, no origin remote, git error/timeout) → class 2 silent
- Any uncaught exception → exit 0 via the `@fail_open` decorator (standalone-wrapper requirement, DESIGN.md #645)

### Tests

```bash
bash tests/hooks/advisory-nudge/test_block_personal_asset_leak.sh
```

Covers: gh create/comment/edit/review body advisory; `--body-file` disk read;
tilde-form `~/.claude` NOT flagged; worktree `/projects/` path NOT flagged;
praxis hook-name NOT flagged; strict-mode block (exit 2); malformed-payload and
non-Bash tool fail-open; multi-marker de-duplication; `=`-joined body flag form
(`--body=...`). Class 2 (against local git fixtures with team/personal origin
remotes): Write/Edit owner-ref into a team repo (warn, incl. URL form and
case-insensitive match); personal-repo target exempt; gitignored-path exempt;
non-git-dir fail-open; prefixed non-owner slug (`xtestowner/foo`) NOT flagged;
env-unset baseline (Write surface and gh owner class both silent); gh
`--repo`/cwd-origin target discrimination; strict-mode block on the owner
class.
