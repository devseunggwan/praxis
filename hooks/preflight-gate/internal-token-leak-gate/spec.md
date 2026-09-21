# PreToolUse Internal-Token Leak Gate

Supported hosts: all

Reference: [Autonomy vs Convention — ETHOS.md](../../../ETHOS.md#autonomy-vs-convention)

`hooks/preflight-gate/internal-token-leak-gate/impl.py` runs in the
`(PreToolUse, Bash)` dispatch group and blocks a `git commit` or a `gh` write
that carries an organization-internal identifier toward a **public** repository.

### Why this exists

Issue #376 swept the author's real environment names out of this repo once.
Nothing was left to stop them returning, and issue #1470 found 13 lines of them
back — inside new hooks, fixtures and docstrings written after that sweep. The
mechanism is not carelessness: an example is easiest to write with the names the
author actually uses, and in a public repo those names are the payload.

The repo whose purpose is to make its own rules fire structurally had no gate for
this one, so a third sweep was already scheduled. This is that gate.

**The token list is not shipped.** Writing it into this repository would publish
the very names it protects, so the list comes only from
`PRAXIS_INTERNAL_TOKENS` (comma-separated) and an unset variable makes the hook
inert. This mirrors `PRAXIS_SECRET_FETCH_CLIS` (#1157): the shipped default
carries public names, an installer's internal ones arrive through the
environment. Widening a **detector** this way costs at most a false advisory,
which is why the same pattern is refused for the read-only allowlist in
`approval-premise-reread-gate` (#1470) — widening an allowlist removes a
question instead of adding one.

### What is scanned

| Surface | Text read | Target repo |
| --- | --- | --- |
| `git commit` (live; `--dry-run` / `--help` / `-h` exempt) | `-m` / `-F` message text, plus the **added** lines of `git diff --cached -U0` (`-a` / `--all` reads `git diff HEAD -U0` instead) | `origin` of the directory the commit runs in |
| `gh <noun> create\|edit\|comment` (the shared `is_gh_external_write` set) | `--title` / `-t`, and the body via `--body` / `--body-file` | `--repo` / `-R` if present, else `origin` of the command's directory |
| `gh api` write to a comment endpoint | the `body` field, or the `body` key of the JSON handed to `--input` | `repos/<owner>/<repo>` from the endpoint; gh's `{owner}/{repo}` placeholder pair falls back to the checkout |

Only **added** diff lines count. A commit that *deletes* a leaked name must not
be blocked for containing it — that commit is the fix.

`--input` is read here although the shared extractor treats it as an unknown
body. The verification-anchor convention revises an anchor with
`gh api -X PATCH …/comments/<id> --input <file>`, so leaving that form unread
would exempt the single most common external write in this repo.

### Matching

One case-insensitive alternation with a **left** boundary only —
`(?<![A-Za-z0-9])(?:tok|…)`. An org name leaks as a prefix (`<org>-wiki`,
`<org>ctl`, `mcp__<org>-slack__`), so a right boundary would miss exactly the
forms that occur; `x<org>` does not match.

### Visibility decides, not ownership

The global rule is *"Visibility decides the gate, not org membership"*, and
this repo is the proof: it is owned by the author's own account and is public,
so an own-org exemption would have permitted every line issue #1470 found. The
hook therefore asks GitHub — `gh api repos/<owner>/<repo> --jq .visibility` —
and blocks only on `public`. `private` and `internal` are silent, because an
org's own names belong in its own closed repos.

A `public` answer is cached at `~/.praxis/cache/internal-token-visibility.json`
for 24h, keyed by lowercased slug, so a session's repeated commits to a public
repo cost one round trip. `private` and `internal` are never cached: a repo made
public inside the window would otherwise take the very write this gate exists
to stop, while a stale `public` only over-blocks. The lookup runs only after a
token hit, so re-asking costs little. `UNRESOLVED` is never cached either.

### Outcomes

| Condition | Result |
| --- | --- |
| hit, target `public` | **block** (exit 2) with the matching locations |
| hit, target `public`, `PRAXIS_INTERNAL_TOKEN_STRICT=0` | stderr advisory, exit 0 |
| hit, visibility `UNRESOLVED` | stderr advisory, exit 0 — the check could not run, so it may not block |
| hit, target `private` / `internal` | silent |
| no hit, or `PRAXIS_INTERNAL_TOKENS` unset | silent, and no `gh` call is made |

The visibility lookup happens **after** a hit is found. A session with no
internal tokens in its writes pays nothing.

### Fail-open paths (ETHOS: infrastructure failure degrades to "no hook")

- `gh` missing, unauthenticated, offline, 404, or below the dispatch group's
  remaining budget → `UNRESOLVED` → advisory, never a block.
- A directory change this hook cannot model (`cd "$VAR"`, `pushd`, a subshell)
  makes the working directory unknown; from that point commit surfaces are
  skipped. Exactly `cd <literal>` is followed, and only when the target exists —
  mirroring `cross-boundary-preflight`'s classifier.
- No GitHub `origin` (a fresh repo, a non-GitHub remote) → nothing to decide
  against → silent.
- An unreadable body file, or `--input` JSON that does not parse, contributes
  its raw text or nothing rather than raising.

### Detection boundaries (read before extending)

- **The gate is pre-push, not post-push.** It screens the commit and the gh
  write; a `git push` of commits that predate the hook is not re-screened.
- Single-call create-and-commit (`printf x > f && git add f && git commit …`)
  sees an index that does not yet hold the file — the same PreToolUse boundary
  the sibling `pre-commit-staged-file-enumeration` documents.
- A body file path is opened relative to the hook process's cwd, inherited from
  `_external_write_body.read_body_file`; a relative `--body-file` written for a
  different directory reads empty.
- `-m` message values are not skipped when scanning the short-flag cluster for
  `-a`, so a message whose first characters are `-a…` would be read as
  `--all`. It costs a wider scan, never a missed one.
- A partly dynamic endpoint (`repos/{owner}/other/…`, `$OWNER/$REPO`) names no
  repo, so the write is silent rather than blocked on a guessed target.
- MCP writes (Slack, Notion) are out of scope for the same reason the sibling
  `block-personal-asset-leak` states: praxis has no wired MCP matcher.

### Tests

`tests/hooks/preflight-gate/test_internal_token_leak_gate.py` — 36 cases, each
running the real hook against a real git repository with `gh` stubbed on PATH so
visibility is set per case. Covered: message hit, added-line hit with
`path:line`, removed line and context line passing, `private`/`internal` silence,
`UNRESOLVED` advisory, unset env inertness, case-insensitivity, the left
boundary (`acmectl` hits, `xacme` does not), `cd <literal>`, `git -C`, opaque
`cd`, `--dry-run`, `-am`, `-F`, `STRICT=0`, the cache (one `gh` call for two
commits) and its `UNRESOLVED` non-caching, gh title / body / `--body-file` /
`gh api -f body=` / `gh api --input`, the `{owner}/{repo}` placeholder,
read-only `gh` and plain `echo` / `grep` passing, and the negative control that
the neutral names this repo keeps (`example-dev-hub`, `orgctl`,
`mcp__slack__…`, `codex`) never fire.
