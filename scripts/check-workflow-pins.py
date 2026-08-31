#!/usr/bin/env python3
"""Invariant canary: workflow dependencies must stay pinned (issue #1171).

Every ``uses:`` in ``.github/workflows/*.yml`` must reference an action by an
immutable ref — a full 40-char commit SHA for repository actions, a
``@sha256:<64-hex digest>`` for ``docker://`` container actions. Tag refs
(``@v4``, ``:3.18``) are mutable, so an upstream re-tag can silently swap the
code CI runs (the tj-actions/changed-files supply-chain incident is the
canonical example). The convention is ``owner/repo@<sha> # <tag>``: the SHA is
what runs, the trailing comment documents the human-readable version for
reviewers and dependabot.

Every ``runs-on:`` must name a pinned runner image label (``ubuntu-24.04``),
never a floating ``*-latest`` alias — ``ubuntu-latest`` retargets to a new
image on GitHub's schedule, changing the preinstalled toolchain under CI
without a commit. GitHub runner labels are case-insensitive, so the match is
too. ``runs-on: ${{ matrix.os }}`` is the same float one indirection away, so
the expression is resolved back to the ``matrix:`` dimension it names and the
literals there are checked. Only dimensions the runner actually references
count as runner candidates: a ``matrix.release: [ubuntu-latest]`` carrying test
data, under a fixed ``runs-on``, is not a floating runner.

Inline tool installs are held to the same discipline: an ``npm install`` of
markdownlint-cli2 must carry ``@<exact version>`` and a ``pip install`` of ruff
must carry ``==<exact version>``, wherever either appears in a workflow. The
pin's *presence* is the invariant; the version itself is bumped freely. A
``run:`` body is split into its individual commands and each command into
words, and the pin is looked for only among the packages that command installs
— matching the whole line instead let an option before the verb
(``pip --no-cache-dir install ruff``) go unrecognised as an install, and let a
pin-shaped token in a neighbouring command (``pip install ruff; echo
ruff==0.15.8``) satisfy the check.

Workflows are parsed with PyYAML rather than scanned line by line, because a
line-oriented reader cannot see the shapes that matter: a block-scalar or
block-list ``runs-on``, a flow-mapping ``matrix: {os: [ubuntu-latest]}``, or a
backslash-continued install whose package name sits on the next physical line.
Each of those reads as clean under a regex scan while selecting a floating
runner or an unpinned tool. Parsing also retires the block-scalar special case
outright: a ``run:`` body is a string value, so a ``uses:`` written inside one
is prose by construction and can never be mistaken for a workflow key.

Anything the checker cannot resolve is drift, not a pass — an expression whose
value cannot be established, a ``runs-on`` that is neither a scalar nor a
sequence of scalars, a matrix dimension the runner names but that holds no
literals, a matrix candidate that is itself an expression (resolving runs-on
to ``matrix.os`` buys nothing if the dimension's own entries are expressions),
a file that does not parse. Failing closed is the point of a canary:
a silent skip is indistinguishable from a clean tree.

Run standalone or via ``scripts/run-tests.sh``. Exit 0 + a verified count on
a clean tree; exit 1 listing each offending ``file:line`` on drift. Unit
tests: ``tests/test_check_workflow_pins.py``.
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DOCKER_DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")

# Floating runner aliases GitHub retargets over time. Matches the bare aliases
# (ubuntu-latest) and their sized variants (windows-latest-8-cores). Runner
# labels are case-insensitive on GitHub, so `Ubuntu-Latest` floats identically.
FLOATING_RUNNER_RE = re.compile(r"^(ubuntu|macos|windows)-latest(-|$)", re.IGNORECASE)

# A value that is exactly one ${{ ... }} expression and nothing else.
LONE_EXPR_RE = re.compile(r"^\s*\$\{\{(?P<expr>.*)\}\}\s*$", re.DOTALL)
MATRIX_REF_RE = re.compile(r"^matrix\.(?P<dim>[A-Za-z_][\w-]*)$")
QUOTED_LITERAL_RE = re.compile(r"^(?P<q>['\"])(?P<val>[^'\"]*)(?P=q)$")

# Inline tool installs that must carry an exact pin wherever they appear. The
# executables are matched as whole words after a path strip, so `/usr/bin/pip3`
# counts and `mypip` does not.
NPM_VERBS = ("install", "i", "add")
NPM_EXE = "npm"
PIP_EXE_RE = re.compile(r"^pip[0-9]*(?:\.[0-9]+)?$")
PYTHON_EXE_RE = re.compile(r"^python[0-9]*(?:\.[0-9]+)?$")
# An exact npm version is a complete semver: `@0` and `@0.23` are ranges, and a
# range is exactly what the pin exists to forbid.
NPM_PINNED_RE = re.compile(
    r"markdownlint-cli2@\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?(?![\w.-])"
)
PIP_RUFF_RE = re.compile(r"\bruff\b")
# Same rule as npm: `ruff==0` and `ruff==0.15` are ranges, not exact versions.
PIP_PINNED_RE = re.compile(
    r"\bruff==\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?(?![\w.-])"
)


class Findings:
    """Accumulator for drift messages plus the count of values inspected."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.checked = 0

    def add(self, message: str) -> None:
        self.messages.append(message)


