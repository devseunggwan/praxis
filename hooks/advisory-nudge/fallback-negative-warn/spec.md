# PreToolUse Fallback-Negative-Verdict Advisory

Supported hosts: all

`hooks/advisory-nudge/fallback-negative-warn/impl.py` warns when a Bash
command suppresses stderr and falls back to a `||` clause whose printed
text is itself a negative verdict — a shape that makes "the command failed"
and "the command ran and genuinely found nothing" collapse into the same
observable string.

## Why this exists (issue #893)

```bash
<cmd> 2>/dev/null | grep -i <key> || echo "(no session)"
```

`2>/dev/null` discards stderr and the `||` fallback prints "(no session)".
Three distinct outcomes become identical:

- the subcommand does not exist (exit 2, `Unknown command`)
- auth expired / daemon not running
- the grep genuinely matched 0 lines

Real incident: an in-flight-session gate written in exactly this shape used
a subcommand that never existed. The gate never ran, but its `||` fallback
printed "no session" and that was reported to the user as a verified fact.
The correct subcommand was already recorded in memory but was never
retrieved at execution time — a self-detection failure, since the fallback
made the miss unobservable from the output alone.

## Trigger criteria (3-condition AND, scoped per `||`)

1. **Stderr-null redirect** — anywhere in the same statement to the LEFT
   of a `||` token. Recognized forms:
   - `2>/dev/null` — fused or whitespace-separated (`2> /dev/null`).
   - `>/dev/null 2>&1` — fused or whitespace-separated (`> /dev/null
     2>&1`), **in this order**. Bash redirections apply left to right:
     this order first points fd1 (stdout) at `/dev/null`, then dups fd2
     (stderr) onto fd1's now-`/dev/null` target — both streams are
     discarded unconditionally.
   - `2>&1 >/dev/null` (the reverse order) — but ONLY when a `|` follows
     it in the same pipeline. This order dups fd2 onto fd1's CURRENT
     target BEFORE `>/dev/null` moves fd1 elsewhere. Without a following
     pipe, fd1's current target is the original stdout (terminal / the
     Bash tool's captured output), so fd2 stays visible there — not a
     suppression (codex review round 1, F1; verified live: `bogus_cmd
     2>&1 >/dev/null || echo none` prints the error). WITH a following
     pipe, fd1's current target is the pipe's write end, so fd2 is
     folded into the pipeline's input — invisible to anyone not reading
     the pipe, and indistinguishable from a genuine 0-match if the
     downstream filter doesn't match it (codex review round 2, F1;
     verified live: `bogus_cmd 2>&1 >/dev/null | grep -i hello || echo
     "(no session)"` prints ONLY "(no session)" — the error never
     appears).

   "Same statement" is bounded by the nearest preceding `;`/`&&`/`&` — a
   fresh statement does not inherit a prior statement's redirect. A pipe
   (`|`) does NOT start a new statement: the redirect on a pipeline's
   first segment is in scope for a `||` that follows the whole pipeline
   (the exact shape in the motivating example).
2. **`||` fallback** — the segment immediately after `||` is `echo`,
   `printf`, or `true`.
3. **Negative-verdict vocabulary** — for `echo`/`printf`, the joined
   argument text contains one of: `없`, `none`, `no ` (trailing space —
   avoids matching "known"/"not"/"annoying"), `not found`, `empty`, `(0`,
   `skip`. `true` never carries a message, so condition 3 can never be
   satisfied for it — this is how `|| true` alone stays silent without a
   special case.

All three must hold. Log-noise suppression (`2>/dev/null` alone) and
existence-branching idioms (`|| true` alone, or a `||` fallback with
neutral text) are common and legitimate — they are silent by construction
because condition 3 is the discriminator.

## Non-goals

- **Blocking (exit 2)** — advisory only, per issue #893: false-positive
  cost exceeds the benefit of hard-blocking a shape that is legitimate in
  the two-condition case.
