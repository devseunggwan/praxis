# PreToolUse Count-Assertion Alternation Advisory

Supported hosts: all

`hooks/advisory-nudge/count-assertion-verify/impl.py` fires on every `PreToolUse(Bash)` event.
It detects `grep -c` commands whose pattern contains an alternation operator
and emits an advisory reminder to verify each arm separately before citing
the count.

### Why this exists

**Recurring failure mode (2x occurrence):**

`grep -c 'pat1\|pat2'` or `grep -Ec 'pat1|pat2'` produces a combined count
that may be inflated when one alternation arm matches unintended lines. The
count is then cited as a precise figure in a PR body, spec, or report without
verifying each arm individually.

  **Incident (PR #258):** `grep -c '^run_case\|^run_test'` returned 29. The
  count was cited as "29 cases" in the PR body. Codex reviewer corrected it
  to 28 — the `\|^run_test` arm introduced one extra match. Rerunning with
  `grep -c '^run_case '` confirmed 28.

The *Information Accuracy* rule's Layer 2 ([`ETHOS.md` → Rules praxis carries](../../../ETHOS.md#rules-praxis-carries)) already requires:

> **Data-driven claims** → show the query/command output first. Verify test
> inputs actually reproduce the intended case before accepting results.

Despite this rule being loaded into context, memory-only retrieval failed
twice. A structural hook adds an attention-shift at the grep invocation point.

References: issue [#277](https://github.com/devseunggwan/praxis/issues/277),
prior entry `feedback_input_surface_enumeration.md`.

### What is detected

| Condition | Advisory |
| ----------- | ---------- |
| `grep` subcommand + count flag (`-c` / `--count` / combined `-cE`) + BRE alternation (`\|`) | Yes |
| `grep` subcommand + count flag + ERE alternation (unescaped `\|`) with `-E`/`-P`/`--extended-regexp`/`--perl-regexp` | Yes |
| `grep -c 'single_pattern'` (no alternation) | No |
| `grep 'pat1\|pat2'` (no count flag) | No |
| `git grep`, `wc -l`, or other non-`grep` commands | No |
| Command contains `# count-verified` | No (opt-out) |
| Malformed payload / missing field | No (fail-open) |

#### Alternation detection rules

| Mode | Alternation token | Example pattern |
| ------ | ------------------ | ---------------- |
| BRE (default grep) | `\|` (backslash-pipe) | `'^run_case\|^run_test'` |
| ERE (`-E`) | unescaped `\|` | `'pat1\|pat2'` |
| PCRE (`-P`) | unescaped `\|` | `'(cat)\|(dog)'` |

In BRE mode, a bare `|` is a literal pipe (not alternation) and does **not**
trigger the advisory. In ERE/PCRE mode, an unescaped `|` is alternation.

#### Supported flag forms

The hook recognises all common grep flag styles:

| Form | Example |
| ------ | --------- |
| Combined short flags | `-cE`, `-Ec`, `-cP`, `-Pci` |
| Separate short flags | `-c -E`, `-E -c` |
| Long flags | `--count --extended-regexp` |
| Explicit pattern flag | `-c -e 'pat1\|pat2'`, `--regexp=pat1\|pat2` |

### Opt-out

Append `# count-verified` to the command when the count has already been
verified per-arm in the current session:

```bash
grep -c 'run_case\|run_test' tests/file.sh  # count-verified
```

The marker suppresses the advisory for that specific command.

### Advisory message

```
[count-assertion-verify] `grep -c` with alternation detected. Alternation can
inflate the count if one arm matches unexpected lines (prior incident: PR #258
body claimed 29 cases, Codex corrected to 28). Verify each arm separately
before citing this count:
  grep -c 'pat1' file   # arm 1
  grep -c 'pat2' file   # arm 2
Opt-out: append `# count-verified` to the command.
```

Emitted to stderr. Exit code `0`. Never blocks — advisory only.

### Parsing guarantees

| Condition | Behavior |
| ----------- | ---------- |
| Malformed / missing stdin JSON | exit 0 (silent pass) |
| `tool_name` not `Bash` | exit 0 (silent pass) |
| Missing `command` field | exit 0 (silent pass) |
| Command parse error (shlex failure) | exit 0 (silent pass, fail-open) |
| `python3` unavailable | exit 0 (shell shim guards) |
| Hook `.py` file missing | exit 0 (shell shim guards) |
| Any uncaught exception in inner logic | exit 0 (silent pass) |

The hook imports `_hook_utils.safe_tokenize`, `iter_command_starts`, and
`strip_prefix` for robust tokenization (handles quoted strings, env prefixes,
wrapper commands like `sudo`, `env`, shell keywords, and compound separators
`&&`, `;`, `|`, `||`).

### Tests

```bash
bash tests/hooks/advisory-nudge/test_count_assertion_verify.sh
```

Covers 25 cases:

**Advisory (should fire):**
- BRE alternation `\|` with `-c` (original incident pattern)
- BRE alternation with multiple arms
- ERE alternation with `-E -c` (separate flags)
- ERE alternation with `-Ec` / `-cE` (combined flags)
- ERE alternation with `--count --extended-regexp` (long flags)
- BRE alternation with `--count` long flag
- PCRE alternation with `-Pc`
- Combined `-cP` flags
- Advisory fires in compound commands (`&&`, `;`)
- Explicit pattern via `-e pat1\|pat2`

**Pass (false-positive guards):**
- Single-pattern `grep -c 'single'` (no alternation)
- ERE mode without alternation
- `grep 'pat1\|pat2'` without `-c` flag
- `grep -l` (list-files, not count)
- `wc -l` (not grep)
- `git grep -c` (different command family)
- Non-grep commands (`ls`)
- `# count-verified` opt-out
- Opt-out in compound command
- BRE bare `|` is literal (not alternation in BRE mode)
- Non-Bash tool name

**Edge:**
- Malformed JSON stdin → exit 0, silent pass
- Empty command string → silent pass