def _line(node: yaml.Node) -> int:
    return node.start_mark.line + 1


def _map_get(node: yaml.Node | None, key: str) -> yaml.Node | None:
    if not isinstance(node, yaml.MappingNode):
        return None
    for key_node, value_node in node.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
            return value_node
    return None


def _scalar_items(node: yaml.Node) -> list[yaml.ScalarNode] | None:
    """Scalars of a scalar-or-sequence-of-scalars node; None if neither shape."""
    if isinstance(node, yaml.ScalarNode):
        return [node]
    if isinstance(node, yaml.SequenceNode):
        items: list[yaml.ScalarNode] = []
        for item in node.value:
            if not isinstance(item, yaml.ScalarNode):
                return None
            items.append(item)
        return items
    return None


def _strip_comment(line: str) -> str:
    """Drop a `# ...` shell comment, ignoring `#` inside quotes.

    A pin that appears only in a comment is not a pin: `pip install ruff #
    ruff==0.15.8` installs whatever version is newest.
    """
    quote: str | None = None
    for i, ch in enumerate(line):
        if quote is not None:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line


def _logical_lines(script: str) -> list[str]:
    """Split a shell snippet into logical lines, joining `\\` continuations."""
    stripped = "\n".join(_strip_comment(line) for line in script.splitlines())
    joined = re.sub(r"\\\n[ \t]*", " ", stripped)
    return [line for line in joined.splitlines() if line.strip()]


def _split_commands(line: str) -> list[str]:
    """Split a logical line into its commands on ``; | & && ||``, honoring quotes.

    Without this, a pin-shaped token anywhere on the line satisfies the check —
    ``pip install ruff; echo ruff==0.15.8`` installs an unpinned ruff while the
    pin the canary sees belongs to a different command entirely.
    """
    commands: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(line):
        char = line[index]
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            current.append(char)
        elif char in ";|&":
            commands.append("".join(current))
            current = []
            # `&&` and `||` are one separator, not two.
            if index + 1 < len(line) and line[index + 1] == char:
                index += 1
        else:
            current.append(char)
        index += 1
    commands.append("".join(current))
    return [command for command in commands if command.strip()]


def _words(command: str) -> list[str]:
    """Split one command into words, dropping the quotes around each."""
    try:
        return shlex.split(command)
    except ValueError:
        # Unbalanced quotes: fall back rather than lose the command entirely.
        return command.split()


