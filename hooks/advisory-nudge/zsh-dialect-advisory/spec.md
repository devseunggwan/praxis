# PreToolUse zsh Dialect Advisory

Supported hosts: all

`hooks/advisory-nudge/zsh-dialect-advisory/impl.py` fires on PreToolUse for
`Bash` tool calls and reports four shapes that behave differently under zsh
than the bash habit they come from. Three are deterministic and return
`permissionDecision: ask`; the fourth cannot be decided from syntax and stays
an advisory.

## Why this exists

All four were observed in a single session on macOS default zsh, and each one
reports a failure that names something other than the shell — or nothing at
all. Every behaviour below was verified on this machine's zsh 5.9 rather than
read from a manual.

### 1 — `=word` is a command path lookup (ask)

```text
$ zsh -f -c 'echo ======'
zsh:1: ===== not found
$ zsh -f -c 'x=a; [ "$x" == a ] && echo yes'
zsh:1: = not found
```

EQUALS expansion resolves a word starting with `=` as a command path. A
separator line of equals signs dies, and so does `[ "$x" == y ]` — `==` outside
`[[ ]]` is such a word. Inside `[[ ]]` zsh parses the condition itself, so the
same `==` is an operator and the hook stays silent there. A bare `=`
(`test 1 = 1`) is not expanded and is not reported.

### 2 — an unmatched `[` in a pattern operator is a glob (ask)

```text
$ zsh -f -c 'w="[[x"; print ${w#[[}'
zsh:1: bad pattern: [[
$ zsh -f -c 'w="[[x"; print "${w#[[}"'
zsh:1: bad pattern: [[
```

Only `#`, `##`, `%`, `%%`, `/` and `//` take a pattern; `${w:-[[}` is a default
value and is left alone. Double quotes do not protect the pattern — quoting the
pattern itself (`${w#"[["}`) is the fix — so this detector masks single-quoted
runs only.

### 3 — a heredoc opener shadowed by its own delimiter (ask)

```text
$ zsh -f nested.zsh          # body opens another <<'EOF'
/tmp/nested.zsh:6: command not found: EOF
```

The inner opener is data. The first terminator line closes the **outer**
heredoc, and every line after it runs as a command — so the rest of the
compound command, often an edit, silently never runs. A nested heredoc with a
*different* delimiter is the normal form and stays silent. A body that merely
*mentions* the delimiter is not this bug either: what separates the two is the
terminator count, so the delimiter is reported only when the body carries its
own closing line on top of the outer one.

This shape is shell-general, so it fires under bash as well as zsh.

### 4 — no word splitting (advisory)

```text
$ zsh -f -c 'spec="a b"; set -- $spec;    print $#'
1
$ zsh -f -c 'spec="a b"; set -- ${=spec}; print $#'
2
```

`SH_WORD_SPLIT` is off by default in zsh, so `set -- $spec` passes ONE argument
and `for x in $list` iterates once over the whole string. The failure is silent
at the shell; the error, when there is one, names the receiving CLI's argument
parser. Measured across the local transcript corpus: **182 uses in 72 sessions,
82 of them (45%) ending in such an error**.

## Why three ask and one advisory

A deterministic shape cannot do what it says whatever the author meant, so an
`ask` gives the call a correction point while it is still being written — the
stderr line arrives with the dispatch and cannot. Shape 4 is different in kind:
passing a deliberately unsplit single argument uses identical syntax, and a
gate that cannot tell intent apart must not interrupt. It writes
`additionalContext` (the only exit-0 channel the actor reads) and stderr (what
the fire ledger grades the fire on).

Neither path is a block. `ask` surfaces the fork; it does not deny the command.

Measured fire rate across the local corpus (851 transcripts, 147,281 Bash
calls): **263 ask-grade fires (0.18%)** — 243 `=word`, 1 pattern, 19 heredoc —
and 486 advisory-grade word-split fires (0.33%).

## Detected shapes

| Shape | Behavior |
| --- | --- |
| `echo ======`, `echo =foo`, `[ "$x" == y ]`, `V==foo` | `ask` |
| `${w#[[}`, `"${w#[[}"`, `${w/[[/Z}` | `ask` |
| An opener inside an open body reusing the outer delimiter, with its own terminator | `ask` |
| `set -- $var`, `set - ${var}`, `for x in $var` | Advisory (`additionalContext` + stderr) |
| `[[ $x == y ]]`, `test 1 = 1`, `print a=b`, `--stat=2` | Silent — not the shape |
| `${w#[a-z]}`, `${w:-[[}`, `'${w#[[}'` | Silent — valid pattern, non-pattern operator, or protected |
| A nested heredoc with a different delimiter, or a body that mentions one | Silent |
| `"$var"`, `${=var}`, `$=var`, `${(s: :)var}`, `$@`, `$1` | Silent |
| Inside a heredoc body | Silent for shapes 1, 2 and 4 — data, not words |
| `$SHELL` is not zsh, or unset | Silent for shapes 1, 2 and 4; shape 3 still fires |
| `# zsh-dialect:ok` or `# word-split:ok` on the command | Silent — opt-out |
| Malformed stdin, non-Bash tool | Silent — fail-open |

`# word-split:ok` predates the other three shapes (#1405) and keeps working;
`# zsh-dialect:ok` silences every detector.

## Scope this deliberately does not cover

An unquoted `$var` handed to an ordinary command (`gh pr view $args`) is the
same mechanism as shape 4, but it is also the overwhelmingly common *correct*
usage, so warning there would bury the two shapes where the intent is legible
from the syntax itself.

Reference: issues [#1405](https://github.com/devseunggwan/praxis/issues/1405)
and [#1425](https://github.com/devseunggwan/praxis/issues/1425).
