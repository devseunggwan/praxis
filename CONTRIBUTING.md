# Contributing to Praxis

Praxis is a personal toolbox — contributions are primarily self-directed, but
the conventions below keep the repo coherent across sessions and prevent the
class of drift bugs that have cost the most debugging time.

## Adding or modifying a skill

### Template

A starter template lives at [`skills/SKILL.md.tmpl`](skills/SKILL.md.tmpl).
Copy it into `skills/<skill-name>/SKILL.md`, fill in the placeholders, and
follow the step-by-step guide at
[`skills/writing-praxis-skill/SKILL.md`](skills/writing-praxis-skill/SKILL.md).

### Directory structure

```
skills/<skill-name>/
  SKILL.md          # spec — frontmatter + prose steps
```

The `name` and `description` fields in the SKILL.md frontmatter are surfaced by
the Claude Code plugin runtime. Keep `description` under 500 characters; the
runtime truncates beyond that.

### Skill spec drift prevention

> **This is the most important section if you are wrapping an external CLI.**

Five independent drift incidents (Issue #208) established that skill specs
authored without a live runtime round-trip contain silent contract violations
that block execution on the very first use. The structural gate below prevents
the sixth.

#### Rule: verify before publishing

Any skill that:
- wraps an external CLI (`codex`, `gh`, `kubectl`, or any binary not
  shipped in this repo), **or**
- calls `AskUserQuestion` with a dynamic option list, **or**
- delegates to another skill via `Skill(...)`

**must** complete a live round-trip invocation before the spec is merged.

#### Frontmatter requirement

After a live verification round-trip, add these three fields to the SKILL.md
frontmatter:

```yaml
verified-against-runtime: true
runtime-verified-at: YYYY-MM-DD
runtime-verified-note: "<cli-name> <version> — one-line description of observed behavior"
```

Example (from `codex-review-wrap`):

```yaml
verified-against-runtime: true
runtime-verified-at: 2026-05-13
runtime-verified-note: "codex-companion 1.0.4 — ARGUMENTS rejected for non-flag string; AskUserQuestion maxItems:4 blocks worktree list >3 items"
```

#### Commit body requirement

The commit that introduces or significantly revises a skill spec must include
a one-line runtime note in the commit body. Use the same text as
`runtime-verified-note`:

```
feat(skills): add my-new-skill

verified: my-cli 2.3.1 — --flag-name accepted; output shape confirmed
```

This embeds the verification evidence in `git log` so it survives frontmatter
refactors and is visible to `git blame`.

#### Known runtime constraints

Read [`RUNTIME_CONSTRAINTS.md`](RUNTIME_CONSTRAINTS.md) before writing a new
spec. It lists fixed Claude Code limits that every skill must work within:

| Constraint | Short form |
| ------------ | ------------ |
| `AskUserQuestion.options` max 4 items | Truncate dynamic lists to 3 + cancel |
| `Skill(...)` cannot invoke `disable-model-invocation: true` skills | Use the underlying binary directly |
| `Bash` cwd resets between calls | Chain with `&&` or use absolute paths |

#### Pre-commit hook (planned)

A pre-commit hook that validates `verified-against-runtime: true` + commit body
note for `skills/*/SKILL.md` changes is planned as a follow-up to Issue #208.
It is not yet enforced — the frontmatter + commit body convention above is the
current gate.

### Skill surface freeze (`EXPECTED_SKILLS`)

Adding or removing a skill directory under `skills/` requires updating the
`EXPECTED_SKILLS` set in [`scripts/constants.py`](scripts/constants.py) in
the same commit. This is a structural gate against silent skill
proliferation — every intentional surface change is paired with an explicit
declaration.

After adding/removing a skill, run:

```bash
./scripts/check-plugin-manifests.py
```

If it reports `UNEXPECTED SKILL(S)` or `REMOVED SKILL(S)`, update
`EXPECTED_SKILLS` in `scripts/constants.py` to match the new surface.

## Adding or modifying a hook

Phase 2 of [ADR-0001](docs/adr/0001-hook-layout.md) shipped the role-based
layout. Each hook now lives in its own directory under one of four roles
(`preflight-gate`, `advisory-nudge`, `postuse-correction`, `completion-verify`),
and the canonical registry is `hooks/manifest.json` (not `hooks.json`).

1. Survey ≥ 2 sibling implementations under `hooks/<role>/` for established
   conventions (state-key naming, payload field access, exit-code semantics)
   before writing your spec. See **Convention Survey Before Design** in
   global `~/.claude/CLAUDE.md`.
2. Author the hook in its own per-hook directory:
   - Impl: `hooks/<role>/<name>/impl.py` (or `impl.sh` for body-as-sh hooks).
   - Make it executable: `chmod +x hooks/<role>/<name>/impl.py`.
   - For shared lib access, top of `impl.py`:
     ```python
     import sys as _sys
     from pathlib import Path as _Path
     _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
     from _hook_utils import ...
     ```
3. Register the hook in [`hooks/manifest.json`](hooks/manifest.json) (one
   entry per `(event, matcher)` group). The entry must include `name`, `role`,
   `event`, and `timeout`; add `matcher`, `hosts`, `args`, `body`, and
   `wrapper_suffix` as needed. See ADR-0001 §2.5 for the schema.
4. Run `./scripts/build-plugin-manifests.py` — this generates the runtime
   wrapper at `hooks/<name>{suffix}.sh` (tracked; the build re-emits it
   on every run, and commits must include the new wrapper because
   marketplace installs do not invoke the build script) and refreshes
   every platform's `hooks.json`.
5. Write the test at `tests/hooks/<role>/test_<name>.{sh,py}`:
   - Resolve repo root via `ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"`.
   - Reference the impl as `$ROOT_DIR/hooks/<role>/<name>/impl.{py,sh}` (or
     the wrapper at `$ROOT_DIR/hooks/<name>.sh` for body-as-sh hooks where
     the wrapper IS the impl invocation surface).
6. Create `hooks/<role>/<name>/spec.md` (template: any existing spec). Include a
   `Supported hosts:` line matching the `hosts` array in `manifest.json`.
7. Add a row to the hook index table in [`ARCHITECTURE.md`](ARCHITECTURE.md#hook-index).
8. Run `./scripts/check-plugin-manifests.py` — it verifies the
   directory↔manifest cross-check, role↔dirname agreement, impl existence,
   Stop ordering, byte-equivalent generated artifacts, and 5+ more
   invariants (see the script preamble).

## Reviewing or auditing a PR

> **When judging what a PR changed, look at the PR ref directly — never assume
> the ambient working tree is the PR.**

A checked-out working tree is not the PR. After a `git reset --hard origin/main`
or a `git checkout`, the tree holds whatever ref you last moved to — often
*pre-merge `main`* — not the PR's contents. Auditing that tree and attributing
the result to the PR produces confident-but-wrong findings: a real incident
reported a guard as "unfixed / live bypass" while the PR branch had already
fixed it, and the claim was only corrected after reading the PR ref with
`git show`.

Inspect the PR by its ref, not by whatever happens to be on disk:

| Question | Command |
| ---------- | --------- |
| What did the PR change? | `git diff origin/main...origin/<branch>` |
| What does a file look like in the PR? | `git show origin/<branch>:<path>` |
| Which ref is checked out right now? | `git rev-parse --abbrev-ref HEAD && git log -1 --oneline` |

Confirm the checked-out ref **before** drawing any conclusion, especially right
after a `reset` or `checkout`. If you must audit from the tree itself, fetch and
check out the PR branch first (`git fetch origin <branch> && git checkout
<branch>`) instead of assuming the current tree reflects it.

## Packaging

**Do not edit generated files directly.** The following are generated outputs:

- `.claude-plugin/plugin.json`
- `.claude-plugin/marketplace.json`
- `.agents/plugins/marketplace.json`
- `plugins/praxis/.codex-plugin/plugin.json`

To regenerate after changing `manifests/*.json` or `VERSION`:

```bash
./scripts/build-plugin-manifests.py
./scripts/check-plugin-manifests.py   # verify no drift
```

`check-plugin-manifests.py` also verifies (a) every hook in
`hooks/manifest.json` appears in both `docs/hook/INDEX.md` and the
`ARCHITECTURE.md` hook index table, and (b) each hook spec's
`Supported hosts:` line agrees with the `hosts` array in
`hooks/manifest.json` (`all` = no `hosts` field; explicit list = exact set
match).

## Commit conventions

- Format: `type(scope): description` (Conventional Commits)
- Title: max 50 characters, lowercase, no trailing period
- Body: written in English; include a `verified:` line when the change touches
  a skill spec (see above)
- Never commit directly to `main`; always use a branch + PR

## Changelog

User-visible changes go into `CHANGELOG.md` under the `## [Unreleased]` section.
The next VERSION bump moves those entries under the new version header. Use
[Keep a Changelog](https://keepachangelog.com/) categories: Added, Changed,
Fixed, Removed.

## Releasing

Releases are automated by `.github/workflows/release.yml`. To cut one:

1. In your version-bump PR, set `VERSION` and move the `## [Unreleased]`
   entries under a new `## [X.Y.Z] - YYYY-MM-DD` header in `CHANGELOG.md`.
2. After it merges to `main`, trigger the `release` workflow via
   `workflow_dispatch` with the tag (e.g. `v7.1.0`) — from the Actions tab or
   `gh workflow run release.yml -f tag=vX.Y.Z`. When the tag does not yet
   exist, the workflow **creates and pushes it from inside Actions**, pointed
   at the `main` commit whose `VERSION` matches, then publishes the release. No
   hand-pushed tag is needed (and a policy-restricted session that cannot push
   `refs/tags/*` is not a blocker).
3. The workflow builds the body from that CHANGELOG section (via
   `scripts/extract-changelog-section.sh`) plus a fixed Install/Update footer
   and publishes the GitHub Release.

Re-running `workflow_dispatch` on an existing tag just edits that release
instead of duplicating it. Pushing a `vX.Y.Z` tag by hand still works as a
fallback and triggers the same publish path.

### Pre-PR checklist (version bump)

Before opening a version-bump PR, verify all three artifacts are in sync —
missing any one of these breaks the automated release body or ships stale
manifests silently:

```bash
# 1. VERSION and generated manifests match
./scripts/build-plugin-manifests.py
./scripts/check-plugin-manifests.py   # verify no drift

# 2. CHANGELOG.md has a `## [X.Y.Z] - YYYY-MM-DD` section for the new
#    version — confirm the `## [Unreleased]` entries were moved under it,
#    not left behind

# 3. The release workflow can extract that section (this is the release
#    body's actual source, not CHANGELOG.md read informally)
bash scripts/extract-changelog-section.sh X.Y.Z

# 4. The new section's "N PRs since X.Y.Z" count matches the PRs actually
#    merged into the release (guards the #750 omission — a hand-written count
#    that undercounts silently drops entries from the published notes). CI runs
#    this too (the `changelog` job).
bash scripts/check-changelog-completeness.sh
```

If step 3 exits non-zero (exit 2 = version not found in `CHANGELOG.md`), the
section header doesn't match `VERSION` — fix it before opening the PR. If step 4
exits non-zero, reconcile the changelog: add the missing entries and correct the
`N PRs since` count (or, for a PR deliberately folded into another entry, adjust
the count). The count is bounded by the `## [prev]`..`## [current]` section
range, so post-release hotfixes never inflate a frozen release's number.

## Testing

```bash
# Run the full test suite (pytest + shell tests + manifest check) from the repo root
bash scripts/run-tests.sh
```

This is the single entry point. It runs pytest, all shell-based hook tests, and
`scripts/check-plugin-manifests.py` under one exit code gate.

CI runs the same command on every push and pull request via
`.github/workflows/test.yml`.

New hooks must ship with a test under `tests/hooks/<role>/`:

- `tests/hooks/<role>/test_<name>.sh` for shell-driven coverage (synthesise
  a Claude Code hook payload, pipe into the hook, assert exit code + stderr).
- `tests/hooks/<role>/test_<name>.py` for pytest-style coverage (matches
  `tests/hooks/completion-verify/test_completion_signal_gate.py`).

Tests reference the hook via `$ROOT_DIR/hooks/<role>/<name>/impl.{py,sh}`
where `ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"` — never hard-code
repo paths.

New skills do not require automated tests, but must satisfy the live
runtime verification requirement described above.