- **General `2>/dev/null` auditing** — a redirect with no `||` fallback, or
  a `||` fallback that isn't negative-worded, never fires.

## Token normalization

`safe_tokenize`'s shlex `punctuation_chars=";|&"` does not include `>`, so
`2>&1` tokenizes as three tokens (`2>`, `&`, `1`) with the bare `&`
misread as a backgrounding separator. `_merge_fd_dup_redirects` merges
`<fd>>&<fd-or-dash>` token runs back into one token before the `||`-scoped
scan, mirroring `pipefail-advisory._merge_fd_dup_redirects` (second
occurrence of this pattern — not extracted to `_lib` per this repo's
3rd-occurrence DRY threshold, matching the convention already established
for the heredoc-body-skip pattern shared by `pipefail-advisory` and
`bash-worktree-existence-advisory`).

Separately, `_merge_spaced_stderr_null` merges a whitespace-separated
redirect-operator + `/dev/null` pair (`cmd 2> /dev/null`, `cmd > /dev/null
2>&1` — legal bash, a space between the redirect operator and its target)
back into the single fused token the exact-token checks expect. Both `2>`
(codex review round 1, F2) and bare `>` (codex review round 2, F2 — needed
for the whitespace-separated combined-redirect idiom) are merged; no other
operator (`>>`, `1>`, ...) is in scope.

A quoted argument whose ENTIRE content happens to equal one of these
redirect tokens is indistinguishable from a live redirect once
`safe_tokenize` dequotes it — see "Known limitations" below (codex review
round 2, F3, accepted rather than fixed).

## Response format

```text
stderr: "[fallback-negative-warn] suppressed-stderr fallback yields a negative verdict
        Detected: <left-of-||> || <fallback>
        <Korean advisory body>
        Reference: issue #893"
exit 0
```

Advisory-only: the hook **never blocks** and never emits JSON.

## Examples

