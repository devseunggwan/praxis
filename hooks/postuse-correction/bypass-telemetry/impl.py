#!/usr/bin/env python3
"""PostToolUse observe-only hook: log bypass-env usage to a JSONL telemetry file.

Issue #441 — Phase 1 (observe only; Phase 2 review CLI and Phase 3 HTTP
forwarding are deferred).

This hook fires after every Bash tool call and checks whether a praxis/Claude
hook bypass env var was active at the time.  When one is detected it appends a
single JSON record to a daily JSONL file.  It NEVER blocks (always exits 0).

Bypass env var families (both must be detected):
  - ``CLAUDE_HOOK_BYPASS_*``  e.g. CLAUDE_HOOK_BYPASS_SCIOMC_GATE
  - ``PRAXIS_*BYPASS*``       e.g. PRAXIS_MOMENTUM_BYPASS, PRAXIS_GH_JSON_BYPASS

Detection sources (union of both):
  1. ``tool_input.command`` — parse leading ``NAME=value`` inline assignments
     (the common ``CLAUDE_HOOK_BYPASS_X=1 git ...`` inline-prefix form).
  2. ``os.environ`` scan — catch vars already present in the process environment
     (Claude Code propagates inline-prefix env into the hook process).

Bypass-name pattern: name starts with ``CLAUDE_HOOK_`` or ``PRAXIS_`` AND
contains ``BYPASS``.  Only vars whose value is truthy (non-empty, not "0")
are counted.

Log record fields (JSONL, one line per bypass event):
  timestamp         UTC ISO-8601 (microsecond precision)
  session_id        from hook payload
  tool              tool_name from payload
  bypass_env_vars   list of detected bypass var *names* (values never stored)
  tool_input        first 200 chars of command/summary (privacy truncation)
  tool_result_status  "ok" | "error" derived from tool_response

Storage:
  Default:  ~/.praxis/telemetry/bypass-events-YYYY-MM-DD.jsonl  (daily rotation)
  Override: PRAXIS_BYPASS_TELEMETRY_FILE (full path, used by tests)
  Opt-out:  PRAXIS_BYPASS_TELEMETRY_DISABLE=1 → no-op

Fail-open: any unwritable telemetry dir or file → silently exit 0.
Malformed stdin → silently exit 0.
No bypass detected → write nothing, exit 0.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _fire_ledger import prune_telemetry, resolve_telemetry_dir  # type: ignore[import-not-found]  # noqa: E402


# ---------------------------------------------------------------------------
# Bypass detection
# ---------------------------------------------------------------------------

# Name must start with CLAUDE_HOOK_ or PRAXIS_, and contain BYPASS.
# Explicitly exclude this hook's own control vars (PRAXIS_BYPASS_TELEMETRY_*)
# which happen to match the pattern but are not bypass flags.
_BYPASS_NAME_RE = re.compile(r"^(?:CLAUDE_HOOK_|PRAXIS_).*BYPASS")
_SELF_VAR_PREFIX = "PRAXIS_BYPASS_TELEMETRY_"

# Inline leading env assignment: NAME=value (value may be quoted or unquoted)
# Matches `NAME=1`, `NAME=true`, `NAME="yes"`, `NAME=''` etc. at the start of
# the command string (possibly preceded by other assignments).
_INLINE_ENV_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)=(\"[^\"]*\"|'[^']*'|[^\s]*)")


def _is_bypass_name(name: str) -> bool:
    # Exclude this hook's own meta-vars from the bypass scan.
    if name.startswith(_SELF_VAR_PREFIX):
        return False
    return bool(_BYPASS_NAME_RE.match(name))


# Non-empty values that shell convention treats as disabled — not recorded.
_FALSY_VALUES = frozenset({"0", "false", "no", "off"})


def _is_truthy_value(raw: str) -> bool:
    """Return True if the value denotes an active (enabled) bypass.

    Non-empty and not one of the conventional falsy literals
    (``0``/``false``/``no``/``off``, matched case-insensitively).
    """
    # Strip surrounding quotes from inline assignment values
    v = raw.strip("\"'")
    return bool(v) and v.lower() not in _FALSY_VALUES


def _parse_inline_env(command: str) -> dict[str, str]:
    """Extract leading ``NAME=value`` assignments from a shell command string.

    Stops at the first token that is not an assignment.
    Returns a dict of {name: raw_value} (including surrounding quotes).
    """
    result: dict[str, str] = {}
    tail = command.lstrip()
    while tail:
        m = _INLINE_ENV_RE.match(tail)
        if not m:
            break
        result[m.group(1)] = m.group(2)
        tail = tail[m.end():].lstrip()
    return result


def _redact_inline_env_values(command: str) -> str:
    """Return the command with ALL leading ``NAME=value`` values replaced by <redacted>.

    Any leading env assignment — bypass or non-bypass — may contain a secret
    (e.g. ``AWS_SECRET_ACCESS_KEY=…`` preceding a bypass flag).  Redacting all
    leading assignment values is the safest default; only the var names are kept.
    """
    redacted_parts: list[str] = []
    tail = command.lstrip()
    consumed = len(command) - len(tail)
    if consumed:
        redacted_parts.append(command[:consumed])

    while tail:
        m = _INLINE_ENV_RE.match(tail)
        if not m:
            break
        name = m.group(1)
        redacted_parts.append(f"{name}=<redacted>")
        tail = tail[m.end():]
        # Preserve inter-token whitespace
        stripped = tail.lstrip()
        ws = tail[: len(tail) - len(stripped)]
        if ws:
            redacted_parts.append(ws)
        tail = stripped

    redacted_parts.append(tail)
    return "".join(redacted_parts)


def detect_bypass_vars(command: str | None) -> list[str]:
    """Return the sorted list of truthy bypass var *names* detected.

    Combines two sources:
    1. Inline leading env assignments parsed from ``command``.
    2. ``os.environ`` scan for names matching the bypass pattern.

    Values are checked for truthiness but never included in the returned list.
    """
    detected: set[str] = set()

    # Source 1: inline prefix parse
    if command:
        for name, raw_val in _parse_inline_env(command).items():
            if _is_bypass_name(name) and _is_truthy_value(raw_val):
                detected.add(name)

    # Source 2: os.environ scan (backstop — Claude Code propagates inline vars)
    for name, val in os.environ.items():
        if _is_bypass_name(name) and _is_truthy_value(val):
            detected.add(name)

    return sorted(detected)


# ---------------------------------------------------------------------------
# tool_response → status derivation
# ---------------------------------------------------------------------------


def derive_status(tool_response: object) -> str:
    """Return "ok" or "error" from the PostToolUse tool_response payload.

    Claude Code's Bash tool_response is a dict with fields like:
      exit       — integer exit code (0 = ok)
      interrupted — bool (False = completed normally)
      output / stdout / stderr — string content (not inspected here)

    For non-dict responses (e.g., the string "success" used in some test
    fixtures) we default to "ok".
    """
    if not isinstance(tool_response, dict):
        return "ok"

    # Check explicit exit code
    exit_code = tool_response.get("exit")
    if exit_code is not None:
        try:
            return "ok" if int(exit_code) == 0 else "error"
        except (TypeError, ValueError):
            pass

    # interrupted=True means the tool was killed / did not complete normally
    interrupted = tool_response.get("interrupted")
    if interrupted is True:
        return "error"

    # isError is used by some tool families (e.g., MCP tools)
    is_error = tool_response.get("isError")
    if is_error is True:
        return "error"

    return "ok"


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def resolve_telemetry_path() -> Path:
    """Resolve the target JSONL path for today's bypass events.

    Shares `_fire_ledger.resolve_telemetry_dir()` so bypass-events and
    fire-events always land in the same directory (issue #934). `bypass-review
    fire-rate` joins both families out of a single `telemetry_dir`, so letting
    only one of them divert to a dev checkout would corrupt both sides of that
    report: the default view would mix production fires with development
    bypasses, and `--dir <dev>` would show fires with no bypasses at all.

    """
    override = os.environ.get("PRAXIS_BYPASS_TELEMETRY_FILE", "").strip()
    if override:
        return Path(override)

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    return resolve_telemetry_dir() / f"bypass-events-{today}.jsonl"


def append_record(record: dict) -> None:
    """Append one JSON line to the telemetry file.  Fail-open: any I/O error
    is silently swallowed — this hook must never block."""
    path = resolve_telemetry_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Same day-rollover trigger the fire ledger uses (#1078): today's file
        # not existing yet is the first write of the day, and the only moment
        # worth paying for a directory sweep. `bypass-events-` is one of the
        # retention prefixes, and this is the only path that writes it — the
        # fire ledger's own sweep never runs when fire telemetry is disabled.
        first_write_of_the_day = not path.exists()
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        return  # fail-open — never block the tool call
    if first_write_of_the_day:
        try:
            prune_telemetry(path.parent)
        except Exception:  # noqa: BLE001
            pass  # housekeeping never breaks the write that triggered it


# ---------------------------------------------------------------------------
# Main hook logic
# ---------------------------------------------------------------------------


def _extract_session_id(payload: dict) -> str:
    sid = payload.get("session_id")
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    return ""


def run(payload: dict) -> None:
    tool_name = (payload.get("tool_name") or "").strip()
    tool_input = payload.get("tool_input") or {}
    command: str | None = tool_input.get("command") if isinstance(tool_input, dict) else None

    bypass_vars = detect_bypass_vars(command)
    if not bypass_vars:
        return  # no bypass active — write nothing

    tool_response = payload.get("tool_response")
    status = derive_status(tool_response)

    # Redact ALL inline leading env var values, then truncate to 200 chars.
    # Any leading NAME=value may contain a secret (e.g. AWS_SECRET_ACCESS_KEY).
    tool_input_summary = (_redact_inline_env_values(command) if command else "")[:200]

    record = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "session_id": _extract_session_id(payload),
        "tool": tool_name,
        "bypass_env_vars": bypass_vars,
        "tool_input": tool_input_summary,
        "tool_result_status": status,
    }
    append_record(record)


@fail_open
def main() -> int:
    # Opt-out gate
    if os.environ.get("PRAXIS_BYPASS_TELEMETRY_DISABLE", "").strip() == "1":
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # malformed stdin — fail-open

    if not isinstance(payload, dict):
        return 0

    run(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
