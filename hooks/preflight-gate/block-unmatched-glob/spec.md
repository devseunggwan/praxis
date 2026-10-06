# PreToolUse Unmatched-Glob Gate

Supported hosts: claude
Requires: zsh (the verdict is delegated to a zsh nomatch replay; without zsh the check passes vacuously)

`hooks/preflight-gate/block-unmatched-glob/impl.py` intercepts `Bash` tool calls and **blocks**
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
| Executing shell (`$SHELL`) is not zsh, or unset | Silent — nothing aborts there |
| No glob metacharacters in the command | Silent — pass |
| Metacharacters were quoted (`-name '*.log'`) | Silent — never expanded |
| Unquoted `$` / `` ` `` (variable, arithmetic, substitution) in a word | That word is skipped — its value is unresolvable; **the other words are still judged** |
| `$` / `` ` `` in the command word of any segment (`$CMD *.x`) | Silent — `$CMD` may be `noglob`, `setopt`, or `cd` |
| Quoted or escaped spelling of a word the gate keys on, as the command word of any segment (`\setopt`, `'cd'`) | Silent — zsh unquotes it before the lookup; a quoted path (`"/opt/my tool"`) is judged as usual |
| Expansion whose end cannot be found (unterminated, newline inside, `case` inside `$(…)`) | Silent — word boundaries unknown |
| Leading `cd <dir>` followed by `&&`, `;`, or a newline, with a plain-word `<dir>` that starts with `/`, `~/`, `./`, or `../` and exists | **Stripped; the rest is judged with `<dir>` as the cwd** |
| Unquoted `&&`, `\|\|`, `&`, `\|&`, `<<`, `;;`, or a backslash-newline | Silent — segment context unknown |
| `&` touching a redirect arrow (`2>&1`, `<&0`, `&>out`, `&>>out`, `&>\|out`) | **Not a background marker — the command is judged as if the `&` were absent** |
| Unquoted `;`, `\|`, or newline | **Cut into segments; each simple command judged on its own** |
| `case` word anywhere, or `;` / `\|` / newline inside `( … )` or `[[ … ]]` (case arms, glob groups `(a\|*.c)`, subshells, multi-line conditions) | Silent — there they are pattern grammar, nesting, or whitespace, not separators |
| `setopt` / `unsetopt` / `emulate` / `eval` / `source` / `.` / `alias`, or `set` with an option flag, **in command position of any segment** (after `builtin`, `command`, `time`, `!`, …) | Silent — a later segment expands under options set earlier |
| Control-flow word (incl. zsh `foreach`/`end`/`always`/`coproc`) or function definition **in command position**, after any precommand word | **Judging stops there** — earlier segments are still judged; the body may run zero times |
| Arithmetic command `(( … ))` | Silent — its words are math, not pathnames |
| `cd` / `pushd` / `popd` **in command position of any segment** (other than the leading `cd <dir> &&` above) | Silent — a later segment runs in a directory the probe would not use |
| Assignment word **before the command word** (`FOO=*.x cmd`) | Silent — values are not glob-expanded |
| `noglob` / `setopt` / `unsetopt` / `eval` **in command position** (also after `time`, `!`, `builtin`, …) | Silent for that segment — failure disabled by the command |
| Shell-syntax word (`[`, `[[`, `]`, `]]`) | Silent — not a pathname pattern |
| Pattern inside a `#` comment | Silent — never reaches the shell |
| zsh expands the pattern successfully | Silent — pass |
| zsh reports `no matches found` | **Blocked (exit 2)** |
| Malformed stdin, non-Bash tool, zsh unavailable, probe timeout | Silent — fail-open |

This trades recall for precision deliberately. `make && echo *.md` does
abort in zsh when `make` succeeds, and the gate lets it through — a blocking
gate that halts a valid command is worse than one that misses a case. The original incident
(`ls -d <path> <glob> 2>/dev/null`) is a single simple command and is still
caught.

`;` and `\|` were on the pass-through side of that trade until #1405, and they
are where the misses actually were: across the local transcript corpus, 129 of
144 `no matches found` aborts (90%) came from a compound command, because a
chained investigation line is exactly what an agent writes. Neither separator
changes how the words around it expand — each side of `a ; b` and `a \| b` is an
ordinary simple command whose own words expand under the same `nomatch` — so
each segment is now judged alone. `&&` and `\|\|` stay out: they decide whether
the next command runs at all, and blocking a command that would never have run
is the false positive this gate is most careful about. `&` and `<<` stay out
for their own reasons (detaching, heredoc bodies that are data rather than
words).

A newline joined them until #1559. An agent's multi-line investigation command
is usually a list of plain lines, so across the local transcript corpus 124 of
597 `no matches found` aborts since 2026-09-16 passed through on a newline,
second only to `&&` / `\|\|`. A plain newline is now a separator like `;`. What
a newline can open is handled by where judging stops, not by passing the line:
segments are judged up to the first one whose command word — after prefix
assignments and precommand words such as `!`, `time`, `builtin` — opens a
compound command (`if`, `for`, `while`, `{`, zsh `foreach`, `repeat`,
`coproc`, …) or defines a function. Everything before it runs unconditionally;
from it on, a body may run zero times. That cut also fixes a false block the
`;` split had: `while false; do :; echo *.x; done` dropped only the `do :`
segment and judged the next one, which never runs. The cost is that a glob in
a loop body is never judged even when the loop does run — 4 rows of the corpus
that the old per-segment rule caught. A backslash-newline, a newline inside
`( … )` or `[[ … ]]`, and a newline inside an expansion still pass the whole
command.

