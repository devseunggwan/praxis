# PreToolUse skill-gate-commands

Supported hosts: all

`hooks/preflight-gate/skill-gate-commands/impl.py` intercepts every Bash tool
call and hard-blocks configured external-mutation commands when the required
skill has not been invoked anywhere in the current session. A mapping applies
in every repository unless it carries a `repo=<owner>/<repo>:` qualifier,
which scopes it to the repository the cwd's GitHub origin names.

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
| ----------- | -------- |
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
| Custom (non-built-in) pattern with leading global flag (`gh -R X issue create`, `git -C dir tag`) | matched correctly — the fallback matcher skips known-binary global flags so the flag value no longer breaks token contiguity (issue #514) |
| Scoped entry (`repo=owner/repo:`), cwd's origin matches | checked as usual — **BLOCKED** or **PASS** by the rule above |
| Scoped entry, cwd's origin differs, is absent, is not GitHub, or the cwd is not a repo | **PASS**, silently — a mapping for another repository has nothing to say here (issue #1423) |
| `repo=` head whose value is not an `owner/repo` slug | entry skipped — it does **not** fall through to the global scope; a typo must not widen a gate |
| Whitespace-free shell operator (`gh pr create&&echo`, `gh pr create;echo`) | matched correctly — tokenisation uses the shared `safe_tokenize` (`shlex.shlex` with `punctuation_chars=';\|&'`), which splits the operator into its own token instead of gluing `create&&echo` into one. Plain `shlex.split` glued it and let the form bypass the gate (issue #514) |

## Config env var

`PRAXIS_SKILL_GATED_COMMANDS` — comma-separated entries, each:

```text
[repo=<owner>/<repo>:]<command-pattern>=><required-skill>
```

The `=>` separator is used because skill names routinely contain colons
(e.g. `org:skill-name`) and the arrow-right is unambiguous. The command
pattern is matched against the normalised token sequence (global flags
before the subcommand group are skipped). See
[docs/skill-gated-commands.md](../../../docs/skill-gated-commands.md)
for the full schema, supported patterns, and examples.

### Repository scope (issue #1423)

The optional `repo=<owner>/<repo>:` head scopes one entry:

```text
repo=acme/web:gh pr create=>acme:code-review,gh pr merge=>praxis:merge-briefing
```

Here the first entry fires only inside `acme/web` and the second everywhere.
The current repository is resolved once per call from the payload's `cwd`
(falling back to the process cwd) via `_git.origin_slug` — `git remote get-url
origin`, parsed by the shared host-anchored regex — and compared
case-insensitively, because GitHub treats owner and repo names that way and the
HTTPS and SSH origin forms must resolve alike.

**This exists because the env var is usually set at user level**, where it
reaches every session in every repository, while the skill it names often
exists in one project only. Without a scope, adding a project's review skill to
the gate turns `gh pr create` into a block in unrelated repos — personal
plugins, dotfiles, scratch repos — whose only exits are a ritual invocation of
a skill that does not belong there, or `PRAXIS_HOOK_BYPASS_SKILL_GATE`, the
bypass-token habit this hook family exists to discourage.

A project-only skill can also be configured in that project's
`.claude/settings.local.json` instead, which scopes it by where the setting
lives rather than by what it says. The qualifier is for the case where the
mapping has to sit in the user-level file anyway.

## Transcript scanning

Whole-transcript scan: one skill invocation anywhere in the session satisfies
all subsequent matching commands. The mechanism is identical to
[`block-commit-without-codex-review`](../block-commit-without-codex-review/spec.md)
(`_scan_transcript` + `_has_skill_tool_use`).

The scan streams the transcript and parses only the lines that contain the
required skill's name — a satisfying record carries it in the tool_use's
`skill` value — instead of loading the file and parsing every line
(issue #1312). The 50 MB bound is counted on the bytes actually read.

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
unreadable transcript fail-open, malformed JSON fail-open, non-Bash tool pass,
and the repository scope: a scoped entry blocking in its own repo, inert in
another and outside a git repo, matching case-insensitively, passing when the
skill ran, a malformed qualifier skipping its entry, and an unscoped entry
still blocking everywhere beside a scoped sibling.
