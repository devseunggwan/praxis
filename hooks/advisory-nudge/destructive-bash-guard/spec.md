# PreToolUse Destructive Bash Guard

Supported hosts: all

`hooks/advisory-nudge/destructive-bash-guard/impl.py` nudges (or, in strict
mode, asks via `permissionDecision: ask`) before destructive filesystem
operations and privilege-escalation commands.

### Why this exists (issue #463)

`Yeachan-Heo/gajae-code` ships `examples/hooks/permission-gate.ts` (MIT)
which gates dangerous bash (`rm -rf`, `sudo`, etc.) before execution.
praxis has `side-effect-scan` for mutation CLIs (`git commit/push/merge`,
`gh pr merge/create`, `kubectl apply/delete`) but no structural backstop
for filesystem destruction (`rm -rf`, `mkfs`, `dd of=/dev/sda`) or
privilege escalation (`sudo`/`doas`).

Under auto / acceptEdits permission modes, the agent can issue
`rm -rf .` without an out-of-band confirmation prompt. This hook
surfaces a stderr warning (default) or upgrades to an `ask`
`permissionDecision` (strict mode) so the user has a structural
veto point.

### Trigger criteria

Fires when **both** are true:

1. `tool_name == "Bash"` AND `tool_input.command` is non-empty.
2. At least one command segment matches a detection rule below, OR the
   command contains the fork-bomb idiom.

### Detection rules

| Rule | Matches | Examples |
| ------ | --------- | ---------- |
| Privilege escalation | argv[0] is `sudo` or `doas` (RAW argv, before `strip_prefix`) | `sudo apt install`, `doas pacman -S`, `sudo rm anything` |
| `rm` recursive force | `rm` with both `-r`/`-R`/`--recursive` AND `-f`/`--force` | `rm -rf .`, `rm -fr /tmp/x`, `rm -r --force foo`, `rm --recursive --force bar` |
| `dd` raw I/O | `dd` with any `if=…` or `of=…` operand | `dd if=/dev/urandom of=/dev/sda`, `dd if=image.iso of=output.img` |
| `mkfs` | argv[0] is `mkfs` or `mkfs.*` | `mkfs.ext4 /dev/sda1`, `mkfs.btrfs /dev/nvme0n1` |
| `chmod` recursive open | `chmod -R` with mode in {`777`, `0777`, `666`, `0666`} | `chmod -R 777 /var/www`, `chmod --recursive 777 .` |
| `chown` recursive | `chown` with `-R`/`--recursive` | `chown -R user:user /etc` |
| Block-device redirect | `>` or `>>` targeting `/dev/sd*`, `/dev/nvme*`, `/dev/disk*`, `/dev/hd*` | `cat foo > /dev/sda`, `tar c . > /dev/nvme0n1` |
| `git clean -f` | `git clean` with `-f` or `--force` (any bundled form) | `git clean -fd`, `git clean -fdx`, `git clean --force .` |
| `git reset --hard` | `git reset` with `--hard` | `git reset --hard HEAD~1` |
| `find … -delete` | `find` with `-delete` or `--delete` | `find /tmp -name '*.bak' -delete` |
| `truncate -s 0` | `truncate -s 0` / `--size 0` / `-s0` / `--size=0` | `truncate -s 0 logfile` |
| `shred` | argv[0] is `shred` | `shred sensitive.dat` |
| Fork bomb | Literal `:(){ : \| :& };:` (whitespace-stripped match) | `:(){ :\|:& };:` |

### Allow-listed redirect targets (safe sinks)

These device targets are NOT flagged when used as redirect targets:

`/dev/null`, `/dev/stdout`, `/dev/stderr`, `/dev/zero`, `/dev/tty`

### Outcome-proxy signal detection (issue #737)

Separate from the destructive-command rules above, this hook also detects
`git revert`, `gh pr close`, and `gh issue reopen` — reversal/undo-adjacent
commands, NOT destructive ones. This is command-pattern detection only (the
moment the command executes), not state-reversal correlation (whether an
earlier mutation was actually undone).

