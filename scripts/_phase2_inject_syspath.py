#!/usr/bin/env python3
"""Phase 2: inject sys.path entry for hooks/_lib into every impl.py that
imports _hook_utils.

After Phase 2 the shared lib lives at hooks/_lib/_hook_utils.py instead of
hooks/_hook_utils.py, so each per-hook impl.py (depth = hooks/<role>/<name>/)
needs sys.path += ../../_lib to keep `import _hook_utils` resolving.

Transient — delete after Phase 2 PR merges.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "hooks"

INJECT_BLOCK = (
    "import sys as _sys\n"
    "from pathlib import Path as _Path\n"
    "_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / \"_lib\"))\n"
)

INJECT_MARKER = "_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / \"_lib\"))"


DEAD_SIBLING_RE = re.compile(
    r"^\s*sys\.path\.insert\(0,\s*os\.path\.dirname\(os\.path\.abspath\(__file__\)\)\)\s*\n",
    re.M,
)
DEAD_COMMENT_RE = re.compile(
    r"^\s*#\s*Resolve sibling\s+`?_hook_utils\.py`?\s+regardless of cwd at invocation time\.\s*\n",
    re.M,
)


def needs_injection(text: str) -> bool:
    if INJECT_MARKER in text:
        return False  # already injected
    return bool(re.search(r"^\s*(?:from\s+_hook_utils\b|import\s+_hook_utils\b)", text, re.M))


def inject(path: Path) -> bool:
    text = path.read_text()
    if not needs_injection(text):
        return False
    # Drop legacy sibling-dir sys.path block + its leading comment.
    text = DEAD_SIBLING_RE.sub("", text)
    text = DEAD_COMMENT_RE.sub("", text)
    # Insert before the first `from _hook_utils` / `import _hook_utils`.
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    injected = False
    for line in lines:
        if not injected and re.match(r"^\s*(?:from\s+_hook_utils\b|import\s+_hook_utils\b)", line):
            out.append(INJECT_BLOCK)
            out.append(line)
            injected = True
        else:
            out.append(line)
    path.write_text("".join(out))
    return True


def cleanup_only(path: Path) -> bool:
    """Strip the legacy sibling-dir sys.path block + comment from an already-
    injected impl.py. Safe to re-run; returns True if file changed."""
    text = path.read_text()
    new = DEAD_SIBLING_RE.sub("", text)
    new = DEAD_COMMENT_RE.sub("", new)
    if new == text:
        return False
    path.write_text(new)
    return True


def main() -> int:
    injected: list[Path] = []
    cleaned: list[Path] = []
    for impl in HOOKS.glob("*/*/impl.py"):
        if not impl.is_file():
            continue
        if inject(impl):
            injected.append(impl)
        elif cleanup_only(impl):
            cleaned.append(impl)
    print(f"Injected sys.path into {len(injected)} impl.py files:")
    for p in injected:
        print(f"  {p.relative_to(REPO)}")
    print(f"Cleaned legacy sibling-dir block in {len(cleaned)} impl.py files:")
    for p in cleaned:
        print(f"  {p.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
