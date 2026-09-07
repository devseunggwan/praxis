"""Static gate: every body a hook emits starts with an English line (issue #1298).

Issue #1160 set the rule for two advisories — English one-line summary first,
Korean detail after — and left the other hooks alone. This test extends it to
the whole suite by scanning the source rather than the runtime: it parses every
``hooks/**/impl.py`` and fails on any string literal that would put Hangul in
front of the reader before any English.

What counts as a hit: a string constant (an f-string is one literal — its
placeholders render as empty, so the check sees the text the reader sees) of
length > 8 whose first non-whitespace character, after an optional
``[hook-name]`` tag, is a precomposed Hangul syllable (U+AC00–U+D7A3).

What is skipped, because it is never emitted or never starts a body:

  - docstrings (module / class / function first-statement strings),
  - anything inside the argument list of a ``re.<fn>(...)`` call,
  - raw-prefixed literals (``r"..."``) — the regex idiom in this repo,
  - module-level tuples/lists/sets of strings that are only ever loaded as a
    ``re.<fn>`` argument or as the iterable of a membership comprehension
    (``any(k in text for k in _VOCAB)``) — match vocabulary, not prose,
  - the right operand of ``+`` and the value of ``+=`` — text glued onto a
    message that already started somewhere else.

Comments are not in the AST, so they never count.

Run: python3 -m pytest tests/test_emit_english_lead.py -q
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / "hooks"

# Literals at or below this length are labels and separators, not bodies.
MIN_BODY_LEN = 9

# Must stay empty. Add an entry only with an issue link explaining why that
# literal cannot carry an English lead yet, e.g.
#   ("hooks/<role>/<name>/impl.py", 123),  # #NNNN — <reason>
# The test also fails when an entry stops matching, so stale entries cannot
# linger after the literal is fixed.
ALLOWLIST: set[tuple[str, int]] = set()

_LEADING_TAG_RE = re.compile(r"^\s*(?:\[[^\]\n]*\]\s*)?")
_STRING_PREFIX_RE = re.compile(r"[A-Za-z]*")


def _is_hangul(ch: str) -> bool:
    return "\uac00" <= ch <= "\ud7a3"


def starts_with_hangul(text: str) -> bool:
    """True when the first thing a reader sees, past an optional tag, is Hangul."""
    rest = _LEADING_TAG_RE.sub("", text, count=1)
    return bool(rest) and _is_hangul(rest[0])


def _render(node: ast.AST) -> str | None:
    """The literal's text as the reader sees it; None for non-string nodes."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value
            for v in node.values
            if isinstance(v, ast.Constant) and isinstance(v.value, str)
        )
    return None


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            ids.add(id(body[0].value))
    return ids


def _is_re_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "re"
    )


def _subtree_ids(*roots: ast.AST) -> set[int]:
    ids: set[int] = set()
    for root in roots:
        ids.update(id(n) for n in ast.walk(root))
    return ids


def _pattern_ids(tree: ast.AST) -> set[int]:
    """Every node inside the argument list of a ``re.<fn>(...)`` call."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if _is_re_call(node):
            ids |= _subtree_ids(*node.args, *(kw.value for kw in node.keywords))
    return ids


def _continuation_ids(tree: ast.AST) -> set[int]:
    """Every node glued onto the right of ``+`` or fed to ``+=``."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            ids |= _subtree_ids(node.right)
        elif isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add):
            ids |= _subtree_ids(node.value)
    return ids


def _parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


