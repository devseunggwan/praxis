# PreToolUse `gh pr create` Duplicate-Search Gate

Supported hosts: all

`hooks/preflight-gate/pre-gh-pr-create-dedup-gate/impl.py` intercepts every Bash tool call and,
when it sees `gh pr create` (or `gh pr new`), runs a duplicate-PR search
against the *target* repo and surfaces the result unconditionally to stderr
before the create command executes.

### Why this exists

An agent completed a full implement-and-PR cycle that turned out to be
byte-identical to a merged PR from another author 3 hours earlier (praxis
issue #234). The agent's pre-creation duplicate check existed only as a
prompt-layer reasoning instruction ("check for in-flight work before
creating a PR"). Under task momentum (plan approved, mid-implementation),
that instruction was not retrieved. A contributing factor: the agent
searched the wrong repo — the repo where the tracking issue lived, not the
repo where the PR would actually land.

This is the same class of failure as `block-pr-without-caller-evidence`
(praxis #158) and `cross-boundary-preflight` (praxis #199): a rule that
exists in context but has no execution-time retrieval trigger at the
action boundary. Memory entries alone fail to enforce; structural gates do.

### What the hook does

| Phase | Action |
| ------- | -------- |
| 1. Match | `gh [global flags] pr create/new` — skips `--help`, non-pr-create, non-Bash |
| 2. Resolve repo | `--repo`/`-R` flag first; fall back to `git remote get-url origin` |
| 3. Extract keywords | Strip Conventional Commits prefix from `--title`, drop stop-words, keep up to 6 tokens |
| 4. Run dedup search | `gh pr list --repo <r> --state all --search "<kw>" --limit 20 --json ...` (4s timeout) |
| 5. Emit artifact | Unconditional stderr block — header + match table (or `no matches`) |

### Pass / block matrix

| Command | Action |
| --------- | -------- |
| `gh pr create --repo o/r --title "feat: add dedup gate"` | **PASS** — artifact emitted, exit 0 |
| `gh pr create --title "fix(hooks): x"` (origin resolves) | **PASS** — artifact emitted, exit 0 |
| `gh pr create --title "fix: x"` (no `--repo`, no origin) | **BLOCK (exit 2)** — `cannot resolve PR target repo` |
| `gh pr create --repo o/r` (no `--title`) | **PASS** — notice that dedup was skipped; manual command suggested |
| `gh pr create --repo o/r --title "WIP"` (all stop-words) | **PASS** — same skip notice |
| `gh pr create --repo bogus/nonexistent --title "x"` | **BLOCK (exit 2)** — `gh exit: <rc>` + raw stderr from gh |
| `gh pr create --help` | **PASS** — help is read-only |
| `gh pr list --state all` | **PASS** — not `pr create` |
| `gh issue create --title "x"` | **PASS** — different subcommand |
| `FOO=1 sudo gh pr create --repo o/r --title "fix: x"` | **PASS** — env/sudo wrappers peeled |
| Non-Bash tool | **PASS** — exits 0 |
| Malformed stdin JSON | **PASS** — fail-open, exit 0 |
| `gh` binary missing on PATH | **PASS** — fail-open, exit 0 (cannot enforce dedup search without gh) |

### Artifact format (emitted to stderr)

```
[pre-gh-pr-create-dedup-gate] Duplicate-PR search
  repo : owner/name
  query: dedup gate

  matches: 2 (review before creating PR)
  #214   [MERGED] @alice  feat(hooks): dedup gate prototype
         https://github.com/owner/name/pull/214
  #220   [OPEN  ] @bob    feat(hooks): add pre-gh-pr-create dedup gate
         https://github.com/owner/name/pull/220
```

No-matches branch:

```
[pre-gh-pr-create-dedup-gate] Duplicate-PR search
  repo : owner/name
  query: dedup gate
  result: no matches
```

The header (repo + query) appears in **both** branches — that's what the
issue spec means by "the artifact must be visible whether or not a match
is found." It demonstrates that the hook ran against the correct repo with
the correct query, removing the silent-failure mode where the dedup check
exists in prompt but never executes.

### Match-found behavior: advisory, not blocking

Per the issue's open question, the hook leans **advisory** on a match
(exit 0 with the artifact rendered). Topic-keyword matching has false
positives — same-topic follow-up PRs are legitimate and common. Hard-
blocking on match would remove agency for those. The agent (or human
reviewer of the agent's transcript) reads the artifact and decides.

Escalation to hard-block on match is deferred until empirical data on
false-positive vs true-positive rates accumulates.

### Block-on-failure rationale

The hook **does** hard-block when:

1. The target repo cannot be resolved (`--repo` absent and `git remote
   get-url origin` fails or output is unparseable). This is the exact
   failure mode the issue cites — a worktree's `origin` differing from
   the PR target repo. Silent fallthrough would defeat the hook.
2. `gh pr list` itself errors (auth failure, repo not found, timeout).
   A silently-failing dedup check is the failure this hook exists to
   prevent. Block surfaces the issue loudly.
3. `gh pr list` returns valid JSON that is not a list (error envelope
   such as `{"message":"Bad credentials"}`, future schema change).
   Same rationale — silent fall-through would defeat the gate.

### Repo resolution algorithm

1. Scan argv for `--repo <r>`, `-R <r>`, `--repo=<r>`, `-R<r>` —
   honoring both the `pr create` subcommand position and the `gh`
   global flag position.
2. If not found, exec `git remote get-url origin` (2s timeout).
3. Parse `owner/repo` from URL forms:
   - `git@github.com:owner/repo.git`
   - `https://github.com/owner/repo.git`
   - `ssh://git@github.com/owner/repo`
4. Unparseable / missing → block.

### Keyword extraction

1. Strip Conventional Commits prefix matching `^[a-z]+(\([^)]+\))?:\s*`
   (`feat:`, `fix(scope):`, `chore(hooks/foo):`, etc.).
2. Tokenize on non-alphanumeric to handle punctuation, slashes,
   apostrophes uniformly.
3. Lowercase, drop stop-words (`a/the/and/of/for/to/in/on/with/add/fix/
   update/remove/make/use/set/new/wip` plus common inflections).
4. Deduplicate, keep first `MAX_KEYWORDS=6` tokens.
5. Empty result → skip search with a notice (exit 0).

### Known limits (parity with sibling hooks)

All praxis Bash gates use `_hook_utils` structural tokenization, not full
shell semantics. The following bypass patterns are accepted limits shared
with `block-pr-without-caller-evidence` and `block-gh-state-all`:

- Variable command name: `SUB=create; gh pr $SUB --title ...`
- `eval`: `eval gh pr create --title ...`
- Wrapper indirection: `xargs gh pr`
- Re-entry via `bash -c "gh pr create ..."`
- Shell function/alias shadowing of `gh`

Closing these would require a real shell parser. Empirically the failure
modes the hook targets (forgotten dedup check under task momentum) do not
involve such patterns; the cost/benefit doesn't favor full coverage.

Closed bypasses (issue #511): path-prefix (`/usr/bin/gh pr create`) and the
`command`/`builtin` shell wrappers (`command gh pr create`) are now caught.
`strip_prefix` peels `command`/`builtin`, and gh detection uses the
basename-aware `_is_gh_binary` helper (symmetric with the commit gates'
`_is_git_binary`), which also normalizes leading subshell/substitution chars
(`(gh …)`, `$(gh …)`).

### Relationship to sibling hooks

| Hook | Overlap |
| ------ | --------- |
| `cross-boundary-preflight` | None — orthogonal: format/cross-repo gate vs. dedup search |
| `block-pr-without-caller-evidence` | None — orthogonal: body content gate vs. dedup search |
| `side-effect-scan` | Same surface (`gh pr create` triggers `gh-merge` category ask) — complementary: side-effect-scan surfaces a generic intent-confirm ask; this hook surfaces the dedup artifact |
| `pre-merge-approval-gate` | None — different subcommand (`gh pr merge`) |

The four together gate `gh pr create` from four orthogonal angles. Each
runs in parallel; their stderr / decisions compose at the tool-decision
layer.

### Performance & timeouts

- Hook `timeout` in `hooks.json`: **8 seconds** (longer than the default
  5s used by other Bash gates because this hook makes two subprocess
  calls — `git remote get-url` then `gh pr list`).
- `gh pr list` subprocess timeout: **4 seconds** (caps network wait;
  treated as a gh-error block).
- `git remote get-url`: **2 seconds**.
- Hook fast-paths to exit 0 on every code path before issuing
  subprocesses, so non-`gh pr create` Bash calls add only tokenization
  cost.

### Tests

```bash
bash hooks/test-pre-gh-pr-create-dedup-gate.sh
```

Covers 27 cases: repo resolution (flag form, short form, equals form,
gh global flag form, origin fallback, unresolved-block), keyword
extraction (Conventional Commits prefix strip, all-stop-words, empty
title, "WIP"), artifact emission (no-matches header, matches table,
MERGED tag, PR URL), gh failure modes (non-zero exit + stderr surfaced,
unparseable JSON, JSON object instead of list), passthroughs (`--help`,
`pr list`, `issue create`, env/sudo wrapper transparency, chained
command, gh-missing fail-open, non-Bash tool, malformed stdin fail-open).
