# Contributing to Praxis

Praxis is a personal toolbox — contributions are primarily self-directed, but
the conventions below keep the repo coherent across sessions and prevent the
class of drift bugs that have cost the most debugging time.

## Local development

### Clone and CLI symlinks

The CLI tools shipped by skills (e.g. `cmux-recover-sessions`,
`claude-recover`, `cmux-save-sessions`) are symlinked from `~/.local/bin` into
whichever clone ran `scripts/install.sh`, so a patch reaches the version that
runs at the shell only if it lands in that clone. Keep one development clone
per machine; a second clone silently leaves the links pointing at stale code.

After every `git pull` or worktree operation, run `./scripts/verify-symlinks.sh`
to confirm all `~/.local/bin` entries still resolve to this clone.

### CLI tools (not skills)

These are shell wrappers installed via `scripts/install.sh` into `~/.local/bin`.
They are not AI skills — they have no `SKILL.md` and cannot be invoked as `/praxis:*`.

| Binary          | Source                               | Purpose                                                                                                                                                     |
| --------------- | ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bypass-review` | `skills/bypass-review/bypass-review` | Review bypass-telemetry event logs written by the bypass-telemetry hook — aggregate and inspect JSONL records (no `SKILL.md`; not invocable as `/praxis:*`) |

### Install / refresh CLI symlinks

```bash
# From inside this clone:
./scripts/install.sh
```

Idempotent. Existing valid links are left alone; missing or drifted ones
are corrected. Re-run after pulls or after adding a new CLI script.

### Verify symlinks point at this clone

```bash
./scripts/verify-symlinks.sh
```

Exits non-zero on drift, so it can be wired into CI or a SessionStart hook
to catch "patch landed in the wrong clone" before it bites a future session.

### Keeping `AGENTS.md` small

`AGENTS.md` (`CLAUDE.md` is a symlink to it) is loaded into every session, so
every word there costs context on every turn.
[`tests/test_agents_md_budget.py`](tests/test_agents_md_budget.py) caps it at
1,000 whitespace-split words (Python `str.split()` — `wc -w` in the C locale
skips tokens made only of non-ASCII characters such as `—` and `→`, so it
reads low). Content only a contributor needs — setup, tooling,
procedures that are never executed in-session — belongs in this file; leave a
one-line pointer in `AGENTS.md`, and keep its Skills tables, compatibility-tier
table, and Issue & PR conventions intact, since `check-plugin-manifests.py`
Rule 13 normalizes those against `README.md` and `using-praxis` (issue #1306).

## Writing a spec

A *feature spec* states what a change must satisfy, before the change exists.
It is a different artifact from the *skill spec* discussed under
[Skill spec drift prevention](#skill-spec-drift-prevention) below — that one is
a `SKILL.md` describing an existing runtime contract.

Feature specs live at `~/.praxis/docs/specs/NNNN-slug.md`, one file per feature,
in a store outside every checkout that `PRAXIS_HOME` relocates. Write one before
implementation starts; writing it afterwards turns it into post-hoc
justification. Because they are not tracked, a spec never reaches a reviewer —
anything a reviewer needs belongs in the PR body or a commit trailer.

Not every change needs one — [`docs/spec-store.md`](docs/spec-store.md) is the
source of truth for the naming rules, the required `**Issue**: #N` header, the
`Verify:` convention `praxis:spec-drift` reads, and the conditions under which
a spec is skipped.

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
  references/       # optional — long procedure text split out of SKILL.md
  <helper>          # optional — a shell/python helper the steps invoke
