# PreToolUse Cross-Boundary Pre-Flight

Supported hosts: all

Reference: [Autonomy vs Convention — ETHOS.md](../../../ETHOS.md#autonomy-vs-convention)

`hooks/cross-boundary-preflight.sh` intercepts every Bash tool call and
fires on two cross-boundary patterns before the command executes.

### Why this exists

Five documented session failures share one meta-pattern: a rule existed in
context but had no execution-time retrieval trigger at the action boundary.
Memory entries for each pattern were written after each incident, but the
same violations recurred on the next relevant session. This hook replaces
the memo with a structural gate that fires at the command boundary (praxis
issue #199).

The two patterns covered:

| Pattern | Trigger | Action |
|---------|---------|--------|
| `HEREDOC_BODY` | `<<` token in same segment as `gh pr/issue create` | **Hard block** (exit 2) — suggests `--body-file` |
| `CROSS_REPO_WRITE` | `--repo/-R` flag in `gh pr/issue create/comment/edit` | **Ask** — surfaces four-point checklist |

### What is blocked / asked

The hook uses `safe_tokenize → iter_command_starts → strip_prefix` (same
pipeline as sibling hooks) so only live `gh` invocations match. Pattern
references inside quoted arguments, echo/grep/commit bodies, or preceding
variable assignments are transparent pass-throughs.

#### HEREDOC_BODY — hard block (exit 2)

| Command | Action |
|---------|--------|
| `gh issue create --title "t" <<EOF` | **BLOCKED** |
| `gh pr create --title "t" <<'EOF'` | **BLOCKED** |
| `gh pr create --title "t" <<-EOF` | **BLOCKED** |
| `gh --repo x issue create --title "t" <<EOF` | **BLOCKED** |
| `BODY=$(cat <<EOF\n...\nEOF\n)\ngh pr create --body "$BODY"` | **PASS** — heredoc in different segment |
| `cat <<EOF > /tmp/f.txt` | **PASS** — non-gh command |

Why heredoc is blocked: `shlex` tokenization does not read heredoc content,
so the `block-pr-without-caller-evidence` hook and `external-write-falsify-check`
hook both see an empty body. Caller-chain evidence and falsification checks
are bypassed silently.

Correct pattern: `Write tool → /tmp/body.md` then `--body-file /tmp/body.md`.

#### CROSS_REPO_WRITE — ask (permissionDecision: "ask")

| Command | Action |
|---------|--------|
| `gh pr create --repo owner/repo --title "t" --body-file /tmp/b.md` | **ASK** |
| `gh issue create --repo owner/repo --title "t"` | **ASK** |
| `gh issue comment 42 --repo owner/repo --body "..."` | **ASK** |
| `gh -R owner/repo pr create --title "t" --body-file /tmp/b.md` | **ASK** |
| `gh pr create --title "t" --body "Caller chain verified: ok"` | **PASS** — no `--repo` |
| `gh issue list --repo owner/repo` | **PASS** — read-only subcommand |
| `gh pr list --repo owner/repo` | **PASS** — read-only subcommand |
| `gh pr create --repo x --title "t" # cross-boundary:ack` | **PASS** — opt-out |

The checklist surfaced for `pr create` (four points):

1. **Per-action authorization gate** — explicit approval for THIS specific action
2. **Caller chain verified** — PR body must contain `Caller chain verified: <source>` (pr create only)
3. **Body delivery format** — `--body-file`, no heredoc
4. **Language & content rules** — English only, no internal identifiers

The checklist for `issue create / comment / edit` skips item ② (no caller-chain
requirement on issues).

### Response format

**HEREDOC_BODY:**
```
stderr: "❌ BLOCKED: heredoc (`<<`) in `gh pr/issue create` ..."
exit 2
```

**CROSS_REPO_WRITE:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "⚠️  Cross-boundary pre-flight: ..."
  }
}
exit 0
```

### Compound cascade advisory (issue #229)

Both response paths (HEREDOC_BODY block, CROSS_REPO_WRITE ask) append the
shared `_hook_utils.compound_cascade_hint` suffix when the parent Bash command
is compound AND contains a state-changing step (`> file`, `mkdir`, `tee`,
`cp`/`mv`/`rm`, `curl -o`). The classic shape is
`cat <<EOF > /tmp/body.md && gh pr create --body-file /tmp/body.md` — the
heredoc redirect side-effect is aborted together with the gh call, leaving the
agent's retry with `No such file or directory`. The hint instructs the caller
to use the Write tool to materialize the body file first, then issue gh in a
separate Bash call. Single-command rejections do not receive the suffix.

### Opt-out

Known-intentional cross-repo writes can bypass the ASK gate by appending
the opt-out marker to the command:

```bash
gh pr create --repo owner/repo --title "t" --body-file /tmp/b.md  # cross-boundary:ack
```

Use only after manually verifying all four checklist items. Place the marker
in the **shell command portion only** — on the same `gh` line or after the
heredoc terminator, never inside the heredoc body. The heredoc body becomes
the published artifact; a marker placed inside it leaks verbatim into the
issue/PR text on the remote surface.

The marker has no effect on HEREDOC_BODY — that pattern is always blocked
regardless. When the `Write tool → --body-file` path is blocked by another
guard, the block message (stderr) now lists the marker as a 3rd option with
placement guidance.

### Relationship to sibling hooks

| Hook | Scope | Overlap |
|------|-------|---------|
| `block-gh-state-all` | `gh search --state all` | None — different subcommand |
| `block-pr-without-caller-evidence` | `gh pr create` body missing `Caller chain verified:` | Complementary — this hook fires first as a pre-flight; sibling fires if body is present but missing the line |
| `pre-merge-approval-gate` | `gh pr merge` | None — different subcommand |
| `side-effect-scan` | `gh pr create` (gh-merge category) | Complementary — side-effect-scan fires first with a generic "remote trigger" ask; this hook fires with a targeted cross-boundary checklist |

### Tests

```bash
bash tests/test_cross_boundary_preflight.sh
```

Covers 35 cases: 7 heredoc block paths (4 original + 3 F2 regression), 12
cross-repo ask paths (including shorthand flags, chained commands, equals
forms, F1 regression), 2 ask-detail checks (caller chain item present/absent
by subcommand), 1 block-msg content check (new ack-placement bullet present
in stderr), 1 F2 false-positive guard, 9 pass paths (no-repo, read-only,
non-gh, opt-out, variable-heredoc), 2 infrastructure (non-Bash passthrough,
malformed JSON fail-open).