def _is_membership_iterable(load: ast.Name, parents: dict[int, ast.AST]) -> bool:
    """``load`` is the ``iter`` of ``<...> for k in load`` whose element tests
    ``k in <text>`` — a substring vocabulary, never emitted."""
    comp = parents.get(id(load))
    if not isinstance(comp, ast.comprehension) or comp.iter is not load:
        return False
    if not isinstance(comp.target, ast.Name):
        return False
    owner = parents.get(id(comp))
    if not isinstance(owner, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
        return False
    elt = owner.elt
    return (
        isinstance(elt, ast.Compare)
        and isinstance(elt.left, ast.Name)
        and elt.left.id == comp.target.id
        and any(isinstance(op, (ast.In, ast.NotIn)) for op in elt.ops)
    )


def _vocabulary_ids(tree: ast.Module, pattern_ids: set[int]) -> set[int]:
    """Elements of module-level string containers that are only ever matched
    against text (as a ``re`` argument or a membership iterable)."""
    parents = _parent_map(tree)
    loads: dict[str, list[ast.Name]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            loads.setdefault(node.id, []).append(node)

    ids: set[int] = set()
    for stmt in tree.body:
        if not (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and isinstance(stmt.value, (ast.Tuple, ast.List, ast.Set))
            and stmt.value.elts
            and all(
                isinstance(e, ast.Constant) and isinstance(e.value, str)
                for e in stmt.value.elts
            )
        ):
            continue
        uses = loads.get(stmt.targets[0].id, [])
        if uses and all(
            id(u) in pattern_ids or _is_membership_iterable(u, parents) for u in uses
        ):
            ids.update(id(e) for e in stmt.value.elts)
    return ids


def _raw_string_starts(source: str) -> set[tuple[int, int]]:
    """(lineno, col) of every STRING token carrying an ``r``/``R`` prefix."""
    starts: set[tuple[int, int]] = set()
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type != tokenize.STRING:
            continue
        prefix = _STRING_PREFIX_RE.match(tok.string)
        if prefix and "r" in prefix.group().lower():
            starts.add(tok.start)
    return starts


def scan_source(source: str, rel_path: str) -> list[tuple[str, int]]:
    """Return ``(rel_path, lineno)`` for every offending literal in ``source``."""
    tree = ast.parse(source)
    pattern_ids = _pattern_ids(tree)
    skip = (
        _docstring_ids(tree)
        | pattern_ids
        | _continuation_ids(tree)
        | _vocabulary_ids(tree, pattern_ids)
    )
    raw_starts = _raw_string_starts(source)
    # An f-string is judged as one literal; its parts are not judged again.
    joined_parts = {
        id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in n.values
    }

    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if id(node) in skip or id(node) in joined_parts:
            continue
        text = _render(node)
        if text is None or len(text) < MIN_BODY_LEN:
            continue
        if (node.lineno, node.col_offset) in raw_starts:
            continue
        if starts_with_hangul(text):
            hits.append((rel_path, node.lineno))
    return hits


def scan_hooks(root: Path = HOOKS_DIR) -> list[tuple[str, int]]:
    hits: list[tuple[str, int]] = []
    for path in sorted(root.rglob("impl.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        hits.extend(scan_source(path.read_text(encoding="utf-8"), rel))
    return hits


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_hook_emissions_start_in_english() -> None:
    hits = scan_hooks()
    unexpected = sorted(h for h in hits if h not in ALLOWLIST)
    stale = sorted(ALLOWLIST - set(hits))
    lines = []
    if unexpected:
        lines.append(
            "Hook string literals that would reach the reader in Korean before "
            "any English (DESIGN.md → English-first emitted bodies, #1298). "
            "Prepend an English lead line; keep the Korean after a newline:"
        )
        lines += [f"  {path}:{lineno}" for path, lineno in unexpected]
    if stale:
        lines.append("ALLOWLIST entries that no longer match — remove them:")
        lines += [f"  {path}:{lineno}" for path, lineno in stale]
    assert not lines, "\n" + "\n".join(lines)


def test_allowlist_stays_empty_or_justified() -> None:
    # A populated allowlist is a tracked exception, not a default. Every entry
    # must sit on a line carrying an issue reference in this file's source.
    source = Path(__file__).read_text(encoding="utf-8").splitlines()
    for path, lineno in ALLOWLIST:
        owners = [ln for ln in source if repr(path) in ln and str(lineno) in ln]
        assert owners and any("#" in ln for ln in owners), (
            f"ALLOWLIST entry {path}:{lineno} has no issue link"
        )


# ---------------------------------------------------------------------------
# The scanner's own contract — so a blind spot fails loudly here rather than
# passing the tree silently.
# ---------------------------------------------------------------------------

_KO = "한국어 본문입니다 — 영문 요약이 앞에 없습니다"


@pytest.mark.parametrize(
    ("source", "expected_lines"),
    [
        pytest.param(f'msg = "{_KO}"\n', [1], id="plain-korean-body"),
        pytest.param(f'msg = "[some-hook] {_KO}"\n', [1], id="tag-then-korean"),
        pytest.param(f'msg = "[some-hook]   \\n  {_KO}"\n', [1], id="tag-space-korean"),
        pytest.param(f'msg = "English lead\\n{_KO}"\n', [], id="english-lead-then-korean"),
        pytest.param(f'msg = f"{{_PREFIX}} {_KO}"\n', [1], id="fstring-placeholder-then-korean"),
        pytest.param(
            f'msg = f"[tag] {{url}} has {{n}} threads\\n  {{url}} 에 {_KO}"\n',
            [],
            id="fstring-english-lead-korean-mid-part",
        ),
        pytest.param(f'msg = "en " + "{_KO}"\n', [], id="right-of-plus-is-continuation"),
        pytest.param(f'msg = "{_KO}" + " tail"\n', [1], id="left-of-plus-is-a-start"),
        pytest.param(f'msg = ""\nmsg += "{_KO}"\n', [], id="augassign-is-continuation"),
        pytest.param(f'"""{_KO}"""\nx = 1\n', [], id="module-docstring"),
        pytest.param(f'def f():\n    """{_KO}"""\n', [], id="function-docstring"),
        pytest.param(f'import re\n_RE = re.compile(r"{_KO}")\n', [], id="re-argument"),
        pytest.param(f'import re\n_RE = re.compile("{_KO}", re.I)\n', [], id="re-argument-not-raw"),
        pytest.param(f'_P = r"{_KO}"\n', [], id="raw-string"),
        pytest.param(
            f'_V = ("{_KO}", "{_KO}")\nhit = any(k in text for k in _V)\n',
            [],
            id="membership-vocabulary",
        ),
        pytest.param(
            f'_V = ("{_KO}",)\nfor k in _V:\n    print(k)\n',
            [1],
            id="container-emitted-in-loop-still-counts",
        ),
        pytest.param(f'x = "{_KO}"  # {_KO}\n', [1], id="comment-ignored-literal-counts"),
        pytest.param('x = "짧음"\n', [], id="short-label-ignored"),
    ],
)
def test_scan_source_rules(source: str, expected_lines: list[int]) -> None:
    hits = scan_source(source, "x.py")
    assert [ln for _, ln in hits] == expected_lines
