# Design

How praxis hooks are built. The mechanisms below implement the [hook
ethos](ETHOS.md#hook-ethos) — they are the concrete primitives a new hook
must reuse so the suite behaves coherently across sessions, shells, and
platforms. The dependency graph between hooks, skills, and manifests lives
in [`ARCHITECTURE.md`](ARCHITECTURE.md); per-hook specs live under
[`docs/hook/<name>.md`](docs/hook/).

## Hook Design Contracts

Every hook ships with full spec in `docs/hook/<name>.md` — design rationale,
matrix of blocked vs. passed commands, response JSON, parsing
guarantees, fail-safe paths, and test summary. CLAUDE.md only carries the
index; consult the per-hook file before editing.

Design mechanisms shared by all hooks:

- **Structural tokenization, not regex.** `hooks/_hook_utils.py`
  (`safe_tokenize` → `iter_command_starts` → `strip_prefix`) is the shared
  primitive. Quoted strings, comments, env prefixes, wrapper commands, and
  shell control-flow keywords are handled consistently across all Bash hooks.
- **Session state via `session_id`.** Per-session memory (intent flags,
  DESCRIBE history) keys on the payload's `session_id` field. PPID is a
  back-compat fallback for direct CLI / test invocation only.
- **Compound-Bash cascade advisory (issue #229).** When a PreToolUse(Bash)
  hook rejects (block) or asks-and-may-deny a compound command (`&&`, `||`,
  `;`, `|`, newline) containing a state-changing step (`> file`, `<<EOF >`,
  `mkdir`, `tee`, `cp`/`mv`/`rm`/`touch`, `curl -o`, `wget -O`), every hook
  appends the shared `_hook_utils.compound_cascade_hint(command)` text to
  its block/ask message. The advisory clarifies that bash never executed
  ANY part of the rejected command — files the redirect/mkdir/download
  would have created do NOT exist on disk — so the agent should not retry
  the second half expecting the first half to have landed. Single-command
  rejections receive no suffix (no cascade to warn about).

## Hook ordering and precedence

- PreToolUse hooks run **in parallel**. Decision precedence is
  `deny > defer > ask > allow`. Order in `hooks.json` is presentational.
- Stop hooks run **sequentially in array order**:
  `completion-verify` → `retrospect-mix-check` → `strike-counter stop`.
  Each gate is independent; first `decision: block` wins, fix it and re-run.
- PostToolUse hooks run **sequentially**; corrective `additionalContext`
  emissions are additive, not exclusive.

## Adding a new hook

1. Survey ≥2 sibling implementations under `hooks/` for the convention
   (state-key naming, payload field access, exit-code semantics). See the
   `Sibling Convention Survey` rule in CLAUDE.md.
2. Write the hook + tests under `hooks/`, register in `hooks/hooks.json`.
3. Create `docs/hook/<name>.md` using an existing spec as the template
   (`Why this exists` / `What is blocked` / `Response` / `Parsing guarantees`
   / `Tests`).
4. Add a row to the index table in [`ARCHITECTURE.md`](ARCHITECTURE.md#hook-index).
5. Run `./scripts/check-plugin-manifests.py` to confirm packaging is clean.
