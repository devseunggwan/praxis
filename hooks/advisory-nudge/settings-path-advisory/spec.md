# PreToolUse Settings-Path Write Advisory

Supported hosts: all

`hooks/advisory-nudge/settings-path-advisory/impl.py` announces (or, in
strict mode, blocks) an `Edit` / `Write` whose target is a Claude Code
settings file — `.claude/settings.json`, `.claude/settings.local.json`,
`~/.claude/settings.json`, or a `managed-settings.json` — and names the
permission / hook keys the written text carries.

## Why this exists (issue #1337, item 3)

[`ETHOS.md`](../../../ETHOS.md#key-principles) principle 5 draws the line at
authorship: after a hook block the agent may relay the gate's own
`Bypass (if truly needed): <VAR>=1` line and nothing it originated — not
"add a permission rule", not "edit `.claude/settings.json`". The user saying
yes to an agent-originated route widens the guard for every later session.

[`docs/hook/RULE-BACKSTOP-GAPS.md`](../../../docs/hook/RULE-BACKSTOP-GAPS.md)
gap #4 measured that gap on three surfaces (2026-08-15). Two are prose — the
menu in the assistant's text and the `AskUserQuestion` option set — and stay
unhooked by decision (#1009): a relayed `Bypass` line and an originated route
are textually indistinguishable. The third is a tool call: the `Write` or
`Edit` to `.claude/settings.json` that follows the user's pick. That call was
silent on every `Edit|Write` hook — `protected-paths-guard` keys on
credential-shaped names (`.env`, `*.pem`, `.ssh/`), and the only other
`settings.json` matcher, `jq-config-empty-dict-advisory`, is a Bash read
advisory. This hook closes that one lane.

`ConfigChange`, the harness event for settings edits, was considered and
rejected for this: the hooks reference says a blocked change "surfaces no
message to you or to Claude", and it discards `systemMessage` — a silent
block is not a signal (ETHOS principle 4). `PreToolUse(Edit|Write)` carries a
message, so the advisory lives there.

## Trigger criteria

The hook fires when **all** are true:

1. `tool_name` is `Edit` or `Write`.
2. `tool_input.file_path` is non-empty and classifies as a settings file
   (table below).
3. None of the skip rules apply.
4. `PRAXIS_HOOK_BYPASS_SETTINGS_PATH` is unset / empty.

The written text (`content` for `Write`, `new_string` for `Edit`) never
decides whether the hook fires; it only names the reason line.

### Path rules (component-exact)

| Kind | Matches | Examples |
| ------ | --------- | ---------- |
| `project-or-user` | basename `settings.json` or `settings.local.json` whose parent component is exactly `.claude` | `.claude/settings.json`, `/repo/.claude/settings.local.json`, `~/.claude/settings.json` |
| `managed` | basename `managed-settings.json` at any depth | `/etc/claude-code/managed-settings.json`, `/Library/Application Support/ClaudeCode/managed-settings.json` |

Not matched: `settings.json` outside a `.claude/` parent (an app's own
config), `my.claude/settings.json`, `.claude/settings.json.bak`,
`.claude/hooks.json`, `~/.claude.json`, `~/.codex/config.toml`.

### Widening shapes (reason line only)

Scanned in the written text, listed in this order, each at most once:

| Shape | Detector |
| ------- | ---------- |
| `"permissions"`, `"allow"`, `"deny"`, `"ask"`, `"hooks"`, `"disableAllHooks"`, `"env"` | the quoted key followed by `:` |
| a permission-rule literal | a quoted `Tool(pattern)` string, e.g. `"Bash(git *)"`, `"Edit(*.ts)"` |
| praxis variable `PRAXIS_…` | a `PRAXIS_[A-Z0-9_]+` token (first three, sorted) |

A settings write carrying none of them still fires with the reason "no
permission/hook key in the written text; the path alone is the surface".

### Skip rules

| Skip rule | Trigger |
| ----------- | --------- |
| Test fixtures | a `fixtures`, `__fixtures__`, `test-data`, `testdata` or `test_data` path component |
| Scratch | absolute path under `/tmp/` or `/private/tmp/` after lexical normalization — a relative `tmp/…` is a project path, and `/tmp/../repo/…` resolves outside the prefix; neither is scratch. The test lives in [`hooks/_lib/_path_scope.py`](../../_lib/_path_scope.py), shared with `protected-paths-guard`, which had the same defect (#1362) |
| Self-edit | absolute path inside `CLAUDE_PLUGIN_ROOT` (fallback: the checkout that owns this file); relative paths are never exempted |

## Examples

| Call | Action | Why |
| ------ | -------- | ----- |
| `Write .claude/settings.json` with `{"permissions": {"allow": ["Bash(git *)"]}}` | **ADVISORY** — reason names `"permissions"`, `"allow"`, a permission-rule literal | project settings, widening shape |
| `Edit ~/.claude/settings.json` new_string `"PRAXIS_HOOK_BYPASS_PROTECTED_PATHS": "1"` | **ADVISORY** — reason names praxis variable `PRAXIS_HOOK_BYPASS_PROTECTED_PATHS` | the bypass moved into settings is permanent |
| `Write .claude/settings.local.json` with `{"model": "opus"}` | **ADVISORY** — "no permission/hook key…" | path alone is the surface |
| `Write /etc/claude-code/managed-settings.json` | **ADVISORY** | managed policy file |
| `Write config/settings.json` | **SILENT** | parent is not `.claude` |
| `Write .claude/settings.json.bak` | **SILENT** | basename differs |
| `Write .claude/hooks.json` | **SILENT** | not a settings file |
| `Write fixtures/.claude/settings.json` | **SILENT** | fixture skip |
| `Write /tmp/x/.claude/settings.json` | **SILENT** | scratch skip |
| `Write $CLAUDE_PLUGIN_ROOT/.claude/settings.json` | **SILENT** | self-edit skip |
| `NotebookEdit …` | **SILENT** | not a target tool |

## Modes

| Env var | Effect |
| --------- | -------- |
| (unset) | Advisory — stderr text + `additionalContext`, exit 0. The write proceeds. |
| `PRAXIS_SETTINGS_PATH_STRICT=1` | Block — stderr text, exit 2. No `additionalContext` (the block reason is already fed back). |
| `PRAXIS_HOOK_BYPASS_SETTINGS_PATH=1` | Full bypass — exit 0 silently. |

## Response format

Advisory (exit 0):

```text
stderr: "[settings-path-advisory] Claude Code settings write — ADVISORY
          Path: <path> (project or user settings | managed policy file)
          Reason: the written text carries "permissions", "allow", a permission-rule literal
          … principle-5 note, the authorship question, bypass options …"
stdout: {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "<same text>"}}
```

Two channels on purpose (issue #874): stderr is what `_fire_ledger` reads to
classify the fire as `advise` and what the dispatcher forwards for the
terminal; `additionalContext` is the one exit-0 PreToolUse channel that
reaches the model. The advisory asks the agent to state, in the response the
user reads, who asked for the edit and what it changes — so the model has to
see it.

Strict (exit 2): stderr only, `BLOCKED (strict mode)` in the header.

## Parsing guarantees (fail-open)

- malformed JSON stdin → exit 0
- non-Edit/Write tool → exit 0
- empty / missing `file_path` → exit 0
- non-dict `tool_input`, non-string `content` / `new_string` → exit 0 (the
  text reads as empty; the path still classifies)
- uncaught exception → swallowed by `fail_open`, exit 0

## Relationship to sibling hooks

| Hook | Scope | Overlap |
| ------ | ------- | --------- |
| `protected-paths-guard` | credential-shaped file names | None — settings files are not in its set; both fire on nothing in common |
| `jq-config-empty-dict-advisory` | `jq` reading a config file (Bash) | None — a read advisory on a different tool |
| `bulk-write-memory-checkpoint` | the 2nd+ write under `.claude/` per session | Both can fire on one `.claude/settings.json` write; that hook is about bulk authoring, this one about what the file does |

## Known limitations

- **Path string only**: a symlink or a `Bash` redirect (`cat > .claude/settings.json`)
  is not seen. The Bash redirect lane is out of scope for an `Edit|Write`
  hook.
- **Authorship is not decidable from the payload.** The hook cannot tell a
  user-requested settings change from an agent-originated one; it asks the
  agent to say which in the response, and the fire-ledger row records that
  the question was put. The prose and menu lanes of gap #4 stay unhooked
  (#1009).
- **`~/.claude.json`** (the global state file) is not matched: it is not
  where `permissions` / `hooks` live per the settings reference.

## Tests

```bash
bash tests/hooks/advisory-nudge/test_settings_path_advisory.sh
```

Cases cover: every path kind, the component-exact negatives, each widening
shape in the reason line and the no-shape wording, `Edit` vs `Write` text
fields, the three skip rules, strict-mode exit 2 without `additionalContext`,
the bypass variable, the `additionalContext` JSON shape, and the fail-open
paths (malformed stdin, NotebookEdit, empty path, non-dict `tool_input`).
