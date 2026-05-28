#!/usr/bin/env python3
"""UserPromptSubmit hook: inject post-compaction context into the next prompt.

Reframes the original #466 design ("PreCompact custom_instructions") after
Wave 0 falsified its premise. The Claude Code PreCompact event accepts only
top-level decision/reason/continue/stopReason/suppressOutput/systemMessage —
no `hookSpecificOutput.additionalContext` channel exists at that event.

The transcript JSONL however records compaction as a `user`-type record with
`isCompactSummary: true` and a stable `uuid`. UserPromptSubmit fires before
each new user prompt and DOES support `hookSpecificOutput.additionalContext`,
so the same goal — make the post-compaction prompt aware of carried-over
session state — can be achieved by detecting the compaction marker in the
transcript tail and injecting context on the first prompt after it.

Behaviour
=========

On every `UserPromptSubmit`:

1. Read `transcript_path`, `session_id`, `cwd` from the payload. Any missing
   → silent fail-open.
2. Tail-read the transcript JSONL (last N lines, default 100) looking for the
   most recent `{"type": "user", "isCompactSummary": true, ...}` entry.
3. If none found → silent (no compaction in recent history).
4. Compare the compaction entry's `uuid` against the session state file's
   `last_compact_uuid_emitted`. If they match → already injected for this
   compaction, silent.
5. Gather context (read-only, fail-open per source):
     - `session_id`        — from payload
     - `cwd`               — from payload (worktree absolute path)
     - `git branch`        — `git -C <cwd> branch --show-current`
     - `active PR`         — `gh pr list --state open --head <branch>` (JSON)
     - `strike state`      — read `$HOME/.claude/state/praxis/strikes/<sid>.json`
6. Emit `hookSpecificOutput.additionalContext` JSON to stdout.
7. Update state file with `last_compact_uuid_emitted = <uuid>`.

State file
==========

`${TMPDIR:-/tmp}/praxis-postcompact-context-${session_id}.json`

Path resolution: `PRAXIS_POSTCOMPACT_CONTEXT_FILE` env override → session_id
keyed file. The PPID fallback used by sibling `preflight-gate/session-intent`
is dropped here because `main()` rejects payloads with missing `session_id`
before `resolve_state_path` is reached, so the fallback would be unreachable.

Env vars
========

`PRAXIS_POSTCOMPACT_CONTEXT_FILE`   — explicit state file path (test override)
`PRAXIS_HOOK_BYPASS_POSTCOMPACT_CONTEXT=1` — full bypass (silent, no scan)
`PRAXIS_POSTCOMPACT_TAIL_LINES`     — tail line count (default 100)

Fail-open
=========

Every external call (file read, subprocess, JSON decode) is wrapped. No
exception propagates. Worst case: a compaction goes uninjected. The hook
NEVER blocks the prompt.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import deque
from pathlib import Path

DEFAULT_TAIL_LINES = 100
BYPASS_ENV = "PRAXIS_HOOK_BYPASS_POSTCOMPACT_CONTEXT"
STATE_FILE_ENV = "PRAXIS_POSTCOMPACT_CONTEXT_FILE"
TAIL_LINES_ENV = "PRAXIS_POSTCOMPACT_TAIL_LINES"


# ---------------------------------------------------------------------------
# Payload + state-file helpers (mirrors session-intent canonical pattern)
# ---------------------------------------------------------------------------

def _extract_session_id(payload: dict) -> str | None:
    sid = payload.get("session_id")
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    return None


def resolve_state_path(session_id: str) -> str:
    """Resolve the dedup state file path.

    Priority: `PRAXIS_POSTCOMPACT_CONTEXT_FILE` env override → `session_id`
    keyed file under `${TMPDIR:-/tmp}`. The caller is responsible for
    rejecting missing session_id BEFORE calling this — `main()` already
    does so, so no PPID fallback is needed (the session-intent hook keeps
    one only to support direct CLI / test invocation without a payload).
    """
    explicit = os.environ.get(STATE_FILE_ENV, "").strip()
    if explicit:
        return explicit
    tmp = os.environ.get("TMPDIR", "/tmp").rstrip("/")
    return os.path.join(tmp, f"praxis-postcompact-context-{session_id}.json")


def read_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError, UnicodeDecodeError):
        # ValueError covers JSONDecodeError + embedded-null path raises.
        pass
    return {}


def write_state(path: str, state: dict) -> None:
    try:
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except (OSError, ValueError):
        pass  # non-fatal: next run will simply re-inject


# ---------------------------------------------------------------------------
# Transcript tail scan
# ---------------------------------------------------------------------------

def _tail_lines(path: str, n: int) -> list[str]:
    """Return the last `n` lines of a UTF-8 text file. Empty list on error.

    Uses a bounded deque so memory stays O(n) regardless of file size — a
    transcript JSONL can grow to tens of MB over a long session. ValueError
    is caught alongside OSError to cover embedded-null path payloads.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return list(deque(fh, maxlen=n))
    except (OSError, ValueError):
        return []


def find_latest_compact_summary(transcript_path: str, n_lines: int) -> dict | None:
    """Scan the tail of the JSONL transcript for the most recent compaction.

    A compaction is encoded as `{"type": "user", "isCompactSummary": true, ...}`.
    The function returns the most-recent matching record (by file order — the
    transcript is append-only and time-ordered) or None.
    """
    lines = _tail_lines(transcript_path, n_lines)
    if not lines:
        return None
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw or '"isCompactSummary"' not in raw:
            # quick lexical filter — avoids json.loads cost for >99% of records
            continue
        try:
            record = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        if record.get("type") != "user":
            continue
        if record.get("isCompactSummary") is not True:
            continue
        return record
    return None


