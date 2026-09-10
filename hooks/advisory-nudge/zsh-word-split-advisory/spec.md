# PreToolUse zsh Word-Split Advisory

Supported hosts: all

`hooks/advisory-nudge/zsh-word-split-advisory/impl.py` fires on PreToolUse for
`Bash` tool calls and emits a **stderr advisory** (never a block, always exit
0) when an unquoted `$var` sits where the author almost certainly meant several
words and zsh will pass one.

## Why this exists

`SH_WORD_SPLIT` is off by default in zsh. `set -- $spec` therefore passes ONE
argument holding the whole string, and `for x in $list` iterates once over it.
bash does the opposite, which is where the habit comes from.

The failure is silent at the shell. The command runs, a wrong argv reaches the
tool, and the error — when there is one — names the receiving CLI's argument
parser. Measured across the local transcript corpus: **182 uses in 72 sessions,
82 of them (45%) ending in an error**, the most frequent text being a CLI
complaining that an argument is required for a flag it was in fact given. The
other 99 exited cleanly, and those are the half with no signal at all.

Verified empirically on zsh 5.9 rather than taken from documentation:

```text
$ zsh -f -c 'spec="a b"; set -- $spec;    print $#'
1
$ zsh -f -c 'spec="a b"; set -- ${=spec}; print $#'
2
```

## Why advisory and not a block

Passing a deliberately unsplit single argument uses the identical syntax. The
hook cannot tell an intended one-argument call from a mistaken one, and a gate
that cannot tell them apart must not block — it can only make the fork visible
while the command is still being written. `${=var}` is the one-character opt-in
to splitting; quoting is the one-character opt-in to the single argument.

## Detected shapes

| Shape | Behavior |
| --- | --- |
| `set -- $var` / `set - ${var}` | Advisory |
| `for x in $var` | Advisory |
| `"$var"` in either position | Silent — one argument, unambiguously intended |
| `${=var}`, `$=var`, `${(s: :)var}` | Silent — the split form is already used |
| `$@`, `$*`, `$#`, `$?`, `$1` … | Silent — own splitting rules, not what was measured |
| Inside a heredoc body | Silent — data, not words |
| `$SHELL` is not zsh, or unset | Silent — bash and fish split it as written |
| `# word-split:ok` on the command | Silent — opt-out |
| Malformed stdin, non-Bash tool | Silent — fail-open |

The command is scanned with quoted runs masked in place, so a `$var` inside
quotes cannot match while the surrounding text still reads normally.

## Scope this deliberately does not cover

Only the two shapes the corpus measured. An unquoted `$var` handed to an
ordinary command (`gh pr view $args`) is the same mechanism, but it is also the
overwhelmingly common *correct* usage — one argument, meant as one argument —
so warning there would bury the two shapes where the intent is legible from the
syntax itself.

Reference: issue [#1405](https://github.com/devseunggwan/praxis/issues/1405).
