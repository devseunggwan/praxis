# Stage 4 execution procedures (retrospect)

Full per-action procedures for [`../SKILL.md`](../SKILL.md) Stage 4. SKILL.md
holds the action map and the non-negotiable gates; this file holds the
complete procedure text — templates, prompt variants, resolution tables, the
per-artifact verification matrix, and the completion-report format. **Read
this file before executing any approved action** — the embedded gates
(Action 1 duplicate-check, Action 3 Global-path staging, and the
cross-boundary write gate's Step 0 / Step 0a — shared by Action 2 and
Action 4) live here in full.

## Cross-boundary write gate (shared by Action 2 and Action 4)

**Scope: every finding row whose Proposed Actions contain `issue` or
`upstream_feedback`.** Both actions end in a `gh issue create` against a repo
the executor *resolved* rather than typed, so both carry the same misrouting
and cross-boundary-write risk. Action 2 is not exempt because "it's our own
repo": since issue #993 the criterion is repo *visibility*, not ownership — a
write to a **public** backing repo needs per-action prior approval even when
the owner is your own handle or org.

Run **Step 0** then **Step 0a**, in that order, **before any mutation**.
Neither action may reach `gh issue create` with either step unrun; Stage 3's
"✅ Execute now" authorizes the action category, never the specific write.

Throughout this section `{gated_action}` is the action this run is gating —
`issue` or `upstream_feedback`. Every prompt below MUST name that action
literally, because the pick removes exactly that action from the finding's
action set; naming the other one drops the wrong action.

### Step 0 — Backing-repo verification gate (MUST run before any mutation)

This gate is the first procedure step for every `issue` row and every `upstream_feedback` row, executed **before** any of the resolution-table lookups below. Skipping it means the most salient file path in the executor's local context (often the working project repo) wins the routing decision — which is the exact failure mode this gate prevents.

1. **Read the declaration.** Parse `backing_repo: <owner/repo>` from the finding's Rationale cell (Stage 2 step 7 makes this MANDATORY for `issue` and `upstream_feedback` rows; Stage 3 surfaces it). If the declaration is absent → ABORT this action and return the finding to Stage 2 step 7 with prompt: `"Finding #N {gated_action} row missing backing_repo declaration — re-run Stage 2 step 7."`

2. **Re-resolve from source-of-truth.** Independently of the declaration, re-resolve the backing repo using the resolution table below. Do NOT use the declared value as the lookup input — use the tool/layer signal from Stage 2 step 4b to derive the repo from scratch. An `issue` row usually carries no tool/layer signal — its target is the working project repo — so re-resolve it from the checkout the finding came from instead: `git -C <project> remote get-url origin`, or `gh repo view --json nameWithOwner -q .nameWithOwner`. Capture the re-resolved value as `live_backing_repo`. If the resolution table's `Other / ambiguous` row matches the layer (no concrete repo derivable), treat `live_backing_repo = AMBIGUOUS` and skip to step 0.4 with a 2-way prompt instead of 3-way.

3. **Compare.** If `live_backing_repo == declared backing_repo` → proceed to **Step 0a below**, never straight to the action's own procedure. Step 0a is the second half of this gate, not part of Action 2 or Action 4: routing from here into the action's bullets is exactly how an `⚠ EXTERNAL`-marked row reaches `gh issue create` with no per-action approval. Normalization rules for equality (apply both sides):
   - Strip leading/trailing whitespace
   - Strip trailing `.git`
   - Treat all of these as equivalent forms of the same repo: `owner/repo`, `https://github.com/owner/repo`, `git@github.com:owner/repo`, `ssh://git@github.com/owner/repo`
   - Case-insensitive on `owner` and `repo` (GitHub treats them case-insensitively for routing)

