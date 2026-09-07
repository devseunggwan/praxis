# ADR-0001: Hook layout restructuring (role-based per-hook folders + wrapper elimination + test consolidation)

- **Status**: Accepted
- **Date**: 2026-05-26
- **Authors**: praxis maintainers
- **Supersedes**: n/a (first ADR in this repo)
- **Related**: [`ETHOS.md`](../../ETHOS.md), [`DESIGN.md`](../../DESIGN.md), [`ARCHITECTURE.md`](../../ARCHITECTURE.md), [`docs/hook/INDEX.md`](../hook/INDEX.md)

---

## 1. Context

Praxis ships a Claude-Code-compatible hook suite (currently 39 hooks across
PreToolUse / PostToolUse / UserPromptSubmit / SessionStart / Stop events). The
runtime source layout has accreted incrementally; the resulting topology now
has measurable friction.

### 1.1 Current layout snapshot (2026-05-26)

| Location | Count | Mix |
| ---------- | ------- | ----- |
| `hooks/` (flat) | 89 files | 35 `.py` implementations + 39 `.sh` wrappers + 13 `test-*.sh` + `hooks.json` + `_hook_utils.py` + `__pycache__/` |
| `tests/` (flat) | 30+ files | 28 `test_*.sh` + 1 `tests/hooks/*.py` + `fixtures/` |
| `docs/hook/` (flat) | 39 markdown specs | `INDEX.md` |

```
hooks/
  _hook_utils.py
  block-gh-state-all.py
  block-gh-state-all.sh         ← 13-line wrapper
  side-effect-scan.py
  side-effect-scan.sh           ← 9-line wrapper
  …  (× 39 wrapper pairs)
  strike-counter.sh             ← 313-line, sh is the body
  test-block-gh-state-all.sh
  …
  hooks.json
docs/hook/
  block-gh-state-all.md
  side-effect-scan.md
  …
  INDEX.md                       ← categorises into 4 roles
tests/
  test_block_gh_state_all.sh
  …
  hooks/test_completion_signal_gate.py
  fixtures/
```

### 1.2 Observed friction

1. **Wrapper boilerplate (×39).** Each `<hook>.sh` is 9–13 lines of nearly
   identical `command -v python3 || exit 0; exec python3 .../<hook>.py`. The
   fail-open contract is duplicated 39 times.
2. **Test location is bifurcated.** `hooks/test-*.sh` (13) and `tests/test_*.sh`
   (28) coexist; two pairs are literal duplicates
   (`test-external-api-literal-trigger`, `test-jq-config-empty-dict-advisory`).
   Convention for new hooks is therefore ambiguous.
3. **`docs/hook/INDEX.md` categories live in prose only.** The
   `preflight-gate / advisory-nudge / postuse-correction / completion-verify`
   taxonomy is the de-facto contract (referenced from spec headers,
   `ARCHITECTURE.md`, hook ethos), but neither the directory tree nor
   `hooks.json` reflects it.
4. **A hook's artifacts span five locations.** For a single hook
   (`block-gh-state-all`): `hooks/*.py`, `hooks/*.sh`, `hooks/test-*.sh`,
   `docs/hook/*.md`, and one entry in `hooks/hooks.json`. PR diffs for
   substantive changes routinely span ≥ 4 directories.
5. **`hooks.json` is a flat array of 32 PreToolUse(Bash) entries** with no
   in-file role grouping. Ordering and intent are only legible via `INDEX.md`.
6. **Multi-event hooks fragment.** `pre-edit-md-escape-advisory` ships as
   `-pre.sh` (PreToolUse(Edit)) + `-post.sh` (PostToolUse(Read)) +
   shared `.py`, creating three filenames that share semantics but are
   alphabetised next to unrelated hooks.

### 1.3 What is already healthy and must be preserved

- `_hook_utils.py` is a clean single shared library (23/34 implementations
  consume it). Its API surface should not change as part of this ADR.
