# PreToolUse Unmatched-Glob Gate

Supported hosts: claude

`hooks/block-unmatched-glob.sh` intercepts `Bash` tool calls and **blocks**
(exit 2) when the command contains an unquoted glob that matches nothing.

## Why this exists

The Bash tool's shell is zsh. On an unmatched glob zsh aborts the entire
command at expansion time (`no matches found`) rather than falling back to the
literal pattern the way bash does. Two consequences make this a correctness
hazard, not noise:

1. **`2>/dev/null` does not suppress it.** The error comes from the shell's
   expansion stage, not from the command, so the redirect never applies.
2. **The command never runs.** "No output" therefore cannot be distinguished
   from "ran and found nothing" — and the agent reads the empty result as an
   established fact.

Real incident (retrospect 2026-07-27, 4th occurrence of this family):

```sh
ls -d ~/projects/resume ~/projects/*/resume 2>/dev/null
```

`~/projects/*/resume` matched nothing, so `ls` never executed. The empty result
was read as "the repo is not local"; the repo was cloned from GitHub, and the
authoritative source for the task was found 20 turns late. Every claim produced
in that window rested on a derived record instead.

The three prior occurrences were logged as "noise accumulation" and remediated
at the memory layer. The 4th occurrence — the first to produce a wrong
conclusion rather than a noisy log — falsifies that remedy, which is what moves
this to structural enforcement.

## Detection

The verdict is **delegated to zsh**, not re-implemented. Each candidate word is
replayed as:

```sh
zsh -f -c 'setopt nomatch; : <source-span>'
```

`:` is the no-op builtin, so glob expansion is the only effect; `-f` skips
startup files so the user's own `setopt nullglob` cannot mask the answer. The
gate fires only when zsh itself reports `no matches found` — precisely the
aborting case.

`-f` also discards options that decide *whether a pattern matches*, which would
make the probe answer a different question than the shell that runs the command
— under `setopt extendedglob`, `^*.md` matches. So the executing shell's own
`setopt` output is read once and its glob-relevant entries (`extendedglob`,
`kshglob`, `nocaseglob`, `globdots`, `bareglobqual`, `globstarshort`,
`globsubst`, and their `no…` forms) are replayed into the probe. `nomatch` is
set last, so nothing forwarded can override it.

The candidate keeps its **original source span**, quotes and any trailing zsh
qualifier included, so mixed quoting (`ARCH*".*"`) and per-occurrence
qualifiers (`*.x(N); *.x`) are judged exactly as written.

The gate judges only a **single simple command**. Anything else passes
through, because there a word's meaning depends on shell grammar the hook does
not model — which branch actually runs, what the cwd is by the time a later
segment executes, whether the text is a heredoc body:

| Condition | Behavior |
| --- | --- |
| No glob metacharacters in the command | Silent — pass |
| Metacharacters were quoted (`-name '*.log'`) | Silent — never expanded |
| Unquoted `$` / `` ` `` (variable, arithmetic, substitution) | Silent — prefix unresolvable |
| Unquoted compound structure: `&&`, `\|\|`, `\|`, `;`, `&`, newline, `<<` | Silent — segment context unknown |
| Control-flow word or `cd` **in command position** | Silent — same reason |
| Assignment word **before the command word** (`FOO=*.x cmd`) | Silent — values are not glob-expanded |
| `noglob` / `setopt` / `unsetopt` / `eval` **in command position** | Silent — failure disabled by the command |
| Shell-syntax word (`[`, `[[`, `]`, `]]`) | Silent — not a pathname pattern |
| Pattern inside a `#` comment | Silent — never reaches the shell |
| zsh expands the pattern successfully | Silent — pass |
| zsh reports `no matches found` | **Blocked (exit 2)** |
| Malformed stdin, non-Bash tool, zsh unavailable, probe timeout | Silent — fail-open |

This trades recall for precision deliberately. `cd hooks && echo *.md` does
abort in zsh and the gate lets it through — a blocking gate that halts a valid
command is worse than one that misses a case. The original incident
(`ls -d <path> <glob> 2>/dev/null`) is a single simple command and is still
caught.

Position, not mere presence, decides the pass-through rows above. All three of
these abort in zsh and are all caught: `LC_ALL=C ls *.missing` (the assignment
is inert, the glob beside it is not), `echo noglob *.missing` (a disabler used
as an argument disables nothing), and `grep 'a|b' *.missing` (a quoted `|` is
not a pipeline). The pass-through rows are matched against the command's
*unquoted skeleton* — quoted text, escaped characters, and comment bodies are
masked first — and against each word's position, not against a raw substring
search.

Note that a glob attached to a flag (`--include=*.log`) **is** a candidate:
zsh expands the whole word before the tool ever sees it, so an unmatched
pattern there aborts exactly like a bare one.

## Why zsh decides, and not this hook

A pure-Python model was written first and rejected. Checked against live zsh it
disagreed on ten of fourteen cases, across seven independent axes: `**`
recursive globs, brace expansion, mixed quoting within a single word, qualifier
placement, `noglob` / `setopt`, variable prefixes, and shell-syntax words such
as `[`. Those are shell *semantics*, not pattern syntax — reproducing them
outside the shell means reimplementing zsh. Delegating the one question that
matters ("would this expand?") to zsh removes the whole class of divergence.

The sibling preflight gates use the role-aware token API (issue #263); this one
does not, because `Token.text` is produced **after shlex unquoting**, which
discards exactly the signal the gate depends on. `find -name "*.log"` and
`find -name *.log` collapse to the same token text, yet only the second aborts.
A quote-aware scanner that preserves source spans is therefore kept locally,
while `_lib`'s `fail_open` wrapper and `format_block` renderer are shared as
usual. Recorded here rather than left implicit, per AGENTS.md
"Convention Survey Before Design".

## Bypass

None. The gate fires only on commands that the shell would refuse to run
anyway, so there is no correct case to preserve — every fix listed in the block
message produces a command that both runs and expresses the original intent.