4. **Divergence / ambiguity handling.** If `live_backing_repo != declared backing_repo` (after normalization) → ABORT and surface to user via `AskUserQuestion`. Two prompt variants:

   **(i) Both sides concrete repos — 3-way prompt:**

   ```text
   ⚠ Backing-repo divergence on Finding #N:
      Stage 2/3 declared:    {declared}
      Stage 4 re-resolved:   {live}

   어느 쪽이 정확합니까?
   [a] declared ({declared}) 으로 진행
   [b] re-resolved ({live}) 으로 진행 (Stage 2 declaration 정정)
   [c] 이 finding 은 skip — {gated_action} 액션 제거
   ```

   **(ii) Re-resolution returned `AMBIGUOUS` (declared is concrete) — 2-way prompt:**

   ```text
   ⚠ Backing-repo re-resolution ambiguous on Finding #N:
      Stage 2/3 declared:    {declared}
      Stage 4 re-resolved:   AMBIGUOUS (resolution table's `Other / ambiguous` row)

   어느 쪽으로 진행할까요?
   [a] declared ({declared}) 으로 진행 (사용자가 Stage 2에서 결정한 값을 신뢰)
   [b] 이 finding 은 skip — {gated_action} 액션 제거
   ```

   **(iii) Declared was `AMBIGUOUS` but re-resolution found a concrete value — 2-way prompt:** mirror of (ii) with `[a]` = use re-resolved, `[b]` = skip.

   Do NOT proceed without an explicit pick. `[b]` (in variant i) requires updating the declared `backing_repo` line — record the corrected value in the Actions Executed report's verification trail rather than re-emitting the entire Stage 3 report (the report is append-only post-Stage-3; corrections live in step 0.5's trail). The skip path removes `{gated_action}` from the row's action set and logs the divergence reason in the Actions Executed section.

5. **Verification trail.** Record both values + the chosen path in the Actions Executed report (e.g., `Finding #N: backing_repo verified (declared=live=<resolved-praxis-repo>)` or `Finding #N: divergence resolved via [b] — switched declared <X> → re-resolved <Y>`). This trail is the defense against silent misrouting in retrospective analysis.

### Backing repo resolution (used by step 0.2 and as reference)

| Tool name / layer pattern | Backing repo resolution |
| --- | --- |
| `mcp__<plugin>__*` from a Claude Code plugin | Read `repository` field from that plugin's `.claude-plugin/plugin.json` (or equivalent manifest) |
| `mcp__<service>-*` from a custom/team MCP server | The MCP server's source repo — `git remote -v` of the server's directory, or read its package manifest |
| Skill within the praxis distribution itself | The praxis source repo this skill was installed from — read `repository` field in praxis's own plugin manifest |
| Hook in `~/.claude/hooks/` or a globally symlinked `~/.claude/CLAUDE.md`/`AGENTS.md` | The user's dotfiles backing repo — resolve via `ls -la` symlink chain, then `git remote -v` of the target dir |
| CLI tool (e.g., `gh`, `kubectl`) | The CLI's open-source upstream if accessible; otherwise `note only` |
| Builtin tool (Read/Edit/Bash/Grep) | Typically not actionable — `note only` |
| Working project repo (the usual `issue` row target) | `git -C <project> remote get-url origin`, or `gh repo view --json nameWithOwner -q .nameWithOwner` run in the checkout the finding came from |
| Other / ambiguous | Ask the user; do NOT fall back to a hardcoded repo |

If the active project's `AGENTS.md` provides a feature-to-repo mapping, consult it before deciding a repo.

### Step 0a — External-repo authorization gate (MUST run before `gh issue create` for external findings)

This gate fires for every `issue` row and every `upstream_feedback` row where the finding's Rationale cell contains `⚠ EXTERNAL: per-action approval required at Stage 4` (set by Stage 2.5 Gate-4). It fires **even when** the user selected "✅ Execute now" in Stage 3 — Stage 3 approval authorizes the action category, not the specific external-org write.

**Detection — two independent triggers. Either one fires this gate.**

1. **The marker.** The Rationale cell carries the literal prefix `⚠ EXTERNAL: per-action approval required at Stage 4`.
2. **The verified repo is public.** `verified_backing_repo` — Step 0's output, not the declared value — resolves public:

   ```bash
   gh repo view <verified_backing_repo> --json visibility -q .visibility
   ```

Trigger 2 exists because the marker is **stale by construction whenever Step 0 diverged.** Stage 2.5 Gate-4 computes the marker from the *declared* `backing_repo` and its declared `repo_visibility`. Step 0 re-resolves independently and its variant `[b]` path explicitly corrects the declared line, so a row declared private that resolves to a public repo carries no marker and would reach `gh issue create` with no per-action approval — the exact policy #993 / #1024 fixed, defeated by a divergence rather than by a missing gate.