| Command | Action |
| --------- | -------- |
| `<cmd> 2>/dev/null \| grep -i key \|\| echo "(no session)"` | **ADVISORY** — issue #893 motivating pattern |
| `cmd >/dev/null 2>&1 \|\| printf "none\n"` | **ADVISORY** — combined redirect, the order that actually suppresses stderr |
| `cmd 2> /dev/null \| grep -i key \|\| echo "(no session)"` | **ADVISORY** — whitespace-separated `2>` `/dev/null` normalized to one token |
| `cmd > /dev/null 2>&1 \|\| echo "none"` | **ADVISORY** — whitespace-separated combined redirect |
| `cd /worktree && cmd 2>/dev/null \| grep foo \|\| echo "(no session)"` | **ADVISORY** — redirect and `\|\|` share a statement after `&&` |
| `cmd 2>&1 >/dev/null \| grep -i key \|\| echo "no match found"` | **ADVISORY** — dup-then-redirect order WITH a following pipe: stderr is folded into the pipe, effectively hidden |
| `gh issue create --body '2>/dev/null' \|\| echo 'none'` | **ADVISORY (known limitation)** — quoted literal exactly matching a redirect token; not a live redirect, but indistinguishable after dequoting — accepted, not fixed |
| `cmd 2>&1 >/dev/null \|\| echo "none"` | **SILENT** — dup-then-redirect order, no pipe: stderr stays visible at its original target |
| `cmd 2>/dev/null \|\| echo "(retrying)"` | **SILENT** — condition 1+2 only, neutral fallback text (issue #893 required silent case) |
| `cmd 2>/dev/null` | **SILENT** — condition 1 alone, no `\|\|` |
| `cmd \|\| true` | **SILENT** — condition 2 alone, no redirect, and `true` never satisfies condition 3 |
| `cmd 2>/dev/null \| grep foo \|\| true` | **SILENT** — conditions 1+2, `true` has no message |
| `cmd 2>/dev/null \|\| echo "0 matches"` | **SILENT** — `0 matches` is not `(0` (issue's literal vocabulary list) |
| `git status 2>/dev/null \| grep -i foo \|\| echo "not the target"` | **SILENT** — fallback text has no negative-vocabulary match |
| `echo "example: cmd 2>/dev/null \|\| echo (none)"` | **SILENT** — quoted-string literal, not a live redirect/fallback |
| `gh issue create --body "example: cmd 2>/dev/null \|\| echo (none)"` | **SILENT** — same, inside a `--body` value |

## Parsing guarantees (fail-open)

- malformed JSON stdin → exit 0
- non-Bash tool → exit 0
- empty / whitespace command → exit 0
- uncaught exception in inner logic → swallowed, exit 0

## Relationship to sibling hooks

| Hook | Scope | Overlap |
| ------ | ------- | --------- |
| `pipefail-advisory` | mutating git/gh piped into `tail`/`head`/`grep` without `pipefail` | None — disjoint failure mode (masked pipeline exit code vs. masked stderr-and-fallback); shares the fd-dup normalization technique |
| `inspection-chain-advisory` | `&&`-chained inspection-only commands | None — disjoint separator scope (`&&` vs `\|\|`) |
| `count-assertion-verify` | count-shaped claims without a citing command | None — different failure mode (unverified count claim vs. masked command failure) |

## Known limitations

Coverage is intentionally conservative — advisory-only, false-positive cost
dominates:

| Case | Behaviour |
| ------ | ----------- |
| `&>/dev/null` (bash combined-redirect shorthand) | Silent (false negative) — only `2>/dev/null`, the order-correct `>/dev/null 2>&1` form, and the reverse order gated on a following pipe are matched |
| A quoted argument whose ENTIRE content happens to equal a recognized redirect token (e.g. `--body '2>/dev/null'`) | False positive (codex review round 2, F3, accepted — not fixed) — `safe_tokenize` dequotes before this hook sees the tokens, so a quote-exact literal is indistinguishable from a live redirect. `pipefail-advisory` documents the identical quote-provenance exposure for its own operators rather than fixing it (`hooks/advisory-nudge/pipefail-advisory/spec.md` → "Known limitations" → the `"\|"`/comment/heredoc-lookalike rows) — recovering the distinction requires re-parsing the original command string's quote spans independently of `safe_tokenize`, a scope and maintenance cost this advisory-only hook does not carry either. The false-positive cost is a single stderr nudge on a command whose stderr was never actually redirected, not a block |
| Heredoc body text containing the trigger pattern as example text | Not specially excluded (unlike `pipefail-advisory`'s heredoc-marker tracking) — a heredoc body line is itself a Bash statement to `safe_tokenize`, so if it happens to contain a live-looking `\|\|` shape it could be flagged. Lower-risk than `pipefail-advisory`'s case in practice (a heredoc body embedding this exact 3-condition shape as prose is rare), and the cost of a false positive here is a single stderr nudge, not a block |
| A negative-worded `||` fallback with a genuinely absent stderr redirect several statements earlier reused via a shell function/alias | Silent (false negative) — the scan is textual/per-invocation, not cross-invocation or alias-aware |

## Tests

```bash
bash tests/hooks/advisory-nudge/test_fallback_negative_warn.sh
```

Cases cover: the issue #893 motivating pattern, the order-correct combined
redirect form (fused and whitespace-separated), the order-incorrect
dup-then-redirect form staying silent with no following pipe but firing
when a pipe follows (codex review round 1 F1 / round 2 F1), the
whitespace-separated `2> /dev/null` and `> /dev/null 2>&1` forms (codex
review round 1 F2 / round 2 F2), the accepted quoted-literal false
positive (codex review round 2 F3), a `&&`-separated statement still
sharing scope with a later `||`, all required silent cases from the
issue's 검증 section (condition 1+2 only with neutral text; `2>/dev/null`
alone; `|| true` alone), neutral-vocabulary fallback text, quoted-literal
example text (non-exact-match forms), and infrastructure fail-open
(non-Bash, malformed JSON, empty
command).
