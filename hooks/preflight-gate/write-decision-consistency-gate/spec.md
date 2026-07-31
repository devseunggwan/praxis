# PreToolUse Write Decision-Consistency Gate

Supported hosts: all

`hooks/preflight-gate/write-decision-consistency-gate/impl.py` intercepts
`Write` and `Edit` calls and emits `permissionDecision: "deny"` when the body
being written authors a **decision block that also carries a constraint**, with
no `Consistency:` line stating that the two do not contradict.

## Why this exists

Three falsification gates already exist, and none of them observes the `Write`
surface:

| gate                            | surfaces scanned                           |
| ------------------------------- | ------------------------------------------ |
| `output-block-falsify-advisory` | AskUserQuestion, Bash                      |
| `pre-output-falsification-gate` | AskUserQuestion (Lane A), Bash (Lane B)    |
| `source-citation-probe-gate`    | gh issue/pr write bodies, Slack/Notion MCP |

So the moment a delegation handoff is authored — a `Write` of
`/tmp/cmux-delegate-*.md` — is structurally unguarded (issue #905).

On 2026-07-31 a handoff `### Decisions` block held both a decision and, three
lines below, the constraint that invalidates it:

```markdown
### Decisions

- 필터 두 개 모두 추가합니다: `udl.use_yn = 'Y'` + `dm.daily_yn = 'Y'`. ...
- `data_source_status` (SYNC_DISABLED) 는 이번 범위가 아닙니다. 디스패처
  docstring 이 "AUTHORIZED/UNAUTHORIZED 체크는 하지 않는다" 를 의도적 설계로
  명시하고 있어, 건드리려면 별도 논의가 필요합니다.
```

The constraint ("failing connections keep being dispatched **by design**")
invalidates the first decision too, but the author scoped it to the axis it
named and never cross-checked. The wrong decision shipped to a delegated agent
as `Decisions` and cost two PRs — one closed after prod measurement showed it
would exclude 25+ healthy connections, one left failing CI — before the track
was cancelled.

CLAUDE.md already carries the prompt-layer rule (**Self-Plan Internal
Consistency Check**). It was loaded and did not fire. Per the prompt-layer
retrieval-failure threshold, the check moves to the tool-call use-site for the
half a PreToolUse hook can reach. The other half of the same session's failure
(a substitute proposal surfaced as prose, no tool call) is **not reachable** by
any PreToolUse hook and stays a memory-layer rule — a documented gap, not an
oversight.

## Block conditions

Fenced code blocks and inline code spans are blanked out first (line count
preserved), so a document that *shows* a Decisions example — including this
spec — is not read as authoring one.

The gate fires when **any** decision block in the remaining body satisfies all
of:

| # | Condition                                                                                             |
| - | ----------------------------------------------------------------------------------------------------- |
| 1 | `tool_name` is in {`Write`, `Edit`} — `NotebookEdit` is excluded                                      |
| 2 | The block is introduced by a decision header (`Decisions` or `결정`, `##`-`######`, case-insensitive) |
| 3 | Its interior holds at least one markdown list item                                                    |
| 4 | Its interior holds at least one **non-negated** constraint marker                                     |
| 5 | Its interior holds no line starting with `Consistency:` (column 0)                                    |

**Block interior** = from the end of the decision header to the next header at
the same or higher level (`#` count at most the decision header's), or
end-of-body.

Three scoping rules carry the design, each from a review finding:

- **Every block is checked, not just the first.** A document whose first
  Decisions block is clean and whose second carries the contradiction would
  otherwise pass.
- **The clearing line must sit inside the block it clears.** A `Consistency:`
  line elsewhere in the document describes a different decision; treating it as
  a document-wide exemption let one placeholder disable the gate permanently.
- **A negated marker states no constraint.** `Authorization is not out of
  scope` is a decision to act, not a boundary. Recognised negations:
  `is/are/was not`, `isn't`, `aren't`, `~가 아닌`, `~이 아니라`.

Scoping conditions 3–5 to the interior is what keeps the false-positive rate
low: a document that merely mentions scope in a Background section does not
fire.

## Constraint markers

Substring match against the lowercased interior:

`범위가 아닙니다` · `범위 밖` · `범위밖` · `건드리지` · `의도적 설계` ·
`의도된 설계` · `하지 않는다` · `하지 않습니다` · `별도 논의` ·
`out of scope` · `not in scope` · `by design` · `deliberate` ·
`do not touch` · `must not`

## Clearing arm

A line at column 0:

```text
Consistency: <decision> vs <constraint> — <why they do not contradict>
```

The prefix check mirrors the established `Falsified:` convention
(`output-block-falsify-advisory`), so the authoring habit transfers. The gate
checks only that the line exists — it cannot verify the reasoning, and does not
claim to. Its value is forcing the cross-check to be *performed and written*,
the step that was skipped in #905.

## Configuration

| Env var                                        | Default | Effect                            |
| ---------------------------------------------- | ------- | --------------------------------- |
| `PRAXIS_HOOK_BYPASS_DECISION_CONSISTENCY_GATE` | unset   | Any non-empty value → full bypass |

## Fail-open contract

- Malformed / missing stdin JSON → exit 0
- `tool_name` not in {`Write`, `Edit`} → exit 0
- `tool_input` not a dict, or content/`new_string` absent or empty → exit 0
- No decision header → exit 0
- Any uncaught exception → exit 0 (via `@fail_open`)

## Known limitations

- **Edit sees only the replacement fragment.** An `Edit` whose `new_string`
  adds a decision to a block whose constraint lives outside the fragment does
  not fire. Whole-file reconstruction was rejected as too costly for a
  PreToolUse path; `Write` (the delegation-handoff shape that caused #905)
  is covered exactly.
- **Marker-based, not semantic.** A constraint phrased without any listed
  marker is invisible. The list is deliberately short — expanding it trades
  false negatives for false positives on ordinary prose. Negation handling is
  likewise lexical: only the prefixes listed above are recognised, so a
  constraint negated some other way (`hardly out of scope`) still counts.
- **The clearing arm is unverifiable.** A `Consistency:` line asserting
  nonsense clears the gate. This is a self-discipline nudge, not an
  adversarial boundary.
