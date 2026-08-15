# PreToolUse Pipefail Advisory

Supported hosts: all

`hooks/advisory-nudge/pipefail-advisory/impl.py` nudges the agent to add
`set -o pipefail` (or drop the pipe) when a mutating `git`/`gh` command's
output is piped into `tail`, `head`, or `grep` — without `pipefail`, the
pipeline's exit code is the LAST command's exit code, so the mutating
command's failure is masked.

## Why this exists (issue #788)

Without `set -o pipefail`, `cmd1 | cmd2` reports `cmd2`'s exit code
regardless of whether `cmd1` failed. Two generations of the same
mechanism have been observed:

- **gen-1 (2026-05-23)**: `git commit ... | tail` swallowed a pre-commit
  hook's exit 1; the chained `&&` push then no-op'd against a stale HEAD
  ("Everything up-to-date").
- **gen-2 (2026-07-15)**: `gh pr merge ... 2>&1 | tail -3` masked a
  network error's non-zero exit; the failure was only caught by reading
  the truncated output text.

Memory entry `feedback_bash_exit_code_if_trap` already covers the `&&`
chain variant of this failure mode (enforced structurally by
`inspection-chain-advisory`), but the pipe (`|`) variant had no
structural backstop until this hook. Per issue #788's 3-generation
threshold, gen-2 promoted this from a memory-layer reminder to a
PreToolUse advisory.

## Trigger criteria

The advisory fires when **both** are true, within a single `|`-connected
pipe chain:

