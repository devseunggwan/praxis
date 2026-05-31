# PostToolUse Push Remote Ref Verify

Supported hosts: all

`hooks/advisory-nudge/push-remote-ref-verify/impl.py` runs on `PostToolUse`
for `Bash` and, after a `git push`, cross-checks the remote branch tip against
the SHA that was supposed to be pushed. It emits a **stderr advisory** (never a
block in the default mode) when the remote did not advance to the pushed SHA.

### Why this exists

In remote execution environments (Claude Code on the web, sandboxes) the git
proxy endpoint can rotate between calls — e.g. `127.0.0.1:39291` on the first
push, `:43651` on the second. A second push in the same session may reach a
*different* endpoint, so the intended PR branch never receives the commit even
though `git push` prints `* [new branch]` and exits 0. The follow-up squash
merge then lands an incomplete branch and `main` goes red (praxis issue #539,
the #536 incident). The `* [new branch]` line on a branch that already existed
is the tell, but it is easy to miss in the moment. No existing hook verifies
the *result* of a push — `side-effect-scan` / `skill-gate-commands` are
PreToolUse preflights that run before the push executes.

### What is emitted

The hook writes advisory text to stderr. It exits 0 (advisory) by default;
`PRAXIS_PUSH_VERIFY_STRICT=1` makes it exit 2 so the note is surfaced to the
agent more forcefully.

| Condition | Result |
|-----------|--------|
| Remote tip != local SHA of the pushed ref | `[push-remote-ref-verify]` advisory (expected vs remote tip) |
| Remote has no such branch while push output claims it wrote it (`-> <branch>` / `[new branch]`) | advisory, remote tip shown as `(absent)` |
| Mismatch coincides with a `* [new branch]` output line | advisory + explicit rotating-endpoint note |
| Remote tip == local SHA of the pushed ref | silent (push verified) |
| Push command exited non-zero / interrupted | silent (failure already visible) |
| Non-`git push` command, or no `git`/`push` substring | silent |
| `--dry-run`, `--delete`/`-d`, `--tags`, `--all`, `--mirror`, `--prune`, `:branch` deletion | silent (skip — nothing to verify) |
| >2 positional args (multiple refspecs) | silent (too ambiguous) |
| Detached HEAD, or bare `git push` without an upstream | silent (target unknown) |
| Remote unreachable (`git ls-remote` rc != 0) / `git` missing / timeout | silent (fail-open) |
| Malformed JSON stdin, non-Bash tool | silent (fail-open) |
| `PRAXIS_PUSH_VERIFY_BYPASS` set | silent (opt-out) |

### How the target is resolved

1. The Bash command is structurally tokenized (`safe_tokenize` →
   `iter_command_starts` → `strip_prefix`, same pipeline as sibling hooks) and
   the first `git push` segment is located, honouring global `git -C <dir>`.
2. The push argv is parsed into `{remote, refspec, force}`. Value-taking flags
   (`-o`/`--push-option`/`--exec`/`--receive-pack`/`--repo`) consume their
   argument so they are not mistaken for positionals. Unparseable / unusual
   pushes are skipped rather than guessed.
3. `{remote, remote_branch, local_ref}` is resolved:
   - `git push <remote> <branch>` → `local_ref = remote_branch = <branch>`.
   - `git push <remote> <local>:<remote>` → split refspec.
   - `git push <remote>` / `git push origin HEAD` → current branch name.
   - bare `git push` → `@{upstream}` (`<remote>/<branch>`); skip if none.
4. `expected = git rev-parse --verify <local_ref>` and
   `remote = git ls-remote --heads <remote> <remote_branch>` are compared.

The `ls-remote` round-trip is the reason the manifest `timeout` is 15s. It runs
only after a command that contains both `git` and `push`, so the network cost is
incurred once per push, not per Bash call.

### Why the absent-branch case requires output corroboration

An empty `ls-remote` result (branch absent) is only treated as a discrepancy
when the push output claims it wrote that branch (`-> <branch>` or
`[new branch] ... <branch>`). This avoids a false positive when an unusual
refspec resolves to a branch name the push never actually targeted. A present
remote tip that simply differs from the pushed SHA is always reliable and needs
no corroboration — a successful push makes the remote tip equal to the pushed
SHA, so any difference means the success was illusory.

### Opt-out / strict

- `PRAXIS_PUSH_VERIFY_BYPASS=1` — disable the hook entirely.
- `PRAXIS_PUSH_VERIFY_STRICT=1` — exit 2 (surface to agent) instead of advisory.

### Relationship to sibling hooks

| Hook | Scope | Overlap |
|------|-------|---------|
| `side-effect-scan` | PreToolUse preflight on `git push` / `gh pr merge` | None — runs *before* the push; this hook verifies the *result* |
| `skill-gate-commands` | PreToolUse gate on gated commands | None — preflight, not result verification |
| `momentum-rule-retrieval-gate` | PreToolUse nudge at force-push / merge | None — different trigger and timing |

### Parsing guarantees (fail-open)

The hook returns exit 0 on every infrastructure error: malformed JSON stdin,
non-Bash invocation, missing/unreachable `git`, `ls-remote` failure or timeout,
unparseable push, and any uncaught exception (via the shared `@fail_open`
decorator in `hooks/_lib/_hook_runtime.py`).

### Tests

```bash
bash tests/hooks/advisory-nudge/test_push_remote_ref_verify.sh
```

Cases use a real local bare repo as the remote (no network), covering: verified
push (silent), diverged remote tip (advisory), absent remote branch with a
`[new branch]` output line (advisory + rotating-endpoint note), failed push
(silent), bypass env (silent), non-push command (silent), `--dry-run` skip
(silent), branch-deletion skip (silent), and unreachable-remote fail-open
(silent).
