# ADR-0002: PreToolUse(Bash) hook dispatch consolidation (single-process group runner)

- **Status**: Accepted (shipped — see the decision log)
- **Date**: 2026-06-05
- **Authors**: praxis maintainers
- **Supersedes**: n/a
- **Builds on**: [ADR-0001](0001-hook-layout.md) (role-based per-hook layout + `manifest.json` registry)
- **Related**: [`ETHOS.md`](../../ETHOS.md) (fail-open invariant), [`ARCHITECTURE.md`](../../ARCHITECTURE.md), [`hooks/_lib/_hook_runtime.py`](../../hooks/_lib/_hook_runtime.py)

---

## 1. Context

ADR-0001 left the runtime contract deliberately unchanged: each hook is still a
`.sh` wrapper that `exec python3 .../impl.py`, and Claude Code spawns one
process per hook. §4.3 of ADR-0001 explicitly deferred wrapper inlining "until
we have evidence that the wrapper layer adds measurable session latency",
noting that *"the cost is ~1ms per hook invocation and is dwarfed by `python3`
startup"*.

We now have that evidence — and it points past the wrapper, at the process
count.

### 1.1 Measurement (2026-06-05)

A session was instrumented to measure the cost of the `PreToolUse(Bash)` hook
group, which fires on **every** `Bash` tool call.

| Metric                                      | Value                                                                                                        |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `PreToolUse(Bash)` hook entries             | 35 fire on a `Bash` call: 33 exact-`Bash` (21 `preflight-gate` + 12 `advisory-nudge`) + 2 multi-tool matcher |
| Per-hook wrapper body                       | `command -v python3 \|\| exit 0; exec python3 .../impl.py`                                                   |
| 35 hooks, parallel wall-clock               | **1.87s** (user 1.44 + sys 1.33 — CPU saturation)                                                            |
| Hook *logic* for a no-op command (`ls -la`) | **2ms** across all 35                                                                                        |

~99% of the latency is **python3 interpreter cold-start multiplied across 35
processes**, not hook logic and not the wrapper. The wrapper is ~1ms as ADR-0001
predicted; inlining it (ADR-0001 §4.3) would not move the number, because the
process count is unchanged.

### 1.2 Prototype

A throwaway prototype imported all 35 `impl.py` modules into a single process
and invoked each `main()` after re-pointing `sys.stdin` at an in-memory copy of
the payload:

| Check                  | Result                                                                                                               |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `import` 35 modules    | 76ms; 35/35 succeed; `if __name__ == "__main__"` guards prevent any `main()` from running at import                  |
| `ls -la` → 35 `main()` | **2ms**                                                                                                              |
| Output capture         | `ask` (stdout JSON), `deny` (exit 2), advisory (stderr) all observable from the caller                               |
| stdin reuse            | re-assigning `sys.stdin` to a fresh `StringIO` per call lets each existing `impl.py` read the payload **unmodified** |
| Crash isolation        | wrapping each `main()` in the existing `_lib/_hook_runtime.fail_open` decorator contains a per-hook exception        |

Estimated single-process cost: python3 boot (~50ms) + import (76ms cold, less
once `.pyc` is warm) + logic (~2ms) ≈ **~0.13s**. A ~14× reduction for the
common case.

### 1.3 What must be preserved

- **Decision semantics.** With N independent processes, Claude Code adopts the
  most-restrictive decision across them. A single-process runner must reproduce
  that aggregation exactly.
- **Fail-open invariant** ([`ETHOS.md`](../../ETHOS.md)). A crashing gate must
  not block a legitimate action. Today process isolation provides this for
  free; a single process must restore it explicitly.
- **ADR-0001 §2.6 invariants.** Stop-hook sequential ordering, `hosts`
  filtering, `excluded_hooks`, multi-event hook coupling, and the per-platform
  generated `hooks.json` contract are all out of scope and must not change.
- **`manifest.json` as the single registry.** No new parallel registry; the
  dispatch grouping is expressed *in* the existing manifest.

### 1.4 Forces

- **Latency compounds with hook count.** The group was 32 entries at ADR-0001
  time and is 35 now. Every new `preflight-gate` or `advisory-nudge` adds
  another cold-started process to every `Bash` call.
- **Most hooks are no-ops for most commands.** `advisory-nudge` never blocks;
  most `preflight-gate` checks are scoped to a specific command family (`gh pr
  create`, `git commit`, `git push`) and return immediately otherwise. The
  process is paid before that early return.
