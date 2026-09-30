# PreToolUse zsh Dialect Advisory

Supported hosts: all

`hooks/advisory-nudge/zsh-dialect-advisory/impl.py` fires on PreToolUse for
`Bash` tool calls and reports five shapes that behave differently under zsh
than the bash habit they come from. Four are deterministic and return
`permissionDecision: ask`; shape 4 cannot be decided from syntax and stays
an advisory.

## Why this exists

Shapes 1-4 were observed in a single session on macOS default zsh, shape 5 in a
later one, and each one
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

### 5 — an assignment to `status` (ask)

```text
$ zsh -f -c 'status=$?'
zsh:1: read-only variable: status
$ zsh -f -c 'local status=1'
zsh:1: read-only variable: status
$ zsh -f -c 'local status'; echo "rc=$?"
status=0
rc=0
```

`status` is zsh's read-only alias of `$?`. Every assignment to it fails:
`status=`, `status+=` or `status[i]=` as a leading word of a simple command,
and a `status=` argument of `local`, `typeset`, `declare`, `export`,
`readonly`, `integer` or `float`.

A word is leading when it starts a command (the start of the text, or after
`;`, `&`, `|`, a newline, `(`, `$(`, `<(`, `>(`, `=(`, a backtick, `{`, the end
of a case pattern, or a `f()` / `function f` header) and is preceded only by other
assignments, redirections (`>out`, `2> err`, `&>out`), the words `if` `then` `else`
`elif` `do` `while` `until` `!` `time` `nocorrect` `coproc`, or `repeat` and its
count.

The scan keeps one prefix per nesting level, so `a=$(x) status=1 cmd` is still
an assignment. An unquoted backslash-newline is joined first, as zsh does, so a
`status=` split across lines is still one word. After `case … in` and after
each `;;`, `;&` or `;|`, the next word is a case pattern, so `(status=1)` there
is a pattern, not a subshell.

A declaration without a value (`local status`) succeeds and is not reported,
and neither is the same text as an ordinary argument (`echo status=1`, `echo $(date) status=1`, `env status=1 cmd`), an array
element (`arr=(a status=1)`), part of `${…}` or `{a,b}`, or a case pattern.

## Why four ask and one advisory

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
and 486 advisory-grade word-split fires (0.33%). Shape 5 came later and has no
measured fire rate.

## Detected shapes

| Shape | Behavior |
| --- | --- |
| `echo ======`, `echo =foo`, `[ "$x" == y ]`, `V==foo` | `ask` |
| `${w#[[}`, `"${w#[[}"`, `${w/[[/Z}` | `ask` |
| An opener inside an open body reusing the outer delimiter, with its own terminator | `ask` |
| `status=$?`, `true; status=1`, `$(status=1)`, `a=$(x) status=2 cmd`, `status+=1`, `status[1]=x`, `>out status=1 cmd`, `f() { status=1; }`, `local status=1`, `export status=1` | `ask` |
| `set -- $var`, `set - ${var}`, `for x in $var` | Advisory (`additionalContext` + stderr) |
| `[[ $x == y ]]`, `test 1 = 1`, `print a=b`, `--stat=2` | Silent — not the shape |
| `(( x == y ))`, `$(( 1 == 1 ))`, `print hi # a==b`, `true;# a==b` | Silent — arithmetic and comments are not expanded words; a `#` starts a comment after whitespace or `;` `&` `\|` |
| `${w#[a-z]}`, `${w#[[]}`, `${w#[]]}`, `${w#[[:alpha:]]}`, `${w:-[[}`, `'${w#[[}'` | Silent — valid pattern, non-pattern operator, or protected |
| `${w#[]}`, `${w#[!]}` | Silent — an open class led by `]` is a no-match in zsh 5.9, not a bad pattern |
| A nested heredoc with a different delimiter, or a body that mentions one | Silent |
| `"$var"`, `${=var}`, `$=var`, `${(s: :)var}`, `$@`, `$1` | Silent |
| `local status`, `STATUS=1`, `exit_status=1`, `echo status=1`, `echo $(date) status=1`, `env status=1 cmd`, `--status=x`, `arr=(a status=1)`, `echo ${status=1}`, `case $x in status=1) …` | Silent — no assignment to `status` |
| Inside a heredoc body | Silent for shapes 1, 2, 4 and 5 — data, not words |
| `$SHELL` is not zsh, or unset | Silent for shapes 1, 2, 4 and 5; shape 3 still fires |
| `# zsh-dialect:ok` or `# word-split:ok` on the command | Silent — opt-out |
| Malformed stdin, non-Bash tool | Silent — fail-open |

`# word-split:ok` predates the other three shapes (#1405) and keeps working;
`# zsh-dialect:ok` silences every detector.

## Scope this deliberately does not cover

An unquoted `$var` handed to an ordinary command (`gh pr view $args`) is the
same mechanism as shape 4, but it is also the overwhelmingly common *correct*
usage, so warning there would bury the two shapes where the intent is legible
from the syntax itself.

The detectors scan quote-masked text and are not a shell parser; shape 5 adds
only a nesting stack, enough to tell a command's words from a value's. They
stay that way on purpose: an `ask` is only worth its interruption on a shape
that is cheap to recognise and certain to fail. Known gaps, each measured
against zsh 5.9 and left in place:

- **Missed `=word` after a control operator** — `true;=foo` and
  `true&&=foo` fail in zsh, but only a word preceded by whitespace or the start
  of the command is scanned.
- **Missed `==` after a literal `[[` argument** — `echo [[ $x == y ]]` fails
  in zsh, but every `[[ … ]]` span is masked as a condition, including one that
  is only an argument to another command.
- **Heredoc shapes inside a quoted string** — a multi-line quoted string
  containing two `<<EOF` openers and two `EOF` lines is read as a shadowed
  heredoc and asks, although it is only data. The opener scan is not
  quote-aware, and `<<-` terminators are compared after a full `strip()`
  rather than tab-only removal.
- **`status` bound by something other than an assignment** —
  `for status in a b`, `read status` and `(( status = 1 ))` fail the same way,
  but none is an assignment word, so none is scanned. `$((status=1))` fails
  too, but arithmetic is masked before the scan. A `status=` inside a
  double-quoted `"$(…)"` is masked with the quotes and is missed as well.
- **`time status=1` with no command asks** — zsh performs no assignment there
  and succeeds, but `time status=1 cmd` fails, and the scan treats `time` as a
  prefix word for both.

Closing any of them needs a real tokenizer (quotes, comments, control
operators, arithmetic and conditional contexts in one pass). That is a
different hook, not a patch to this one.

Reference: issues [#1405](https://github.com/devseunggwan/praxis/issues/1405),
[#1425](https://github.com/devseunggwan/praxis/issues/1425) and
[#1526](https://github.com/devseunggwan/praxis/issues/1526).
