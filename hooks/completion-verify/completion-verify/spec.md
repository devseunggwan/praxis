# Stop Hook Completion Evidence Verification

Supported hosts: all

`hooks/completion-verify.sh` fires on every `Stop` event and blocks assistant
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
| Frontend (`.tsx` `.jsx` `.vue` `.svelte` `.astro` `.html` `.htm` `.css` `.scss` `.sass` `.less`) | terminal extension of `Edit`/`Write`/`MultiEdit`/`NotebookEdit` `file_path` (or `notebook_path`), case-insensitive | a genuine Bash command invoking a browser driver (`cmux … browser {snapshot,screenshot,eval,get,is,wait}`, `--snapshot-after`, `playwright`, `puppeteer`) **or** a `tool_use` whose name carries `browser` / `playwright` / `puppeteer` / `chrome_devtools` |
| Anything else | — | the generic gate's verdict stands |

Two properties are load-bearing:

- The browser command is drawn from the **genuine** command set, so
  `echo "cmux browser snapshot"` is a fabrication here for the same reason it
  is one at L4.
- The tool-name path exists because a browser MCP tool never appears as a Bash
  command at all — gating on commands alone would block every turn verified
  through Playwright MCP.

The extension test is terminal (`\.tsx$`), so a `Docs.tsx.bak` backup is not a
frontend surface. `.ipynb` is deliberately absent: a notebook is edited through
`NotebookEdit`'s `notebook_path` and its output is not a rendered page.

**Residuals** (documented, not bugs): the check keys on the *file extension*,
so a frontend surface authored in a non-listed extension (a `.js` React file, a
templating language, a design-system token file) is not covered; and a browser
command that ran but observed a different page than the one edited still
passes — the gate proves *something* observed a page, not that it observed the
right one.

### Response

When blocked, the hook emits:

```json
{
  "decision": "block",
  "reason": "Completion claim detected without same-turn verification evidence. ..."
}
```

and appends an entry to `${PRAXIS_HOME:-$HOME/.praxis}/scope-confirm/stop-triggered.log`.

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
silently age out. Strict same-turn enforcement matches the global `~/.claude/CLAUDE.md`
"Verification Before Completion" rule that requires verification commands in
the *immediately preceding* turn.

### No escape hatch

Unlike `side-effect-scan` (`# side-effect:ack` marker), this hook
intentionally has **no bypass**. False positives should be reported as a new
issue, not papered over with a marker — the pattern this hook catches is the
same pattern the marker would re-enable.

### Tests

`tests/hooks/completion-verify/test_completion_verify.sh` covers 31 cases:
8 acceptance scenarios (same-turn pass, no-Bash claim, no-evidence claim,
no-paste claim, mid-message claim ignored, non-Bash tool ignored, realistic
pytest output, Korean evidence), 5 anti-gaming scenarios (echo-fabricated
blocked, printf-fabricated blocked, real command chained with echo passes,
Korean echo-fabricated blocked, echo of command-substitution passes —
issue #758), 14 surface-class scenarios (curl-only evidence on a `.tsx` blocked,
`--snapshot-after` / `browser get` / a global flag before `browser` /
a browser MCP tool all pass, echoed browser command blocked, backend edit and
no-edit turns unaffected, `.tsx.bak` not frontend, uppercase `.TSX` is,
`Write` counts, `.ipynb` does not, no-claim turn never arms, `.scss` counts —
issue #943), and 4 fail-safes (`stop_hook_active`, missing transcript, empty
file, malformed JSONL). Run before editing the hook:

```bash
./tests/hooks/completion-verify/test_completion_verify.sh
```