**Fail closed.** If the visibility query errors, times out, or returns anything other than `PRIVATE` / `INTERNAL`, treat the repo as public and fire the gate. This is an authorization gate: an unanswerable question is not permission. Record the failure in the approval log line.

If neither trigger fires → skip to the gated action's issue-creation steps (Action 2's bullets, or Action 4's "Then create the issue").

**Mandatory `AskUserQuestion` prompt (do NOT proceed without explicit `[a]` pick):**

```text
⚠ External-repo write authorization required — Finding #N

Proposed: create GitHub issue in {verified_backing_repo}  ← Step 0's verified value, the repo `--repo` will target
Trigger: {marker | public-visibility recheck | visibility unknown → failed closed}
Title: {proposed_issue_title}
Evidence: {one-line friction event summary}

Per global `~/.claude/CLAUDE.md` "External / third-party repo content isolation (MUST)", this requires
explicit per-action approval. Stage 3 "Execute now" does not satisfy this gate.
Auto-mode override, batch approval, and "prior selection ratifies this" inferences
are all invalid — only an explicit [a] pick here allows proceeding.

어느 쪽으로 진행할까요?
[a] 승인 — {verified_backing_repo}에 이슈 생성 진행
[b] Skip — {gated_action} 액션 제거 (이 finding의 action set에서 제외)
[c] Issue draft를 먼저 검토한 뒤 결정 (draft를 보여준 뒤 재질문)
```

- If `[a]` → log the approval in the Actions Executed report's verification trail (`Step 0a: [a] approved <verified_backing_repo> for finding #N (trigger: <marker|visibility|failed-closed>)`), then proceed to `gh issue create`. The verification matrix requires that line; without it the approval leaves no record and the matrix row cannot be satisfied.
- If `[b]` → remove `{gated_action}` from this finding's action set; log reason in Actions Executed report
- If `[c]` → show the draft issue body (title, labels, full body text) and re-issue this AskUserQuestion
- No other pick proceeds.

## Per-action procedures

For each approved action:

