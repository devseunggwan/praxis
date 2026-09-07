#!/usr/bin/env python3
"""PreToolUse(Edit|Write) advisory: an Edit or Write that targets a Claude
Code settings file — the file that carries `permissions`, `hooks`, `env` and
`disableAllHooks` — is announced before it runs.

Issue #1337 (item 3), closing the follow-up-write lane of
`docs/hook/RULE-BACKSTOP-GAPS.md` gap #4: when a hook blocks a call, the agent
may relay the gate's own `Bypass (if truly needed): …` line and nothing else
(ETHOS.md principle 5). The route it must not originate is "add a permission
rule / edit `.claude/settings.json`", and the `Write` that follows such a
menu was measured silent on every `Edit|Write` hook — `protected-paths-guard`
keys on credential-shaped names only. This hook is the backstop for that one
tool call. The prose and menu lanes of the same gap stay unhooked (#1009).

Detection model — path first, content second:

  • **Settings path** — basename `settings.json` or `settings.local.json`
    whose parent component is `.claude` (project `.claude/settings.json`,
    `.claude/settings.local.json`, user `~/.claude/settings.json`), or
    basename `managed-settings.json` anywhere (the managed-policy file the
    settings reference documents under `/etc/claude-code/` and its macOS /
    Windows equivalents). Component-exact: `my.claude/settings.json` and
    `.claude/settings.json.bak` do not match.
  • **Widening shape** — the text being written (`content` for `Write`,
    `new_string` for `Edit`) is scanned for the keys and tokens that change
    what hooks and permissions do: `"permissions"`, `"allow"`, `"deny"`,
    `"ask"`, `"hooks"`, `"disableAllHooks"`, `"env"`, a permission-rule literal
    (`Tool(pattern)`), or a `PRAXIS_` variable name. The shape names the
    reason line; it never decides whether the hook fires. A settings write
    that carries none of them (a theme change, a model pin) still gets the
    advisory, because the path alone is the surface gap #4 measured.

Skip rules:

  • Test fixtures — a `fixtures`, `__fixtures__`, `test-data`, `testdata`
    or `test_data` path component (shared with `protected-paths-guard`).
  • Scratch — an absolute path under `/tmp/` or `/private/tmp/` after
    lexical normalization; relative paths and `/tmp/../x` are not scratch.
  • Self-edit — a path inside `CLAUDE_PLUGIN_ROOT` (praxis' own checkout may
    carry a `.claude/settings.json` for its contributors).

Modes: advisory by default (stderr + `additionalContext`, exit 0);
`PRAXIS_SETTINGS_PATH_STRICT=1` blocks (exit 2);
`PRAXIS_HOOK_BYPASS_SETTINGS_PATH=1` is silent.

Output channels (issue #874): stderr carries the text so `_fire_ledger`
classifies the fire as `advise` and the dispatcher forwards it; stdout carries
the same text as `hookSpecificOutput.additionalContext`, the one exit-0
PreToolUse channel that reaches the model. The point of this hook is that the
agent answers the authorship question *in the response the user reads*, so
the model must see the text, not only the terminal.

Fail-open contract: malformed stdin, a non-Edit/Write tool, an empty
`file_path`, or any uncaught exception → exit 0 silently.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path, PurePosixPath
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _path_scope import (  # type: ignore[import-not-found]  # noqa: E402
    under_absolute_prefix,
)
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402

TARGET_TOOLS = frozenset({"Edit", "Write"})

_SETTINGS_BASENAMES = frozenset({"settings.json", "settings.local.json"})
_SETTINGS_PARENT = ".claude"
_MANAGED_BASENAME = "managed-settings.json"

_FIXTURE_DIRECTORIES = frozenset({
    "fixtures",
    "__fixtures__",
    "test-data",
    "testdata",
    "test_data",
})
_SCRATCH_PREFIXES = ("/tmp/", "/private/tmp/")

# Keys whose presence in the written text names what the write changes. Order
# is the order the reason line lists them in.
_WIDENING_KEYS: tuple[str, ...] = (
    "permissions",
    "allow",
    "deny",
    "ask",
    "hooks",
    "disableAllHooks",
    "env",
)
_KEY_RE = {key: re.compile(r'"' + re.escape(key) + r'"\s*:') for key in _WIDENING_KEYS}
# A permission-rule literal: `Bash(git *)`, `Edit(*.ts)`, `mcp__x__y` is a bare
# name and stays out — the parenthesised form is the one settings carry.
_RULE_LITERAL_RE = re.compile(r'"[A-Za-z][A-Za-z0-9_]*\([^"\n]*\)"')
_PRAXIS_VAR_RE = re.compile(r"\bPRAXIS_[A-Z0-9_]+\b")

_ADVISORY_HEADER = "[settings-path-advisory] Claude Code settings write"


def _normalize(path: str) -> PurePosixPath:
    return PurePosixPath(path.replace("\\", "/"))


def _path_components(path: str) -> tuple[str, ...]:
    parts = _normalize(path).parts
    if parts and parts[0] == "/":
        return parts[1:]
    return parts


def _self_plugin_root() -> str:
    """Walk up from this file to the `hooks/manifest.json` marker (mirrors
    `protected-paths-guard`) so a move of this file keeps the self-edit skip."""
    cur = os.path.dirname(os.path.abspath(__file__))
    while cur and cur != os.path.dirname(cur):
        if os.path.isfile(os.path.join(cur, "hooks", "manifest.json")):
            return cur
        cur = os.path.dirname(cur)
    return ""


def _is_self_edit(path: str) -> bool:
    """True when `path` is inside the praxis plugin root. Relative paths are
    never trusted: `os.path.abspath` would resolve them against the hook's
    cwd, and a cwd inside the plugin root would exempt every bare
    `.claude/settings.json`."""
    if not os.path.isabs(path):
        return False
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "").strip() or _self_plugin_root()
    if not plugin_root:
        return False
    try:
        rel = os.path.relpath(os.path.normpath(path), os.path.abspath(plugin_root))
    except ValueError:
        return False
    return rel != ".." and not rel.startswith(".." + os.sep)


def _is_scratch(path: str) -> bool:
    """True for an absolute path under `/tmp/` or `/private/tmp/` after lexical
    normalization. A relative `tmp/.claude/settings.json` is a project path
    and is never scratch; `/tmp/../repo/.claude/settings.json` resolves
    outside `/tmp/` and is not scratch either (CodeRabbit on #1356). The rule
    lives in `_lib/_path_scope.py` since `protected-paths-guard` needed the
    same one (#1362)."""
    return under_absolute_prefix(path, _SCRATCH_PREFIXES)


def _is_test_fixture(components: tuple[str, ...]) -> bool:
    return any(comp in _FIXTURE_DIRECTORIES for comp in components)


def classify_path(path: str) -> str | None:
    """Return the settings-file kind for `path`, or None.

    `project-or-user` — `<…>/.claude/settings.json` or
    `<…>/.claude/settings.local.json`; the parent component must be exactly
    `.claude`. `managed` — basename `managed-settings.json` at any depth.
    """
    components = _path_components(path)
    if not components:
        return None
    basename = components[-1]
    if basename == _MANAGED_BASENAME:
        return "managed"
    if basename in _SETTINGS_BASENAMES and len(components) >= 2:
        if components[-2] == _SETTINGS_PARENT:
            return "project-or-user"
    return None


def written_text(tool_name: str, tool_input: dict) -> str:
    """The text the call writes: `content` for Write, `new_string` for Edit.
    Non-string values (a malformed payload) read as empty."""
    field = "content" if tool_name == "Write" else "new_string"
    value = tool_input.get(field)
    return value if isinstance(value, str) else ""


def widening_shapes(text: str) -> list[str]:
    """Name the widening-shaped keys and tokens present in `text`, in a fixed
    order, each at most once. Empty list = none seen (the write still fires;
    this only feeds the reason line)."""
    found: list[str] = []
    for key in _WIDENING_KEYS:
        if _KEY_RE[key].search(text):
            found.append(f'"{key}"')
    if _RULE_LITERAL_RE.search(text):
        found.append("a permission-rule literal")
    praxis_vars = sorted(set(_PRAXIS_VAR_RE.findall(text)))
    if praxis_vars:
        found.append("praxis variable " + ", ".join(praxis_vars[:3]))
    return found


def advisory_text(path: str, kind: str, shapes: list[str], strict: bool) -> str:
    mode = "BLOCKED (strict mode)" if strict else "ADVISORY"
    if shapes:
        reason = "the written text carries " + ", ".join(shapes)
    else:
        reason = "no permission/hook key in the written text; the path alone is the surface"
    kind_label = "managed policy file" if kind == "managed" else "project or user settings"
    return (
        f"{_ADVISORY_HEADER} — {mode}\n"
        "\n"
        f"  Path: {path} ({kind_label})\n"
        f"  Reason: {reason}\n"
        "\n"
        "  This file decides what hooks and permission rules do in every later\n"
        "  session. If this write follows a hook block, the only route the agent\n"
        "  may relay is the gate's own `Bypass (if truly needed): <VAR>=1` line;\n"
        "  a permission rule, a hooks edit or a PRAXIS_* variable in settings.json\n"
        "  that the agent proposed is an agent-originated workaround\n"
        "  (ETHOS.md principle 5), whoever approved it.\n"
        "\n"
        "  Before the call: state in the response who asked for this edit and\n"
        "  what it changes, in one sentence. A user-initiated settings change is\n"
        "  not a workaround; say so and proceed.\n"
        "\n"
        "  Bypass options:\n"
        "    • Skip this single call: PRAXIS_HOOK_BYPASS_SETTINGS_PATH=1\n"
        "    • Test fixtures: place under a /fixtures/, /__fixtures__/,\n"
        "      /test-data/ or /testdata/ directory component\n"
        "\n"
        "  Reference: issue #1337; docs/hook/RULE-BACKSTOP-GAPS.md gap #4"
    )


def _emit_additional_context(advisory: str) -> None:
    """The exit-0 PreToolUse channel that reaches the model (issue #874;
    shape shared with `pipefail-advisory`)."""
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": advisory,
            }
        },
        sys.stdout,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")


@fail_open
def main() -> int:
    if os.environ.get("PRAXIS_HOOK_BYPASS_SETTINGS_PATH", "").strip():
        return 0

    payload = read_payload(TARGET_TOOLS)
    if payload is None:
        return 0

    tool_name = payload.get("tool_name", "") or ""
    tool_input = payload.get("tool_input", {}) or {}
    if not isinstance(tool_input, dict):
        return 0
    file_path = (tool_input.get("file_path") or "").strip()
    if not file_path:
        return 0

    kind = classify_path(file_path)
    if kind is None:
        return 0
    if _is_self_edit(file_path) or _is_scratch(file_path):
        return 0
    if _is_test_fixture(_path_components(file_path)):
        return 0

    shapes = widening_shapes(written_text(tool_name, tool_input))
    strict = os.environ.get("PRAXIS_SETTINGS_PATH_STRICT", "").strip() == "1"
    text = advisory_text(file_path, kind, shapes, strict)
    sys.stderr.write(text + "\n")
    if strict:
        return 2
    _emit_additional_context(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
