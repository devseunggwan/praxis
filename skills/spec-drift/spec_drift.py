#!/usr/bin/env python3
"""Report which spec requirements the current tree does not yet satisfy (#1005).

Reads `<praxis_specs_dir()>/NNNN-slug.md`, runs each requirement's `Verify:`
command, and classifies the result. Read-only: it never writes, commits, or
files anything — the same invariant `praxis:debt` carries.

The spec directory is supplied by `spec-drift`, the shell entry point that owns
the PRAXIS_HOME resolution; this module holds no default for it. Commands run
from the repository root of wherever the report was invoked, so `Verify:` lines
are repo-relative while the store they came from is not.

Only a `Verify:` line is ever executed. Backticks in a requirement's prose are
left alone on purpose: a well-written requirement quotes commands it wants
*rejected* as oracles (001's FR-001 cites `git check-ignore` to disqualify it),
so a tool that guessed between prose backticks would run the one command the
spec says not to trust.

Verdicts:
  implemented  the Verify command exited 0
  missing      it exited non-zero — command and output are printed
  UNKNOWN      no Verify line — the recommendation that closes it is printed
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

SPEC_GLOB = "[0-9][0-9][0-9][0-9]-*.md"

# A requirement opens a top-level list item: `- **FR-001**: …`. The label is
# left open (FR / SC / NFR / anything a spec introduces) so a new category does
# not silently drop out of the report.
REQ_RE = re.compile(r"^- \*\*([A-Z]{2,4}-\d+)\*\*:")
# `Verify:` sits in a nested item under its requirement. The command is the
# single backticked span; nested backticks are not supported and would be a
# spec-authoring error rather than a parse case to guess at.
VERIFY_RE = re.compile(r"^\s+- Verify:\s*`(.+)`\s*$")

IMPLEMENTED, MISSING, UNKNOWN = "implemented", "missing", "UNKNOWN"

UNKNOWN_HINT = (
    "add a `Verify:` line whose exit code reports this requirement "
    "(see praxis docs/spec-store.md -> Verification lines)"
)


def repo_root() -> Path:
    """Resolve the repository root, or exit with a readable message."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:  # git absent, or unusable
        sys.exit(f"spec-drift: cannot resolve repository root: {exc}")
    if proc.returncode != 0:
        sys.exit("spec-drift: not inside a git repository")
    return Path(proc.stdout.strip())


def parse_spec(path: Path) -> list[tuple[str, str | None, list[str]]]:
    """Return [(requirement id, Verify command or None, ignored commands)].

    A `Verify:` line binds to the requirement whose block it sits in, and a
    block ends at the next line that starts in column 0 — the requirement's own
    nested items and continuation prose are indented, so that boundary is the
    document's own. Binding to "the most recent requirement" instead let a
    nested `- Verify:` in a *later* section rebind the requirement above it and
    silently replace its command, which means running something the spec never
    put under that requirement.

    A second `Verify:` inside one block is an authoring error, not a case to
    guess at: the first binds and the rest are returned so the report can name
    them. Requirements are kept per occurrence, so a duplicated id carries its
    own command rather than sharing one.
    """
    found: list[tuple[str, str | None, list[str]]] = []
    in_block = False
    for line in path.read_text(encoding="utf-8").splitlines():
        req = REQ_RE.match(line)
        if req:
            found.append((req.group(1), None, []))
            in_block = True
            continue
        if not in_block:
            continue
        if line.strip() and not line[0].isspace():
            in_block = False
            continue
        verify = VERIFY_RE.match(line)
        if verify:
            rid, command, extras = found[-1]
            if command is None:
                found[-1] = (rid, verify.group(1), extras)
            else:
                extras.append(verify.group(1))
    return found


