# PreToolUse skill-gate-commands

Supported hosts: all

`hooks/preflight-gate/skill-gate-commands/impl.py` intercepts every Bash tool
call and hard-blocks configured external-mutation commands when the required
skill has not been invoked anywhere in the current session.

## Why this exists

Some high-impact mutations (PR creation, PR merge, push to origin) should be
preceded by a required review or validation skill. Prose-layer reminders fail;
this hook enforces the gate structurally at the command checkpoint.

The hook is opt-in via `PRAXIS_SKILL_GATED_COMMANDS` (see
[docs/skill-gated-commands.md](../../../docs/skill-gated-commands.md)).
**When the env var is unset / empty the hook is a NO-OP** — praxis ships no
default gated commands so no existing workflow is affected out-of-the-box.

## What is blocked

A command is blocked (exit 2) when ALL hold:

1. `PRAXIS_SKILL_GATED_COMMANDS` is set and contains ≥1 valid entry.
2. The Bash command matches a configured pattern (after tokenisation and
   global-flag skipping).
3. The session transcript contains no `Skill` tool_use with
   `input.skill == <required-skill>`.
4. `PRAXIS_HOOK_BYPASS_SKILL_GATE` is unset / empty.

| Situation | Action |
|-----------|--------|
| `PRAXIS_SKILL_GATED_COMMANDS` unset | **PASS** (NO-OP) |
| All config entries malformed | **PASS** (fail-safe) |
| Command does not match any configured pattern | **PASS** |
| Command matches; required skill found in transcript | **PASS** |
| Command matches; required skill NOT found in transcript | **BLOCKED** |
| `PRAXIS_HOOK_BYPASS_SKILL_GATE` set (non-empty) | **PASS** |
| Missing `transcript_path` | **PASS** (fail-open) |
| Unreadable / oversized (>50 MB) transcript | **PASS** (fail-open) |
| Malformed JSON stdin | **PASS** (fail-open) |
| non-Bash tool call | **PASS** |
| Global flags before subcommand (`gh -R X pr create`) | matched correctly |

## Config env var

`PRAXIS_SKILL_GATED_COMMANDS` — comma-separated entries, each:

```
<command-pattern>=><required-skill>
```

The `=>` separator is used because skill names routinely contain colons
(e.g. `org:skill-name`) and the arrow-right is unambiguous. The command
pattern is matched against the normalised token sequence (global flags
before the subcommand group are skipped). See
[docs/skill-gated-commands.md](../../../docs/skill-gated-commands.md)
for the full schema, supported patterns, and examples.

## Transcript scanning

Whole-transcript scan: one skill invocation anywhere in the session satisfies
all subsequent matching commands. The mechanism is identical to
[`block-commit-without-codex-review`](block-commit-without-codex-review.md)
(`_scan_transcript` + `_has_skill_tool_use`).

## Escape hatches

- **`PRAXIS_HOOK_BYPASS_SKILL_GATE`** — set to any non-empty value to bypass
  for the session. Use when the required skill was intentionally skipped.
- **No config** — when `PRAXIS_SKILL_GATED_COMMANDS` is unset or empty, the
  hook is entirely inert.
- **Malformed or missing config** — fail-safe pass; no valid entry matched.

## Tests

```bash
bash tests/hooks/preflight-gate/test_skill_gate_commands.sh
```

Covers: no-config NO-OP, gh-pr-create block/pass, gh-pr-merge block/pass,
git-push-origin block/pass, non-configured command pass, bypass env pass,
global-flag ordering, skill name with colon, missing transcript fail-open,
unreadable transcript fail-open, malformed JSON fail-open, non-Bash tool pass.
