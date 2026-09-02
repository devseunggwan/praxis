#!/usr/bin/env python3
"""Release guard: a commit release-please silently dropped must turn CI red.

release-please parses every commit since the last release tag. A commit whose
message its parser rejects is logged as ``commit could not be parsed:`` and
then **skipped** — the run still ends ``completed/success`` with ``PR ...
remained the same``, so the release ships with that entry missing from the
CHANGELOG and nothing on the CI surface says so (issue #1228; three occurrences
across v7.13.0 and v7.14.0, each found only by a human opening the log).

This guard checks the *outcome*, not the parser. For every commit since the
last released tag whose Conventional Commit type is declared non-hidden in
``release-please-config.json``, it requires a corresponding entry on the
pending release branch's CHANGELOG. A parse failure removes exactly that entry,
so the guard goes red on it — and it stays red for any other cause with the
same effect (a type mapping that stops matching, a range boundary that moves),
which is why the oracle is deliberately the changelog rather than the log line.

Why a new check and not a widening of the #750 guard: that guard
(``scripts/check-changelog-completeness.sh``, a "N PRs since X.Y.Z" count
recomputation) was **deleted** in #753 when release-please was adopted, on the
stated ground that "release-please generates the changelog from commits, so
undercount is structurally impossible". #1228 is that premise failing. There is
no longer a guard to widen; this file restores the role under the new pipeline,
where the claim to check is per-commit presence rather than a declared count.

Matching is by full commit sha OR by the ``([#N](`` link release-please writes
for the entry's own pull request — it emits both, and either alone is sufficient
evidence the commit was not dropped. Two are accepted rather than one so that a
config change to either link style cannot turn this guard into a false red.

Skipped deliberately:

* types declared ``hidden: true`` (``chore``/``test``/``style`` here) and types
  absent from ``changelog-sections`` entirely — release-please omits both by
  design, so requiring an entry would be a false red;
* a subject that is not Conventional Commits at all (a plain merge subject).

Fails open — exit 0 with a warning — when it cannot see enough to judge: a
missing release tag, a shallow clone, an unreadable config. A guard that blocks
main because it could not look is worse than the gap it closes.

Usage::

    scripts/check-changelog-coverage.py                    # CI: derive from git
    scripts/check-changelog-coverage.py \\
        --commits-file F --changelog-file F                # fixtures / control

``--commits-file`` holds one ``<sha> <subject>`` per line (``git log
--format='%H %s'``); ``--changelog-file`` holds the changelog text the entries
must appear in. Together they bypass every git call, which is how the test
suite and the historical positive control exercise the same code path.

Exit codes: 0 covered (or skipped), 1 a required commit has no entry,
2 usage / environment error.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RELEASE_BRANCH = "release-please--branches--main"

# `type(scope)!: subject`, with the scope and the `!` breaking marker optional.
SUBJECT_RE = re.compile(r"^(?P<type>[a-zA-Z]+)(?:\([^)]*\))?!?:\s")
# Trailing `(#1232)` — the squash-merge reference GitHub appends.
PR_RE = re.compile(r"\(#(?P<pr>\d+)\)\s*$")


class Commit:
    """One commit in the release range, reduced to what the guard needs."""

    def __init__(self, sha: str, subject: str) -> None:
        self.sha = sha
        self.subject = subject
        match = SUBJECT_RE.match(subject)
        self.type = match.group("type").lower() if match else None
        pr_match = PR_RE.search(subject)
        self.pr = pr_match.group("pr") if pr_match else None

    def is_covered_by(self, changelog: str) -> bool:
        if self.sha and self.sha in changelog:
            return True
        # `([#N](` is the entry's own reference. A bare `#N` is not enough: an
        # unrelated entry's trailing `closes [#1213](...)` would then cover for
        # a dropped #1213 — three such references sit in the pending release.
        return bool(self.pr) and f"([#{self.pr}](" in changelog

    def __str__(self) -> str:
        return f"{self.sha[:8]} {self.subject}"


def required_types(config_path: Path) -> set[str]:
    """Conventional Commit types release-please is configured to publish."""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    return {
        section["type"].lower()
        for section in config.get("changelog-sections", [])
        if isinstance(section, dict) and section.get("type") and not section.get("hidden")
    }


def parse_commits(text: str) -> list[Commit]:
    """Read ``<sha> <subject>`` lines into Commits, ignoring blank lines."""
    commits = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        sha, _, subject = line.partition(" ")
        commits.append(Commit(sha, subject.strip()))
    return commits


def find_missing(commits: list[Commit], changelog: str, types: set[str]) -> list[Commit]:
    return [c for c in commits if c.type in types and not c.is_covered_by(changelog)]


def git(args: list[str], repo_root: Path) -> tuple[int, str]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, result.stdout


def resolve_release_ref(branch: str, repo_root: Path) -> str | None:
    for ref in (f"origin/{branch}", branch):
        code, _ = git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], repo_root)
        if code == 0:
            return ref
    return None


def added_changelog_lines(release_ref: str, changelog_path: str, repo_root: Path) -> str:
    """The changelog text the release branch adds on top of HEAD.

    Scoping to the diff, rather than the whole file, keeps a stale reference to
    the same number in an older section from covering for a dropped commit.
    """
    code, diff = git(
        ["diff", f"HEAD:{changelog_path}", f"{release_ref}:{changelog_path}"], repo_root
    )
    if code != 0:
        return ""
    return "\n".join(line[1:] for line in diff.splitlines() if line.startswith("+"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--changelog-path", default="CHANGELOG.md")
    parser.add_argument("--release-branch", default=DEFAULT_RELEASE_BRANCH)
    parser.add_argument(
        "--commits-file",
        type=Path,
        default=None,
        help="fixture hook: '<sha> <subject>' lines used instead of `git log`",
    )
    parser.add_argument(
        "--changelog-file",
        type=Path,
        default=None,
        help="fixture hook: changelog text used instead of the release branch",
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    config_path = args.config or repo_root / "release-please-config.json"
    manifest_path = args.manifest or repo_root / ".release-please-manifest.json"

    try:
        types = required_types(config_path)
    except (OSError, ValueError) as exc:
        print(f"warning: cannot read {config_path} ({exc}); skipping", file=sys.stderr)
        return 0
    if not types:
        print(f"warning: no non-hidden changelog-sections in {config_path}; skipping", file=sys.stderr)
        return 0

    # --- commits in the release range ---------------------------------------
    if args.commits_file:
        commits = parse_commits(args.commits_file.read_text(encoding="utf-8"))
        scope = args.commits_file.name
    else:
        try:
            version = json.loads(manifest_path.read_text(encoding="utf-8"))["."]
        except (OSError, ValueError, KeyError) as exc:
            print(f"warning: cannot read {manifest_path} ({exc}); skipping", file=sys.stderr)
            return 0
        tag = f"v{version}"
        scope = f"{tag}..HEAD"
        code, _ = git(["rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"], repo_root)
        if code != 0:
            print(f"warning: tag {tag} not found (shallow clone?); skipping", file=sys.stderr)
            return 0
        code, log = git(["log", f"{tag}..HEAD", "--no-merges", "--format=%H %s"], repo_root)
        if code != 0:
            print(f"warning: cannot enumerate {tag}..HEAD; skipping", file=sys.stderr)
            return 0
        commits = parse_commits(log)

    needed = [c for c in commits if c.type in types]
    if not needed:
        print(f"changelog-coverage OK — no publishable commit in {scope}")
        return 0

    # --- changelog the entries must appear in --------------------------------
    if args.changelog_file:
        changelog = args.changelog_file.read_text(encoding="utf-8")
    else:
        release_ref = resolve_release_ref(args.release_branch, repo_root)
        if release_ref is None:
            print(
                f"error: {len(needed)} publishable commit(s) in {scope} but no "
                f"'{args.release_branch}' branch — release-please opened no release PR:",
                file=sys.stderr,
            )
            for commit in needed:
                print(f"  {commit}", file=sys.stderr)
            return 1
        changelog = added_changelog_lines(release_ref, args.changelog_path, repo_root)

    missing = find_missing(commits, changelog, types)
    if missing:
        print(
            f"error: {len(missing)} of {len(needed)} publishable commit(s) in "
            f"{scope} have no CHANGELOG entry on the pending release:",
            file=sys.stderr,
        )
        for commit in missing:
            print(f"  {commit}", file=sys.stderr)
        print(
            "\nrelease-please dropped them. Check the release-please step's log for\n"
            "'commit could not be parsed:' — a rejected message is skipped inside an\n"
            "otherwise green run (issue #1228).",
            file=sys.stderr,
        )
        return 1

    print(f"changelog-coverage OK — all {len(needed)} publishable commit(s) in {scope} are covered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
