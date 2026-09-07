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
_MATERIALIZING_METHODS = {"read_text", "read_bytes", "readlines"}
_MATERIALIZING_FUNCS = {"load_transcript"}
# Builtins that drain a stream into memory. `deque(fh, maxlen=N)` is bounded
# and passes; the bare form is not.
_DRAINING_FUNCS = {"list", "tuple", "sorted", "set", "frozenset", "deque"}
# Calls whose result is a stream over the transcript: draining one is the
# #1076 shape (a 224 MB session as a list of dicts) whatever the reader.
_STREAM_FUNCS = {"open", "iter_transcript", "iter_transcript_bounded"}
# Readers whose result is transcript CONTENT, not a path: a loop over them
# binds lines or events, and a file named inside one is not the transcript.
_CONTENT_READERS = {
    "tail_lines", "load_current_turn", "load_recent_events", "load_transcript",
    "iter_transcript", "iter_transcript_bounded", "read_last_user_message",
    "scan_user_rejections", "reduce_transcript_resumable",
}

# Offenders known at the time the guard landed, each with the PR that removes
# it. An entry here is skipped, never asserted stale — the PRs land in any
# order, and a stale entry is harmless where a false failure is not. It is
# asserted to NAME A REAL HOOK, so a rename or removal has to touch this list
# instead of leaving an exemption nothing can ever hit.
# Hooks the invariant skips, as "<role>/<name>" with the PR that removes the
# entry. Empty since #1307 and #1314 landed: the three offenders this guard
# was born with (issues #1277, #1312) now go through the bounded readers. An
# entry here is a debt with a named payer, never a permanent exemption.
_ALLOWLIST: set[str] = set()


def _names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _mentions_key(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Constant) and n.value == _KEY for n in ast.walk(node)
    )


