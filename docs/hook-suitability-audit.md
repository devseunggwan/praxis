# Hook Suitability Audit

> **Snapshot.** Written in August 2026 against the roster and the five
> platform manifests of that time. Hook lists below are historical; the
> platform references were corrected on 2026-09-05 after the Gemini (#1221)
> and OpenCode (#1226) removals. The current roster is `hooks/manifest.json`.

A complement to [`hook-prune-audit.md`](hook-prune-audit.md). That audit asks
"does this hook fire?" against the fire-rate ledger and found nothing to drop.
This audit asks a different question: **is each hook appropriate for the
context it ships into?** — where "context" means (a) this repository as a
publicly distributed, multi-platform plugin (Claude, Codex, Cursor),
and (b) an installing environment that may lack the author's toolchain
(codex CLI, cmux, oh-my-claudecode, a `hookable:` memory store, zsh,
slack/notion MCP servers).

Method: every `spec.md` + `impl.{py,sh}` under `hooks/{advisory-nudge,
preflight-gate,completion-verify,postuse-correction}/` was read and classified
on five axes — external runtime dependencies, workflow/convention assumptions,
hardcoded personal/org assets, host restrictions, and escalation tier. A hook
is *unsuitable* here only when its premise cannot hold in the shipping
context, not when it merely encodes an opinionated rule (encoding the
author's CLAUDE.md rules is praxis's stated mission, per `ETHOS.md`).

Fire-rate verdicts are deliberately not re-litigated. Where a finding is
already tracked by an open issue, the issue is cited instead of re-reported.

## A. Opt-in hook whose siblings delegate coverage to it as if always-on

> **Correction (2026-08-30).** The first revision of this audit called
> `external-write-falsify-check` an *orphaned artifact* whose wrapper "the
> build should have removed". That was wrong: the hook is a **deliberate
> opt-in carve-out** — `scripts/constants.py:35` registers it in
> `OPT_IN_HOOKS`, `build-plugin-manifests.py:412-417` generates its wrapper
> on purpose, `check-plugin-manifests.py:9` names the carve-out, and
> `docs/hook/INDEX.md:122` documents it as "PreToolUse (opt-in)". The
> half-state is designed, not accidental.

What survives of the finding is narrower: **some sibling-spec lines delegate
coverage territory to an off-by-default hook without qualifying it.** Two
siblings already state the opt-in status in prose
(`source-citation-probe-gate/spec.md:17`, `caller-probe-gate/spec.md:132`),
but the load-bearing delegation and comparison lines did not:
`source-citation-probe-gate`'s two "author-exempt Check 2 territory"
exclusions, the related-hook table rows in `exclusion-probe-gate`,
`protected-paths-guard`, and `block-personal-asset-leak`, and
`cross-boundary-preflight`'s bypass rationale. A reader of those lines (or a
future audit — this one included) concludes the surface is covered; in a
default install it is not.

**Verdict: doc-only fix (applied with this audit).** The six delegation/
comparison sites above now carry "(opt-in, off by default)"; code-copy
mirror notes (`merge-state-claim-gate`, `version-bump-evidence-check`) were
left alone — mirrored code exists regardless of registration.

## B. Hooks that cannot fire without components this repo does not declare

All of these fail open, so they are *harmless* — the cost is plugin weight,
dispatch overhead, and misleading documentation of coverage. None fires in a
stock Claude Code session without the named component:

| Hook | Missing component → behavior | Evidence |
| --- | --- | --- |
| `codex-review-route` | Matches only `^/codex(:\|-)review` prompts — inert without the openai-codex plugin; also needs `jq` + `gh` | `impl.sh:42` |
| `model-routing-advisory` | Recognizes only `cmux …`/`cmux-delegate` delegation argv | `impl.py:14-15` |
| `momentum-rule-retrieval-gate` (dispatch trigger only) | `cmux new-workspace` arm dead without cmux; merge/force-push arms still live | `impl.py:15` |
| `block-unmatched-glob` | Verdict delegated to `zsh -f`; no zsh → vacuous pass | `impl.py:14-15`, `spec.md:82` |
| `memory-hint` | Permanent no-op without a memory dir using `hookable:`/`hookKeywords:` frontmatter | `impl.py:27-30` |
| `builtin-task-postuse` | Exists to correct **oh-my-claudecode** `pre-tool-enforcer` false positives; without omc there is nothing to correct | its `spec.md` |

**Verdict: keep, but declare.** The README's dependency-tier table covers
skills, not hooks. A `Requires:` line per affected hook entry (or a
`hosts`-style optional-dependency field in the manifest) would let
`check-plugin-manifests.py` verify the claim, and would let a future
packaging step exclude dead-matcher hooks from platforms that cannot satisfy
them. `builtin-task-postuse` is the strongest candidate for an explicit
omc-conditional: it ships `hosts: all` while its trigger condition is another
plugin's bug.

## C. Personal/org assets hardcoded into a publicly distributed plugin

The repository is public and packaged for three hook-installing platforms, but several hooks
carry the author's private namespace as code, not config:

| Location | Asset | Note |
| --- | --- | --- |
| `preflight-gate/block-gh-issue-create-without-dup-search/impl.py:121` | `_PERSONAL_REPO_RE = ^devseunggwan/` | The blast-radius exemption only works for the author's namespace; every other installer gets the strict path unconditionally |
| `advisory-nudge/secret-print-redaction-advisory/impl.py` (fetch-CLI regex) | `hubctl token fetch` as first alternative | `hubctl` is an org-internal tool; the other alternatives (aws/vault/op/gh/kubectl) are public |
| `completion-verify/completion-signal-gate/impl.py` | `laplace-dev-hub`, `laplace-wiki`, `oh-my-claudecode`, `_KNOWN_FOREIGN_SKILLS` | Rule 2 (foreign-plugin slash command) is gated on cwd == praxis, so contained — but the namespace list is still personal |
| `advisory-nudge/model-routing-advisory/spec.md`, `merge-menu-review-options-advisory/impl.py` | `laplace-dev-hub:*`, `oh-my-claudecode:security-reviewer` named in emitted guidance | Advice text tells any installer to run plugins they do not have |
| `preflight-gate/side-effect-scan` (`wrapper-commit` category) | `iceberg-schema migrate/promote`, `omc ralph` | Author-toolchain command names in a shipped trigger table |
| `hooks/_lib/_memory_dir.py:49-50` | a real personal `/Users/<name>/.claude` path in a docstring example | Fixed with this audit (R1) — replaced by a placeholder in code, spec, and test fixtures |
| four hooks (`pr-report-destination-gate`, `protected-paths-guard` exclusion, `external-write-falsify-check`, `postcompact-context` docs) | `.omc/plans/` scratch path | omc-convention path assumed to be where planning artifacts live |

`block-personal-asset-leak` is the counter-example done right: its class-2
personal-owner list comes from `PRAXIS_PERSONAL_REPO_OWNERS` (unset = inert),
no username in code.

**Verdict: migrate literals to config with the author's values as *their*
config, not the shipped default.** The `^devseunggwan/` regex is the clearest
case — it changes enforcement behavior per-installer and already has a
sibling pattern to copy (`PRAXIS_PERSONAL_REPO_OWNERS`). `hubctl` and the
`wrapper-commit` command names belong in an env-extensible list
(`PRAXIS_SECRET_FETCH_CLIS`, `PRAXIS_WRAPPER_COMMIT_CMDS` or similar). The
docstring name is a one-line fix.

## D. Default-blocking gates whose premise is a convention, not correctness

These block/deny **by default** and their trigger is an author-workflow
convention. For this environment they are working as designed; for any other
installer of the public plugin they block standard workflows out of the box.
The repo already contains both patterns — on-by-default and opt-in — for the
*same* rule, which is the inconsistency worth fixing:

| Hook | Default behavior in a convention-less repo | Existing escape |
| --- | --- | --- |
| `block-commit-without-codex-review` (hosts: claude) | Blocks **every content commit** unless `praxis:codex-review-wrap` ran this session | `[skip-codex-review]`, `CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE=1` |
| `pre-edit-protected-branch-guard` | Denies **every Edit/Write** while HEAD is on `main/dev/prod/master` (`impl.py:65`) — on by default | `PRAXIS_PBGUARD_SKIP=1` |
| `branch-name-check` | Denies branch creation not matching `^(hub\|issue)-[0-9]+-(feat\|…)-[a-z0-9-]+$` (`impl.py:71-73`) | `PRAXIS_BRANCH_NAME_REGEX`, `_STRICT=0` |
| `block-pr-without-caller-evidence` / `block-pr-without-precommit-evidence` | Deny `gh pr create` unless the body carries praxis-invented literal marker lines | env bypasses |
| `commit-title-format-check` | Blocks non-Conventional-Commits titles | `_STRICT=0` |
| `block-gh-issue-create-without-dup-search` | Blocks issue creation without a prior dup search; exemption hardcoded to `devseunggwan/` (see §C) | — |

Contrast: `worktree-edit-gate` enforces the *same* worktree rule as
`pre-edit-protected-branch-guard` but is explicitly opt-in ("This is opt-in
only — the hook must not interfere by default", `impl.py:30-31`), as are
`skill-gate-commands` (no shipped defaults) and `block-child-repo-issue-create`
(inert without `PRAXIS_HUB_MEDIATED_ORGS`).

**Verdict: pick one posture and state it.** Either (a) document that the
shipped defaults assume the author's full workflow and publish a
"minimal-profile" env preset for other installers, or (b) converge the
on-by-default conventions toward the opt-in pattern the newer hooks already
use. The `pre-edit-protected-branch-guard` / `worktree-edit-gate` pair
enforcing one rule under two opposite defaults is the concrete
inconsistency either option resolves.

## E. Host-suitability (already tracked — no new verdict)

The "hook installed on all hosts, premise only true on claude" family is
already filed: #1153 (`side-effect-scan`'s ADVISE demotion rests on a
claude-only sibling set) and #1154 (`verify-commit-flag-override`'s deny
checklist names hooks the host does not install). This audit found the same
shape in one more place: `builtin-task-postuse` ships `hosts: all` while its
premise (omc's enforcer misfiring on `Task*` tools) is Claude-ecosystem-only —
worth folding into the #1153/#1154 remediation rather than a separate issue.

## F. Language coupling on blocking tiers

Bilingual (KO+EN) matchers are by design and mostly advisory. Two places
where language coupling meets a **blocking** tier or user-facing output are
worth a deliberate decision, given five-platform public packaging:

- `negative-existence-verdict-gate` — Stop **block by default**, and its
  registered decision framings are Korean-dominant (`게이트 결과`, `게이트 판정`,
  `판정이 나왔`; English side only `acceptance`/`ac #`). For non-Korean
  sessions the gate is near-inert (harmless); the asymmetry just means the
  documented protection effectively exists in one language.
- `fallback-negative-warn` and `second-failure-advisory` — the emitted
  advisory bodies are Korean-only. A non-Korean installer receives guidance
  they cannot read at the exact moment the hook decided guidance was needed.

**Verdict: keep matchers bilingual; make *emitted* advisory bodies bilingual
(or English-with-Korean-detail), starting with the two Korean-only bodies.**

## G. Minor spec inconsistency

`completion-verify/pr-claim-mutation-gate/spec.md` states default-**block**
in its tier section but its closing summary says the hook "never blocks a
normal Stop in the default (advisory) mode". The impl blocks by default
(`PRAXIS_PR_CLAIM_ADVISORY=1` demotes). One sentence needs correcting.

## Summary

| Category | Hooks | Action |
| --- | --- | --- |
| A. Opt-in coverage delegation | `external-write-falsify-check` siblings ×6 | Qualify sibling-spec references with "(opt-in, off by default)" |
| B. Dead without undeclared component | `codex-review-route`, `model-routing-advisory`, `momentum-rule-retrieval-gate` (cmux arm), MCP matchers ×2, `block-unmatched-glob`, `memory-hint`, `builtin-task-postuse` | Declare the dependency per hook; consider packaging-level exclusion |
| C. Hardcoded personal/org assets | `block-gh-issue-create-without-dup-search`, `secret-print-redaction-advisory`, `completion-signal-gate`, `model-routing-advisory`, `merge-menu-review-options-advisory`, `side-effect-scan`, `_lib/_memory_dir.py` | Move literals to config; author's values become author's config |
| D. Convention-premised default blocks | `block-commit-without-codex-review`, `pre-edit-protected-branch-guard`, `branch-name-check`, `block-pr-without-*-evidence` ×2, `commit-title-format-check` | Choose documented-defaults vs opt-in posture; resolve the §D guard/gate inconsistency |
| E. Host mismatch | `builtin-task-postuse` (+ #1153/#1154 set) | Fold into existing issues |
| F. Korean-only emitted bodies | `fallback-negative-warn`, `second-failure-advisory` | Bilingual advisory text |
| G. Spec self-contradiction | `pr-claim-mutation-gate` | One-line doc fix |

Nothing here contradicts `hook-prune-audit.md`'s "no hook meets the bar for
removal". Every finding is a suitability boundary (dependency, namespace,
default posture, language, documentation accuracy), not a fire-rate
argument.

## Remediation plan

Per-category prescription, ordered by (behavioral impact × cost). Each item
is sized to the repo's one-issue-one-PR convention; verification plans
follow the negative-control discipline the existing issues use.

### R1 — `_lib/_memory_dir.py` docstring name (C, trivial — applied)

Applied with this audit: the real name at `hooks/_lib/_memory_dir.py:49-50`
and its echoes in `memory-hint/spec.md`, `test_memory_dir.py`,
`test_memory_hint.sh`, and `test_completion_signal_gate.py` were replaced
with a `jane.doe` placeholder — the dotted username is kept so the
double-dash slug point the example exists to make survives.

### R2 — personal-owner exemption becomes config (C, behavioral) — issue #1156

`block-gh-issue-create-without-dup-search/impl.py:121` replaces
`_PERSONAL_REPO_RE = ^devseunggwan/` with the owner list from
**`PRAXIS_PERSONAL_REPO_OWNERS`** — the exact env var
`block-personal-asset-leak` already reads for the same concept ("owners that
are mine"), so no new configuration surface is invented. Unset → no
exemption (everyone gets the strict path, the author included, until they
set their env). Verification: existing fixtures flip from
hardcoded-owner to env-injected owner; negative control — unset env must
reproduce today's strict behavior for a non-author repo.

### R3 — author-toolchain literals in trigger tables become extensible (C) — issue #1157

Same shape, two sites, one PR:

- `secret-print-redaction-advisory`: keep the public fetch-CLI alternatives
  (aws/vault/op/infisical/kubectl/gh); move `hubctl` out of the builtin
  regex into a comma-separated **`PRAXIS_SECRET_FETCH_CLIS`** extension env.
- `side-effect-scan` `wrapper-commit` category: move `iceberg-schema
  migrate/promote` and `omc ralph` into **`PRAXIS_WRAPPER_COMMIT_CMDS`**;
  shipped default empty (the category goes silent unless configured, which
  is correct — no one else has those wrappers).

Lower priority, explicitly deferred from this pass: the plugin-namespace
list in `completion-signal-gate` Rule 2 (already contained by its
`cwd == praxis` gate) and the reviewer names in
`model-routing-advisory` / `merge-menu-review-options-advisory` guidance
text (those hooks are claude-host and cmux-dependent anyway — R4 declares
that instead).

### R4 — declare component dependencies in the manifest (B) — issue #1158

Two mechanisms, matching what each dependency actually is:

1. **Host-expressible** → use the existing `hosts` field:
   `builtin-task-postuse` gets `hosts: ["claude"]` (its premise — omc's
   `pre-tool-enforcer` misfiring — is Claude-ecosystem-only). Fold into the
   #1153/#1154 remediation since it is the same "installed-sibling premise"
   family.
2. **Not host-expressible** (cmux, zsh, a `hookable:` memory store,
   slack/notion MCP servers, the openai-codex plugin) → add an optional
   **`requires`** array per manifest entry, mirrored by a `Requires:` line
   in each spec, verified by `check-plugin-manifests.py` exactly the way
   `Supported hosts:` already is. Runtime behavior does not change (these
   hooks already fail open); the field makes the dead-matcher cost visible
   and gives a future packaging step something to filter on.

Verification: checker fixture both directions (manifest-without-spec-line
and spec-line-without-manifest-field both fail); negative control — a hook
with no `requires` passes untouched.

### R5 — convention gates: strict-only-when-configured (D, decided — issue #1159)

The §D inconsistency needs one stated principle, and the candidate this
audit recommends is: **a convention gate blocks only when the convention is
locally attested — by explicit config or a detectable dependency; on
shipped defaults it advises.** Applied:

- `block-commit-without-codex-review` — detect whether
  `praxis:codex-review-wrap` / the codex plugin is actually installed;
  absent → advisory (or silent), present → block as today. Capability
  detection, no new config.
- `branch-name-check` — `PRAXIS_BRANCH_NAME_REGEX` set → deny as today;
  unset (shipped author regex) → advisory. The author sets one env var and
  loses nothing.
- `block-pr-without-caller-evidence` / `-precommit-evidence` — gate behind
  an explicit opt-in env (praxis-invented body markers are the least
  discoverable convention in the set), or demote to advisory-by-default.
- `pre-edit-protected-branch-guard` vs `worktree-edit-gate` — resolve the
  opposite-defaults pair by **keeping the guard default-on** (protecting
  `main` is defensible beyond this author; the escape hatch exists) and
  documenting the two-tier design: guard = generic safety net, gate =
  opt-in strict worktree workflow. Alternative if the principle is applied
  uniformly: guard also demotes to advisory when `PRAXIS_PROTECTED_BRANCHES`
  is unset — flag both options in the issue; this audit leans keep-on.
- `commit-title-format-check` — Conventional Commits is common enough that
  block-by-default is defensible; leave as-is, document
  `_STRICT=0` in the README defaults table.

Alternatives considered: (a) status quo + a documented "minimal profile"
env preset — cheapest, but leaves every new installer's first commit
blocked; (b) converge everything to opt-in — regresses the author's own
protection. The principle above is the middle that costs the author one-time
env configuration and costs installers nothing.

Decision recorded 2026-08-30 (maintainer): the strict-only-when-configured
principle is adopted, with `pre-edit-protected-branch-guard` kept default-on
as the documented two-tier exception — see issue #1159.

### R6 — bilingual advisory bodies (F) — issue #1160

`fallback-negative-warn` and `second-failure-advisory` emit Korean-only
bodies. Prepend a one-line English summary above the Korean detail
(pattern already used by the bilingual matcher hooks). Fixture updates are
the whole cost. The `negative-existence-verdict-gate` framing asymmetry is
recorded as known-and-accepted (near-inert in non-Korean sessions is
harmless); widening its English framing set is optional follow-up, not
remediation.

### R7 — doc corrections (A + G)

One docs PR: qualify the six sibling-spec references to
`external-write-falsify-check` with "(opt-in, off by default)" (§A), and fix
the `pr-claim-mutation-gate/spec.md` sentence that contradicts its own
default-block tier (§G).

### Sequencing

| Order | Item | Why this order |
| --- | --- | --- |
| 1 | R7 + R1 | Doc/docstring only, zero risk, closes the audit's own false lead |
| 2 | R2 | Smallest behavioral fix, reuses an existing env var |
| 3 | R4 | Pure metadata + checker; unblocks packaging decisions |
| 4 | R3 | Trigger-table config extraction, same tested shape as R2 |
| 5 | R5 | Decision recorded in #1159; per-hook PRs land last |
| 6 | R6 | Cosmetic, no urgency |
