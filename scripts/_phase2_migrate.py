#!/usr/bin/env python3
"""Phase 2 migration helper for ADR-0001 (#422).

Creates hooks/_lib + 4 role subdirectories + 39 per-hook folders, then
git-moves each hook's impl into the new layout and git-rms the 35
hand-maintained source wrappers (and 2 multi-event wrapper variants).

Transient — delete this script and `git mv scripts/_phase2_migrate.py /dev/null`
in the final commit of the Phase 2 PR.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "hooks"

ROLE_MAP: dict[str, list[str]] = {
    "preflight-gate": [
        "block-ask-end-option",
        "block-gh-issue-create-without-dup-search",
        "block-gh-state-all",
        "block-manufactured-action-menu",
        "block-pr-without-caller-evidence",
        "block-pr-without-precommit-evidence",
        "block-sciomc-finding-commit",
        "commit-title-length-check",
        "cross-boundary-preflight",
        "gh-flag-verify",
        "gh-json-validator",
        "gh-label-verify",
        "pre-edit-protected-branch-guard",
        "pre-gh-pr-create-dedup-gate",
        "pre-merge-approval-gate",
        "session-intent",
        "side-effect-scan",
        "verify-commit-flag-override",
    ],
    "advisory-nudge": [
        "advisory-wrapper-signature-verify",
        "bash-worktree-existence-advisory",
        "cli-flag-incompat-advisory",
        "codex-review-route",                   # body-as-sh
        "count-assertion-verify",
        "external-api-literal-trigger",
        "external-write-falsify-check",         # opt-in, no manifest entry
        "external-write-path-existence-check",
        "jq-config-empty-dict-advisory",
        "memory-hint",
        "momentum-rule-retrieval-gate",
        "output-block-falsify-advisory",
        "path-probe-gate",
        "version-bump-evidence-check",
    ],
    "postuse-correction": [
        "builtin-task-postuse",
        "pre-edit-md-escape-advisory",          # multi-event (pre + post wrappers)
    ],
    "completion-verify": [
        "completion-signal-gate",               # wrapper-pair
        "completion-verify",                    # body-as-sh
        "retrospect-mix-check",                 # body-as-sh
        "strike-counter",                       # body-as-sh
    ],
}

BODY_AS_SH = {
    "codex-review-route",
    "completion-verify",
    "retrospect-mix-check",
    "strike-counter",
}
MULTI_EVENT = {"pre-edit-md-escape-advisory"}


def run(cmd: list[str]) -> None:
    print(f"$ {' '.join(cmd)}")
    subprocess.check_call(cmd, cwd=REPO)


def rel(p: Path) -> str:
    return str(p.relative_to(REPO))


def main() -> int:
    # Step 2: directory shells
    (HOOKS / "_lib").mkdir(parents=True, exist_ok=True)
    for role, names in ROLE_MAP.items():
        (HOOKS / role).mkdir(parents=True, exist_ok=True)
        for name in names:
            (HOOKS / role / name).mkdir(parents=True, exist_ok=True)

    # Step 3: shared lib
    src = HOOKS / "_hook_utils.py"
    if src.exists():
        run(["git", "mv", rel(src), rel(HOOKS / "_lib" / "_hook_utils.py")])

    # Step 3: hook impls + wrapper cleanup
    for role, names in ROLE_MAP.items():
        for name in names:
            target_dir = HOOKS / role / name
            if name in BODY_AS_SH:
                src_sh = HOOKS / f"{name}.sh"
                if src_sh.exists():
                    run(["git", "mv", rel(src_sh), rel(target_dir / "impl.sh")])
            elif name in MULTI_EVENT:
                src_py = HOOKS / f"{name}.py"
                if src_py.exists():
                    run(["git", "mv", rel(src_py), rel(target_dir / "impl.py")])
                for variant in (f"{name}-pre.sh", f"{name}-post.sh"):
                    src_sh = HOOKS / variant
                    if src_sh.exists():
                        run(["git", "rm", rel(src_sh)])
            else:
                src_py = HOOKS / f"{name}.py"
                if src_py.exists():
                    run(["git", "mv", rel(src_py), rel(target_dir / "impl.py")])
                src_sh = HOOKS / f"{name}.sh"
                if src_sh.exists():
                    run(["git", "rm", rel(src_sh)])

    return 0


if __name__ == "__main__":
    sys.exit(main())
