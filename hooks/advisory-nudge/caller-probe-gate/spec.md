# PreToolUse Caller Probe Gate

Supported hosts: all

`hooks/advisory-nudge/caller-probe-gate/impl.py` is a **default-on**
PreToolUse(Bash) advisory that
fires when an external-write body asserts that identified code is
**defective** and no **call-site search** appears in the session's transcript
or in the body itself.

## Why this exists

"This code does X" and "this code is defective" are different claims. The
first is settled by reading the code. The second depends on the *caller* —
what state holds when the code runs, and whether the outcome the caller
produces is actually wrong. Publishing the second claim on the first claim's
evidence is the error class this gate covers.

**Motivating case (2026-07-31).** An issue asserted that an account-deletion
DAG dropped data still reachable by other project members. It quoted the
guard function verbatim, correctly, and attached three SQL result sets
measuring the exposure at 6,097 rows across 196 owners. The caller — the
`DELETE /account` controller — revoked every co-member's access in the same
transaction *before* triggering the DAG, so the asserted state never existed
at deletion time. By the time a single `grep -rn <dag_name>` collapsed the
claim, the issue had already been distributed to a downstream repo; both the
parent and its child were retracted.

The attached SQL was real, and that is the instructive part: it was a
*steady-state* measurement, meaningful only if the claim was already true.
Evidence gathered on the assumption that a claim holds cannot disconfirm it,
so volume of evidence is not a proxy for having checked.

## Why the sibling gates do not cover it

| Gate | Oracle | On the motivating case |
| --- | --- | --- |
| `external-write-falsify-check` Check 1 | hypothesis hedging present | body had zero hedging tokens — **passed by construction** |
| `external-write-falsify-check` Check 5 | first-person completion claim | not a completion claim — no match |
| `source-citation-probe-gate` | was the CITED file read? | it was read, and read correctly — **cleared** |
| `exclusion-probe-gate` | exclusion directive in `Write`/`Edit` | surface mismatch |
| `proposal-premise-gate` | prose proposal at `Stop` | surface mismatch (this was a tool call) |

`source-citation-probe-gate` is the near miss worth stating precisely. It
asks whether the citation was read. Here the unread file was not the cited
one but *its caller* — so that gate clears on exactly the evidence this gate
must reject. The two are complementary, not redundant: cite-then-read guards
fabrication, caller-probe guards misattribution of blame.

## What is detected

Firing requires **both** axes, with no clearing arm present.

| Axis | Meaning | Signals |
| --- | --- | --- |
| **A — defect assertion** | the write declares an outcome is wrong | **call-level**: `--title` matching `fix(`, or `--label` containing `bug`. **body-level**: EN `fails to`, `does not check`, `never checks`, `does not account`, `incorrectly`, `is a defect`, `is a bug`, `breaks when`, `data loss`; KO `결함`, `버그`, `잘못`, `누락`, `무시하`, `보지 않`, `놓치`, `소실` |
| **B — code identification** | the write names the code being blamed | source path (`.py .ts .tsx .js .jsx .go .rb .java .kt .rs .sql .sh`) or a backtick symbol matching `^[A-Za-z_][\w.]{3,}$` |

When axis A is **body-level**, axis B must sit **on the same line** as the
defect token. When axis A is **call-level**, axis B also counts in the
**title** and in the body's **first paragraph** (the lines before its first
blank line), where a fix states its defect.

Both scopes were narrowed in #1542, after #1538 replayed 16 recent fires and
found no true positive among them:

- A call-level signal used to put every body line in scope, so any path or
  backtick span anywhere in a `fix(` PR body counted as blamed code (6 of the
  16 fires; four of them had no defect token in the body at all). The title
  and first paragraph keep the call-level signal where it states the defect;
  the rest of the body is scanned only through a same-line token.
- A body-level token used to reach code within ±3 lines. `결함` in an evidence
  heading, a known-limits note or a retraction then blamed every path near
  it, which accounted for most body-level fires.

A backtick span shaped like a commit SHA (`^[0-9a-f]{7,40}$`) is not code: it
names a revision, whose caller decides nothing, and in the sample a SHA was
the only "blamed code" in two fires.

Axis A alone is an ordinary bug report and fires far too often to gate on;
the conjunction with a concrete code citation is what isolates the class.
Broad words (`silently`, `wrong`, `issue`, `problem`) are deliberately absent
from the token list — they match ordinary prose and would swamp the signal.