One `&&` is the exception, since #1554. In a leading `cd <dir> &&` the text
decides both questions: the rest runs exactly when `<dir>` is a directory, and
it runs there. That shape was 167 of the 229 `&&` pass-through aborts in the
local transcript corpus — an agent's investigation line usually starts by
entering a worktree. When `<dir>` is a plain word (no quoting, expansion, glob,
or option) that starts with `/`, `~/`, `./`, or `../` (or is exactly `~`, `.`,
or `..`) and exists, the prefix is stripped and the rest is judged by every
rule above with `<dir>` as the cwd. A bare relative `<dir>` such as `logs`
passes through: zsh looks it up in `cdpath` before the cwd when `cdpath` lists
another entry ahead of `.` or `posixcd` is set, and the hook cannot see the
shell's `cdpath`. The same rule passes `cd -`, `cd +N` (the directory stack),
`=cmd`, and `~user`. `~/…` is expanded; `..` is normalised rather than
resolved, matching zsh's logical `cd`. Where `..` follows a symlink,
`chaselinks` or `chasedots` would resolve it physically instead, so a `<dir>`
whose logical and physical resolutions differ passes through. So does a
`<dir>` holding `^`, `#`, or a non-leading `~`, which `extendedglob` turns into
pattern syntax, and so does a missing `<dir>`, because then the rest never
runs. Since #1559 the same strip applies when `;` or a newline follows the
`cd`: the rest then runs either way, but in `<dir>` exactly when `<dir>` is a
directory, which is the only case stripped — 45 of the 124 newline aborts
started with such a line. A second `&&` or any `\|\|` in the rest still passes through whole, and
so does a rest with `cd`, `pushd`, or `popd` in any segment's command
position. The remaining false positive is a `<dir>` that exists but cannot be
entered (no search permission), where the gate blocks a command that would not
have run.

The `&` marker is matched on the skeleton, so until #1526 it also caught the
`&` inside a redirect. `ls *.x 2>&1 | head` read as a background job and passed
through whole; across the local transcript corpus that was 79 of 559 `no
matches found` aborts since 2026-09-16 (14%), the third-largest miss after
dynamic prefixes and `&&` / `\|\|`. An `&` directly after `<` or `>`, or
directly before `>`, is now masked before the markers are checked. Live zsh
confirms every such form leaves no background job (`$!` stays `0`) while
`cmd &`, `&\|`, and `&!` each leave one. `\|&` pipes both streams; its `&` is
not masked and the command still passes through.

A `$` or backtick used to pass the whole command through. Since #1555 it makes
only its own word undecidable. zsh performs every substitution before any
filename generation, and an expanded value never becomes a separator, so the
other words expand exactly as written: `ls *.x; echo "$?"` and
`` echo `date` *.x `` abort in zsh and are now caught. Across the local
transcript corpus that rule was the largest miss: since 2026-09-16, 217 `no
matches found` aborts passed through on a `$`, 152 of them with a literal glob
word. The scanners consume an expansion whole — `$(…)`, `$((…))`, `${…}`,
`$[…]`, backticks, and outside double quotes `$'…'` / `$"…"` — so the
separators, `cd`, `&&`, quotes, and spaces in its body neither cut the line
nor pass it through. The word holding the expansion is never probed, so the
hook never runs a substitution. Two cases still pass the whole command
through: a dynamic command word in any segment, since `$CMD` may be `noglob`,
`setopt`, or `cd`; and an expansion whose end the scanner cannot find
(unterminated, a newline inside, or a `case` inside `$(…)`, whose `pat)`
breaks paren counting).

Two consequences are accepted. A glob inside an expansion's value
(`${x:-*.x}`) is a miss: zsh does glob it, but it is part of a dynamic word.
And a substitution with a filesystem side effect is not modelled:
`ls $(touch a.x) *.x` runs in zsh, because the substitution creates the match
before globbing, yet the gate blocks it — the same trade it already makes for
`touch a.x; ls *.x` across a `;`.

The separator split is index-aligned with the *unquoted skeleton*, so a `;`
inside quotes is invisible here exactly as it is to the shell. Two disabler
scopes are distinguished, because segmentation makes the difference observable
for the first time: `noglob` and `eval` are prefixes that shield their own
command, so only their segment is dropped and a neighbour on the same line is
still judged — while `setopt` / `unsetopt` change the running shell's options
and therefore outlive their command, so a line containing one passes through
whole.

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

## Where this gate is inert

The premise is zsh-specific, so the gate is silent wherever it does not hold —
in every case by passing through, never by blocking on a guess.

| Environment | Behavior |
| --- | --- |
| Login shell is bash / fish (`$SHELL`) | Silent. Those hand the literal pattern to the command, which runs, and whose stderr `2>/dev/null` does suppress — the hazard does not exist |
| `$SHELL` unset | Silent. The executing shell is unknown, and an unknown shell is not a licence to block |
| zsh binary absent (most Linux containers and CI images) | Silent. Both probes fail and return the never-block value |
| Login shell sets `nullglob` / `nonomatch` / `noglob` / `cshnullglob` | Silent. Nothing aborts in the first place |
| Login shell startup exceeds the 2s probe timeout | Silent. Timeout is a fail-open path, not a fail-closed one |

The repo's own CI installs zsh for exactly this reason: without it the hook's
suite tests nothing but the fail-open path, which passes vacuously.

## Bypass

None. The gate fires only on commands that the shell would refuse to run
anyway, so there is no correct case to preserve — every fix listed in the block
message produces a command that both runs and expresses the original intent.