1. **MEMORY.md feedback** → Write to `$CLAUDE_CONFIG_DIR/projects/.../memory/` with proper frontmatter. This action processes two input streams:

   **Friction-event origin** (existing behavior): finding row with `memory` in Proposed Actions. Write MEMORY.md entry per existing convention (Type: `feedback`, include: rule, why, how to apply). Description uses the standard feedback format.

   **Success-pattern origin** (new): `successful_patterns` row where `reinforce_action: memory`. Write MEMORY.md entry with:
   - Type: `feedback`
   - Description MUST start with `Reinforced — <pattern>` (the `Reinforced —` prefix distinguishes positive reinforcement entries from friction entries in future retrospect scans)
   - Evidence: from the `successful_patterns.evidence` field
   - How to apply: describe how to intentionally replicate the successful pattern

   Per-entry workflow:
   1. For each finding with `memory` action → write MEMORY.md entry per existing convention.
   2. For each `successful_patterns` row with `reinforce_action: memory` → write MEMORY.md entry with description starting `Reinforced — <pattern>`, evidence link from the `successful_patterns.evidence` field.

   - Update `MEMORY.md` index (for both origins)

   **Frontmatter contract — `memory-hint` opt-in (mandatory consideration)**

   In addition to standard fields (`name`, `description`, `type`), every new memory MUST evaluate whether to include the `memory-hint` opt-in fields — `hookable` and `hookKeywords`. The full field spec (parser semantics, matching rules, fail-open contract) lives in `hooks/advisory-nudge/memory-hint/spec.md`; this section defines the authoring-time decision. The parser ignores `type` entirely and reads `hookable` / `hookKeywords` / `hookEvents` regardless of nesting depth (the regexes are indentation-tolerant), so place `type:` per your host's memory convention — this repo's on-disk memories nest taxonomy fields under `metadata:`. Only the opt-in fields' spelling and single-line list form matter for the hook.

   **Canonical schema (issue #942).** The taxonomy block below is the full
   field set this repo's memories converge on. Nesting these fields under
   `metadata:` (vs. leaving them top-level) has **no runtime effect** —
   `hooks/advisory-nudge/memory-hint/impl.py`'s regexes match either
   position — so the *only* reason to enforce nesting is cross-entry
   consistency: a reader (human or a future retrospect scan) should not have
   to check two positions per field. `scripts/check-memory-frontmatter.py`
   lints this shape and is wired into CI (see its docstring for what it does
   and does not catch).

   ```yaml
   ---
   name: my-memory
   description: Short rule statement
   metadata:                                # this repo nests taxonomy fields here
     node_type: memory                      # optional; no hook reads it (grep-verified, issue #942) — carry it when present, don't backfill it when absent
     type: feedback
     originSessionId: <uuid>                # session the memory was authored in
     hookable: true                         # opt into PreToolUse surface
     hookKeywords: [keyword1, keyword2]      # flat single-line list, whole-token (case-sensitive)
     hookEvents: [Bash, Edit]               # optional; default [Bash] when omitted
     modified: <ISO-8601>                   # optional; set on later edits
   momentum: [merge]                        # opt-in, top-level — NOT a taxonomy field, see below
   ---
   ```

   **`momentum:` stays top-level — a deliberate exclusion, not a residual gap.**
   `momentum` is a *different* opt-in mechanism (consumed by
   `hooks/advisory-nudge/momentum-rule-retrieval-gate/impl.py`'s
   `MOMENTUM_RE`, not by the memory-hint hook's `hookKeywords`/`hookEvents`)
   with its own independent hook consumer — it answers "surface this memory
   at a high-momentum action point" rather than "what kind of memory is
   this", so it does not belong in the `metadata:` taxonomy block
   conceptually. Practically: every existing `momentum:` entry (3/3, as of
   issue #942's scan) is already top-level, so nesting it would be swimming
   against its own 100%-consistent convention rather than fixing drift.
   `scripts/check-memory-frontmatter.py` deliberately excludes `momentum`
   from the fields it checks — this is the schema decision that exclusion
   encodes, not an oversight.

   **`node_type: memory` is optional, not required.** `grep -rn node_type`
   across every hook and script in this repo (issue #942) finds no
   consumer at all — unlike `type`/`hookable`/`hookKeywords`/`hookEvents`,
   nothing reads it. It is present on most existing entries (a fixed literal
   from an earlier authoring convention) but its absence is inert: two
   entries lack it entirely and `scripts/check-memory-frontmatter.py`
   reports them clean, correctly, because there is nothing to enforce.
   When authoring or normalizing a memory, carry `node_type: memory` if
   it's already there — don't add it to a file that lacks it, and don't
   treat its absence as drift.

   **Category-based default (apply unless rationale documented):**

   | Category | `hookable` default | Rationale |
   | --- | --- | --- |
   | behavioral retrieval-critical (silent-recurrence likely; failure mode is "Loaded ≠ Retrieved") | `true` | hook is the only structural enforcement; skill/memory alone fails retrieval at action time |
   | success-pattern reinforcement (`Reinforced —` prefix) where intentional replication needs same retrieval surface | `true` | same "retrieve at action time" need applies in reverse |
   | abstract / meta / cross-cutting principle (no concrete action signal) | `false` | keyword match would be noisy across unrelated commands |
   | author-generated rule (belongs in `~/.claude/CLAUDE.md` draft) or upstream-feedback note | `false` | not action-gateable; memory is a holding pen, not the enforcement surface |

   When uncertain → default `hookable: false` (safer to omit than to add noise) AND record the uncertainty in the Actions Executed report so a future retrospect can re-evaluate.

   **`hookKeywords` selection rules:**
   - Choose tokens that appear in the *action* the memory is meant to gate — CLI subcommand (`merge`, `close`), tool name (`Edit`, `cmux-delegate`), distinctive flag (`--force`), or domain identifier (`Closes`, `Recommended`).
   - Whole-token, case-sensitive matching only (per `hooks/advisory-nudge/memory-hint/spec.md`). List multiple casings explicitly if needed (`[Edit, edit]`).
   - 1–4 keywords typical; >5 raises false-positive risk linearly.
   - **Avoid generic English words** (`add`, `run`, `test`, `update`) — they fire on unrelated commands and erode the hint signal.
   - **`hookKeywords` must be a flat single-line list** (`[a, b]`). Multi-line YAML-block form (`- item` on separate lines) and scalar form (`hookKeywords: foo`) are silently skipped — the entire memory is then dropped (not indexed at all) and the hint never fires. Verify the list is single-line before committing. **This is not hypothetical** — issue #942's full-corpus scan found two `hookable: true` entries (`feedback_hook_flag_file_heredoc_timing.md`, `feedback_merge_ask_ci_comments_precheck.md`) shipped in the block-list form, so their hints had never fired despite `hookable: true` reading as "on". `scripts/check-memory-frontmatter.py` now catches this shape.
   - When the memory targets a non-Bash event, add `hookEvents:` to opt in — the memory-hint hook (`hooks/advisory-nudge/memory-hint/impl.py`) supports `[Bash, Edit, Write, NotebookEdit, AskUserQuestion]` (default `[Bash]` when omitted). Unsupported tool names in the list are dropped; if every listed event is unsupported the parser keeps the `[Bash]` default.

   **Why cycle 17's finding recurred instead of closing (issue #942):** the
   Stage 1.5 hygiene cursor (`.omc/state/retrospect-hygiene-cursor.json`,
   gitignored local state) shows cycle 17 recorded the schema-drift family as
   a carried `cycle_note` string only — no `gh issue create` action ran, so
   the finding had nowhere to be closed *against*. It sat in the cursor's
   carry-forward note, silently grew from 2 files to 4 across cycle
   17→18, and only became an issue (#942) once cycle 18 explicitly promoted
   it. The gap this exposes: a carried `cycle_note` has no re-verification
   path of its own (no CI, no lint, no issue to close) — `scripts/check-memory-frontmatter.py`
   is the structural fix, but only on a contributor's own machine: the
   memory directory it lints is a local, gitignored, per-user store that is
   structurally absent in CI, so `scripts/run-tests.sh`'s call to it always
   prints `N/A` and exits 0 there (verified — F3, issue #942 codex-review
   pass). **CI cannot catch the next drift of this family; only a local
   `run-tests.sh` run, or a contributor running the script directly,
   catches it.**

   **That split is the intended design, not a residual gap (issue #975).**
   The re-verification path has two halves and only one of them is
   closable. The script's *logic* is already enforced on every push:
   `tests/test_check_memory_frontmatter.py` covers 97% of its statements
   (100 statements, 3 missed — the clean-corpus `OK:` print, its `return
   0`, and the `sys.exit(main())` entry guard; re-measured under
   `coverage` for issue #975) and runs inside `run-tests.sh`'s blocking
   pytest step, which `.github/workflows/ci.yml` executes on every PR. What
   stays opt-in is only the *scan of the real store*, and that half cannot
   be closed by writing more tests: its subject is a gitignored per-user
   memory directory that by construction never enters the repo, so CI is
   missing an **input**, not a check. Substituting an in-repo fixture
   corpus would not close it either — it would re-exercise already-covered
   logic against a second hand-maintained corpus that must stay
   schema-clean forever, inside a repo whose existing memory fixtures
   (`tests/fixtures/memory-hint/`) are deliberately drifted on purpose, so
   pointing this lint at them reports violations by design. Read the `N/A`
   line as correct output for an absent input, not as a gap awaiting
   closure.

   **⚠️ MANDATORY: Duplicate check before creating any memory file:**

   **Precondition:** This check applies ONLY when the finding's action type is `memory` (new pattern). If Stage 2 already marked `repeat=true` and escalated to issue/hook/global `~/.claude/CLAUDE.md` draft, skip this check — the escalation ladder takes precedence over merge.

   a. Reuse Stage 2 step 6's repeat scan results — if a finding matched an existing memory but was NOT escalated (i.e., it's a genuinely new sub-pattern), that file is the merge target
   b. If no Stage 2 match: scan MEMORY.md index for entries with overlapping root cause or topic (concept-level, not keyword)
   c. For each candidate, read the existing memory file and compare:
      - Same root cause / principle → **merge**: append new context (examples, How to apply items) to the existing file. If merge makes this the 2nd+ occurrence, re-evaluate whether action type should escalate per Stage 2 step 7
      - Related but distinct principle → **create new file** (genuinely different insight)
   d. **Never create a new file when the insight is a specific instance of an existing general rule** — add it as a numbered sub-item instead
   e. After merge or create, update MEMORY.md index (update description if merged, add new line if created)

2. **GitHub issue** → Use project's issue creation skill or `gh issue create`

   **Gate:** run the [Cross-boundary write gate](#cross-boundary-write-gate-shared-by-action-2-and-action-4) with `{gated_action} = issue` — Step 0 (backing-repo verification), then Step 0a (external-repo authorization). Both steps apply to `issue` rows exactly as they do to `upstream_feedback` rows: an `issue` row whose verified backing repo is public carries the `⚠ EXTERNAL: per-action approval required at Stage 4` marker from Stage 2.5 Gate-4 even when the owner is your own org, and Stage 3's "✅ Execute now" does not satisfy it. No `issue` row may reach `gh issue create` with either step unrun.

   **Order:** draft the title and body FIRST, then run the gate, then call `gh issue create`. The gate must precede the **mutation**, not the drafting — Step 0a's prompt renders `{proposed_issue_title}` and its `[c]` branch shows the full draft body, neither of which exists before the draft is written. Drafting touches no remote, so nothing is at risk in that order.

   - Title: Conventional Commits format (per project convention)
   - Body: per project convention, with background + task list
   - **Now run the gate** (Step 0 → Step 0a) with the draft in hand.
   - Command: `gh issue create --repo <verified_backing_repo> --title "$TITLE" --body "$BODY"` — always pass `--repo` with the repo Step 0 verified. A bare `gh issue create` silently targets whichever checkout the shell happens to be in, discarding the Step 0 result and skipping the `cross-boundary-preflight` hook, which keys on `--repo`.
   - **Hub-mediated orgs:** the mandatory `--repo` newly exposes this call to `block-child-repo-issue-create`, which hard-blocks (exit 2) when `PRAXIS_HUB_MEDIATED_ORGS` names the verified repo's org and the repo is not that org's hub. A bare `gh issue create` used to pass that hook by carrying no `--repo` at all. When the verified backing repo is a child repo of a hub-mediated org, route through the org's hub creation skill instead of `gh issue create` — do not drop `--repo` to get past the block, and do not set `PRAXIS_HOOK_BYPASS_HUB_ENFORCE`. Praxis itself configures no hub-mediated org, so this branch is inert by default.
   - **Verification (mandatory):** issue URL is returned, `gh issue view {url}` succeeds, AND the URL's repo matches the verified backing repo (catches misrouting)

3. **Global `~/.claude/CLAUDE.md` draft** → Write proposed rule addition as a markdown block, routed by target

   **Step 0 — Target detection (MUST run first):**
   Classification is two-stage:
   1. **Global check uses either the input path OR `realpath` equivalence to the canonical config path**. A dotfiles-symlinked `~/.claude/CLAUDE.md` whose `realpath` resolves outside `~/.claude/` is still the user's own global config — and a finding that directly declares the resolved dotfiles backing path must still route to the Global flow.
   2. **Project vs External uses `realpath`** of the input. This correctly routes both `AGENTS.md` (regular file) and project-local symlinks such as praxis's own `CLAUDE.md → AGENTS.md` (whose `realpath` lands on a file inside cwd) to the Project path.

   | Target | Detection | Execution path |
   | -------- | ----------- | --------------- |
   | **Global `~/.claude/CLAUDE.md`** | EITHER the input path equals `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/CLAUDE.md` (after `$HOME`/`~` expansion) OR `realpath <input>` equals `realpath ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/CLAUDE.md` (i.e., the finding declared the resolved dotfiles backing file path directly — still the user's own global config, must take the Global flow). | Staging → AskUserQuestion → apply only on explicit approval (see Global path below) |
   | **Project `AGENTS.md`** | Not Global AND `realpath <path>` resolves inside cwd. Covers `AGENTS.md` directly and project-local symlinks like `CLAUDE.md → AGENTS.md`. | Direct Edit (see Project path below) |
   | **External-repo rule file** | Not Global AND `realpath <path>` resolves outside cwd AND outside `~/.claude/` | Same as external-repo gate — do NOT edit; surface to user with resolved path |

   **Project path** (input is project `AGENTS.md`):
   - Present the draft diff to the user inline
   - Apply with explicit approval ("yes, add this rule") → Direct Edit

   **Global path** (input equals `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/CLAUDE.md`):
   ⚠️ Global scope — changes affect every project. Claude Code's self-modification classifier blocks direct Edit/Write without explicit approval.

   a. **Stage draft**: write the proposed rule block to `/tmp/claude-md-draft-{slug}.md` (use `.omc/plans/claude-md-draft-{slug}.md` as fallback when `/tmp/` is not writable). Present the full draft content inline before showing the prompt.

   b. **AskUserQuestion — 3-option prompt**:

      ```text
      options:
        apply  — 승인. 지금 바로 적용합니다.
        수정   — 변경할 내용을 free-text로 입력하면 재작성 후 다시 이 단계로 돌아옵니다.
        보류   — 이번 세션은 적용하지 않습니다. 스테이징 파일을 남겨둡니다.
      ```

      - `수정` 선택 시: "무엇을 바꿀까요?" 입력 받음 → re-draft → 다시 (b) 단계로 복귀.
        Cap: 최대 3 라운드. 3 라운드 초과 시: "3회 재작성을 초과했습니다. 수동 편집을 권장합니다: `{staging_path}`" 후 보류 처리.
      - `보류` 선택 시: Edit 호출 없이 staging 파일 경로를 completion report에 기록하고 종료.

   c. **`apply` 선택 시**: Resolve the actual Edit target via `edit_target="$(realpath "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/CLAUDE.md")"` first — the builtin `Edit` tool refuses to write through a symlink, so the resolved path is the only writable target in symlinked dotfiles environments. Edit `$edit_target` to insert the approved rule at the indicated position. Show the resulting diff as verification.

4. **Upstream feedback** → Resolve the tool's **backing repo first** (do NOT hardcode any specific repo), then create a labeled issue there. Hardcoding misroutes plugin defects, custom MCP defects, dotfiles defects across user environments.

   **Gate (MUST run first):** run the [Cross-boundary write gate](#cross-boundary-write-gate-shared-by-action-2-and-action-4) with `{gated_action} = upstream_feedback` — Step 0 (backing-repo verification), then Step 0a (external-repo authorization). That section's resolution table is the source-of-truth lookup for step 0.2.

   **Then create the issue (using the verified backing_repo from step 0):**
   - Title: `{type}({tool_layer}): {friction description}` (Conventional Commits format)
   - Label: `tool-friction:{layer}` is praxis's own convention. Apply it ONLY when the verified backing repo is the praxis distribution itself. For any other backing repo, use that repo's existing label conventions (e.g., `bug`, `enhancement`); do NOT auto-create praxis-style labels in unrelated repos.
   - If `tool-friction:*` is needed and missing in the praxis repo: `gh label create "tool-friction:{layer}" --repo <verified-praxis-repo>`
   - Body: include evidence, expected behavior, proposed fix direction from step 4b finding
   - Command: `gh issue create --repo <verified_backing_repo> --title "$TITLE" --label "$LABEL" --body "$BODY"` — substitute the verified repo, never hardcode
   - **Verification (mandatory):** issue URL is returned, `gh issue view {url}` succeeds, AND the URL's repo matches the verified backing repo (catches misrouting)

5. **Skill idea note** → Write to `{current_project}/.omc/plans/retrospect-skill-idea-{slug}.md`
   - `{current_project}` = `$CLAUDE_PROJECT_DIR` or `git rev-parse --show-toplevel`
   - Include: problem, proposed skill trigger, pipeline sketch

6. **Hook code** → For enforcement-level actions (repeat 3x+):
   a. **Write the hook script.** First resolve the target repo (the project's
      `.claude/hooks/`, or a personal-config / dotfiles repo when the hook backs
      `~/.claude/`) and check its current branch: `git -C <repo> branch --show-current`.
      - **On a protected branch (`main` / `dev` / `prod` / `master`)**: an inline
        `Write` here is blocked by `pre-edit-protected-branch-guard` (it guards
        Edit/Write on protected branches — both the dirty-tree and the clean-tree
        PR-workflow-signal paths). Create a dedicated worktree on a new branch and
        write the hook inside it:

        ```bash
        git -C <repo> worktree add -b retrospect-hook-{slug} \
            <repo-parent>/<repo-name>.retrospect-hook-{slug} <protected-branch>
        ```

        Surface the worktree path to the user for the commit / PR decision —
        retrospect does NOT auto-commit or auto-PR the hook; its contract ends at
        the file write.
      - **Already on a feature branch**: write the hook directly to
        `.claude/hooks/` or the appropriate location.
   b. Present the hook code to user for review
   c. Explain how to register in `.claude/settings.json` (show the exact JSON entry)
   d. Use AskUserQuestion: "Hook을 settings.json에 등록할까요?" (✅ 등록 / ⏭ 파일만 유지 / 🕐 나중에)
   e. If approved: Edit `.claude/settings.json` to register the hook (inside the
      step-(a) worktree when one was created — `settings.json` shares the same
      protected-branch repo as the hook file)
   f. If skipped/deferred: leave the hook file in place and provide manual registration instructions

7. **Verification** — For each executed action, verify the artifact:

   | Artifact | Verification |
   | ---------- | ------------- |
   | MEMORY.md feedback (new) | File exists + MEMORY.md index updated + `hookable`/`hookKeywords` frontmatter decision recorded (true with keywords, OR false with rationale in Actions Executed report) + if the memory targets a non-Bash tool, confirm `hookEvents` lists that tool (a missing/mistyped `hookEvents` silently reverts to `[Bash]` and the hint never fires on the intended event) |
   | MEMORY.md feedback (merged) | Existing file updated (diff shown) + MEMORY.md index description updated if needed + if existing entry had `hookable: false` **or the field is missing entirely** and merged context now meets the retrieval-critical default, re-evaluate and add/update frontmatter (most pre-existing memories lack `hookable` — missing field is the dominant case, not false) |
   | GitHub issue | `gh issue view {url}` returns valid data + URL repo matches `verified_backing_repo` from Step 0 + if Step 0a fired, the `[a]` approval is logged in the Actions Executed report |
   | Upstream feedback | `gh issue view {url}` returns valid data + URL repo matches `verified_backing_repo` from step 0 + if Step 0a fired, the `[a]` approval is logged in the Actions Executed report + label convention is correct for the verified repo (`tool-friction:{layer}` ONLY when verified repo is the praxis distribution; otherwise the repo's own convention label per Action 4's label rule) |
   | Hook code | Script file exists + settings.json registration confirmed (dry-run varies by hook type — no generic check). If Action 6 step (a) created a worktree, report the worktree path and confirm the file exists there. |
   | Global `~/.claude/CLAUDE.md` draft | **Project target (`AGENTS.md`)**: Diff shown + explicit approval received + Edit applied. **Global target (`~/.claude/CLAUDE.md`)**: Staging file created at `/tmp/claude-md-draft-{slug}.md` → AskUserQuestion 3-option presented → `apply`: Edit applied + diff shown; `보류`: staging file path logged in completion report. |
   | Skill idea note | File exists in `.omc/plans/` |

   Report verification results in the completion table.

8. **Completion report:**

```markdown
## Actions Executed

| # | Action | Result |
|---|--------|--------|
| 1 | MEMORY.md feedback added | ✅ {file_path} |
| 2 | GitHub issue created | ✅ {url} |
| 3 | Upstream feedback (Finding #N) | ✅ {url} (backing_repo verified: declared=live={owner/repo}) |
| 4 | Upstream feedback (Finding #M) | ⚠ aborted at step 0 — declared {X} ≠ re-resolved {Y}; user picked [b], re-issued at {url} |
| 5 | Upstream feedback (Finding #P) | ⊘ skipped at step 0 — divergence; user picked [c], action removed; reason: declared {X} not reachable, re-resolved {Y} unfamiliar to user |
...

Session learnings captured. Next session will benefit from these improvements.
```