```

`references/*.md` count as part of the spec for every check below: a step
moved there still needs the runtime verification metadata, and the
`description` budget is measured on `SKILL.md` alone.

The `name`, `description`, and `when_to_use` fields in the SKILL.md frontmatter
are surfaced by the Claude Code plugin runtime, which truncates the listing past
a bounded budget. [`RUNTIME_CONSTRAINTS.md` §5](RUNTIME_CONSTRAINTS.md) owns
that budget and records what is and is not measured; do not restate a number
here, because two numbers in two files is how a description ends up satisfying
one contract and failing the other. The operative rule while the limit is
unmeasured: `description` says only what the skill does, and the
`Triggers on "..."` clause lives in `when_to_use` (issue #1331), so trimming
description prose never touches a trigger phrase. `docs/skills.md` mirrors the
phrases from `when_to_use` — `check-plugin-manifests.py` Rule 13e fails on any
drift between the two.

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

| § | Constraint | Short form |
| --- | ------------ | ------------ |
| 1 | `AskUserQuestion.options` max 4 items | Truncate dynamic lists to 3 + cancel |
| 1a | `AskUserQuestion.questions` max 4 items | Batch into consecutive calls of ≤ 4 |
| 2 | `Skill(...)` cannot invoke `disable-model-invocation: true` skills | Use the underlying binary directly |
| 3 | `Agent(subagent_type=...)` cannot invoke a skill | Always `Skill(skill="praxis:<name>")` |
| 4 | `Bash` cwd resets between calls | Chain with `&&` or use absolute paths |
| 5 | Skill `description` truncated past ~1,024 chars | Triggers live in `when_to_use`, not the description tail |

#### Enforcement

The frontmatter half is enforced: `scripts/check-plugin-manifests.py` Rule 11
detects a runtime-sensitive skill (external CLI, `AskUserQuestion`,
`Skill(...)`, or a helper executable) and fails when any of the three fields
is missing, a template placeholder, or malformed. It runs locally through
`scripts/run-tests.sh` and in CI. The commit-body `verified:` line is a
convention only — nothing checks it.

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
   before writing your spec — the *Convention Survey Before Design* rule
   ([`ETHOS.md` → Rules praxis carries](ETHOS.md#rules-praxis-carries)).
2. Author the hook in its own per-hook directory:
   - Impl: `hooks/<role>/<name>/impl.py` (or `impl.sh` for body-as-sh hooks).
   - Make it executable: `chmod +x hooks/<role>/<name>/impl.py`.
   - For shared lib access, top of `impl.py`:
     ```python
     import sys as _sys
     from pathlib import Path as _Path
     _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
     from _shell_tokenize import ...
     ```
     Import from the module that defines the name — `_shell_tokenize`
     (`safe_tokenize`, `iter_command_starts`, `strip_prefix`), `_subst`
     (`iter_command_texts`), `_compound` (`compound_cascade_hint`), or
     `_roles` (`tokenize_with_roles`). `_hook_utils` re-exports all four
     for the hooks written before issue #1305 split it; do not route new
     imports through it.
3. Register the hook in [`hooks/manifest.json`](hooks/manifest.json) (one
   entry per `(event, matcher)` group). The entry must include `name`, `role`,
   `event`, and `timeout`, and the first registration of the name must carry
   `review_by` (see [Evidence and sunset review](#evidence-and-sunset-review));
   add `matcher`, `hosts`, `args`, `body`, and `wrapper_suffix` as needed. See
   ADR-0001 §2.5 for the schema.
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
7. Add the hook under its role in [`docs/hook/INDEX.md`](docs/hook/INDEX.md).
   The generated [Hook Operating Matrix](docs/hook-operating-matrix.md)
   picks it up from the manifest on the next build; nothing is added to
   `ARCHITECTURE.md`.
8. Run `./scripts/check-plugin-manifests.py` — it verifies the
   directory↔manifest cross-check, role↔dirname agreement, impl existence,
   Stop ordering, byte-equivalent generated artifacts, and 5+ more
   invariants (see the script preamble).
9. Canary the change — see
   [Verifying a hook change at runtime](#verifying-a-hook-change-at-runtime-canary)
   below. A green test suite does not tell you what the runtime is executing.

### Evidence and sunset review

The roster only grows: [`docs/hook-prune-audit.md`](docs/hook-prune-audit.md)
found zero drops on a 30-day ledger and says it cannot rank hooks by value.
Three rules keep that growth honest (issue #1300):

1. **Recurrence evidence as numbers.** An issue proposing a new hook states
   what it fires on as counts, not adjectives: the transcript grep count, the
   number of sessions, and the window scanned. Copy the format of issue
   #1271's `빈도` section — the scope (`~/.claude*/projects/*/*.jsonl`), the
   count at each narrowing step with its percentage, verbatim samples — and
   say plainly when the real-world failure count is zero, as #1271 does.
2. **`review_by` = merge date + 90 days.** Every hook name carries a
   `review_by` date in `hooks/manifest.json` (first registration, step 3
   above). Rule 26 of `check-plugin-manifests.py` fails CI once that date
   passes. Re-audit the hook against the fire ledger, then either bump the
   date or record the verdict in `docs/hook-prune-audit.md` — never bump
   without the audit.
3. **Zero escalations means Investigate.** At review, a hook that records
   rich fire-ledger rows but has zero `advise` / `ask` / `deny` fires goes
   onto the audit's Investigate list — unless its manifest `mode` sets
   `observe_only: true`, which marks a hook whose zero escalations are its
   specification (`askuserquestion-loop-signal`), not a signal about it.

### Host-aware filtering

Hooks support host-aware filtering via an optional `hosts` field on each hook
entry in `hooks/manifest.json`. When `hosts` is absent the hook is included for
all platforms (default). When present it must be an array of host identifiers
(`"claude"`, `"codex"`, etc.) — the hook is written only to the platform whose
`host_id` in `manifests/platforms/<name>.json` appears in that list. The build
script (`scripts/build-plugin-manifests.py`) reads this field and writes a
platform-specific `hooks.json` for each platform under its plugin directory.
Each per-hook `spec.md` carries a `Supported hosts:` line that documents the
classification; `scripts/check-plugin-manifests.py` verifies that every hook
entry has a corresponding spec file.

### Verifying a hook change at runtime (canary)

Hooks do not execute from this repository. They execute from a versioned copy
under `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache/praxis/praxis/<version>/`
— note the config dir is relocatable, so `~/.claude` is the default, not a
given — and a merged change is not live until
`release → plugin update → session reload` completes.

Both hops were measured once, on 2026-07-28 (issue #841); the figures below
are that snapshot, not a live metric. They are not comparable in size, so
quote the window along with the number — an unqualified median invites
the reader to apply a 14-day figure to a 7-week history:

| Hop | Window | Result |
| ---- | ------- | ------- |
| merge → release | trailing 14 days (n=46) | median **22.6h**, max 90.8h |
| merge → release | since the first release (n=120) | median **50.0h**, max 469.2h |
| release → adoption | v7.6.0, both config dirs | **~2 minutes** |

Merge → release is therefore the whole of the lag; the plugin-cache hop is
effectively instant, so a stale cache is a symptom to investigate rather than
the expected state. Treat the release lag itself as a permanent property of the
repo, not an incident: the release cadence is deliberate.

The consequence is that **you cannot validate a hook change in the session that
wrote it by simply triggering the hook.** Triggering it runs the *previous*
release. Two separate questions need two separate procedures — conflating them
is the failure this section exists to prevent.

#### A. Is the new logic correct?

Invoke the working-tree impl directly with a synthetic payload. This is the same
surface the tests use, so mirror an existing test rather than inventing one.

The invocation surface depends on the hook's manifest entry — read `body` and
`args` before assuming `impl.py` with no arguments:

```bash
# Default: body omitted (impl.py), no args
printf '%s' "$PAYLOAD" | python3 hooks/<role>/<name>/impl.py; echo "rc=$?"

# body: "impl.sh" (codex-review-route, completion-verify,
# retrospect-mix-check, strike-counter) — the generated wrapper is the
# invocation surface, and args are part of the registration
printf '%s' "$PAYLOAD" | hooks/<name>.sh <args...>; echo "rc=$?"
```

A hook can hold several manifest entries that differ only by `args` —
`strike-counter` registers `session-start`, `preprompt`, and `stop` — and
`pre-edit-md-escape-advisory` splits into `-pre` / `-post` wrappers via
`wrapper_suffix`. Probing the wrong entry exercises a different mode than the
one you changed:

```bash
python3 -c "import json;[print(h['name'], h.get('body','impl.py'), h.get('args'), h.get('wrapper_suffix','')) for h in json.load(open('hooks/manifest.json'))['hooks'] if h['name']=='<name>']"
```

This proves the logic. It proves nothing about the runtime.

#### B. Is the runtime executing the new logic?

Compare against the cache copy that is actually registered:

```bash
# Resolve the config dir FIRST — CLAUDE_CONFIG_DIR relocates it, and reading
# the wrong one silently reports a different (often stale) install.
CFG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

# Which version is live right now
LIVE=$(jq -r '.plugins["praxis@praxis"][0].installPath' "$CFG/plugins/installed_plugins.json")
echo "$LIVE"

# Diff the live copy against your working tree.
# Use the hook's actual body from the manifest — impl.sh for the shell hooks,
# and diff the generated wrapper (hooks/<name>.sh) separately for those.
BODY=$(python3 -c "import json;print(next(h.get('body','impl.py') for h in json.load(open('hooks/manifest.json'))['hooks'] if h['name']=='<name>'))")
diff "$LIVE/hooks/<role>/<name>/$BODY" "hooks/<role>/<name>/$BODY"
```

`CLAUDE_CONFIG_DIR` is easy to miss and the failure is silent: both config
dirs can hold a populated `plugins/cache/praxis/praxis/<version>/` tree, so
hardcoding `$HOME/.claude` returns a plausible version that is simply not the
one running. Verify against the config dir the session actually loaded — the
`praxis:*` skill header printed at invocation names it directly.

A non-empty diff means the runtime is still on the old logic — any behaviour you
observe in-session is evidence about the *old* hook, and must not be reported as
verification of the change.

#### Canary probes

Use these to confirm a hook is wired and discriminating, without performing any
mutation. Each has been used on a real release-lag investigation.

1. **Fire-ledger probe** — confirm the hook fired at all, and with which
   decision. `@fail_open` hooks append JSONL records to
   `~/.praxis/telemetry/fire-events-YYYY-MM-DD.jsonl`. Isolate the probe with
   `PRAXIS_FIRE_TELEMETRY_FILE` so it never pollutes the production ledger
   (see issue #849) — and **query the same path you set**, or you will be
   reading pre-existing production rows and mistaking them for your probe:

   ```bash
   LEDGER=$(mktemp)
   # Unset the opt-out — if PRAXIS_FIRE_TELEMETRY_DISABLE=1 is inherited the
   # writer is a no-op, and the empty ledger reads as "hook never fired".
   printf '%s' "$PAYLOAD" | env -u PRAXIS_FIRE_TELEMETRY_DISABLE \
     PRAXIS_FIRE_TELEMETRY_FILE="$LEDGER" python3 hooks/<role>/<name>/impl.py
   jq -r 'select(.hook=="<name>") | "\(.decision) \(.granularity)"' "$LEDGER"
   ```

   Use the invocation surface from procedure A — `hooks/<name>.sh <args...>`
   for `impl.sh` hooks — not a bare `impl.py` for every hook.

   Two caveats when reading the result:

   - **`granularity` first.** `coarse` records collapse `ask`/`advise`/`pass`
     together, and Stop / UserPromptSubmit hooks that block via a stdout
     `decision` field while exiting 0 are recorded as `pass`. A `coarse`
     "pass" is therefore **not** evidence the hook allowed the call.
   - **One fire can produce two rows.** A hook that calls
     `record_session_fire` directly (`pr-report-destination-gate`,
     `askuserquestion-loop-signal`) still passes through `@fail_open`, so a
     `rich` row and a `coarse` duplicate are both written for a single
     invocation — see `_fire_ledger.record_session_fire`'s docstring. Filter
     to `granularity=="rich"` rather than counting rows, or a single fire
     reads as two.

2. **Non-existent-target probe** — for any gate that queries external state
   (`gh pr view`, `gh issue view`), drive it with an identifier that does not
   exist. A correct gate fails open or blocks explicitly; a defective one
   fabricates a verdict from an empty response. This costs nothing and mutates
   nothing.

3. **Negative-discrimination probe** — feed an input the hook is specified to
   *ignore* and confirm silence. For `model-routing-advisory`, a non-Claude
   provider prefix (`--model gemini:pro`) must stay silent per its spec. A hook
   that fires on its documented no-op input is over-triggering, which a
   positive-only test will not catch.

   Silence alone is not a pass: a malformed payload is also silent. Pair every
   negative probe with a positive one that differs *only* in the discriminating
   field, and confirm the positive case fires. Without that pair you are
   verifying your payload shape, not the hook.

#### Advisory output is not visible to the model

`advisory-nudge` hooks emit on stderr, which the model does not see. Never infer
that an advisory fired from the absence of a reaction in the transcript, and
never report an advisory as verified on that basis. Confirm through the
fire-ledger (probe 1) or by invoking the impl directly (procedure A).

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

### Anchor revision without `gh`

`gh` is also a prerequisite of the verification-anchor convention, and for
revision specifically. Creating an anchor needs only a way to post a comment;
editing one in place is a `PATCH` against its comment id, which a session whose
only GitHub surface is the MCP server cannot issue when that server exposes no
comment `update` — comment bodies are add-only there, while issue and PR bodies
are not. Enumerating `github/github-mcp-server` (version unknown, 2026-09-04)
found that absence upstream rather than in one deployment, so it does not
resolve by waiting; it is one measurement of one server, so confirm `update` is
absent from the **active** session's tool list before applying any of this. The
same surface strips `<details>` / `<summary>` on the comment **read** path as
well, which leaves the gate's PostToolUse structure re-check with no substitute
there at all.

Past rev 1 the anchor rule is therefore unsatisfiable in such a session. The
procedure: post rev 1 in full, grade the re-check `unknown`, say in the PR body
which SHA the anchor is stamped at and that the host cannot update it, refresh
that body on every later push that would have been a revision (or stop pushing
until the anchor can be revised), and carry the delta there or into the merge
commit — never a comment of its own, and never a second anchor, which makes id
recovery ambiguous. Steps in
[`anchor-comment-gate/spec.md` → Procedure for a gh-less session](hooks/preflight-gate/anchor-comment-gate/spec.md#procedure-for-a-gh-less-session),
the precondition in
[the section holding it](hooks/preflight-gate/anchor-comment-gate/spec.md#prerequisite--gh-for-revision-specifically).
Issue #1211.

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
`hooks/manifest.json` appears in `docs/hook/INDEX.md`, and (b) each hook
spec's `Supported hosts:` line agrees with the `hosts` array in
`hooks/manifest.json` (`all` = no `hosts` field; explicit list = exact set
match).

## Commit conventions

- Format: `type(scope): description` (Conventional Commits)
- Title: max 50 characters, lowercase, no trailing period
- Body: written in English; include a `verified:` line when the change touches
  a skill spec (see above)
- Never commit directly to `main`; always use a branch + PR

## Changelog

`CHANGELOG.md` is generated by release-please from the Conventional Commit
history — you do not edit it by hand. Write a clear `type(scope): summary`
commit and it lands in the right section on the next release PR: `feat` →
Added, `fix` → Fixed, `perf`/`refactor`/`docs`/`ci` → Changed; `chore`/`test`/
`style` are hidden. The full mapping lives in `release-please-config.json`
(`changelog-sections`).

## Releasing

Releases are fully automated by `.github/workflows/release-please.yml`
(issue #752). You do **not** cut releases by hand or bump `VERSION` yourself.

### How it works

1. Every push to `main` runs release-please. It reads the Conventional Commits
   since the last release and, when one is warranted, opens (or updates) a
   **release PR** that bumps `VERSION` and prepends the generated
   `CHANGELOG.md` section.
2. A follow-up step regenerates the per-platform manifests from the bumped
   `VERSION` (`scripts/build-plugin-manifests.py`) and commits them onto the
   release PR, so the `check-plugin-manifests.py` VERSION-DRIFT gate passes.
3. Review the release PR. Optionally edit its CHANGELOG section to add a human
   "Headline changes" summary before merging — generated entries plus an
   editorial lead (the hybrid flow).
4. Merge the release PR (squash). The next push-to-`main` run tags the commit
   `vX.Y.Z` and publishes the GitHub Release.

> **Squash-merge determines the version bump.** Because every PR is squash-merged,
> release-please sees one commit per PR whose type is the **PR title's**
> Conventional Commit type — not the individual branch commits. A `ci:` / `chore:`
> / `docs:` PR title bumps **patch**; `feat:` bumps **minor**; a `!` / `BREAKING
> CHANGE` bumps **major**. Title release-worthy PRs accordingly. To force a
> specific version regardless of title, put `Release-As: X.Y.Z` in the merged
> commit body (a one-time override — see the release-please README).

### The changelog-coverage guard

release-please **skips** a commit whose message its parser rejects: the step
logs `commit could not be parsed:` and the run still ends green with
`PR ... remained the same`, so the entry is simply missing from the release
(issue #1228 — three commits lost across v7.13.0 and v7.14.0 that way).

`scripts/check-changelog-coverage.py` runs as the workflow's last step and
fails the run when a commit whose type is non-hidden in `release-please-config.json`
has no entry on the pending release PR. When it goes red:

1. Open the `release-please` step's log and search for
   `commit could not be parsed:` — it names the commit and the parse position.
2. **Add the missing entry to the pending release PR's `CHANGELOG.md` by hand**,
   before merging it — the same hand-edit the hybrid flow already allows in step
   3 above. This is the only remediation that works: `main` is never rewritten,
   so the rejected commit stays in the release range, and a follow-up commit
   with a corrected message carries a different sha and PR number rather than
   replacing it. The guard turns green on the next push-to-`main` run, which
   re-reads the edited release branch.
3. Fix the message convention going forward so the next commit does not trip the
   parser again. A dropped entry is never recovered later: release-please
   computes each release from the last tag, so a commit missed by release *N*
   is not picked up by *N+1*.

The guard fails **open** — it exits 0 with a warning — when it cannot see
enough to judge (missing release tag, shallow clone, unreadable config).

### One-time setup — the `RELEASE_PLEASE_TOKEN` secret

release-please must act through a fine-grained PAT (or a GitHub App), **not**
the default `GITHUB_TOKEN`: GitHub does not start workflow runs for events
created by `GITHUB_TOKEN`, so a release PR opened with it would carry no CI
checks and the manifest-sync push would not re-trigger them.

Create a fine-grained personal access token scoped to this repository with:

- **Contents**: Read and write
- **Issues**: Read and write (release-please labels the release PR via the Issues API)
- **Pull requests**: Read and write

Store it as the repository secret `RELEASE_PLEASE_TOKEN`:

```bash
gh secret set RELEASE_PLEASE_TOKEN --repo <owner>/<repo>
```

### Versioning source of truth

`VERSION` is authoritative and release-please owns bumping it;
`.release-please-manifest.json` tracks the last released version. Never bump
either by hand. When you change the manifest set *without* a version bump
(adding a hook, a platform), still run `./scripts/build-plugin-manifests.py`
and `./scripts/check-plugin-manifests.py` locally to keep the generated
artifacts in sync.

## Testing

```bash
# Run the full test suite (tests + manifest checks + static lint) from the repo root
bash scripts/run-tests.sh
```

This is the single entry point. It runs pytest, all shell-based hook tests,
`scripts/check-plugin-manifests.py`, `scripts/check-hook-token-invariants.py`,
`scripts/check-sibling-commit-gates.py`, `scripts/check-omc-name-drift.py`,
`ruff check`, `mypy`, and `shellcheck` under one
exit code gate, plus an advisory
markdownlint pass over the markdown files your branch changed.

All four static checks — `ruff`, `mypy`, `shellcheck`, and `markdownlint` — skip with
an explicit `SKIPPED:` line when the tool is not installed, so a missing
toolchain does not block you. The corresponding CI job still runs either way,
so install them if you want local parity.

`bash scripts/run-tests.sh --doctor` tells you up front which of those
`SKIPPED:` lines a run on your machine will produce. It prints one table of
every external tool the runner or a sub-suite needs (`python3`, `pytest`,
`coverage`, `mypy`, `PyYAML`, `git`, `jq`, `zsh`, `tmux`, `lsof`, `ruff`,
`shellcheck`, `markdownlint-cli2`) with found/missing, the version when found, and the same
install hint the `SKIPPED:` line would carry. It runs no tests and always
exits 0.

The pytest step also measures statement coverage (issue #1303). When the
`coverage` module is importable (`pip install 'coverage==7.16.0'`, the version
CI pins), pytest runs under `coverage run` with the configuration in
`.coveragerc`: sources are `hooks/` and `scripts/`, and `coverage report`
fails the step when the TOTAL drops below `fail_under` — 50%, one point under
the 51% measured when the floor was introduced, so the check catches a real
regression rather than rounding noise. To raise it, run the pytest step, read
the new TOTAL, and set `fail_under` to that minus one in the same PR as the
tests that earned it. Only the Python that pytest imports in-process is
counted: `impl.py` executions driven from the shell suites are subprocesses
and are not yet measured, which is the deferred half of #1303. Without the
module the tests still run, plain, and the step prints `SKIPPED: coverage`
under the same strict-mode protocol as the static checks. CI appends the
per-file table to the job summary.

The markdownlint pass normally covers only the markdown your branch changed,
measured against `origin/main`. When `origin/main` is not available — a shallow
clone, or a fork that has not fetched upstream — it prints
`NOTE: origin/main unavailable` and falls back to linting every tracked
markdown file, which surfaces the repo's existing backlog. It stays advisory in
both modes and never fails the run.

CI invokes this runner from the `test` job in `.github/workflows/ci.yml`. It is
not the whole of CI: `ci.yml` additionally runs `ruff`, `mypy`, `shellcheck`,
`markdownlint`, `actionlint`, `gitleaks`, and `link-check` as separate jobs,
and CodeQL's `analyze` job runs from its own `.github/workflows/codeql.yml`.
Those jobs are authoritative. The runner mirrors `ruff`, `mypy`, `shellcheck`,
and `markdownlint` so they surface before you open a PR; the rest stay CI-only
because they depend on network access, tokens, or a full-history scan.

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
