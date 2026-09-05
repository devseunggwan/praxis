"""Pin the import surface of the `_hook_utils` shim after the #1305 split.

`hooks/_lib/_hook_utils.py` used to define the whole tokenizer. Issue #1305
moved the code into `_shell_tokenize`, `_subst`, `_compound`, and `_roles`
and left a re-export shim behind so the `from _hook_utils import …` preamble
in every `impl.py` keeps working. Three things can regress silently from
here, and this file pins each one:

1. A name importable before the split stops being importable. The frozen
   list below was recorded from `origin/main` at 9a75c74 with
   `dir(_hook_utils)` minus dunders, minus the module's own stdlib imports
   (`re`, `shlex`, `Enum`, `Optional`, `dataclass`, `annotations`) — no
   consumer imports those, and the shim deliberately does not carry them.
2. A sub-module stops importing on its own. The sharp edge is a name that
   collides with a built-in module: CPython 3.11+ ships `_tokenize` as a
   built-in, and built-ins shadow `sys.path`, which is why the tokenizer
   module is `_shell_tokenize`. Each sub-module is imported in a fresh
   interpreter with only `hooks/_lib` added, and its spec must resolve to
   the file in `hooks/_lib`.
3. A sub-module imports the shim, closing a cycle through it. The allowed
   dependency graph is pinned explicitly: `_shell_tokenize` imports no
   `_lib` sibling, the other three import only `_shell_tokenize`.
"""
from __future__ import annotations

import ast
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_DIR = REPO_ROOT / "hooks" / "_lib"

SUB_MODULES = ("_shell_tokenize", "_subst", "_compound", "_roles")

# Which `_lib` siblings each sub-module may import. `_hook_utils` is absent
# from every set — that is the no-cycle rule.
ALLOWED_LIB_IMPORTS = {
    "_shell_tokenize": frozenset(),
    "_subst": frozenset({"_shell_tokenize"}),
    "_compound": frozenset({"_shell_tokenize"}),
    "_roles": frozenset({"_shell_tokenize"}),
}

# Recorded from origin/main 9a75c74:
#   git show origin/main:hooks/_lib/_hook_utils.py > /tmp/hu.py
#   python3 -c 'import sys; sys.path.insert(0, "/tmp"); import hu;
#     print(sorted(n for n in dir(hu) if not n.startswith("__")))'
# minus the stdlib import leakage named in the module docstring.
ORIGIN_MAIN_SURFACE = frozenset({
    "COMPOUND_CASCADE_HINT",
    "ENV_ASSIGN_RE",
    "GH_MERGE_VALUE_FLAGS",
    "HELP_FLAGS",
    "MAX_SUBST_DEPTH",
    "PREFIX_WRAPPERS",
    "SHELL_KEYWORDS",
    "SHELL_SEPARATORS",
    "STATE_CHANGING_COMMANDS",
    "Token",
    "TokenRole",
    "WRAPPER_OPTS_WITH_ARG",
    "_ACK_MARKER_RE",
    "_CURL_OUTPUT_FLAGS",
    "_EXPANSION_START",
    "_GROUP_PREFIX_CHARS",
    "_HEREDOC_WORD_CHARS",
    "_WGET_OUTPUT_FLAGS",
    "_WORD_BOUNDARY_CHARS",
    "_active_substitutions",
    "_closing_backtick",
    "_closing_paren",
    "_coalesce_subst_runs",
    "_command_spec_key",
    "_heredoc_starts_on_line",
    "_is_gh_binary",
    "_logical_lines",
    "_quote_open_at_eol",
    "_read_heredoc_delim",
    "_resolve_subcommand",
    "_segment_has_redirect",
    "_segment_has_state_change",
    "_starts_unquoted_comment",
    "_strip_trailing_comment",
    "_unquoted_comment_start",
    "compound_cascade_hint",
    "filter_argv",
    "has_shell_expansion",
    "has_state_changing_redirect",
    "has_unclosed_expansion",
    "heredoc_bodies",
    "heredoc_delimiters",
    "heredoc_sources",
    "is_compound_command",
    "is_help_invocation",
    "iter_command_starts",
    "iter_command_texts",
    "safe_tokenize",
    "strip_heredoc_bodies",
    "strip_prefix",
    "tokenize_with_roles",
})

# The only non-dunder name the shim may carry beyond the frozen surface: the
# `from __future__ import annotations` line every module in this repo opens
# with binds it.
_SHIM_OWN_NAMES = frozenset({"annotations"})


