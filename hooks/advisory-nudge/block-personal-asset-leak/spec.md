# PreToolUse Personal-Asset Leak Advisory

Supported hosts: all

The hook implementation lives at
`hooks/advisory-nudge/block-personal-asset-leak/impl.py`; it is a member of the
`(PreToolUse, Bash)` dispatch group, so the build registers it under the shared
`hooks/_dispatch.sh` dispatcher node in each platform `hooks.json` rather than
emitting a per-hook wrapper. It fires on PreToolUse(Bash), inspects the `gh` write
body, and emits a stderr advisory when the body contains an **absolute
home-dotfiles path** (`/Users/<name>/.claude/...`, `/home/<name>/.config/...`).

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

| Surface | Trigger |
|---------|---------|
| Bash `gh issue/pr create\|comment\|edit`, `gh pr review` with a body flag (`--body` / `-b` / `--body-file` / `-F`) | body contains an absolute home-dotfiles path |

The marker regex is `(?:/Users|/home)/[^/\s<>]+/\.[A-Za-z0-9._-]+` — an absolute
`/Users` or `/home` prefix, a username segment (excluding `<` / `>` so
documentation placeholders do not fire), then a **dot-prefixed** directory.

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

| Input | Flagged? | Why |
|-------|----------|-----|
| `/Users/alice/.claude/settings.json` | **yes** | absolute path + hidden dir → leaks username + layout |
| `/home/bob/.config/foo` | **yes** | same, Linux home |
| `~/.claude/settings.json` | **no** | tilde form is home-relative, exposes no username — it is the *recommended replacement* the advisory suggests |
| `/Users/alice/projects/praxis/hooks` | **no** | no dot-prefixed segment after the username → not a dotfiles path; worktree paths are out of scope by user decision |
| `/Users/<name>/.claude/settings.json` | **no** | the username segment excludes `<` / `>` (`[^/\s<>]+`), so documentation placeholders — which praxis specs and PR bodies write constantly — do not fire; only a concrete username leaks |
| `block-gh-state-all`, `pre-merge-approval-gate` (praxis hook names) | **no** | hook filenames are not matched; this is what keeps praxis's own PR/issue bodies (which discuss hooks constantly) from being nagged |

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

Single-direction (personal-asset → external write), always-fire. The hook does
not branch on the write target (`--repo` org vs personal) — an absolute
dotfiles path is undesirable in any published body, including praxis's own, and
the marker is narrow enough that always-firing stays low-FP. Reverse-direction
leak (org identifiers leaking into a personal-repo write) is **out of scope** for
this hook.

### Mode and env var behavior

| Env var state | Mode | Exit code on match |
|---------------|------|-------------------|
| `PRAXIS_PERSONAL_LEAK_STRICT` unset (default) | **Advisory** | 0 + stderr warning |
| `PRAXIS_PERSONAL_LEAK_STRICT=1` | Strict | 2 (block) |

Default is advisory: a dotfiles path in a body is a *missed cleanup*, not always
a confidentiality breach, and blocking on the ship-from repo would be a standing
tax. Strict mode is for users who want the path scrubbed before any external
write lands.

### Relationship to sibling hooks

| Hook | Scope | Overlap |
|------|-------|---------|
| `external-write-falsify-check` | hypothesis-marker / author-exempt-identifier scan on gh/MCP write surfaces | Complementary — same gh body-extraction logic (this hook copies the gh detection; 2nd occurrence, no shared-lib extraction yet per DRY-on-3rd), different marker class |
| `cross-boundary-preflight` | `--repo` cross-repo write ASK + heredoc block | Complementary — that hook surfaces a "no internal identifiers" *reminder* on cross-repo writes; this hook deterministically scans the body for one concrete identifier class |

### Parsing guarantees

- Malformed JSON payload → exit 0 (fail-open)
- `tool_name` other than `Bash` → exit 0
- Bash command with no gh external-write subcommand → exit 0
- gh write subcommand with no body flag → exit 0
- `--body-file` pointing at an unreadable path → treated as empty body → exit 0
- Body with no absolute home-dotfiles path → exit 0

### Tests

```bash
bash tests/hooks/advisory-nudge/test_block_personal_asset_leak.sh
```

Covers: gh create/comment/edit/review body advisory; `--body-file` disk read;
tilde-form `~/.claude` NOT flagged; worktree `/projects/` path NOT flagged;
praxis hook-name NOT flagged; strict-mode block (exit 2); malformed-payload and
non-Bash tool fail-open; multi-marker de-duplication; `=`-joined body flag form
(`--body=...`).