- The per-platform manifest generator (`scripts/build-plugin-manifests.py`)
  already filters by host whitelist and writes per-platform artifacts. The
  invariant "platform manifests are generated, never hand-edited" must hold.
- Stop-hook sequential ordering (`completion-verify → retrospect-mix-check →
  completion-signal-gate → strike-counter stop`) is load-bearing for the
  evidence-gate semantics; any restructure must preserve declared order.
- `excluded_hooks` per platform (currently used to omit Claude-only hooks
  from Cursor/OpenCode manifests) must keep working.

### 1.4 Forces

- **Discoverability**: a contributor adding a new hook should see at a glance
  which role bucket it belongs to, what files it needs, and where its tests
  go. Today the answer is "five different folders".
- **Spec-code coherence**: spec drift (the spec saying X while the impl does
  Y) has been a recurring cost. Physically co-locating spec and impl is the
  cheapest structural fix.
- **Boilerplate cost**: the 39 wrappers are zero-information files. Each new
  hook today requires copying one. Build-time generation removes the manual
  copy without changing the runtime behaviour (Claude Code still sees a `.sh`
  command, since the build emits one).
- **Risk envelope**: hooks are load-bearing for active sessions. Any
  restructure that lands as a single commit risks blast-radius across every
  platform manifest. Phased rollout is required.

---

## 2. Decision

Praxis will move to the following canonical layout:

```
hooks/
  _lib/
    _hook_utils.py                # shared tokenizer (unchanged API)
  manifest.json                   # canonical hook registry (replaces hooks.json)
  preflight-gate/                 # role 1
    block-gh-state-all/
      impl.py
      spec.md                     # ex docs/hook/block-gh-state-all.md
    side-effect-scan/
      impl.py
      spec.md
    pre-edit-protected-branch-guard/
      impl.py
      spec.md
    …
  advisory-nudge/                 # role 2
    memory-hint/
      impl.py
      spec.md
    momentum-rule-retrieval-gate/
      …
  postuse-correction/             # role 3
    builtin-task-postuse/
      impl.py
      spec.md
    pre-edit-md-escape-advisory/   # multi-event hook
      pre.py                       # PreToolUse(Edit)
      post.py                      # PostToolUse(Read)
      spec.md
  completion-verify/              # role 4
    completion-verify/
      impl.sh                      # sh is the body, not a wrapper
      spec.md
    retrospect-mix-check/
      …
    completion-signal-gate/
      …
    strike-counter/
      impl.sh                      # 313-line sh body
      spec.md
docs/
  adr/
    0001-hook-layout.md            # this document
  hook/
    INDEX.md                       # surviving high-level taxonomy
tests/
  hooks/
    preflight-gate/
      test_block-gh-state-all.sh
    advisory-nudge/
      test_memory-hint.sh
    …
    fixtures/
```

