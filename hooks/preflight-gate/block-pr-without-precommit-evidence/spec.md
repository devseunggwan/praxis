# PreToolUse Block PR Without Pre-commit Evidence

Supported hosts: claude, codex

`hooks/block-pr-without-precommit-evidence.py` fires on every PreToolUse(Bash)
event and inspects the command for `gh pr create` / `gh pr new` invocations.
The hook blocks the call (when attested — see the tiering section below) unless the effective PR body declares the
pre-commit verification state in one of three accepted marker lines. Goal:
enforce the pre-PR verification habit at the last checkpoint before
shared-state mutation (praxis #406).

### Attested-convention tiering (issue #1186, principle from #1159)

The marker line is a praxis-invented convention with no other local
attestation signal, so **the deny applies only when
`PRAXIS_PR_EVIDENCE_STRICT` is set truthy** (anything but `0`/empty; the
env is shared by both PR-marker gates). On shipped defaults the same
message ships as a stderr **advisory** — prefixed with a line naming the
env that escalates it — and the `gh pr create` proceeds: a fresh installer
cannot be denied by a marker phrase they have never seen. `STRICT=0`
explicitly forces advisory. All other allow conditions and bypasses are
unchanged.

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
| --------- | ------- |
| `Pre-commit verified: <free text>` | `(?im)^Pre-commit verified:[ \t]*\S` |
| `Pre-commit: verified by CI (<free text>)` | `(?im)^Pre-commit:[ \t]*verified by CI[ \t]*\S` |
| `Pre-commit: n/a (<free text reason>)` | `(?im)^Pre-commit:[ \t]*n/a[ \t]*\S` |

| Condition | Behavior |
| ----------- | ---------- |
| `--help` / `-h` present | allow (read-only introspection) |
| `--template` / `-T` without `--body` / `-b` / `--body-file` | allow (interactive fill-in; body composed after the hook runs) |
| `--body` / `-b` value contains any marker | allow |
| `--body-file <path>` content contains any marker | allow |
| `--body-file -` (stdin) | block\* — pipe content is uninspectable at PreToolUse time |
| `--body-file <path>` with path missing on disk | block\* — emits a **path-not-found diagnostic** (see below) instead of the generic token-missing message |
| `--body-file <path>` where the same Bash command redirects to `path` (`> path`, `>> path`, `tee path`) | block\* — TOCTOU; the file on disk is about to be overwritten |
| Marker present only inside a fenced code block | block\* — fenced blocks are stripped before matching |
| `--repo` / `-R` present | **does NOT bypass** (differs from sibling `block-pr-without-caller-evidence`) |

\* "block" rows describe the attested tier; on shipped defaults every
"block" verdict ships as the advisory-tier warning instead (see Response).

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

**Attested tier** (`PRAXIS_PR_EVIDENCE_STRICT` truthy): the hook writes the
deny message to stderr and exits with code `2` (PreToolUse blocking exit).
Claude Code surfaces the stderr text as the block reason; no JSON envelope
is emitted. The hard-block path uses stderr+exit-2 rather than the JSON
`permissionDecision: "deny"` envelope — both surface as block reasons, and
exit-2 predates the tiering (issue #1186) when this was an unconditional
gate.

**Advisory tier** (shipped default — env unset/empty/`0`): the same
guidance ships to stderr prefixed with an `[advisory]` header naming the
escalation env, the `❌ BLOCKED:` first line becomes `⚠ missing:`, the
compound-cascade hint is omitted (it describes an abort that does not
happen in this tier), and the hook exits `0` — the create proceeds. The
scan `continue`s, so a second offending `gh pr create` in the same
compound command gets its own advisory.

**Generic token-missing message** (body present but no evidence marker):

```
❌ BLOCKED: `gh pr create` without pre-commit evidence.

Add ONE of these lines to the PR body:

  Pre-commit verified: <command + result>
  Pre-commit: verified by CI (<workflow path or url>)
  Pre-commit: n/a (<reason>)
  ...
```

The generic token-missing message is suffixed with the shared PR-body
evidence checklist (`hooks/_lib/block_message.py →
pr_body_evidence_checklist()`, praxis #824) enumerating ALL required
pr-body tokens — this gate's `Pre-commit verified:` plus the sibling
gate's `Caller chain verified:` — with the column-0 / same-line /
outside-fenced-blocks format rules, so one deny teaches the full
enumeration. The path-not-found diagnostic below is NOT suffixed — its
cause is a path resolution failure, not a missing token.

**Path-not-found diagnostic** (praxis #608) — emitted when `--body-file` names a
path that does not exist on disk (and is not stdin or a TOCTOU overwrite).
The real cause is that the hook resolves relative paths against its own process
cwd, not the PR worktree, so a relative path like `.omc/pr-123-body.md` that
exists in the worktree is invisible to the hook (the diagnostic follows
the same tiering — advisory on shipped defaults, deny when attested,
without the cascade hint in the advisory tier):

```
❌ BLOCKED: --body-file not found at <resolved-path>

The hook resolves relative paths against its own process cwd, not the PR
worktree. Use an absolute path so the hook can read the file:

  gh pr create --body-file /absolute/path/to/pr-body.md

Or inline the body directly:

  gh pr create --body "Pre-commit verified: ..."
```

This replaces the generic token-missing message when the body-file is absent
and no inline body is provided, preventing the author from chasing a missing
token that was never reachable.

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

`hooks/test-block-pr-without-precommit-evidence.sh` covers 49 cases (55+ checks; tier boundary cases included) —
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
