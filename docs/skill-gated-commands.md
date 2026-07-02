# Skill-Gated Commands

The `skill-gate-commands` PreToolUse hook gates configured external-mutation
Bash commands behind a required skill invocation in the current session.
Block (exit 2) when a configured command pattern matches AND its required skill
was NOT invoked this session.

**Default behaviour: NO-OP** — the hook is entirely inert when
`PRAXIS_SKILL_GATED_COMMANDS` is unset or empty.

## Config: `PRAXIS_SKILL_GATED_COMMANDS`

Set this env var (in your shell profile or Claude Code `env` config) to a
comma-separated list of entries, each:

```
<command-pattern>=><required-skill>
```

The `=>` separator is used because skill names routinely contain colons
(e.g. `org:skill-name`) and the arrow-right is unambiguous.

### Supported command patterns

| Pattern | Matches |
| --------- | --------- |
| `gh pr create` | `gh [global-flags] pr create ...` |
| `gh pr merge` | `gh [global-flags] pr merge ...` |
| `git push origin` | `git [global-flags] push [flags] origin ...` |

Global flags (e.g. `-R owner/repo`, `--repo=X`) before the subcommand group
are skipped during matching, so `gh -R owner/repo pr create` still matches the
pattern `gh pr create`.

Additional patterns beyond the three above may be added; unrecognised patterns
fall back to a naive contiguous non-flag token subsequence match.

### Example

Gate `gh pr create` behind `laplace-dev-hub:create-hub-pr` AND
`gh pr merge` behind `praxis:codex-review-wrap`:

```bash
export PRAXIS_SKILL_GATED_COMMANDS="\
gh pr create=>laplace-dev-hub:create-hub-pr,\
gh pr merge=>praxis:codex-review-wrap"
```

### Parsing rules

- Entries are split on commas; leading/trailing whitespace is stripped.
- Each entry is split on the **first `=>`**; everything after is the
  `required-skill` verbatim (so skills containing `=>` are not supported, but
  skill names containing colons are fully supported).
- Malformed entries (no `=>` separator, or empty pattern/skill) are silently
  skipped — fail-safe pass.

## Bypass: `PRAXIS_HOOK_BYPASS_SKILL_GATE`

Set to any non-empty value to bypass the gate for the session:

```bash
PRAXIS_HOOK_BYPASS_SKILL_GATE=1 gh pr create ...
```

Use when the required skill was intentionally skipped (e.g. hotfix under time
pressure) and you accept the responsibility.

## How the gate works

1. On every `Bash` tool call, the hook tokenises the command and checks
   whether it matches a configured pattern.
2. If it matches, the hook scans the session transcript (`transcript_path`
   from the hook payload) for a `Skill` tool_use entry with
   `input.skill == <required-skill>`.
3. One skill invocation anywhere in the session satisfies all subsequent
   matching commands — the same coarse session-level granularity as the other
   skill-gate hooks.

### Fail-open conditions

The hook passes silently (exit 0) when:

- `PRAXIS_SKILL_GATED_COMMANDS` is unset or empty.
- All config entries are malformed.
- `transcript_path` is absent from the payload.
- The transcript file does not exist or is unreadable or exceeds 50 MB.
- The Bash command is unparseable (unbalanced quotes).
- The tool is not `Bash`.
- `PRAXIS_HOOK_BYPASS_SKILL_GATE` is set.

## Related

- Hook spec: [`hooks/preflight-gate/skill-gate-commands/spec.md`](../hooks/preflight-gate/skill-gate-commands/spec.md)
- Sibling (single-skill hardcoded): [`block-commit-without-codex-review`](../hooks/preflight-gate/block-commit-without-codex-review/spec.md)
- Hub opt-in pattern: [`docs/hub-mediated-orgs.md`](hub-mediated-orgs.md)
