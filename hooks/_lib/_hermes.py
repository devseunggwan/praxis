"""Hermes Agent host adapter: translate Hermes shell-hook payloads to praxis.

Hermes runs praxis through one shell hook (`plugins/hermes/bridge.py`). Hermes
pipes a JSON payload on stdin and reads one JSON object from stdout; praxis
hooks expect Claude Code's payload and decision shapes. This module holds the
translation in both directions and nothing else, so the entry script stays a
thin loop over `_dispatch.run_group`.

Three translations:

  - Tool calls. Hermes tool names and argument shapes differ from Claude's
    (`terminal` / `command` vs `Bash` / `command`, `clarify` / `choices` vs
    `AskUserQuestion` / `options[].label`). `TOOLS` maps each Hermes tool a
    praxis matcher can select to its Claude name and input shape. A Hermes tool
    absent from the map reaches no praxis hook; `mcp__*` names are shared.
  - Transcript. 75 of the manifest's hooks read Claude's JSONL transcript.
    Hermes keeps each session's messages in `state.db`, and writes the
    assistant message carrying a tool call before that call runs, so a gate on
    the call can read the text written just before it. `sync_transcript`
    appends the session's new rows to a Claude-shaped JSONL file.
  - Decisions. A praxis deny becomes `{"action": "block"}`, an ask becomes
    Hermes's human approval (`{"action": "approve"}`), additionalContext and
    advisory stderr become `{"context": ...}`, and a Bash `updatedInput`
    becomes `{"action": "modify"}` on the `terminal` command.

Everything here fails open toward "no transcript" or "no decision": a schema
change in Hermes's private database must cost the transcript-reading gates,
never block a tool call by itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

HOST_ID = "hermes"


def _terminal(args: dict) -> dict:
    out: Dict[str, Any] = {"command": str(args.get("command") or ""), "description": ""}
    if args.get("background"):
        out["run_in_background"] = True
    return out


def _write_file(args: dict) -> dict:
    return {"file_path": str(args.get("path") or ""), "content": str(args.get("content") or "")}


def _patch(args: dict) -> dict:
    return {
        "file_path": str(args.get("path") or ""),
        "old_string": str(args.get("old_string") or ""),
        "new_string": str(args.get("new_string") or ""),
        "replace_all": bool(args.get("replace_all")),
    }


def _read_file(args: dict) -> dict:
    out: Dict[str, Any] = {"file_path": str(args.get("path") or "")}
    for key in ("offset", "limit"):
        if isinstance(args.get(key), int):
            out[key] = args[key]
    return out


def _clarify(args: dict) -> dict:
    questions = []
    for q in args.get("questions") or []:
        if not isinstance(q, dict):
            continue
        questions.append({
            "question": str(q.get("question") or ""),
            "header": "",
            "options": [{"label": str(c), "description": ""} for c in q.get("choices") or []],
            "multiSelect": bool(q.get("multi_select")),
        })
    return {"questions": questions}


def _skill_view(args: dict) -> dict:
    return {"skill": str(args.get("name") or ""), "args": ""}


def delegate_tasks(args: dict) -> List[dict]:
    """One Claude `Agent` input per spawned Hermes task.

    `delegate_task` spawns a batch; Claude spawns each subagent with its own
    `Agent` call. Fan-out gates count those calls, so a batch of N becomes N
    inputs. Control actions (`list` / `steer` / `stop`) spawn nothing.
    """
    if (args.get("action") or "spawn") != "spawn":
        return []
    out = []
    for task in args.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        goal = str(task.get("goal") or "")
        context = str(task.get("context") or "")
        out.append({
            "description": goal[:80],
            "prompt": goal + ("\n\n" + context if context else ""),
            "subagent_type": "general-purpose",
        })
    return out


# Hermes tool -> (Claude tool, input translator). `delegate_task` is handled by
# `claude_calls` because one Hermes call becomes several Claude calls.
TOOLS: Dict[str, Tuple[str, Callable[[dict], dict]]] = {
    "terminal": ("Bash", _terminal),
    "write_file": ("Write", _write_file),
    "patch": ("Edit", _patch),
    "read_file": ("Read", _read_file),
    "clarify": ("AskUserQuestion", _clarify),
    "skill_view": ("Skill", _skill_view),
}


def claude_calls(tool: str, args: dict) -> List[Tuple[str, dict]]:
    """The Claude `(tool_name, tool_input)` calls one Hermes call stands for."""
    if not isinstance(args, dict):
        args = {}
    if tool in TOOLS:
        name, translate = TOOLS[tool]
        if tool == "skill_view" and args.get("file_path"):
            return []  # reading a skill's linked file is not invoking the skill
        return [(name, translate(args))]
    if tool == "delegate_task":
        return [("Agent", task) for task in delegate_tasks(args)]
    if tool.startswith("mcp__"):
        return [(tool, dict(args))]
    return []


# --- transcript ---------------------------------------------------------------

def state_db_path() -> Path:
    home = os.environ.get("HERMES_HOME") or "~/.hermes"
    return Path(home).expanduser() / "state.db"


def _parse_json(raw: Any) -> Any:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _clarify_answer_text(result: Any) -> Optional[str]:
    """Claude's AskUserQuestion result text for a Hermes `clarify` result.

    praxis reads answers as `"<question>"="<answer>"` pairs (the shape Claude
    Code writes), so a picked choice or a typed reply is rendered that way. An
    unanswered or cancelled question carries no pair, as a declined Claude
    question carries none.
    """
    data = _parse_json(result)
    if not isinstance(data, dict):
        return None
    pairs = []
    for r in data.get("responses") or []:
        if isinstance(r, dict) and r.get("status") == "answered" and r.get("user_response") is not None:
            q = json.dumps(str(r.get("question") or ""), ensure_ascii=False)
            a = json.dumps(str(r.get("user_response")), ensure_ascii=False)
            pairs.append(f"{q}={a}")
    if not pairs:
        return None
    return ("User has answered your questions: " + ", ".join(pairs)
            + ". You can now continue with the user's answers in mind.")


def _assistant_event(row: sqlite3.Row) -> Tuple[dict, Dict[str, str]]:
    """Claude assistant event for one Hermes assistant row, plus id -> Hermes tool."""
    blocks: List[dict] = []
    text = row["content"]
    if isinstance(text, str) and text.strip():
        blocks.append({"type": "text", "text": text})
    names: Dict[str, str] = {}
    for call in _parse_json(row["tool_calls"]) or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        tool = str(fn.get("name") or "")
        call_id = str(call.get("id") or "")
        args = _parse_json(fn.get("arguments")) if isinstance(fn.get("arguments"), str) else fn.get("arguments")
        calls = claude_calls(tool, args or {})
        if calls:
            names[call_id] = tool  # only mapped calls get their result rendered
        for i, (name, tool_input) in enumerate(calls):
            blocks.append({"type": "tool_use", "id": call_id if i == 0 else f"{call_id}#{i}",
                           "name": name, "input": tool_input})
    return {"type": "assistant", "message": {"role": "assistant", "content": blocks}}, names


def _tool_event(row: sqlite3.Row, tool: str) -> dict:
    content = row["content"] if isinstance(row["content"], str) else ""
    block: Dict[str, Any] = {"type": "tool_result", "tool_use_id": str(row["tool_call_id"] or "")}
    if tool == "clarify":
        answer = _clarify_answer_text(content)
        if answer is None:
            block.update(content=content, is_error=True)
        else:
            block["content"] = answer
    else:
        block["content"] = content
    return {"type": "user", "message": {"role": "user", "content": [block]}}


def transcript_events(rows: List[sqlite3.Row], names: Dict[str, str]) -> Iterator[dict]:
    """Claude transcript events for Hermes message rows, in order.

    `names` maps tool-call id -> Hermes tool name across calls, so a tool row
    is rendered by the tool that produced it even when its call was synced
    earlier.
    """
    for row in rows:
        role = row["role"]
        if role == "user":
            yield {"type": "user", "message": {"role": "user", "content": row["content"] or ""}}
        elif role == "assistant":
            event, ids = _assistant_event(row)
            names.update(ids)
            if event["message"]["content"]:
                yield event
        elif role == "tool":
            tool = names.get(str(row["tool_call_id"] or ""))
            if tool is not None:  # a result whose call reached no praxis tool has no tool_use
                yield _tool_event(row, tool)


def sync_transcript(session_id: str, out_dir: Path, db_path: Optional[Path] = None) -> Optional[Path]:
    """Append the session's not-yet-synced Hermes messages to its Claude JSONL.

    Returns the transcript path, or None when nothing usable exists (no
    session id, no database, an unknown schema). Incremental: a sidecar keeps
    the last synced row id and the tool-call names seen so far, so each call
    reads only new rows.
    """
    if not session_id:
        return None
    db = db_path or state_db_path()
    if not db.is_file():
        return None
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)[:120]
    digest = hashlib.sha256(session_id.encode()).hexdigest()[:8]
    out = out_dir / f"{safe}-{digest}.jsonl"
    cursor_file = out.with_suffix(".cursor.json")
    cursor: Dict[str, Any] = {}
    if out.is_file() and cursor_file.is_file():
        cursor = _parse_json(cursor_file.read_text(encoding="utf-8")) or {}
    last_id = int(cursor.get("last_id") or 0) if out.is_file() else 0
    names: Dict[str, str] = dict(cursor.get("names") or {})
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, role, content, tool_call_id, tool_calls, tool_name FROM messages "
                "WHERE session_id = ? AND id > ? AND active = 1 ORDER BY id",
                (session_id, last_id),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return out if out.is_file() else None
    if rows or not out.is_file():
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out, "a", encoding="utf-8") as fh:
            for event in transcript_events(rows, names):
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        last_id = rows[-1]["id"] if rows else last_id
        cursor_file.write_text(json.dumps({"last_id": last_id, "names": names}), encoding="utf-8")
    return out


# --- decisions ----------------------------------------------------------------

def parse_decision(stdout: str) -> Tuple[str, str, str, dict]:
    """-> (permissionDecision, reason, additionalContext, updatedInput) of a group's output."""
    data = _parse_json((stdout or "").strip())
    if not isinstance(data, dict):
        return "", "", "", {}
    hso = data.get("hookSpecificOutput") or {}
    if not isinstance(hso, dict):
        return "", "", "", {}
    updated = hso.get("updatedInput")
    return (
        str(hso.get("permissionDecision") or "").lower(),
        str(hso.get("permissionDecisionReason") or ""),
        str(hso.get("additionalContext") or ""),
        updated if isinstance(updated, dict) else {},
    )


def approve_response(asks: List[str]) -> dict:
    """Hermes human-approval request for praxis asks.

    `rule_key` is stable across processes, so an "always" answer keeps
    matching the same gate and reason on later calls.
    """
    message = " | ".join(asks)
    rule_key = "praxis:" + hashlib.sha256(message.encode("utf-8")).hexdigest()[:12]
    return {"action": "approve", "message": f"[praxis] {message}"[:1500], "rule_key": rule_key}
