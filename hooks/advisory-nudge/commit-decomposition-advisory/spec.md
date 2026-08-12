# PreToolUse Commit-Decomposition Advisory

Supported hosts: claude

`hooks/advisory-nudge/commit-decomposition-advisory/impl.py` intercepts `Bash`
tool calls containing a fresh `git commit` whose message is readable from argv,
and emits a **stderr advisory** (never a block) when the message itself already
says the change is more than one commit.

## Why this exists

The decomposition signal is not something to remember — it is something the
agent just wrote. A body listing N bullets is the judgement "this is N
commits"; a title typed `fix` over a body that also says `refactor` is the type
field disagreeing with itself. AGENTS.md (`Atomic Commits`) and memory
`feedback_commit_axis_signal_is_authored_not_recalled` carry the rule, and the
rule keeps not firing because the failing layer is retrieval, not wording: in
the originating session the rule file was opened **0 times** across the 435
transcript entries between the instruction and the commit, and the rule text
was then improved *within that same session* — landing on the same unread
layer. This hook fires at the one moment the message exists and the commit has
not yet run.

## Axis direction is part of the message

Splitting a commit is free: same branch, same PR, same review round. Splitting
an issue or a PR is not — it buys a review round, a merge ordering, a tracker
entry, and a reviewer's context reload, each time. An advisory that nudged
toward "split things" in general would convert this defect into its mirror,
tracker fragmentation, so the emitted text names the commit axis and says
explicitly that the issue axis is not the target.

## Detection

Two signals, evaluated over the `-m` / `--message` values in argv order (first
value is the title, the rest are the body):

| Signal | Fires when |
| --- | --- |
| bullet count | the body has **3 or more** lines matching `^\s*[-*]\s+\S` |
| type disagreement | the title matches `^<type>(\(scope\))?!?:` and the body carries a **different** Conventional type at a line start (optionally behind a bullet) |

Either signal alone emits. `<type>` is the Conventional Commits set:
`feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `perf`, `ci`,
`build`, `revert`.

## What is emitted

Advisory text to stderr; exit 0. Tool execution is never blocked.

| Condition | Result |
| --- | --- |
| fresh `git commit -m` with ≥3 body bullets, or a title/body type disagreement | `[commit-decomp]` advisory naming which signal(s) fired |
| fewer than 3 bullets and no type disagreement | silent |
| `--amend` | silent — rewriting the previous commit is not an authoring point for a split |
| `--dry-run`, `--help`, `-h` | silent — nothing is committed |
| `-F` / `--file` / `-C` / `--reuse-message` / `-c` / `--reedit-message` / `--squash` / `--fixup` | silent — the message does not come from argv |
| no `-m` / `--message` at all (editor flow) | silent |
| `# [commit-decomp-ack]` in the command | silent (explicit ack) |
| `PRAXIS_SKIP_COMMIT_DECOMPOSITION_ADVISORY=1` | silent |
| non-Bash tool, empty command, malformed stdin | silent (fail-open) |

## Detection boundaries

argv-only by design. A message assembled by an editor, or piped in through
`-F -`, is invisible here — recovering it would require a shell parser, which
memory `feedback_shell_parser_diminishing_returns` rules out. The shared
tokenizer boundaries apply as documented in the sibling
`pre-commit-staged-file-enumeration/spec.md`: `result=$(git commit ...)` is
missed, and a `git commit` literal inside a heredoc body false-surfaces. Both
are conservative for an advisory — a miss costs nothing the current state does
not already cost, and a false surface costs one line of stderr.
