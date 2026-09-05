# Privacy Policy

Praxis is a local-only plugin for Claude Code, Codex, and Cursor. Praxis code
stores state only on the user's filesystem and does not transmit data on its
own. It does invoke external CLIs (`git`, `gh`, `zsh`, `cmux`, `claude`,
`codex`, `gemini`) on the user's behalf, and some of those CLIs make network
calls — see [External CLI Invocations](#external-cli-invocations) and
[Telemetry](#telemetry) for the enumerated egress paths.

## Transcript Reading

Many hooks read the Claude Code session transcript (a `.jsonl` file whose path
the runtime passes in the hook payload as `transcript_path`). The read is
local and bounded: the shared reader in
[`hooks/_lib/_transcript.py`](hooks/_lib/_transcript.py) loads a tail of the
file (400 lines by default) or scans it incrementally from the last-seen
offset, never the whole file. Each hook uses what it reads to answer one
question about the session inside its own process — did a verification
command run in the turn that claims completion, did the user send a stop
signal, does a claimed PR state match a fresh fetch, did the last user message
carry an approval — and then exits.

Three classes of hook read the transcript:

- **Stop-lane gates** (`hooks/completion-verify/*`) — inspect the turn that is
  about to end for an evidence claim without evidence behind it.
- **Advisory scans** (`hooks/advisory-nudge/*`, e.g. `postcompact-context`,
  `caller-probe-gate`, `unenforced-step-advisory`) — look for a recent
  command or rule the current call should have honoured.
- **Preflight gates** (`hooks/preflight-gate/*`, e.g. `block-ask-end-option`,
  `rejected-mutation-reconsent-gate`) — read the most recent user message
  before deciding whether to block a tool call.

The list changes as hooks are added; the current set is

```bash
grep -rl transcript_path hooks --include='impl.py' --include='impl.sh'
```

and each hook's `spec.md` says what it looks for. Transcript text never
leaves the hook process. Hooks persist only the derived flags, counters, and
offsets described in the next section — never a copy of a message.

## Session State Files

Hooks persist lightweight per-session state under a host-neutral `~/.praxis`
root, keyed by the `session_id` field of the hook payload (`PPID` is a
back-compat fallback for direct CLI invocation). The full layout — every root,
every file, its producer and consumers — is maintained in
[`docs/runtime-state-layout.md`](docs/runtime-state-layout.md); this section
summarises what each root holds so a reader can judge its sensitivity.

| Root                    | Holds                                                                                                | Override                                 |
| ----------------------- | ---------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| `~/.praxis/state/`      | Durable state: strike counts and the reasons the agent declared for them; phantom-path dedup markers | `PRAXIS_STATE_DIR`, `PRAXIS_HOME`        |
| `~/.praxis/cache/`      | Session-scoped flags and dedup sets: detected intent, retrospect marker, Read-file path sets, last compact id, approval-premise acks, poll-loop waiter ids | `PRAXIS_HOME`; swept after `PRAXIS_CACHE_TTL_DAYS` (7) |
| `~/.praxis/logs/`       | `hook-errors.jsonl` (exception text from a hook that failed open) and Stop-gate block logs           | `PRAXIS_HOOK_ERROR_LOG`, `PRAXIS_HOME`   |
| `~/.praxis/telemetry/`  | The two local ledgers described under [Telemetry](#telemetry)                                        | `PRAXIS_*_TELEMETRY_FILE`, `PRAXIS_HOME` |
| `~/.praxis/docs/specs/` | Feature specs a person writes; praxis only reads them                                                | `PRAXIS_HOME`                            |

`PRAXIS_HOME` relocates the whole tree. When the home directory is not
writable, files fall back to `${TMPDIR}/praxis-<file>` (pre-#903 sessions kept
session state there permanently, and a one-time move adopts such files).

State files hold session-scoped metadata — flags, counters, path sets,
hashes, and short agent-written labels such as a strike reason or an approval
premise. They never hold a copy of a user message or an assistant response.

## Memory Access

`hooks/advisory-nudge/memory-hint/impl.py` reads `*.md` files from the project
memory directory (`~/.claude/projects/<slugified-cwd>/memory/`, or
`PRAXIS_MEMORY_DIR` if set). This is a read-only scan to surface relevant
memory entry descriptions as advisory stderr output. Hooks never modify memory
files.

## External CLI Invocations

Praxis hooks invoke `git`, `gh`, and `zsh` with the user's own credentials and
environment — `git` and `gh` for read-only repository and PR state queries,
`zsh` for one-word glob-expansion probes. The per-hook allowlist, verified
against each hook's `external_commands` declaration, is in
[SECURITY.md — Hook External-Command Allowlist](SECURITY.md#hook-external-command-allowlist).
Praxis does not intercept, log, or store the output of these commands beyond
the hook's in-process execution. Third-party CLI privacy policies apply.

## Telemetry

Praxis includes no analytics, no error reporting to a remote service, and no
phone-home. It does keep two **local, append-only ledgers** under
`~/.praxis/telemetry/`, so a reader who finds that directory should know what
is in it:

| Ledger                           | One line per                                   | Fields                                                      | Off switch                       |
| -------------------------------- | ---------------------------------------------- | ----------------------------------------------------------- | -------------------------------- |
| `fire-events-YYYY-MM-DD.jsonl`   | hook invocation                                | hook name, role, decision (block/ask/advise/pass), session id, tool name | `PRAXIS_FIRE_TELEMETRY_DISABLE=1` |
| `bypass-events-YYYY-MM-DD.jsonl` | tool call made while a `PRAXIS_*` bypass variable was set | timestamp, session id, tool name, the variables set, the first 200 characters of the command with leading `NAME=value` secrets redacted, result status | `PRAXIS_BYPASS_TELEMETRY_DISABLE=1` |

The fire ledger records no command text. The bypass ledger keeps a redacted,
200-character prefix of the command so a bypass can be reviewed later; neither
records file contents or transcript text. Files rotate daily, are gzipped on rollover, and are deleted after
`PRAXIS_TELEMETRY_RETENTION_DAYS` (default 30). Their only readers are the
`bypass-review` CLI wrapper and the evidence-based audits under `docs/`, both
of which run locally. See [`docs/bypass-telemetry.md`](docs/bypass-telemetry.md).

### Network egress through invoked CLIs

Praxis hooks and skills DO invoke external CLIs (`git`, `gh`, `cmux`,
`claude`, `codex`, `gemini`) with the user's own credentials, and some of
those invocations make network calls — most notably:

- `hooks/preflight-gate/pre-gh-pr-create-dedup-gate/impl.py` runs `gh pr list`
  against the target repo's PR search API to detect duplicate PRs.
- `hooks/preflight-gate/pr-state-refetch-gate/impl.py`,
  `gh-merge-worktree-precondition/impl.py`, and `anchor-comment-gate/impl.py`
  run `gh pr view` / `gh api` reads to compare a claim against live PR state.
- `skills/cmux-delegate` performs two distinct egress steps when run
  in a GitHub-backed repo:
  1. **Context collection** — calls `gh pr list --head <branch>` and
     `gh api repos/<owner>/<repo>/pulls/<num>/comments` to enrich the
     delegated prompt with current PR metadata.
  2. **Prompt forwarding** — passes the resulting prompt file to the
     `claude` / `codex` / `gemini` CLI, each of which sends the prompt to
     its provider's API.

These egress paths are visible in the hook/skill source (see SECURITY.md
"Hook External-Command Allowlist") and run under the user's environment.
Praxis does not intercept, store, or transmit additional data via its own
code paths beyond what the invoked CLI requires.
