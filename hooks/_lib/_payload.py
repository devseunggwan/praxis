"""Standard stdin-payload reading for praxis hooks (issue #1178).

89 impl.py files hand-rolled the identical entry preamble — `payload =
json.load(sys.stdin)` inside a try/except, a `tool_name` filter, and a
`command` extraction from `tool_input`. The copies drifted three ways:

  - Exception clauses varied (77x `except Exception`, 10x
    `(json.JSONDecodeError, ValueError)`, 2x with `OSError` added) for the
    same fail-open outcome (return 0).
  - 46 sites extracted via the null-UNSAFE `payload.get("tool_input",
    {}).get(...)`, which raises AttributeError on an explicit
    `"tool_input": null` — `@fail_open` swallows it, but the gate silently
    disarms and the error log fills with noise.
  - The tool filter was written as `!= "Bash"`, `not in TARGET_TOOLS`, or
    omitted, at slightly different points in the preamble.

This module pins the preamble in a single place, mirroring the sibling
`_hook_io.py` (issue #470) extraction on the input side. `_hook_io`'s
docstring scope is the decision-EMIT format (stdout shapes), so payload
READING lives in this separate module rather than stretching that contract.

Contract:

  - Any parse failure (malformed JSON, unreadable stream, non-dict top
    level) returns None. Callers translate None to `return 0` — the same
    fail-open outcome every hand-rolled variant produced, whether it caught
    the exception locally or let `@fail_open` do it.
  - Null-safety everywhere: `tool_input` is read as
    `(payload.get("tool_input") or {})`, so `"tool_input": null` degrades to
    an empty command instead of an AttributeError.
  - `command` is always a str: a missing or non-str `command` value yields
    `""` (hand-rolled sites either coerced with `or ""` or crashed into
    `@fail_open` on a non-str; both netted exit 0).

Neither function exits — the caller owns the exit code.
"""
from __future__ import annotations

import json
import sys
from typing import Container, Optional, TextIO


def read_payload(
    tool_names: Optional[Container[str]] = None,
    stream: Optional[TextIO] = None,
) -> Optional[dict]:
    """Read the hook payload from stdin. None = unparseable / filtered out.

    Args:
      tool_names: when given, the payload's `tool_name` must be a member or
        None is returned (the standard tool filter). None = no filtering
        (Stop / UserPromptSubmit hooks, or hooks with bespoke filters).
      stream: injectable input stream for testing; defaults to `sys.stdin`.

    Returns:
      The payload dict, or None on malformed JSON, a non-dict top level, or
      a filtered-out `tool_name`. Callers translate None to `return 0`.
    """
    try:
        payload = json.load(stream if stream is not None else sys.stdin)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if tool_names is not None and payload.get("tool_name") not in tool_names:
        return None
    return payload


def read_bash_payload(
    stream: Optional[TextIO] = None,
) -> Optional[tuple[dict, str]]:
    """Read a Bash-tool payload from stdin. None = not Bash / unparseable.

    Returns:
      `(payload, command)` where `command` is always a str (missing, null,
      or non-str `tool_input.command` yields `""`), or None when the payload
      is unparseable or `tool_name` is not "Bash". Callers translate None to
      `return 0` and keep their own empty-command handling.

    A non-dict `tool_input` yields `""` too. `or {}` alone covers only the
    falsy shapes: a truthy string or list still reaches `.get` and raises,
    which would push the caller into its fail-open path — the gate goes
    silently quiet, which is the class of bug this helper exists to end.
    """
    payload = read_payload(("Bash",), stream)
    if payload is None:
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        command = ""
    return payload, command
