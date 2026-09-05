# PreToolUse Verify Commit-Flag Override

Supported hosts: all

`hooks/preflight-gate/verify-commit-flag-override/impl.py` fires on every PreToolUse(Bash)
event and inspects the command for `git commit` invocations that override
the repo's commit-policy defaults (hooks, signing, hook path, template).
When any such override is detected, the hook denies the call so the
operator must verify the environment before bypassing the policy.

### Why this exists

The global rule:

> Never skip hooks (`--no-verify`) or bypass signing (`--no-gpg-sign`,
> `-c commit.gpgsign=false`) unless the user has explicitly asked for it.
> If a hook fails, investigate and fix the underlying issue.

The hook is a structural enforcement of that rule. It exists in praxis
because a project-local predecessor regex-matched the bare substring `-n`
anywhere in the bash command, producing false positives on benign
invocations like `echo -n "$VAR"`, `head -n 5`, `sed -n`, and heredoc
message bodies containing such expressions (#184). The praxis version
tokenizes with `shlex` so quoted strings, heredoc bodies, and command
substitutions are excluded from matching.

### What is blocked

Live `git commit` invocations carrying any of these overrides:

| Override | Why it blocks |
| ---------- | --------------- |
| `-n`, `--no-verify` | skips pre-commit hooks (lint/test/format) |
| `--no-gpg-sign` | forces unsigned commit |
| `-S`, `-S<keyid>`, `--gpg-sign`, `--gpg-sign=<keyid>` | forces signing without confirming a secret key |
| `-c commit.gpgsign=true \| false` (git-level `-c`) | config-level signing override |
| `-c core.hooksPath=...` | redirects pre-commit hooks to another path |
| `-c commit.template=...` | overrides commit template |

The git-level `-c key=value` form is parsed in both space-separated
(`git -c commit.gpgsign=false commit -m "..."`) and `=`-joined
(`git -c=commit.gpgsign=false commit`) variants. Value-bearing git-level
flags (`-C`, `--git-dir`, `--work-tree`, `--namespace`, `--config-env`,
`--exec-path`, `--super-prefix`) are consumed together with their value
so the subcommand position is correctly identified — otherwise an
invocation like `git -C /tmp commit -n` would misread `/tmp` as the
subcommand and let the `-n` through (#194 Codex review P2).

### Allow conditions

The only allow path the hook actually recognizes is the env-var bypass:

- `PRAXIS_SKIP_COMMIT_FLAG_CHECK=1` — exported in the operator's
  environment before invoking `git commit`. Use sparingly; the bypass
  must be justified in the commit message body or PR description.

The deny message states BOTH requirements explicitly (the *Suppression needs
a reason and approval* rule, [`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries):
suppression needs a stated reason AND explicit user approval, neither one
alone is sufficient) — the bypass env var is not, on its own, a satisfying
condition; it must be paired with a recorded reason and the user's
per-instance approval. Before this change (issue #941) the message read
as an either/or menu ("set the env var" OR "remove the flag"), which did
not make the approval half of the rule visible at the block site.

The deny message also enumerates every OTHER gate that fires on `git
commit` (`block-commit-without-codex-review`, `commit-title-format-check`,
`commit-title-length-check`, `pre-commit-staged-file-enumeration`) in one pass (issue #941) — an author
clearing this gate should not discover the next commit-time gate only on
the following retry. The checklist is local to this hook's `impl.py`
(`GIT_COMMIT_GATE_CHECKLIST`), not in `hooks/_lib/block_message.py`'s
verb-checklist registry: this hook is its only caller, and each token is
transcribed from the owning sibling hook's own source.

The printed rows are filtered to the gates the running host actually
installs (`render_gate_checklist`). Two of the four name hooks carrying
`hosts: ["claude"]` while this hook carries no `hosts` key, so on
codex/cursor the unfiltered text named gates that are not installed and
offered remedies (`praxis:codex-review-wrap`,
`CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE`) that mean nothing there. The host
comes from the dispatcher's own argv — dispatch-group members run
in-process, and the generated hooks.json bakes the platform into
`_dispatch.sh <event> <matcher> <host>` — and the row set is derived from
`hooks/manifest.json`, so a gate added later with a `hosts` whitelist
cannot drift back into the printed list. A host that cannot be resolved
(standalone run, or a value outside `hooks/manifest.schema.json`'s enum)
prints the full list unfiltered: naming a gate the host does not install
wastes a reader's time, while dropping one it does install hides the next
block entirely.

The host resolution, the manifest lookup, and the `← <hook>` row filter moved
to shared code once issue #1245 classified the rest of the hook suite and found
two more surfaces printing sibling gate names
(`hooks/_lib/_hosts.py → runtime_host` / `installed_hook_names`, and
`hooks/_lib/block_message.py → filter_gate_rows`). `render_gate_checklist` and
the `GIT_COMMIT_GATE_CHECKLIST` literal stay in this file: the literal is still
the single place a row's text and its owning hook are paired, and
`scripts/check-sibling-commit-gates.py` reads both from here.

The deny message also lists "verification commands" (e.g.,
`git config --get commit.gpgsign`, `gpg --list-secret-keys`). Running
those does **not** unblock subsequent invocations — the hook does not
persist a "verification done" state. The commands exist so the operator
can decide whether the override is appropriate; the operator then sets
the env var (or removes the override flag) before re-running. Running
the commands without setting the env var leaves the same `git commit`
invocation still blocked.

### Response

The hook emits the `deny` decision as JSON on stdout (not stderr) and
exits with code `2`:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "BLOCKED: Commit-flag override(s) failed environment verification.\n\nDetected override(s): ...\n\nEnvironment issues:\n  - ...\n\nRequired verification before this command:\n  1. git config --get commit.gpgsign\n  2. gpg --list-secret-keys\n  3. git log --pretty=format:%G? -1\n  4. git config --get core.hooksPath\n\nAllow conditions: ..."
  }
}
```

### Compound cascade advisory (issue #229)

When the deny fires on a compound Bash command containing a
state-changing step (`mkdir`, `tee`, `cp`/`mv`/`rm`/`touch`, redirect,
`curl -o`, `wget -O`), the reason is suffixed with the shared cascade
advisory from `_hook_utils.compound_cascade_hint` so the agent does not
assume the side-effect ran before the denied commit.

### Parsing guarantees

Commands are tokenized with `_hook_utils.safe_tokenize` (shlex-based).
Specifically:

- Quotes (`"` / `'`) protect literal strings from being parsed as
  commands — heredoc message bodies like
  `git commit -m "$(cat <<EOF
  Notes about -n flag.
  EOF
  )"` are not mistakenly matched on the `-n` inside the body.
- Shell operators split the command into segments; each segment is
  evaluated independently via `iter_command_starts`.
- Env prefixes and wrapper commands are peeled via `strip_prefix`.
- Backslash-newline continuations are normalized before tokenization.
- Subcommand detection iterates through git-level global flags so
  value-bearing flags (`-C /tmp`) are correctly skipped before the
  `commit` keyword is matched.

### Tests

`tests/hooks/preflight-gate/test_verify_commit_flag_override.sh` covers 28 cases — every
override token in short / long / `=`-joined form, the `-c` config family
across both git-level positions, false-positive guards (`echo -n`,
`head -n`, `sed -n`, heredoc bodies), wrapper-prefix peeling, value-bearing
git-level flag handling, the env-var bypass, and malformed-input
fail-open. Run before editing the hook:

```bash
tests/hooks/preflight-gate/test_verify_commit_flag_override.sh
```
