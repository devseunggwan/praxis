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
    keeps a Claude-shaped JSONL file in step with the session's active rows.
  - Decisions. A praxis deny becomes `{"action": "block"}`, an ask becomes
    Hermes's human approval (`{"action": "approve"}`), additionalContext and
    advisory stderr become `{"context": ...}`, and a Bash `updatedInput`
    becomes `{"action": "modify"}` on the `terminal` command.

Everything here fails open toward "no transcript" or "no decision": a schema
change in Hermes's private database must cost the transcript-reading gates,
never block a tool call by itself.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import tempfile
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

    The batch is read as Hermes's `_normalize_task_list` reads it
    (`tools/delegate_tool_tasks.py`): `tasks` sent as a JSON string is parsed,
    an empty `tasks` counts as none, and then a top-level `goal` (the legacy
    single-goal shape) is a one-task batch. A batch Hermes rejects outright (a
    task that is not an object, or has no goal) spawns nothing. Its batch
    quality checks (too many tasks, placeholder or very short goals) are not
    copied: such a batch still reaches the gates, which can only ask too often.
    """
    if (args.get("action") or "spawn") != "spawn":
        return []
    tasks = args.get("tasks")
    if isinstance(tasks, str):
        tasks = _parse_json(tasks)
        if not isinstance(tasks, list):
            return []  # Hermes rejects the call: nothing is spawned
    if not (isinstance(tasks, list) and tasks):
        goal = args.get("goal")
        tasks = [{"goal": goal, "context": args.get("context")}] if isinstance(goal, str) and goal.strip() else []
    # Hermes rejects the whole call when any task is not an object or has no goal.
    if not all(isinstance(t, dict) and isinstance(t.get("goal"), str) and t["goal"].strip() for t in tasks):
        return []
    out = []
    for task in tasks:
        goal = task["goal"]
        raw_context = task.get("context")
        context = raw_context if isinstance(raw_context, str) else (
            "" if raw_context is None else json.dumps(raw_context, ensure_ascii=False, default=str))
        out.append({
            "description": goal[:80],
            "prompt": goal + ("\n\n" + context if context else ""),
            "subagent_type": "general-purpose",
        })
    return out


# Hermes's V4A patch grammar, byte-identical to `tools/patch_parser.py`
# (`_BEGIN_MARKER`, `_END_MARKER`, `_OP_MARKERS`).
_V4A_BEGIN = re.compile(r'^\*\*\*\s*Begin\s+Patch\s*$')
_V4A_END = re.compile(r'^\*\*\*\s*End\s+Patch\s*$')
_V4A_OPS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("update", re.compile(r'\*\*\*\s*Update\s+File:\s*(.+)')),
    ("add", re.compile(r'\*\*\*\s*Add\s+File:\s*(.+)')),
    ("delete", re.compile(r'\*\*\*\s*Delete\s+File:\s*(.+)')),
    ("move", re.compile(r'\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+)'))]


def v4a_calls(patch: str) -> List[Tuple[str, dict]]:
    """One Claude call per hunk or file a V4A `patch` call touches.

    A V4A patch (`patch` with `mode: "patch"`) names its files in operation
    headers, not in `path`. Lines are read as Hermes's `parse_v4a_patch` reads
    them, including where it starts and stops, and a patch Hermes rejects as a
    parse error (an empty path, an update with no hunk, a move with no
    destination) becomes no call, as Hermes applies none of it. Each hunk of an
    update becomes an `Edit` of its lines; an added file a `Write` of its
    lines; a deleted file, and both paths of a move, an `Edit` with empty
    strings, so gates that read the target path see every file and gates that
    read written content are not shown an empty file.
    """
    lines = [line.removesuffix("\r") for line in patch.split("\n")]
    start, end = -1, len(lines)
    for i, line in enumerate(lines):
        if _V4A_BEGIN.match(line):
            start = i
        elif _V4A_END.match(line):
            end = i
            break
    ops: List[Dict[str, Any]] = []
    op: Optional[Dict[str, Any]] = None
    hunk: Optional[Dict[str, List[str]]] = None
    for line in lines[start + 1:end]:
        hit = next(((kind, m) for kind, rx in _V4A_OPS if (m := rx.match(line))), None)
        if hit:
            kind, m = hit
            op = {"kind": kind, "path": m.group(1).strip(), "hunks": [],
                  "to": m.group(2).strip() if kind == "move" else None}
            ops.append(op)
            hunk = None
            if kind == "add":
                hunk = {"old": [], "new": []}
                op["hunks"].append(hunk)
            elif kind in ("delete", "move"):
                op = None
        elif line.startswith("@@"):
            if op is not None:
                hunk = {"old": [], "new": []}
                op["hunks"].append(hunk)
        elif op is not None and line and line[0] != "\\":
            if hunk is None:
                hunk = {"old": [], "new": []}
                op["hunks"].append(hunk)
            mark, text = (line[0], line[1:]) if line[0] in "+- " else (" ", line)
            if mark != "+":
                hunk["old"].append(text)
            if mark != "-":
                hunk["new"].append(text)
    for o in ops:
        o["hunks"] = [h for h in o["hunks"] if h["old"] or h["new"]]
        if not o["path"] or (o["kind"] == "update" and not o["hunks"]) or (o["kind"] == "move" and not o["to"]):
            return []
    calls: List[Tuple[str, dict]] = []
    for o in ops:
        if o["kind"] == "update":
            calls.extend(("Edit", {"file_path": o["path"], "old_string": "\n".join(h["old"]),
                                   "new_string": "\n".join(h["new"]), "replace_all": False}) for h in o["hunks"])
        elif o["kind"] == "add":
            content = "\n".join(text for h in o["hunks"] for text in h["new"])
            calls.append(("Write", {"file_path": o["path"], "content": content}))
        else:
            calls.extend(("Edit", {"file_path": path, "old_string": "", "new_string": "", "replace_all": False})
                         for path in (o["path"], o["to"]) if path)
    return calls


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
    if tool == "patch" and args.get("mode") == "patch":
        return v4a_calls(str(args.get("patch") or ""))
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


# Hermes stores non-string message content (a user message with an image is a
# list of parts) as this prefix plus JSON: `_CONTENT_JSON_PREFIX` in
# `hermes_state.py`, written by `_encode_content` in `hermes_state_messages.py`.
_CONTENT_JSON_PREFIX = "\x00json:"


def part_text(part: Any) -> str:
    """A part's text, read as `_transcript.read_last_user_record` reads one:
    a string, or a `text` part (typed `text` or untyped)."""
    if isinstance(part, str):
        return part
    if isinstance(part, dict) and part.get("type", "text") == "text" and isinstance(part.get("text"), str):
        return part["text"]
    return ""


def content_text(raw: Any) -> str:
    """The text of one Hermes message content, stored in `state.db` or passed to a hook.

    Decodes the `\\x00json:` form as Hermes's `_decode_content` does (a
    malformed one stays the raw string; a prefixed literal string is
    double-encoded) and keeps the text parts of a parts list, so gates read
    what the user wrote, not the stored JSON. Content with no text part (an
    image-only message, a JSON object, a number) is kept as its JSON, so it is
    never read as an empty message that a gate skips past.
    """
    if isinstance(raw, str):
        if not raw.startswith(_CONTENT_JSON_PREFIX):
            return raw
        try:
            raw = json.loads(raw[len(_CONTENT_JSON_PREFIX):])
        except ValueError:
            return raw
        if isinstance(raw, str):
            return raw
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    parts = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
    text = "\n".join(t for t in map(part_text, parts) if t)
    return text or json.dumps(raw, ensure_ascii=False, default=str)


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


def tool_use_id(call_id: str, index: int) -> str:
    """Claude tool_use id for the `index`-th Claude call one Hermes call stands for.

    The transcript and the PreToolUse payload must agree on it: gates find the
    gated call's own block in the rebuilt transcript by this id.
    """
    return call_id if index == 0 else f"{call_id}#{index}"


def _assistant_event(row: sqlite3.Row) -> Tuple[dict, Dict[str, str]]:
    """Claude assistant event for one Hermes assistant row, plus id -> Hermes tool."""
    blocks: List[dict] = []
    text = content_text(row["content"])
    if text.strip():
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
            blocks.append({"type": "tool_use", "id": tool_use_id(call_id, i),
                           "name": name, "input": tool_input})
    return {"type": "assistant", "message": {"role": "assistant", "content": blocks}}, names


def _tool_event(row: sqlite3.Row, tool: str) -> dict:
    content = content_text(row["content"])
    block: Dict[str, Any] = {"type": "tool_result", "tool_use_id": str(row["tool_call_id"] or "")}
    if tool == "clarify":
        answer = _clarify_answer_text(content)
        if answer is None:
            block.update(content=content, is_error=True)
        else:
            block["content"] = answer
    else:
        block["content"] = content
        if _tool_failed(content):
            block["is_error"] = True
    return {"type": "user", "message": {"role": "user", "content": [block]}}


def _tool_failed(content: str) -> bool:
    """A Hermes tool result that reports a failure, as Claude marks one `is_error`.

    A gate block comes back as `{"error": ...}` with no exit code; a command
    that ran and failed carries a non-zero `exit_code`. Gates that ask whether a
    call actually ran (the merge gate's executed-merge scan) read `is_error`.
    """
    data = _parse_json(content)
    if not isinstance(data, dict):
        return False
    exit_code = data.get("exit_code")
    return bool(data.get("error")) or (type(exit_code) is int and exit_code != 0)


# Hermes's skill-invocation scaffold markers, byte-identical to the builders in
# its `agent/skill_commands.py`.
_SKILL_INVOCATION_PREFIX = "[IMPORTANT: The user has invoked the "
_SINGLE_SKILL_MARKER = "The full skill content is loaded below.]"
_SINGLE_SKILL_INSTRUCTION = "The user has provided the following instruction alongside the skill invocation: "
_RUNTIME_NOTE = "\n\n[Runtime note:"
_BUNDLE_MARKER = " skill bundle,"
_BUNDLE_USER_INSTRUCTION = "\nUser instruction: "
_BUNDLE_FIRST_SKILL_BLOCK = "\n\n[Loaded as part of the "
_SKILL_DIR_LINE = "\n\n[Skill directory: "


def _is_skill_scaffold(content: str) -> bool:
    """Whether a user row is Hermes's expansion, not text that merely starts like it."""
    header = content.split("\n", 1)[0]
    return content.startswith(_SKILL_INVOCATION_PREFIX) and (
        _SINGLE_SKILL_MARKER in header or _BUNDLE_MARKER in header
    )


def _user_message(text: str, meta: bool = False) -> dict:
    event: Dict[str, Any] = {"type": "user", "message": {"role": "user", "content": text}}
    if meta:
        event["isMeta"] = True
    return event


# `display_kind` values Hermes stamps on user rows it wrote itself, not the user:
# a background process finishing (`tools/process_registry_notifications.py`
# PROCESS_COMPLETE_DISPLAY_KIND), a background subagent finishing, and an
# internal notice. `steer` is absent on purpose: it is the user's own
# out-of-band message.
_HOST_NOTIFICATION_KINDS = frozenset({
    "process_complete", "async_delegation_complete", "internal_notification",
})


def _host_notification(row: sqlite3.Row) -> Optional[str]:
    """The row's display_kind when Hermes, not the user, wrote this user row."""
    if "display_kind" not in row.keys():  # older databases have no column
        return None
    kind = row["display_kind"]
    return kind if kind in _HOST_NOTIFICATION_KINDS else None


def _skill_instruction(content: str) -> Optional[str]:
    """The user's instruction in a skill-invocation scaffold, or None for a bare invocation."""
    header = content.split("\n", 1)[0]
    if _BUNDLE_MARKER in header:
        # A bundle states the instruction before the loaded skills; a body may quote the marker.
        marker, stop = _BUNDLE_USER_INSTRUCTION, _BUNDLE_FIRST_SKILL_BLOCK
        end = content.find(stop)
        idx = content.find(marker, 0, end if end >= 0 else len(content))
    elif _SINGLE_SKILL_MARKER in header:
        # A single skill states it last, after the body's skill-directory line: a marker
        # with that line still after it was quoted by the body. The user's text may quote
        # the footer sentence, so the line, not the sentence, is the anchor. A skill
        # loaded without a directory has no anchor; then the last marker is taken, as
        # Hermes's own extractor does.
        marker, stop = _SINGLE_SKILL_INSTRUCTION, _RUNTIME_NOTE
        idx = content.rfind(marker)
        if idx >= 0 and content.find(_SKILL_DIR_LINE, idx) >= 0:
            idx = -1
    else:
        return None
    if idx < 0:
        return None
    return content[idx + len(marker):].split(stop, 1)[0].strip() or None


def _bare_invocation(content: str) -> Optional[str]:
    """The `/<skill>` typed for an invocation with no instruction (the first quoted span names it)."""
    parts = content[len(_SKILL_INVOCATION_PREFIX):].split('"', 2)
    name = parts[1].strip() if len(parts) == 3 else ""
    if not name:
        return None
    return name if name.startswith("/") else f"/{name}"


def _user_events(content: str) -> Iterator[dict]:
    """Claude user events for one Hermes user row.

    Hermes expands a slash-skill invocation into one user message: an activation
    header, the skill body, then the user's instruction (`agent/skill_commands.py`).
    The row becomes the body as an `isMeta` entry, which the gates skip as
    Claude Code's own skill bodies are skipped, then the instruction as plain
    text. Claude Code writes the typed command first, tag-wrapped; the plain text
    last is deliberate, so a reader that does not skip `isMeta` also gets the
    instruction. The instruction is cut out with Hermes's
    own markers, searched only where Hermes writes it (after the body's skill-directory
    line, or before a bundle's first skill) so a body quoting a marker is not read as
    one; with none, the typed text was the bare `/<skill>`.
    """
    if not _is_skill_scaffold(content):
        yield _user_message(content)
        return
    yield _user_message(content, meta=True)
    typed = _skill_instruction(content) or _bare_invocation(content)
    if typed:
        yield _user_message(typed)


def transcript_events(rows: List[sqlite3.Row], names: Dict[str, str],
                      widths: Optional[Dict[str, int]] = None) -> Iterator[dict]:
    """Claude transcript events for Hermes message rows, in order.

    `names` collects tool-call id -> Hermes tool name as assistant rows are
    read, so each tool row is rendered by the tool that produced it. `widths`
    collects Hermes call id -> how many Claude tool_use blocks it was rendered
    as. Passing both back in renders rows that follow ones already rendered,
    exactly as one pass over all of them would.
    """
    if widths is None:
        widths = {}
    for row in rows:
        role = row["role"]
        if role == "user":
            notice = _host_notification(row)
            if notice is not None:
                # Claude Code tags its own notifications with `origin.kind`
                # (#1410), and the gates skip any kind but `human`.
                event = _user_message(content_text(row["content"]))
                event["origin"] = {"kind": notice}
                yield event
                continue
            yield from _user_events(content_text(row["content"]))
        elif role == "assistant":
            event, ids = _assistant_event(row)
            names.update(ids)
            block_ids = {b.get("id") for b in event["message"]["content"] if b.get("type") == "tool_use"}
            for call_id in ids:
                width = 1
                while tool_use_id(call_id, width) in block_ids:
                    width += 1
                widths[call_id] = width
            if event["message"]["content"]:
                yield event
        elif role == "tool":
            call_id = str(row["tool_call_id"] or "")
            tool = names.get(call_id)
            if tool is not None:  # a result whose call reached no praxis tool has no tool_use
                event = _tool_event(row, tool)
                [block] = event["message"]["content"]
                if block.get("is_error"):
                    # One Hermes call failed as a whole: none of the Claude
                    # calls it stands for started, so each gets the error.
                    event["message"]["content"] += [
                        dict(block, tool_use_id=tool_use_id(call_id, i))
                        for i in range(1, widths.get(call_id, 1))]
                yield event


def _is_queued(row: sqlite3.Row) -> bool:
    """A user row Hermes accepted into its busy queue but has not delivered.

    A prompt typed while a turn runs is written at once with
    `display_metadata._queued_prompt` set, then re-placed unmarked when the queue
    drains and the marked row deactivated. Until then the model has not seen it,
    so it is not the last user message of the running turn. Parsed here, not in
    SQL, so an SQLite built without JSON functions reads the same transcript.
    """
    if "display_metadata" not in row.keys():  # older databases have no column
        return False
    meta = _parse_json(row["display_metadata"])
    return isinstance(meta, dict) and meta.get("_queued_prompt") == 1


def _active_rows(session_id: str, db: Path) -> Optional[List[sqlite3.Row]]:
    """The session's active rows, in id order; None when the database is unusable."""
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
            meta = "".join(f", {c}" for c in ("display_metadata", "display_kind") if c in columns)
            return conn.execute(
                f"SELECT id, role, content, tool_call_id, tool_calls, tool_name{meta} FROM messages "
                "WHERE session_id = ? AND active = 1 ORDER BY id",
                (session_id,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None


_SYNC_STATE_VERSION = 1


def _rows_digests(rows: List[sqlite3.Row], last_id: int) -> Tuple[str, str]:
    """-> (digest of the rows up to `last_id`, digest of all rows).

    Every column read goes in, so a row Hermes rewrites in place (a repair, a
    redaction, a queued prompt's metadata) changes the digest even when the
    row count and the highest id stay put.
    """
    h = hashlib.sha256()
    prefix: Optional[str] = None
    for row in rows:
        if prefix is None and row["id"] > last_id:
            prefix = h.hexdigest()
        h.update(repr(tuple(row)).encode("utf-8", "backslashreplace"))
    full = h.hexdigest()
    return prefix or full, full


def _load_sync_state(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    shape = {"version": int, "last_id": int, "digest": str, "ino": int, "size": int,
             "names": dict, "widths": dict}
    if not isinstance(data, dict) or any(type(data.get(k)) is not t for k, t in shape.items()):
        return None
    return data if data["version"] == _SYNC_STATE_VERSION else None


def _save_sync_state(path: Path, state: dict) -> None:
    """Write the sync state; a failure only costs the next call a full rebuild."""
    tmp = ""
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
    except OSError:
        if tmp:
            with contextlib.suppress(OSError):
                os.unlink(tmp)


def _render(rows: List[sqlite3.Row], names: Dict[str, str], widths: Dict[str, int]) -> bytes:
    events = transcript_events([row for row in rows if not _is_queued(row)], names, widths)
    return "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events).encode("utf-8")


def _append(out: Path, rows: List[sqlite3.Row], state: dict) -> Optional[Tuple[int, int]]:
    """Append `rows` (all newer than the last sync) to `out`; -> (inode, size) after.

    None when `out` is not the file the state describes (removed, replaced,
    grown or cut by anything else), so the caller rebuilds. The check and the
    write go through one descriptor, so a file swapped in between is never
    written to; a swap after the write leaves an inode the next call rejects.
    """
    try:
        fd = os.open(out, os.O_WRONLY | os.O_APPEND)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if st.st_ino != state["ino"] or st.st_size != state["size"]:
            return None
        data = memoryview(_render(rows, state["names"], state["widths"]))
        while data:
            data = data[os.write(fd, data):]
        st = os.fstat(fd)
        return st.st_ino, st.st_size
    except OSError:
        return None  # a part-written tail is replaced by the rebuild that follows
    finally:
        os.close(fd)


def _rebuild(out: Path, rows: List[sqlite3.Row], names: Dict[str, str], widths: Dict[str, int]) -> Tuple[int, int]:
    """Write all of `rows` to a temp file and swap it in; -> (inode, size)."""
    fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=out.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(_render(rows, names, widths))
        os.replace(tmp, out)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    st = out.stat()
    return st.st_ino, st.st_size


def sync_transcript(session_id: str, out_dir: Path, db_path: Optional[Path] = None) -> Optional[Path]:
    """Write the session's active Hermes messages as a Claude JSONL; return its path.

    None when nothing usable exists (no session id, no database, an unknown
    schema). A state file beside the transcript records the last row id it
    holds and a digest of the rows up to it. When those rows are unchanged,
    only the newer rows are rendered and appended, so the file stays
    append-only and the gates' resumable scans (`scan_transcript_resumable`)
    pick up where they stopped. Anything else (a row Hermes deactivated or
    rewrote, a missing or foreign state, a file changed underneath) rebuilds
    the whole file and swaps it in with `os.replace`, under a new inode, so a
    removed row leaves the transcript and no reader keeps a stale offset.
    A 492-row session rebuilds in about 10 ms and writes 0.7 MB; an append
    writes only the new rows.
    """
    if not session_id:
        return None
    db = db_path or state_db_path()
    if not db.is_file():
        return None
    rows = _active_rows(session_id, db)
    if rows is None:
        return None
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)[:120]
    digest = hashlib.sha256(session_id.encode()).hexdigest()[:8]
    out = out_dir / f"{safe}-{digest}.jsonl"
    state_path = out_dir / f"{out.name}.state"
    out_dir.mkdir(parents=True, exist_ok=True)
    last_id = rows[-1]["id"] if rows else 0
    # Two calls of one session (parallel tool calls) must not both append the
    # same rows; without the lock the call rebuilds, which is safe unlocked.
    with state_lock(str(state_path)) as locked:
        state = _load_sync_state(state_path) if locked else None
        prefix, full = _rows_digests(rows, state["last_id"] if state else last_id)
        written: Optional[Tuple[int, int]] = None
        if state is not None and prefix == state["digest"]:
            written = _append(out, [row for row in rows if row["id"] > state["last_id"]], state)
        if state is None or written is None:
            state = {"version": _SYNC_STATE_VERSION, "names": {}, "widths": {}}
            written = _rebuild(out, rows, state["names"], state["widths"])
        state.update(last_id=last_id, digest=full, ino=written[0], size=written[1])
        _save_sync_state(state_path, state)
    return out


def session_cwd(session_id: str, db_path: Optional[Path] = None) -> Optional[str]:
    """The directory a command moved this session to, or None when none did.

    Hermes runs a `terminal` call without `workdir` in the session's own cwd
    record, updated after every completed command (`tools/terminal_tool.py`
    `_resolve_command_cwd`), not in its process cwd, which is what the hook
    payload carries. The result of a command that changed directory echoes
    the new one as `"cwd"` (`tools/terminal_tool_result.py`); a call with
    `workdir` is never recorded. The record lives in Hermes's memory for the
    whole process, so every row of the session counts, active or not. A
    restarted process starts without it (a resumed CLI session starts in its
    recorded start directory); a move made before the restart is still read
    here.
    """
    if not session_id:
        return None
    db = db_path or state_db_path()
    if not db.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT role, content, tool_call_id, tool_calls FROM messages "
                "WHERE session_id = ? AND role IN ('assistant', 'tool') ORDER BY id",
                (session_id,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    recorded: Dict[str, bool] = {}  # terminal call id -> its cwd is recorded (no workdir)
    cwd: Optional[str] = None
    for row in rows:
        if row["role"] == "assistant":
            for call in _parse_json(row["tool_calls"]) or []:
                fn = call.get("function") if isinstance(call, dict) else None
                if not isinstance(fn, dict) or fn.get("name") != "terminal":
                    continue
                raw = fn.get("arguments")
                args = _parse_json(raw) if isinstance(raw, str) else raw
                call_id = str(call.get("id") or "")
                if call_id:  # an id-less call cannot be paired with its result
                    recorded[call_id] = not (isinstance(args, dict) and args.get("workdir"))
        elif row["tool_call_id"] and recorded.get(str(row["tool_call_id"])):
            data = _parse_json(row["content"])
            moved = data.get("cwd") if isinstance(data, dict) else None
            if isinstance(moved, str) and moved.strip():
                cwd = moved
    return cwd


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


# Gates whose "always" answer may cover later calls of the same category.
# Opt-in: most asks are per-action consent (a merge, an external write, a
# public mutation in a read-only session), and a gate added later must not
# start sharing one permanent answer across calls by default. Every other
# gate's ask gets a key that changes with the call. The advisory nudges here
# ask about how a call or question is shaped, not for consent to an action,
# so one "always" may retire the nudge. `destructive-bash-guard` stays per-call:
# its strict-mode ask is consent to a destructive command.
GROUPED_APPROVAL_HOOKS = frozenset({
    "commit-title-length-check",
    "cwd-relative-exec-advisory",
    "menu-mutation-tier-advisory",
    "output-block-falsify-advisory",
    "side-effect-scan",
})

_CATEGORY_TAG = re.compile(r"\[([a-z][a-z0-9-]*)\]")


def approval_rule_key(asks: List[Tuple[str, str]], call: Optional[dict] = None) -> str:
    """Hermes "always" grain for a set of `(hook, reason)` asks.

    Hermes remembers an "always" answer by `rule_key`. A key hashed from the
    reason never matches again, because reasons quote the call (commit title,
    repo, character count), so every call prompted anew. The key is the gate
    plus the bracketed category tags its reason carries (`[git-push]`,
    `[prod]`): one "always" covers that gate and category, and a category the
    user has not seen still asks. An ask from any gate outside
    `GROUPED_APPROVAL_HOOKS`, or from an unknown gate, makes the whole key a
    hash of the reasons and the tool call's arguments, since such a gate's
    reason alone can repeat across calls.
    """
    hooks = [hook for hook, _ in asks]
    if not all(hook in GROUPED_APPROVAL_HOOKS for hook in hooks):
        material = json.dumps([asks, call], sort_keys=True, ensure_ascii=False, default=str)
        return "praxis:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
    grains = set()
    for hook, reason in asks:
        tags = sorted(set(_CATEGORY_TAG.findall(reason)))
        grains.add(hook + "".join(f"[{tag}]" for tag in tags))
    return "praxis:" + "+".join(sorted(grains))


def approve_response(asks: List[Tuple[str, str]], call: Optional[dict] = None) -> dict:
    """Hermes human-approval request for praxis `(hook, reason)` asks on one tool call.

    Per-call gates go first so the message cap never cuts their reason off.
    """
    asks = sorted(asks, key=lambda ask: ask[0] in GROUPED_APPROVAL_HOOKS)
    message = " | ".join(f"[praxis:{hook}] {reason}" if hook else reason for hook, reason in asks)
    return {"action": "approve", "message": f"[praxis] {message}"[:1500],
            "rule_key": approval_rule_key(asks, call)}


# --- asks: agent self-check vs human approval ----------------------------------

# A praxis `ask` tells the agent to stop and re-check the call against the
# gate's checklist; it is not a question for the human. Only these gates ask
# for the human's consent, and only their asks become a Hermes approval.
HUMAN_APPROVAL_HOOKS = frozenset({
    "fan-out-scope-gate",
    "pre-merge-approval-gate",
})

SELF_CHECK_TTL_SEC = 3600

try:
    from _state_lock import state_lock  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - `_lib` not on sys.path
    # Same degradation `_fire_ledger` applies: without the lock the record is
    # racy, never a blocked call.
    @contextlib.contextmanager
    def state_lock(path: str, timeout: Optional[float] = None) -> Iterator[bool]:  # type: ignore[misc]
        yield False


def split_asks(asks: List[Tuple[str, str]]) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """-> (asks for the human's consent, asks for the agent's self-check)."""
    human = [ask for ask in asks if ask[0] in HUMAN_APPROVAL_HOOKS]
    return human, [ask for ask in asks if ask[0] not in HUMAN_APPROVAL_HOOKS]


def self_check_key(tool: str, args: dict) -> str:
    material = json.dumps([tool, args], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _self_check_file(state_dir: Path, session_id: str) -> Path:
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id) or "no-session"
    return state_dir / f"{name}.json"


def take_self_check(state_dir: Path, session_id: str, key: str, now: float) -> bool:
    """Record a self-check for this call, or consume one already recorded.

    -> True when the same call was already sent back for a self-check in this
    session: the agent has seen the checklist and re-issued it, so it passes.
    -> False on the first sight: the call is recorded and must be blocked.

    The read-modify-write runs under `state_lock`: Hermes can run tool calls in
    parallel, and two identical re-issues must not both consume one record.
    """
    path = _self_check_file(state_dir, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with state_lock(str(path)):
        try:
            seen = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            seen = {}
        if not isinstance(seen, dict):
            seen = {}
        seen = {k: t for k, t in seen.items() if isinstance(t, (int, float)) and now - t < SELF_CHECK_TTL_SEC}
        passed = seen.pop(key, None) is not None
        if not passed:
            seen[key] = now
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=path.name,
                                         suffix=".tmp", delete=False) as tmp:
            tmp.write(json.dumps(seen))
        os.replace(tmp.name, path)
    return passed


SELF_CHECK_REASON_CAP = 1500


def self_check_response(asks: List[Tuple[str, str]]) -> dict:
    """Send the call back to the agent with the gates' checklists instead of asking the human.

    Each reason is capped on its own, so a long checklist never pushes a later
    gate's checklist out of the message.
    """
    reasons = "\n\n".join(
        (f"[praxis:{hook}] {reason}" if hook else reason)[:SELF_CHECK_REASON_CAP] for hook, reason in asks)
    message = (
        "[praxis self-check] Not run yet. Re-check this call against the points below. "
        "If they hold, re-issue the same call unchanged (or with the gate's ack marker) and it runs; "
        "if not, change the call. This is a check for you, not a question for the user.\n\n" + reasons
    )
    return {"action": "block", "message": message}
