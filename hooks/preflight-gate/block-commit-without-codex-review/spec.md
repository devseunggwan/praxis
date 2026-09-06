# PreToolUse git commit Without codex-review-wrap Block

Supported hosts: claude

`hooks/preflight-gate/block-commit-without-codex-review/impl.py` intercepts every Bash tool call and
hard-blocks a content `git commit` (when the codex capability is attested —
see the tiering section below) when `praxis:codex-review-wrap` has not been
invoked anywhere in the current session — including inside any subagent that
session dispatched (see [Subagent transcript scanning](#subagent-transcript-scanning-issue-730) below).

### Capability tiering (issue #1187, principle from #1159)

The review this gate demands runs through the openai-codex CLI, so the deny
applies only when the capability is locally attested:

- **`codex` binary on PATH** (`shutil.which`) → deny as before, no env
  needed — the detectable dependency is the attestation.
- **`PRAXIS_CODEX_REVIEW_STRICT` non-empty** → explicit override wins both
  ways, matching the sibling tiering contracts (`branch-name-check`, the
  PR-marker gates): any value but `0` pins the deny regardless of
  detection (for setups that route the review without a PATH-visible
  binary); `0` forces advisory even when codex is detected.
- **Neither** → the guidance ships as a stderr advisory naming both
  escalation routes, and the commit proceeds: a commit cannot be denied for
  skipping a review that is impossible to run here. The advisory has two
  variants — undetected (install/pin guidance) and detected-but-demoted
  via `STRICT=0` (unset guidance; it must not falsely claim the CLI is
  missing).

Detection is deterministic in its return values — `shutil.which` has no
ambiguous-failure state, so there is no path where a transient error
silently demotes the gate. (A pathological PATH entry — a dead NFS mount —
can hang the probe; the hook's 5s manifest timeout bounds that, failing
open, the same envelope the transcript read already had.) A
PATH deliberately stripped of codex does demote it; that is accepted
because the same actor already holds the
`CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE` escape, and the strict env exists
precisely to pin the deny. The advisory path skips the escalation banner —
the escalation counter tracks repeated denies, and an advisory is not one.
All other allow conditions and bypasses are unchanged.

### Why this exists

The workflow this hook was written for (a `Deliver` table in the author's
dotfiles `AGENTS.md`, devseunggwan/ai-dotfiles#93 — outside this repo) lists `praxis:codex-review-wrap` as a second mandatory independent review pass
before commit — an independent Codex pass after `omc:code-reviewer` that catches
defects a single reviewer misses. Prose alone is unreliable (prompt-layer
retrieval failure); per the established escalation pattern, structural
enforcement at the commit checkpoint backs the rule.

This gate blocks on the **absence** of the required skill invocation, rather
than on the presence of any marker in the transcript.

### What is blocked

Verdicts below describe the attested tier; on shipped defaults with no
codex on PATH every "BLOCKED" verdict ships as the advisory-tier warning
instead (see Capability tiering above).

Both conditions must hold for a block (exit 2):

1. The Bash command is a content `git commit` — `--amend`, `git merge`,
   `git rebase`, `git cherry-pick`, and `git revert` are exempt. `--allow-empty`
   / `--allow-empty-message` are **not** exempt: they only permit an empty
   commit / message and do not prevent staged content from being committed, so
   a content commit using them is still gated (use the skip token or the
   persistent env bypass for an intentional empty CI-trigger commit).
2. Neither the session's own transcript nor any of its subagent transcripts
   contains a `Skill` tool_use with `input.skill == "praxis:codex-review-wrap"`,
   and the root transcript has no `/praxis:codex-review-wrap` slash-command
   invocation either (a prose mention such as "should I run
   /praxis:codex-review-wrap?" does not count — the match is line-anchored).

| Situation | Action |
| ----------- | -------- |
| `git commit -m "..."` with no codex-review-wrap invocation this session | **BLOCKED** (exit 2) |
| `git commit` after a `Skill(praxis:codex-review-wrap)` tool_use | **PASS** (review ran) |
| `git commit` after a `/praxis:codex-review-wrap` slash command | **PASS** (review ran) |
| `git commit` after a subagent ran `Skill(praxis:codex-review-wrap)` | **PASS** (review ran, in a subagent transcript — issue #730) |
| `git commit --amend` / `git revert` / `git merge` | **PASS** (non-content / exempt) |
| `git commit -m "docs [skip-codex-review]"` | **PASS** (skip token) |

Granularity is session-level: one codex-review-wrap invocation satisfies all
subsequent commits in the same session (whole-transcript scan, root +
subagents), matching the other commit / PR review gates.

### Scan cost (issue #1277)

The scan streams each transcript and parses only the lines that contain the
literal `codex-review-wrap` — every record that can satisfy the gate carries
it, so a line without it is rejected before `json.loads`. The earlier shape
loaded the whole file into a list and parsed every line: 490 ms and ~70 MB of
RSS per `git commit` on a 36 MB session, paid inside the Bash dispatch group's
shared deadline (#1167). With the prefilter the same file scans in 41 ms at
constant memory.

The scan is resumable: each transcript file (root and every subagent's) has a
cursor under `~/.praxis/cache/` keyed on the session id
(`scan-block-commit-without-codex-review-<file>-<session_id>.json`, swept
with the cache TTL and spared for the live session), holding the byte offset
of the last complete record read and whether the invocation was found. A
`git commit` therefore reads only the bytes appended since the previous one,
and once the invocation is on record the file body is not read at all. The
50 MB bound is a budget per call rather than a ceiling on the session: a
transcript past it answers "cannot enforce" for the few commits it takes to
catch up — the cursor advances by one budget each time — then costs its
delta for the rest of the session. The offset is trusted only while the
cursor's inode, size and a sample of the bytes before the offset still
match the file; anything else restarts from byte 0. A payload without a
`session_id` scans without persistence, under the same budget.

### Subagent transcript scanning (issue #730)

A `Skill(praxis:codex-review-wrap)` call made *inside* a Task/Agent-dispatched
subagent is recorded only in that subagent's own JSONL file — the root
transcript carries no `isSidechain` entries at all (live-verified: 0
`isSidechain` lines across sampled root transcripts, full subagent turns only
under the sibling `subagents/` directory). A root-only scan is therefore
structurally blind to review work a subagent actually performed, which forced
use of the `[skip-codex-review]` escape hatch even when a real review had run
(surfaced during issue #720 work).

The hook now also scans `<session-dir>/subagents/agent-*.jsonl` — the sibling
directory Claude Code writes one file into per dispatched subagent, keyed off
the root transcript path (`<project-dir>/<session_id>.jsonl` →
`<project-dir>/<session_id>/subagents/`). Each Claude Code session — and
therefore each worktree/ultrawork branch, since each runs as its own session
— has its own transcript and its own sibling `subagents/` directory, so this
scan never crosses session boundaries: a review run in one worktree's session
does not satisfy the gate for a commit in a different worktree's session. A
subagent transcript that is individually unreadable is skipped (that one
subagent's history just can't contribute a PASS); it does not turn the whole
scan into a fail-open, since the root transcript already answered the
question for everything outside that one subagent run. A subagent transcript
the resumable scan has not caught up with yet is different: its unread tail
may hold the invocation, so unless a later subagent confirms one the verdict
is indeterminate (fail-open, like a root that has not caught up), never a
block, for the few commits the catch-up takes.

### Escalation on repeated same-session block (issue #805)

The block verdict is self-sufficient, but a session that hits it repeatedly is
a signal in itself: the agent is changing *how* it invokes the commit (heredoc,
`-F` file, subshell, a different separator) rather than running the review the
message asks for. Those input-shape changes are orthogonal to the gate
condition, so each re-check blocks again on the same ground — and, with an
identical message every time, the 1st block and the 3rd are indistinguishable.

The hook now reads its own prior fires back from the fire ledger
(`_fire_ledger.count_session_fires`, the in-session read path added in
issue #805) and, from the **2nd same-session block onward**, prepends an
escalation banner that names that exact anti-pattern ("changing HOW you invoke
the commit
does NOT change the gate condition — run the review"). The count is
session-scoped and `decision="block"`-filtered: only prior *blocks* of this hook
in this `session_id` raise it, and the dispatcher records each block *after* the
hook returns, so the in-flight block is excluded (the banner reports blocks
*before* now).

This is a **message-only** escalation (option (a) of the issue): the block/allow
verdict is unchanged — escalation never relaxes the gate (which would create a
bypass incentive) nor tightens it (e.g. revoking the skip token, which would
punish a legitimately trivial commit). Fail-open throughout: a missing / opted-
out / unreadable ledger, or an absent `session_id`, yields count 0 and the plain
(non-escalated) message. Granularity is session-level, matching the gate's
existing model (one review satisfies all commits this session); it does not
distinguish "episode" boundaries (the same commit retried vs. a genuinely
different commit) — an accepted bound, since the banner's claim ("this gate
already blocked N times this session") is true regardless of episode structure.

### Escape hatches

- Add a `[skip-codex-review]` token to the commit `-m` / `--message` message
  for a deliberate skip (e.g. a trivial docs / typo change). A token elsewhere
  in a compound command does not count — for `-F file` / heredoc commits use
  the persistent env bypass instead (see below).
- Set `CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE=1` as a **persistent** environment
  variable — exported in the shell *before* Claude Code starts, or configured
  via the host's session/settings env. **This does NOT work as an inline
  command prefix** (`CLAUDE_HOOK_BYPASS_CODEX_REVIEW_GATE=1 git commit …`):
  the PreToolUse hook runs as a separate process that only inherits the
  Claude Code host's own environment. An inline assignment lives inside
  `tool_input.command` — data the hook inspects, not env applied to the hook
  process — so it never reaches this process's `os.environ`. Verified
  empirically during issue #720 work; the identical inline-prefix bypass is
  documented (and equally non-functional as written) on the retired
  sciomc-finding gate.
- Missing / unreadable transcript, or one whose scan has not yet caught up
  (more than 50 MB of unread bytes this call) → silent pass (cannot enforce).
  Malformed stdin or an unparseable command (unbalanced quotes) → silent
  fail-open.

### Implementation note — token-level classification

Detection operates on `shlex` tokens, not the raw command string. This avoids
three unsound matches a raw-string regex would make: `--amend` inside a `-m`
message would falsely exempt a content commit; `git commit-tree` would falsely
match `commit` via a `\b` boundary; and `echo "git commit"` /
`git log --grep="git commit"` would falsely trip the gate on a non-commit
command. A `git commit` invocation is recognised only as a token-level
`git` → `commit` adjacency.

The tokenizer uses `shlex.shlex(punctuation_chars=True)` (ported from
`block-sciomc-finding-commit` PR #445) which splits shell operators (`;`, `|`,
`(`, `)`) into their own tokens even without surrounding spaces. A hybrid
two-layer approach is used:

1. **Direct tokenisation** detects plain commits, grouped commits `(git commit
   …)`, unquoted command-substitution `$(git commit …)`, and separator-chained
   forms `true;git commit …`.
2. **Span scan** (`_extract_substitutions`) detects commits inside
   double-quoted command-substitution spans `"$(git commit …)"` that `shlex`
   folds into a single token.

Single-quoted text `'$(git commit …)'` is treated as a literal string (bash
does not execute it) — correctly ignored.

### Tests

```bash
bash tests/hooks/preflight-gate/test_block_commit_without_codex_review.sh
```

Covers 57 cases (62 checks; capability-tier cases included): block paths (no invocation, wrong skill, `-F` body, prose-only
slash mention, subagent dir present but no subagent invocation, slash-command
line inside a subagent transcript not satisfying the gate), pass paths (Skill
tool_use, slash command, garbage-line resilience, Skill tool_use inside a
subagent transcript — issue #730), exemptions (amend / allow-empty / merge /
revert / rebase / cherry-pick), escape hatches (`-m` skip token incl. joined /
`--message=` forms, persistent env bypass), token-level edge cases (`git commit-tree`
plumbing, `echo "git commit"`, `git log --grep`, `--amend` inside the message,
skip token outside the message via `;` / `&&`, commit after `&&`),
hardened-parser bypass forms (grouped `(git commit …)`, unquoted substitution
`$(git commit …)`, no-space separator `true;git commit …`, nested substitution,
quoted substitution, single-quoted literal pass, double-quoted literal pass,
terminal options `--help`/`--version`), out-of-scope (non-Bash tool, `git push`
/ `git status`), fail-open (no `transcript_path`, nonexistent path,
malformed stdin, unparseable command), and repeated-block escalation
(issue #805: 1st same-session block has no banner, 2nd block escalates, a
different session is independent). The `count_session_fires` read primitive is
unit-tested
in `tests/test_fire_ledger.py`.
