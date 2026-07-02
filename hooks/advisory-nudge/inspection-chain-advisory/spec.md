# PreToolUse `&&`-Chained Inspection Advisory

Supported hosts: all

`hooks/advisory-nudge/inspection-chain-advisory/impl.py` nudges the agent
to split inspection-only commands chained with `&&` into separate Bash
tool calls so a non-match exit code (e.g. `grep` returning 1) does not
silently drop every later probe in the chain.

### Why this exists (issue #469)

A `cmd1 && cmd2 && ... && cmdN` chain halts at the first non-zero exit,
so a probe that returns "no match" (`grep` exit 1, `test` exit 1, `ls`
of an absent path) silently aborts the chain. The agent then assumes
every step "completed" because nothing surfaced to stderr.

This recurred in the 2026-05-28 retrospect session: a 6-step probe chain
where step 4 returned no match, steps 5–6 never ran, and the agent
reported all six as verified until a follow-up call surfaced the gap.

Memory entry `feedback_bash_exit_code_if_trap.md` already documents the
rule ("검사용 명령은 `&&` 체인 금지·별도 호출 split") and was loaded
into context during the failing session, but retrieval at
command-composition time failed. This hook moves a fragment of the
enforcement to the Bash boundary as a structural backstop, regardless
of which root cause (retrieval discipline / working-memory bandwidth)
explains a given session's failure.

### Trigger criteria

The advisory fires when **both** are true:

1. The Bash command contains at least one `&&`-connected chain of 2 or
   more segments.
2. **Every** segment in that chain is an inspection-only command — a
   pure read binary (`grep`, `find`, `ls`, `wc`, `head`, `tail`, `cat`,
   `stat`, `file`, `which`, `test`, `[`, `du`, `df`, `echo`, `printf`,
   `true`, `false`, `basename`, `dirname`, `realpath`, `readlink`,
   `tr`, `sort`, `uniq`, `cut`, `awk`, `sed`, `jq`, `yq`, `diff`,
   `cmp`, `date`, `hostname`, `uname`, `pwd`, `env`, `printenv`), or
   an inspection-only subcommand of `git` (`log`, `status`, `diff`,
   `show`, `branch`, `tag`, `worktree`, `rev-parse`, `ls-files`,
   `cat-file`, `blame`, `grep`, `describe`, `reflog`, `shortlog`,
   `name-rev`, `merge-base`, `show-ref`, `diff-tree`, `diff-files`,
   `diff-index`, `rev-list`, `config`, `fsck`, `count-objects`), or
   `gh` — top-level inspection (`search`, `status`, `auth`,
   `alias`, `config`, `browse`, `help`, `extension`) and noun-verb
   form (`gh <pr|issue|repo|release|gist|workflow|run|label|secret|
   variable|ssh-key|gpg-key|codespace|org|project> <view|list|status|
   diff|checks|ready>`).

A chain that mixes inspection and state-changing commands (e.g.
`mkdir foo && ls foo` — the canonical "make state, then verify it"
pattern) is **silent**. Only chains where every segment is read-only
trigger the advisory, because those are the chains where a non-match
exit silently drops downstream probes.

### Examples

| Command | Action |
| --------- | -------- |
| `grep X foo.txt && grep Y bar.txt` | **ADVISORY** — both segments read-only |
| `gh issue view 1 && gh pr view 2 && git log -1` | **ADVISORY** — three read-only segments |
| `find . -name '*.py' && wc -l *.py` | **ADVISORY** — find + wc, both read-only |
| `git log --oneline && git status` | **ADVISORY** — both git inspection subcmds |
| `mkdir foo && ls foo` | **SILENT** — mkdir is state-changing |
| `cd repo && grep X file` | **SILENT** — cd is cwd mutation |
| `cp a b && diff a b` | **SILENT** — cp is state-changing |
| `grep X file \|\| echo missing` | **SILENT** — `\|\|` is fallback, not chain |
| `grep X file; grep Y file` | **SILENT** — `;` is unconditional sequencing |
| `grep X file \| sort \| uniq` | **SILENT** — pipe, not `&&` chain |
| `find . -name '*.tmp' -delete && ls` | **SILENT** — `-delete` is mutation form |
| `sed -i 's/a/b/' foo && cat foo` | **SILENT** — `-i` is mutation form |
| `git commit -m x && git push` | **SILENT** — both mutation subcmds |
| `gh pr create && gh pr view` | **SILENT** — create is mutation |

### Response format

```
stderr: "[inspection-chain-advisory] `&&`-chained inspection commands
        Detected: <bin1> && <bin2> && ...
        <body>"
exit 0
```

Advisory-only: the hook **never blocks** and never emits JSON. The user
sees the stderr text and decides whether to split the chain or proceed.

### Parsing guarantees (fail-open)

- malformed JSON stdin → exit 0
- non-Bash tool → exit 0
- empty / whitespace command → exit 0
- uncaught exception in inner logic → swallowed, exit 0

### Relationship to sibling hooks

| Hook | Scope | Overlap |
| ------ | ------- | --------- |
| `cli-flag-incompat-advisory` | mode-incompatible flag combinations on `git`/`kubectl` | None — different defect class |
| `side-effect-scan` | gate on mutation CLIs (`git commit`, `gh pr merge`, `kubectl apply`) | None — this hook silently allows mutation chains |
| `cross-boundary-preflight` | heredoc cross-boundary write | None — different defect class |
| `memory-hint` | reminds agent to consult memory before a Bash call | Complementary — memory rule exists but retrieval fails at composition time; this hook surfaces a fragment at execution time |

### Known limitations

Coverage is intentionally conservative — false-positive nudges have
non-zero cost, and the hook is advisory only. Known false-negatives
(cases where the silent-cascade defect could still bite):

| Case | Behaviour |
| ------ | ----------- |
| `(grep x && grep y)` (subshell) | silent — subshell parens corrupt the argv-0 lookup, classifier returns False |
| `find . && xargs wc -l` | silent — `xargs` is not in the inspection allowlist; its delegated binary is not inspected |
| `git config user.name X` / `git branch -d foo` / `git tag v1.0` / `git worktree add ...` | classified as inspection — flat subcommand allowlist does not inspect argv. Chains mixing these with reads still nudge spuriously; pure-mutation chains slip through. `side-effect-scan` is the right hook for these. |
| `gh auth login` / `gh alias set` / `gh config set` / `gh extension install` | classified as inspection — top-level `gh` allowlist does not split inspection from mutation verbs |
| `awk '{print > "/tmp/foo"}'` style internal redirect | classified as inspection — token-level guard cannot see redirects inside the awk script string |
| `gh pr view -w` (`--web` opens browser) | classified as inspection — browser-opening side effect not modelled |

The fail-open philosophy treats these as "the next round of empirical
friction will tighten the allowlist". Filing a follow-up issue per
recurrence is cheaper than expanding the surface to catch theoretical
cases.

### Tests

```bash
bash tests/test_inspection_chain_advisory.sh
```

Cases cover: advisory firing on pure-inspection chains (grep, gh view,
git log, find/wc, mixed pure-inspection binaries), silent on
state-changing chains (mkdir, cp, cd, git commit, gh pr create),
silent on non-`&&` separators (`||`, `;`, `|`), silent on
mutation-form flags (sed -i, find -delete), and infrastructure
fail-open (non-Bash, malformed JSON, empty command).
