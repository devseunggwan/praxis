"""Fire-event telemetry: record which hooks fired and their decision (issue #710).

Companion to bypass-telemetry (which logs bypass *events*). This module logs
hook *fires* from the central PreToolUse(Bash) dispatcher (`_dispatch.py`) — the
single point that already runs every member of the hot Bash group and captures
each one's `(exit, stdout, stderr)`. One JSONL line per member per dispatched
tool call.

CEILING (issue #710, hot-path MVP): only the dispatched PreToolUse(Bash) group
is instrumented — the group `_dispatch.py` consolidates. Hooks on other events
(Stop, UserPromptSubmit, PostToolUse, SessionStart) and non-Bash PreToolUse
matchers run as standalone processes and are NOT recorded here.
UPGRADE PATH: when another `(event, matcher)` is added to `manifest.json`
`dispatch_groups`, it flows through `run_group` and is recorded automatically.
Universal coverage of standalone hooks would instrument
`_hook_runtime.fail_open` instead — coarser, because that wrapper sees only the
return code (advise/pass collapse, no stdout) and has no payload (no session_id
/ tool).

Record fields (JSONL, one line per hook fire):
  timestamp    UTC ISO-8601
  session_id   from payload
  tool         tool_name from payload
  hook         hook name (manifest `name`)
  role         hook role (manifest `role`)
  decision     "block" | "ask" | "advise" | "pass"

Storage:
  Default:  ~/.praxis/telemetry/fire-events-YYYY-MM-DD.jsonl  (daily rotation)
  Override: PRAXIS_FIRE_TELEMETRY_FILE (full path, used by tests)
  Opt-out:  PRAXIS_FIRE_TELEMETRY_DISABLE=1 → no-op

Fail-open: any error → silently no-op. Never raises into the dispatcher.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DECISION_BLOCK = "block"
DECISION_ASK = "ask"
DECISION_ADVISE = "advise"
DECISION_PASS = "pass"

# Decision markers — kept in sync with _dispatch.py run_group aggregation.
# (The invariant canary planned in issue #712 will pin this pairing.)
_ASK_MARKER = '"permissionDecision": "ask"'
_DENY_MARKER = '"permissionDecision": "deny"'


def classify_decision(rc: int, stdout: str, stderr: str) -> str:
    """Map a member's `(exit, stdout, stderr)` to a fire decision.

    Mirrors `_dispatch.run_group`'s PER-MEMBER decision precedence (this is one
    member's own outcome, not the cross-member aggregate the dispatcher emits):
    deny (exit 2 / deny marker) > ask (ask marker) > advise (any stderr) > pass.
    """
    if rc == 2 or _DENY_MARKER in stdout:
        return DECISION_BLOCK
    if _ASK_MARKER in stdout:
        return DECISION_ASK
    if stderr.strip():
        return DECISION_ADVISE
    return DECISION_PASS


def _disabled() -> bool:
    return os.environ.get("PRAXIS_FIRE_TELEMETRY_DISABLE", "").strip() == "1"


def resolve_path() -> Path:
    """Resolve today's fire-events JSONL path (PRAXIS_FIRE_TELEMETRY_FILE wins)."""
    override = os.environ.get("PRAXIS_FIRE_TELEMETRY_FILE", "").strip()
    if override:
        return Path(override)
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    return Path.home() / ".praxis" / "telemetry" / f"fire-events-{today}.jsonl"


def _extract_payload(payload_raw: str) -> tuple[str, str]:
    """Return `(session_id, tool)` from the raw JSON payload; `("", "")` on error."""
    try:
        data = json.loads(payload_raw)
    except Exception:
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    sid = data.get("session_id")
    tool = data.get("tool_name")
    return (sid if isinstance(sid, str) else ""), (tool if isinstance(tool, str) else "")


def record_group_fires(members, results, payload_raw: str) -> None:
    """Append one fire record per `(member, result)` pair. Fail-open, batched.

    `members`   : list of `(role, name, impl)` from `group_members`.
    `results`   : list of `(rc, stdout, stderr)` from `run_one`, positionally aligned.
    `payload_raw`: the raw hook payload JSON (session_id / tool_name source).

    Batched into ONE file open per dispatched tool call (the dispatcher is the
    hot path — one open for ~N members, not N opens).
    """
    if _disabled():
        return
    try:
        session_id, tool = _extract_payload(payload_raw)
        ts = datetime.now(tz=timezone.utc).isoformat()
        lines: list[str] = []
        for (role, name, _impl), (rc, stdout, stderr) in zip(members, results):
            lines.append(json.dumps({
                "timestamp": ts,
                "session_id": session_id,
                "tool": tool,
                "hook": name,
                "role": role,
                "decision": classify_decision(rc, stdout, stderr),
            }, ensure_ascii=False))
        if not lines:
            return
        path = resolve_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception:
        pass  # fail-open — never break the dispatcher
