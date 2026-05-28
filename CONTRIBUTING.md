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
|------------|------------|
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
`EXPECTED_SKILLS` set in
[`scripts/check-plugin-manifests.py`](scripts/check-plugin-manifests.py) in the
same commit. This is a structural gate against silent skill proliferation —
every intentional surface change is paired with an explicit declaration.

After adding/removing a skill, run:

```bash
./scripts/check-plugin-manifests.py
```

If it reports `UNEXPECTED SKILL(S)` or `REMOVED SKILL(S)`, update
`EXPECTED_SKILLS` to match the new surface.

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

## Testing

```bash
# Run the full test suite from the repo root
python -m pytest tests/

# Run the shell-based hook tests directly (pytest only collects .py files)
for f in tests/hooks/*/test_*.sh; do bash "$f"; done
```

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
