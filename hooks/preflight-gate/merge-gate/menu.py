#!/usr/bin/env python3
"""PreToolUse(AskUserQuestion) nudge: merge-decision menu missing review options.

A merge-decision AskUserQuestion menu (an option whose label names a merge /
squash action) is the last gate before an irreversible shared-state mutation.
At that gate the user often wants a *quality* lever — re-run codex-review-wrap,
re-run code-reviewer, or open a critic debate — before committing to the merge.
But the option set is authored ad-hoc by the agent each turn, so those levers
are routinely absent and the user must request them manually.

A PreToolUse hook cannot inject options into the rendered menu (it can only
allow / block / advise). So this hook nudges: when a merge-decision menu carries
NO review/debate option, it emits an advisory asking the agent to re-author the
menu with those options before surfacing it.

This is the sibling of `block-manufactured-action-menu` (which catches a merge
menu that is *redundant* — surfaced after the user already said "merge"). This
hook catches a merge menu that is *incomplete* — missing the review/debate
levers a pre-merge decision should offer.

Default mode: advisory (exit 0 + stderr nudge).
Strict mode (PRAXIS_MERGE_MENU_REVIEW_STRICT=1): block (exit 2).

Allow conditions (no nudge/block emitted):
  1. tool_name != "AskUserQuestion"
  2. No option label names a merge-decision action
  3. A review/debate option is already present (the menu already offers the lever)
  4. Malformed / partial payload (fail-open per project hook design contract)
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402
from _payload import read_payload  # type: ignore[import-not-found]  # noqa: E402

# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

# Merge-decision trigger tokens in an option label. These identify the menu as
# a go/no-go gate on a merge. Precision matters here (we do not want to nudge on
# unrelated menus), so English tokens use ASCII-letter lookaround: `merge`
# matches but `merged` / `merger` do not (followed by an ASCII letter), while
# the mixed-script label `squash 머지` still matches via the Korean token.
#
# The Korean token uses a negative lookahead to exclude two inflections that
# invert the gate's meaning: `머지된` (already-merged, a triage label) and
# `머지하지` (the `머지하지 말고` "do NOT merge" form). Both would otherwise fire a
# pre-merge nudge on a label that is not a merge-decision — the second case is a
# semantic inversion (an explicit no-merge label triggering a merge nudge). The
# lookahead keeps real gates matching: `머지`, `머지하기` (하기 ≠ 하지),
# `머지할까요`, `스쿼시 머지`, `머지 + 정리`.
_KO_MERGE_RE = re.compile(r"머지(?!된|하지)")
MERGE_TOKENS_EN = (
    "merge",
    "squash",
)

# Review / debate option tokens. Presence of any of these in a label means the
# menu already offers a quality lever — suppress the nudge. Detection uses
# SUBSTRING matching (not lookaround) on purpose: a false positive here only
# *suppresses* the advisory, which is the safe direction (never nag when a
# review option might exist). So `reviewer` inside `code-reviewer` correctly
# counts, as does `review` inside `codex-review-wrap`.
REVIEW_TOKENS_KO = (
    "리뷰",
    "검토",
)
REVIEW_TOKENS_EN = (
    "codex",
    "review",
    "reviewer",
    "critic",
    # `designer` and `security` are added for self-consistency with the
    # context-aware routing (L2). The UX family recommends the `designer` agent,
    # whose name has no `review`/`reviewer` substring. The security family's
    # example agent `security-reviewer` self-recognizes via `reviewer`, but a
    # security review option phrased without that suffix (`security audit`,
    # `security scan`) would not — so `security` is included per the issue SOT.
    # The remaining routed reviewers (review-data, review-service-design) already
    # match `review`. False suppression from these broad tokens only silences the
    # nudge (the safe direction), consistent with the substring-match rationale.
    "designer",
    "security",
)

# ---------------------------------------------------------------------------
# Context-aware reviewer routing (L2)
# ---------------------------------------------------------------------------
#
# When the menu IS a merge gate missing a review option, tailor WHICH reviewer
# the advisory recommends to the nature of the change being merged. The hook
# reads the branch's changed file paths and maps them to a reviewer family.
# This runs ONLY after the merge-decision + no-review early-returns, so a
# non-merge menu pays zero subprocess cost.
#
# Routing is path-based (cheap, no diff-content read). Multiple matches resolve
# by priority (security > data > design > ux) to a single recommendation —
# a merge gate should surface the single most consequential lens, not a wall of
# options. Each family carries a Hybrid recommendation string: a portable
# reviewer *type* plus a parenthetical example agent (the type conveys intent on
# every host; the example is a hint for hosts where that agent exists).
#
# Each entry: (key, recommendation_text, signal_label, path_substrings,
# path_suffixes). A file path matches the family if its lowercased form contains
# any of path_substrings OR ends with any of path_suffixes.
_REVIEWER_FAMILIES: tuple[tuple[str, str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "security",
        "security review / 보안 리뷰 (예: security-reviewer / oh-my-claudecode:security-reviewer)",
        "auth / token / secret / credential",
        ("auth", "token", "secret", "credential", "permission", "oauth"),
        (".pem", ".key"),
    ),
    (
        "data",
        "data/SQL review / 데이터/SQL 리뷰 (예: review-data)",
        "SQL / 적재 경로",
        # Substrings kept tight to avoid mis-routing non-data paths: bare `etl`
        # matched `getlist.py`, `/load` matched `src/loader.py`, and `template`
        # matched FE template dirs (`web/templates/Card.tsx`). `.sql` suffix +
        # `/sql/` + `migration` are the reliable data signals; a SQL template
        # without a `.sql` suffix falls to the generic advisory (safe direction).
        ("/sql/", "migration"),
        (".sql",),
    ),
    (
        "design",
        "design review / 설계 리뷰 (예: review-service-design)",
        "a new model / table / schema (신규 모델 / 테이블 / 스키마)",
        ("schema", "/models/", "/model/", "entity", "entities"),
        (".prisma", ".proto"),
    ),
    (
        "ux",
        "design/UX review / 디자인/UX 리뷰 (예: designer)",
        "FE / 컴포넌트",
        ("component", "/ui/", "/views/", "/pages/"),
        (".tsx", ".jsx", ".vue", ".css", ".scss"),
    ),
)


# Total git-subprocess budget MUST stay safely under the hook's manifest timeout
# (5s) so the Python fail-open path always runs before the hook runner kills the
# process — otherwise a hung/locked git (NFS stall, index.lock contention) would
# prevent the advisory/strict block from being emitted at all. Worst case for
# base resolution: 5 candidates × (1 merge-base + 1 is-ancestor) at most, but
# is-ancestor only runs when a candidate's merge-base differs (the rare
# multi-base case), so the worst ceiling is 5 merge-base + 4 is-ancestor = 9
# calls × _GIT_RESOLVE_TIMEOUT (0.3s) = 2.7s, + _GIT_DIFF_TIMEOUT (1.5s) = 4.2s
# < 5s manifest. Legit calls return in milliseconds; the timeouts only bound the
# pathological hang.
_GIT_RESOLVE_TIMEOUT = 0.3
_GIT_DIFF_TIMEOUT = 1.5


# Candidate base refs. origin/HEAD is the remote default; the explicit
# origin/main, origin/dev cover repos whose feature branches target a
# *non-default* base (branches target dev while the remote default is main). The
# local `main` / `dev` are the fallback for repos without a reachable origin
# (e.g. a fresh worktree or a local-only repo). Duplicate merge-bases (origin/main
# and main usually point at the same commit) collapse to one comparison.
_BASE_CANDIDATES = ("origin/HEAD", "origin/main", "origin/dev", "main", "dev")


def _merge_base(cwd: str, ref: str) -> str | None:
    """Return the merge-base sha of `ref` and HEAD, or None on miss/error.

    Doubles as an existence check: a non-existent ref makes `git merge-base`
    exit non-zero, which yields None (no separate rev-parse needed).
    """
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "merge-base", ref, "HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_RESOLVE_TIMEOUT,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _is_ancestor(cwd: str, ancestor: str, descendant: str) -> bool | None:
    """True if `ancestor` is an ancestor of `descendant`. None on error.

    `git merge-base --is-ancestor` exits 0 (yes), 1 (no), other (error).
    """
    try:
        rc = subprocess.run(
            ["git", "-C", cwd, "merge-base", "--is-ancestor", ancestor, descendant],
            capture_output=True,
            timeout=_GIT_RESOLVE_TIMEOUT,
        ).returncode
    except Exception:
        return None
    if rc == 0:
        return True
    if rc == 1:
        return False
    return None


def _resolve_base(cwd: str) -> str | None:
    """Resolve the base ref whose merge-base with HEAD is *nearest* (the true
    fork point), so `git diff <base>...HEAD` includes only this branch's own
    changes — not a non-default base's unique commits.

    Among the resolvable candidates, the nearest fork point is the merge-base
    that is a descendant of all the others. Returns that candidate's ref name,
    or None when nothing resolves (caller falls back to the static advisory).
    """
    best_ref: str | None = None
    best_mb: str | None = None
    for ref in _BASE_CANDIDATES:
        mb = _merge_base(cwd, ref)
        if mb is None:
            continue
        if best_mb is None:
            best_ref, best_mb = ref, mb
            continue
        if mb == best_mb:
            continue
        # best_mb is an ancestor of mb  ⇒  mb is the newer fork point  ⇒  ref is
        # nearer. (None on error → keep the current best, never regress.)
        if _is_ancestor(cwd, best_mb, mb) is True:
            best_ref, best_mb = ref, mb
    return best_ref


def _changed_files(cwd: str) -> list[str] | None:
    """Return the branch's changed file paths vs the resolved base.

    Fail-open: returns None on any failure (non-git cwd, no base ref, git
    error, timeout). None signals the caller to use the static advisory rather
    than a routed one — a diff we cannot read must never crash the hook or
    fabricate a routing decision.
    """
    if not cwd or not isinstance(cwd, str):
        return None
    base = _resolve_base(cwd)
    if base is None:
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "diff", "--name-only", f"{base}...HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_DIFF_TIMEOUT,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _route_reviewer(changed_files: list[str]) -> tuple[str, str, str] | None:
    """Map changed file paths to the highest-priority reviewer family.

    Pure function (no I/O) — the routing decision is unit-testable in isolation.
    Returns (key, recommendation_text, signal_label) for the first family (in
    priority order) that any changed path matches, or None when nothing matches
    (caller uses the static advisory).
    """
    lowered = [p.lower() for p in changed_files]
    for key, text, signal, substrings, suffixes in _REVIEWER_FAMILIES:
        for path in lowered:
            if any(s in path for s in substrings) or path.endswith(suffixes):
                return (key, text, signal)
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_option_labels(tool_input: dict) -> list[str]:
    """Walk questions[].options[].label and return all label strings.

    Tolerant of partial schemas — any missing field yields an empty list
    rather than an exception. The hook must never crash on a malformed payload.
    """
    labels: list[str] = []
    questions = tool_input.get("questions") or []
    if not isinstance(questions, list):
        return labels
    for q in questions:
        if not isinstance(q, dict):
            continue
        options = q.get("options") or []
        if not isinstance(options, list):
            continue
        for o in options:
            if not isinstance(o, dict):
                continue
            label = o.get("label")
            if isinstance(label, str):
                labels.append(label)
    return labels


def _en_token_present(token: str, lower_label: str) -> bool:
    """ASCII-letter lookaround match: token not flanked by ASCII letters.

    Rejects `merged` / `merger` for token `merge` while matching mixed-script
    labels like `squash 머지` (token followed by a Korean char, which `\\b`
    would not split because Python's Unicode-aware `\\w` treats Hangul as a
    word character).
    """
    pattern = r"(?<![a-z])" + re.escape(token.lower()) + r"(?![a-z])"
    return re.search(pattern, lower_label) is not None


def _is_merge_label(label: str) -> bool:
    """True if this single label names a merge-decision action.

    The Korean token uses a regex with a negative lookahead (excludes the
    meaning-inverting `머지된` / `머지하지` inflections). English tokens use
    ASCII-letter lookaround for precision.
    """
    lower = label.lower()
    if _KO_MERGE_RE.search(label):
        return True
    return any(_en_token_present(token, lower) for token in MERGE_TOKENS_EN)


def _has_merge_decision_option(labels: list[str]) -> bool:
    """True if any option label names a merge-decision action."""
    return any(_is_merge_label(label) for label in labels)


def _has_review_option(labels: list[str]) -> bool:
    """True if any option label already offers a review / debate lever.

    Uses substring matching for both KO and EN: a false positive only
    suppresses the advisory (safe direction — never nag when a review option
    may exist), so the broader match is intentional.
    """
    for label in labels:
        lower = label.lower()
        for token in REVIEW_TOKENS_KO:
            if token in label:
                return True
        for token in REVIEW_TOKENS_EN:
            if token in lower:
                return True
    return False


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

# Generic quality levers, always offered as the baseline set.
_GENERIC_LEVERS = """\
  - re-run codex-review-wrap (independent Codex pass)
  - re-run oh-my-claudecode:code-reviewer
  - open a critic debate (oh-my-claudecode:critic)"""


def _routed_lever_block(routed: tuple[str, str, str] | None) -> str:
    """Build the recommended-levers block.

    When `routed` is set (the branch diff matched a reviewer family), lead with
    the context-specific recommendation, then the generic levers as fallback.
    When None, emit only the generic levers (original behaviour).
    """
    if routed is None:
        return _GENERIC_LEVERS
    text, signal = routed[1], routed[2]
    return (
        f"  - {text}\n"
        f"    (this change touches {signal} — prioritise this lens)\n"
        f"{_GENERIC_LEVERS}"
    )


def _advisory_msg(routed: tuple[str, str, str] | None) -> str:
    return (
        "[advisory] This AskUserQuestion is a merge-decision menu (an option names a\n"
        "merge / squash action) but offers NO review / debate option.\n"
        "\n"
        "A pre-merge gate is the last point before an irreversible shared-state\n"
        "mutation. Before surfacing merge vs hold, consider re-authoring the menu to\n"
        "also offer the quality levers the user may want at this gate:\n"
        f"{_routed_lever_block(routed)}\n"
        "\n"
        "A PreToolUse hook cannot inject options into the rendered menu — re-issue the\n"
        "AskUserQuestion with these options added if a fresh review pass is appropriate.\n"
        "\n"
        "Strict mode disabled. Set PRAXIS_MERGE_MENU_REVIEW_STRICT=1 to block.\n"
    )


def _block_msg(routed: tuple[str, str, str] | None) -> str:
    return (
        "BLOCKED: merge-decision AskUserQuestion menu offers no review / debate option.\n"
        "\n"
        "A merge / squash option is present, but none of the options offers a quality\n"
        "lever (codex-review-wrap, code-reviewer, critic). A pre-merge gate is the last\n"
        "point before an irreversible shared-state mutation — surface the review/debate\n"
        "levers as menu options so the user can choose a fresh review before merging.\n"
        "\n"
        "Re-issue the AskUserQuestion with options for:\n"
        f"{_routed_lever_block(routed)}\n"
        "\n"
        "To opt out: unset PRAXIS_MERGE_MENU_REVIEW_STRICT (default is advisory).\n"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


@fail_open
def main() -> int:
    payload = read_payload()
    if payload is None:
        return 0

    if not isinstance(payload, dict):
        return 0
    if payload.get("tool_name") != "AskUserQuestion":
        return 0

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0

    labels = _collect_option_labels(tool_input)
    if not labels:
        return 0

    # Trigger only on a merge-decision menu.
    if not _has_merge_decision_option(labels):
        return 0

    # If a review/debate lever is already offered as a DISTINCT option, the menu
    # is complete — pass. Review detection runs only on non-merge labels: a merge
    # action label describing its change area (`merge security fix`,
    # `merge designer changes`) must NOT count as a review option, or the broad
    # `security`/`designer` suppression tokens would defeat the nudge on exactly
    # the high-stakes merges they describe. A review lever is a separate option,
    # not the merge action itself.
    review_candidate_labels = [label for label in labels if not _is_merge_label(label)]
    if _has_review_option(review_candidate_labels):
        return 0

    # Past the early-returns: this IS a merge gate missing a review option.
    # Only now pay the diff cost to route the recommendation to the change's
    # nature. A None result (non-git cwd, base unresolved, git error) falls back
    # to the static, family-agnostic advisory — never crashes, never fabricates.
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        cwd = os.getcwd()
    changed = _changed_files(cwd)
    routed = _route_reviewer(changed) if changed else None

    # Strict only on the documented `=1` value (spec mode table + advisory text
    # both contract `PRAXIS_MERGE_MENU_REVIEW_STRICT=1`). `.strip() == "1"`
    # matches the dominant codebase convention (destructive-bash-guard,
    # protected-paths-guard, push-remote-ref-verify, path-probe-gate) and avoids
    # the broader `not in (...)` form that would treat a disable-intent value
    # like `=no` as strict — contradicting the documented contract.
    strict_set = os.environ.get("PRAXIS_MERGE_MENU_REVIEW_STRICT", "").strip() == "1"

    if strict_set:
        sys.stderr.write(_block_msg(routed))
        return 2

    sys.stderr.write(_advisory_msg(routed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
