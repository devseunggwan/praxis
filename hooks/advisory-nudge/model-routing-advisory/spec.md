# PreToolUse Model-Routing Advisory

Supported hosts: claude
Requires: cmux (only cmux / cmux-delegate delegation argv is recognized)

`hooks/advisory-nudge/model-routing-advisory/impl.py` intercepts `Bash` tool
calls that delegate work with an explicit `--model <tier>` and emits a **stderr
advisory** (never a block) when the chosen tier does not match the tier implied
by the task keywords in the command.

This hook, together with the reference decision tree in this spec, is the
**replacement mechanism** for the `Skill & Agent Routing` + `Model Routing Rules`
prose blocks in the always-loaded user-level `CLAUDE.md`: the enforcement (tier
nudge) lives in the hook and the on-demand reference knowledge (which skill/agent/
model for which task) lives in this spec — neither is always-loaded. Removing the
blocks from the always-loaded file is a **companion change to the ruleset this
hook slims** (the author's dotfiles, outside this repo); this praxis PR ships
only the mechanism. The
tier nudge covers the *complexity→model* phase of the two-phase Provider Routing
model (see ARCHITECTURE.md) — Claude tiers only, not `codex:`/`gemini:` provider
selection.

## Why this exists

Cost-aware model selection is not optional — opus-for-everything wastes budget,
and haiku-for-architecture is under-powered. The routing rules already express
the signal as a keyword→tier mapping (`find/search → haiku`, `implement/fix →
sonnet`, `architect/design/security → opus`), which is automation (a hook), not
knowledge that must sit in always-loaded context (a skill). When a delegation
command carries an explicit `--model`, the hook compares the chosen tier against
the implied tier and nudges on a mismatch, at the moment the worker is spawned.

## Scope (v1) — Bash delegation only

Only `Bash` commands carrying `--model <bare-claude-tier>` are inspected
(`cmux ... claude -p ... --model`, `cmux-delegate --model`, a bare
`claude -p ... --model`). The `Task` / `Agent` tool is **out of scope for now**:
its tier is implied by `subagent_type` with only an OPTIONAL `model` override, so
there is rarely an explicit chosen tier to compare against. A follow-up can add
`Task` handling that fires only when an explicit `model` override is present and
mismatches the `subagent_type`'s implied tier.

## What is emitted

The hook writes advisory text to stderr and exits 0. Tool execution is never
blocked.

| Condition | Result |
| --- | --- |
| `--model opus` with a haiku-signal task (`find` / `cleanup` / …) | `[model-routing]` advisory — over-powered, keyword → implied tier |
| `--model haiku` with an opus-signal task (`architect` / `security` / …) | `[model-routing]` advisory — under-powered |
| `--model <tier>` matching the implied tier | silent |
| No `--model` in the command | silent |
| `--model` value not a bare Claude tier (`codex:gpt-5`, `gemini:…`, `claude-opus-4-8`) | silent (no comparable tier) |
| `--model claude:opus` (explicit Claude provider prefix) | inspected (`claude:` resolves to the bare tier) |
| Command has a `--model` tier but no task-signal keyword | silent (no implied tier) |
| Opt-out marker `# [model-routing-ack]` in the command | silent |
| `PRAXIS_SKIP_MODEL_ROUTING=1` in the environment | silent |
| Non-Bash tool / malformed JSON stdin / empty command | silent (fail-open) |

## Tier classification

**Chosen tier** — the value of the first `--model <val>` / `--model=<val>`.
A `provider:model` value keeps only the model when the provider is `claude`;
any other provider prefix, or a value not in `{haiku, sonnet, opus}`, yields no
comparable tier and the hook stays silent.

**Implied tier** — the highest-precedence task signal present in the command
text, matched on ASCII word boundaries (`(?<![a-z])kw(?![a-z])`, lower-cased):

| Implied tier | Signal keywords | Precedence |
| --- | --- | --- |
| `opus` | architect, design, security, incident | highest (tested first) |
| `sonnet` | implement, add, fix, test, review, refactor | middle |
| `haiku` | find, search, list, status, cleanup, rename, move | lowest |

opus signals are tested first so the costliest implied tier wins when multiple
signals co-occur. A mismatch (`chosen != implied`) nudges in the appropriate
direction (over- / under-powered).

**Regex signals not covered.** The Model Routing Rules list two *regex* opus
signals — `debug.*race` and `system.*design` — that a literal-keyword hook cannot
express. A bare `debug` is deliberately **not** an opus signal (only
race-condition debugging is opus; promoting standalone `debug` would false-surface
ordinary `debug the parser` work on sonnet). Both regex signals are documented
false-negatives (silent), not enforced here.

**Chosen tier is the first `--model`.** The documented delegation forms carry a
single flag. Two edge cases follow from first-match + raw scan, both accepted:

- A prompt that itself contains a literal `--model <tier>` before the real flag
  (`-p "explain --model opus" --model sonnet`) is read as the prompt's tier — a
  known misread; structural arg parsing is deliberately avoided.
- The captured value keeps only its leading tier-shaped run, so trailing shell
  punctuation is tolerated: `--model opus; echo done` and `--model opus,sonnet`
  both classify as `opus` (without this, `opus;` would be unrecognized and the
  advisory silently missed).

Signals match on the exact stem, not inflections: `architecture` does not match
`architect` — a harmless false-negative (silent, same as no hook).

## Detection — raw-string scan, not tokenization

The chosen tier and the task prompt are typically **nested** inside a quoted
`--command "claude -p '...' --model opus"`, which `safe_tokenize` keeps opaque as
a single token. So detection is a raw-string regex scan of the whole command,
which handles the nested and flat forms uniformly. The trade-off — quoting and
heredoc boundaries are not respected, so a `--model opus` literal inside a
heredoc body or an echoed string would be inspected — is acceptable for a
fail-open advisory and is the same anti-fragile-parser posture as the sibling
`git commit` hooks (memory `feedback_shell_parser_diminishing_returns`).

## Word-boundary keyword matching

Signals match on ASCII-plus-hyphen boundaries — `(?<![a-z0-9_-])kw(?![a-z0-9_-])`
on the lower-cased command — so a keyword fires only as a standalone word. Two
collision classes are excluded and pinned as test cases:

- **Intra-word**: `move` ∉ `remove`, `search` ∉ `research`, `list` ∉ `blocklist`,
  `add` ∉ `address`, `fix` ∉ `prefix`, `test` ∉ `latest`.
- **CLI flag / hyphenated-path / numbered token**: `add` ∉ `--add-dir`, `test` ∉
  `test-utils`, `fix` ∉ `fix2`, `test` ∉ `test_v3`. Including the hyphen, digit,
  and underscore in the boundary class stops a CLI flag name or a path segment
  from hijacking the implied tier — without this, `claude -p "find X" --model
  haiku --add-dir /repo/src` would wrongly nudge "under-powered" (the `add` in
  `--add-dir` outranking the real `find` signal).

**Residual boundary (accepted, not fixed).** The scan is over the whole command,
not just the `-p` prompt, so a signal word standing alone inside another flag's
quoted VALUE still fires: `--append-system-prompt "you review code"` nudges on
`review`. Scoping the scan to the prompt segment would reintroduce the quote/
nesting tokenization the raw-string design deliberately avoids (memory
`feedback_shell_parser_diminishing_returns`), so this rare false-surface is
documented and left to the exit-0 nudge + opt-out rather than chased.

The boundary is ASCII-only; it is not intended to separate a keyword from adjacent
Hangul (routing keywords and `--model` flags are English delegation syntax), so a
keyword abutting non-ASCII is a tolerated false-surface on an exit-0 nudge.

## Reference — Skill & Agent Routing decision tree

The full routing knowledge the always-loaded blocks used to carry now lives here,
consulted on demand.

### Plan (decide what/how)

| Condition (match top-to-bottom) | Skill/Agent | Notes |
| --- | --- | --- |
| Requirements ambiguous (no files/criteria) | `oh-my-claudecode:deep-interview` | Socratic, math-gated |
| Investigation + requirements | `oh-my-claudecode:research` | canonical research lane |
| Generic planning | `oh-my-claudecode:plan` | optional interview |
| Project-specific planning | see project CLAUDE.md | laplace-dev-hub:*, praxis:*, etc. |

### Execute (write code)

| Condition | Skill/Mode | Notes |
| --- | --- | --- |
| Setup from scratch (issue+branch+worktree) | manual (`gh issue create` + `git worktree add`) | follow the Issue-Driven Worktree Workflow in AGENTS.md |
| Full-cycle autonomous (plan→code→QA) | `oh-my-claudecode:autopilot` | includes ralph |
| Persist until done | `oh-my-claudecode:ralph` | loop with verification |
| 5+ independent parallel tasks | `oh-my-claudecode:execute` | `team` for coordinated workers |
| Coordinated agent team | `oh-my-claudecode:team` | native Claude Code |
| Documentation lookup | `context7` | library docs |

### Verify (mandatory before completion)

Before any completion claim, run the project's tests + lint and paste the actual
output. No skill wraps this — same-turn evidence in the assistant message is what
the Stop hook gates on.

### Deliver (PR → merge → cleanup)

| Condition | Skill | Notes |
| --- | --- | --- |
| Project-specific review/PR | see project CLAUDE.md | laplace-dev-hub:code-review, laplace-dev-hub:create-hub-pr, etc. |
| Code review (general, MANDATORY before commit) | `oh-my-claudecode:code-reviewer` agent | spec compliance + security + logic + SOLID, severity-rated |
| Second independent review (MANDATORY before commit) | `praxis:codex-review-wrap` skill | independent Codex pass, premise-verification gate + A→B→A flip detection |
| Security-sensitive change (auth / token / secret / SQL / XSS) | `oh-my-claudecode:security-reviewer` agent | additional pass beyond generic review |
| Pre-PR rebase + atomic commit splitting | `oh-my-claudecode:git-master` agent | `git fetch origin && git rebase origin/<base>` before `gh pr create`; `--force-with-lease` only |
| Branch cleanup after merge | manual (`gh pr merge --squash --delete-branch`, `git worktree remove`, `git branch -D`) | trivial, no skill wrapping |

### Debug (investigate problems)

| Condition | Skill/Agent | Notes |
| --- | --- | --- |
| Bug / test failure | read stack trace + `oh-my-claudecode:debugger` agent if non-trivial | direct diagnosis |
| Causal tracing (why did X happen) | `oh-my-claudecode:trace` | 3-lane hypothesis |
| Deep investigation + spec | `oh-my-claudecode:research` | canonical research lane |
| OMC session diagnosis | `oh-my-claudecode:debug` | session/runtime only |

### Agent Delegation (within execution)

| Complexity | Model | Agent Examples |
| --- | --- | --- |
| Simple (search, lookup) | haiku | `explore`, `executor-low`, `code-reviewer-low`, `architect-low` |
| Standard (implement, review) | sonnet | `executor`, `explore-medium` |
| Complex (design, security, incident) | opus | `architect`, `explore-high`, `executor-high`, `code-reviewer`, `security-reviewer` |
| **Default** | **sonnet** | |

### Model Routing tiers (the signal this hook enforces)

| Tier | Model | When to use |
| --- | --- | --- |
| Low | `haiku` | file search, code lookup, status check, cleanup, rename, move |
| Medium | `sonnet` | feature implementation, test writing, review triage, bug fix, refactoring |
| High | `opus` | architecture, complex debugging, security review, incident, multi-repo design |

Default is `sonnet`, never opus. When spawning cmux workers
(`cmux new-workspace --command "claude -p …"`) always pass `--model <tier>`.

### Override Rules (keyword conflict resolution)

| Keyword | Priority Skill | Alternative (explicit only) |
| --- | --- | --- |
| `plan` | project planner skill (see project CLAUDE.md) | `oh-my-claudecode:plan` (generic context) |

### OMC Mode Keywords

| Keyword | Effect |
| --- | --- |
| `plan` | Start planning interview (routes via Override Rules) |
| `autopilot` | Autonomous implementation (workflow steps still apply) |
| `ralph` | Persistence loop — don't stop until verified complete |
| `eco` / `ecomode` | Token-efficient parallel execution |
| `stop` / `cancel` | Cancel any active OMC mode |

## Limitation — no negation / status-context awareness

The keyword scan is presence-based: a signal fires whenever the word appears,
regardless of surrounding intent. A prompt that *negates* a task
(`find callers; do not design or modify anything` → `design` fires → opus
implied) or merely *mentions* it in a status/question framing over-surfaces. Full
negation/intent parsing is an NLP surface deliberately not built (memory
`feedback_shell_parser_diminishing_returns` / `feedback_input_surface_enumeration`)
— on an exit-0 nudge with an opt-out, a rare wrong-direction surface is cheaper
than a fragile negation parser. Ack with `# [model-routing-ack]` when a negated
keyword mis-fires.

## Limitation — active nudge covers tier only

The hook actively nudges only the **model-tier** signal (`--model` vs task
keyword). The `Skill & Agent Routing` tree above (scenario → which skill/agent)
is reference knowledge consulted on demand, not an active per-command nudge — a
keyword-in-`--model`-command trigger cannot express "you should have used
`research` instead of `plan`." Actively nudging scenario→skill would require a
prompt-time (`UserPromptSubmit`) intent classifier, deliberately deferred to keep
this first hook precise and low-noise.

## Parsing guarantees (fail-open)

The hook returns exit 0 on every path — advisory or infrastructure error:
malformed JSON stdin, non-Bash tool, empty command, or any uncaught exception
(wrapped by `@fail_open`).

## Tests

```bash
bash tests/hooks/advisory-nudge/test_model_routing_advisory.sh
```

Covers: over-powered mismatch (`--model opus` + `find`) surfaced; under-powered
mismatch (`--model haiku` + `architect`) surfaced; matching tier silent;
non-Claude provider / full model id / no `--model` / no signal keyword silent;
`claude:opus` prefix inspected; nested `cmux --command "… --model …"` detected;
word-boundary collisions (`remove` / `research` / `address` / `prefix`) silent;
opt-out marker and env bypass silent; fail-open (non-Bash, malformed JSON, empty
command).
