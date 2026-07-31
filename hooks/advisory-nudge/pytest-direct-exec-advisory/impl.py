#!/usr/bin/env python3
"""Advise when pytest-shaped files are executed directly with Python."""
from __future__ import annotations

import ast
import json
import os
import shlex
import sys
from pathlib import Path

_HOOK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_HOOK_DIR.parent.parent / "_lib"))


def _load_hook_libs():
    from _hook_runtime import fail_open
    from _hook_utils import TokenRole, filter_argv, tokenize_with_roles

    return fail_open, TokenRole, filter_argv, tokenize_with_roles


fail_open, TokenRole, filter_argv, tokenize_with_roles = _load_hook_libs()

_PYTHON_COMMANDS = frozenset({"python", "python3"})
_VALUE_FLAGS = frozenset({"-W", "-X", "--check-hash-based-pycs"})
_FLAG_VALUE_SPEC = {command: set(_VALUE_FLAGS) for command in _PYTHON_COMMANDS}
_DYNAMIC_PATH_CHARS = frozenset("$`*?[]")


def _script_operand(segment) -> str | None:
    argv = filter_argv(segment)
    if not argv or Path(argv[0].text).name not in _PYTHON_COMMANDS:
        return None

    index = 1
    while index < len(argv):
        token = argv[index]
        text = token.text

        if token.role == TokenRole.SEPARATOR_DD:
            index += 1
            continue
        if token.role == TokenRole.SUBST_RUN:
            return None
        if token.role == TokenRole.FLAG:
            if text in {"-c", "-m"}:
                return None
            if text in _VALUE_FLAGS and index + 1 < len(argv):
                index += 2
                continue
            index += 1
            continue
        if token.role == TokenRole.FLAG_VALUE:
            index += 1
            continue
        if text == "-":
            return None
        return text

    return None


def _is_dynamic_path(script: str) -> bool:
    return any(char in script for char in _DYNAMIC_PATH_CHARS)


def _resolve_script(script: str, cwd: str) -> Path | None:
    if _is_dynamic_path(script):
        return None

    path = Path(script).expanduser()
    if not path.is_absolute():
        path = Path(cwd) / path
    try:
        if not path.is_file():
            return None
        return path.resolve()
    except OSError:
        return None


def _has_selected_path_shape(path: Path) -> bool:
    if path.suffix != ".py":
        return False
    return path.name.startswith("test_") or "tests" in path.parts


def _uses_pytest(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "pytest" or alias.name.startswith("pytest.") for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == "pytest" or (node.module or "").startswith("pytest."):
                return True
        elif isinstance(node, ast.Attribute):
            value = node.value
            while isinstance(value, ast.Attribute):
                value = value.value
            if isinstance(value, ast.Name) and value.id == "pytest":
                return True
    return False


def _has_test_structure(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                return True
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            if any(
                isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                and member.name.startswith("test_")
                for member in node.body
            ):
                return True
    return False


def _is_pytest_source(path: Path) -> bool:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, UnicodeError, SyntaxError):
        return False
    return _uses_pytest(tree) or _has_test_structure(tree)


def _heredoc_marker(line: str) -> tuple[str, bool] | None:
    quote: str | None = None
    index = 0
    while index < len(line):
        char = line[index]
        if quote is not None:
            if char == "\\" and quote == '"':
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char == "#" and (index == 0 or line[index - 1].isspace()):
            return None
        if not line.startswith("<<", index) or line.startswith("<<<", index):
            index += 1
            continue

        marker_start = index + 2
        strip_tabs = marker_start < len(line) and line[marker_start] == "-"
        if strip_tabs:
            marker_start += 1
        while marker_start < len(line) and line[marker_start] in " \t":
            marker_start += 1
        if marker_start >= len(line):
            return None

        marker_quote = line[marker_start] if line[marker_start] in {"'", '"'} else None
        if marker_quote is not None:
            marker_start += 1
            marker_end = line.find(marker_quote, marker_start)
            if marker_end < 0:
                return None
        else:
            marker_end = marker_start
            while marker_end < len(line) and line[marker_end] not in " \t;|&<>":
                marker_end += 1

        marker = line[marker_start:marker_end]
        return (marker, strip_tabs) if marker else None
    return None


def _without_heredoc_bodies(command: str) -> str:
    live_lines: list[str] = []
    active_marker: tuple[str, bool] | None = None

    for line in command.splitlines():
        if active_marker is not None:
            marker, strip_tabs = active_marker
            candidate = line.lstrip("\t") if strip_tabs else line
            if candidate == marker:
                active_marker = None
            continue

        live_lines.append(line)
        active_marker = _heredoc_marker(line)

    return "\n".join(live_lines)


def _find_direct_exec(command: str, cwd: str) -> str | None:
    live_command = _without_heredoc_bodies(command)
    for segment in tokenize_with_roles(live_command.replace("\\\n", " "), _FLAG_VALUE_SPEC):
        script = _script_operand(segment)
        if script is None:
            continue
        path = _resolve_script(script, cwd)
        if path is None or not _has_selected_path_shape(path):
            continue
        if _is_pytest_source(path):
            return script
    return None


def _advisory(script: str) -> str:
    pytest_command = f"python3 -m pytest {shlex.quote(script)}"
    return (
        "[pytest-direct-exec-advisory] "
        f"`{script}` looks like a pytest test file, but direct Python execution "
        "can exit 0 without collecting or running its tests.\n"
        f"[pytest-direct-exec-advisory] Run `{pytest_command}` instead."
    )


@fail_open
def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0
    command = (payload.get("tool_input") or {}).get("command", "")
    if not isinstance(command, str) or not command.strip():
        return 0

    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        cwd = os.getcwd()

    script = _find_direct_exec(command, cwd)
    if script is not None:
        sys.stderr.write(_advisory(script) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
