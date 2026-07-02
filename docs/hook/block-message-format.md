# Standard block-message format (issue #439)

Every praxis preflight-gate hook that rejects a command emits a block message
in one shared five-field format. Before #439 each hook hand-rolled its own
stderr text, so the wording, field order, and "how do I get past this" guidance
drifted hook-to-hook. The standard format pins the shape so the agent — and the
human reading the transcript — sees the same structure every time.

## The format

```
⚠️ <RULE_NAME> blocked

Why: <one line — which convention is violated>
Correct path: <specific next action — skill name, command, or pattern>
Bypass (if truly needed): <env var name>=1 <reason-comment requirement>
Reference: <CLAUDE.md section, docs/ path, or wiki link>
```

## The helper

- Python hooks: `hooks/_lib/block_message.py`
  - `format_block(rule_name, why, correct_path, bypass_env, reference)` → string
    (no I/O). Use when the message is carried in a JSON `permissionDecisionReason`.
  - `emit_block(...)` → writes the formatted string to stderr (+ newline). Use on
    the stderr / `exit 2` block path.
- Shell hooks: `hooks/_lib/block_message.sh`
  - source it, then `praxis_format_block` (stdout) / `praxis_emit_block` (stderr).
  - Output is byte-identical to the Python helper.

Neither helper exits the process or prints the compound-cascade hint — the
caller keeps control of the exit code and may append
`compound_cascade_hint(command)` after the block text.

## Field semantics: mandatory vs informational

| Field | Status | Notes |
| ------- | -------- | ------- |
| `rule_name` | **Mandatory** | Short rule label; rendered uppercased in the header. |
| `Why` | **Mandatory** | One line naming the violated convention. Not the symptom — the rule. |
| `Correct path` | **Mandatory** | The concrete next action: a skill name, an exact command, or a pattern. Avoid "fix it" — say *how*. |
| `Reference` | **Mandatory** | Where to read more: a `CLAUDE.md` section, a `docs/hook/<name>.md` path, or a wiki link. Gives the reader the authoritative source. |
| `Bypass` | **Informational / conditional** | Rendered only when the gate has an authoritative bypass env var. Pass `bypass_env=None` (Python) or `""` (shell) to omit the line entirely. |

### When to omit the Bypass line (`bypass_env=None`)

Some gates have **no** authoritative self-bypass, and surfacing a bypass would
defeat the gate. Omit the line in those cases:

- **`pre-merge-approval-gate`** — a merge approval must come from the user, not
  from a marker the agent can attach to its own command. The only authoritative
  signal is `CMUX_DELEGATE=1` set in the *session's* shell env at startup, which
  is not an inline bypass the agent can self-grant.
- **`block-gh-state-all`** — `--state all` is simply invalid for `gh search`;
  there is nothing to bypass, only a correct alternative.

### When to include the Bypass line

When a one-off escape hatch genuinely exists and is acceptable to use with a
stated reason — e.g. **`block-gh-issue-create-without-dup-search`** exposes
`CLAUDE_HOOK_BYPASS_DUP_GATE=1` for the case where the duplicate check was done
outside the current session. The Bypass line's trailing text reminds the agent
to record *why* it is bypassing.

## Enforcement

`tests/test_block_message.py` lints every preflight-gate `impl.py`: any hook
that emits a block/ask message must build it via `block_message.py`, unless it
is explicitly listed in the test's `LEGACY_UNMIGRATED` allowlist (hooks not yet
ported). A new hand-rolled block hook — or a migrated hook that drops the
helper — fails the lint. Migrating a legacy hook later means removing it from
that allowlist.
