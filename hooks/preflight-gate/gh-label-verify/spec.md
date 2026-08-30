# PreToolUse gh PR/Issue Label Verifier

Supported hosts: claude, codex

`hooks/preflight-gate/gh-label-verify/impl.py` intercepts every Bash tool call of the form
`gh (issue|pr) (create|edit)` and hard-blocks invocations whose `--label`
/ `-l` / `--add-label` values are not present in the target repository's
label set. Probes `gh label list` with a per-repo TTL cache so the
verification cost is amortized across a session.

### Why this exists

Label namespaces differ across repositories. A label name that exists in
one repo (e.g. a prefixed `area:foo`) does not exist in another (e.g. a
bare `foo`). When a session pattern-matches a label convention from one
repo onto another without probing the target repo's label set first,
`gh pr create --label <missing>` fails after the round-trip — wasted
work that recurs across sessions.

This hook fills the One-Probe-Before-Action gap for the label-namespace
sub-variant. Memory-only enforcement was insufficient; the verification
is mechanical, so it belongs at the hook layer.

### What is blocked

The hook tokenizes the command via `safe_tokenize` →
`iter_command_starts` → `strip_prefix` (the same pipeline as
`gh-flag-verify`), so chained segments and env-prefixed invocations are
covered uniformly.

| Command                                                             | Action                            |
| ------------------------------------------------------------------- | --------------------------------- |
| `gh pr create --label bug --repo acme/repo` (bug exists)            | **PASS**                          |
| `gh pr create --label nope --repo acme/repo` (nope absent)          | **BLOCKED**                       |
| `gh pr create --label bug,nope --repo acme/repo` (one missing)      | **BLOCKED**                       |
| `gh pr create --label bug --label nope --repo acme/repo`            | **BLOCKED**                       |
| `gh issue create -l nope --repo acme/repo`                          | **BLOCKED**                       |
| `gh issue edit 1 --add-label nope --repo acme/repo`                 | **BLOCKED**                       |
| `gh pr edit 1 --add-label bug --repo acme/repo`                     | **PASS**                          |
| `gh pr create --label=nope --repo acme/repo`                        | **BLOCKED** (inline `=` form)     |
| `gh pr create --label bug` (no `--repo`, cwd resolves to acme/repo) | **PASS**                          |
| `gh pr create --label bug` (no `--repo`, cwd not a git repo)        | **PASS** (fail-open)              |
| `gh label list --repo ...` fails (network / auth)                   | **PASS** (fail-open)              |
| `gh pr list --label nope` (label is a filter here, not a write)     | **PASS** (subcmd not in scope)    |
| `gh pr create --label nope --help`                                  | **PASS** (help skips enforcement) |
| Non-Bash tool                                                       | **PASS**                          |

### Repo resolution

1. `--repo <r>` / `-R <r>` / `--repo=<r>` in argv → use as-is.
2. Otherwise parse `git -C <cwd> remote get-url origin` → `owner/repo`.
3. Both unavailable → fail-open (cannot key the cache, cannot validate).

### Caching

Label sets are cached per repo at
`${PRAXIS_HOME:-~/.praxis}/cache/gh-label-cache.json`, resolved through the
shared `_paths.resolve_writable("cache", …)` (#1182) so `PRAXIS_HOME`
relocates it and the opportunistic `prune_stale` TTL sweep covers it like
every other cache entry (the hook payload's `session_id` is threaded into the
sweep so it never deletes the calling session's own live markers).
Deliberately NOT `resolve_cache_file`: its legacy-`${TMPDIR}` adoption would
let a pre-seeded world-writable `${TMPDIR}/praxis-gh-label-cache.json` be
promoted into the trusted label cache, and this file never lived in TMPDIR.
Cache entries expire after `PRAXIS_GH_LABEL_CACHE_TTL_SEC` seconds (default
300); an entry whose `fetched_at` lies in the future is rejected as stale
rather than read as forever-fresh. Cache corruption, write failures, or read
failures are all fail-open: enforcement still runs from a fresh fetch, just
without persistence.

Override the cache file location via `PRAXIS_GH_LABEL_CACHE_PATH` (used
by tests; also useful for isolated sandboxes).

Before #1182 the cache lived at
`${XDG_CACHE_HOME:-~/.cache}/claude-praxis/gh-label-cache.json`, outside the
documented praxis roots. Old files are not migrated — entries expire in
minutes, so a stranded file costs at most one refetch and rots harmlessly.

### Fail-open paths

The hook never breaks a session on infrastructure failure:

- `gh` not installed
- Network / auth failure on `gh label list`
- `shlex` / `safe_tokenize` failure
- Cache file unreadable or unwritable
- `--repo` absent AND cwd not a git repo

In every case the hook exits 0 silently; the gh command proceeds and may
fail at execution time, surfacing the original error.

### Configuration

| Env var                         | Default    | Purpose                       |
| ------------------------------- | ---------- | ----------------------------- |
| `PRAXIS_GH_LABEL_CACHE_TTL_SEC` | `300`      | Per-repo cache TTL in seconds |
| `PRAXIS_GH_LABEL_CACHE_PATH`    | (computed) | Override cache file path      |

### Relationship to gh-flag-verify

`gh-flag-verify` validates *which flag names* are accepted by a
subcommand (a name-level check). This hook validates the *values*
passed to `--label` for the subset of subcommands that write labels (a
value-level check). The two hooks co-fire under the same PreToolUse
matcher; deny precedence ensures the user sees the first denial reason.

### Tests

```bash
bash tests/hooks/preflight-gate/test_gh_label_verify.sh
```