class _Taint(ast.NodeVisitor):
    """Collect tainted names and offences for one function body."""

    def __init__(self, tainted: set[str], handles: set[str], key_names: set[str]):
        self.tainted = set(tainted)
        self.handles = set(handles)  # streams opened on a tainted path
        self.key_names = key_names  # module constants bound to the key literal
        self.offences: list[int] = []
        # (callee, {param index or keyword: kind}) — kind is "taint" or "handle"
        self.calls: list[tuple[str, dict]] = []

    def _mentions_key(self, node: ast.AST) -> bool:
        return _mentions_key(node) or bool(_names_in(node) & self.key_names)

    def _is_tainted(self, node: ast.AST) -> bool:
        # A tainted name, or the payload key read inline in the expression
        # (`Path(payload.get("transcript_path")).read_text()`).
        return bool(_names_in(node) & self.tainted) or self._mentions_key(node)

    def _is_stream_call(self, node: ast.AST) -> bool:
        """`open(...)` / `p.open(...)` / `iter_transcript(...)` on a tainted path."""
        if not isinstance(node, ast.Call):
            return False
        f = node.func
        named = (isinstance(f, ast.Name) and f.id in _STREAM_FUNCS) or (
            isinstance(f, ast.Attribute) and f.attr in _STREAM_FUNCS
        )
        return named and (self._is_tainted(node) or self._is_handle_expr(f))

    def _is_handle_expr(self, node: ast.AST) -> bool:
        return (isinstance(node, ast.Name) and node.id in self.handles) or self._is_stream_call(node)

    def _bind(self, target: ast.AST, value: ast.AST | None) -> None:
        """Assignment semantics: a name takes the taint of its new value, and
        loses it when the value is clean — a name reused for an unrelated file
        must not carry the transcript's taint into a false positive."""
        names = [n.id for n in ast.walk(target) if isinstance(n, ast.Name)]
        if value is None:
            return
        tainted = self._is_tainted(value)
        handle = self._is_stream_call(value) or (isinstance(value, ast.Name) and value.id in self.handles)
        for name in names:
            (self.tainted.add if tainted else self.tainted.discard)(name)
            (self.handles.add if handle else self.handles.discard)(name)

    def visit_Assign(self, node: ast.Assign) -> None:
        for t in node.targets:
            self._bind(t, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._bind(node.target, node.value)
        self.generic_visit(node)

    @staticmethod
    def _yields_content(node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        f = node.func
        name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
        return name in _CONTENT_READERS

    def visit_For(self, node: ast.For) -> None:
        # `for agent_file in subagents_dir.glob(...)` binds transcript PATHS;
        # `for line in tail_lines(p, N)` / `for raw in fh` bind CONTENT, and
        # reading a file named in a line is not reading the transcript.
        if (
            self._is_tainted(node.iter)
            and not self._is_handle_expr(node.iter)
            and not self._yields_content(node.iter)
        ):
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name):
                    self.tainted.add(n.id)
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if item.optional_vars is not None:
                self._bind(item.optional_vars, item.context_expr)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        # `read_text` / `read_bytes` / `readlines` on any tainted path or
        # handle; an unbounded `.read()`, `json.load`, `"".join` and the
        # draining builtins only on a STREAM opened on the transcript —
        # `list(dict.fromkeys(samples))` over data that merely derived from
        # the transcript is not a file read.
        if isinstance(func, ast.Attribute):
            if func.attr in _MATERIALIZING_METHODS and (
                self._is_tainted(func.value) or self._is_handle_expr(func.value)
            ):
                self.offences.append(node.lineno)
            elif func.attr in _MATERIALIZING_FUNCS and any(self._is_tainted(a) for a in node.args):
                self.offences.append(node.lineno)  # `_transcript.load_transcript(p)`
            elif func.attr == "read" and not node.args and self._is_handle_expr(func.value):
                self.offences.append(node.lineno)
            elif func.attr == "join" and node.args and self._is_handle_expr(node.args[0]):
                self.offences.append(node.lineno)
            elif (
                func.attr == "load"
                and isinstance(func.value, ast.Name)
                and func.value.id == "json"
                and node.args
                and self._is_handle_expr(node.args[0])
            ):
                self.offences.append(node.lineno)
        elif isinstance(func, ast.Name):
            if func.id in _MATERIALIZING_FUNCS and any(self._is_tainted(a) for a in node.args):
                self.offences.append(node.lineno)
            elif func.id in _DRAINING_FUNCS and node.args and self._is_handle_expr(node.args[0]):
                if not (func.id == "deque" and any(k.arg == "maxlen" for k in node.keywords)):
                    self.offences.append(node.lineno)
            elif func.id not in _STREAM_FUNCS:
                flow: dict = {}
                for i, a in enumerate(node.args):
                    if self._is_handle_expr(a):
                        flow[i] = "handle"
                    elif self._is_tainted(a):
                        flow[i] = "taint"
                for k in node.keywords:
                    if k.arg is None:
                        continue
                    if self._is_handle_expr(k.value):
                        flow[k.arg] = "handle"
                    elif self._is_tainted(k.value):
                        flow[k.arg] = "taint"
                if flow:
                    self.calls.append((func.id, flow))
        self.generic_visit(node)


def materializing_transcript_reads(source: str) -> list[int]:
    """Line numbers where `source` loads the whole transcript into memory."""
    tree = ast.parse(source)
    funcs = {
        n.name: n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    # A module constant bound to the key literal (`KEY = "transcript_path"`)
    # reads the key just as the literal does.
    key_names = {
        t.id
        for n in tree.body
        if isinstance(n, ast.Assign) and _mentions_key(n.value)
        for t in n.targets
        if isinstance(t, ast.Name)
    }

    def params(fn) -> list[str]:
        return [a.arg for a in fn.args.posonlyargs + fn.args.args]

    # Seed: every function whose body mentions the payload key, plus any
    # parameter literally named after the transcript.
    seeds: dict[str, tuple[set[str], set[str]]] = {}
    for name, fn in funcs.items():
        seeded = {p for p in params(fn) + [a.arg for a in fn.args.kwonlyargs] if "transcript" in p}
        if _mentions_key(fn) or (_names_in(fn) & key_names) or seeded:
            seeds[name] = (seeded, set())
    offences: set[int] = set()
    seen: set[tuple[str, frozenset[str], frozenset[str]]] = set()
    queue = list(seeds.items())
    while queue:
        name, (tainted, handles) = queue.pop()
        key = (name, frozenset(tainted), frozenset(handles))
        if key in seen:
            continue
        seen.add(key)
        walker = _Taint(tainted, handles, key_names)
        walker.visit(funcs[name])
        offences.update(walker.offences)
        for callee, flow in walker.calls:
            fn = funcs.get(callee)
            if fn is None:
                continue
            names = params(fn)
            kw = {a.arg for a in fn.args.kwonlyargs} | set(names)
            t_in, h_in = set(), set()
            for slot, kind in flow.items():
                pname = names[slot] if isinstance(slot, int) and slot < len(names) else (
                    slot if isinstance(slot, str) and slot in kw else None
                )
                if pname is None:
                    continue
                (h_in if kind == "handle" else t_in).add(pname)
            queue.append((callee, (t_in | h_in, h_in)))
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
    # Draining a streaming reader is the #1076 shape whatever the reader.
    ('p = payload.get("transcript_path")\n    events = list(iter_transcript(p))', True),
    ('p = payload.get("transcript_path")\n    events = [e for e in iter_transcript(p)]', False),  # a comprehension is not drained by name; documented gap
    ('p = payload.get("transcript_path")\n    with open(p) as fh:\n        body = "".join(fh)', True),
    ('p = payload.get("transcript_path")\n    with open(p) as fh:\n        deque(fh)', True),
    ('p = payload.get("transcript_path")\n    with open(p) as fh:\n        deque(fh, maxlen=400)', False),
    # read_bytes is read_text without the decode.
    ('p = payload.get("transcript_path")\n    Path(p).read_bytes()', True),
    # Module-qualified legacy loader.
    ('p = payload.get("transcript_path")\n    _transcript.load_transcript(p)', True),
    # A handle passed to a helper keeps its handle-ness there.
    ('p = payload.get("transcript_path")\n    with open(p) as fh:\n        slurp(fh)\n'
     'def slurp(fh):\n    return fh.read()', True),
    # Keyword-argument and positional-only flows.
    ('p = payload.get("transcript_path")\n    helper(path=p)\n'
     'def helper(path):\n    Path(path).read_text()', True),
    ('p = payload.get("transcript_path")\n    helper(1, p)\n'
     'def helper(a, /, path):\n    Path(path).read_text()', True),
    # The key read through a module constant.
    ('p = payload.get(KEY)\n    Path(p).read_text()\nKEY = "transcript_path"', True),
    # Reassignment clears the taint: a name reused for another file is clean.
    ('p = payload.get("transcript_path")\n    tail_lines(p, 400)\n    p = Path(cwd) / "marker.json"\n    p.read_text()', False),
    # A sibling subagent transcript found by globbing next to the root one IS one.
    ('p = payload.get("transcript_path")\n    for f in Path(p).with_suffix("").glob("agent-*.jsonl"):\n        f.read_text()', True),
    # A file NAMED IN the transcript is not the transcript.
    ('p = payload.get("transcript_path")\n    for line in tail_lines(p, 400):\n        Path(line.strip()).read_text()', False),
])
def test_the_detector_can_fail(body, expect):
    # Positive control: an empty offender list has to mean "no whole-file
    # reads", not "the walk stopped matching".
    head, _, tail = body.partition("\ndef ")
    trailer = ""
    if "\nKEY = " in head:
        head, _, trailer = head.partition("\nKEY = ")
        trailer = "\nKEY = " + trailer
    src = "def main(payload):\n    " + head + ("\n\ndef " + tail if tail else "") + trailer
    assert bool(materializing_transcript_reads(src)) is expect, src
