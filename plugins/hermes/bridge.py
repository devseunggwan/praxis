#!/usr/bin/env python3
"""Hermes Agent shell hook that runs praxis hooks registered for the `hermes` host.

Register it in Hermes's `config.yaml` (`plugins/hermes/hooks.yaml` is the
generated block). Hermes pipes one event payload on stdin; this script
translates it to the Claude Code payload praxis hooks read, runs each matching
manifest group through praxis's own dispatcher with `host="hermes"`, and prints
one Hermes response object:

  pre_tool_call -> PreToolUse groups     block / approve / modify / context
  pre_llm_call  -> UserPromptSubmit      context

Which hooks run is decided by `hooks/manifest.json` (`hosts`), not here.
Internal errors print `{}`; Hermes's `fail_closed: true` on the entry turns a
crash or timeout of this process into a block.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PRAXIS_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PRAXIS_ROOT / "hooks" / "_lib"))
os.environ.setdefault("CLAUDE_PLUGIN_ROOT", str(PRAXIS_ROOT))

import _hermes  # noqa: E402
from _dispatch import run_group  # noqa: E402
from _paths import praxis_cache_dir  # noqa: E402

MANIFEST = PRAXIS_ROOT / "hooks" / "manifest.json"


def _emit(obj: dict) -> int:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    return 0


def _groups(event: str, tool: Optional[str]) -> List[Optional[str]]:
    """Matchers of the manifest groups this event + tool selects, manifest order.

    Host filtering happens inside `run_group`; a group with no `hermes` member
    runs nothing.
    """
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    out: List[Optional[str]] = []
    for hook in manifest.get("hooks", []):
        if hook.get("event") != event:
            continue
        matcher = hook.get("matcher")
        if tool is not None and not (matcher and re.fullmatch(matcher, tool)):
            continue
        if matcher not in out:
            out.append(matcher)
    return out


def _run(event: str, matcher: Optional[str], payload: dict) -> Tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = run_group(event, matcher, json.dumps(payload), _hermes.HOST_ID)
    return rc, out.getvalue(), err.getvalue()


def _transcript(session_id: str) -> str:
    try:
        path = _hermes.sync_transcript(session_id, Path(praxis_cache_dir()) / "hermes-transcripts")
    except OSError:
        path = None
    return str(path) if path else ""


def _base(hermes: dict, event: str) -> dict:
    session_id = str(hermes.get("session_id") or "")
    return {
        "hook_event_name": event,
        "session_id": session_id,
        "transcript_path": _transcript(session_id),
        "cwd": str(hermes.get("cwd") or os.getcwd()),
        "permission_mode": "default",
    }


def pre_tool_call(hermes: dict) -> dict:
    tool = str(hermes.get("tool_name") or "")
    args = hermes.get("tool_input") or {}
    calls = _hermes.claude_calls(tool, args)
    if not calls:
        return {}
    extra = hermes.get("extra") or {}
    base = _base(hermes, "PreToolUse")
    if tool == "terminal" and args.get("workdir"):
        base["cwd"] = os.path.expanduser(str(args["workdir"]))
    if os.path.isdir(base["cwd"]):
        os.chdir(base["cwd"])  # gates read git state relative to the process cwd
    asks: List[str] = []
    contexts: List[str] = []
    command_rewrite: Optional[str] = None
    for i, (claude_tool, claude_input) in enumerate(calls):
        payload = dict(base, tool_name=claude_tool, tool_input=claude_input,
                       tool_use_id=f"{extra.get('tool_call_id') or 'hermes'}#{i}")
        for matcher in _groups("PreToolUse", claude_tool):
            rc, out, err = _run("PreToolUse", matcher, payload)
            decision, reason, context, updated = _hermes.parse_decision(out)
            if rc == 2 or decision == "deny":
                message = reason or err.strip()[:800] or "Blocked by a praxis gate."
                return {"action": "block", "message": message}
            if decision == "ask":
                asks.append(reason or "requires confirmation")
            if context:
                contexts.append(context)
            elif err.strip() and rc == 0 and not decision:
                contexts.append(err.strip()[:1200])  # advisories print to stderr at exit 0
            if claude_tool == "Bash" and isinstance(updated.get("command"), str):
                command_rewrite = updated["command"]
                payload["tool_input"] = dict(claude_input, command=command_rewrite)
    if asks:
        return _hermes.approve_response(asks)
    response: Dict[str, object] = {}
    if command_rewrite is not None and command_rewrite != args.get("command"):
        response.update(action="modify", args={"command": command_rewrite})
    if contexts:
        response["context"] = "[praxis advisory] " + "\n\n".join(contexts)[:3000]
    return response


def pre_llm_call(hermes: dict) -> dict:
    extra = hermes.get("extra") or {}
    prompt = extra.get("user_message")
    if not isinstance(prompt, str):
        return {}
    payload = dict(_base(hermes, "UserPromptSubmit"), prompt=prompt)
    contexts: List[str] = []
    for matcher in _groups("UserPromptSubmit", None):
        rc, out, err = _run("UserPromptSubmit", matcher, payload)
        _decision, reason, context, _updated = _hermes.parse_decision(out)
        # Hermes cannot reject a prompt from here; a praxis block reaches the
        # model as context instead of being dropped.
        for text in (reason, context, err.strip() if rc == 0 else ""):
            if text:
                contexts.append(text[:1200])
    return {"context": "[praxis] " + "\n\n".join(contexts)[:3000]} if contexts else {}


HANDLERS = {"pre_tool_call": pre_tool_call, "pre_llm_call": pre_llm_call}

# Hermes's plugin layer blocks a call whose hook outlives
# `plugins.hook_callback_timeout` (default 30 s) regardless of `fail_closed`,
# so the entry's own timeout stays under it.
HOOK_TIMEOUT_SEC = 25


def _hermes_matcher() -> str:
    """Hermes tools whose Claude counterpart has at least one `hermes` PreToolUse hook."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    matchers = [h.get("matcher") or "" for h in manifest.get("hooks", [])
                if h.get("event") == "PreToolUse"
                and (h.get("hosts") is None or _hermes.HOST_ID in h["hosts"])]
    probes = {tool: name for tool, (name, _t) in _hermes.TOOLS.items()}
    probes.update({"delegate_task": "Agent", "mcp__.*": "mcp__server__tool"})
    tools = [tool for tool, name in probes.items()
             if any(m and re.fullmatch(m, name) for m in matchers)]
    return "^(?:" + "|".join(tools) + ")$"


def print_config() -> int:
    """Print the `hooks:` entries for Hermes's config.yaml, pointing at this clone."""
    command = str(Path(__file__).resolve())
    home = str(Path.home())
    if command.startswith(home + os.sep):
        command = "~" + command[len(home):]
    sys.stdout.write(
        "hooks:\n"
        "  pre_tool_call:\n"
        f"    - matcher: '{_hermes_matcher()}'\n"
        f"      command: {command}\n"
        f"      timeout: {HOOK_TIMEOUT_SEC}\n"
        "      fail_closed: true\n"
        "  pre_llm_call:\n"
        f"    - command: {command}\n"
        f"      timeout: {HOOK_TIMEOUT_SEC}\n"
    )
    return 0


def main() -> int:
    if sys.argv[1:] == ["--print-config"]:
        return print_config()
    try:
        hermes = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return _emit({})
    handler = HANDLERS.get(str(hermes.get("hook_event_name") or ""))
    if handler is None or not isinstance(hermes, dict):
        return _emit({})
    return _emit(handler(hermes))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # a parseable no-op; Hermes logs stderr
        sys.stderr.write(f"praxis hermes bridge internal error: {exc!r}\n")
        sys.stdout.write("{}\n")
        sys.exit(0)
