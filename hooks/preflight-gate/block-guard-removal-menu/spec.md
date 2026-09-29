# PreToolUse AskUserQuestion Guard-Removal Menu Gate

Supported hosts: all

`hooks/preflight-gate/block-guard-removal-menu/impl.py` fires on every
PreToolUse(AskUserQuestion) event. It warns (default) or blocks (strict) when a
menu offers to **remove or loosen a guard** right after a hook or permission
rule blocked one of the agent's calls in the same turn.

### Why this exists

Observed case: a protected-branch guard refused three `Write` calls on the
default branch. The very next `AskUserQuestion` offered "add a hook exception
(Recommended)" as its first option. The repository documented a sanctioned
path that satisfies the guard (a project CLI that opens an issue worktree), the
menu did not offer it, and the user had to point it out.

This is the **menu lane** of
[`docs/hook/RULE-BACKSTOP-GAPS.md`](../../../docs/hook/RULE-BACKSTOP-GAPS.md)
gap #4 ([`ETHOS.md`](../../../ETHOS.md#key-principles) principle 5, "Delegating
a workaround is inventing one"). Gap #4 measured all `AskUserQuestion` hooks
silent on such a menu. `bypass-route-signal` meters the prose lane on `Stop`
and leaves this lane open by design; `settings-path-advisory` covers the
follow-up write to a settings file.

### When it fires

Both conditions must hold:

| # | Condition | Source |
| --- | --- | --- |
| a | An option label **or description** carries guard-removal vocabulary | `tool_input.questions[].options[]` via `_lib/ask_option_text.collect_option_texts` |
| b | The current turn (events since the last real user message) holds a denial with `toolDenialKind: "permission-rule"` and `is_error: true` on its `tool_result` | `_transcript.load_current_turn` + `HOOK_BLOCK_DENIAL_KIND` |

Condition (b) keeps unrelated menus quiet: a menu about hook configuration in a
turn where nothing was blocked passes. It is structural, the same two markers
`_transcript.scan_user_rejections` uses for this denial kind, and never reads
the block's prose to decide whether a block happened. `permission-rule` covers
both a PreToolUse hook refusal and a settings permission-rule refusal; offering
to remove either guard is the same shape. `user-rejected` (the user said no)
and `automode-blocked` are not counted. Sidechain events are skipped.

### Detect patterns

English patterns match case-insensitively with `(?<![A-Za-z0-9_])` /
`(?![A-Za-z0-9_])` guards instead of `\b`, for the mixed-script reason
`bypass-route-signal/spec.md` measured (`\b` finds no boundary between ASCII
and Hangul). Digits and `_` are excluded so `GATE_BYPASS=1` reads as one env
name, not as the verb `bypass`.

| Family | Examples that match |
| --- | --- |
| removal verb, then guard noun within three words | `disable the hook`, `bypass this guard`, `turn off branch protection`, `skip the pre-commit hook` |
| guard noun, then exception noun | `hook exception`, `guard bypass`, `gate override`, `hook allowlist` |
| adding an exception | `add an exception`, `add a hook exception` (not `add an exception handler/class/type/clause`) |
| direct write to a protected branch | `allow direct write`, `allow direct push`, `commit directly to main` |
| verification skip | `--no-verify` |
| env switch | `SOME_BYPASS=1`, `SKIP_CHECK=true`, `X_DISABLE=on` (name contains `BYPASS`, `SKIP`, `DISABLE`, `ALLOW`, `OVERRIDE`, `EXEMPT`, `NO_VERIFY`, `OFF` or `ADVISORY`) |

Guard nouns: `hook`, `guard`, `gate`, `safeguard`, `protection`,
`branch protection`, `permission rule`, `deny rule`, `pre-commit`.
Removal verbs: `disable`, `bypass`, `skip`, `turn off`, `remove`, `relax`,
`loosen`, `override`, `circumvent`, `work around`, `suppress`, `silence`,
`exempt`, `deactivate`, `unregister`, `weaken` (and their `-ing` forms).

Korean markers are plain substrings, each pairing the guard noun with the
removal act so ordinary labels such as `훅 설정 확인` do not match:
`훅 예외`, `훅 비활성`, `훅 끄`, `훅 우회`, `훅 해제`, `훅 제거`,
`가드 우회`, `가드 비활성`, `가드 해제`, `가드 예외`, `게이트 우회`,
`게이트 비활성`, `게이트 예외`, `예외 추가`, `예외 설정`, `예외 등록`,
`보호 해제`, `브랜치 보호 우회`, `직접 쓰기 허용`, `직접 커밋 허용`,
`직접 푸시 허용`, `우회 설정`, `권한 규칙 추가`, `허용 목록에 추가`
(plus the `을`/`를` and no-space variants listed in `impl.py`).

A bare guard noun (`hook`) or a bare verb (`skip the slow tests`) does not
match: the shape is the conjunction.

### Relay carve-out

Principle 5 lets the agent relay the gate's own route, the
`Bypass (if truly needed): VAR=1` line the blocking hook printed. Every
`VAR=1` token found in this turn's denial texts is collected, and an option's
env switch naming one of them does not fire. Any other guard-removal phrase in
the same menu still fires, so relaying one line cannot launder an originated
route beside it.

### Mode and env var behavior

| Env var state | Mode | On match |
| --- | --- | --- |
| unset (default) | **Advisory** | exit 0; message on stderr (fire-ledger `advise`) and as `hookSpecificOutput.additionalContext` on stdout |
| `PRAXIS_GUARD_REMOVAL_MENU_STRICT=1` | Strict | exit 2 + message on stderr |

Default is advisory, following `block-manufactured-action-menu`: the vocabulary
is new and its false-positive floor is unmeasured. The advisory also writes
`additionalContext` because bare stderr at exit 0 never reaches the model
(`hooks/_lib/_hook_io.py`, issue #1265); the older sibling predates that.

The message is rendered by `hooks/_lib/block_message.py` (`emit_block` on the
strict path, `format_block` on the advisory path) with `bypass_env=None`, so it
carries no `Bypass` line: a message about not offering a way around a guard
does not offer one around itself. It tells the agent to re-read the blocking
message, read the blocking hook's spec or source, and check the repository's
documented workflow for the path that satisfies the gate, then offer that path,
or stop and report the block.

### What passes silently

| Scenario | Result |
| --- | --- |
| `tool_name != "AskUserQuestion"` | pass |
| Guard-removal option, nothing blocked in the current turn | pass |
| Guard-removal option, the only block was in an earlier turn | pass |
| Guard-removal option, the denial was `user-rejected` | pass |
| Block in this turn, options name satisfying paths only | pass |
| Block in this turn, option relays the gate's own `VAR=1` | pass |
| Malformed payload / missing or unreadable transcript | pass (fail-open) |

### Known limits

- A menu written in assistant prose instead of `AskUserQuestion` is the prose
  lane (`bypass-route-signal`), not this hook.
- A guard-removal option offered in a later turn, after the user replied, is
  not caught: condition (b) is scoped to the current turn on purpose.
- The vocabulary is a closed list; a paraphrase outside it passes.

### Tests

```bash
bash tests/hooks/preflight-gate/test_block_guard_removal_menu.sh
```

Covers: Korean and English exemption labels after a block (advisory and
strict), each English family, a Korean marker in a description only, an
originated env switch beside a relayed one; negatives for no block, a
successful tool result, a user rejection, a block in an earlier turn, a normal
satisfying-path menu (English and Korean), the gate's own relayed `VAR=1`,
`add an exception handler`, `skip the slow tests`, a non-AskUserQuestion tool,
a missing transcript, and a malformed payload.
