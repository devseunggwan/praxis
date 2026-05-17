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
- wraps an external CLI (`codex`, `gh`, `kubectl`, `hubctl`, or any binary not
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

## Adding or modifying a hook

1. Survey ≥ 2 sibling implementations under `hooks/` for established conventions
   (state-key naming, payload field access, exit-code semantics) before writing
   your spec. See the **Convention Survey Before Design** rule in `CLAUDE.md`.
2. Write the hook + tests, register in `hooks/hooks.json`.
3. Create `docs/hook/<name>.md` (use an existing spec as a template).
4. Add a row to the hook index table in `CLAUDE.md`.
5. Run `./scripts/check-plugin-manifests.py` to confirm packaging is clean.

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

## Commit conventions

- Format: `type(scope): description` (Conventional Commits)
- Title: max 50 characters, lowercase, no trailing period
- Body: written in English; include a `verified:` line when the change touches
  a skill spec (see above)
- Never commit directly to `main`; always use a branch + PR

## Testing

```bash
# Run the full test suite from the repo root
python -m pytest tests/
```

New hooks must ship with tests under `tests/`. New skills do not require
automated tests, but must satisfy the live runtime verification requirement
described above.