- **Risk envelope.** Hooks are load-bearing for active sessions. As in
  ADR-0001, any change here ships in phases, and the runtime wiring is the last
  step, not the first.

---

## 2. Decision

Introduce a **single-process dispatch runner** for same-`(event, matcher)` hook
groups, starting with `PreToolUse(Bash)`.

### 2.1 Runner: `hooks/_lib/_dispatch.py`

A new shared module that:

1. Reads the hook JSON from stdin **once**.
2. Resolves the ordered list of `impl.py` for the requested `(event, matcher)`
   group from `manifest.json`.
3. For each hook, re-assigns `sys.stdin` to a fresh `StringIO` of the payload,
   captures stdout/stderr, and calls its `fail_open`-wrapped `main()`.
4. Aggregates the per-hook results into one decision and emits it.

Existing `impl.py` files are **not modified**: they keep reading stdin and
returning an `int` exactly as today. The dispatcher adapts around them.

### 2.1a Scope: exact-`Bash` matcher only

The `PreToolUse(Bash)` group is the **32** hooks whose manifest `matcher` is
exactly `Bash`. Three `advisory-nudge` hooks carry multi-tool matchers and are
**not** in this group:

- `memory-hint` (`Bash|Edit|Write|NotebookEdit|AskUserQuestion`)
- `external-api-literal-trigger` (`Write|Edit|Bash`)
- `block-personal-asset-leak` (`Write|Edit|Bash` — left the group when issue
  #658 added Write/Edit surfaces; the §1.1 measurement above predates the
  change and counted it as exact-`Bash`)

Folding a multi-tool hook into a Bash-only runner would drop its
Edit/Write/NotebookEdit firing, so these keep their standalone wrappers in this
phase. Registering such a hook into each of its tool groups is a follow-up.
Net: 35 hooks fire on a `Bash` call, **32 are consolidated**, 3 stay independent.

### 2.2 Decision aggregation (most-restrictive wins)

Reproduces the current multi-process semantics:

| Condition (first match wins)                                | Dispatcher result                     |
| ----------------------------------------------------------- | ------------------------------------- |
| any hook → `deny` (exit 2, or `permissionDecision: "deny"`) | propagate `deny` (+ reasons)          |
| else any hook → `ask` (`permissionDecision: "ask"`)         | propagate `ask` (+ reasons)           |
| else any `advisory-nudge` stderr/`additionalContext`        | accumulate and emit as context, allow |
| else                                                        | exit 0 (transparent pass-through)     |

Aggregation is **role-agnostic**: the dispatcher classifies each member's result
purely by exit code (`2`) or the `permissionDecision` marker on stdout, never by
`role`. This is deliberate — some `advisory-nudge` hooks DO emit `ask`/`deny`
(`destructive-bash-guard` calls `emit_decision("ask")` under
`PRAXIS_DESTRUCTIVE_BASH_STRICT`; `output-block-falsify-advisory` emits both
`ask` and `deny`), so a role-gated split would silently drop their gate
decisions. `role` is used only for path/module naming, not for deny/ask
eligibility.

### 2.3 Isolation: reuse `fail_open`

Each `main()` is invoked through the existing
`_lib/_hook_runtime.fail_open` decorator. A hook that raises is logged and
treated as a pass (exit 0 equivalent), so one buggy `impl.py` cannot block the
tool call or abort the other 34 checks — restoring the guarantee that process
isolation gave for free.

### 2.4 Import strategy: eager

The dispatcher imports all hooks in the group up front (76ms measured, faster
warm). Command-classification-based lazy import (only import hooks that could
fire for this command) is a possible follow-up but is **not** part of this
decision — eager import is already well under the per-process baseline.

### 2.5 What does NOT change

- Per-platform generated `hooks.json` schema and the "generated, never
  hand-edited" invariant (ADR-0001 §1.3).
