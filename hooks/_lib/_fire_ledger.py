"""Fire-event telemetry: record which hooks fired and their decision (issue #710).

Companion to bypass-telemetry (which logs bypass *events*). This module logs
hook *fires* from the central PreToolUse(Bash) dispatcher (`_dispatch.py`) — the
single point that already runs every member of the hot Bash group and captures
each one's `(exit, stdout, stderr)`. One JSONL line per member per dispatched
tool call.

COVERAGE (issue #710, two tiers):
  RICH   — the dispatched PreToolUse(Bash) group, recorded by
           `record_group_fires` from the dispatcher which captures each member's
           `(rc, stdout, stderr)` → full block/ask/advise/pass + session_id/tool.
  COARSE — every other hook that uses the @fail_open decorator (Stop,
           UserPromptSubmit, PostToolUse, SessionStart, non-Bash PreToolUse),
           recorded by `record_standalone_fire` from `_hook_runtime.fail_open`.
           That wrapper sees only the return code (no stdout/stdin capture — it
           must not interfere with a live hook process), so ask/advise/pass
           collapse to "pass" and session_id/tool are absent. "block" means
           exit-code 2 ONLY: Stop/UserPromptSubmit hooks block via a stdout JSON
           `decision` field while exiting 0, so THEIR blocks are invisible to the
           coarse path and recorded as "pass" (PreToolUse blocks do exit 2 and
           are captured). Treat coarse Block as a lower bound.
CEILING: a hook that does NOT use @fail_open is uninstrumented. The dispatcher
process marks itself (mark_dispatcher_process) so its Bash-group members are not
double-counted by the coarse path.

Record fields (JSONL, one line per hook fire):
  timestamp    UTC ISO-8601
  session_id   from payload (rich only; "" for coarse)
  tool         tool_name from payload (rich only; "" for coarse)
  hook         hook name (manifest `name`)
  role         hook role (manifest `role`)
  decision     "block" | "ask" | "advise" | "pass"
  granularity  "rich" | "coarse"

Storage:
  Default:  ~/.praxis/telemetry/fire-events-YYYY-MM-DD.jsonl  (daily rotation)
  Override: PRAXIS_FIRE_TELEMETRY_FILE (full path, used by tests)
  Opt-out:  PRAXIS_FIRE_TELEMETRY_DISABLE=1 → no-op

Fail-open: any error → silently no-op. Never raises into the dispatcher.
"""
from __future__ import annotations

import json
import os
import stat
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


# Set True in the dispatcher process so fail_open-level recording skips the
# Bash-group members — they are already recorded richly by record_group_fires.
# Process-local: standalone hook processes never import/run the dispatcher.
_DISPATCHER_PROCESS = False


def mark_dispatcher_process() -> None:
    """Mark the current process as the dispatcher (suppresses coarse recording).

    Intentionally one-way and process-lifetime — there is no unmark. Production
    is safe because each hook (and the dispatcher) runs in its own fresh process,
    so the flag never leaks across roles. The only place it must be reset is a
    single-process test harness running both roles (see test helper).
    """
    global _DISPATCHER_PROCESS
    _DISPATCHER_PROCESS = True


def _atomic_append(path: Path, lines: list[str]) -> None:
    """Append `lines` as JSONL with per-line atomic writes; best-effort safe.

    - Per-line `os.write` under O_APPEND: each record (~150-250 B) is far under
      PIPE_BUF (>=4096), so concurrent writers can't tear a line (whole-line
      interleaving is harmless for line-oriented JSONL). A single joined write of
      all N members (~5 KB) WOULD exceed PIPE_BUF and risk torn lines.
    - Regular-file guard + O_NOFOLLOW: the universal fail_open path opens this on
      every hook invocation, so a FIFO/device/symlink at the target (planted, or
      via PRAXIS_FIRE_TELEMETRY_FILE) must not block or misdirect the guard.
    """
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return  # FIFO / device / socket / symlink — refuse to write
    except FileNotFoundError:
        pass  # absent — created as a regular file below
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o644)
    try:
        for line in lines:
            os.write(fd, (line + "\n").encode("utf-8"))
    finally:
        os.close(fd)


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
                "granularity": "rich",
            }, ensure_ascii=False))
        _atomic_append(resolve_path(), lines)
    except Exception:
        pass  # fail-open — never break the dispatcher


def record_standalone_fire(hook: str, role: str, rc: int) -> None:
    """Append a COARSE fire record for a standalone (non-dispatched) hook.

    Called from `_hook_runtime.fail_open`, the universal decorator every hook's
    `main()` runs through — so this extends fire coverage to hooks outside the
    PreToolUse(Bash) dispatch group (Stop, UserPromptSubmit, PostToolUse,
    non-Bash PreToolUse, SessionStart).

    Deliberately NON-INVASIVE: it does not touch the hook's stdin/stdout/stderr
    (capturing them could break a live hook process), so the ONLY block signal it
    sees is the exit code. "block" here means exit-code 2.

    IMPORTANT block-coverage limitation: praxis Stop and UserPromptSubmit hooks
    block by emitting a `{"decision": "block"}` JSON on stdout while exiting 0
    (see _hook_io.py — "the caller owns the exit code"). Those blocks are
    INVISIBLE to this path and are recorded as "pass". PreToolUse standalone
    hooks do exit 2 on block, so their blocks ARE captured. ask/advise likewise
    collapse to "pass" (granularity="coarse"); session_id/tool are absent. The
    Bash group keeps full granularity via record_group_fires; this path is skipped
    inside the dispatcher process to avoid double-counting those members.
    """
    if _disabled() or _DISPATCHER_PROCESS:
        return
    try:
        record = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "session_id": "",
            "tool": "",
            "hook": hook,
            "role": role,
            "decision": DECISION_BLOCK if rc == 2 else DECISION_PASS,
            "granularity": "coarse",
        }
        _atomic_append(resolve_path(), [json.dumps(record, ensure_ascii=False)])
    except Exception:
        pass  # fail-open — never break the hook
