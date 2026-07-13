#!/usr/bin/env python3
"""Multi-event hook: maintain a session-scoped 'retrospect-active' marker.

Foundation for the issue #666 Stage-3 fence-omission bypass gate. The Stop
hook `hooks/completion-verify/retrospect-mix-check/impl.sh` keys its #666 gate
on this marker — a format-independent signal that a retrospect Stage 3 report
is owed in the current turn. A free-form report (localized header, plain
findings table, no distribution fence) cannot evade the marker, because the
marker is recorded at *skill-invocation* time, not at *report* time. This is
the level the bug lives at: the Stop hook's own identifier checks key on the
agent's output format (`## Retrospect Report` header + distribution fence), so
a report that omits both silently no-ops every downstream gate (incl. Gate-7).

Events
------
PreToolUse(Skill)
    SET the marker when the Skill tool invokes a retrospect skill
    (`tool_input.skill` contains 'retrospect'). Primary, format-independent
    capture point — the model cannot produce a retrospect report without first
    invoking the skill, and the skill name is not something a bypassing report
    can avoid.

UserPromptSubmit
    A new user turn RESETS the marker:
      * SET when the prompt is an explicit retrospect slash-command invocation
        (`/retrospect` or `/praxis:retrospect`), so the marker is armed even
        before the Skill `tool_use` record exists.
      * CLEAR otherwise. This bounds the marker to the active retrospect
        turn(s) and removes the false positive where the user abandons
        retrospect / changes topic and a later unrelated Stop would otherwise
        be blocked.

The Stop hook additionally clears the marker on Stage 4 (`## Actions
Executed`). Between SET and CLEAR the marker means "retrospect invoked this
turn, not yet completed" — exactly when a Stage 3 distribution fence is owed.

Natural-language triggers ("retrospect"/"회고" in prose) deliberately do NOT
SET the marker on UserPromptSubmit (a casual mention must not arm the gate);
the PreToolUse(Skill) path covers natural-language auto-invocation, because
that still routes through the Skill tool.

State file (priority order)
  1. `PRAXIS_RETROSPECT_ACTIVE_FILE` env var (explicit override for tests).
  2. `${TMPDIR:-/tmp}/praxis-retrospect-active-${session_id}.json` — the
     canonical praxis hook session key (same field consumed by
     `session-intent`, `retrospect-mix-check.sh`, `strike-counter.sh`).
  3. `${TMPDIR:-/tmp}/praxis-retrospect-active-${PPID}.json` back-compat
     fallback when no `session_id` is supplied (direct CLI / test usage).

Fail-open everywhere — malformed payloads / unwritable state exit 0 silently.
The hook never blocks; it only records side-effect state for the Stop gate.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path as _Path

_sys_path = str(_Path(__file__).resolve().parent.parent.parent / "_lib")
if _sys_path not in sys.path:
    sys.path.insert(0, _sys_path)
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402

# Explicit retrospect slash-command invocation at the start of the prompt.
# `/retrospect`, `/praxis:retrospect` (the plugin-namespaced form). A trailing
# word boundary keeps `/retrospects-foo` from matching. Leading whitespace is
# tolerated.
_SLASH_INVOKE_RE = re.compile(r"^\s*/(?:praxis:)?retrospect(?![\w-])", re.IGNORECASE)

# Skill-name match: the Skill tool's `skill` input names the skill, e.g.
# `praxis:retrospect` or `retrospect`. Substring match (case-insensitive) is
# sufficient and host-namespace agnostic.
_SKILL_NAME_RE = re.compile(r"retrospect", re.IGNORECASE)


# ---------------------------------------------------------------------------
# State file IO
# ---------------------------------------------------------------------------

def _extract_session_id(payload: dict) -> str | None:
    sid = payload.get("session_id")
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    return None


def resolve_state_path(session_id: str | None = None) -> str:
    """Resolve the marker file path with the documented priority order."""
    explicit = os.environ.get("PRAXIS_RETROSPECT_ACTIVE_FILE", "").strip()
    if explicit:
        return explicit

    # Root-safe: `"/".rstrip("/")` is "", which would yield a *relative*
    # marker path. Fall back to "/" so the path stays absolute and the sh
    # consumer (which computes the same path) agrees.
    tmp = os.environ.get("TMPDIR", "/tmp").rstrip("/") or "/"
    if session_id:
        return os.path.join(tmp, f"praxis-retrospect-active-{session_id}.json")
    return os.path.join(tmp, f"praxis-retrospect-active-{os.getppid()}.json")


def set_marker(path: str, source: str) -> None:
    """Atomically write the marker (existence is the signal; body is a hint)."""
    try:
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=parent or ".", prefix=".retrospect-active-"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"retrospect_active": True, "source": source}, fh)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError:
        # Non-fatal: the gate simply won't arm for this session.
        pass


def clear_marker(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        # Already absent (the common case) or unwritable — both fine.
        pass


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

def _skill_name(payload: dict) -> str:
    """Best-effort extraction of the invoked skill name from a Skill call."""
    ti = payload.get("tool_input")
    if not isinstance(ti, dict):
        return ""
    for key in ("skill", "command", "name"):
        val = ti.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


# Shared silent-pass detection assets (single source of truth with the Stop
# hook — both call the SAME scanner + catalog so detection never diverges).
_LIB_DIR = _Path(__file__).resolve().parent.parent.parent / "_lib"
_SCANNER = _LIB_DIR / "scan-silent-pass.sh"
_CATALOG = _LIB_DIR / "silent-pass-catalog.json"


def resolve_candidates_path(session_id: str | None = None) -> str:
    """Resolve the front-load hint path — the candidates sibling of the marker.

    Mirrors resolve_state_path's location logic with a distinct filename so the
    hint file and the marker are always separate files. Under an explicit marker
    override the override path is opaque, so the sibling is that path plus a
    `.candidates.json` suffix; otherwise it is the TMPDIR
    `praxis-retrospect-candidates-<session>.json` companion of the marker.
    """
    explicit = os.environ.get("PRAXIS_RETROSPECT_ACTIVE_FILE", "").strip()
    if explicit:
        return explicit + ".candidates.json"
    tmp = os.environ.get("TMPDIR", "/tmp").rstrip("/") or "/"
    token = session_id if session_id else str(os.getppid())
    return os.path.join(tmp, f"praxis-retrospect-candidates-{token}.json")


def write_candidate_hints(payload: dict, session_id: str | None) -> None:
    """Best-effort Stage-2 front-load: scan the session-so-far transcript for
    silent-pass conduct and drop a candidate hint file the retrospect skill can
    read before authoring its report.

    Purely proactive — the Stop-hook Gate-9 re-scans independently and remains
    the source of truth. Any failure here is swallowed: the marker (already
    written by the caller) must never be jeopardized by this hint pass.
    """
    try:
        transcript = payload.get("transcript_path")
        if not (isinstance(transcript, str) and transcript and os.path.isfile(transcript)):
            return  # No transcript to scan — skip silently (R1).
        if not (_SCANNER.is_file() and _CATALOG.is_file()):
            return
        proc = subprocess.run(
            ["bash", str(_SCANNER), "--transcript", transcript, "--catalog", str(_CATALOG)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        candidates = []
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and parts[0]:
                candidates.append(
                    {"class_id": parts[0], "severity": parts[1], "evidence": parts[2]}
                )
        try:
            catalog_version = json.loads(_CATALOG.read_text()).get("version")
        except Exception:
            catalog_version = None
        payload_out = {
            "mandatory_candidates": candidates,
            "catalog_version": catalog_version,
        }
        cand_path = resolve_candidates_path(session_id)
        parent = os.path.dirname(cand_path)
        with tempfile.NamedTemporaryFile(
            "w", delete=False, dir=parent or ".", prefix=".retrospect-candidates-"
        ) as fh:
            json.dump(payload_out, fh)
            tmp = fh.name
        os.replace(tmp, cand_path)
    except Exception:
        # Fail-open: front-load is advisory; never break the hook.
        pass


def handle_pre_tool_use(payload: dict) -> int:
    if payload.get("tool_name") != "Skill":
        return 0
    if _SKILL_NAME_RE.search(_skill_name(payload)):
        session_id = _extract_session_id(payload)
        set_marker(resolve_state_path(session_id), "skill")
        write_candidate_hints(payload, session_id)
    return 0


def handle_user_prompt_submit(payload: dict) -> int:
    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        prompt = payload.get("tool_input", {}).get("prompt", "") if isinstance(
            payload.get("tool_input"), dict
        ) else ""
    path = resolve_state_path(_extract_session_id(payload))
    if isinstance(prompt, str) and _SLASH_INVOKE_RE.search(prompt):
        set_marker(path, "slash")
    else:
        # New user turn that is not a retrospect invocation resets the marker.
        clear_marker(path)
    return 0


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def detect_event(payload: dict) -> str:
    explicit = payload.get("hookEventName") or payload.get("hook_event_name")
    if isinstance(explicit, str) and explicit:
        return explicit
    # PreToolUse carries `tool_name`; UserPromptSubmit carries `prompt`.
    # Mirror the sibling session-intent guard ordering for consistency.
    if "prompt" in payload and "tool_name" not in payload:
        return "UserPromptSubmit"
    if "tool_name" in payload:
        return "PreToolUse"
    return ""


@fail_open
def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0

    event = detect_event(payload)
    if event == "PreToolUse":
        return handle_pre_tool_use(payload)
    if event == "UserPromptSubmit":
        return handle_user_prompt_submit(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