- `hosts` filtering, `excluded_hooks`, `excluded_skills`.
- Stop-hook sequential ordering and the `completion-verify` role.
- `UserPromptSubmit`, `Edit`/`Write`/`NotebookEdit` matchers, and every event
  other than `PreToolUse(Bash)` (future groups are a follow-up, not this ADR).
  Extended since: the `Edit|Write`, `Edit|NotebookEdit|Write` and
  `Bash|Edit|Write` groups (#1168), the Stop decision lane (#1169), and the
  `PostToolUse(Bash)` group (#1239) — see §7. A PostToolUse group reuses the
  event-agnostic lanes (exit 2, `additionalContext` merge) with one
  difference: an exit-2 member does not end the group. The tool has already
  run and standalone every member ran regardless of its siblings, so the
  group runs the whole roster and returns 2 afterwards (a PreToolUse deny
  still returns at once — the blocked call has nothing left to gate).
  `bypass-telemetry` is declared first in the manifest: members run in
  array order, and the one still ahead when the deadline is spent is the
  one skipped — the audit record must not be it. It does NOT recognise a top-level `{"decision": "block"}` on PostToolUse,
  which no member emits today — a member that starts to would need its own
  lane, the way Stop got one.
  The `Stop` group (#1281) closed the last high-fan-out event: thirteen
  interpreter cold starts per Stop became one dispatcher node plus the
  standalone `strike-counter stop` node (an `args` member, kept outside by
  the #1199 rule). Two things made it possible. A Stop advisory lane merges
  members' top-level `systemMessage` strings — the field is common to every
  hook event, so it rides on the block object when a sibling blocks — where
  before the context merge dropped them. And `run_one` runs a `body:
  impl.sh` member as a subprocess under the member deadline, with the
  child's own fire-ledger arming switched off so the group records it
  once; the build's "no shell body in a group" gate was removed with it.
  Members that read the current turn share one transcript parse per group
  run (`_transcript.enable_turn_memo`, keyed on size and mtime).
- Each hook's `impl.py` source and its `spec.md`.
- `_lib/_hook_utils.py` / `_lib/_hook_io.py` public API.

---

## 3. Consequences

### 3.1 Positive

| Metric                                               | Before                  | After                             |
| ---------------------------------------------------- | ----------------------- | --------------------------------- |
| python3 processes per `Bash` call (PreToolUse group) | 35                      | 1 (+3 multi-matcher standalone)   |
| `PreToolUse(Bash)` wall-clock (common command)       | ~1.87s                  | ~0.13s                            |
| Cost growth per added hook in the group              | +1 cold-started process | +1 in-process `main()` call (~ms) |

### 3.2 Negative / Costs

- **Serialized heavy gates.** A command that legitimately fires several heavy
  `preflight-gate` checks at once (e.g. PR creation, where gates run `git log`
  / `gh search`) runs them sequentially instead of in parallel. Prototype
  measured ~802ms for that path vs ~600ms parallel — ~200ms on a rare,
  high-stakes command. Mitigation/follow-up: thread-parallelize only the
  network-bound preflight gates.
- **Single fault domain.** A crash in one `impl.py` now executes inside the
  shared process. Mitigated by §2.3 (`fail_open`); a regression in `fail_open`
  itself would be higher-impact than before.
- **Dispatcher is new shared surface.** `_dispatch.py` becomes load-bearing for
  every `Bash` call. Covered by the §5 phased rollout (built and tested off the
  runtime path before wiring).

### 3.3 Risks

| Risk                                                                          | Mitigation                                                                                                                                                               |
| ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Aggregated decision differs from the current per-process outcome              | Phase 2 ships a test that runs all 35 hooks through the dispatcher vs directly and asserts output/exit equivalence per hook, for both a no-op and a gate-firing payload. |
| A hook relies on process-exit side effects (atexit, fd close)                 | Audit during Phase 2; none observed in the prototype, but the equivalence test is the gate.                                                                              |
| stdin reinjection misses a hook that reads `sys.argv` or env instead of stdin | Dispatcher passes through `sys.argv` and the process env unchanged; only stdin is swapped.                                                                               |
| Stop/other groups accidentally pulled in                                      | Scope is `PreToolUse(Bash)` only; manifest grouping is explicit and asserted by `check-plugin-manifests.py` (Phase 4).                                                   |

---

## 4. Alternatives Considered

### 4.1 Wrapper inlining (ADR-0001 §4.3)

Emit `python3 ${CLAUDE_PLUGIN_ROOT}/.../impl.py` directly in `hooks.json`,
dropping the `.sh` wrapper. **Rejected as a solution to this problem**: it
removes the ~1ms wrapper but leaves 35 python3 processes, so it does not move
the 1.87s. Still worth doing for its own (boilerplate) reasons, independently.

### 4.2 Lazy / command-classified import

Classify the command first, import only the hooks that could fire. **Deferred.**
Eager import is already 76ms; classification adds a command→hook mapping to
maintain. Revisit if eager import grows materially.

### 4.3 Thread/async-parallel inside one process

Run the 35 `main()` calls concurrently in threads. **Deferred to follow-up.**
Most hooks are 2ms, so parallelism buys nothing for the common case; it only
helps the rare heavy-gate path (§3.2). Sequencing first keeps the aggregation
logic simple and deterministic; threading the heavy gates is an additive
optimization later.

### 4.4 No change

**Rejected.** Latency is paid on every `Bash` call and grows with hook count.
The friction is invisible per-call but compounds across a session.

---

## 5. Implementation phases

Each phase ships as a separate PR. No phase merges until the previous one has
soaked for at least one session without regressions (mirrors ADR-0001 §5).

### 5.1 Phase 1 — this ADR

Decision record only. No code change.

### 5.2 Phase 2 — dispatcher + equivalence test (off the runtime path)

- Add `hooks/_lib/_dispatch.py`.
- Add `tests/hooks/test_dispatch.*`:
  - import all 35 group hooks; assert 35/35 importable with no side effects;
  - for a no-op payload and a gate-firing payload, assert dispatcher output/exit
    == direct per-hook output/exit, hook by hook;
  - assert a deliberately-crashing hook is contained (`fail_open`) and does not
    abort the group;
  - measure and record group latency.
- **Not wired into any `manifest.json` entry** — blast radius 0.

### 5.3 Phase 3 — manifest + build wiring

- Extend `manifest.json` schema with a dispatch-group concept for
  `PreToolUse(Bash)`.
- Update `scripts/build-plugin-manifests.py` to emit one dispatcher command for
  the group instead of 35 per-hook entries, per platform.
- Assert each generated platform `hooks.json` is semantically equivalent
  (same decisions for the equivalence-test payloads) to the pre-Phase-3
  baseline.

### 5.4 Phase 4 — guard + measurement

- Extend `scripts/check-plugin-manifests.py`: dispatch-group ↔ manifest
  consistency; every grouped hook still has its directory and test.
- Re-measure latency with the same `/usr/bin/time -p` method; record before/after.
- Update `ARCHITECTURE.md` hook section.

---

## 6. Open questions

1. **Schema shape for the dispatch group.** Per-hook entries with a shared
   `dispatch: "pretooluse-bash"` tag, or a dedicated group object that lists
   member hook names? The build must still know per-hook `timeout`/`hosts`.
   Recommendation: resolve in Phase 3 against the existing manifest shape.
2. **Per-hook `timeout` under a single process.** Today each hook has its own
   `timeout`; merged, only the group has one wall-clock budget. Recommendation:
   group timeout = max of members, plus an internal soft budget the dispatcher
   logs (not enforces) per hook.
3. **Should advisory-nudge and preflight-gate share one dispatcher or get two?**
   One keeps a single import pass; two cleanly separates the never-block role
   from the can-block role. Recommendation: one runner, role-aware aggregation
   (§2.2), revisit if the split becomes load-bearing.

---

## 7. Decision record

| Date       | Decision                                                                                                              | Decided by         |
| ---------- | --------------------------------------------------------------------------------------------------------------------- | ------------------ |
| 2026-06-05 | ADR drafted, Status = Proposed                                                                                        | praxis maintainers |
| 2026-09-03 | Scope extended to `PostToolUse(Bash)` (#1239); multi-matcher hooks split their `Bash` leg into the exact-`Bash` group | praxis maintainers |
| 2026-09-05 | Status → Accepted. The design has been the live runtime path since `_dispatch.py` and the Rule 14 guard landed (#617); five groups are collapsed today and `ARCHITECTURE.md` documents it as current architecture | praxis maintainers |
| 2026-09-06 | Scope extended to `Stop` (#1281): systemMessage lane, subprocess path for `impl.sh` members, shared turn parse        | praxis maintainers |

---

## 8. References

- [ADR-0001](0001-hook-layout.md) §4.3 (wrapper inlining deferral), §2.6 (invariants), §5 (phased rollout)
- [`hooks/_lib/_hook_runtime.py`](../../hooks/_lib/_hook_runtime.py) — `fail_open`
- [`hooks/_lib/_hook_io.py`](../../hooks/_lib/_hook_io.py) — `emit_decision` / `format_decision`
- [`hooks/manifest.json`](../../hooks/manifest.json) — hook registry
- [`ETHOS.md`](../../ETHOS.md) — fail-open invariant
