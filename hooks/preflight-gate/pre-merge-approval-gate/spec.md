# PreToolUse Pre-Merge Approval Gate

Supported hosts: all

`hooks/pre-merge-approval-gate.py` fires on every PreToolUse(Bash) event and
intercepts `gh pr merge` invocations. In direct interactive Claude sessions the
gate emits `permissionDecision: "ask"` so the user sees the merge attempt and
must approve it in the Claude Code permission UI. Background cmux-delegate
agents (identified by `CMUX_DELEGATE=1` in their shell environment) pass
through silently — the delegation intent from the task prompt already carries
the authorization.

### Why this exists

Merge is shared-state and irreversible. A task prompt containing a
"fire-and-forget" or "no STOP gate" directive — intended for background agents
dispatched via `cmux-delegate` — can bleed into direct interactive sessions
that mistakenly apply the same exemption. This hook removes the exemption
ambiguity by making the environment variable (`CMUX_DELEGATE=1`) the sole
signal for the background-agent path.

The per-PR approval rule is already codified in the global `~/.claude/CLAUDE.md` (`No
Approval Transfer Across Companion PRs` and `Pre-Merge Reporting`). This hook
adds structural enforcement so the rule fires even when memory-based feedback
is not retrieved.

### What is blocked

| Scenario | Action |
| ---------- | -------- |
| Direct session (no `CMUX_DELEGATE`), any `gh pr merge` | `permissionDecision: "ask"` |
| Background agent (`CMUX_DELEGATE=1`), any `gh pr merge` | silent pass-through |
| Inline `env CMUX_DELEGATE=1 gh pr merge` from direct session | `ask` — inline env sets the child's env, not the hook's own env |
| `# merge-approval:ack` marker (or any comment text) | `ask` — no agent-attachable bypass exists by design |
| Non-merge gh commands (`gh pr view`, `gh pr list`, etc.) | silent pass-through |
| `git commit -m "merge note"` (merge in message, not a gh call) | silent pass-through |

### Trigger

1. `tool_name == "Bash"` — non-Bash tools exit 0 silently.
2. Tokenize with `_hook_utils.safe_tokenize` + `iter_command_starts` +
   `strip_prefix` and scan every command segment.
3. Any segment whose `argv[0..2] == ("gh", "pr", "merge")` triggers the check
   (`gh` global flags such as `-R/--repo`/`--hostname`/`--color` are skipped
   so `gh -R owner/repo pr merge` is detected correctly).
4. A `--help` / `-h` invocation is not a merge and does not trigger (issue
   #985) — value-flag values are skipped, so `--subject -h` still triggers.
   Heredoc bodies are blanked by `safe_tokenize` for the same reason: a commit
   message quoting `gh pr merge` is prose, not a merge.
5. If `CMUX_DELEGATE=1` in the hook's own process env → pass.
6. Otherwise → emit `permissionDecision: "ask"`.

### Inline env limitation (known)

The hook reads its **own** process environment, not the child's. An inline
`env CMUX_DELEGATE=1 gh pr merge` prefix only sets `CMUX_DELEGATE` for the
child `gh` process — the hook process sees no `CMUX_DELEGATE`. This is
intentional: the only authoritative delegation signal is `CMUX_DELEGATE=1`
set in the session's shell environment at startup (e.g. by `cmux-delegate`
when spawning the agent workspace).

### No opt-out marker (deliberate)

Unlike `side-effect-scan` (`# side-effect:ack`), this hook has **no
agent-attachable bypass**. Issue #180's contract is that direct sessions
ALWAYS surface a per-PR approval prompt — a comment-style marker would let
the agent silently self-bypass the same gate it is meant to enforce. The
only authoritative bypass is `CMUX_DELEGATE=1` in the *session's* shell env
at startup; inline `env CMUX_DELEGATE=1` does not satisfy this (see above).

If a legitimate direct-session merge must proceed, approve the surfaced
prompt — that single confirmation is the approval the rule requires.

### Response

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "gh pr merge detected in a direct interactive session..."
  }
}
```

### Compound cascade advisory (issue #229)

When the ask fires on a compound Bash command that also contains a
state-changing step (e.g. `git fetch && gh pr merge 42` chained with an
`mkdir`/redirect/`curl -o`), the ask reason is suffixed with the shared
`_hook_utils.compound_cascade_hint` text. If the user denies the prompt, all
chained side-effects abort with the merge. Single-command merges (just
`gh pr merge 42`) do not receive the suffix.

### Tests

```bash
bash tests/test_pre_merge_approval_gate.sh
```

Covers direct-session ASK paths (bare, `--merge`, `--delete-branch`),
background-agent SILENT paths, non-merge command SILENT paths,
chained-command ASK paths, quoted-body SILENT (text mentions "gh pr merge"
but is not executed), inline-env ASK, non-Bash tool SILENT, malformed-JSON
SILENT, `gh -R/--repo/--hostname/--color` global-flag handling, and
regression tests confirming the previously-shipped `# merge-approval:ack`
marker no longer bypasses (round 4 finding — agent-attachable bypass
removed by design).