def _basename(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def _npm_install_args(words: list[str]) -> list[str] | None:
    """Package arguments of an npm install command, or None if not one."""
    for index, word in enumerate(words):
        if _basename(word) != NPM_EXE:
            continue
        rest = words[index + 1 :]
        for verb in NPM_VERBS:
            if verb in rest:
                return rest[rest.index(verb) + 1 :]
        return None
    return None


def _pip_install_args(words: list[str]) -> list[str] | None:
    """Package arguments of a pip install command, or None if not one.

    Scanning for the executable and the verb separately is what makes an option
    between them (``pip --no-cache-dir install ruff``) still read as an install;
    an adjacency regex misses it and lets an unpinned install through.
    """
    rest: list[str] | None = None
    for index, word in enumerate(words):
        base = _basename(word)
        if PIP_EXE_RE.fullmatch(base):
            rest = words[index + 1 :]
            break
        if PYTHON_EXE_RE.fullmatch(base):
            tail = words[index + 1 :]
            if "-m" not in tail:
                continue
            module_at = tail.index("-m") + 1
            if module_at < len(tail) and PIP_EXE_RE.fullmatch(_basename(tail[module_at])):
                rest = tail[module_at + 1 :]
                break
    if rest is None or "install" not in rest:
        return None
    return rest[rest.index("install") + 1 :]


def check_uses(value: str, where: str) -> str | None:
    """Return a drift message for an unpinned ``uses:`` value, else None."""
    ref = value.strip()
    if "${{" in ref:
        return (
            f"{where}: uses '{ref}' is built from an expression — the ref it "
            f"resolves to cannot be verified; write the SHA literally"
        )
    if ref.startswith("./"):
        # Local action: versioned by the enclosing commit itself — no ref to pin.
        return None
    if ref.startswith("docker://"):
        # Container action: the immutable form is a sha256 digest, not a git SHA.
        if DOCKER_DIGEST_RE.search(ref):
            return None
        return (
            f"{where}: docker action '{ref}' is not pinned to an immutable "
            f"digest (use docker://image@sha256:<64-hex digest>)"
        )
    if "@" not in ref:
        return f"{where}: uses '{ref}' carries no @ref at all — pin a 40-char commit SHA"
    sha = ref.rsplit("@", 1)[1]
    if not SHA_RE.fullmatch(sha):
        return (
            f"{where}: uses '{ref}' is not pinned to a 40-char commit SHA "
            f"(got ref '{sha}'; use owner/repo@<sha> # <tag>)"
        )
    return None


def _floating(label: str, where: str, origin: str) -> str | None:
    if FLOATING_RUNNER_RE.match(label.strip()):
        return (
            f"{where}: {origin} '{label.strip()}' floats with GitHub's image "
            f"rollout — pin an explicit image label (e.g. ubuntu-24.04)"
        )
    return None


def _matrix_literals(job: yaml.Node, dim: str) -> list[yaml.ScalarNode] | None:
    """Literal scalars a matrix dimension can take, or None if unresolvable."""
    matrix = _map_get(_map_get(job, "strategy"), "matrix")
    if matrix is None:
        return None
    found: list[yaml.ScalarNode] = []
    direct = _map_get(matrix, dim)
    if direct is not None:
        items = _scalar_items(direct)
        if items is None:
            return None
        found.extend(items)
    include = _map_get(matrix, "include")
    if isinstance(include, yaml.SequenceNode):
        for entry in include.value:
            value = _map_get(entry, dim)
            if value is None:
                continue
            if not isinstance(value, yaml.ScalarNode):
                return None
            found.append(value)
    return found or None


def _check_runner_scalar(
    node: yaml.ScalarNode, job: yaml.Node, rel: str, out: Findings
) -> None:
    """Check one runs-on scalar, resolving a lone ${{ }} expression."""
    where = f"{rel}:{_line(node)}"
    raw = node.value.strip()
    out.checked += 1

    if "${{" not in raw:
        drift = _floating(raw, where, "runs-on")
        if drift:
            out.add(drift)
        return

    match = LONE_EXPR_RE.match(raw)
    if not match:
        out.add(
            f"{where}: runs-on '{raw}' interpolates an expression into a larger "
            f"string — the resulting label cannot be verified"
        )
        return

    expr = match.group("expr").strip()
    literal = QUOTED_LITERAL_RE.match(expr)
    if literal:
        drift = _floating(literal.group("val"), where, "runs-on")
        if drift:
            out.add(drift)
        return

    ref = MATRIX_REF_RE.match(expr)
    if not ref:
        out.add(
            f"{where}: runs-on expression '{expr}' resolves to neither a literal "
            f"nor a matrix dimension — its runner cannot be verified"
        )
        return

    dim = ref.group("dim")
    literals = _matrix_literals(job, dim)
    if literals is None:
        out.add(
            f"{where}: runs-on references matrix.{dim}, but that dimension holds "
            f"no literal labels — its runner cannot be verified"
        )
        return
    for item in literals:
        out.checked += 1
        candidate = item.value.strip()
        item_where = f"{rel}:{_line(item)}"
        if "${{" in candidate:
            # The indirection only moved: resolving runs-on to matrix.<dim> is
            # worth nothing if the dimension's own entries are expressions.
            out.add(
                f"{item_where}: matrix.{dim} candidate '{candidate}' is built "
                f"from an expression — the runner it resolves to cannot be "
                f"verified; write the label literally"
            )
            continue
        drift = _floating(candidate, item_where, f"matrix.{dim} value")
        if drift:
            out.add(drift)


def check_tool_pins(script: str, where: str, out: Findings) -> None:
    """Assert inline installs of the pinned tools carry an exact version.

    Each logical line is split into its individual commands and each command
    into words, and the pin is then looked for only among the packages that
    command actually installs. Matching the whole line instead let two evasions
    through: an option before the verb went unrecognised as an install, and a
    pin-shaped token in a neighbouring command satisfied the check.
    """
    for line in _logical_lines(script):
        for command in _split_commands(line):
            words = _words(command)

            npm_args = _npm_install_args(words)
            if npm_args is not None and any("markdownlint-cli2" in a for a in npm_args):
                out.checked += 1
                if not any(NPM_PINNED_RE.search(a) for a in npm_args):
                    out.add(
                        f"{where}: npm install of markdownlint-cli2 is unpinned — "
                        f"use markdownlint-cli2@<exact version>"
                    )

            pip_args = _pip_install_args(words)
            if pip_args is not None and any(PIP_RUFF_RE.search(a) for a in pip_args):
                out.checked += 1
                if not any(PIP_PINNED_RE.search(a) for a in pip_args):
                    out.add(
                        f"{where}: pip install of ruff is unpinned — "
                        f'use "ruff==<exact version>"'
                    )


def _check_step(step: yaml.Node, rel: str, out: Findings) -> None:
    uses = _map_get(step, "uses")
    if isinstance(uses, yaml.ScalarNode):
        out.checked += 1
        drift = check_uses(uses.value, f"{rel}:{_line(uses)}")
        if drift:
            out.add(drift)
    run = _map_get(step, "run")
    if isinstance(run, yaml.ScalarNode):
        check_tool_pins(run.value, f"{rel}:{_line(run)}", out)


def _check_job(job: yaml.Node, rel: str, out: Findings) -> None:
    # A reusable-workflow call carries `uses:` at job level, not inside steps.
    uses = _map_get(job, "uses")
    if isinstance(uses, yaml.ScalarNode):
        out.checked += 1
        drift = check_uses(uses.value, f"{rel}:{_line(uses)}")
        if drift:
            out.add(drift)

    runs_on = _map_get(job, "runs-on")
    if runs_on is not None:
        items = _scalar_items(runs_on)
        if items is None:
            out.add(
                f"{rel}:{_line(runs_on)}: runs-on is neither a scalar nor a "
                f"sequence of scalars — its runner cannot be verified"
            )
        else:
            for item in items:
                _check_runner_scalar(item, job, rel, out)

    steps = _map_get(job, "steps")
    if isinstance(steps, yaml.SequenceNode):
        for step in steps.value:
            _check_step(step, rel, out)


def scan_file(path: Path, rel: str, out: Findings) -> None:
    """Scan one workflow, appending findings to ``out``."""
    try:
        with path.open(encoding="utf-8") as handle:
            root = yaml.compose(handle)
    except yaml.YAMLError as exc:
        out.add(
            f"{rel}: does not parse as YAML ({exc.__class__.__name__}) — cannot verify"
        )
        return
    if root is None:
        out.add(f"{rel}: is empty — cannot verify")
        return
    jobs = _map_get(root, "jobs")
    if not isinstance(jobs, yaml.MappingNode):
        out.add(f"{rel}: has no jobs: mapping — cannot verify")
        return
    for _, job in jobs.value:
        _check_job(job, rel, out)


def check(workflows_dir: Path | None = None) -> tuple[list[str], int]:
    """Scan every workflow; return (drift messages, count of checked values)."""
    if workflows_dir is None:
        workflows_dir = WORKFLOWS
    out = Findings()
    for path in sorted(
        list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml"))
    ):
        try:
            rel = str(path.relative_to(workflows_dir.parents[1]))
        except ValueError:
            rel = str(path)
        scan_file(path, rel, out)
    if out.checked == 0:
        out.add(
            f"no uses:/runs-on: values found under {workflows_dir} — "
            f"canary is scanning the wrong place"
        )
    return out.messages, out.checked


def main() -> int:
    drifts, checked = check()
    if drifts:
        print("workflow-pin check FAILED:")
        for d in drifts:
            print(f"  - {d}")
        return 1
    print(f"workflow-pin check OK ({checked} uses:/runs-on:/install values verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
