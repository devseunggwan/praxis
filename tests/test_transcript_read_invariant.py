"""No hook materializes the session transcript (issues #1076, #1277, #1279).

Every transcript-cost fix so far was applied per call site — #1224, #1243,
#1251 — and each one held, but nothing stopped the pattern re-entering
through code those fixes never touched: a gate that was only ever refactored
(#1277), and a reader the `readlines()` search of #1251 did not match
(#1279). The subprocess budget has the guard this lacked
(`test_dispatch.py::test_every_subprocess_member_is_budget_aware`); this is
its sibling for transcript reads.

The invariant: a hook that takes `transcript_path` from its payload may hand
the file only to the bounded readers in `hooks/_lib/_transcript.py`
(`tail_lines`, `load_current_turn`, `load_recent_events`,
`read_last_user_message`, `scan_user_rejections`,
`reduce_transcript_resumable`, a streaming `iter_transcript`) or stream it
itself a line at a time. What it must never do is load the whole file into
memory — `read_text()`, `readlines()`, an unbounded `.read()`, `list(fh)`,
`json.load(fh)`, or the legacy `load_transcript()` — because a session JSONL
reaches hundreds of MB (a 224 MB one cost 741 MB of RSS per hook, #1076) and
every such read runs inside a shared dispatch deadline.

The check is a taint walk over each `impl.py`'s AST: names assigned from an
expression that mentions the `"transcript_path"` key are tainted, taint
follows assignments and local calls into the callee's parameters, and a
materializing read on a tainted expression is an offence. It is a necessary
condition, not a proof — a read routed through a module the walk does not
follow would pass — so the scanners in `_transcript.py` are the place such a
read must live, where `test_transcript.py` pins their shape.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS = REPO_ROOT / "hooks"

_KEY = "transcript_path"
# Whole-file reads. `.read()` with an argument is a bounded read and passes.
_MATERIALIZING_METHODS = {"read_text", "readlines"}
_MATERIALIZING_FUNCS = {"load_transcript"}

# Offenders known at the time the guard landed, each with the PR that removes
# it. An entry here is skipped, never asserted stale — the PRs land in any
# order, and a stale entry is harmless where a false failure is not.
_ALLOWLIST = {
    # `_read_lines` materializes the root and every subagent transcript;
    # replaced by a streaming prefiltered scan in #1307 (issue #1277).
    "preflight-gate/block-commit-without-codex-review",
    # Found by this guard's first run (issue #1312): the same read_text() +
    # parse-every-line shape, on every `git commit`. Fixed in #1312's PR.
    "preflight-gate/skill-gate-commands",
    "advisory-nudge/pre-commit-staged-file-enumeration",
}


def _names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _mentions_key(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Constant) and n.value == _KEY for n in ast.walk(node)
    )


class _Taint(ast.NodeVisitor):
    """Collect tainted names and offences for one function body."""

    def __init__(self, tainted: set[str]):
        self.tainted = set(tainted)
        self.handles: set[str] = set()  # file objects opened on a tainted path
        self.offences: list[int] = []
        self.calls: list[tuple[str, list[int]]] = []  # (callee, tainted arg idx)

    def _is_tainted(self, node: ast.AST) -> bool:
        # A tainted name, or the payload key read inline in the expression
        # (`Path(payload.get("transcript_path")).read_text()`).
        return bool(_names_in(node) & self.tainted) or _mentions_key(node)

    def _taint_targets(self, target: ast.AST, handle: bool = False) -> None:
        for n in ast.walk(target):
            if isinstance(n, ast.Name):
                self.tainted.add(n.id)
                if handle:
                    self.handles.add(n.id)

    @staticmethod
    def _opens_file(node: ast.AST) -> bool:
        """`open(...)` / `Path.open(...)` — the value is a file object."""
        if not isinstance(node, ast.Call):
            return False
        f = node.func
        return (isinstance(f, ast.Name) and f.id == "open") or (
            isinstance(f, ast.Attribute) and f.attr == "open"
        )

    def _is_handle(self, node: ast.AST) -> bool:
        return isinstance(node, ast.Name) and node.id in self.handles

    def visit_Assign(self, node: ast.Assign) -> None:
        if _mentions_key(node.value) or self._is_tainted(node.value):
            for t in node.targets:
                self._taint_targets(t, handle=self._opens_file(node.value))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None and (
            _mentions_key(node.value) or self._is_tainted(node.value)
        ):
            self._taint_targets(node.target)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        if self._is_tainted(node.iter):
            self._taint_targets(node.target)
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if item.optional_vars is not None and self._is_tainted(item.context_expr):
                self._taint_targets(
                    item.optional_vars, handle=self._opens_file(item.context_expr)
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        # `read_text` / `readlines` on any tainted path or handle; an unbounded
        # `.read()`, `json.load` and `list(...)` only on a file HANDLE opened on
        # the transcript — `list(dict.fromkeys(samples))` over data that merely
        # derived from the transcript is not a file read.
        if isinstance(func, ast.Attribute):
            if func.attr in _MATERIALIZING_METHODS and self._is_tainted(func.value):
                self.offences.append(node.lineno)
            elif func.attr == "read" and not node.args and self._is_handle(func.value):
                self.offences.append(node.lineno)
            elif (
                func.attr == "load"
                and isinstance(func.value, ast.Name)
                and func.value.id == "json"
                and node.args
                and self._is_handle(node.args[0])
            ):
                self.offences.append(node.lineno)
        elif isinstance(func, ast.Name):
            if func.id in _MATERIALIZING_FUNCS and any(self._is_tainted(a) for a in node.args):
                self.offences.append(node.lineno)
            elif func.id == "list" and node.args and self._is_handle(node.args[0]):
                self.offences.append(node.lineno)
            else:
                idx = [i for i, a in enumerate(node.args) if self._is_tainted(a)]
                if idx:
                    self.calls.append((func.id, idx))
        self.generic_visit(node)


def materializing_transcript_reads(source: str) -> list[int]:
    """Line numbers where `source` loads the whole transcript into memory."""
    tree = ast.parse(source)
    funcs = {
        n.name: n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    # Seed: every function whose body mentions the payload key, plus any
    # parameter literally named after the transcript.
    seeds: dict[str, set[str]] = {}
    for name, fn in funcs.items():
        params = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
        seeded = {p for p in params if "transcript" in p}
        if _mentions_key(fn) or seeded:
            seeds[name] = seeded
    offences: set[int] = set()
    seen: set[tuple[str, frozenset[str]]] = set()
    queue = list(seeds.items())
    while queue:
        name, tainted = queue.pop()
        key = (name, frozenset(tainted))
        if key in seen:
            continue
        seen.add(key)
        walker = _Taint(tainted)
        walker.visit(funcs[name])
        offences.update(walker.offences)
        for callee, idx in walker.calls:
            fn = funcs.get(callee)
            if fn is None:
                continue
            params = [a.arg for a in fn.args.args]
            queue.append((callee, {params[i] for i in idx if i < len(params)}))
    return sorted(offences)


def test_no_hook_materializes_the_transcript():
    offenders = {}
    for impl in sorted(HOOKS.glob("*/*/impl.py")):
        rel = f"{impl.parent.parent.name}/{impl.parent.name}"
        if rel in _ALLOWLIST:
            continue
        lines = materializing_transcript_reads(impl.read_text(encoding="utf-8"))
        if lines:
            offenders[rel] = lines
    assert offenders == {}, (
        "whole-transcript reads outside hooks/_lib/_transcript.py — use "
        "tail_lines / load_current_turn / iter_transcript(needle=...) or add "
        f"a justified _ALLOWLIST entry: {offenders}"
    )


def test_allowlist_names_real_hooks():
    for rel in _ALLOWLIST:
        assert (HOOKS / rel / "impl.py").is_file(), rel


@pytest.mark.parametrize("body, expect", [
    # Materializing reads on the payload's transcript path, each caught.
    ('Path(payload.get("transcript_path")).read_text()', True),
    ('p = payload.get("transcript_path")\n    Path(p).read_text()', True),
    ('p = payload.get("transcript_path")\n    open(p).readlines()', True),
    ('p = payload.get("transcript_path")\n    with open(p) as fh:\n        fh.read()', True),
    ('p = payload.get("transcript_path")\n    with open(p) as fh:\n        list(fh)', True),
    ('p = payload.get("transcript_path")\n    with open(p) as fh:\n        json.load(fh)', True),
    ('p = payload.get("transcript_path")\n    load_transcript(p)', True),
    # Routed through a local helper: taint follows the argument.
    ('p = payload.get("transcript_path")\n    helper(p)\n'
     'def helper(path):\n    Path(path).read_text()', True),
    # Bounded and streaming shapes, each allowed.
    ('p = payload.get("transcript_path")\n    tail_lines(p, 400)', False),
    ('p = payload.get("transcript_path")\n    load_current_turn(p)', False),
    ('p = payload.get("transcript_path")\n    with open(p, "rb") as fh:\n        fh.read(4096)', False),
    ('p = payload.get("transcript_path")\n    with open(p) as fh:\n        for line in fh:\n            pass', False),
    ('p = payload.get("transcript_path")\n    for ev in iter_transcript(p, needle="x"):\n        pass', False),
    # Whole-file read of an unrelated file: not the transcript, not flagged.
    ('p = payload.get("transcript_path")\n    Path("catalog.json").read_text()', False),
    # `list(...)` over data derived from the transcript is not a file read.
    ('p = payload.get("transcript_path")\n    hits = tail_lines(p, 400)\n    list(dict.fromkeys(hits))', False),
    # A handle bound by assignment rather than `with`.
    ('p = payload.get("transcript_path")\n    fh = open(p)\n    fh.readlines()', True),
])
def test_the_detector_can_fail(body, expect):
    # Positive control: an empty offender list has to mean "no whole-file
    # reads", not "the walk stopped matching".
    head, _, tail = body.partition("\ndef ")
    src = "def main(payload):\n    " + head + ("\n\ndef " + tail if tail else "")
    assert bool(materializing_transcript_reads(src)) is expect, src