def run_verify(command: str, cwd: Path, timeout: int) -> tuple[int, str]:
    """Run one Verify command, returning (exit code, combined output).

    stdin is /dev/null, not the report's own (#1008). Inheriting it meant a
    command that reads stdin never saw EOF, burned the whole timeout, and was
    reported `missing (exit 124)` — a working implementation called
    unimplemented, with the verdict turning on whether the invoking shell
    happened to have an open stdin at all. docs/spec-store.md already requires
    a Verify command to terminate without input; this is that rule in code
    rather than in prose.
    """
    try:
        proc = subprocess.run(
            ["bash", "-c", command],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as expired:
        # Whatever the command managed to say before it hung is usually the
        # diagnosis; dropping it leaves a `missing` row carrying nothing but
        # the fact that it hung. The attributes come back as bytes even under
        # text=True, and either can be None.
        partial = _decode(expired.stdout) + _decode(expired.stderr)
        note = f"timed out after {timeout}s"
        return 124, f"{partial.strip()}\n{note}" if partial.strip() else note
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _decode(stream: bytes | str | None) -> str:
    """Bytes / str / None from a TimeoutExpired, as text."""
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return stream


def display_path(path: Path, root: Path) -> str:
    """Repo-relative path when the spec is inside the repo, absolute otherwise.

    The store normally sits under PRAXIS_HOME, outside the checkout, where
    `Path.relative_to` raises rather than returning something printable. The
    relative form survives only for a spec dir that happens to be inside the
    repo — which is what the tests' fixture trees are.
    """
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def report_spec(path: Path, root: Path, timeout: int) -> dict[str, int]:
    """Print one spec's report; return its verdict counts."""
    counts = {IMPLEMENTED: 0, MISSING: 0, UNKNOWN: 0}
    print(f"\n=== {display_path(path, root)} ===")
    requirements = parse_spec(path)
    if not requirements:
        print("  (no requirements found)")
        return counts

    for rid, command, ignored in requirements:
        for extra in ignored:
            # Never silently: a second Verify in one block means the author
            # expects a command to run that this report is not running.
            print(f"  warning      {rid}: second Verify line ignored: {extra}")
        if command is None:
            counts[UNKNOWN] += 1
            print(f"  {UNKNOWN:<12} {rid}")
            print(f"               {UNKNOWN_HINT}")
            continue
        # Printed before it runs, so the executed set is auditable from the
        # report alone rather than from trusting this program.
        print(f"  running      {rid}: {command}")
        code, output = run_verify(command, root, timeout)
        if code == 0:
            counts[IMPLEMENTED] += 1
            print(f"  {IMPLEMENTED:<12} {rid}")
        else:
            counts[MISSING] += 1
            print(f"  {MISSING:<12} {rid}  (exit {code})")
            for out_line in (output or "(no output)").splitlines():
                print(f"               {out_line}")
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report unmet requirements across the praxis spec store",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="per-command timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--spec-dir",
        required=True,
        help=(
            "the spec directory. Supplied by the `spec-drift` shell entry from "
            "praxis_specs_dir(); no default here, so the two cannot disagree"
        ),
    )
    args = parser.parse_args()

    root = repo_root()
    # Relative only when a caller passed one (the tests' in-repo fixtures);
    # the resolved store is absolute and `/` leaves it alone.
    spec_dir = root / str(args.spec_dir)
    # A README and the template may sit alongside specs and are not specs; the
    # numeric glob is what separates them.
    specs = sorted(spec_dir.glob(SPEC_GLOB))
    if not specs:
        print(f"spec-drift: no specs under {spec_dir}")
        return 0

    total = {IMPLEMENTED: 0, MISSING: 0, UNKNOWN: 0}
    for spec in specs:
        for verdict, n in report_spec(spec, root, args.timeout).items():
            total[verdict] += n

    print(
        f"\n{len(specs)} spec(s): "
        f"{total[IMPLEMENTED]} {IMPLEMENTED}, "
        f"{total[MISSING]} {MISSING}, "
        f"{total[UNKNOWN]} {UNKNOWN}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
