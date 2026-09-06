# Stop Hook Completion Evidence Verification

Supported hosts: all

`hooks/completion-verify/completion-verify/impl.sh` (run by the `Stop` dispatch
group, `hooks/_dispatch.sh Stop -`, since issue #1281) fires on every `Stop` event and blocks assistant
turns that declare completion without same-turn verification evidence.

### Why this exists

Memory-based feedback alone (`feedback_test_pass_not_done.md` and friends) was
insufficient — the same evidence-less "✅ done" pattern recurred across
sessions, costing one extra round-trip every time. A hook moves enforcement
from "Claude tries to remember" to "Claude is structurally blocked from
shipping unverified completion claims."

### What is blocked

When the last 10 lines of the last assistant message match `CLAIM_PATTERNS`
(완료 / 작업 완료 / `done.` / `finished.` / `all done` / `implementation
complete` / etc.), the hook checks the **current turn** — i.e., everything
since the last real user input — for verification evidence.

The turn passes only if **all** of the following hold:

| Gate | Condition |
| ------ | ----------- |
| L1 | A `Bash` tool_use occurred in this turn |
| L4 | At least one Bash tool_result is **genuine** — produced by a command that is not an `echo`/`printf`-only fabrication of the success token (issue #758) |
| L3 | A genuine `tool_result.content` matches `EVIDENCE_PATTERNS` (`X passed`, `tests passed`, `\bPASS\b`, `exit code 0`, `lint clean`, `테스트.*통과`, `✅`, etc.) |
| L2 | At least one `EVIDENCE_PATTERNS`-matching span from that genuine `tool_result` is paste'd verbatim in the assistant message text — e.g. `12 passed`, `tests passed`, `lint clean`, `✅` |

A claim with no Bash, with Bash but no evidence signal, or with evidence but
the verify token not quoted, all block. Tool results from non-Bash tools
(e.g. `Read`, `Write`) do **not** count as evidence — only an actually
executed Bash command qualifies. Span-based paste detection is decoration-
agnostic — pytest's `============= 12 passed in 0.85s =============` border
output passes when the assistant cites `12 passed in 0.85s`.

### Anti-gaming: echo/printf-fabricated evidence (issue #758)

The gate reads `tool_result` content **and** the producing command
(`tool_use.input.command`). A result whose command is `echo`/`printf`-only —
matches `^\s*(echo|printf)\b` and contains none of `; & | $` backtick or a
newline — is excluded from the evidence set, so `echo "5 tests passed"` no
longer satisfies L3/L2. A command that chains (`pytest -q; echo done`) or
substitutes (`echo "$(pytest -q)"`) runs a real command and stays genuine.

**Limitation** (documented, not a bug): a real-command *disguise* such as
`true && echo "tests passed"` or `echo a && echo "tests passed"` is not
caught — the threat model is the model's own lazy single-`echo "done"`
pattern, not adversarial evasion. The chaining char in a disguise keeps the
command in the genuine set by design (avoiding false positives on real
chained verification).

### Evidence class follows the changed surface (issue #943)

The gates above ask *whether* evidence exists, never *whether that evidence
can see the thing that changed*. A turn that edits a rendered page and then
runs `curl -w '%{http_code}'` satisfies L1–L4 in full while nothing ever
looked at what the browser shows — the motivating failure was exactly that:
a link swapped in a `.tsx` file, verified by an HTTP status code.

So after the generic gate passes, one surface check runs:

| Changed surface | Detected by | Evidence required |
| --- | --- | --- |
| Frontend (`.tsx` `.jsx` `.vue` `.svelte` `.astro` `.html` `.htm` `.css` `.scss` `.sass` `.less`) | terminal extension of a **succeeded** `Edit`/`Write`/`MultiEdit`/`NotebookEdit` `file_path` (or `notebook_path`), case-insensitive — **or** a genuine Bash command that writes such a path (`sed -i`, `>`/`>>`, `tee`, `cp`/`mv`) | a genuine, **succeeded** Bash command invoking a browser driver (`cmux … browser {snapshot,screenshot,eval,get,is,wait}` with the subcommand immediately after `browser`, `--snapshot-after`, or `playwright`/`puppeteer` at command position) **or** a succeeded `tool_use` whose name carries both a driver (`browser`/`playwright`/`puppeteer`/`chrome_devtools`) and an observation verb |
| Anything else | — | the generic gate's verdict stands |

Four properties are load-bearing:

- The browser command is drawn from the **genuine** command set, so
  `echo "cmux browser snapshot"` is a fabrication here for the same reason it
  is one at L4.
- The tool-name path exists because a browser MCP tool never appears as a Bash
  command at all — gating on commands alone would block every turn verified
  through Playwright MCP.
- **Attempting is not observing.** Evidence tool_uses are correlated
  `id` ↔ `tool_result.tool_use_id` and must have a result that is not
  `is_error` — a snapshot that failed, or one whose result never arrived,
  observed nothing. Conversely a **failed** `Edit` changed nothing, so it is
  dropped from the surface rather than blocking a reply. The same correlation
  is used by `advisory-nudge/pre-commit-staged-file-enumeration` and
  `completion-verify/pr-claim-mutation-gate`.
- **The match must be an operation, not a mention.** `npm ls puppeteer`,
  `grep -R playwright package.json`, `cmux browser goto http://host/snapshot`,
  and `browser_close` all name a driver while reading nothing back, so the
  subcommand is anchored immediately after `browser`, driver names are anchored
  at command position, and an MCP tool name must also carry an observation verb
  (`snapshot`/`screenshot`/`eval`/`content`/`text`/`dom`/`inspect`/`console`/
  `network`/`query`/`read`/`get`). Verbs rather than a vendor tool list,
  because the inventory is per-server and not verifiable from inside the hook.

The extension test is terminal (`\.tsx$`), so a `Docs.tsx.bak` backup is not a
frontend surface. `.ipynb` is deliberately absent: a notebook is edited through
`NotebookEdit`'s `notebook_path` and its output is not a rendered page.

**Residuals** (documented, not bugs): the check keys on the *file extension*,
so a frontend surface authored in a non-listed extension (a `.js` React file, a
templating language, a design-system token file) is not covered; a browser
command that ran but observed a different page than the one edited still
passes — the gate proves *something* observed a page, not that it observed the
right one; a frontend file written by a script whose own body holds the path
(`python build.py`) carries no frontend path on the command line and is not
detected, since chasing it needs the general shell parser this repo has already
found to be unbounded; and an MCP tool whose name puts the observation verb
before the driver (`snapshot_browser`) is missed by the verb-after-driver
ordering.

### Response

When blocked, the hook emits:

```json
{
  "decision": "block",
  "reason": "Completion claim detected without same-turn verification evidence. ..."
}
```

and appends an entry to `${PRAXIS_HOME:-$HOME/.praxis}/logs/stop-triggered.log`
(best-effort — a failed write never changes the hook's decision or exit
status). Before #1182 this log lived at the undocumented
`~/.praxis/scope-confirm/stop-triggered.log`; old files are not migrated and
a legacy `scope-confirm/` directory may linger harmlessly.

### Fail-safe paths

The hook exits 0 (passes) when any of:

- `stop_hook_active` is true (re-entry guard)
- `transcript_path` is missing or unreadable
- The transcript is empty or contains no parseable assistant text
- The claim does not appear in the last 10 lines (mid-message 완료 mention)
- `jq` is not installed

### Why "same turn" specifically

Cross-turn carry-over (verifying in turn N, claiming in turn N+1) is the
exact pattern this hook is designed to prevent — it lets stale evidence
silently age out. Strict same-turn enforcement matches the *Verification Before Completion*
rule ([`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries)) that requires verification commands in
the *immediately preceding* turn.

### No escape hatch

Unlike `side-effect-scan` (`# side-effect:ack` marker), this hook
intentionally has **no bypass**. False positives should be reported as a new
issue, not papered over with a marker — the pattern this hook catches is the
same pattern the marker would re-enable.

### SubagentStop (issue #1337)

The hook is also registered on `SubagentStop`, Claude-only (`hosts:
["claude"]`), which fires when a subagent finishes and "use[s] the same
decision control format as Stop hooks" (hooks reference, read 2026-09-06). A
subagent that reports completion is making the same claim on the same surface;
until this registration, nothing graded it.

Two payload differences decide what the hook reads, both handled by
`hooks/_lib/_transcript.py` (`resolve_stop_transcript`, `load_stop_turn`,
`stop_last_assistant_text`) so the three registered gates cannot drift:

| Field | On `Stop` | On `SubagentStop` |
| ------- | ----------- | ------------------- |
| `transcript_path` | the session's | the **parent** session's — reading it here grades the wrong conversation |
| `agent_transcript_path` | absent | the subagent's own, in a nested `subagents/` folder — the **only** transcript this hook will read on the event |
| `last_assistant_message` | final text, ahead of the lagging transcript | same, for the subagent |

`isSidechain` markers are dropped while the agent transcript is parsed: every
event in a per-agent file belongs to that agent, and the filter that keeps a
subagent's events out of the MAIN transcript would otherwise empty the turn
and pass every subagent silently.

**No fallback to the parent.** When the agent transcript is missing or
unreadable — not yet flushed, or a payload that carries no such key — the hook
reads *nothing* and passes. Falling back to `transcript_path` was the first
draft and it reinstated the defect this registration removes: the turn came
from the parent while the claim came from the subagent's
`last_assistant_message`, so a subagent that ran nothing and merely repeated a
number from the parent's output cleared the evidence check against evidence it
never produced. Grading one conversation's claim with another's evidence is
worse than not grading it.

Unchanged by the registration: `stop_hook_active` still ends the re-entrant
loop, and an unreadable transcript or an empty turn still passes. A claim is
never graded against evidence this run did not read.

Not measured live — see [`RUNTIME_CONSTRAINTS.md` entry 7](../../../RUNTIME_CONSTRAINTS.md).

### Tests

`tests/hooks/completion-verify/test_completion_verify.sh` covers 38 cases:
8 acceptance scenarios (same-turn pass, no-Bash claim, no-evidence claim,
no-paste claim, mid-message claim ignored, non-Bash tool ignored, realistic
pytest output, Korean evidence), 5 anti-gaming scenarios (echo-fabricated
blocked, printf-fabricated blocked, real command chained with echo passes,
Korean echo-fabricated blocked, echo of command-substitution passes —
issue #758), 21 surface-class scenarios (issue #943) — 14 for the mapping
itself (curl-only evidence on a `.tsx` blocked, `--snapshot-after` /
`browser get` / a global flag before `browser` / a browser MCP tool all pass,
echoed browser command blocked, backend edit and no-edit turns unaffected,
`.tsx.bak` not frontend, uppercase `.TSX` is, `Write` counts, `.ipynb` does
not, no-claim turn never arms, `.scss` counts) and 7 from the codex review
round (failed browser MCP call blocked, failed `Edit` does not block,
`sed -i` on a `.tsx` blocks, a `.tsx` only read by the command does not,
`npm ls puppeteer` blocked, `snapshot` inside a `goto` URL blocked,
`browser_close` blocked) — and 4 fail-safes (`stop_hook_active`, missing
transcript, empty file, malformed JSONL). Run before editing the hook:

```bash
./tests/hooks/completion-verify/test_completion_verify.sh
```
