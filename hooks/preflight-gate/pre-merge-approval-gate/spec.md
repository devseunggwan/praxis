# PreToolUse Pre-Merge Approval Gate

Supported hosts: all

`hooks/preflight-gate/pre-merge-approval-gate/impl.py` fires on every PreToolUse(Bash) event and
intercepts `gh pr merge` invocations. The gate emits
`permissionDecision: "ask"` so the user sees the merge attempt and must approve
it in the Claude Code permission UI. This applies to every session — there is
no environment-variable exemption.

### Why this exists

Merge is shared-state and irreversible. A task prompt containing a
"fire-and-forget" or "no STOP gate" directive — historically written for agents
dispatched via `cmux-delegate` — can bleed into a session where the user is
actively reading responses. This hook removes the ambiguity by gating every
`gh pr merge` unconditionally: no directive in a prompt can stand in for the
per-PR approval.

Issue #180 originally paired this gate with a `CMUX_DELEGATE=1` background-agent
exemption. Issue #1055 removed it. Nothing in the repository ever set that
variable, and the delegated worker now runs with a live stdin (#1054 item A /
PR #1057), so a prompt surfaced inside a delegated pane is answered by the same
human — the premise "the delegation intent is the approval" no longer holds.

The per-PR approval rule is already codified as *No Approval Transfer Across
Companion PRs* and *Pre-Merge Reporting* ([`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries)). This hook
adds structural enforcement so the rule fires even when memory-based feedback
is not retrieved.

### What is blocked

| Scenario | Action |
| ---------- | -------- |
| Any session, any `gh pr merge` | `permissionDecision: "ask"` |
| Any env-var prefix (`env FOO=1 gh pr merge`) | `ask` — no environment variable exempts a merge |
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
5. Emit `permissionDecision: "ask"`.

### No opt-out marker (deliberate)

Unlike `side-effect-scan` (`# side-effect:ack`), this hook has **no
agent-attachable bypass** and, since #1055, no environment bypass either. The
contract is that a merge ALWAYS surfaces a per-PR approval prompt — a
comment-style marker would let the agent silently self-bypass the same gate it
is meant to enforce.

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
bash tests/hooks/preflight-gate/test_pre_merge_approval_gate.sh
```

Covers ASK paths (bare, `--merge`, `--delete-branch`), ASK under
`CMUX_DELEGATE=1` (regression guard for the #1055 exemption removal),
non-merge command SILENT paths,
chained-command ASK paths, quoted-body SILENT (text mentions "gh pr merge"
but is not executed), inline-env ASK, non-Bash tool SILENT, malformed-JSON
SILENT, `gh -R/--repo/--hostname/--color` global-flag handling, and
regression tests confirming the previously-shipped `# merge-approval:ack`
marker no longer bypasses (round 4 finding — agent-attachable bypass
removed by design).
