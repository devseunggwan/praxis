# PreToolUse Worktree Prune Snapshot Gate

Supported hosts: all

`hooks/preflight-gate/worktree-prune-snapshot-gate/impl.py` blocks a bare
`git worktree prune` (no `-n`/`--dry-run`) unless a `git worktree list`
snapshot has already been taken earlier in the same session (or earlier in
the same compound command).

## Why this exists (issue #870, Refs #865)

`git worktree prune` takes no target argument — it removes the
administrative files of **every** unlocked worktree whose directory has
disappeared, not just the one the agent intended to clean up. Issue #865 /
PR #867 documented a snapshot -> dry-run -> STOP gate -> post-diff procedure
in prose (`praxis:worktree-merge-cleanup` skill), but no hook enforced it. A
2026-07-27 session retrospect (finding #3, HIGH — flagged by an external
critic as "the biggest engineering misjudgement of the session", original:
"이번 세션 최대의 엔지니어링 오판") found the gap:
`grep -rln "worktree prune" hooks/` returned nothing. The pattern is
deterministic and checkable before execution, and the sibling failure mode
(`gh pr merge --delete-branch` vs. a checked-out worktree) is already
enforced structurally by `gh-merge-worktree-precondition` — this hook closes
the equivalent gap for bare prune.

## Snapshot-recorded decision: state file, not transcript tail

The task named two candidate designs; this hook uses a **session-scoped
state file** keyed by `session_id`, the same primitive `session-intent`
already established (`hooks/preflight-gate/session-intent/impl.py`). A
transcript-tail scan was rejected: it would need to re-parse the full
session's tool-call history on every Bash invocation (unbounded, growing
cost) and the hook payload has no stable API for "list of prior commands
this session" — only `session_id` itself. The state file is O(1) per call
and mirrors the repo convention.

State file location (priority order):

1. `PRAXIS_WORKTREE_PRUNE_SNAPSHOT_FILE` env var (explicit override; used by
   tests).
2. `<PRAXIS_HOME>/cache/worktree-prune-snapshot-${session_id}.json` when the
   payload carries `session_id` (primary key). Pre-#903 this lived under
   `${TMPDIR}`; `resolve_cache_file` adopts that file if it is still there.
3. `${PPID}` replaces `${session_id}` in the filename (back-compat fallback
   for direct CLI/test invocation without a payload).

State file shape: `{"snapshot_taken": true}`. The flag is sticky once set —
it never resets within a session (mirrors `session-intent`'s
`mutation_verb_seen`).

## Detection

1. `tool_name == "Bash"` — non-Bash tools exit 0 silently.
2. Tokenize with `_hook_utils.safe_tokenize` + `iter_command_starts`
   (compound commands decomposed segment by segment, in the order they
   appear).
3. A segment matching `git [-C <dir>|-c <k=v>|<bare flag>]* worktree list
   [...]` marks the snapshot taken — persisted immediately AND visible to
   later segments in the *same* compound command (so `git worktree list
   --porcelain > /tmp/x && git worktree prune` is self-satisfying in one
   call). The `--porcelain` flag is not required — any `git worktree list`
   invocation counts, since the point is "the agent has seen the worktree
   set before pruning," not the exact output format.
4. A segment matching `git [-C <dir>|-c <k=v>|<bare flag>]* worktree prune
   [...]` is the gated action:
   - `-n`/`--dry-run` present anywhere in the tail -> always silent pass (no
     destructive effect regardless of snapshot state).
   - no dry-run flag AND no snapshot recorded (this call or a prior one this
     session) -> **block** (exit 2).
   - no dry-run flag AND a snapshot was recorded -> silent pass.

## Response format

**Block** (`block_message.emit_block`, exit 2):

```text
⚠️ WORKTREE-PRUNE-SNAPSHOT-GATE blocked

Why: `git worktree prune` takes no target argument — it removes the
administrative files of EVERY unlocked worktree whose directory has
disappeared, not just the one you intended to clean up (issue #865).
Correct path: Run `git worktree list --porcelain` first to snapshot the
current worktree set, then re-run the prune (add `> /tmp/<file>` to keep
the snapshot for a post-prune diff). See `praxis:worktree-merge-cleanup`
for the full snapshot -> dry-run -> STOP gate -> post-diff procedure. A
dry run (`git worktree prune -n`) is always allowed without a snapshot.
Bypass (if truly needed): PRAXIS_HOOK_BYPASS_WORKTREE_PRUNE_SNAPSHOT=1 with
a one-line reason comment explaining why
Reference: issue #870, #865
```

A compound-cascade hint (`_hook_utils.compound_cascade_hint`) is appended
when the blocked command is part of a state-changing compound chain.

**Pass**: no output, exit 0.

## Modes

| Env var | Effect |
| --------- | -------- |
| `PRAXIS_HOOK_BYPASS_WORKTREE_PRUNE_SNAPSHOT=1` | Full bypass — exit 0 silently, no state read/write. |
| `PRAXIS_WORKTREE_PRUNE_SNAPSHOT_FILE=<path>` | Explicit state-file path override (tests). |

## Fail-open

- malformed JSON stdin -> exit 0
- non-Bash tool -> exit 0
- empty/whitespace command -> exit 0
- state-file read/write failure -> treated as "no snapshot recorded" (gate
  still fires on read failure; a write failure is swallowed and simply means
  the session forgets its snapshot, equivalent to a fresh session)
- any uncaught exception in inner logic -> swallowed via `@fail_open`, exit 0

This hook only blocks on a positively-confirmed bare-prune-without-snapshot;
it never blocks on an inability to determine session state.

## Relationship to sibling hooks

| Hook | Scope | Overlap |
| ------ | ------- | --------- |
| `gh-merge-worktree-precondition` | Blocks `gh pr merge --delete-branch` when the head branch is still checked out in a worktree | Same failure family (worktree-registry hazard around merge/prune), different trigger command and different check (this hook checks session history, not live `git worktree list` state at gate time) |
| `session-intent` | Session-scoped read-intent vs. mutation-pivot drift, same state-file-keyed-by-`session_id` primitive | Shares the state-file design, unrelated detection domain |
| `destructive-bash-guard` | Advisory nudge for `rm -rf`, `git clean -f`, `git reset --hard`, etc. | Different role (advisory vs. preflight-gate) and different failure mode — that hook flags destructive commands unconditionally; this hook conditions the block on *absence of a prior snapshot*, not on the command itself being inherently destructive |

## Registration status (deferred)

This hook is **not yet registered** in `hooks/manifest.json`, the generated
platform `hooks.json` files, `docs/hook/INDEX.md`, or `ARCHITECTURE.md`.
Nine hook issues across three Phase 2 lanes ship in this window; registration
and manifest regeneration (`scripts/build-plugin-manifests.py`) happen once,
after all lanes merge, to avoid nine separate manifest-conflict rebases.
`scripts/check-plugin-manifests.py` will report this hook as unregistered
until that integration step runs — expected, not a defect in this PR.

## Tests

```bash
bash tests/hooks/preflight-gate/test_worktree_prune_snapshot_gate.sh
```

15 cases: bare prune with/without a prior snapshot (block/pass), `-n` and
`--dry-run` forms (always pass regardless of snapshot state), snapshot
recorded in an earlier call vs. the same compound command, `git worktree
list` with and without `--porcelain`, `git -C <dir>` global-flag handling for
both `list` and `prune`, `git worktree add`/`remove` are not pruned,
unrelated git/Bash commands, non-Bash tool passthrough, malformed-JSON
fail-open, and the bypass env var.
