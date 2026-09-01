# Session-Scope Read-Intent vs Mutation-Pivot Gate

Supported hosts: all

`hooks/preflight-gate/session-intent/impl.py` is a multi-event hook (`UserPromptSubmit` +
`PreToolUse`) that detects the session-scope drift pattern described in
issue [#178](https://github.com/devseunggwan/praxis/issues/178): a user
opens a session with read-intent (`compare`, `analyze`, `review`, `비교`,
`검토`, `장단점`, ...), the AI authors an A/B option menu, cumulative
"1" selections drift into a mutation cascade, and the assistant attempts a
public-surface mutation (`gh issue comment`, `gh pr merge`, ...) without
the user ever speaking an explicit mutation verb.

### Why a hook (not a skill)

Existing memory rules at the individual-decision level (`feedback_falsify_
external_finding_premise`, `feedback_no_option_cycling_after_fundamental_
block`, `feedback_self_authored_labels_not_ratified_scope`) fire per-
decision, but the friction here is at the session-trajectory level —
invisible from any single decision point. A skill cannot intercept tool
calls; only a `PreToolUse` hook can gate at the actual mutation boundary.
The "lower-resolution lexical-only" concern raised in the issue body is
addressed by combining the hook (gate) with a session state file (cross-
turn memory).

### State persistence

Hooks are independent processes — no shared in-memory state. Session
intent is persisted to a JSON file, resolved in this order:

1. `PRAXIS_SESSION_INTENT_FILE` env var (explicit path; used by tests)
2. `${TMPDIR:-/tmp}/praxis-session-intent-${session_id}.json` when the
   hook payload carries a `session_id` field (**primary key** — the
   canonical praxis hook session pattern, also used by
   `completion-verify.sh`, `retrospect-mix-check.sh`, and
   `strike-counter.sh` via `jq -r '.session_id // ...'`). `session_id`
   is stable across hook invocations within a single Claude Code session
   and resets at session boundaries — the exact lifetime the gate needs.
3. `${TMPDIR:-/tmp}/praxis-session-intent-${PPID}.json` (back-compat
   fallback when the payload does not include `session_id`; retained
   so direct CLI / test usage without a payload still works)

A `$CLAUDE_PROJECT_DIR/.praxis-session-intent.json` tier was considered
and intentionally **rejected** (codex P1 on PR #190): a project-rooted
file persists across sessions on the same project and would silently
leak `mutation_verb_seen=True` from a prior session into a new
read-only session, breaking the session-scope contract that is this
hook's primary purpose.

The PPID-only fallback shipped first (codex R1 on PR #190) was
**incoherent within a single session** (codex R2 on PR #190): Claude
Code spawns hook commands in separate processes, so `os.getppid()` in
one hook invocation can differ from the PPID in a subsequent hook
invocation within the same session. The UserPromptSubmit handler would
write to one PPID-suffixed file and the PreToolUse handler would read a
different one — state never cohered, the gate failed open silently.
`session_id` is stable across those invocations and is now the primary
key. The PPID tier remains as a back-compat fallback for invocations
without a payload `session_id`.

State file shape:

```json
{
  "read_intent_anchored": true,
  "read_intent_marker": "compare",
  "first_prompt_snippet": "compare pros/cons of issue 178",
  "mutation_verb_seen": false,
  "mutation_verb_seen_at": ""
}
```

The `read_intent_anchored` field is set **once** on the first prompt —
subsequent prompts do not overwrite the anchor. The `mutation_verb_seen`
field is sticky once set; it never resets within a session.

### Event handlers

**UserPromptSubmit** — scans the prompt:

1. If `read_intent_anchored` is not yet set in the state file, this is
   the session opener. Scan for read-intent markers and write the verdict
   (anchor stays for the rest of the session).
2. Independently, scan every prompt for mutation verbs. If found, set
   `mutation_verb_seen: true`. Same-utterance read + mutation verb means
   both flags get written in the same write, so the later mutation tool
   call passes silently (false-positive guard).

**PreToolUse** — only fires on `Bash` tool with a mutating `gh` command
(v1 scope). When matched:

- `read_intent_anchored == true` AND `mutation_verb_seen == false` →
  emit `permissionDecision: "ask"` (default) or `"deny"` if
  `PRAXIS_INTENT_PIVOT_MODE=block` is set.
- Otherwise → silent pass.

### Mutation-capable surface (v1 scope)

| Pattern                                                                           | Action                   |
| --------------------------------------------------------------------------------- | ------------------------ |
| `gh issue (close\|comment\|create\|edit\|delete\|reopen\|lock\|unlock\|transfer)` | gate candidate           |
| `gh pr (create\|comment\|edit\|merge\|close\|reopen\|ready\|review)`              | gate candidate           |
| `gh release (create\|edit\|delete\|upload)`                                       | gate candidate           |
| `gh label (create\|edit\|delete)`                                                 | gate candidate           |
| `gh api ... --method (POST\|PATCH\|PUT\|DELETE)`                                  | gate candidate           |
| `gh issue list`, `gh pr view`, `gh api repos/foo/bar` (default GET)               | silent                   |
| Non-`gh` Bash commands                                                            | silent                   |
| MCP `mcp__*slack*__*post*`, `mcp__*notion*__*update*`, etc.                       | **v2** (not yet covered) |

`gh` global flags (`-R/--repo`, `--hostname`, `--color`) are peeled before
subcommand detection so `gh -R owner/repo issue create` is detected
correctly. Tokenization uses the shared `_hook_utils.safe_tokenize` /
`iter_command_starts` / `strip_prefix` pipeline so quoted bodies, env
prefixes, and shell control-flow keywords are handled consistently with
the other PreToolUse(Bash) hooks.

### Read-intent + mutation-verb lexicon

Module-level constants in `hooks/preflight-gate/session-intent/impl.py`. English markers are
matched as whole words (regex `(?<![A-Za-z0-9])MARKER(?![A-Za-z0-9])`)
to avoid `comment` matching `commentary`. Korean markers are matched as
substrings since CJK has no whitespace tokenization.

Read-intent (English): `compare`, `analyze`, `analyse`, `review`, `check`,
`investigate`, `explore`, `evaluate`, `assess`, `examine`, `diff`,
`pros/cons`, `pros and cons`, `trade-off`, `tradeoff`, `summary`,
`summarize`, `summarise`, `look at`, `look into`.

Read-intent (Korean): `비교`, `검토`, `분석`, `확인`, `조사`, `살펴`,
`장단점`, `요약`, `정리해`, `리뷰`, `체크`.

Mutation verbs (English): `close`, `merge`, `post`, `push`, `comment`,
`create`, `cancel`, `delete`, `remove`, `publish`, `send`, `submit`,
`approve`, `reject`, `execute`, `run it`, `go ahead`, `proceed`,
`ship it`.

Mutation verbs (Korean): `닫`, `머지`, `게시`, `푸시`, `등록`, `삭제`,
`취소`, `전송`, `보내`, `승인`, `반려`, `실행해`, `올려`, `진행해`,
`처리해`.

### Modes

| `PRAXIS_INTENT_PIVOT_MODE` | Effect                                                                    |
| -------------------------- | ------------------------------------------------------------------------- |
| unset (default)            | `permissionDecision: "ask"` — surfaces a confirmation prompt              |
| `block`                    | `permissionDecision: "deny"` — hard block, user must re-anchor explicitly |

### False-positive guards

- **Anchor-once semantics** — read-intent is only checked on the first
  prompt of the session. A mid-conversation `review` mention does NOT
  re-anchor the session.
- **Same-utterance read + mutation** — "review this PR and merge if
  good" records both flags simultaneously; later `gh pr merge` passes.
- **Quoted strings** — `echo "next step: gh pr merge"` does not trip the
  scan (shlex tokenization respects quotes).
- **Read-only `gh`** — `gh issue list`, `gh pr view`, `gh api repos/...`
  (default GET) are explicit silent paths.
- **Session opener without intent state** — if the mutation tool fires
  before any `UserPromptSubmit` has run (state file absent), the gate
  is silent (no anchor to compare against).

### Fail-safe paths

The hook exits 0 silently when:

- `python3` is unavailable (shell wrapper guard)
- The JSON payload is malformed
- The event type cannot be determined (unknown / missing `hookEventName`)
- The tool is not `Bash` (PreToolUse path)
- The Bash command is empty or tokenizes to zero tokens
- The state file is missing or unreadable (PreToolUse path)
- The state directory write fails (UserPromptSubmit path — gate simply
  won't fire for this session, equivalent to a missing state file)

### Tests

```bash
bash tests/hooks/preflight-gate/test_session_intent.sh
```

26 cases: read-intent anchor write, mutation-verb flag write, mutation
tool call gate paths (ask / silent / deny / block-mode), Korean
read-intent and Korean mutation verbs, malformed JSON fail-open, unknown
event silent, quoted-string tokenization, first-call empty-state silent,
same-utterance read+mutation silent, `gh api --method POST` ask vs
default-GET silent, non-Bash tool silent, anchor-stickiness,
`gh -R owner/repo` global-flag handling, `$CLAUDE_PROJECT_DIR` is not
honored by `resolve_state_path()` (codex R1 P1), and three `session_id`
keying regressions (codex R2 P1): different ids route to different
state files, payload session_id takes priority over PPID, and missing
session_id falls back to PPID.
