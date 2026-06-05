# PreToolUse gh CLI Flag Validator

Supported hosts: claude, codex

`hooks/preflight-gate/gh-flag-verify/impl.py` intercepts every Bash tool call and hard-blocks
`gh <subcommand>` invocations that supply a flag not in the subcommand's
accepted set, emitting `permissionDecision: "deny"` before the command executes.

### Why this exists

Claude routinely pattern-matches a flag from one subcommand to another where
it doesn't exist (e.g. `--base` on `gh issue list`, `--include-prs` on
`gh pr list`). The existing `block-gh-state-all` hook covers one specific
value-based mistake (`--state all` on search); this hook generalises the
pattern to cover unknown-flag mistakes structurally across all listed
subcommands. Flag compatibility table sourced from `gh <subcmd> --help` output
(verified live — see PR #176 for captured --help outputs).

### What is blocked

The hook uses the same structural tokenization pipeline (`safe_tokenize` →
`iter_command_starts` → `strip_prefix`) as sibling hooks. Only subcommands
explicitly listed in the compatibility table are validated; unknown subcommands
pass through silently (fail-open). Inherited flags (`--help`, `-R/--repo`,
`--hostname`, `--color`) are always allowed regardless of subcommand.

| Command | Action |
|---------|--------|
| `gh issue list --base main` | **BLOCKED** (`--base` not valid for `issue list`) |
| `gh pr list --include-prs` | **BLOCKED** (`--include-prs` not valid for `pr list`) |
| `gh issue create --base main` | **BLOCKED** (`--base` not valid for `issue create`) |
| `gh pr create --state open` | **BLOCKED** (`--state` not valid for `pr create`) |
| `gh search repos --state open` | **BLOCKED** (`--state` not valid for `search repos`) |
| `gh issue list --base main` chained after valid cmd | **BLOCKED** (chained segment scanned) |
| `gh -R owner/repo issue list --base main` | **BLOCKED** (global flag stripped before check) |
| `gh issue list --state all` | **PASS** (`--state` is valid for `issue list`) |
| `gh pr list --state merged` | **PASS** (`--state` is valid for `pr list`) |
| `gh search issues --state open` | **PASS** (`--state` is valid for `search issues`) |
| `gh release list --limit 10` | **PASS** (unknown subcommand — silent pass-through) |
| `gh pr comment 1 --body "note --base flag"` | **PASS** (flag text inside quoted string) |
| Non-Bash tool (`Read`, `Write`, etc.) | **PASS** |

### Covered subcommands

Validated against the static compatibility table (flags sourced from `gh --help`,
verified 2026-05-11):

| Subcommand | Notable valid-only flags |
|------------|------------------------|
| `gh search issues` | `--state {open\|closed}`, `--include-prs`, `--app`, `--commenter` |
| `gh search prs` | `--state {open\|closed}`, `--merged`, `--review`, `--checks` |
| `gh search repos` | `--stars`, `--forks`, `--topic` (no `--state`) |
| `gh issue list` | `--state {open\|closed\|all}`, `--mention`, `--search` |
| `gh pr list` | `--state {open\|closed\|merged\|all}`, `--base`, `--head`, `--draft` |
| `gh issue create` | `--title`, `--body`, `--assignee`, `--label`, `--milestone` |
| `gh pr create` | `--base`, `--head`, `--fill`, `--reviewer`, `--draft` |
| `gh issue comment` | `--body`, `--edit-last`, `--delete-last` |
| `gh pr comment` | `--body`, `--edit-last`, `--delete-last` |

### Relationship to block-gh-state-all

`block-gh-state-all` blocks the specific value `--state all` on `gh search`
subcommands (a value-level check). This hook blocks unknown flag *names*
(a flag-presence check). The two hooks co-fire in parallel (PreToolUse hooks
run concurrently); both may block the same command without conflict — deny
precedence ensures the user sees the first denial reason. Removing either
hook does not break the other.

### Response

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "Flag '--base' is not valid for 'gh issue list'. Run 'gh issue list --help' to see accepted flags."
  }
}
```

### Tests

```bash
bash tests/test_gh_flag_verify.sh
```

Covers 26 cases: known-good calls per subcommand (silent), known-bad
single-flag deny paths (`--base` on issue list, `--include-prs` on pr list,
`--base` on issue create, `--state` on pr create, `--state` on search repos),
multiple-flags-one-bad deny, unknown subcommand silent pass-through, non-Bash
tool silent, malformed JSON fail-open, quoted body containing flag text (silent),
chained command with bad flag in second segment, gh global flag stripping, and
short-flag validation.
