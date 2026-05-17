# Privacy Policy

Praxis is a local-only Claude Code plugin. Praxis code itself stores state
only on the user's filesystem and does not transmit data on its own. However,
praxis invokes external CLIs (`git`, `gh`, `cmux`, `claude`, `codex`,
`gemini`) on the user's behalf, and some of those CLIs make network calls —
see the [No Telemetry](#no-telemetry) section below for the enumerated egress
paths.

## Transcript Reading

Certain hooks read the Claude Code session transcript (a `.jsonl` file whose
path is passed by the Claude Code runtime in the hook payload as
`transcript_path`). This is done entirely locally to inspect the most-recent
user message or recent Bash commands.

| Hook | What it reads | Purpose |
|------|--------------|---------|
| `completion-verify.sh` | Last ~400 lines of transcript | Verify that a Bash verification command was run in the same turn as a completion claim |
| `retrospect-mix-check.sh` | Last ~400 lines of transcript | Confirm that retrospect Stage 3 outputs include non-memory action types |
| `block-ask-end-option.py` | Most recent user message | Detect whether the user sent a stop signal before blocking an end-option menu item |
| `block-manufactured-action-menu.py` | Most recent user message | Detect command-intent signals to suppress unnecessary confirmation menus |
| `external-write-falsify-check.py` | Recent Bash commands | Confirm a verification call precedes an external write |

Transcript data is read locally only, never forwarded or stored beyond the
hook's in-process execution.

## Session State Files

Hooks persist lightweight per-session state in the OS temporary directory
(`${TMPDIR:-/tmp}`) using the `session_id` field from the Claude Code hook
payload as the key. When `session_id` is unavailable, `PPID` is used as a
back-compat fallback.

| Hook | State file | Contents |
|------|-----------|---------|
| `trino-describe-first.py` | `${TMPDIR:-/tmp}/praxis-describe-history-<session_id>.json` | Set of Trino table names that have been `DESCRIBE`d in this session |
| `session-intent.py` | `${TMPDIR:-/tmp}/praxis-session-intent-<session_id>.json` | Detected session intent flag (read vs. mutation) |
| `pre-edit-md-escape-advisory.py` | `${TMPDIR:-/tmp}/praxis-md-read-history-<session_id>.json` | Set of `.md` file paths Read in this session |

Strike counter state is stored in a dedicated directory:

| Hook | State file | Contents |
|------|-----------|---------|
| `strike-counter.sh` | `${PRAXIS_STATE_DIR:-$HOME/.claude/state/praxis}/strikes/<session_id>` | Strike count and violation reasons for the active session |

All state files contain only session-scoped metadata (table names, path sets,
counters). They never contain the text of user messages or assistant responses.

## Memory Access

`memory-hint.py` reads `*.md` files from the project memory directory
(`~/.claude/projects/<slugified-cwd>/memory/`, or `PRAXIS_MEMORY_DIR` if set).
This is a read-only scan to surface relevant memory entry descriptions as
advisory stderr output. Hooks never modify memory files.

## External CLI Invocations

Praxis hooks invoke `git` and `gh` with the user's own credentials and
environment (see [SECURITY.md — Hook External-Command Allowlist](SECURITY.md)).
Praxis does not intercept, log, or store the output of these commands beyond
the hook's in-process execution. Third-party CLI privacy policies apply.

## No Telemetry

Praxis itself does not include analytics, telemetry, or error reporting.
There is no phone-home and no usage tracking embedded in praxis code.

However, praxis hooks and skills DO invoke external CLIs (`git`, `gh`,
`cmux`, `claude`, `codex`, `gemini`) with the user's own credentials.
Some of those invocations make network calls — most notably:

- `hooks/pre-gh-pr-create-dedup-gate.py` runs `gh pr list` against the
  target repo's PR search API to detect duplicate PRs.
- `skills/cmux-delegate` performs two distinct egress steps when run
  in a GitHub-backed repo:
  1. **Context collection** — calls `gh pr list --head <branch>` and
     `gh api repos/<owner>/<repo>/pulls/<num>/comments` to enrich the
     delegated prompt with current PR metadata.
  2. **Prompt forwarding** — pipes the resulting prompt file to
     `claude` / `codex` / `gemini` CLIs, each of which sends the
     prompt to its respective provider's API.

These egress paths are visible in the hook/skill source (see SECURITY.md
"Hook External-Command Allowlist") and run under the user's environment.
Praxis does not intercept, store, or transmit additional data via its
own code paths beyond what the invoked CLI requires.
