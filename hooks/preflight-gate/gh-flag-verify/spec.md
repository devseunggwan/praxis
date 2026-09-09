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

The hook uses the same role-aware structural tokenization
(`_hook_utils.tokenize_with_roles`, issue #263) as sibling hooks. Only subcommands
explicitly listed in the compatibility table are validated; unknown subcommands
pass through silently (fail-open). Inherited flags (`--help`, `-R/--repo`,
`--hostname`, `--color`) are always allowed regardless of subcommand.

| Command | Action |
| --------- | -------- |
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
| ------------ | ------------------------ |
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

### Rewrite arm — `PRAXIS_GH_FLAG_VERIFY_REWRITE=1` (issue #1334)

Off by default. Exported as `1` (surrounding whitespace is stripped before the
comparison), a deny whose offending flag has exactly one plausible correction
is replaced by that correction, handed to the harness through
`hookSpecificOutput.updatedInput` so the call proceeds. Nothing else about the
detection changes.

This arm makes a **guess**, which the sibling `block-gh-state-all` arm does
not. `--state all` is invalid for `gh search` and omitting it returns every
state, so that correction restores an intent the CLI rejected — there is one
answer and no judgement. A misspelled flag has no such property: the hook is
inferring what the caller meant to type. Four guards keep the inference narrow
enough to be worth making, and every one of them falls back to the ordinary
deny:

| Guard | Why |
| ----- | --- |
| exactly one candidate one edit away | `--stat` sits one edit from both `--state` and `--stats`; with two candidates the hook has no basis to pick, and guessing replaces a round-trip the actor resolves in one turn with a wrong flag they never chose |
| long flags only | `-b` is one edit from `-B`, `-a`, and every other single letter the subcommand accepts, so uniqueness carries no information about intent at that length |
| the value arity has to match in both directions | a value-taking flag given no value leaves a command gh still rejects; so does a value-less flag inheriting the offender's value (`--wed open` → `--web open`, where `open` becomes a positional gh does not accept). Either way the swap trades one error for another |
| single segment, no line continuation | same reasoning as the sibling arm: the correction must change exactly the one thing it claims to change |

The swap is textual (the tokenizer keeps no offsets, so rebuilding the command
from tokens would lose the caller's quoting) and then certified by
re-tokenizing: the result must match the original tokens with exactly one
position changed, and that position must be the offending flag.

The corrected call is announced on the same object, as `additionalContext` —
naming both flags and both commands. Not decoration: the transcript's
`tool_use` record keeps the command the model wrote (measured on Claude Code
2.1.266), so an unannounced rewrite is invisible to the actor, to a reviewer
reading the transcript, and to the praxis hooks that scan prior calls.

Each firing is recorded in the fire ledger with decision `rewrite`
(`_fire_ledger.DECISION_REWRITE`), which is what the promotion-to-default
decision is measured on — and for an arm that guesses, that measurement is the
whole basis for ever promoting it.

### Tests

```bash
bash tests/hooks/preflight-gate/test_gh_flag_verify.sh
python3 -m pytest tests/hooks/preflight-gate/test_gh_flag_verify_rewrite.py
```

The pytest file covers the rewrite arm: the two value spellings it corrects,
the other `tool_input` fields surviving, the context naming both flags, the
corrected command no longer denying, the arm switch across
unset/`0`/`""`/`true`/`11`, and each guard's fallback to the deny (two
candidates, no candidate, a short flag, a missing value, a compound command, a
line continuation) plus `_edit_distance_1`'s own insert/delete/substitute
table.

Covers 26 cases: known-good calls per subcommand (silent), known-bad
single-flag deny paths (`--base` on issue list, `--include-prs` on pr list,
`--base` on issue create, `--state` on pr create, `--state` on search repos),
multiple-flags-one-bad deny, unknown subcommand silent pass-through, non-Bash
tool silent, malformed JSON fail-open, quoted body containing flag text (silent),
chained command with bad flag in second segment, gh global flag stripping, and
short-flag validation.
