# PreToolUse Block PR Without Pre-commit Evidence

Supported hosts: claude, codex

`hooks/block-pr-without-precommit-evidence.py` fires on every PreToolUse(Bash)
event and inspects the command for `gh pr create` / `gh pr new` invocations.
The hook blocks the call unless the effective PR body declares the
pre-commit verification state in one of three accepted marker lines. Goal:
enforce the pre-PR verification habit at the last checkpoint before
shared-state mutation (praxis #406).

### Why this exists

`verify-commit-flag-override` denies `--no-verify` bypass, but only when
the target repo *has* a pre-commit hook to be bypassed. Repos with no
pre-commit configuration (or with placeholder `exit 0` hooks) have no
structural guard. The prompt-layer `Verification Before Completion` /
`Evidence-Based Delivery` rules nominally cover this gap but repeatedly
fail to retrieve under task momentum — the same failure family as
`block-pr-without-caller-evidence` (praxis #158) and
`pre-gh-pr-create-dedup-gate` (praxis #234).

This hook closes the gap by requiring the PR author to declare the
pre-commit state explicitly in the PR body. Reason / evidence text is
free-form — the audit trail is the persistence, not the format. Repo-side
pre-commit configuration is intentionally *not* inspected: the author
is the most accurate authority on whether verification actually ran.

### What is blocked

`gh pr create` / `gh pr new` invocations whose effective body matches
none of the three marker patterns below.

| Pattern | Regex |
|---------|-------|
| `Pre-commit verified: <free text>` | `(?im)^Pre-commit verified:[ \t]*\S` |
| `Pre-commit: verified by CI (<free text>)` | `(?im)^Pre-commit:[ \t]*verified by CI[ \t]*\S` |
| `Pre-commit: n/a (<free text reason>)` | `(?im)^Pre-commit:[ \t]*n/a[ \t]*\S` |

| Condition | Behavior |
|-----------|----------|
| `--help` / `-h` present | allow (read-only introspection) |
| `--template` / `-T` without `--body` / `-b` / `--body-file` | allow (interactive fill-in; body composed after the hook runs) |
| `--body` / `-b` value contains any marker | allow |
| `--body-file <path>` content contains any marker | allow |
| `--body-file -` (stdin) | block — pipe content is uninspectable at PreToolUse time |
| `--body-file <path>` with path missing on disk | block — treat as empty body so the marker check fires |
| `--body-file <path>` where the same Bash command redirects to `path` (`> path`, `>> path`, `tee path`) | block — TOCTOU; the file on disk is about to be overwritten |
| Marker present only inside a fenced code block | block — fenced blocks are stripped before matching |
| `--repo` / `-R` present | **does NOT bypass** (differs from sibling `block-pr-without-caller-evidence`) |

#### Why `--repo` is not a bypass

`block-pr-without-caller-evidence` treats `--repo` presence as an
"operator assertion that the caller-chain step has been done out-of-band"
— sibling intent is "cross-project PR is out of scope". This hook's
intent is the opposite: the `--repo` value *is* the verification target
repo, so honoring `--repo` as a bypass would defeat the purpose. Every
`gh pr create` invocation — local or cross-repo — must carry an explicit
pre-commit evidence line.

Accepted marker line forms (any one in the body passes the gate):

```
Pre-commit verified: ran `pre-commit run --all-files`, all hooks passed
Pre-commit verified: pytest + ruff + mypy clean
Pre-commit: verified by CI (.github/workflows/ci.yml — lint + test jobs)
Pre-commit: verified by CI (https://github.com/o/r/actions/runs/12345)
Pre-commit: n/a (docs-only repo, no executable code touched)
Pre-commit: n/a (legacy repo, lint deferred to CI gate)
```

### Response

The hook writes the deny message to stderr and exits with code `2`
(PreToolUse blocking exit). Claude Code surfaces the stderr text as the
block reason; no JSON envelope is emitted. The hard-block path uses
stderr+exit-2 rather than the JSON `permissionDecision: "deny"` envelope
— matches sibling `block-pr-without-caller-evidence` and is the simpler
choice for unconditional gates with no `ask` / fallback mode.

```
❌ BLOCKED: `gh pr create` without pre-commit evidence.

Add ONE of these lines to the PR body:

  Pre-commit verified: <command + result>
  Pre-commit: verified by CI (<workflow path or url>)
  Pre-commit: n/a (<reason>)
  ...
```

### Compound cascade advisory

When the block fires on a compound Bash command (`&&`, `||`, `;`, `|`,
newline) that also contains a state-changing step (`mkdir`, `tee`,
redirect, `curl -o`, etc.), the block message is suffixed with the
shared cascade advisory from `_hook_utils.compound_cascade_hint`. The
advisory reminds the caller that bash never ran any segment of the
rejected command — including the redirect that would have created the
`--body-file` target — so the cascade should be re-staged with the
Write tool before retrying. Mirrors sibling `block-pr-without-caller-evidence`.

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
- Backslash-newline continuations are normalized to a single space.
- Heredoc body extraction: `VAR=$(cat <<TAG ... TAG)` assignments are
  parsed and `$VAR` / `${VAR}` references in the body argument are
  resolved against the resulting map so heredoc-built body strings are
  inspected just like inline `--body` values.
- Fenced code blocks (both closed ` ``` ` / `~~~` pairs and unclosed
  openings to end-of-body) are stripped before the marker regex runs.
- Subshells (`$(...)`) other than the heredoc assignment form are opaque
  to shlex and not decomposed — an acknowledged limitation matching the
  sibling.

### Tests

`hooks/test-block-pr-without-precommit-evidence.sh` covers 42 cases —
positive blocks (no body, body without marker, value-empty marker, marker
in fenced block, stdin body, missing file, TOCTOU overwrite, `--repo`
without marker, lookalike-keyword variants), positive passes (each of the
three marker patterns, case-insensitive variants, inline marker, file
with marker, heredoc-built body, `--help` / `--template` allow paths,
env wrapper peeling), and compound-cascade advisory suffix presence /
absence. Run before editing the hook:

```bash
./hooks/test-block-pr-without-precommit-evidence.sh
```
