# Hook Suitability Audit

A complement to [`hook-prune-audit.md`](hook-prune-audit.md). That audit asks
"does this hook fire?" against the fire-rate ledger and found nothing to drop.
This audit asks a different question: **is each hook appropriate for the
context it ships into?** — where "context" means (a) this repository as a
publicly distributed, multi-platform plugin (Claude, Codex, Cursor, OpenCode),
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

## A. Orphaned artifact — retired hook whose files still ship

`external-write-falsify-check` left `hooks/manifest.json` (last registered
2026-08-01, per `hook-prune-audit.md`'s exclusion list), but three artifacts
remain in the tree:

- `hooks/advisory-nudge/external-write-falsify-check/spec.md`
- `hooks/advisory-nudge/external-write-falsify-check/impl.py`
- `hooks/external-write-falsify-check.sh` — the *generated wrapper*, which
  should only exist for registered hooks

`grep -c external-write-falsify-check hooks/manifest.json` → 0. The wrapper
is dead code that the build should have removed; the spec is still
cross-referenced by `source-citation-probe-gate/spec.md` as live territory
("`external-write-falsify-check`'s author-exempt Check 2 territory"), so
siblings' documented coverage boundaries now point at a hook that never runs.

**Verdict: decide retire-fully or re-register.** If retired, delete the
wrapper, archive the spec (or mark it retired), and update the two siblings
whose specs delegate coverage to it. If the deregistration was accidental,
re-register. Either way the current half-state is the worst option:
`docs/hook/INDEX.md` and sibling specs describe enforcement that does not
exist.

## B. Hooks that cannot fire without components this repo does not declare

All of these fail open, so they are *harmless* — the cost is plugin weight,
dispatch overhead, and misleading documentation of coverage. None fires in a
stock Claude Code session without the named component:

| Hook | Missing component → behavior | Evidence |
| --- | --- | --- |
| `codex-review-route` | Matches only `^/codex(:\|-)review` prompts — inert without the openai-codex plugin; also needs `jq` + `gh` | `impl.sh:42` |
| `model-routing-advisory` | Recognizes only `cmux …`/`cmux-delegate` delegation argv | `impl.py:14-15` |
| `momentum-rule-retrieval-gate` (dispatch trigger only) | `cmux new-workspace` arm dead without cmux; merge/force-push arms still live | `impl.py:15` |
| `caller-probe-gate`, `source-citation-probe-gate` (MCP matcher only) | `mcp__.*slack.*\|mcp__.*notion.*` entries never match without those servers; Bash matcher still live | `hooks/manifest.json` |
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

The repository is public and packaged for five platforms, but several hooks
carry the author's private namespace as code, not config:

| Location | Asset | Note |
| --- | --- | --- |
| `preflight-gate/block-gh-issue-create-without-dup-search/impl.py:121` | `_PERSONAL_REPO_RE = ^devseunggwan/` | The blast-radius exemption only works for the author's namespace; every other installer gets the strict path unconditionally |
| `advisory-nudge/secret-print-redaction-advisory/impl.py` (fetch-CLI regex) | `hubctl token fetch` as first alternative | `hubctl` is an org-internal tool; the other alternatives (aws/vault/op/gh/kubectl) are public |
| `completion-verify/completion-signal-gate/impl.py` | `laplace-dev-hub`, `laplace-wiki`, `oh-my-claudecode`, `_KNOWN_FOREIGN_SKILLS` | Rule 2 (foreign-plugin slash command) is gated on cwd == praxis, so contained — but the namespace list is still personal |
| `advisory-nudge/model-routing-advisory/spec.md`, `merge-menu-review-options-advisory/impl.py` | `laplace-dev-hub:*`, `oh-my-claudecode:security-reviewer` named in emitted guidance | Advice text tells any installer to run plugins they do not have |
| `preflight-gate/side-effect-scan` (`wrapper-commit` category) | `iceberg-schema migrate/promote`, `omc ralph` | Author-toolchain command names in a shipped trigger table |
| `hooks/_lib/_memory_dir.py:49-50` | `/Users/nathan.song/.claude` in a docstring example | A real person's name in a public repo; cosmetic but should be a placeholder |
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
| A. Orphaned artifact | `external-write-falsify-check` | Retire fully (wrapper + spec + sibling cross-refs) or re-register |
| B. Dead without undeclared component | `codex-review-route`, `model-routing-advisory`, `momentum-rule-retrieval-gate` (cmux arm), MCP matchers ×2, `block-unmatched-glob`, `memory-hint`, `builtin-task-postuse` | Declare the dependency per hook; consider packaging-level exclusion |
| C. Hardcoded personal/org assets | `block-gh-issue-create-without-dup-search`, `secret-print-redaction-advisory`, `completion-signal-gate`, `model-routing-advisory`, `merge-menu-review-options-advisory`, `side-effect-scan`, `_lib/_memory_dir.py` | Move literals to config; author's values become author's config |
| D. Convention-premised default blocks | `block-commit-without-codex-review`, `pre-edit-protected-branch-guard`, `branch-name-check`, `block-pr-without-*-evidence` ×2, `commit-title-format-check` | Choose documented-defaults vs opt-in posture; resolve the §D guard/gate inconsistency |
| E. Host mismatch | `builtin-task-postuse` (+ #1153/#1154 set) | Fold into existing issues |
| F. Korean-only emitted bodies | `fallback-negative-warn`, `second-failure-advisory` | Bilingual advisory text |
| G. Spec self-contradiction | `pr-claim-mutation-gate` | One-line doc fix |

Nothing here contradicts `hook-prune-audit.md`'s "no hook meets the bar for
removal" — with one exception: `external-write-falsify-check` is already
removed from the manifest and only its corpse ships. Every other finding is a
suitability boundary (dependency, namespace, default posture, language), not
a fire-rate argument.