1. **Any non-last segment** in the chain is a mutating `git`/`gh`
   invocation:
   - `git` subcommand in `{commit, push, merge, rebase, cherry-pick,
     revert}` (mirrors `side-effect-scan`'s `git-commit` / `git-push`
     categories).
   - `gh <object> <verb>` where verb is mutating for that object
     (mirrors `session-intent`'s `GH_MUTATING_VERBS`): `issue
     {close,comment,create,edit,delete,reopen,lock,unlock,transfer}`,
     `pr {create,comment,edit,merge,close,reopen,ready,review}`,
     `release {create,edit,delete,upload}`, `label
     {create,edit,delete}`, `workflow {run}`.
2. **The LAST segment** in the chain is `tail`, `head`, or `grep` — the
   three sinks named in issue #788 and observed in both generations.

## Exclusions (silent — no advisory)

- **Read-only commands piped**: `git log | head`, `git status | grep`,
  `gh pr list | head` — not in the mutating enum, so excluded by
  construction (no explicit allowlist needed; anything not in the
  mutating enum is silent).
- **Non-truncating sink**: `git commit -m x | cat` — `cat` does not
  reproduce the truncate-then-hide-exit-code shape this hook targets.
- **`&&`/`;`/`||`/`&` chains**: a different defect class, already
  covered by `inspection-chain-advisory` (`&&`-scoped) and out of scope
  here (pipe-scoped only).
- **Quoted-string literal**: a pipe character inside a quoted argument
  (e.g. `gh issue create --body "example: git commit -m x | tail -3"`)
  tokenizes as a single token, not a `|` separator — never reaches
  chain analysis.
- **Heredoc body text**: example text inside a heredoc body containing
  a mutating+pipe pattern (the exact false positive hit while filing
  issue #788 itself, via `gh issue create`'s heredoc-assigned `--body`)
  is skipped via heredoc marker tracking — see "False-positive
  surfaces" below.

## `2>&1` fd-dup normalization

`safe_tokenize`'s shlex `punctuation_chars=";|&"` does not include `>`,
so `2>&1` tokenizes as three tokens (`2>`, `&`, `1`) with the bare `&`
misread as a backgrounding `SHELL_SEPARATOR`. Both observed generations'
trigger commands use `2>&1` (gen-2 explicitly: `gh pr merge ... 2>&1 |
tail -3`), so this hook merges `<fd>>&<fd-or-dash>` token runs back into
a single non-separator token (`_merge_fd_dup_redirects`) before chain
analysis. Without this normalization, `2>&1` would split the pipeline
into two spurious chains and hide the mutating command from the chain
the hook inspects — silently missing the gen-2 pattern this hook exists
to catch.

## `|&` pipe operator

Bash's `cmd1 |& cmd2` (shorthand for `cmd1 2>&1 | cmd2`) tokenizes as a
single `|&` token, distinct from the plain `|` token and not a member of
`SHELL_SEPARATORS`. Since `|&` has the exact same masked-exit-code shape
as the `2>&1 |` pattern this hook targets, it is treated as a
pipe-continuation separator equivalent to `|` in both the raw
segment-splitter and the chain-continuation check.

## Subshell / command-substitution prefix recovery

A mutating command wrapped in a compact subshell (`(git commit ... |
tail)`) or an assignment-with-substitution (`OUT=$(gh pr merge ... |
tail -3)`) fuses the wrapper onto the command token: `strip_prefix`
treats `OUT=$(gh` as a plain `KEY=VALUE` env assignment and drops it
(losing the fused `gh`), and `(git` has no basename match against
`"git"`. `_recover_command_argv` strips a `VAR=$(` assignment-with-subst
prefix (keeping only the fragment after the last `$(`) and any leading
grouping/substitution characters (`(`, `$(`, `` ` ``) before the
mutating-command check runs, so both forms are recognized. Symmetrically,
`_is_truncating_sink` strips one trailing `)` from a bare sink token
(`tail)` from `(... | tail)` with no trailing args) before the basename
comparison — a sink with trailing args (`(... | tail -3)`) fuses the
paren onto the last argument token instead, so the sink token itself is
already clean and needs no stripping.

## False-positive surfaces enumerated (issue #788 requirement)

| Surface | Handling |
| --------- | ---------- |
| Heredoc body line (`cat <<EOF` ... `EOF`) containing mutating+pipe example text | Skipped — `_heredoc_open_marker` detects the opener (space-separated `<< EOF` and fused `<<EOF`/`<<-EOF` forms), and every segment up to the matching end-marker segment is excluded from chain-building entirely |
| Heredoc body line that starts with the end-marker word but has trailing content (`EOF not-end`) | Not treated as the closing delimiter — the close check requires the full segment to be exactly one token equal to the marker (POSIX heredoc semantics: the delimiter must be alone on its line), so a body line merely starting with the marker word does not prematurely close the heredoc and expose the next body line to live chain analysis |
| Quoted string containing a literal `\|` (e.g. `--body "... \| tail"`) | Never tokenizes as a `\|` separator — `safe_tokenize` keeps the quoted content as one token |
| `2>&1` before the pipe | Normalized to a single token so the fd-dup `&` is not misread as `SHELL_SEPARATOR` backgrounding |
| `&&`/`;`/`\|\|`/`&`-chained mutating commands (no pipe) | Silent — chain-building only follows `\|`-continuations; any other separator starts a fresh chain |
| Read-only command piped to tail/head/grep | Silent — not in the mutating enum |
| Mutating command piped to a non-truncating sink (`cat`, `sort`, `wc`) | Silent — last segment must be `tail`/`head`/`grep` |

## False-negative surfaces closed (2026-07-15 codex review round)

| Surface | Handling |
| --------- | ---------- |
| `OUT=$(gh pr merge ... \| tail -3)` (assignment + command substitution) | `_recover_command_argv` strips the `VAR=$(` prefix before the mutating check, recovering `gh` as argv[0] |
| `(git commit ... \| tail)` / `(git commit ... \| tail -3)` (compact subshell) | `_recover_command_argv` strips the leading `(`; `_is_truncating_sink` strips a trailing `)` from a bare sink token |
| `gh pr merge 1 \|& tail -3` (`\|&` pipe-with-stderr operator) | `\|&` is treated as a pipe-continuation separator, equivalent to `\|` |

## False-negative surfaces closed (2026-07-16 codex review round 2)

| Surface | Handling |
| --------- | ---------- |
| `read x <<< value; git commit -m x \| tail` (here-string `<<<` before the pipeline) | `_heredoc_open_marker` previously matched `<<<` via its generic `tok.startswith("<<")` check (`"<<<".startswith("<<")` is true) and returned a bogus end-marker, causing the following mutating pipeline to be swallowed as fake heredoc body. `<<<` is now excluded before that generic check — bash here-strings are a distinct operator, never a heredoc opener |
| `gh issue -R owner/repo create \| head` / `gh pr --repo owner/repo merge 1 \| tail` (global gh flag between object and verb) | `_gh_object_verb` previously only skipped global flags *before* the object, so a flag placed *after* the object (a valid `gh` invocation form) was itself misread as the verb. `_skip_gh_global_flags` is now called at both positions |
| `LANG=C RESULT=$(gh pr merge 1 \| tail -3)` (plain env assignment preceding the capture assignment) | `_recover_command_argv` previously only inspected `argv[0]` for the `VAR=$(` pattern, so a leading plain assignment (`LANG=C`) shifted the capture assignment to `argv[1]` and it was missed — `strip_prefix` then dropped both tokens, losing `gh` entirely. The `$(` search now walks past any number of leading plain assignments before checking for the substitution prefix |

## False-negative surfaces closed (2026-07-16 codex review round 3)

| Surface | Handling |
| --------- | ---------- |
| `( git commit -m x \| tail )` (standalone `(` token, space after the paren) | `_recover_command_argv`'s grouping-char `lstrip` previously left an empty-string `argv[0]` when the token was pure grouping characters, which fails every downstream `os.path.basename(argv[0]) == "git"` / `"gh"` check. The empty-token case is now dropped and the function recurses on the remainder so the real command becomes argv[0] |
| `FOO=$(date) git commit -m x \| tail` (plain assignment whose value is a *self-contained* command substitution, prefixed to a separate command) | `_recover_command_argv` previously treated any `$(` inside an assignment token as an unresolved-extraction target, corrupting `FOO=$(date)` into `date)` and hiding the real `git commit` that follows. The `$(` is now only treated as unresolved when unbalanced within the token (`tok.count("(") > tok.count(")")`, e.g. `OUT=$(gh` from a substitution whose closing `)` is in a later token) — a balanced `VAR=$(cmd)` is left alone so `strip_prefix`'s existing plain-assignment peel finds the real command normally |

## False-negative surfaces closed (2026-07-16 codex review round 4)

| Surface | Handling |
| --------- | ---------- |
| `STAMP=$(date +%s) git commit -m x \| tail` (multi-token command substitution — the substitution's own command has a space, so `safe_tokenize` splits it into `'STAMP=$(date'` + `'+%s)'`) | Round 3's single-token paren-balance check (`tok.count("(") > tok.count(")")`) only looked at the FIRST fragment (`'STAMP=$(date'`, 1 open / 0 close — appears unbalanced in isolation) and mis-extracted `date` as the recovered command, hiding the real `git commit` that follows. `_recover_command_argv` now walks forward accumulating paren-balance across subsequent tokens until it actually closes (or runs off the end of the argv slice) before deciding whether the substitution is self-contained (skip past the whole run) or genuinely split by a pipe (extract from inside it) |
| `if (git commit -m x \| tail); then ...` / `while (git push \| head); do ...` (parenthesized pipeline after a shell keyword) | `_recover_command_argv` previously only checked `argv[0]` for a leading grouping character, so a keyword token (`if`, `while`, ...) ahead of the `(`-prefixed command left the grouping check looking at the wrong token and the mutating command was never recovered. Shell keywords (`SHELL_KEYWORDS`, shared with `strip_prefix`) are now skipped the same way as plain env assignments before the grouping-char check runs |

## False-negative surfaces closed (2026-07-16 codex review round 5)

| Surface | Handling |
| --------- | ---------- |
| `gh pr merge 1 --squash \|` (bare newline) `tail -3` (pipe operator followed by a physical line break — valid bash, no trailing backslash needed after `\|`) | `_pipe_chains`'s empty-argv branch previously overwrote `sep_before` unconditionally with the synthetic `;` `safe_tokenize` inserts between physical lines, discarding the fact that the real preceding separator was `\|`/`\|&` and breaking the chain in two. `cmd \| ;` can never be valid bash syntax, so a `;` observed immediately after `\|`/`\|&` is always that synthetic per-line-break artifact — `sep_before` is now only overwritten when it wasn't already `\|`/`\|&`, preserving the pipe-continuation across the gap |
| `OUT="$(gh pr merge 1 2>&1 \| tail -3)"` (mutating pipeline embedded inside a *quoted* command substitution) | A quoted `"$(...)"` dequotes to a single opaque shlex token — the whole RHS, `\|` included, joins the `OUT=` prefix as one token instead of splitting into separate `\|`-delimited tokens the way an *unquoted* substitution does, leaving `_pipe_chains` no `\|` token to split on. `main()` now falls back to a second pass when the top-level scan finds nothing: `_extract_substitution_bodies` pulls the balanced `$(...)` content out of each top-level token (paren-depth tracked, so nested parens don't mis-close), and each extracted body is re-tokenized and scanned independently, one level deep only (not itself searched for a further-nested substitution, mirroring round 3's balanced-substitution recursive-premise convention) |

## False-negative surfaces closed (2026-07-16 codex review round 6)

| Surface | Handling |
| --------- | ---------- |
| `OUT="$(date) $(gh pr merge 1 \| tail -3)"` (two *sibling* command substitutions in one quoted token, mutating one is not the first) | Round 5's `_extract_substitution_body` (singular) returned only the FIRST `$(...)` run found in a token, so a benign leading substitution (`$(date)`) hid a mutating one later in the same token. Renamed to `_extract_substitution_bodies` (plural) and changed to collect every balanced top-level `$(...)` run in the token — `main()`'s fallback pass now scans each one independently instead of stopping after the first |
| `gh api repos/o/r/issues -X POST 2>&1 \| tail -3` / `gh api ... --method=DELETE \| tail` (mutating `gh api` REST call) | `gh api` is a passthrough verb with no fixed object/verb pair, so `_gh_object_verb`'s enum never recognized it as mutating regardless of HTTP method. New `_gh_api_mutating_method()` mirrors `session-intent.is_gh_mutating()`'s `gh api` branch (`hooks/preflight-gate/session-intent/impl.py:367-387`) — detects `-X`/`--method`/`-XPOST`/`--method=POST` and matches against `{POST, PATCH, PUT, DELETE}`; a default-method (GET) `gh api` call remains silent |

## Examples

| Command | Action |
| --------- | -------- |
| `git commit -m x \| tail` | **ADVISORY** — gen-1 pattern |
| `gh pr merge 123 --squash 2>&1 \| tail -3` | **ADVISORY** — gen-2 pattern |
| `git push origin main 2>&1 \| tail -3` | **ADVISORY** — mutating push + truncating sink |
| `git commit -m x \| tee log.txt \| tail -20` | **ADVISORY** — 3-segment chain, mutating in non-last position |
| `gh issue create --title x \| head` | **ADVISORY** — gh mutating create |
| `OUT=$(gh pr merge 123 \| tail -3)` | **ADVISORY** — assignment + command-substitution prefix recovered |
| `(git commit -m x \| tail)` | **ADVISORY** — compact-subshell prefix/suffix recovered |
| `gh pr merge 1 \|& tail -3` | **ADVISORY** — `\|&` operator |
| `read x <<< value; git commit -m x \| tail` | **ADVISORY** — here-string `<<<` correctly not mistaken for a heredoc opener |
| `gh issue -R owner/repo create \| head` | **ADVISORY** — global gh flag between object and verb correctly skipped |
| `LANG=C RESULT=$(gh pr merge 1 \| tail -3)` | **ADVISORY** — plain env assignment before a capture assignment correctly recovered |
| `( git commit -m x \| tail )` | **ADVISORY** — standalone grouping token dropped instead of leaving an empty argv[0] |
| `FOO=$(date) git commit -m x \| tail` | **ADVISORY** — balanced self-contained substitution left alone, real command reached normally |
| `STAMP=$(date +%s) git commit -m x \| tail` | **ADVISORY** — multi-token substitution balance tracked across tokens, real command reached |
| `if (git commit -m x \| tail); then echo done; fi` | **ADVISORY** — shell keyword skipped before the grouping-char check |
| `gh pr merge 1 --squash \|` (newline) `tail -3` | **ADVISORY** — pipe-then-newline continuation preserved through the synthetic-`;` gap |
| `OUT="$(gh pr merge 1 2>&1 \| tail -3)"` | **ADVISORY** — mutating pipeline inside a quoted command substitution recovered via one-level sub-scan |
| `OUT="$(date) $(gh pr merge 1 \| tail -3)"` | **ADVISORY** — sibling substitutions in one quoted token all scanned, not just the first |
| `gh api repos/o/r/issues -X POST 2>&1 \| tail -3` | **ADVISORY** — `gh api` mutating REST method detected |
| `gh api repos/o/r/issues 2>&1 \| tail -3` | **SILENT** — `gh api` default method (GET) is read-only |
| `git log --oneline \| head -20` | **SILENT** — `log` is read-only |
| `git status \| grep modified` | **SILENT** — `status` is read-only |
| `gh pr list \| head -5` | **SILENT** — `list` is read-only |
| `git commit -m x \| cat` | **SILENT** — `cat` does not truncate |
| `git commit -m x && git push` | **SILENT** — `&&`, not a pipe |
| `gh issue create --body "example: git commit -m x \| tail -3"` | **SILENT** — quoted-string literal, not a live pipe |
| `BODY=$(cat <<'EOF'` / heredoc body containing `git commit -m x \| tail -3` / `EOF` / `gh issue create --body "$BODY"` | **SILENT** — heredoc body line excluded from chain-building |
| `cat <<EOF` / `git commit \| tail` / `EOF not-end` / `git push \| tail -1` / `EOF` | **SILENT** — `EOF not-end` does not prematurely close the heredoc (line-anchored end-marker check) |

## Response format

```text
stderr: "[pipefail-advisory] mutating command piped without `set -o pipefail`
        Detected: <bin1> | <bin2> | ...
        Mutating segment: <desc>
        <body>"
exit 0
```

Advisory-only: the hook **never blocks**. By default it emits no JSON at
all; `PRAXIS_PIPEFAIL_ADVISORY_CONTEXT=1` adds a second copy of the same
text on stdout — see the next section.

## ADVISE-channel experiment (issue #874)

`docs/hook-prune-audit.md` closed the "is the ADVISE tier inert?" question
(it is not — 24% recurrence over 466 sessions) but left the delivery
channel explicitly open: *"The delivery-channel question the issue raises
(stderr vs. `systemMessage`) stands on its own and is not settled by this
data either way — it needs an experiment, not a larger window."* This hook
is the named first subject: at 250/1056 advises it is the largest advisory
load in the 30-day window.

**Arms.** Control = the stderr line above. Treatment =
`hookSpecificOutput.additionalContext` on stdout, gated on
`PRAXIS_PIPEFAIL_ADVISORY_CONTEXT=1` (exact value `1`, mirroring
`PRAXIS_ANCHOR_GATE_ADVISORY`). With the variable unset the hook is
byte-identical to its pre-#874 form.

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "<the same advisory text>"}}
```

**Why `additionalContext` and not `systemMessage`.** `_hook_io.py:88-92`
documents `systemMessage` as transcript-only and *"NOT fed to the model"*,
so promoting to it would change where a human sees the text without
changing what the model sees — the experiment would measure nothing.
`additionalContext` is the channel PR #1000 (commit `7262740`) validated
and shipped for `anchor-comment-gate`'s non-blocking findings on this same
problem; this hook mirrors that mechanism rather than inventing one.

**Why the stderr line stays in both arms** (#1000 dropped it; this hook
must not):

| Reason | Evidence |
| ------ | -------- |
| The metric is derived from stderr | `_fire_ledger.classify_decision` returns `advise` iff `stderr.strip()` is non-empty (`hooks/_lib/_fire_ledger.py:118-119`). Moving the text to stdout would reclassify every fire as `pass`, erasing the recurrence rate the two arms are compared with |
| stderr is the only channel that leaves the dispatch group today | `_dispatch.run_group` forwards every member's stderr unconditionally (`hooks/_lib/_dispatch.py:196-199`) |

**Known blocker — the treatment arm is inert end-to-end.** This hook is a
member of the dispatched `PreToolUse(Bash)` group, and
`_dispatch.run_group` forwards member *stdout* only when it carries a
`"permissionDecision": "deny"` or `"ask"` marker
(`hooks/_lib/_dispatch.py:207-218`); everything else is discarded inside
the dispatcher process. All four generated `hooks.json` files route
`PreToolUse`/`Bash` through `_dispatch.sh`, so this holds on every
platform. #1000's hook is PostToolUse and runs in its own process, which
is why the same emission works there.

Measured, 2026-08-15 (`hooks/_lib/_dispatch.py PreToolUse Bash claude`,
payload `gh pr merge 123 --squash 2>&1 | tail -3  # side-effect:ack`, env
`PRAXIS_PIPEFAIL_ADVISORY_CONTEXT=1`): the hook's own stdout carries
`additionalContext`; the dispatcher's stdout does not. Enabling the arm
for a real session therefore requires `run_group` to also forward
non-decision stdout — a change outside this hook's file.

Two design constraints the experiment inherits from the audit:

- **Right-censoring.** The last advise of a session has no later fire to
  compare against and is excluded from the recurrence denominator. That
  exclusion is not random (a session ending right after an advisory is
  exactly the case where nothing was done about it), so both arms must be
  scored with the same exclusion, and the absolute rate read as a bound.
- **The metric is the hook's own re-evaluation**, not a behaviour diff: a
  later `pass` can also mean the session moved to commands the matcher
  does not cover.

## Parsing guarantees (fail-open)

- malformed JSON stdin → exit 0
- non-Bash tool → exit 0
- empty / whitespace command → exit 0
- uncaught exception in inner logic → swallowed, exit 0

## Relationship to sibling hooks

| Hook | Scope | Overlap |
| ------ | ------- | --------- |
| `inspection-chain-advisory` | `&&`-chained inspection-only commands | None — disjoint separator scope (`&&` vs `\|`) and disjoint command class (read-only vs mutating) |
| `side-effect-scan` | gates (asks) on mutation CLIs before execution | None — this hook fires on a different axis (pipe-masked exit code, not the mutation itself) and never blocks |
| `bash-worktree-existence-advisory` | `cd`/`pushd` target existence | Shares the heredoc-body-skip pattern (independently implemented as a plain-argv walk here vs this hook's Token-typed walk there — not extracted to `_lib` per DRY's 3rd-occurrence threshold) |

## Known limitations

Coverage is intentionally conservative — advisory-only, false-positive
cost dominates:

| Case | Behaviour |
| ------ | ----------- |
| Sink other than `tail`/`head`/`grep` (e.g. `awk`, `sed`, `cut`) | Silent — the enum is scoped to the three sinks named in issue #788's two observed generations |
| Mutating command from a CLI other than `git`/`gh` (e.g. `kubectl apply \| tail`, `npm publish \| tail`) | Silent — the mutating enum mirrors `side-effect-scan`'s git/gh categories only |
| `set -o pipefail` already present earlier in the same compound command | Not detected — the hook does not scan for a preceding `set -o pipefail;` and will still advise (false-positive nudge, not a functional issue since the advice is redundant but harmless) |
| Multi-line inline heredoc embedded inside a single quoted argument (`--body "$(cat <<'EOF' ... EOF)"` all on the `--body` line) | `safe_tokenize` drops that physical line's tokens entirely on `ValueError` (documented in `safe_tokenize`'s own docstring) — the recommended `BODY=$(cat <<'EOF' ... EOF)` + `--body "$BODY"` two-step form (used in this repo's own test payloads) is the form this hook's heredoc skip is verified against |
| Heredoc opener that is itself a live mutating pipeline (`gh issue create --body-file - <<EOF \| tail -3`) | Silent (false negative) — the opener segment is dropped entirely once a heredoc-open marker is detected, and the physical-line boundary between the opener's own `\|`-continuation and the heredoc body's first line is not distinguishable after `safe_tokenize` flattens both into the same synthetic-`;`-separated stream. Fixing this correctly requires tracking physical-line provenance through tokenization — out of scope for issue #788; codex review round (2026-07-15) flagged this as [P2] but the compound pattern (heredoc used simultaneously as stdin *and* the command's stdout piped) is rare relative to the gen-1/gen-2 motivating cases, all of which lack heredoc input entirely |
| Quoted/escaped separator-lookalike content misread as a real operator (e.g. `git commit -m "\|" tail` — the quoted `"\|"` value dequotes to a bare `\|` token indistinguishable from a real pipe) | False positive — `safe_tokenize` (shared `_lib` tokenizer) strips quotes before returning plain strings, discarding quote-provenance. This is a structural limitation of the shared tokenizer, not unique to this hook: `inspection-chain-advisory`'s `&&`-chain detector has the identical exposure (verified: `git a "&&" && grep b foo` → `safe_tokenize` returns two adjacent `&&` tokens, one from the quoted literal). A per-hook workaround would diverge from the established sibling pattern for a narrow, low-likelihood case (a value whose ENTIRE quoted content is exactly `\|`/`&&`/etc., not merely containing the character); a real fix belongs at the `_lib.safe_tokenize` layer as a separate cross-cutting issue |
| Shell comment analyzed as live command (e.g. `echo ok # example; gh pr merge 1 \| tail` — bash never executes anything after the unescaped `#`, but the hook still flags the commented-out example text as a live mutating pipeline) | False positive — `safe_tokenize` sets `lex.commenters = ""` deliberately (`hooks/_lib/_hook_utils.py:124`), a shared design choice: enabling shlex's default comment-stripping would truncate the token stream at the first unquoted `#`, which is far more damaging for the tokenizer's other consumers (e.g. `block-pr-without-caller-evidence` scanning a `--body` value for a marker line — any `#` in the body, such as a markdown heading, would silently hide everything after it from the gate). A per-hook comment-stripping fix in this file cannot safely distinguish a real comment token from a quoted literal that merely starts with `#` post-dequote (verified: `--body "# heading in markdown body"` and a real `# comment` are both indistinguishable single/plain tokens after `safe_tokenize`) — the same quote-provenance loss as the row above. Codex review round 2 (2026-07-16) flagged this as [P2]; low severity in practice since this hook is advisory-only (never blocks) |
| Quoted heredoc-lookalike content misread as a real heredoc opener (e.g. `echo "<<EOF"; git commit -m x \| tail` — `echo` merely prints the literal string `<<EOF`, it does not open a heredoc, but `safe_tokenize` dequotes `"<<EOF"` to the bare token `<<EOF`, indistinguishable from an unquoted heredoc opener) | False negative — same quote-provenance loss as the two rows above: `_heredoc_open_marker` cannot tell a real `<<EOF` from a dequoted `"<<EOF"` string literal once `safe_tokenize` has stripped the quotes. Once misdetected as an opener, the rest of the command — including the real mutating pipeline that follows — is swallowed as fake heredoc body (no matching close-marker segment ever appears), so the advisory that should fire stays silent. Codex review round 3 (2026-07-16) flagged this as [P2]; fixing it requires the same shared-tokenizer quote-tracking change ruled out for the rows above, so it is documented here rather than worked around locally |
| Single-quoted `$(...)`-lookalike text misread as a live command substitution (e.g. `echo '$(gh pr merge 1 \| tail -3)'` — bash single quotes suppress ALL expansion, so `echo` merely prints the literal string, no substitution ever runs) | False positive — round 5's quoted-command-substitution fallback (`_extract_substitution_bodies`, see round 5's row above) treats any token containing a balanced `$(...)` as a potential live substitution to re-scan. A real distinction exists in bash (double quotes still allow `$(...)` expansion; single quotes never do), but `safe_tokenize`'s posix dequoting strips the wrapping quote character before this hook ever sees the token, so single- and double-quoted forms are indistinguishable at this layer — the same quote-provenance loss as the three rows above. Recovering the distinction would require re-parsing the original command string's quote spans independently of `safe_tokenize` (effectively hand-rolling a second shell tokenizer) — a scope and maintenance cost this advisory-only hook does not carry. Codex review round 6 (2026-07-16) flagged this as [P2]; documented here rather than worked around locally, consistent with the three rows above |

## Tests

```bash
bash tests/hooks/advisory-nudge/test_pipefail_advisory.sh
```

Cases cover: advisory firing on gen-1/gen-2 patterns, git push, 3-segment
chains, gh mutating verbs, assignment/subshell/`\|&` prefix recovery
(2026-07-15 codex review round), here-string/gh-flag-position/multi-
assignment-prefix recovery (2026-07-16 codex review round 2),
standalone-grouping-token/balanced-substitution-assignment recovery
(2026-07-16 codex review round 3), multi-token-substitution/keyword-plus-
grouping-prefix recovery (2026-07-16 codex review round 4),
pipe-then-newline continuation/quoted-substitution-embedded-pipeline
recovery (2026-07-16 codex review round 5), sibling-substitutions-in-one-
token/gh-api-mutating-method recovery (2026-07-16 codex review round 6);
silent on read-only commands piped, non-truncating sinks, `&&`/`;`/`\|\|`/`&`
separators, quoted-pipe literals, heredoc body false positives (incl.
the EOF-not-end premature-close guard), single commands,
gh api default-method (GET) calls, and
infrastructure fail-open (non-Bash,
malformed JSON, empty command).
