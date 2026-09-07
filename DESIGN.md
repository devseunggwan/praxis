# Design

How praxis hooks are built. The mechanisms below implement the [hook
ethos](ETHOS.md#hook-ethos) — they are the concrete primitives a new hook
must reuse so the suite behaves coherently across sessions, shells, and
platforms. The dependency graph between hooks, skills, and manifests lives
in [`ARCHITECTURE.md`](ARCHITECTURE.md); per-hook specs live at
[`hooks/<role>/<name>/spec.md`](hooks/) (the
[`docs/hook/INDEX.md`](docs/hook/INDEX.md) index links to them).

## Hook Design Contracts

Every hook ships with full spec at `hooks/<role>/<name>/spec.md` — design
rationale, matrix of blocked vs. passed commands, response JSON, parsing
guarantees, fail-safe paths, and test summary. The hook index lives in
[`docs/hook/INDEX.md`](docs/hook/INDEX.md); consult the per-hook spec before
editing.

Design mechanisms shared by all hooks:

- **Structural tokenization, not regex.** `hooks/_lib/_shell_tokenize.py`
  (`safe_tokenize` → `iter_command_starts` → `strip_prefix`) is the shared
  primitive; `_subst.py` walks active `$(…)` / backtick spans
  (`iter_command_texts`), `_compound.py` classifies compound and
  state-changing commands and owns the cascade hint, and `_roles.py` is
  the typed `Token` API (`tokenize_with_roles`, `filter_argv`).
  `_hook_utils.py` is a re-export shim over all four, kept so the
  pre-#1305 `from _hook_utils import …` preamble still resolves — new code
  imports from the defining sub-module. Per-hook `impl.py` files add
  `hooks/_lib` to `sys.path` via the three-line preamble documented in
  [`CONTRIBUTING.md → Adding or modifying a hook`](CONTRIBUTING.md#adding-or-modifying-a-hook).
  Quoted strings, comments, env prefixes, wrapper commands, and shell
  control-flow keywords are handled consistently across all Bash hooks.
- **Session state via `session_id`.** Per-session memory (intent flags,
  DESCRIBE history) keys on the payload's `session_id` field. PPID is a
  back-compat fallback for direct CLI / test invocation only.
- **Fail-open via `_hook_runtime.fail_open` (issue #645).** A hook crash
  must never block a legitimate tool call (ETHOS). Coverage has exactly
  two paths, by execution mode:
  - **Dispatched hooks** — members of the `(PreToolUse, Bash)` dispatch
    group (ADR-0002) run inside `_dispatch.py`, which wraps every member
    `main()` with `_hook_runtime.fail_open` at runtime. Member `impl.py`
    files need no decorator of their own; an `impl.py`-level reference is
    redundant but harmless (double-wrap is a no-op).
  - **Standalone hooks** — everything invoked through its own
    `hooks/<name>.sh` wrapper (non-Bash or multi-tool matchers,
    UserPromptSubmit/PostToolUse/PostToolUseFailure/Stop/SubagentStop
    events, opt-in hooks) must apply
    `@fail_open` to `main()` in `impl.py` directly (argv-style mains wrap
    a zero-arg `_entry()` instead). Rule 16 in
    `scripts/check-plugin-manifests.py` enforces this invariant.
  - **Before either path — the launcher (issue #1053).** Both bullets above
    run *inside* Python, so neither covers an impl that never starts. A
    missing `impl.py` makes `python3` exit 2, and 2 is this repo's deny code
    (`_dispatch.py` aggregates on `rc == 2`), so the host reads a partial
    install as a block nobody decided. Every generated launcher therefore
    checks `[ -f "$IMPL" ]` before `exec`. It normalizes *nothing* else:
    flattening non-zero codes would swallow the deliberate exit 2 that every
    gate blocks with. `tests/test_launcher_fail_open.sh` pins both halves.

  Shell hooks (`impl.sh`) have no Python entrypoint, so neither path
  applies to them — they carry their own `set +e` / `|| true` posture, and
  reach the fire ledger through `_lib/record_fire.sh` instead (see the
  instrumentation bullet below).

  Trade-off, stated explicitly: for *gates*, fail-open means a crashed
  guard allows the action it would have screened. That is the deliberate
  ETHOS choice — hook infrastructure failure must degrade to "no hook"
  rather than "no work".
- **Fire-ledger instrumentation for shell hooks (issue #848).** A hook's
  engagements land in the fire ledger via `@fail_open` (standalone) or the
  dispatcher (Bash group) — both Python-only, so the then-four `impl.sh`
  hooks (three since issue #1304 ported `codex-review-route` to Python)
  recorded nothing at all while an audit reading that ledger scored the
  silence as "never fires". A shell hook sources `_lib/record_fire.sh` and
  calls `praxis_fire_arm <hook> <role> "$SESSION_ID" ""` right after it
  parses its stdin payload; the armed EXIT trap writes exactly one RICH
  record whichever branch exits, so a later-added early `exit 0` cannot
  silently drop out of the ledger. Set `PRAXIS_FIRE_DECISION=block|advise`
  before the emitting branch; the default is `pass`. Rule 18 in
  `scripts/check-plugin-manifests.py` enforces that every manifest-listed
  `impl.sh` arms it.
- **Compound-Bash cascade advisory (issue #229).** When a PreToolUse(Bash)
  hook rejects (block) or asks-and-may-deny a compound command (`&&`, `||`,
  `;`, `|`, newline) containing a state-changing step (`> file`, `<<EOF >`,
  `mkdir`, `tee`, `cp`/`mv`/`rm`/`touch`, `curl -o`, `wget -O`), every hook
  appends the shared `_compound.compound_cascade_hint(command)` text
  (re-exported by `_hook_utils`) to its block/ask message. The advisory clarifies that bash never executed
  ANY part of the rejected command — files the redirect/mkdir/download
  would have created do NOT exist on disk — so the agent should not retry
  the second half expecting the first half to have landed. Single-command
  rejections receive no suffix (no cascade to warn about).
- **English-first emitted bodies (issues #1160, #1298).** Every body a hook
  hands to the user — stderr advisory text, a `permissionDecisionReason`, a
  Stop `reason`, a `systemMessage`, an `additionalContext` — starts with an
  English line; Korean detail may follow on the next line. Issue #1160 set
  the rule for two advisories and left the rest of the suite alone, so a
  reader who does not read Korean saw a block with no visible cause; #1298
  extends it to every hook. The gate is `tests/test_emit_english_lead.py`:
  it parses every `hooks/**/impl.py` and fails on any string literal longer
  than eight characters whose first character — after an optional
  `[hook-name]` tag — is Hangul, skipping docstrings, regex and
  match-vocabulary literals, and text glued to the right of `+`. Its
  `ALLOWLIST` must stay empty; an entry needs an issue link.

  Rule: *English lead line first; Korean after a newline, never before.*

## Session-state concurrency

Every hook that keeps session state does the same three things: read the
JSON at `resolve_cache_file(...)`, modify it, replace it. `os.replace` makes
the *replace* atomic — a reader never sees half a file — and that is all it
makes atomic. The read-modify-write around it is not serialized, so two hook
processes sharing a `session_id` (parallel sub-agent tool calls are the
ordinary source) read the same value, each modifies its own copy, and the
later replace discards the earlier modification.

Locking all of them would be the wrong reading of that. The absence of a lock
was neither documented as intent nor as an oversight before issue #951; what
follows is the criterion that settles it per hook, so a new consumer of
`resolve_cache_file` is classified rather than reflexively locked.

**A lost update needs a lock when the state carries information no later
event reproduces.** Four questions, in order — the first `yes` decides it.
The first asks what the concurrent write *is*; only if it is a lost update do
the remaining three ask what losing it costs:

0. **Does the write stage through a name a sibling sharing this `session_id`
   also uses?** Then the failure is not loss but corruption, and the three
   questions below cannot see it — they all price one missing entry. Two
   processes writing one `<path>.tmp` interleave, and the shorter write
   published over the longer one's tail leaves bytes that every reader's
   `except ValueError` answers with *empty state*: the whole accumulated set
   is gone, not one entry, so a hook whose lost update was merely a repeated
   advisory now restarts its session's dedup from scratch. **Lock — and give
   the staging file a per-process name**, because the lock is fail-open and
   an unacquired one puts the shared name straight back (issue #970).
1. **Does a threshold read the state?** A hook firing on an exact boundary
   (`count == 2`) misfires in both directions when an increment is lost: two
   processes crossing the boundary together both fire, and the count that
   never advanced never fires again. **Lock.**
2. **Does a gate read the state to decide block vs. pass?** A dropped entry
   becomes a false verdict about the session — a file that was Read scored as
   unread — and the verdict lands on the user, not in a log. **Lock.**
3. **Otherwise:** the state is a dedup marker or a monotone flag, and the
   worst case is one advisory line repeated or missing. **No lock.** The
   contention is real, its consequence is not; a lock here buys latency on
   every tool call and nothing else.

Every consumer of `resolve_cache_file` gets a row here — a new consumer is
classified, not reflexively locked. The first six rows were measured live
(`jq-config` in #970, the other five in #1034); the last two were classified
from their implementation and carry no measurement yet. A seventh measured
row, `advisory-nudge/postcompact-context`, left the table in #1339: the hook
moved from `UserPromptSubmit` to `SessionStart(compact)`, where the event
fires once per compaction, so its last-injected-uuid state and the lock
around it were deleted — the #1034 measurement of the old write
(5/300 torn unforced, 0/300 after the fix) stays on record in
[`docs/hook-state-concurrency-measurements.md`](docs/hook-state-concurrency-measurements.md):

| Hook | State | Loss consequence | Locked |
| --- | --- | --- | --- |
| `postuse-correction/second-failure-advisory` | per-`(tool, signature)` counter | fires on `prior_count == 1` exactly — Q1 | yes — Q0 PASS(live), 0/100 (#1034) |
| `postuse-correction/pre-edit-md-escape-advisory` | accumulating set of Read paths | the Edit gate warns, or denies under `PRAXIS_MD_ESCAPE_MODE=block`, for a file that was Read — Q2 | yes — Q0 PASS(live), 0/100 vs 4/100 unlocked (#1034) |
| `advisory-nudge/jq-config-empty-dict-advisory` | dedup set of advised paths | one advisory repeats — but the pair shared one `.tmp` staging name, so the surviving file could also be unparseable, which `_load_seen` reads as empty and the session's whole dedup set restarts — Q0 | yes |
| `preflight-gate/worktree-prune-snapshot-gate` | single `snapshot_taken` flag, only ever set true | concurrent writers write the identical value | no — Q0 PASS(live), 0/100 (#1034) |
| `preflight-gate/session-intent` | set-once intent flags | written only from `UserPromptSubmit`, which is serialized per session; the `PreToolUse` gate path is read-only | no — Q0 PASS(live), 0/100 (#1034) |
| `preflight-gate/retrospect-active-marker` | marker existence | whole-file write and `unlink`, no read-modify-write to lose | no — Q0 PASS(live), 0/100 (#1034) |
| `preflight-gate/foreground-poll-loop-guard` | per-session registry of background waiters (start time, armed flag, command display string) | one advisory does not fire — Q3; stages through a per-pid name, so Q0 does not apply | no — classified from the impl, not measured |
| `preflight-gate/approval-premise-reread-gate` | single-use premise ack file | consumed by an atomic `os.rename` claim; no read-modify-write to lose | no — classified from the impl, not measured |

### Q0, measured (issue #1034)

Every Q0 verdict in the table above is a measurement, not an inference. The
measurement record — 100 concurrent pairs of the real impl per row, each
paired with a negative control that removes only the property its exemption
rests on, and the `postcompact-context` corruption it caught — lives in
[`docs/hook-state-concurrency-measurements.md`](docs/hook-state-concurrency-measurements.md).
The rule it established, which applies to every future row: **a 0 with no
control arm that can fail it is not a measurement** — it cannot distinguish
"exempt" from "the harness never reached the state file".

`hooks/_lib/_state_lock.py` is the primitive: `with state_lock(path):` around
the read *and* the write, `fcntl.flock` on a `<path>.lock` sibling. Readers
take no lock — `os.replace` already gives them a whole file.

It yields False rather than raising or waiting when the lock cannot be taken,
and the body runs regardless. That is the fail-open contract above, applied
one level down: a hook that cannot lock must degrade to the pre-lock
behaviour, never to a blocked tool call. The wait has a deadline
(`PRAXIS_STATE_LOCK_TIMEOUT`, 2s) for the same reason — a lock left by a
killed sibling must not stall the tool call the hook was only observing.

That deadline sets a rule for what may go inside the section: only the
read-modify-write. Anything slower than the deadline — a subprocess, a network
call — held inside it does not merely add latency, it disables the lock. The
holder outlasts every sibling's acquisition deadline, each sibling proceeds
unlocked (that is the fail-open contract, not a bug), and they all read state
the holder has not written yet. `postcompact-context` was the worked example
until #1339 removed its state: its `build_context` shelled out to `git` and
`gh` under 1.5s and 3.0s timeouts, so it was built *before* the lock and the
decision was re-taken inside it. The rule outlives the example: when building
ahead of the lock needs an unlocked pre-check to stay cheap, the pre-check may
only skip work; the in-lock re-read is what decides.

`tests/test_hook_state_concurrency.py` runs every consumer above in two real
processes against one state file. Each locked hook carries an unlocked arm
alongside the shipped one, so the defect stays pinned and a regression that
drops the lock fails there instead of quietly passing. The `jq-config` arm
adds the Q0 half: a companion case asserts the staging name varies per
process, because a race arm alone cannot say which of the two defects a given
scheduling hit. The #1034 cases carry the same discipline in the other
direction — each pairs its shipped arm with a `stage_mode="shared"` control
that forces one staging name, so a row is only ever promoted against an arm
that could have failed it.

## Hook ordering and precedence

- PreToolUse hooks run **in parallel**. Decision precedence is
  `deny > defer > ask > allow`. Order in `hooks/manifest.json` (and the
  generated platform `hooks.json`) is presentational.
- Stop hooks run **sequentially in array order**. The order is fixed by
  `scripts/check-plugin-manifests.py` Rule 4 (`expected_stop`):
  `completion-verify` and `retrospect-mix-check` first, then the
  completion-verify evidence gates, and `strike-counter stop` last —
  thirteen entries at the time of writing; the manifest is the list. Since
  issue #1281 the twelve stdin-only entries form one `(Stop)` dispatch
  group — a single `_dispatch.sh Stop -` node runs them in that order
  inside one process (the two `impl.sh` members as subprocesses under the
  member deadline) — and `strike-counter stop` follows as its own node,
  because it reads its mode from argv, which a group cannot forward. Each
  gate is independent. Inside the group every blocking member's reason is
  kept and merged into one `decision: block` (issue #1169), and every
  advisory member's `systemMessage` merges the same way, riding on the block
  object when a sibling blocks; fix what the merged reason lists and re-run.
- PostToolUse hooks run **sequentially**; corrective `additionalContext`
  emissions are additive, not exclusive. Inside the `PostToolUse(Bash)`
  dispatch group the manifest array order is the run order, and
  `bypass-telemetry` comes first so a slow sibling cannot cost the audit
  line (ADR-0002 §2.5).

## Adding a new hook

1. Survey ≥2 sibling implementations under `hooks/<role>/` for the
   convention (state-key naming, payload field access, exit-code
   semantics) — the *Convention Survey Before Design* rule
   ([`ETHOS.md` → Rules praxis carries](ETHOS.md#rules-praxis-carries)).
2. Author `hooks/<role>/<name>/impl.py` (or `impl.sh` for body-as-sh),
   make it executable, add the `sys.path` preamble for `hooks/_lib` and
   import from `_shell_tokenize` / `_subst` / `_compound` / `_roles` (the
   `_hook_utils` shim re-exports them for hooks written before #1305).
3. Register the hook in [`hooks/manifest.json`](hooks/manifest.json) per
   ADR-0001 §2.5 schema (`name`, `role`, `event`, `matcher`, `hosts`,
   `timeout`, `args`, `body`, `wrapper_suffix` as applicable).
4. Run `./scripts/build-plugin-manifests.py` — the build emits the
   runtime wrapper at `hooks/<name>{suffix}.sh` (tracked; commit the
   generated file alongside the manifest entry — marketplace installs
   do not run this build) and all platform `hooks.json` files.
5. Add the test at `tests/hooks/<role>/test_<name>.{sh,py}`.
6. Create `hooks/<role>/<name>/spec.md` (template: any existing spec).
7. Add the hook under its role in [`docs/hook/INDEX.md`](docs/hook/INDEX.md).
8. Run `./scripts/check-plugin-manifests.py` — confirms the
   directory↔manifest cross-check, role agreement, byte-identical
   generated artifacts, plus 5+ other invariants.

See also [`CONTRIBUTING.md → Adding or modifying a hook`](CONTRIBUTING.md#adding-or-modifying-a-hook)
for the full workflow including the `sys.path` preamble template.
