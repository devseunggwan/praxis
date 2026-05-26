#!/usr/bin/env python3
"""Phase 2 helper: re-shard hook tests into tests/hooks/<role>/ and rewrite
hook path references inside each test from hooks/<name>.{sh,py} →
hooks/<role>/<name>/impl.{sh,py}.

Reads role assignments from hooks/manifest.json. Test files that do not
correspond to any registered hook (non-hook tests, opt-in hook tests) stay
in tests/ flat.

Transient — delete after Phase 2 PR merges.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "tests"
HOOKS = REPO / "hooks"
MANIFEST = HOOKS / "manifest.json"

# Tests for opt-in hooks that have a hook directory but no manifest entry.
OPT_IN_HOOK_TO_ROLE = {
    "external-write-falsify-check": "advisory-nudge",
}

# Tests that are NOT hook tests; stay in tests/ flat.
NON_HOOK_TESTS = {
    "test_build_hosts_filter.sh",
    "test_cmux_browser.sh",
    "test_critic_pre_lock_probe.sh",
    "test_retrospect_falsify_recommended.sh",
    "test_retrospect_routing.sh",
    "test_hook_utils.sh",
}


def load_role_map() -> dict[str, str]:
    manifest = json.loads(MANIFEST.read_text())
    role_map: dict[str, str] = dict(OPT_IN_HOOK_TO_ROLE)
    for entry in manifest["hooks"]:
        role_map[entry["name"]] = entry["role"]
    return role_map


def run(cmd: list[str]) -> None:
    print(f"$ {' '.join(cmd)}")
    subprocess.check_call(cmd, cwd=REPO)


def hook_name_from_test(test_file: Path) -> str:
    """test_block_gh_state_all.sh → block-gh-state-all
    test_pre_edit_md_escape_advisory.sh → pre-edit-md-escape-advisory
    """
    stem = test_file.stem  # test_block_gh_state_all
    assert stem.startswith("test_"), stem
    return stem[len("test_"):].replace("_", "-")


def find_test_files() -> list[Path]:
    files: list[Path] = []
    for p in TESTS.glob("test_*.sh"):
        files.append(p)
    for p in (TESTS / "hooks").glob("test_*.sh"):
        files.append(p)
    for p in (TESTS / "hooks").glob("test_*.py"):
        files.append(p)
    return files


PATH_RE = re.compile(r"(\$\w+_DIR/hooks/)([a-z0-9][a-z0-9_-]*)\.(sh|py)\b")


def rewrite_hook_paths(text: str, role_map: dict[str, str]) -> tuple[str, int]:
    count = 0

    def sub(m: re.Match[str]) -> str:
        nonlocal count
        prefix = m.group(1)
        stem = m.group(2)
        ext = m.group(3)
        # Multi-event variants normalize to the shared impl (single impl.py).
        base = stem
        for suffix in ("-pre", "-post"):
            if stem.endswith(suffix):
                base = stem[: -len(suffix)]
                break
        role = role_map.get(base)
        if role is None:
            return m.group(0)  # not a registered hook — leave alone
        # body-as-sh hooks (codex-review-route, completion-verify, etc.)
        # have impl.sh; everything else has impl.py. We can't tell from
        # the test reference alone — trust the test's extension as ground
        # truth (tests reference the actual impl file extension).
        count += 1
        return f"{prefix}{role}/{base}/impl.{ext}"

    new_text = PATH_RE.sub(sub, text)
    return new_text, count


def determine_target_path(test_file: Path, role_map: dict[str, str]) -> Path | None:
    """Return the new path for this test, or None if it shouldn't move."""
    if test_file.name in NON_HOOK_TESTS:
        return None
    hook = hook_name_from_test(test_file)
    role = role_map.get(hook)
    if role is None:
        # Unknown hook — leave in current location, but warn
        print(f"  WARN: {test_file.relative_to(REPO)} → unknown hook {hook!r}", file=sys.stderr)
        return None
    target_dir = TESTS / "hooks" / role
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / test_file.name


def main() -> int:
    role_map = load_role_map()
    moved = 0
    rewritten = 0

    # ROOT_DIR depth: test_*.sh sets ROOT_DIR from SCRIPT_DIR by climbing
    # parent directories. Re-sharding into tests/hooks/<role>/ moves tests
    # one level deeper — adjust the climb.
    depth_map: dict[Path, int] = {}

    files = find_test_files()
    # Phase 1: rewrite content first (before moves), so the diff is small
    # per file and git mv preserves rename detection.
    for f in files:
        text = f.read_text()
        new_text, count = rewrite_hook_paths(text, role_map)
        depth_map[f] = count
        if count and new_text != text:
            f.write_text(new_text)
            rewritten += 1

    # Phase 2: move into role dir
    for f in files:
        target = determine_target_path(f, role_map)
        if target is None:
            continue
        if target.resolve() == f.resolve():
            continue
        run(["git", "mv", str(f.relative_to(REPO)), str(target.relative_to(REPO))])
        moved += 1

    print(f"\nrewrote hook paths in {rewritten} test files; moved {moved} test files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
