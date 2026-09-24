#!/usr/bin/env python3
"""PreToolUse guard: ask before reaching a hook-blocked target through another tool.

Issue #1485. A describe-before-query gate blocked a SQL query on one query
tool. The describe the gate asked for failed on that tool, and the agent then
queried the same table through a second query tool the gate does not watch.
Every existing repeat-block signal is keyed on the same tool, so switching
tools reset all of them.

A hook block is a statement about a target, not about a tool. This hook reads
the session's `permission-rule` denials and, when the pending call uses a
DIFFERENT tool on a target one of those blocked calls named, asks — quoting
the original block so the operator sees what was refused and why.

  1. an earlier call in this session was denied with `permission-rule`;
  2. the pending call's tool family differs from the blocked call's;
  3. both calls name the SAME literal target (see TARGETS);
  4. no call with the pending tool on that target has already run since the
     block (see LIFTING).

All four → `permissionDecision: "ask"`.

TARGETS — a closed list, compared literally after normalization:

  SQL identifiers following FROM / JOIN / INTO / UPDATE / DESCRIBE / TABLE,
  lowercased with quotes stripped, read only from text that executes a query:
  an MCP input's `sql` / `query` / `statement` field, or a Bash command that
  runs a SQL client. A commit body saying "from the" or a file that mentions a
  table is not a query on it. `cat.sch.tbl` does not match `tbl`: guessing
  which qualified name an unqualified one resolves to is how a literal gate
  turns into a fuzzy one.

  The path of a blocked Edit / Write / NotebookEdit. A pending call reaches it
  when its own path equals it, or when it is a Bash command that writes that
  path (a redirect, `tee`, `touch`, `sed -i`, `cp`/`mv`/`rm`, a write-mode
  `open(...)`). Reading the refused file is not a reroute. A path held in a
  shell variable is not followed.

  A blocked Bash command contributes SQL identifiers only, never paths: a Bash
  block is usually about the command's shape (a glob, a flag, a title), and
  the scratch files it mentions are not what was refused.

SAME-TOOL CALLS ARE SILENT. Re-issuing a corrected call on the blocked tool is
the recovery the block asks for; repeat blocks there are `block_message`'s
repeat notice's job. Edit, Write and NotebookEdit count as one tool family:
gates register them under one matcher, so switching among them reaches the
same gate again.

LIFTING. The block is never lifted by a later call on the original tool
succeeding. In the incident the describe the gate asked for *ran* and failed
as an ordinary tool result, so "a later call was not denied" would have lifted
the block one call before the reroute. What lifts it, per (block, new tool)
pair, is a call with that new tool on that target that actually ran — the
operator approved this very ask once. A rejected ask leaves the pair armed.

INDETERMINATE SCAN IS SILENT. Unlike `rejected-mutation-reconsent-gate`, the
reach here is every SQL query and every edit, so failing closed on a long
transcript would ask on routine calls for the few calls the resumable scan
takes to catch up. The cursor makes that window short.

TIER: ask, no bypass marker. Approving the ask is the operator's decision to
take the new route.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _compound import compound_cascade_hint  # type: ignore[import-not-found]  # noqa: E402
from _hook_io import emit_decision  # type: ignore[import-not-found]  # noqa: E402
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402
from _transcript import (  # type: ignore[import-not-found]  # noqa: E402
    HOOK_BLOCK_DENIAL_KIND,
    TranscriptReadError,
    scan_cursor_path,
    scan_transcript_resumable,
)
from block_message import format_block  # type: ignore[import-not-found]  # noqa: E402

_HOOK_NAME = "cross-tool-reroute-gate"

SCAN_MAX_BYTES = 20 * 1024 * 1024
# A block resolves against the tool_use it refused; that record is in the
# same or the previous assistant turn, so a short ring suffices.
RECENT_TOOL_USES = 32
MAX_BLOCKS = 20
MAX_LIFTED = 200
REASON_MAX_CHARS = 400

PATH_FIELDS = {"Edit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path"}

_IDENT = r"[`\"]?[A-Za-z_][\w$]*[`\"]?"
_SQL_TARGET_RE = re.compile(
    rf"(?i)\b(?:from|join|into|update|describe|table)\s+((?:{_IDENT}\.){{0,3}}{_IDENT})"
)
# Words that follow the keywords above without naming a table.
_SQL_NON_TARGETS = frozenset({
    "table", "select", "where", "set", "values", "lateral", "unnest", "if", "only",
})

# Only these carry a query: an MCP input field that holds SQL, or a Bash command
# that runs a SQL client. Prose (commit bodies, docs) and code (`from x import`)
# name tables without querying them.
_QUERY_FIELDS = frozenset({"sql", "query", "statement"})
# A SQL client counts only in command position — line start or after a shell
# separator, past env assignments and wrappers — so a PR body that mentions
# `trino-plugin` or quotes `$ trino` output is not read as running it.
_SQL_CLI_RE = re.compile(
    r"(?m)(?:^|[;&|(])\s*(?:[A-Za-z_]\w*=\S*\s+)*"
    r"(?:(?:timeout\s+\S+|sudo|time|command|exec|env)\s+)*"
    r"(?:trino|presto|psql|mysql|duckdb|sqlite3|clickhouse(?:-client)?|bq|snowsql)"
    # An invocation takes a flag, stdin, a subcommand, or a database file; a
    # markdown table cell (`| trino ...`) takes none of them.
    r"(?=\s+(?:-|<|query\b|\S+\.(?:db|duckdb|sqlite3?)\b))"
)
# Shell forms that write the path right after them.
_BASH_WRITE_PREFIXES = (
    r"(?<![-=])>>?\s*", r"\btee\s+(?:-a\s+)?", r"\btouch\s+(?:-\S+\s+)*",
    r"\b(?:sed|perl)\s+[^|;&\n]*-i\b[^|;&\n]*?",
    r"\b(?:mv|rm|truncate)\s+[^|;&\n]*?",
)
# cp / install / ln write only their last operand; an earlier one is a read.
_BASH_DEST_ONLY_PREFIX = r"\b(?:cp|install|ln)\s+[^|;&\n]*\s"
_SEGMENT_END = r"""["']?(?=\s*(?:$|[|;&\n<>]|\d+>))"""
# `open("<path>", "w")` in an inline script; a bare `open(path)` is a read.
_PY_OPEN_WRITE = r"""open\(\s*["']{path}["']\s*,\s*["'][wax]"""
_PY_IMPORT_RE = re.compile(r"\bfrom\s+[\w.]+\s+import\b")

# The runtime prefixes a hook block with the hook's own command line, which can
# fill the whole excerpt before the hook's message starts.
_RUNTIME_PREFIX_RE = re.compile(r"^PreToolUse:\S+ hook error: \[.*?\]: ", re.DOTALL)

_NEEDLES = (b'"tool_use"', b'"tool_result"')


def bash_command(tool_name: str, tool_input: dict) -> str:
    """The command of a Bash call, or ""."""
    command = tool_input.get("command") if tool_name == "Bash" else None
    return command if isinstance(command, str) else ""


def query_text(tool_name: str, tool_input: dict) -> str:
    """The SQL a call executes, or "" when it executes none (see TARGETS)."""
    if tool_name.startswith("mcp__"):
        return "\n".join(
            value for key, value in tool_input.items()
            if key in _QUERY_FIELDS and isinstance(value, str)
        )
    command = bash_command(tool_name, tool_input)
    return command if _SQL_CLI_RE.search(command) else ""


def sql_targets(text: str) -> set[str]:
    """Normalized SQL identifiers named after a table-position keyword."""
    found = set()
    for match in _SQL_TARGET_RE.finditer(_PY_IMPORT_RE.sub(" ", text)):
        ident = match.group(1).replace("`", "").replace('"', "").lower()
        if ident in _SQL_NON_TARGETS:
            continue
        found.add(ident)
    return found


def tool_family(tool_name: str) -> str:
    """Edit / Write / NotebookEdit are one tool: gates register them together."""
    return "file-edit" if tool_name in PATH_FIELDS else tool_name


def bash_writes(command: str, path: str) -> bool:
    """Whether a Bash command writes `path` (see TARGETS); reads do not count."""
    quoted = r"[\"']?" + re.escape(path) + r"(?![\w./-])"
    if re.search(_PY_OPEN_WRITE.format(path=re.escape(path)), command):
        return True
    if re.search(_BASH_DEST_ONLY_PREFIX + quoted + _SEGMENT_END, command):
        return True
    return any(re.search(prefix + quoted, command) for prefix in _BASH_WRITE_PREFIXES)


def edit_path(tool_name: str, tool_input: dict) -> str:
    """The absolute path an Edit / Write / NotebookEdit writes, or ""."""
    field = PATH_FIELDS.get(tool_name)
    value = tool_input.get(field) if field else None
    return value if isinstance(value, str) and value.startswith("/") else ""


def blocked_targets(tool_name: str, tool_input: dict) -> set[str]:
    """What a blocked call was refused on (see TARGETS)."""
    targets = sql_targets(query_text(tool_name, tool_input))
    path = edit_path(tool_name, tool_input)
    if path:
        targets.add(path)
    return targets


def shared_targets(tool_name: str, tool_input: dict, targets) -> set[str]:
    """The subset of `targets` the given call reaches."""
    path = edit_path(tool_name, tool_input)
    command = bash_command(tool_name, tool_input)
    hits = sql_targets(query_text(tool_name, tool_input)) & set(targets)
    for target in targets:
        if target.startswith("/") and (target == path or (command and bash_writes(command, target))):
            hits.add(target)
    return hits


# ---------------------------------------------------------------------------
# Transcript reduction
# ---------------------------------------------------------------------------


def _new_state() -> dict:
    return {"recent": [], "blocks": [], "armed": {}, "lifted": []}


def _block_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def _note_tool_uses(state: dict, msg: dict) -> None:
    """Park each tool_use in the ring and arm it against earlier blocks."""
    for block in msg.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        use_id, name = block.get("id"), block.get("name")
        tool_input = block.get("input")
        if not isinstance(use_id, str) or not isinstance(name, str):
            continue
        if not isinstance(tool_input, dict):
            tool_input = {}
        state["recent"].append(
            {"id": use_id, "name": name, "targets": sorted(blocked_targets(name, tool_input))}
        )
        pairs = [
            f"{b['id']}|{name}" for b in state["blocks"]
            if tool_family(b["tool"]) != tool_family(name)
            and shared_targets(name, tool_input, b["targets"])
        ]
        if pairs:
            state["armed"][use_id] = pairs
    if len(state["recent"]) > RECENT_TOOL_USES:
        del state["recent"][: len(state["recent"]) - RECENT_TOOL_USES]


def _note_results(state: dict, ev: dict, msg: dict) -> None:
    """Record hook blocks, and lift pairs whose armed call actually ran."""
    denial = ev.get("toolDenialKind")
    for block in msg.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        use_id = block.get("tool_use_id")
        if not isinstance(use_id, str):
            continue
        pairs = state["armed"].pop(use_id, None)
        if pairs and not denial:
            for pair in pairs:
                if pair not in state["lifted"]:
                    state["lifted"].append(pair)
            del state["lifted"][:-MAX_LIFTED]
        if denial != HOOK_BLOCK_DENIAL_KIND or block.get("is_error") is not True:
            continue
        use = next((u for u in reversed(state["recent"]) if u["id"] == use_id), None)
        if use is None or not use["targets"]:
            continue
        state["blocks"].append({
            "id": use_id,
            "tool": use["name"],
            "targets": use["targets"],
            "reason": _RUNTIME_PREFIX_RE.sub("", _block_text(block), count=1)[:REASON_MAX_CHARS],
        })
        del state["blocks"][:-MAX_BLOCKS]


def reduce_event(state: dict, ev: dict) -> None:
    """Fold one transcript record into the state."""
    msg = ev.get("message")
    if not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
        return
    if msg.get("role") == "assistant":
        _note_tool_uses(state, msg)
    elif msg.get("role") == "user":
        _note_results(state, ev, msg)


def find_reroute(state: dict, tool_name: str, tool_input: dict):
    """The newest armed block the pending call reroutes around, or None."""
    for block in reversed(state["blocks"]):
        if tool_family(block["tool"]) == tool_family(tool_name):
            continue
        if f"{block['id']}|{tool_name}" in state["lifted"]:
            continue
        shared = shared_targets(tool_name, tool_input, block["targets"])
        if shared:
            return block, sorted(shared)
    return None


def build_reason(block: dict, shared: list[str], tool_name: str) -> str:
    targets = ", ".join(f"`{t}`" for t in shared)
    excerpt = " ".join(block["reason"].split())
    return format_block(
        rule_name="cross-tool reroute",
        why=(
            f"`{block['tool']}` was blocked on {targets} earlier in this session, "
            f"and this `{tool_name}` call reaches the same target through a tool "
            "that gate does not watch"
        ),
        correct_path=(
            "satisfy the original block on its own terms. If it cannot be "
            "satisfied, stop and tell the user instead of switching tools. "
            "Approve here only if the user has chosen this route. "
            f"Original block: {excerpt}"
        ),
        bypass_env=None,
        reference="issue #1485",
    )


@fail_open
def main() -> int:
    """Hook entry point: read the payload, match earlier blocks, emit the verdict."""
    payload = read_payload()
    if payload is None:
        return 0
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
        return 0

    # Cheapest discriminator first: a call naming no table and no path cannot
    # share a target, so the transcript is never read.
    command = bash_command(tool_name, tool_input)
    if (not sql_targets(query_text(tool_name, tool_input))
            and "/" not in command and not edit_path(tool_name, tool_input)):
        return 0

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return 0
    try:
        state, complete = scan_transcript_resumable(
            transcript_path,
            scan_cursor_path(_HOOK_NAME, payload.get("session_id")),
            _new_state,
            reduce_event,
            needle=_NEEDLES,
            max_bytes=SCAN_MAX_BYTES,
        )
    except TranscriptReadError:
        return 0
    if not complete:
        return 0

    found = find_reroute(state, tool_name, tool_input)
    if found is None:
        return 0
    block, shared = found
    reason = build_reason(block, shared, tool_name)
    if command:
        reason += compound_cascade_hint(command)
    emit_decision("ask", reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