| Rule | Matches | Examples |
| ------ | --------- | ---------- |
| `git revert` | `git revert` (any flags/target) | `git revert HEAD`, `git revert --no-edit HEAD~1` |
| `gh pr close` | `gh pr close` (leading `gh` flags skipped, incl. `-R`/`--repo <value>`) | `gh pr close 5`, `gh -R o/r pr close 5`, `gh --repo=o/r pr close 5` |
| `gh issue reopen` | `gh issue reopen` (leading `gh` flags skipped, incl. `-R`/`--repo <value>`) | `gh issue reopen 10`, `gh --repo o/r issue reopen 10` |

These fire a **separate** stderr note — `"[destructive-bash-guard] outcome-proxy
signal detected"` — never the "destructive command detected" banner, and they
never escalate to `permissionDecision: ask` even under
`PRAXIS_DESTRUCTIVE_BASH_STRICT=1` (the command is not destructive, so strict
mode has nothing to gate). The stderr write still makes the shared
fire-ledger dispatcher (`hooks/_lib/_fire_ledger.py`) record a
`decision=advise` fire for this hook, which `bypass-review fire-rate`'s
Outcome Proxy section reads as a best-effort `external_write_revert_count`
signal (see that script's module docstring "OUTCOME PROXY LIMITATIONS" for
the precision caveat — the fire-ledger schema cannot distinguish these
patterns from the hook's pre-existing destructive-command detections).

`PRAXIS_HOOK_BYPASS_DESTRUCTIVE_BASH=1` silences these too (full hook
bypass); the strict-mode env var has no effect on them.

### Modes

| Env var | Effect |
| --------- | -------- |
| (unset) | Advisory — stderr text, exit 0. Command proceeds. |
| `PRAXIS_DESTRUCTIVE_BASH_STRICT=1` | Ask — emit `permissionDecision: ask` JSON for destructive-command matches. Outcome-proxy signal matches are unaffected (still informational stderr only). |
| `PRAXIS_HOOK_BYPASS_DESTRUCTIVE_BASH=1` | Full bypass — exit 0 silently (covers both destructive and outcome-proxy signal detection). |

### Examples

| Command | Action | Why |
| --------- | -------- | ----- |
| `rm -rf /tmp/scratch` | **ADVISORY** | rm + recursive + force |
| `rm -fr /tmp/x` | **ADVISORY** | rm + bundled recursive+force |
| `rm -r --force foo` | **ADVISORY** | long form recursive + force |
| `rm /tmp/file` | **SILENT** | no recursive flag |
| `rm -i file` | **SILENT** | interactive only — not destructive enough |
| `ls removed/` | **SILENT** | `removed` is a filename, not `rm` invocation |
| `sudo apt update` | **ADVISORY** | sudo prefix |
| `doas pacman -Syu` | **ADVISORY** | doas prefix |
| `mkdir /tmp/x && rm -rf /tmp/x` | **ADVISORY** | second segment matches |
| `dd if=/dev/urandom of=/dev/sda` | **ADVISORY** | dd raw I/O |
| `dd if=image.iso of=out.img` | **ADVISORY** | dd if=/of= (conservative — issue spec lists `dd if=`) |
| `mkfs.ext4 /dev/sda1` | **ADVISORY** | mkfs |
| `chmod -R 777 /var/www` | **ADVISORY** | chmod -R 777 |
| `chmod 777 file` | **SILENT** | no -R |
| `chown -R user file` | **ADVISORY** | chown -R |
| `cat foo > /dev/sda` | **ADVISORY** | block-device redirect |
| `echo done > /dev/null` | **SILENT** | safe device target |
| `git clean -fdx` | **ADVISORY** | git clean with force |
| `git clean -dn` | **SILENT** | dry-run (`-n`), no force |
| `git reset --hard HEAD` | **ADVISORY** | git reset --hard |
| `git reset HEAD foo` | **SILENT** | not --hard |
| `find /tmp -name '*.tmp' -delete` | **ADVISORY** | find -delete |
| `truncate -s 0 log.txt` | **ADVISORY** | truncate zero |
| `truncate -s 100M file` | **SILENT** | non-zero size |
| `shred secrets.txt` | **ADVISORY** | shred |
| `:(){ :\|:& };:` | **ADVISORY** | fork bomb |

### Response format

**Advisory (default)**:
```
stderr: "[destructive-bash-guard] destructive command detected — ADVISORY
        Detected:
            - <reason>
            - ...
        Destructive commands can permanently delete files, ...
        Bypass options: ..."
exit 0
```

**Strict mode** (`PRAXIS_DESTRUCTIVE_BASH_STRICT=1`):
```
stdout: {"hookSpecificOutput": {"hookEventName": "PreToolUse",
         "permissionDecision": "ask",
         "permissionDecisionReason": "<same advisory text>"}}
exit 0
```

### Parsing guarantees (fail-open)

- malformed JSON stdin → exit 0
- non-Bash tool → exit 0
- empty / whitespace command → exit 0
- uncaught exception in inner logic → swallowed, exit 0

### Relationship to sibling hooks

| Hook | Scope | Overlap |
| ------ | ------- | --------- |
| `side-effect-scan` | mutation CLI gate (`git commit/push/merge`, `gh pr merge/create`, `kubectl apply/delete`) | Complementary — side-effect-scan covers project-state mutations via established CLIs; this hook covers filesystem destruction + privilege escalation. No detected command overlap (rm/dd/mkfs/sudo/doas/find/shred are NOT in side-effect-scan). |
| `cross-boundary-preflight` | heredoc cross-boundary write, cross-repo ask | None — different defect class |
| `inspection-chain-advisory` | `&&`-chained inspection commands | Different intent — that hook nudges silent-cascade; this one nudges destruction. Both can fire on the same compound command if applicable. |
| `protected-paths-guard` | Edit/Write/NotebookEdit on sensitive files | Different matcher (Edit/Write vs Bash) |

### Known limitations

The hook is intentionally conservative — false-positive `ask` prompts have
non-trivial UX cost, and the hook fires only on argv shapes that clearly
match the destructive idiom. Known false-negatives:

| Case | Behaviour |
| ------ | ----------- |
| `bash -c "rm -rf ."` | silent — the inner command is a string operand of `bash`, not parsed by the tokenizer's argv scanner. Detect via shell wrapper at the cost of N false positives on legitimate `bash -c "echo hi"` calls. |
| Path with `rm` in the name (`/usr/local/bin/myrm`) | classified as `rm` if basename == `rm`; the basename check could yield false positives on non-coreutils `rm` binaries with destructive semantics. Acceptable in practice. |
| `python -c 'import shutil; shutil.rmtree(".")'` | silent — interpreter-internal calls bypass the bash-level detection |
| `xargs rm -rf` | silent — `xargs` is not in the wrapper set; the delegated `rm` is not inspected |
| Subshell `(cd / && rm -rf foo)` | silent — subshell parens corrupt the argv-0 lookup |
| Mode permutations beyond the listed `777`/`666` set (e.g., `chmod -R 707`) | silent — covered modes are the canonical world-write patterns, but world-write-only modes (`707`, `077`) are equally destructive |

### Tests

```bash
bash tests/hooks/advisory-nudge/test_destructive_bash_guard.sh
```

Cases cover: every detection rule, false-positive guards (`rm -i`, `removed/`
path, `git clean -n`, safe device targets, non-recursive chmod), compound
command decomposition (`mkdir x && rm -rf x`), strict-mode JSON output,
bypass env var, infrastructure fail-open, and the outcome-proxy signal rules
(`git revert`, `gh pr close`, `gh issue reopen` — including that strict mode
does NOT escalate them to `ask`, and that a compound command mixing a
destructive match with a signal match emits both).