# ---------------------------------------------------------------------------
# Context gathering (read-only, fail-open per source)
# ---------------------------------------------------------------------------

def _run(cmd: list[str], cwd: str | None = None, timeout: float = 2.0) -> str:
    """Run a read-only subprocess and return stdout (stripped) or ''.

    ValueError is caught alongside OSError/SubprocessError so embedded-null
    cwd / argv payloads degrade silently instead of crashing the hook.
    """
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode == 0:
            return (result.stdout or "").strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return ""


def _git_branch(cwd: str) -> str:
    # 1.5s leaves headroom under the 8s manifest budget when paired with
    # _active_pr's 3s. Git branch-show is essentially instant; the timeout
    # exists only to bound pathological FS conditions.
    return _run(["git", "-C", cwd, "branch", "--show-current"], cwd=cwd, timeout=1.5)


def _active_pr(cwd: str, branch: str) -> dict | None:
    """Return {"number": int, "url": str, "title": str} or None.

    Uses `gh pr list --state open --head <branch>` so we resolve a PR even
    when cwd is not the repo root the PR was created from. Fail-open on
    missing gh / no PR / JSON parse error.
    """
    if not branch:
        return None
    # 3.0s keeps the worst-case (gh call) + 1.5s git + ~0.5s python startup
    # under the 8s manifest budget. Authenticated gh on a fast network
    # responds in <1s; the timeout exists to bound auth-prompt / network-hung
    # paths so the hook never trips Claude Code's hard timeout.
    out = _run(
        [
            "gh", "pr", "list",
            "--state", "open",
            "--head", branch,
            "--json", "number,url,title",
            "--limit", "1",
        ],
        cwd=cwd,
        timeout=3.0,
    )
    if not out:
        return None
    try:
        data = json.loads(out)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        entry = data[0]
        if entry.get("number") and entry.get("url"):
            return {
                "number": entry["number"],
                "url": entry["url"],
                "title": entry.get("title") or "",
            }
    return None


def _strike_state(session_id: str) -> dict | None:
    """Read the praxis strike state for this session. None if absent/empty."""
    state_dir = os.environ.get(
        "PRAXIS_STATE_DIR",
        os.path.join(os.path.expanduser("~"), ".claude", "state", "praxis"),
    )
    path = os.path.join(state_dir, "strikes", f"{session_id}.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    count = data.get("count")
    if not isinstance(count, int) or count <= 0:
        return None
    reasons = data.get("reasons")
    if not isinstance(reasons, list):
        reasons = []
    return {"count": count, "reasons": [str(r) for r in reasons]}


def build_context(
    session_id: str,
    cwd: str,
    compact_uuid: str,
    compact_timestamp: str,
) -> str:
    """Assemble the human-readable additionalContext block."""
    branch = _git_branch(cwd)
    pr = _active_pr(cwd, branch) if branch else None
    strikes = _strike_state(session_id)

    lines = [
        "📎 Praxis post-compaction context",
        "",
        "Session state carried across the compaction boundary:",
        f"  • session_id : {session_id}",
        f"  • cwd        : {cwd}",
        f"  • branch     : {branch or '(detached/unknown)'}",
    ]

    if pr:
        lines.append(f"  • active PR  : #{pr['number']} — {pr['title']}")
        lines.append(f"                 {pr['url']}")
    else:
        lines.append("  • active PR  : (none for current branch)")

    if strikes:
        lines.append(f"  • strikes    : {strikes['count']}/3")
        for idx, reason in enumerate(strikes["reasons"], start=1):
            lines.append(f"      {idx}. {reason}")
    else:
        lines.append("  • strikes    : 0/3")

    lines.append("")
    ts_repr = compact_timestamp or "(unknown)"
    lines.append(
        f"Compaction marker uuid={compact_uuid} timestamp={ts_repr}. "
        "This context is injected once per compaction event; subsequent prompts "
        "will not repeat it."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _tail_lines_setting() -> int:
    raw = os.environ.get(TAIL_LINES_ENV, "").strip()
    if not raw:
        return DEFAULT_TAIL_LINES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TAIL_LINES
    if value < 1:
        return DEFAULT_TAIL_LINES
    return value


def main() -> int:
    if os.environ.get(BYPASS_ENV, "").strip() == "1":
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError, OSError):
        return 0
    if not isinstance(payload, dict):
        return 0

    transcript_path = payload.get("transcript_path")
    session_id = _extract_session_id(payload)
    cwd = payload.get("cwd")

    if not isinstance(transcript_path, str) or not transcript_path:
        return 0
    if not session_id:
        return 0
    if not isinstance(cwd, str) or not cwd:
        return 0
    if not Path(transcript_path).is_file():
        return 0

    compact = find_latest_compact_summary(transcript_path, _tail_lines_setting())
    if not compact:
        return 0

    compact_uuid = compact.get("uuid")
    compact_timestamp = compact.get("timestamp", "")
    if not isinstance(compact_uuid, str) or not compact_uuid:
        return 0

    state_path = resolve_state_path(session_id)
    state = read_state(state_path)
    if state.get("last_compact_uuid_emitted") == compact_uuid:
        return 0  # already injected for this compaction

    context = build_context(session_id, cwd, compact_uuid, compact_timestamp)

    state["last_compact_uuid_emitted"] = compact_uuid
    write_state(state_path, state)

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        },
        sys.stdout,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