Surfaces scanned are the shared set from `_lib/_external_write_body.py`:
`gh issue|pr comment|create|edit` and `gh pr review` with `--body` / `-b` /
`--body=` / `--body-file` / `-F`, plus the
`gh api` comment endpoints (issue #1265) — a `POST` / `PATCH` / `PUT` against
`repos/{o}/{r}/issues/comments/<id>`, `issues/<n>/comments`,
`pulls/comments/<id>`, `pulls/<n>/comments` or `pulls/<n>/reviews`, with the
body read from `-f body=` / `--raw-field`, `-F body=@<file>` / `--field` (`@`
expanded as gh expands it). `--input` makes the body unknown and nothing is
scanned — including beside a `body=` field, which gh sends as a query
parameter rather than merging it into the file's request body.
Every other method or endpoint stays outside: a read, `graphql`, a
workflow dispatch. The verification-anchor convention makes `gh api` the only
path a rev ≥2 anchor can take, so before #1265 that write went unscanned.

## Clearing

**Arm A (in-body)** — a filled-in probe line, prefix `Caller-probe:` or
`반증 프로브:`, or a `Caller chain verified:` line that names `grep`, `rg`, `ag`
or `ack`:

```text
Caller-probe: grep -rn account_deletion analytics_backend/ → controller.py:124 triggers the DAG
```

Anti-bypass ported from `output-block-falsify-advisory` (PR #796): a line
still carrying scaffold placeholders (`<command>`, `<observed>`, `<...>`,
`<output>`) or with empty evidence after the arrow does **not** clear. One
valid line clears the whole body.

`Caller chain verified:` is the line `block-pr-without-caller-evidence`
requires on every PR body in this repository. When it names a search tool it
records the same evidence a `Caller-probe:` line does, and in the sample it
was present, unaccepted, in 3 fires (#1542). A line that names no search tool
(`Caller chain verified: N/A — docs-only change`) clears nothing.

**Arm B (transcript, whole session)** — a **search** whose pattern carries the
cited symbol or the cited path's stem:

- a Bash command matching `grep` / `rg` / `ag` / `ack`
- a `Grep` tool call's `pattern`

The **whole session** is read (#1542), through the shared `iter_transcript`
with a `"tool_use"` needle so every other line is rejected before it is
parsed. This gate's error class is long-investigation-specific: the clearing
search happens early and the defect claim is published late. On the
motivating session the clearing `grep` sat ~1,350 lines from the end, and the
4,000-line window that replaced the shared 400-line tail still missed one
search in #1538's sample. Replaying the 16 sampled fires, the slowest call
took 0.15 s against the 5 s hook timeout; the largest of those transcripts
has 26,392 lines.

**A `Read` of the cited file deliberately does NOT clear this gate.** That is
the precise evidence the motivating case already had, and admitting it would
reproduce `source-citation-probe-gate`'s oracle rather than complement it.
Single-file openers (`cat`, `sed`, `head`, `tail`, `nl`) are excluded for the
same reason — they inspect a file already known, which is not a search for
who calls it.

## Response

```text
REMINDER (External-Surface Write / Caller Probe): body asserts a code defect
citing {samples} with no call-site search found in this session's transcript.
Reading the cited code proves what it DOES; whether that outcome is wrong
depends on the caller, which decides the state the code runs under. Attached
query output is not a substitute — evidence gathered on the assumption the
claim is true cannot disconfirm it.
Run one search for the call site (grep -rn <symbol>) and either cite it
inline (`Caller-probe: <command> → <output>`) or drop the defect framing.
A Read of the cited file does NOT clear this gate — that is the exact
evidence the motivating case already had (issue #906).
Set PRAXIS_CALLER_PROBE_STRICT=1 to convert this advisory into a hard block
(exit 2).
```

Default mode writes the reminder to stderr and **exits 0** (advisory, not
block). Set `PRAXIS_CALLER_PROBE_STRICT=1` — literal `1` only, `true`/`yes`/
`on` do not activate — for a hard block (exit 2).

## Default-on rationale

`external-write-falsify-check` shipped opt-in because its rule had no
recurrence trail at adoption time; `source-citation-probe-gate` shipped
default-on because its pattern had one and its clearing arm kept the
false-positive cost low. This gate follows the second precedent:

- The rule it enforces (*Falsification Gates*, [`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries):
  "if this is wrong, what observation should be MISSING?") is an existing
  documented rule that failed prompt-layer retrieval — the motivating session
  had already retracted one analysis built on an unverified premise before
  producing the issue that motivated this hook, same root cause both times.
- The clearing arm is cheap and near-universal: an investigation that
  genuinely traced a defect has almost always grepped the symbol at some
  point, and one `Caller-probe:` line clears the body outright.

Default mode is advisory (stderr only), so the cost of a false positive is
one line of noise, not a blocked call.

## Known limits

- **Fires on true defect claims too.** The gate cannot tell a correct defect
  claim from a wrong one; it can only tell whether a call-site search
  happened. A correctly-investigated bug report that grepped nothing (the
  caller was already open in context, the defect is caller-independent) fires
  and must be cleared with an explicit `Caller-probe:` line.
- **Caller-independent defects are out of scope by construction.** A typo, a
  syntax error, or a self-contained arithmetic bug is wrong regardless of the
  caller, yet still trips the conjunction. Accepted: the advisory costs one
  line, and the class it targets is invisible to every other gate.
- **Arm B matches on substring, not call graph.** Any Bash command that both
  runs a search tool and mentions the symbol clears the gate, even when the
  two are unrelated — observed live while testing this hook: a payload naming
  a symbol landed in the transcript and cleared a later run for that same
  symbol. A true call-graph check is out of scope for a 5-second PreToolUse
  hook, so live checks of this gate must use identifiers not mentioned earlier
  in the session.
- **Axis-A token lists are lexical** and English/Korean only. Paraphrased
  defect assertions ("the guard lets these through") do not match.
- **A same-line token does not say what it blames.** Three of #1538's 16
  sampled fires still warn after #1542: a defect token and a backtick span
  share a line, but the sentence blames something else. Telling them apart
  needs a reading of the sentence, which a token match cannot give.
- **A defect stated in a later paragraph of a `fix(` body is missed** unless
  its line carries a body-level token. The first-paragraph scope was chosen on
  the 6 sampled fires that carried a call-level signal, all false positives,
  and has not been checked for new misses outside that sample.
- Transcript reading requires `transcript_path` in the hook payload. If the
  field is absent or the file is unreadable, Arm B fails open (only Arm A can
  clear).
- Literal `\n` inside a quoted `--body` value splits the body, and
  `--body-file -` (stdin) silent-passes — both inherited from the shared
  extraction module.

**Slack / Notion MCP writes are out of scope (#1359).** The
`mcp__.*slack.*|mcp__.*notion.*` registration this hook carried was dropped
on an owner judgement: it hardcoded two vendors into the runtime surface for
a leg that only reaches an installer who has such a server, and no fire was
ever measured on it. The rule itself is unchanged — it still scans `gh` external writes and fires when both
axes above are present with no clearing arm. The body extractor those writes used
(`_lib/_external_write_body.py`) stays: the opt-in
`external-write-falsify-check` still consumes it.

## Tests

```bash
bash tests/hooks/advisory-nudge/test_caller_probe_gate.sh
```

### Replay fixtures

`tests/fixtures/caller-probe-gate/replay-1542/NN/` holds the 16 fires
from #1538's sample, pseudonymized, as `body.txt`, `title.txt`, `labels.txt` and
`transcript.jsonl`. The body is not stored as `.md`, so the markdownlint and
link-check jobs do not lint a pasted PR body as a repository document. ASCII alphanumeric runs of 4+ characters became
consistent aliases (`w12_`; the underscore keeps a path of aliases from
forming the 40-character run `tests/test_no_live_keys_in_fixtures.sh`
flags); shorter words, the defect-token words, source
extensions and the `fix` / `bug` vocabulary stayed. SHA-shaped runs went
through a fixed per-character permutation, so a short SHA stays a prefix of
its full form and keeps its shape. Clear keys were swapped out before word
aliasing, so a key that sat inside a longer word of a search command still
does. The transcript keeps, for each clear key, the last search that holds it
and nothing else; the last 4,050 lines keep their spacing, so a search beyond
the old 4,000-line window stays beyond it.

A fixture was accepted only when it fired, and named the same number of
samples, exactly as its real counterpart did under both the base and the new
impl. Cases 03, 08 and 16 are the three known false positives above and still
warn; the other 13 are silent.
