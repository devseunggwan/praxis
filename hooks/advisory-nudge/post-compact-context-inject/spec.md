# UserPromptSubmit Post-Compact Context Injection

Supported hosts: claude (Claude Code's `UserPromptSubmit` is the only host whose `additionalContext` survives the compaction boundary — Codex/Cursor/Gemini have no equivalent surface)

`hooks/advisory-nudge/post-compact-context-inject/impl.sh` fires on every `UserPromptSubmit` event and emits a single `additionalContext` block carrying session-preservation info on the FIRST prompt after a Claude Code `/compact`.

### Why this exists

Claude Code's `/compact` operation discards transient session state — active PR/issue context, the worktree absolute path, the current branch, and praxis strike state — by replacing the live transcript with a model-generated summary. The agent then resumes against the summary alone, often re-asking the user "which PR / which worktree / what was the issue number" or silently drifting to a different worktree.

Issue #466 originally proposed solving this with a `PreCompact` hook that would inject `customInstructions` into the summarization prompt. A probe ([#466 comment thread](https://github.com/devseunggwan/praxis/issues/466)) falsified that path — `PreCompact` in Claude Code does NOT support `hookSpecificOutput`/`additionalContext`/`customInstructions`. Issue #472 supersedes #466 by relocating the handoff one step later: on the **next** `UserPromptSubmit`, where `additionalContext` IS supported.

### Mechanism

| Step | Action |
|------|--------|
| 1 | Read payload from stdin: `session_id`, `cwd`, `transcript_path`. |
| 2 | Check session-scoped dedup marker `${PRAXIS_STATE_DIR:-$HOME/.claude/state/praxis}/post-compact/${session_id}.injected`. If present → silent exit. |
| 3 | Tail-scan the last 100 lines of `transcript_path` (JSONL). Look for at least one record with `isCompactSummary == true`. If absent → silent exit. |
| 4 | Collect read-only context: praxis strike state (count + reasons), current branch (`git -C "$cwd" branch --show-current`), open PR for that branch (`gh pr list --state open --head <branch> --json number,url --limit 1`). |
| 5 | Touch the marker FIRST (so a downstream `jq` failure cannot cause a re-fire). |
| 6 | Emit `additionalContext` JSON. |

Dedup is by `session_id`. The marker persists for the full session (TTL cleanup drops files older than 7 days — matching the strike-counter cleanup pattern).

### Detection thresholds

| Input | Action |
|-------|--------|
| `transcript_path` unreadable / missing field | silent — fail-open |
| `session_id` missing | silent — fail-open |
| Tail-100 contains no `isCompactSummary == true` record | silent — first prompts of a fresh session |
| Marker already present | silent — already fired this session |
| `jq` not installed | silent — graceful degrade |
| `gh` not installed / branch has no open PR / not a git repo | injection still fires; PR line is just omitted |
| Strike state file missing | injection still fires; strike count reported as 0/3 |

### Response

```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "🧷 Post-compact session context (auto-injected once after /compact):\n\n- Session ID: ...\n- Worktree: ...\n- Branch: ...\n- Active PR: #N (https://github.com/...)\n- Praxis strikes: K/3\n  Reasons:\n  1. ...\n\n(This block fires once per session ...)"
  }
}
```

The hook does **not** block the prompt. Claude reads the `additionalContext` alongside the user's new prompt and uses the IDs to re-anchor work that spans the compaction boundary.

### Why advisory-nudge, not preflight-gate

Per ADR-0001 / `ETHOS.md`, `preflight-gate` hooks may block or ask; `advisory-nudge` hooks emit context only and never block. Post-compact injection is purely informational — it never affects the decision to run a tool — so `advisory-nudge` is the correct role.

### Fail-open posture

Every external call is wrapped (`2>/dev/null`, `|| true`, `|| echo 0`, `try ... catch 0` for jq). The script uses `set +e` and never relies on `trap ... ERR`. The final fallback is unconditional `exit 0` after emission. This is mandatory because the hook fires on **every** UserPromptSubmit — a crash here would surface as a session-wide failure to receive any user input.

### Tests

```bash
bash tests/hooks/advisory-nudge/test_post_compact_context_inject.sh
```

Covers:
- Fires once when transcript tail contains `isCompactSummary == true` and no marker exists.
- Silent on second invocation (marker now present).
- Silent when transcript has no compact-summary record.
- Fail-open silent on missing `transcript_path`, missing `session_id`, unreadable file, missing `jq`.
- Strike count + branch + PR fields are included when state is present; their absence does not abort the injection.
