# Rule No-op Audit (issue #1534)

> **Snapshot.** Stage 2 was measured on 2026-09-29 against `main` at
> `0fd84489`. Stage 1 is not a snapshot: `tests/test_rule_noop_audit.py`
> recomputes it from `hooks/manifest.json` and the hook bodies, so a hook that
> gains or loses a model channel fails the test until its row is updated.

[`hook-prune-audit.md`](hook-prune-audit.md) scores hooks by how often they
fire. It cannot say whether the rule a hook delivers changes what the model
does. A rule the model already follows by default is a *no-op*: it costs
context on every fire and changes nothing. This audit asks that question of
every `advisory-nudge` hook, in two stages, because a behaviour test is only
meaningful for a rule the model can see.

## Stage 1 — can the model see the rule?

**Data source.** The `Channels` column of
[`hook-operating-matrix.md`](hook-operating-matrix.md), derived from each hook
body by `scripts/hook_channels.py` (#1265, PR #1400). The table below is
recomputed from the same function.

**Method.** `decision`, `context`, `rewrite` and `stop-block` reach the model.
On the exit-0 path of an `advisory-nudge` hook, `stderr` reaches only the debug
log (`hooks/_lib/_hook_io.py`: "an advisory written only there fires correctly
and is indistinguishable, to the actor, from a hook that does not exist"). A
hook whose only channel is `stderr` is therefore **unreachable**: its rule
cannot change behaviour, so no A/B is run for it.

**Live check.** Two `PreToolUse(Bash)` hooks were registered on one headless
run (`claude -p … --setting-sources project --settings <file>`), and the model
ran `echo hi` and was asked to quote every `MARKER-` string it saw. The hook
that wrote `MARKER-STDERR-QX7` to stderr and exited 0 ran — it left a witness
file — and the model did not quote it; the stream carried it 0 times. The hook
that sent `MARKER-CTX-ZP4` as `hookSpecificOutput.additionalContext` was quoted
back verbatim.

**Two limits of the column.** It lists what a body *can* emit, not what it
emits by default. A hook whose `decision` comes only from its strict mode is
`reachable` here but reaches the model only with its strict env set
(`unmeasured: strict` below). In the other direction, an `unreachable` hook with
a strict env does reach the model in strict mode, because a stderr block reason
on exit 2 is shown to the model; those are the 7 with a `Strict env` value.

## Stage 2 — does the rule change the reply?

**Method.** [`scripts/rule-ab-eval.py`](../scripts/rule-ab-eval.py) runs each
task of a suite in a `hook on` and a `hook off` arm, 3 reps each, in a shuffled
order, from an archived copy of the repository and without the operator's user
settings or plugins (#1535). A task's oracle is a shell command run before any
model call; the reply passes when it contains every line the oracle printed.

**Verdicts.** `no-op` only when the arms are indistinguishable on the oracle
**and** the `off` arm already passes; `keep` when the `on` arm passes where the
`off` arm does not; `investigate` otherwise.

**Scope.** The runner grades the reply and nothing else, so only a hook whose
effect shows in the reply can be measured. Of the 22 reachable hooks, one does.
The others are `unmeasured`, for one of these reasons:

| Reason | Meaning |
| --- | --- |
| `command` | The rule changes the shape of a tool call, not the reply. |
| `write` | The hook fires only on an external write (`gh`, `git push`) or an edit to a protected, settings or personal file, which an eval job does not make. |
| `event` | The hook fires on an event an eval job never raises: jobs allow only `Read`, `Grep`, `Glob` and `Bash`, so no `AskUserQuestion` or subagent, and a short job never compacts. |
| `strict` | The model channel is the strict-mode deny; by default the hook writes stderr only. |
| `language` | The effect is the reply's language, which a line-presence oracle cannot grade. |

### Result: `elapsed-time-signal`

Four read-only tasks against this repository, 3 reps per arm, 24 runs, 0
failed, about $2.45 in total. The `budget` arm sets
`PRAXIS_TIME_BUDGET_S=600` and `PRAXIS_TIME_START_EPOCH=@now`; the `none` arm
sets neither. `Signal` sums, over a task's 3 runs, the hook's `elapsed …`
context lines in each run's stream; it counts lines, not runs.

| Task | Arm | Oracle | Median wall s | Median tools | Signal |
| --- | --- | --- | --- | --- | --- |
| `consumers` | budget | 3/3 | 17 | 3 | 9 |
| `consumers` | none | 3/3 | 13 | 1 | 0 |
| `specdrift` | budget | - | 31 | 2 | 9 |
| `specdrift` | none | - | 30 | 2 | 0 |
| `stophooks` | budget | 3/3 | 37 | 8 | 27 |
| `stophooks` | none | 3/3 | 47 | 9 | 0 |
| `tests` | budget | 3/3 | 35 | 1 | 6 |
| `tests` | none | 3/3 | 35 | 1 | 0 |

**Verdict: `no-op`.** Each of the 12 `budget` runs carried 2 to 11 signal
lines, 51 in all; none of the 12 `none` runs carried one. On the three graded
tasks both arms passed 3/3. Pooled over the 12 runs of each arm, not taken from
the per-task medians above, the median wall time is 32.5 s and the median tool
count is 2 in both arms. `specdrift` has no oracle and is not graded.

**Scope of the verdict.** The longest run took 53 s against a 600 s budget, so
no run came near the limit. The verdict says the signal changes nothing on a
task that finishes well inside its budget; what the model does as elapsed time
approaches the budget was not measured.

## Verdict table

`Reach` is stage 1; `Stage 2` is the measurement or the reason there is none.

<!-- rule-noop-audit:begin -->
| Hook | Channels | Strict env | Reach | Stage 2 |
| --- | --- | --- | --- | --- |
| `advisory-wrapper-signature-verify` | stderr | - | unreachable | - |
| `bash-worktree-existence-advisory` | stderr | - | unreachable | - |
| `block-personal-asset-leak` | context, stderr | `PRAXIS_PERSONAL_LEAK_STRICT` | reachable | unmeasured: write |
| `bulk-write-memory-checkpoint` | stderr | - | unreachable | - |
| `caller-probe-gate` | stderr | `PRAXIS_CALLER_PROBE_STRICT` | unreachable | - |
| `cited-rule-gate` | decision, context, stderr | `PRAXIS_CITED_RULE_STRICT` | reachable | unmeasured: command |
| `cli-flag-incompat-advisory` | stderr | - | unreachable | - |
| `codex-review-route` | context | - | reachable | unmeasured: event |
| `comment-yap-advisory` | stderr | - | unreachable | - |
| `commit-decomposition-advisory` | stderr | - | unreachable | - |
| `composed-command-gate` | stderr | `PRAXIS_COMPOSED_COMMAND_STRICT` | unreachable | - |
| `count-assertion-verify` | stderr | - | unreachable | - |
| `cwd-relative-exec-advisory` | decision | - | reachable | unmeasured: command |
| `delegation-context-inject` | context | - | reachable | unmeasured: event |
| `destructive-bash-guard` | decision, context, stderr | `PRAXIS_DESTRUCTIVE_BASH_STRICT` | reachable | unmeasured: command |
| `elapsed-time-signal` | context | - | reachable | measured: no-op |
| `exclusion-probe-gate` | decision, stderr | `PRAXIS_EXCLUSION_PROBE_STRICT` | reachable | unmeasured: strict |
| `external-api-literal-trigger` | stderr | - | unreachable | - |
| `external-write-path-existence-check` | context, stderr | `PRAXIS_PHANTOM_PATH_STRICT` | reachable | unmeasured: write |
| `fallback-negative-warn` | stderr | - | unreachable | - |
| `inspection-chain-advisory` | stderr | - | unreachable | - |
| `jq-config-empty-dict-advisory` | stderr | - | unreachable | - |
| `long-foreground-call-advisory` | stderr | - | unreachable | - |
| `memory-hint` | stderr | - | unreachable | - |
| `menu-mutation-tier-advisory` | decision, stderr | `PRAXIS_MENU_MUTATION_TIER_STRICT` | reachable | unmeasured: event |
| `merge-menu-review-options-advisory` | stderr | `PRAXIS_MERGE_MENU_REVIEW_STRICT` | unreachable | - |
| `model-routing-advisory` | stderr | - | unreachable | - |
| `momentum-rule-retrieval-gate` | decision, stderr | `PRAXIS_MOMENTUM_STRICT` | reachable | unmeasured: write |
| `n1-quantitative-claim-advisory` | stderr | - | unreachable | - |
| `negation-answer-quote-advisory` | decision | - | reachable | unmeasured: write |
| `output-block-falsify-advisory` | decision, stderr | - | reachable | unmeasured: write |
| `path-probe-gate` | decision, stderr | `PRAXIS_PATH_PROBE_STRICT` | reachable | unmeasured: strict |
| `perf-multiplier-evidence-advisory` | stderr | - | unreachable | - |
| `pipefail-advisory` | context, rewrite, stderr | - | reachable | unmeasured: command |
| `postcompact-context` | context, system-msg | - | reachable | unmeasured: event |
| `pr-thread-resolve-advisory` | context, stderr | `PRAXIS_PR_THREAD_ADVISORY_STRICT` | reachable | unmeasured: write |
| `pre-commit-staged-file-enumeration` | stderr | - | unreachable | - |
| `pre-output-falsification-gate` | stderr | - | unreachable | - |
| `protected-paths-guard` | context, stderr | `PRAXIS_PROTECTED_PATHS_STRICT` | reachable | unmeasured: write |
| `push-remote-ref-verify` | stderr | `PRAXIS_PUSH_VERIFY_STRICT` | unreachable | - |
| `pytest-direct-exec-advisory` | stderr | - | unreachable | - |
| `response-language-nudge` | context | - | reachable | unmeasured: language |
| `secret-print-redaction-advisory` | context, stderr | - | reachable | unmeasured: command |
| `settings-path-advisory` | context, stderr | `PRAXIS_SETTINGS_PATH_STRICT` | reachable | unmeasured: write |
| `source-citation-probe-gate` | stderr | `PRAXIS_SOURCE_CITATION_STRICT` | unreachable | - |
| `unenforced-step-advisory` | stderr | `PRAXIS_UNENFORCED_STEP_STRICT` | unreachable | - |
| `version-bump-evidence-check` | stderr | `PRAXIS_VERSION_BUMP_STRICT` | unreachable | - |
| `zsh-dialect-advisory` | decision, context, stderr | - | reachable | unmeasured: command |
<!-- rule-noop-audit:end -->

## What this audit does not do

Report-only. An `unreachable` or `no-op` verdict proposes action in its own
issue; this document removes and changes nothing. Moving an `unreachable` hook
to `additionalContext` is how `pipefail-advisory` was fixed (#1408, PR #1411),
and each such move is its own change.

An `unreachable` row is not by itself a defect. #1265 decided to route the
model channel only to hooks that guard a hard-to-reverse mutation, because
routing every advisory would add about 1114 messages a day to the model's
context against 19 for that subset (one day of one operator's fire ledger).
The remaining `unreachable` hooks are stderr-only on the default exit-0 path
by that decision; a strict-mode block still reaches the model, as stage 1
notes. #1538 checked the six that inspect external-write bodies and so meet
the criterion. None of the five with fires reached 70% true positives on a
replayed sample, and `version-bump-evidence-check` had no fires to sample, so
all six stay stderr-only on the default path until their detectors are fixed
([precision table](https://github.com/devseunggwan/praxis/issues/1538#issuecomment-5888613453)).
