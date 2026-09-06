#!/usr/bin/env python3
"""SessionStart(compact) hook: re-inject session context after a compaction.

History of the design, because each step falsified the one before it:

- #466 proposed a `PreCompact` hook seeding the summary. `PreCompact` accepts
  only top-level decision / reason / continue / stopReason / suppressOutput /
  systemMessage — no `hookSpecificOutput.additionalContext` channel.
- #472 moved to `UserPromptSubmit`, which does carry `additionalContext`, and
  detected the compaction by scanning the transcript tail for the
  `{"type": "user", "isCompactSummary": true, "uuid": ...}` record. Because
  the hook then fired on EVERY prompt and the marker stayed in the tail for
  many of them, it needed a per-session state file recording the last
  injected uuid, a `state_lock` around it (#1034), and a bounded reverse
  reader (#1155) — all machinery for answering "did a compaction just
  happen?" from the outside.
- #1339 (this file): Claude Code's `SessionStart` event exposes a `compact`
  matcher and a `source` payload field, and supports
  `hookSpecificOutput.additionalContext` (`hookEventName: "SessionStart"`).
  The hooks guide ships a "Re-inject context after compaction" recipe that
  is exactly `SessionStart` + `"matcher": "compact"`. `PostCompact` exists
  but "hooks have no decision control" and its `systemMessage` / `continue`
  fields are discarded, so it is not a context channel either. Read
  2026-09-06 at https://code.claude.com/docs/en/hooks and
  https://code.claude.com/docs/en/hooks-guide.

The event IS the trigger now: it fires once per compaction, so the transcript
scan, the uuid state file, the lock, and the tail-line budget are gone.

Behaviour
=========

On `SessionStart` with manifest matcher `compact`:

1. Bypass env set → silent.
2. Read the payload. `source`, when present, must be `compact`; any other
   value (`startup`, `resume`, `clear`, `fork`, ...) → silent. The manifest
   matcher already filters, so this is a belt for a host that ignores it.
3. `session_id` and `cwd` are required; either missing → silent fail-open.
4. Gather context (read-only, fail-open per source):
     - `session_id`  — from payload
     - `cwd`         — from payload (worktree absolute path)
     - `git branch`  — `git -C <cwd> branch --show-current`
     - `active PR`   — `gh pr list --state open --head <branch>` (JSON)
     - `strike state`— `~/.praxis/state/strikes/<sid>.json` first; fallback
                       to `~/.claude/state/praxis/strikes/<sid>.json` when
                       no `PRAXIS_STATE_DIR` override is set and the new
                       location is absent (pre-#527 legacy support)
5. Emit `hookSpecificOutput.additionalContext` JSON to stdout, exit 0.

Time budget
===========

`git` 1.5s + `gh` 3.0s + ~0.5s interpreter startup is a 5.0s ceiling under
the manifest's `timeout: 8`. There is no lock to wait on any more.

Env vars
========

`PRAXIS_HOOK_BYPASS_POSTCOMPACT_CONTEXT=1` — full bypass (silent)
`PRAXIS_POSTCOMPACT_GIT_TIMEOUT` / `PRAXIS_POSTCOMPACT_GH_TIMEOUT` — test
overrides for the subprocess timeouts (seconds); production keeps the defaults.

Coverage
========

The hooks reference lists the `compact` matcher as "Auto or manual
compaction" (read 2026-09-06), so this one registration covers `/compact`
and the automatic compaction alike. Documented, not yet observed live from
this repo; nothing here depends on the distinction.

Fail-open
=========

Every external call (file read, subprocess, JSON decode) is wrapped. No
exception propagates. Worst case: a compaction goes uninjected. The hook
NEVER blocks the session.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _git import run_git  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _paths import (  # type: ignore[import-not-found]  # noqa: E402
    legacy_state_dir,
    praxis_state_dir,
)
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402

BYPASS_ENV = "PRAXIS_HOOK_BYPASS_POSTCOMPACT_CONTEXT"
COMPACT_SOURCE = "compact"


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------

def _extract_session_id(payload: dict) -> str | None:
    sid = payload.get("session_id")
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    return None


def is_compact_source(payload: dict) -> bool:
    """True unless the payload names a SessionStart `source` other than compact.

    The manifest registers this hook with `matcher: "compact"`, so the host
    should only ever deliver `source: "compact"`. A missing `source` is
    accepted — the matcher already did the filtering and an older host that
    omits the field must not silence the hook. Any OTHER value means the
    matcher was not honoured, and the context would land on a fresh
    `startup` / `resume` / `clear` / `fork` session that has no compaction
    boundary to carry state across.
    """
    source = payload.get("source")
    if source is None:
        return True
    return isinstance(source, str) and source.strip() == COMPACT_SOURCE


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


def _env_timeout(var: str, default: float) -> float:
    """Subprocess-timeout override from env; falls back to default on a
    missing, non-numeric, or non-positive value.

    The production defaults (1.5s git / 3.0s gh) are tuned against the 8s
    manifest budget for real `git`/`gh`. Under a full local test-suite run,
    however, fork/exec + python-startup contention can push even a trivial
    mock subprocess past those tight bounds, producing a false timeout (empty
    PR section) and a flaky assertion. Tests set these vars high so the
    assertion measures rendering logic, not host load — production behavior is
    unchanged because the defaults are identical to the previous literals.
    """
    raw = os.environ.get(var)
    if raw is None:
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


def _git_branch(cwd: str) -> str:
    # 1.5s leaves headroom under the 8s manifest budget when paired with
    # _active_pr's 3s. Git branch-show is essentially instant; the timeout
    # exists only to bound pathological FS conditions. Shared runner:
    # hooks/_lib/_git.py (issue #1178).
    out = run_git(
        ["-C", cwd, "branch", "--show-current"],
        timeout=_env_timeout("PRAXIS_POSTCOMPACT_GIT_TIMEOUT", 1.5),
        cwd=cwd,
    )
    return (out or "").strip()


def _active_pr(cwd: str, branch: str) -> dict | None:
    """Return {"number": int, "url": str, "title": str} or None.

    Uses `gh pr list --state open --head <branch>` so we resolve a PR even
    when cwd is not the repo root the PR was created from. Fail-open on
    missing gh / no PR / JSON parse error.
    """
    if not branch:
        return None
    # 3.0s gh + 1.5s git + ~0.5s python startup is a 5.0s ceiling under the
    # manifest's `timeout: 8`. Authenticated gh on a fast network responds in
    # <1s; the timeout exists to bound auth-prompt / network-hung paths so the
    # hook never trips Claude Code's hard timeout.
    out = _run(
        [
            "gh", "pr", "list",
            "--state", "open",
            "--head", branch,
            "--json", "number,url,title",
            "--limit", "1",
        ],
        cwd=cwd,
        timeout=_env_timeout("PRAXIS_POSTCOMPACT_GH_TIMEOUT", 3.0),
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
    """Read the praxis strike state for this session. None if absent/empty.

    Reads the host-neutral ~/.praxis/state default (#527), with a read-fallback
    to the pre-#527 ~/.claude/state/praxis location when no PRAXIS_STATE_DIR
    override is set and the new location is absent — so strike state written by
    an older strike-counter still surfaces here.
    """
    bases = [praxis_state_dir()]
    if not os.environ.get("PRAXIS_STATE_DIR"):
        bases.append(legacy_state_dir())
    data = None
    for base in bases:
        path = os.path.join(base, "strikes", f"{session_id}.json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            break
        except (OSError, ValueError, UnicodeDecodeError):
            continue
    if data is None:
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


def build_context(session_id: str, cwd: str) -> str:
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
    lines.append(
        "Injected by the SessionStart(compact) hook once per compaction; "
        "later prompts will not repeat it."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

@fail_open
def main() -> int:
    if os.environ.get(BYPASS_ENV, "").strip() == "1":
        return 0

    payload = read_payload()
    if payload is None:
        return 0
    if not isinstance(payload, dict):
        return 0

    if not is_compact_source(payload):
        return 0

    session_id = _extract_session_id(payload)
    cwd = payload.get("cwd")
    if not session_id:
        return 0
    if not isinstance(cwd, str) or not cwd:
        return 0

    context = build_context(session_id, cwd)

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        },
        sys.stdout,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