def _import_lib(name: str):
    if str(LIB_DIR) not in sys.path:
        sys.path.insert(0, str(LIB_DIR))
    return importlib.import_module(name)


def _module_level_names(path: Path) -> set[str]:
    """Names a module defines at its top level — not what it imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return {n for n in names if not n.startswith("__")}


def _lib_imports(path: Path) -> set[str]:
    """Top-level modules imported by `path` that are `hooks/_lib` siblings."""
    siblings = {p.stem for p in LIB_DIR.glob("*.py")}
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found & siblings


# ---------------------------------------------------------------------------
# 1. The shim's surface equals the frozen origin/main surface
# ---------------------------------------------------------------------------


def test_shim_surface_equals_origin_main():
    shim = _import_lib("_hook_utils")
    surface = {n for n in dir(shim) if not n.startswith("__")} - _SHIM_OWN_NAMES
    missing = ORIGIN_MAIN_SURFACE - surface
    extra = surface - ORIGIN_MAIN_SURFACE
    assert not missing, f"names dropped from _hook_utils: {sorted(missing)}"
    assert not extra, f"names added to _hook_utils beyond the frozen surface: {sorted(extra)}"


def test_shim_dunder_all_equals_origin_main():
    shim = _import_lib("_hook_utils")
    assert set(shim.__all__) == ORIGIN_MAIN_SURFACE
    assert len(shim.__all__) == len(ORIGIN_MAIN_SURFACE), "duplicate entry in __all__"


def test_every_surface_name_is_defined_in_exactly_one_sub_module():
    defined = {name: _module_level_names(LIB_DIR / f"{name}.py") for name in SUB_MODULES}
    union: set[str] = set()
    for a in SUB_MODULES:
        for b in SUB_MODULES:
            if a < b:
                overlap = defined[a] & defined[b]
                assert not overlap, f"{a} and {b} both define {sorted(overlap)}"
        union |= defined[a]
    assert union == ORIGIN_MAIN_SURFACE, (
        f"missing from every sub-module: {sorted(ORIGIN_MAIN_SURFACE - union)}; "
        f"defined but not on the frozen surface: {sorted(union - ORIGIN_MAIN_SURFACE)}"
    )


def test_shim_re_exports_the_defining_module_objects():
    """Same object, not a copy — a caller that identity-checks a Token role
    against `_roles.TokenRole` must agree with one imported via the shim."""
    shim = _import_lib("_hook_utils")
    for mod_name in SUB_MODULES:
        mod = _import_lib(mod_name)
        for name in _module_level_names(LIB_DIR / f"{mod_name}.py"):
            assert getattr(shim, name) is getattr(mod, name), f"{name} via shim != {mod_name}.{name}"


# ---------------------------------------------------------------------------
# 2. Each sub-module imports cleanly in isolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", SUB_MODULES)
def test_sub_module_imports_in_isolation(name: str):
    expected_origin = str(LIB_DIR / f"{name}.py")
    code = (
        "import importlib, importlib.util, sys\n"
        f"sys.path.insert(0, {str(LIB_DIR)!r})\n"
        f"spec = importlib.util.find_spec({name!r})\n"
        f"assert spec is not None, {name!r} + ' not found'\n"
        f"assert spec.origin == {expected_origin!r}, "
        f"'{name} resolved to ' + str(spec.origin) + ' — shadowed by a built-in or stdlib module?'\n"
        f"importlib.import_module({name!r})\n"
        "assert '_hook_utils' not in sys.modules, 'sub-module pulled the shim in'\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"


def test_no_sub_module_name_is_a_builtin_module():
    for name in SUB_MODULES:
        assert name not in sys.builtin_module_names, (
            f"{name} is a CPython built-in module and would shadow hooks/_lib/{name}.py"
        )


# ---------------------------------------------------------------------------
# 3. No cycle through the shim; dependency graph is the pinned one
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", SUB_MODULES)
def test_sub_module_imports_only_allowed_lib_siblings(name: str):
    imported = _lib_imports(LIB_DIR / f"{name}.py")
    assert "_hook_utils" not in imported, f"{name} imports the shim — cycle"
    assert imported <= ALLOWED_LIB_IMPORTS[name], (
        f"{name} imports {sorted(imported - ALLOWED_LIB_IMPORTS[name])} outside the pinned graph"
    )


def test_shim_imports_exactly_the_four_sub_modules():
    imported = _lib_imports(LIB_DIR / "_hook_utils.py")
    assert imported == set(SUB_MODULES)
