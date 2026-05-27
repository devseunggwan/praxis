# PreToolUse Path-Probe Gate

Supported hosts: claude, codex

`hooks/path-probe-gate.sh` intercepts `Write`, `Edit`, and `NotebookEdit` tool
calls and emits a **stderr advisory** (or a hard deny in strict mode) when the
target path is inside a git worktree at a depth greater than one directory below
the worktree root, and the intermediate parent directory has not been recorded
as enumerated in the current session.

### Why this exists

During a retrospect session (praxis issue #386), the agent's first `Write`
after entering a new worktree placed an artifact one directory too shallow.  The
intended target was `<worktree-root>/<repo-subdir>/<content-root>/file.md`; the
Write landed at `<worktree-root>/<content-root>/file.md`.  Recovery required
`mv` + `rmdir`.

Root cause (from the retrospect tracer pass):

- A session-compaction summary cited the main worktree's nested path as ground
  truth.
- The agent generalized that path to a *different* worktree without re-probing
  the sibling's layout.
- A size-cutoff finding in the same retrospect eliminated the cross-check that
  would otherwise have caught the generalization.

This hook is the structural enforcement layer for the global behavioral contract
rule "One-Probe-Before-Action Gate" applied to the `Write` tool surface.

### What is emitted

The hook writes advisory text to stderr and exits 0.  Tool execution is never
blocked in the default mode.

| Condition | Behavior |
|-----------|----------|
| Target directly under worktree root (`depth=0`) | Silent — no intermediate dirs |
| Target at `depth>=1`, parent already enumerated this session | Silent — probe already ran |
| Target at `depth>=1`, parent not yet enumerated | Advisory to stderr (exit 0) |
| Target at `depth>=1`, parent not enumerated, `PRAXIS_PATH_PROBE_STRICT=1` | Hard deny (exit 2) |
| `PRAXIS_PATH_PROBE_SKIP=1` | Silent for all paths |
| Path not inside any known git worktree | Silent (fail-open) |
| git unavailable / timeout | Silent (fail-open) |
| Malformed JSON stdin | Silent (fail-open) |
| Non-target tool name (e.g. Bash) | Silent |

### Advisory message (default mode)

```
[path-probe-gate] Writing to /wt/repo/src/content/file.md — the parent directory
/wt/repo/src/content has not been enumerated this session.
Run `ls /wt/repo/src/content` or `git ls-files /wt/repo/src/content` first to
confirm the correct layout before writing.
Set PRAXIS_PATH_PROBE_STRICT=1 to convert this advisory to a hard block.
```

### Deny message (strict mode)

```
[path-probe-gate] Write blocked: /wt/repo/src/content/file.md targets a directory
(/wt/repo/src/content) that has not been enumerated this session.

The target is 2 level(s) below worktree root /wt/repo.
A session-compaction summary may have generalized a sibling-worktree path
without re-probing the layout.

Required: enumerate the parent directory first:
  ls /wt/repo/src/content
  # or: git ls-files /wt/repo/src/content

After enumerating, retry the Write.
To disable this gate: PRAXIS_PATH_PROBE_SKIP=1
```

### Depth calculation

The hook resolves the target path to an absolute path and computes its depth
relative to the containing worktree root:

- **depth 0**: file is directly under the worktree root (e.g. `/wt/repo/file.md`) → no gate
- **depth 1**: one intermediate directory (e.g. `/wt/repo/src/file.md`) → gate fires on first Write to `/wt/repo/src/`
- **depth 2+**: multiple intermediate directories (e.g. `/wt/repo/src/content/file.md`) → gate fires on first Write to `/wt/repo/src/content/`

The gate checks the **immediate parent directory** (deepest intermediate dir).
Once the parent is recorded as enumerated for the session, subsequent Writes to
the same parent are silent.

### Session-scoped enumeration state

Enumeration markers live under:

```
${TMPDIR:-/tmp}/praxis-path-probe-gate/<session_id_hash>/<parent_hash>
```

A marker's presence means the parent was enumerated in this session.  In
advisory mode the hook records the parent as seen when it first emits the
advisory, so the advisory fires **at most once per (session, parent) pair**.
In strict mode the hook does NOT record the parent — the agent must actually
enumerate (run `ls` / `git ls-files`) and then the gate will pass on retry.

Stale markers are cleaned up by the OS tmp purge policy.

### Worktree detection

The hook runs `git worktree list --porcelain` from the payload's `cwd` field to
obtain the list of known worktree roots.  The target path is matched against
each root (longest match first) to identify the containing worktree.  If the
path is not inside any known worktree, the hook passes through silently.

### Env vars

| Variable | Effect |
|----------|--------|
| `PRAXIS_PATH_PROBE_STRICT=1` | Escalate advisory to hard deny (exit 2) |
| `PRAXIS_PATH_PROBE_SKIP=1` | Disable the hook entirely for this session |

### How to enable

The hook is registered in `hooks/hooks.json` under `PreToolUse` with matcher
`Edit|Write|NotebookEdit`.  When installed as a Claude Code plugin, it is
active automatically.

For a manual `.claude/settings.json` installation:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|NotebookEdit",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/path-probe-gate.sh",
            "timeout": 8
          }
        ]
      }
    ]
  }
}
```

### Relationship to sibling hooks

| Hook | Scope | Overlap |
|------|-------|---------|
| `pre-edit-protected-branch-guard` | Blocks Write/Edit on protected branches | None — different failure mode |
| `bash-worktree-existence-advisory` | Warns when `cd <path>` targets a missing dir | Complementary — fires on navigation step; this hook fires on the Write step |
| `external-write-path-existence-check` | Warns when `gh issue/pr` body references phantom repo paths | None — different tool and surface |

### Parsing guarantees (fail-open)

The hook returns exit 0 on every infrastructure error:

- malformed JSON stdin
- non-target tool invocation (Bash, Read, etc.)
- empty or unresolvable file path
- `python3` unavailable (the shell wrapper handles this)
- git unavailable or timeout
- path outside all known worktrees
- any uncaught exception in the inner logic

### Tests

```bash
bash tests/test_path_probe_gate.sh
```

Covers: depth-0 (silent), depth-1 advisory on first write, depth-1 silent on
second write (deduped), depth-2 advisory on first write, strict mode deny,
strict mode no-record (advisory fires again after restart), skip env var,
path outside worktree (silent), fail-open cases (malformed JSON, non-target
tool, no git).
