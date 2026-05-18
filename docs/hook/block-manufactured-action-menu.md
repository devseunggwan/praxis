# PreToolUse AskUserQuestion Manufactured Action-Menu Gate

Supported hosts: all

`hooks/block-manufactured-action-menu.py` fires on every PreToolUse(AskUserQuestion)
event and inspects `options[].label` for manufactured action-menu markers — option
labels that re-ask "shall we proceed?" ("진행할까요", "계속할까요", "proceed",
"continue", "go ahead"). When a marker is found, the most recent user message in
the transcript is checked for a command-intent signal. If a command-intent signal
is present, the user has already given direction — the confirmation menu is
manufactured friction.

### Why this exists

2026-05-13 retrospect Strike 1: agent completed an action then automatically emitted
an AskUserQuestion 4-option menu ("다음 액션 진행할까요?") even when the user's
immediately prior message was a direct command ("진행", "go ahead", "실행"). This
pattern fragments decisions, ignores established user intent, and adds an unnecessary
confirmation roundtrip.

The existing `block-ask-end-option` hook catches *termination* menu misuse (mechanical
"end here" boilerplate). This hook is its sibling: it catches *continuation* menu
misuse — surfacing a "shall we proceed?" confirmation when the user has already said
"yes, proceed".

`feedback_no_premature_option_delegation.md` recorded the same pattern but memory
alone was insufficient — the same session saw repeated recurrences. This hook enforces
the rule at the tool boundary, where the check runs mechanically regardless of
retrieval state.

### What is blocked

| Scenario | Action |
|----------|--------|
| Default mode, manufactured marker in any option label, command signal in prior user msg | exit 0 + advisory stderr |
| `PRAXIS_BLOCK_MANUFACTURED_MENU_STRICT=1`, marker present, command signal in prior msg | exit 2 (block) |
| Any tool name other than `AskUserQuestion` | silent pass-through |
| Marker present BUT no command-intent signal in prior user message | silent pass-through |
| Missing / unreadable transcript | silent pass-through (graceful degrade) |
| No options match any manufactured-menu marker | silent pass-through |

### Detect patterns

#### Manufactured-menu option label markers (Korean)

- `진행할까요`
- `계속할까요`
- `다음 액션`
- `머지할까요`
- `push할까요`

#### Manufactured-menu option label markers (English)

- `proceed`
- `continue`
- `go ahead`

All matches are case-insensitive substring checks against the option label.

### Command-intent signals (user message)

The hook walks the transcript in reverse to find the most recent human-authored
user message (skipping `tool_result`-only entries). Command-intent is detected when:

**Korean** (substring match):
- `진행`, `실행`, `머지`, `커밋`, `push`, `푸시`

**English** (whole-word match, case-insensitive):
- `go`, `go ahead`, `proceed`, `continue`, `merge`, `commit`, `push`

English tokens use `\b` word-boundary matching to prevent false positives from
substrings (e.g. "continuing" → "continue", "progress" does not match "go").

### Mode and env var behavior

| Env var state | Mode | Exit code on match |
|---------------|------|-------------------|
| Neither var set (default) | **Advisory** | 0 + stderr warning |
| `PRAXIS_BLOCK_MANUFACTURED_MENU_STRICT=1` | Strict | 2 (block) |

Default is advisory because this hook is new and exceptions
(irreversible/destructive actions, genuine multiple alternatives) need to be
learned before strict promotion. Set `PRAXIS_BLOCK_MANUFACTURED_MENU_STRICT=1`
to enable hard blocking.

### When manufactured menus are legitimate

This hook fires only when the prior user message contains a command-intent
signal. When no command signal is present, the hook passes silently — the
AskUserQuestion may be a genuine first-time decision point.

Legitimate cases that survive the hook even with a command signal:
- Multiple real alternatives with meaningful trade-offs (not just "proceed" vs "cancel")
- Destructive / irreversible actions requiring explicit confirmation

#### Destructive-confirmation exception (automatic, even in strict mode)

When ANY option label contains a destructive / irreversible action token,
the hook passes regardless of mode. The user's prior generic command does
not absorb per-action approval for shared-state mutations — surfacing a
confirmation menu is required by the project CLAUDE.md
"Pre-Merge Reporting" + "Executing actions with care" rules.

Detected destructive tokens in option labels:

- Korean: `머지`, `푸시`, `삭제`, `지우`, `드롭`, `초기화`, `force`, `프로덕션`
- English: `merge`, `push`, `delete`, `drop`, `truncate`, `force`,
  `prod`, `destroy` (matched with ASCII-letter lookaround rather than
  `\b` so mixed-script labels like `push할까요` match while
  `production-ready` / `Product plan` do not — `production` is
  intentionally not a separate token because it overlaps with
  non-destructive adjectives)

Additionally, the hook ignores status-query / question messages so
phrasings like `진행 상황 알려줘` or `where should we go from here?`
do not register as command-intent and therefore never reach the
manufactured-marker check. Negated directives (`don't proceed yet`,
`do not continue`, `진행하지 마`, `계속하지 말아줘`) are also rejected:
Korean tokens require the following 12 chars to not contain
`하지 마` / `하지 말`, and English tokens require the preceding 30
chars to not contain a negation marker (`don't`, `do not`, `won't`,
`will not`, `cannot`, `should not`, `never`, ` not `, ...).

The Korean command-signal list also includes `계속` so that
continuation messages like `계속해` / `계속 진행` correctly pair with
the `계속할까요` manufactured marker. Detected query forms:

- Korean: `진행 상황`, `진행 중`, `진행 정도`, `진행률`, `어디까지`,
  `어떻게 진행`, `상황 알려`, `상태 확인`, `상태 알려`
- English: trailing `?`, or any of `where should we`, `where do we`,
  `where to go`, `from here`, `how do we`, `what do we`, `should we`

For other legitimate cases (substantive alternatives, less obvious
destructive contexts) the advisory mode will warn but not block; the
designer should ensure options present substantive decision information
rather than a bare continuation marker.

### Response

Advisory response (exit 0 + stderr message only — no JSON output):

```
[advisory] AskUserQuestion includes a manufactured action-menu option ...
```

Block response (exit 2):

```json
{
  "decision": "block",
  "reason": "AskUserQuestion includes a manufactured action-menu option..."
}
```

### Parsing guarantees

- Malformed JSON payload → exit 0 (fail-open)
- `tool_name != "AskUserQuestion"` → exit 0
- Missing / unreadable transcript → exit 0 (fail-open)
- `questions` absent or not a list → exit 0
- `options` absent or not a list in a question → that question skipped
- `tool_result`-only user entries → skipped when walking backward for human text

### Tests

```bash
bash hooks/test-block-manufactured-action-menu.sh
```

Covers: Korean manufactured markers (advisory + strict block), English markers
(proceed, continue, go ahead), command-intent signals in prior user message,
pass when no command signal present, pass when no manufactured marker, non-AskUserQuestion
tool pass-through, missing transcript fail-open, malformed payload fail-open,
tool_result-only entry skip, multi-question payload, false-positive avoidance
(normal work options only).
