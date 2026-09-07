# Architecture

The component graph of praxis — how skills, hooks, providers, and platform
manifests relate. Values come from [`ETHOS.md`](ETHOS.md); implementation
mechanisms come from [`DESIGN.md`](DESIGN.md). This file describes the
*wiring*.

## Architectural shape

Four architectural patterns, one per layer — a reading map for the sections
below.

- **Microkernel (plugin) core.** A small shared kernel (`hooks/_lib/` — the
  fail-open runtime, the dispatcher, and shared helpers) hosts the hook suite
  and the skills as independent plugins. Extending praxis at the kernel level
  means adding one hook directory plus one manifest entry
  ([`CONTRIBUTING.md`](CONTRIBUTING.md) walks the full checklist); the kernel
  only executes, isolates, and aggregates. Counts live in
  [`README.md` → Hooks](README.md#hooks) and the generated
  [Hook Operating Matrix](docs/hook-operating-matrix.md), not here.
- **Interceptor chain, most-restrictive-wins.** Hooks intercept the host's
  lifecycle (PreToolUse → PostToolUse / PostToolUseFailure → Stop /
  SubagentStop, plus UserPromptSubmit and SessionStart). Unlike a classic
  chain-of-responsibility, every member runs
  and decisions aggregate `deny > ask > allow`
  ([§Single-process dispatch](#single-process-dispatch-adr-0002)), with each
  member fail-open isolated.
- **Ports-and-adapters packaging.** The runtime core (`skills/`, `hooks/`,
  `scripts/`) knows nothing about platforms; per-platform artifacts are
  build-time adapters generated from `manifests/`
  ([§Multi-Platform Packaging](#multi-platform-packaging)). Adding a platform
  is one manifest file, one entry in the schema's `hosts` enum, and one
  build run.
- **Declared state + drift gate.** Generated artifacts are committed, and
  `scripts/check-plugin-manifests.py` invariants enforce manifest ↔ output
  parity in CI — the same reconciliation model infrastructure-as-code uses.

The structure is self-similar: to its hosts praxis is a plugin; inside, it is
a microkernel made of plugins.

## Provider Routing

Skills that dispatch external CLI workers (`cmux-delegate`) can route tasks to multiple AI providers. When only `claude` is installed, the system behaves exactly as before — no errors, no degradation.

### Provider CLI Spec

| Provider | Non-interactive command | Output format | Stdin prompt | Write access |
| ---------- | ---------------------- | --------------- | ------------- | ------------- |
| `claude` | `cat $F \| claude --model {m} --output-format stream-json --permission-mode auto` | stream-json (JSONL) | `cat file \| claude` | Full |
| `codex` | `cat $F \| codex exec {m:+-m m} -o $RESULT_FILE` | stdout verbose logs + last message isolated in `$RESULT_FILE` (preferred); `--json` JSONL also supported | `cat file \| codex exec` | Sandbox-restricted — explicit fallback required |
| `gemini` | `gemini -p "$(cat $F)" --approval-mode yolo {m:+-m m}` | stream-json (`-o stream-json`) | via `-p` flag | Full |

There is no completion sentinel in this repository. The line documenting
`; echo '===WORKER_DONE===' >> $LOG` as a shared convention has been removed (#1054):
`grep -rn WORKER_DONE` found that sentence and nothing else — no caller ever appended
it and no consumer ever waited on it. It also cannot hold for `cmux-delegate`, whose
claude worker stays interactive and does not exit after the task. That skill is
fire-and-forget: nothing reports completion back to the delegator, and the user
reads the result in the worker's own cmux tab.

The `claude` row's stdin column carries a precondition the command does not state.
`claude --help` says the workspace trust dialog is skipped "via `-p`, or when
stdout is not a TTY", and that dialog reads stdin — so a caller that runs the row's
command with stdout inherited from a terminal, in a directory the user has never
trusted, has a dialog and a piped prompt competing for the same stream. Neither
exemption is supplied by the row itself. Using the stdin column obliges the caller
to supply one: redirect stdout, or add `-p`.

**`cmux-delegate` does not use the stdin column** (#1054). Its worker has to stay an
ordinary session — a live TTY on both descriptors — so it passes the prompt as argv
instead: `claude --model {m} "$(cat $F)"`. Command substitution inside double quotes
is not re-expanded by the shell, so this is exactly as safe as the pipe for `$`,
backticks, and braces, while leaving fd 0 on the terminal. A piped stdin closes when
`cat` exits, and the worker then has no channel to answer a permission prompt or a
trust dialog on — measured: it prints "Awaiting your confirmation" and exits 0. The
precondition above is moot for that caller, because a human can answer the dialog.
The stdin column stays correct for genuinely non-interactive callers, which must
still supply one of the two exemptions.

### Model Notation

Unified `--model` flag across all skills: `<provider>:<model>` or bare model name.

| Notation | Resolves to | CLI command |
| ---------- | ------------- | ------------- |
| `opus`, `sonnet`, `haiku` | `claude:{name}` | `claude --model {name}` |
| `claude` | Claude default model | `claude` |
| `claude:opus` | Claude Opus | `claude --model opus` |
| `codex` | Codex default model | `codex exec` |
| `codex:o3` | Codex with o3 | `codex exec -m o3` |
| `gemini` | Gemini default model | `gemini` |
| `gemini:flash` | Gemini Flash | `gemini -m flash` |

Bare names (`opus`, `sonnet`, `haiku`) always resolve to Claude — full backward compatibility.

### Task-Type Routing

Two-phase routing: task keywords select the provider, then complexity selects the model.

**Phase 1 — Task type to provider:**

| Task pattern | Provider | Rationale |
| ------------- | ---------- | ----------- |
| implement, fix, refactor, code generation | `codex` | Code-centric, fast execution |
| search, analyze, summarize, large context | `gemini` | Large context window, search integration |
| review, design, architecture, security, debug | `claude` | Reasoning depth, nuanced judgment |
| Default (unmatched) | `claude` | Safe default |

**Phase 2 — Complexity to model (claude only; codex/gemini use provider defaults):**

| Provider | Low | Medium | High |
| ---------- | ----- | -------- | ------ |
| `claude` | haiku | sonnet | opus |
| `codex` | (default) | (default) | (default or explicit) |
| `gemini` | (default) | (default) | (default or explicit) |

### Fallback Policy

1. **Pre-flight**: `command -v <cli>` before dispatch. If missing → fall back to `claude:sonnet` with warning.
2. **Runtime**: Worker failure → re-dispatch with `claude` as fallback provider.
3. **Graceful**: If only `claude` is installed, all routing resolves to claude. Original behavior preserved.

> **codex write detection**: After a codex worker completes, run `git status` to verify files were actually written. An empty diff after a code-generation task is a strong signal of sandbox write failure — trigger a claude fallback re-dispatch immediately.
> <!-- TODO: automate re-dispatch on empty git diff -->

### Provider Resolution Logic

Skills parse `--model` using this algorithm:

```
input = "--model" value

if input matches /^(codex|gemini)(?::(.+))?$/:
  provider = match[1]           # "codex" or "gemini"
  sub_model = match[2] || ""    # "" or "o3" or "flash" (colon stripped)
elif input in ["opus", "sonnet", "haiku"]:
  provider = "claude"
  sub_model = input
elif input matches /^claude(?::(.+))?$/:
  provider = "claude"
  sub_model = match[1] || ""
else:
  provider = "claude"
  sub_model = input
```

## Hook index

The per-hook list is not maintained here. Two documents carry it:

- [`docs/hook/INDEX.md`](docs/hook/INDEX.md) — the hand-maintained index,
  grouped by role (`preflight-gate` / `advisory-nudge` / `postuse-correction`
  / `completion-verify`), linking each hook to its `hooks/<role>/<name>/spec.md`.
  `scripts/check-plugin-manifests.py` (Rule 7) fails when a manifest hook is
  missing from it.
- [`docs/hook-operating-matrix.md`](docs/hook-operating-matrix.md) — the
  operating surface generated from `hooks/manifest.json` by
  `scripts/build-plugin-manifests.py`: role, events, host filter, strict /
  bypass knobs, and the external commands each hook may run.

The spec is the source of truth for what a hook blocks, passes, and how it
fails open; the matrix is the source of truth for how it is registered.

### Single-process dispatch (ADR-0002)

Every `Bash` tool call fires the whole `PreToolUse(Bash)` hook group. Under the
per-hook model each member is a `.sh` wrapper that `exec python3 .../impl.py`, so
one `Bash` call cold-started ~33 python3 interpreters — ~99% of the latency is
interpreter startup, not hook logic. ADR-0002 collapses that group into **one**
python3 process.

- **Declaration.** `hooks/manifest.json` carries a `dispatch_groups` array of
  `{event, matcher}` pairs. Six groups are collapsed today: `(PreToolUse,
  Bash)` — the hooks whose manifest `matcher` is exactly `Bash` (count asserted
  by `tests/hooks/_lib/test_dispatch.py::test_group_members_count_and_roles` —
  keep in sync when adding/removing an exact-`Bash` hook) — plus, since #1168,
  `(PreToolUse, Edit|Write)`, `(PreToolUse, Edit|NotebookEdit|Write)`, and
  `(PreToolUse, Bash|Edit|Write)` (secret-print-redaction-advisory,
  block-personal-asset-leak, external-api-literal-trigger), since #1239
  `(PostToolUse, Bash)` (anchor-comment-gate, push-remote-ref-verify,
  pr-thread-resolve-advisory, bypass-telemetry), and since #1281 `(Stop)` —
  the matcher-less group of the twelve stdin-only Stop gates, declared as
  `{"event": "Stop"}` and rendered with the `-` matcher sentinel;
  `strike-counter stop` stays a standalone node beside it because it reads
  its mode from argv (the #1199 `args` rule). A hook that fires on `Bash`
  and on other tools registers its `Bash` leg as a separate exact-`Bash`
  entry so it joins the group, and keeps its other matcher as its own
  standalone node (fan-out-scope-gate `Agent`, memory-hint
  `AskUserQuestion|Edit|NotebookEdit|Write`, approval-premise-reread-gate
  `mcp__.*`); memory-hint's `Bash` leg is declared first in that roster so
  its hint still precedes a deny (see its spec). Groups are keyed
  by the FULL matcher string, so a multi-tool group never merges into the
  exact-`Bash` group — each matcher set runs exactly the members that declare
  it, on exactly the tools it names. The `Edit|Write` and
  `Edit|NotebookEdit|Write` groups are deliberately separate for the same
  reason: folding an `Edit|Write` hook into the NotebookEdit group would make
  it fire on NotebookEdit calls its matcher never covered. Matchers are
  spelled in canonical (alphabetically sorted) token order so identical
  matcher sets share one literal string and can coalesce — enforced by
  `check-plugin-manifests.py` Rule 21. `memory-hint`'s remaining
  `AskUserQuestion|Edit|NotebookEdit|Write` leg keeps its standalone wrapper
  only because it is the sole hook on that matcher — a one-member group
  spawns the same single process a wrapper does, so grouping it buys nothing
  until a second hook shares the matcher.
- **Build path.** For each platform, `build-plugin-manifests.py`
  (`filter_hooks_for_host`) emits, after host filtering, exactly **one** dispatcher
  node per group — `${CLAUDE_PLUGIN_ROOT}/hooks/_dispatch.sh <event> <matcher>
  <host>` — instead of one node per member. The platform `host_id` is baked into
  the command so the runtime applies the same `hosts` filter. The node `timeout`
  is the max of member timeouts (members run sequentially in one process, so the
  budget matches the slowest member's per-process budget, not the sum).
- **Runtime path.** `hooks/_lib/_dispatch.py` reads the payload from stdin once,
  resolves the ordered member list for `(event, matcher)` from the manifest
  (host-filtered to match the build), imports each member's `impl.py`
  in-process — the `if __name__ == "__main__"` guard means importing does **not**
  run `main()` — re-points `sys.stdin` at a fresh copy of the payload per member,
  and runs each member's `main()` through the existing `_hook_runtime.fail_open`
  decorator. Member `impl.py` files are unmodified; the dispatcher adapts around
  them. A `body: impl.sh` member (the Stop group's completion-verify and
  retrospect-mix-check) has no `main()` to import: `run_one` execs it as a
  subprocess with the payload on stdin and the member deadline as its timeout,
  with the child's own `record_fire.sh` arming switched off so the group's
  ledger record is the only one (#1281). Members that read the current turn
  share one transcript parse per group run (`_transcript.enable_turn_memo`).
- **Aggregation (most-restrictive wins).** Decisions are classified
  role-agnostically by exit code / `permissionDecision` marker: any member
  `deny` (exit 2 or `"permissionDecision": "deny"`) → propagate `deny`; else any
  `ask` → propagate `ask`; else allow. Every member's stderr (advisory nudges and
  deny reasons alike) is always forwarded. Role-agnostic detection is deliberate —
  some `advisory-nudge` hooks emit `ask`/`deny` under strict modes, so a role-gated
  split would silently drop their gate decisions. On `Stop`, blocking is carried
  by a top-level `{"decision": "block"}` JSON instead of the exit code: every
  blocking member's reason is merged into one block (#1169), and every advisory
  member's top-level `systemMessage` is merged the same way and rides on the
  block object when a sibling blocks (#1281).
- **Fail-open** ([`ETHOS.md`](ETHOS.md)). Each member runs under `fail_open`, and
  the dispatcher's own `main()` swallows exceptions to a `0` (allow), so a crash
  in one `impl.py` cannot block the tool call or abort the other members —
  restoring the isolation process separation gave for free. Import-time failures
  are forwarded to stderr (visible, not silent) before failing open.
- **Guard.** `scripts/check-plugin-manifests.py` Rule 14 ties the build and
  runtime paths together: for every `dispatch_groups` pair, per platform, the
  committed `hooks.json` must hold exactly one dispatcher node (no leaked member
  node, no second node, correct host args), and `_dispatch.group_members` must
  resolve the same member set the build collapsed, with every `impl.py` present
  on disk. A future manifest or schema edit that breaks the collapse fails CI.

**Measured latency** (`/usr/bin/time -p`, warm caches, no-op `ls -la` payload, 33
members at measurement time, claude host — historical benchmark run, not
resynced to the current member count on every hook addition):

| Path | Wall-clock |
| ------ | ----------- |
| Single-process dispatcher (wired runtime path) | **~0.13s** |
| Reconstructed per-process model (33 wrappers, spawned in parallel) | ~0.46s |

The dispatcher's measured ~0.13s matches the ADR-0002 §1.2 prototype estimate.
The per-process baseline above is the parallel wrapper-spawn cost on the same
bench; the ADR §1.1 figure of 1.87s was measured inside a live, CPU-saturated
Claude Code session (35 hooks) and reflects a harsher orchestration context.
Either way, the cost-growth shape is what changed: each hook added to the group
now costs one in-process `main()` call (~ms) instead of one more cold-started
process on every `Bash` call.

## Multi-Platform Packaging

Runtime source (`skills/`, `hooks/`, `scripts/`) is shared. Platform-specific
packaging is *generated* from canonical metadata, not hand-edited:

- `manifests/plugin.base.json` — shared metadata (name, description, author,
  repository, homepage, category, keywords). `VERSION` is the authoritative
  version string.
- `manifests/platforms/{claude,codex,cursor}.json` — per-platform output list.
- `manifests/platforms/agent-plugins.json` — not a host, a *format*: the
  vendor-neutral [Agent Plugins](https://agent-plugins.org/) 1.0.0 manifest.
- `scripts/build-plugin-manifests.py` — regenerate every artifact. Idempotent.
- `scripts/check-plugin-manifests.py` — CI drift gate. Verifies generated
  files match the source and that the Codex adapter shell's symlinks
  (`plugins/praxis/{skills,hooks,scripts}`) point at the repo root.

Platform manifests support two optional top-level fields:
- `excluded_hooks` — hook script names (without `.sh`) to omit when generating
  `filtered-hooks` outputs. Also serves as compatibility documentation.
- `excluded_skills` — reserved for future per-platform skill filtering.

### Why `plugin.json` declares no `dependencies`

Claude Code plugins can list other plugins in a `plugin.json` `dependencies`
array (`name@marketplace`, with `~` / `^` / `>=` / `=` version ranges). praxis
does not, by decision (#1332, plugin-dependencies reference read 2026-09-06):

- A declared dependency is installed with the plugin and, when it is missing
  or disabled, the dependent plugin is switched off with
  `dependency-unsatisfied`. There is no optional-dependency form — nothing
  that says "more features when present, still works when absent".
- Removing a plugin that another enabled plugin depends on fails outright, so
  declaring oh-my-claudecode would block a user from uninstalling it.
- Cross-marketplace dependencies need the root marketplace's
  `allowCrossMarketplaceDependenciesOn`, which praxis does not control on the
  user's side.

The Standalone tier (`recover-sessions`, the strike skills, `debt`) must work
with none of oh-my-claudecode, cmux, or the codex plugin installed, so a hard
dependency would break the tier model. What praxis carries instead is
declarative: the per-hook `requires` field in `hooks/manifest.json` (#1158),
mirrored by each spec's `Requires:` line (check Rule 20) and by the README's
[Hook dependencies](README.md#hook-dependencies) table (check Rule 27). Runtime
behaviour is unchanged either way — the affected hooks already fail open.

Generated (committed) outputs:

| Path | Consumer |
| ------ | ---------- |
| `.claude-plugin/plugin.json` | Claude plugin root |
| `.claude-plugin/marketplace.json` | Claude marketplace catalog |
| `.agents/plugins/marketplace.json` | Codex marketplace root |
| `plugins/praxis/.codex-plugin/plugin.json` | Codex plugin root |
| `plugins/praxis/{skills,hooks,scripts}` | Symlinks into repo-root runtime |
| `.cursor-plugin/plugin.json` | Cursor IDE plugin root |
| `.cursor-plugin/hooks/hooks.json` | Cursor-compatible hooks (filtered) |
| `plugin.json` | Agent Plugins 1.0.0 portable manifest |

### Agent Plugins portable manifest

The spec locates a plugin's manifest at `plugin.json` in the **plugin root**,
so which hosts this file reaches is decided entirely by where each host thinks
praxis's plugin root is. praxis has two:

| Plugin root | Hosts | Sees the root `plugin.json` |
| ------------- | ------- | ----------------------------- |
| repo root | Claude (`marketplace.json` source `./`), Cursor, spec-only clients | yes |
| `plugins/praxis/` | Codex (`marketplace.json` source `./plugins/praxis`) | **no** |

**Codex is deliberately out of scope here.** Its plugin root is nested, so it
never looks at the repo-root file; covering Codex needs a second output at
`plugins/praxis/plugin.json` and is tracked separately. Do not describe this
manifest as Codex support.

What the covered hosts do with it differs, and only one of them acts on it
today:

- **Claude** ignores it — its docs never mention the standard and it reads
  `.claude-plugin/plugin.json`.
- **Cursor** supports the standard alongside its own format and picks between
  them by inspecting the manifest. Which one wins when both are present is
  undocumented and unverified; if the portable manifest wins, `.cursor-plugin/`
  hooks would stop loading. Verify against a real Cursor install before
  relying on either outcome.
- **Spec-only clients** (ChatGPT, Copilot, VS Code, Kiro) are the intended
  consumers — this file is the only thing praxis ships that they could read.
  No install of any of them has been observed loading it, so treat the
  coverage as designed-for, not demonstrated.

Two constraints hold the manifest together, both gated by
check-plugin-manifests Rule 26 because neither is visible to the byte-identity
drift check:

- **`$schema` is pinned to 1.0.0** — the only published spec version (1.1.0 is
  a working draft). Clients find this file by path and read `$schema` only to
  pick which local validation rules to apply, so an unsupported version is
  fatal on its own: "A missing or unsupported `$schema` rejects the plugin."
  The spec defines no fallback, so a newer pin does not defer to the host's
  own manifest — it just fails to load.
- **No host-specific keys** (`skills`, `hooks`, `mcpServers`, `apps`,
  `interface`). This one is praxis policy, not a spec requirement: conformant
  clients "report and ignore each unknown top-level field, then continue if
  the manifest is otherwise valid", which is exactly the problem — such a key
  would load clean and point at a location nothing reads. Component locations
  are fixed by the spec at `skills/` and `mcp.json`; host data belongs under
  `extensions.<namespace>`.

Spec quotes above are from
[loading and discovery](https://agent-plugins.org/client-implementers/loading-and-discovery).

The spec's portable component types are Agent Skills and MCP servers only —
hooks are explicitly outside v1 and the 1.1.0 draft. So this manifest carries
praxis's skills, not its hooks, and it replaces none of the host manifests
above.

**Do not edit generated files directly.** Change `manifests/*.json` (or
`VERSION`) and re-run the build script. Run `./scripts/check-plugin-manifests.py`
before committing if you touched any packaging surface.

Adding a new platform = one file at `manifests/platforms/<name>.json`, its
`host_id` added to the `hosts` enum in `hooks/manifest.schema.json`
(`tests/test_check_manifest_schema.py` asserts the enum equals the platform
set, so a missing entry fails CI), and one build run. No skill, hook, or
existing-platform changes required. Hooks without a `hosts` field ship to
every platform automatically; a hook with an explicit `hosts` list stays off
the new host until that list (and its `Supported hosts:` line) names it.