> **Amendment (issue #1305, 2026-09-05).** `_lib/_hook_utils.py` was split
> into `_shell_tokenize.py`, `_subst.py`, `_compound.py`, and `_roles.py`.
> It stays in place as a re-export shim, so the "unchanged API" promise in
> the tree above, and in the list of what this ADR leaves unchanged, still
> holds for every `from _hook_utils import …` consumer; new code imports
> from the defining sub-module.

Four structural changes combine into a single coherent end state:

### 2.1 A — Role-based subdirectories under `hooks/`

The four roles in `docs/hook/INDEX.md` become directories:

- `preflight-gate/` — PreToolUse blockers and ask-gates
- `advisory-nudge/` — PreToolUse stderr nudges (never block)
- `postuse-correction/` — PostToolUse corrective context
- `completion-verify/` — Stop-event evidence/discipline gates (includes
  `strike-counter`, which technically also fires on `SessionStart` and
  `UserPromptSubmit`; its primary role is session-lifecycle discipline and is
  classified with completion-verify in `INDEX.md`)

A single canonical taxonomy. Drift between code and `INDEX.md` becomes a
build-time failure (see §2.5).

### 2.2 B — Per-hook folder

Each hook becomes a directory containing all of its artifacts:

- `impl.py` (default) or `impl.sh` (when shell is the body)
- `spec.md` (moved from `docs/hook/<name>.md`)
- Multi-event hooks: named files (`pre.py`, `post.py`, …) instead of `impl.py`

Result: a PR that changes a hook's behaviour can only touch one directory
(plus the matching test file and `manifest.json` if the registration shape
changed). Spec drift becomes a diff-time inconsistency, not a cross-folder
hunt.

### 2.3 C — Wrapper elimination via build-time generation

The 39 hand-maintained `.sh` wrappers disappear from source. The build
script (`scripts/build-plugin-manifests.py`) becomes responsible for
emitting a wrapper into each generated platform artifact, with the same
fail-open contract:

```bash
#!/bin/sh
# AUTO-GENERATED by scripts/build-plugin-manifests.py — DO NOT EDIT
set +e
command -v python3 >/dev/null 2>&1 || exit 0
exec python3 "$(dirname "$0")/impl.py" "$@"
```

For hooks where `impl.sh` is the body (e.g. `strike-counter`, `completion-verify`,
`retrospect-mix-check`, `completion-signal-gate`), the build writes an entry
that points directly at `impl.sh` with no wrapper layer.

The build retains the option of inlining the wrapper command into
`hooks.json` (Claude Code accepts `python3 ${CLAUDE_PLUGIN_ROOT}/...`
directly), but the conservative path is to emit a thin `.sh` per platform
so the runtime contract is unchanged byte-for-byte. **The first cut will
emit the `.sh`**; inlining is a follow-up if no regressions surface.

### 2.4 D — Test consolidation under `tests/hooks/`

All `hooks/test-*.sh` move to `tests/hooks/<role>/test_<name>.sh`. The two
literal duplicates are reconciled (kept the more recent variant per
`git log`, then deleted the other).

Naming convention:

| Today | After |
| ------- | ------- |
| `hooks/test-block-gh-state-all.sh` | `tests/hooks/preflight-gate/test_block-gh-state-all.sh` |
| `tests/test_memory_hint.sh` | `tests/hooks/advisory-nudge/test_memory-hint.sh` |
| `tests/hooks/test_completion_signal_gate.py` | `tests/hooks/completion-verify/test_completion-signal-gate.py` |

Tests use hyphenated names matching the hook directory (`test_<name>.sh`)
for consistent grep/discovery (`tests/hooks/**/test_*.{sh,py}`).

### 2.5 Canonical registry: `hooks/manifest.json`

`hooks/hooks.json` is replaced with `hooks/manifest.json`, a higher-level
schema that the build expands into the existing platform-specific
`hooks.json` files.

```json
{
  "$schema": "./manifest.schema.json",
  "hooks": [
    {
      "name": "block-gh-state-all",
      "role": "preflight-gate",
      "event": "PreToolUse",
      "matcher": "Bash",
      "hosts": ["claude", "codex"],
      "timeout": 5
    },
    {
      "name": "pre-edit-md-escape-advisory",
      "role": "postuse-correction",
      "hosts": ["claude", "codex"],
      "entries": [
        { "event": "PreToolUse",  "matcher": "Edit", "file": "pre.py",  "timeout": 5 },
        { "event": "PostToolUse", "matcher": "Read", "file": "post.py", "timeout": 5 }
      ]
    },
    {
      "name": "strike-counter",
      "role": "completion-verify",
      "hosts": [],
      "entries": [
        { "event": "SessionStart",      "args": ["session-start"], "timeout": 5 },
        { "event": "UserPromptSubmit",  "args": ["preprompt"],      "timeout": 5 },
        { "event": "Stop",              "args": ["stop"],           "timeout": 5 }
      ],
      "body": "impl.sh"
    }
  ]
}
```

> **Retired form (#1169, #1173):** the nested `entries` registration sketched
> above never gained a single manifest use — multi-event hooks register as
> flat sibling entries (one object per event) sharing the same `name`.
> `hooks/manifest.schema.json` (#1173) models only the flat form, so an
> `entries` key now fails schema validation; the remaining dead `entries`
> fallbacks in manifest readers are removed under issue #1169.

Build invariants verified by `scripts/check-plugin-manifests.py`:

- Every `hooks/<role>/<name>/` directory has a matching `manifest.json`
  entry, and vice versa.
- `role` field value matches the parent directory name on disk.
- For each entry, `hooks/<role>/<name>/<file>` exists.
- For each entry, `tests/hooks/<role>/test_<name>.{sh,py}` exists (warns
  for advisory-nudge with no behavioural assertion; hard-fails for
  preflight-gate and completion-verify).
- Stop-hook ordering matches `manifest.json` array order in the
  `completion-verify` role.

### 2.6 What does NOT change

- The runtime contract emitted into `.claude-plugin/hooks/hooks.json`,
  `plugins/praxis/.codex-plugin/hooks/hooks.json`,
  `.cursor-plugin/hooks/hooks.json`, `.opencode/hooks/hooks.json` — same
  schema, same commands, same host filtering. Claude Code (and the other
  platforms) see no behavioural difference.
- The `hosts` filtering semantics or its default ("absent = all hosts").
- `_hook_utils.py` public API.
- Stop-hook sequential ordering.
- `excluded_hooks` and `excluded_skills` per-platform manifest fields.
- The skill layout under `skills/` (out of scope for this ADR).
- `VERSION`, `manifests/plugin.base.json`, `manifests/platforms/*.json`
  (shared metadata layer).

---

## 3. Consequences

### 3.1 Positive

| Metric | Before | After |
| -------- | -------- | ------- |
| Flat files under `hooks/` | 89 | ~4 (`_lib/`, `manifest.json`, 4 role dirs) |
| Hand-maintained wrapper `.sh` files | 39 | 0 |
| Test locations | 2 (with 2 duplicates) | 1 |
| Folders touched when adding a hook | 5 (`hooks/*.py`, `hooks/*.sh`, `hooks/hooks.json`, `tests/*.sh`, `docs/hook/*.md`) | 2 (`hooks/<role>/<name>/`, `tests/hooks/<role>/`) + 1 entry in `manifest.json` |
| Spec drift signal | Cross-folder PR; easy to miss | Same-folder diff; PR diff lists spec.md next to impl.py |
| `INDEX.md` ↔ disk drift | Possible (prose-only taxonomy) | Build-time failure (§2.5 invariants) |
| Multi-event hook fragmentation | Three alphabetised filenames | One directory, named files |

### 3.2 Negative / Costs

- **One-time migration cost.** ~150 `git mv` operations across hooks, tests,
  and specs. Path references in `ARCHITECTURE.md`, `DESIGN.md`,
  `CONTRIBUTING.md`, `README.md`, `docs/hook/INDEX.md`, and every spec
  cross-link must update.
- **Generated artifact volume increases slightly.** Each generated platform
  `hooks.json` now embeds wrapper script bodies (or paths to generated
  per-platform wrappers). Net new files in generated outputs: ~39 per
  platform that didn't have them; net new files in source: 0.
- **Build script complexity grows.** `scripts/build-plugin-manifests.py`
  gains: manifest schema validation, per-role directory walk, wrapper
  emission, test-file coverage check. Estimated +200 LOC.
- **External documentation churn.** Any blog post / external link pointing
  at `docs/hook/<name>.md` URLs breaks. Mitigation: keep
  `docs/hook/<name>.md` as 1-line stubs that redirect to the new path for
  one release cycle.
- **Symlink-based deployment** (`plugins/praxis/{skills,hooks,scripts}`
  symlinks into the repo root) continues to work — `hooks/` still exists
  as a directory, just with internal structure.
- **`__pycache__/` proliferation.** Per-role directory means `__pycache__/`
  appears in five places instead of one. Already gitignored — no real
  impact, but worth noting for `find` invocations in CI.

### 3.3 Risks

| Risk | Mitigation |
| ------ | ------------ |
| Generated platform `hooks.json` paths change and existing plugin installs break mid-upgrade | Phase rollout (§5). Phase 2 keeps wrapper `.sh` files on disk during transition so external installs continue to resolve. Wrapper deletion happens only after Phase 3 build is proven. |
| Stop-hook ordering accidentally reshuffles | `manifest.json` array order is the canonical declaration. `check-plugin-manifests.py` adds an assertion for the completion-verify ordering. |
| Spec moves leave dangling links from skill SKILL.md / external docs | grep for `docs/hook/` references during Phase 3; rewrite to `hooks/<role>/<name>/spec.md`. |
| Multi-event hook (`pre-edit-md-escape-advisory`) loses its session-state coupling between `pre.py` and `post.py` | Both files continue to import the same `_lib/_hook_utils.py` and key on `session_id`. State persistence path (under `${PRAXIS_STATE_DIR}`) is unchanged. |
| Build script becomes the single point of failure | Existing fail-safe (`exit 0` on missing python3) is preserved in generated wrappers. The build itself runs in CI; a regression is caught before merge. |

---

## 4. Alternatives Considered

### 4.1 Event-based subdirectories (instead of role-based)

```
hooks/
  pre-tool-use/
  post-tool-use/
  user-prompt-submit/
  session-start/
  stop/
```

**Rejected** because:

- The most populated event is `PreToolUse`, which holds both
  `preflight-gate` (blocks) and `advisory-nudge` (never blocks) — two
  semantically opposite roles in one bucket.
- `INDEX.md`'s existing four-role taxonomy is already the way contributors
  reason about hooks. Re-anchoring on event would force a second mental
  model.
- Multi-event hooks (`pre-edit-md-escape-advisory`,
  `strike-counter`, `session-intent`) fragment under event-based dirs but
  stay coherent under role-based dirs.

### 4.2 Per-hook folders WITHOUT role grouping

```
hooks/
  _lib/
  block-gh-state-all/
  side-effect-scan/
  …  (× 39 folders, alphabetised)
```

**Rejected** because:

- Solves spec drift but not discoverability. Forty-plus top-level entries
  remain hard to scan; an "add a new hook here" contributor still needs
  `INDEX.md` to know where it fits.
- A second restructure ("now add role dirs") becomes inevitable.

### 4.3 Wrapper elimination via direct `python3` in `hooks.json`

```json
"command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/preflight-gate/block-gh-state-all/impl.py"
```

**Considered as a follow-up, not part of this ADR.** Reasons:

- Loses the inlined fail-open: if `python3` is absent or non-executable,
  Claude Code's hook runner will surface an error rather than silently
  passing. The wrapper's `command -v python3 || exit 0` is load-bearing.
- A platform-specific generated wrapper preserves this guarantee at zero
  source-cost (build emits it).
- Worth reconsidering once we have evidence that the wrapper layer adds
  measurable session latency. Today the cost is ~1ms per hook invocation
  and is dwarfed by `python3` startup.

### 4.4 TOML manifest (instead of JSON)

**Rejected for now**. TOML has nicer human-edit ergonomics (comments,
trailing-comma tolerance), but:

- The rest of the manifest pipeline (`plugin.base.json`,
  `platforms/*.json`) is JSON. Mixing schemas in one build script doubles
  parsing surface.
- `hooks/manifest.json` is touched roughly once per hook addition; the
  edit-frequency argument for TOML is weak here.
- Revisit if/when contributor count grows enough that JSON comma-discipline
  becomes a measurable papercut.

### 4.5 Keep `docs/hook/` separate from per-hook folder

**Rejected**. Spec drift is the costliest observed failure mode (multiple
PR review rounds caught spec saying X while impl did Y). Co-location is the
cheapest structural prevention. The argument for keeping docs separate
("`docs/` is the documentation tree") is weaker than the argument for
keeping spec next to code ("a hook IS its spec + impl + test").
`docs/hook/INDEX.md` survives as the surviving categorical landing page;
the per-hook specs move.

### 4.6 No change (status quo)

**Rejected**. The friction is real but slow-bleeding — each individual
papercut is small, but contributors hit them on every hook addition. The
costs in §1.2 will compound as the hook count grows past 50.

---

## 5. Implementation phases

Each phase ships as a separate PR. No phase merges until the previous one
has soaked in `dev` for at least one session without regressions.

### 5.1 Phase 1 — Low-risk cleanup (issue #421, PR #424)

**Scope** ([D] + a slice of [C]):

- Move `hooks/test-*.sh` → `tests/hooks/test_*.sh` (flat, no role dirs
  yet). Reconcile the two duplicates.
- Add a build-script feature flag: when set, the build generates wrapper
  `.sh` files into a `_generated/` directory next to existing source
  wrappers. Source wrappers stay (proves generation works in parallel).
- Add CI assertion that source wrappers and generated wrappers are
  byte-equivalent (modulo header comment).
- Update `CONTRIBUTING.md` to point at the new test location.

**Exit criteria**:

- `tests/hooks/` is the only location for hook tests.
- One full release cycle with parallel-generated wrappers and no diff.

**Rollback**: revert the PR. Source wrappers are untouched, so the runtime
is identical.

### 5.2 Phase 2 — Wrapper elimination + role-based directories (issue #422)

**Scope** ([A] + remainder of [C]):

- Move `hooks/<name>.py` → `hooks/<role>/<name>/impl.py` (39 git mv).
- Move `hooks/<name>.sh` for `impl.sh`-as-body hooks
  (`strike-counter`, `completion-verify`, `retrospect-mix-check`,
  `completion-signal-gate`) → `hooks/<role>/<name>/impl.sh`.
- Delete the 39 hand-maintained wrapper `.sh` files.
- Replace `hooks/hooks.json` with `hooks/manifest.json` (new schema).
- Rewrite `scripts/build-plugin-manifests.py` to consume the new schema
  and emit per-platform `hooks.json` with embedded/co-located generated
  wrappers.
- Update `scripts/check-plugin-manifests.py`:
  - directory ↔ manifest cross-check
  - role-name ↔ parent-directory equality
  - Stop-hook ordering preservation
- Update `_lib/_hook_utils.py` import path references (`hooks/_lib/` now).
- Move `hooks/_hook_utils.py` → `hooks/_lib/_hook_utils.py`.
- Tests move to `tests/hooks/<role>/`.
- Rebuild every generated artifact; commit them.
- Update `ARCHITECTURE.md` hook index table (paths to spec.md files
  to be updated in Phase 3).

**Exit criteria**:

- Every platform's generated `hooks.json` is byte-equivalent to the
  pre-Phase-2 baseline (modulo per-platform wrapper-emission strategy).
- `pytest tests/` is green.
- `./scripts/verify-symlinks.sh` is green.
- Smoke test: install the freshly built plugin in a throwaway Claude Code
  session and trigger one hook from each role.

**Rollback**: revert the PR. Phase 1 state continues to work.

### 5.3 Phase 3 — Per-hook spec collocation + docs cleanup (issue #423, PR #435)

**Scope** ([B]):

- Move `docs/hook/<name>.md` → `hooks/<role>/<name>/spec.md` (39 git mv).
- Leave 1-line redirect stubs at `docs/hook/<name>.md` pointing at the
  new path for one release cycle.
- Rewrite cross-links in:
  - `ARCHITECTURE.md` hook index table (39 link updates)
  - `docs/hook/INDEX.md` (39 link updates)
  - `CONTRIBUTING.md` (template references)
  - Any skill `SKILL.md` that links to a hook spec
- Add a build assertion: every `hooks/<role>/<name>/` MUST contain
  `spec.md` and `impl.{py,sh}` (or named files for multi-event hooks).

**Exit criteria**:

- `docs/hook/` contains only `INDEX.md` and 39 1-line redirect stubs.
- `git grep "docs/hook/" -- ':!docs/adr/'` returns no actionable
  references outside the redirect stubs.
- README and CONTRIBUTING walkthroughs point at the new structure.

**Rollback**: revert the PR. Phase 2 state continues to work; specs return
to `docs/hook/`.

### 5.4 Phase 4 (cleanup, optional, separate issue) — remove redirect stubs

After one full release cycle following Phase 3, delete the 39
`docs/hook/<name>.md` redirect stubs.

---

## 6. Open questions

1. **Should `_lib/` graduate to `hooks/_lib/` or to a top-level `praxis_lib/`?**
   The shared tokenizer is hook-specific today; skill code does not import
   it. Recommendation: `hooks/_lib/` keeps the import surface small and
   makes the boundary explicit. Revisit if/when a skill needs to share
   tokenization logic.
2. **Naming: `impl.py` vs `<name>.py` inside per-hook folders.** `impl.py`
   is shorter and uniform across folders (tooling-friendly grep,
   `tests/hooks/<role>/test_<name>.sh` can `source` it predictably).
   `<name>.py` is more search-friendly when opening a file in an IDE by
   typing its name. Recommendation: `impl.py` for the implementation,
   keep the directory name as the searchable identifier. Aligns with the
   existing convention `SKILL.md` (not `<skill-name>.md`) inside
   `skills/<skill-name>/`.
3. **Should `manifest.json` carry a `role` field given the parent
   directory already encodes it?** Yes — redundant but defensive. The
   build asserts the two agree, so any human edit that drifts one without
   the other is caught.
4. **Generated wrapper: emit into `.claude-plugin/hooks/<role>/<name>/_entry.sh`
   or inline into the `command` string in `hooks.json`?** Recommended
   start: emit a `.sh` file per hook per platform (matches the current
   runtime contract closely). Inlining is a follow-up if it materially
   shrinks the generated artifact.

---

## 7. Decision record

| Date | Decision | Decided by |
| ------ | ---------- | ------------ |
| 2026-05-26 | ADR drafted, Status = Proposed | praxis maintainers |
| 2026-05-26 | Status → Accepted | praxis maintainers |
| 2026-05-26 | Phase 1 merged (#421 / PR #424) | praxis maintainers |
| 2026-05-26 | Phase 2 merged (#422) — role dirs + per-hook folders + `manifest.json` + wrapper drop | praxis maintainers |
| 2026-05-27 | Phase 3 merged (#423 / PR #435) — spec collocation + redirect stubs + check-manifest invariant #10 | praxis maintainers |
| 2026-09-05 | Post-Phase-3 soak row closed: the layout has been unchanged since Phase 3 and Status stays Accepted | praxis maintainers |

---

## 8. References

- [`docs/hook/INDEX.md`](../hook/INDEX.md) — current four-role taxonomy
- [`ARCHITECTURE.md → Hook index`](../../ARCHITECTURE.md#hook-index) — pointer
  to the per-hook index and the generated operating matrix (the flat hook
  table it once held was retired in #1306)
- [`DESIGN.md`](../../DESIGN.md) — hook design contracts (structural
  tokenization, session_id keying, compound-bash cascade)
- [`ETHOS.md`](../../ETHOS.md) — why hooks exist; fail-open invariant
- [`CONTRIBUTING.md`](../../CONTRIBUTING.md) — adding-a-hook checklist
  (must update in Phase 2)
- `scripts/build-plugin-manifests.py` — current generator; rewritten in
  Phase 2
- `scripts/check-plugin-manifests.py` — current CI drift gate; extended
  in Phase 2
