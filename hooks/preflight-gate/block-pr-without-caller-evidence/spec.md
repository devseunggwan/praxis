# PreToolUse Block PR Without Caller-Chain Evidence

Supported hosts: claude, codex

`hooks/block-pr-without-caller-evidence.py` fires on every PreToolUse(Bash)
event and inspects the command for `gh pr create` / `gh pr new` invocations.
The hook blocks the call unless the effective PR body contains a literal
`Caller chain verified:` line. Goal: enforce the pre-PR caller-chain grep
habit at the last checkpoint before shared-state mutation (praxis #158).

### Why this exists

Five converging memory rules encode the same procedure — grep the caller
chain before opening a PR. Memory-only enforcement repeatedly failed under
cognitive load: agents authored PRs without the grep step and surfaced
broken-link or unused-symbol regressions in review. The hook converts
"remember to grep" into "the PR cannot open without an explicit evidence
line", which removes the failure mode.

### What is blocked

`gh pr create` / `gh pr new` invocations whose effective body has no
`/^Caller chain verified:[ \t]*\S/i` line.

| Condition | Behavior |
| ----------- | ---------- |
| `--help` / `-h` present | allow (read-only introspection) |
| `--repo` / `-R` is present (any value) | allow — the hook checks for the flag's **presence only**, not whether the value points at another project. `gh pr create --repo <current-repo>` therefore also bypasses the gate. Intent is "cross-project PRs are out of scope"; the implementation is broader. Treat the presence of `--repo` as an explicit operator assertion that the caller-chain step has already been done out-of-band. |
| `--template` / `-T` without `--body` / `-b` / `--body-file` | allow (interactive fill-in; the body is composed after the hook runs) |
| `--body` / `-b` value contains the marker | allow |
| `--body-file <path>` content contains the marker | allow |
| `--body-file -` (stdin) | block — pipe content is uninspectable at PreToolUse time |
| `--body-file <path>` with path missing on disk | block — treat as empty body so the marker check fires |
| `--body-file <path>` where the same Bash command redirects to `path` (`> path`, `>> path`, `tee path`) | block — TOCTOU; the file on disk is about to be overwritten |
| Marker present only inside a fenced code block | block — fenced blocks are stripped before matching |

Accepted marker line forms (any of these in the body passes the gate):

```
Caller chain verified: grep found 3 callers in src/providers/
Caller chain verified: new symbol, no caller expected
Caller chain verified: planned caller in #<followup>
Caller chain verified: N/A -- docs-only change
```

### Response

The hook writes the deny message to stderr and exits with code `2`
(PreToolUse blocking exit). Claude Code surfaces the stderr text as the
block reason; no JSON envelope is emitted for this hook. The hard-block
path uses stderr+exit-2 (Claude Code's PreToolUse blocking shortcut)
rather than the JSON `permissionDecision: "deny"` envelope used by
sibling hooks (`side-effect-scan`, `verify-commit-flag-override`) — both
surface as block reasons, but exit-2 is simpler for unconditional gates
that have no `ask` / fallback mode.

```
❌ BLOCKED: `gh pr create` without caller-chain evidence.

Add a `Caller chain verified:` line to the PR body first:
  ...
```

### Compound cascade advisory (issue #229)

When the block fires on a compound Bash command (`&&`, `||`, `;`, `|`,
newline) that also contains a state-changing step (`mkdir`, `tee`, redirect,
`curl -o`, etc.), the block message is suffixed with the shared cascade
advisory from `_hook_utils.compound_cascade_hint`. The advisory reminds
the caller that bash never ran any segment of the rejected command —
including the redirect that would have created the `--body-file` target —
so the cascade should be re-staged with the Write tool before retrying.

### Parsing guarantees

Commands are tokenized with the shared `_hook_utils.safe_tokenize`
primitive (shlex-based, posix=True). Specifically:

- Quotes (`"` / `'`) protect literal strings from being parsed as commands.
- Shell operators (`;`, `|`, `&`, `&&`, `||`, newlines) split the command
  into segments; each segment is scanned independently via
  `iter_command_starts`.
- Env prefixes (`FOO=1 gh pr create ...`), wrapper commands (`env`,
  `sudo`, `nice`, `time`), and their option flags are peeled via
  `strip_prefix` before matching.
- Backslash-newline continuations are normalized to a single space so the
  tokenizer sees one logical line.
- Heredoc body extraction: `VAR=$(cat <<TAG ... TAG)` assignments are
  parsed and `$VAR` / `${VAR}` references in the body argument are
  resolved against the resulting map so heredoc-built body strings are
  inspected just like inline `--body` values.
- Fenced code blocks (both closed ` ``` ` / `~~~` pairs and unclosed
  openings to end-of-body) are stripped before the marker regex runs, so
  example snippets inside the body cannot accidentally satisfy the gate.
- Subshells (`$(...)`) other than the heredoc assignment form are opaque
  to shlex and not decomposed — an acknowledged limitation.

### Tests

`hooks/test-block-pr-without-caller-evidence.sh` covers 32 cases —
positive blocks (no body, body without marker, marker in fenced block,
stdin body, missing file, TOCTOU overwrite, marker after newline-only
gap), positive passes (inline marker, file with marker, heredoc-built
body, `--repo`/template/help allow paths), shlex-aware evasion attempts,
compound-cascade advisory suffix, env/sudo prefix peeling, and malformed
input fail-open. Run before editing the hook:

```bash
./hooks/test-block-pr-without-caller-evidence.sh
```
