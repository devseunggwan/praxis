"""Rule 30 of check-plugin-manifests.py: SKILL.md shape (#1643).

docs/skill-authoring.md, skills/SKILL.md.tmpl and RUNTIME_CONSTRAINTS.md §5
state how a SKILL.md is shaped. Rules 11-13 cover runtime metadata, the skill
roster and trigger mirroring; this module covers the rest of what those docs
say that a script can decide without reading for meaning:

  30a  frontmatter `name` equals the directory name.
  30b  `description` holds no trigger phrase (`Triggers on`, `Use when`);
       routing phrases live in `when_to_use`.
  30c  `when_to_use` is present and carries a `Triggers on "..."` clause.
       A `disable-model-invocation: true` skill is exempt: the model never
       routes to it, so its `when_to_use` only tells the reader what the user
       types.
  30d  `description` <= 1,024 characters and `description` + `when_to_use`
       <= 1,536 (RUNTIME_CONSTRAINTS.md §5), measured whitespace-folded.
  30e  `allowed-tools` lists no bare `Bash` and no mutation command.
  30f  every `### Step` section ends on a `Done when:` line.
  30g  a SKILL.md over BODY_SPLIT_BYTES has a references/ directory.
  30h  `## Overview`, `## When to Use` and `## Process` are present.
       `disable-model-invocation` skills are exempt (user-typed commands with
       their own short shape: strike, strikes, reset-strikes).

30f-30h carry a frozen baseline of the skills that predate the rule (#1532
kept retrofitting out of scope). A baselined skill is skipped; a baselined
skill that now complies, or no longer exists, is drift, so a baseline only
shrinks. Rule 12's EXPECTED_SKILLS is the same freeze pattern.

Trigger keyword count outside TRIGGER_KEYWORD_RANGE is a warning, never a
failure: skill-authoring.md calls 3-6 a target range, not a limit.

Stdlib only, like the rest of the check: frontmatter fields are read with the
same line-anchored regex Rule 13e uses, then whitespace-folded, which is what
YAML `>` folding produces for this repo's single-paragraph fields.
"""
from __future__ import annotations

import re
from pathlib import Path

DESCRIPTION_MAX_CHARS = 1024
LISTING_MAX_CHARS = 1536
BODY_SPLIT_BYTES = 15 * 1024
TRIGGER_KEYWORD_RANGE = (3, 6)
REQUIRED_SECTIONS = ("Overview", "When to Use", "Process")
# The mutations skill-authoring.md names as never pre-approved.
MUTATION_COMMANDS = ("gh pr merge", "git push", "gh issue create")

DONE_WHEN_BASELINE: frozenset[str] = frozenset({
    "cmux-delegate",
    "cmux-recover-sessions",
    "codex-review-wrap",
    "merge-briefing",
    "perf-leak-review",
    "surface-enumeration",
    "tradeoff",
})
SPLIT_BASELINE: frozenset[str] = frozenset({
    "cmux-delegate",
    "merge-briefing",
    "upstream-bug-report",
})
SECTIONS_BASELINE: frozenset[str] = frozenset({
    "retrospect",
    "worktree-merge-cleanup",
})

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)
_DESCRIPTION_TRIGGER_RE = re.compile(r"\bTriggers on\b|\bUse (?:ONLY )?when\b", re.IGNORECASE)
_STEP_SPLIT_RE = re.compile(r"(?m)^###\s+Step\b")
_SECTION_END_RE = re.compile(r"(?m)^#{1,3}\s")
_FENCE_RE = re.compile(r"(?ms)^\s*```.*?^\s*```")


def _field(frontmatter: str, key: str) -> str | None:
    """Raw text of one top-level frontmatter field, or None when absent.

    A YAML block list may put its `- item` lines at column zero, so those
    lines still belong to the field.
    """
    match = re.search(
        rf"^{re.escape(key)}:(.*?)(?=^(?!-\s)\S|\Z)", frontmatter, re.DOTALL | re.MULTILINE
    )
    return None if match is None else match.group(1)


def _scalar(raw: str) -> str:
    """Whitespace-folded scalar value: block indicator and quotes removed."""
    text = re.sub(r"\A\s*[>|][+-]?\s*\n", "", raw)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1]
    return text


def _list_items(raw: str) -> list[str]:
    """Items of a YAML block list, flow list, or comma-separated scalar."""
    items = re.findall(r"(?m)^\s*-\s+(.+?)\s*$", raw)
    if items:
        return items
    inline = raw.strip().strip("[]")
    return [part.strip().strip("\"'") for part in inline.split(",") if part.strip()]


def _trigger_keywords(when_to_use: str) -> list[str]:
    positive = when_to_use.split("Do NOT activate on")[0]
    return re.findall(r'"([^"]+)"', positive)


def _steps_without_done_when(body: str) -> list[str]:
    body = _FENCE_RE.sub("", body)
    missing: list[str] = []
    for chunk in _STEP_SPLIT_RE.split(body)[1:]:
        title, _, rest = chunk.partition("\n")
        end = _SECTION_END_RE.search(rest)
        section = rest if end is None else rest[: end.start()]
        if "Done when" not in section:
            missing.append(f"Step {title.strip()}")
    return missing


def _missing_sections(body: str) -> list[str]:
    headings = [h.strip().lower() for h in re.findall(r"(?m)^##\s+(.+?)\s*$", body)]
    return [s for s in REQUIRED_SECTIONS if not any(h.startswith(s.lower()) for h in headings)]


def _baseline_drift(rule: str, skill: str, complies: bool) -> list[str]:
    if not complies:
        return []
    return [
        f"SKILL SHAPE BASELINE {skill}: now passes Rule {rule} - remove it from "
        f"the Rule {rule} baseline in scripts/skill_authoring.py"
    ]


def skill_shape_findings(skill_dir: Path) -> tuple[list[str], list[str]]:
    """(drifts, warnings) for one skills/<name>/ directory."""
    name = skill_dir.name
    skill_md = skill_dir / "SKILL.md"
    try:
        raw_bytes = skill_md.read_bytes()
    except OSError as exc:
        return [f"SKILL SHAPE {name}: SKILL.md unreadable ({exc})"], []
    text = raw_bytes.decode("utf-8", errors="replace")
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return [f"SKILL SHAPE {name}: no `---` frontmatter block (30a)"], []
    frontmatter, body = match.group(1), match.group(2)
    drifts: list[str] = []
    warnings: list[str] = []

    declared = _field(frontmatter, "name")
    if declared is None or _scalar(declared) != name:
        drifts.append(
            f"SKILL SHAPE {name} (30a): frontmatter name "
            f"{None if declared is None else _scalar(declared)!r} must equal the "
            "directory name"
        )

    description = _scalar(_field(frontmatter, "description") or "")
    raw_when = _field(frontmatter, "when_to_use")
    when_to_use = _scalar(raw_when or "")
    model_routed = _scalar(_field(frontmatter, "disable-model-invocation") or "") != "true"

    hit = _DESCRIPTION_TRIGGER_RE.search(description)
    if hit:
        drifts.append(
            f"SKILL SHAPE {name} (30b): description carries the trigger phrase "
            f"{hit.group(0)!r} - routing phrases belong in when_to_use"
        )
    if raw_when is None or not when_to_use:
        drifts.append(f"SKILL SHAPE {name} (30c): when_to_use is missing")
    elif model_routed and "Triggers on" not in when_to_use:
        drifts.append(
            f"SKILL SHAPE {name} (30c): when_to_use has no `Triggers on \"...\"` clause"
        )

    if not description:
        drifts.append(f"SKILL SHAPE {name} (30d): description is missing")
    elif len(description) > DESCRIPTION_MAX_CHARS:
        drifts.append(
            f"SKILL SHAPE {name} (30d): description is {len(description)} chars, "
            f"over {DESCRIPTION_MAX_CHARS}"
        )
    listing = len(description) + len(when_to_use)
    if listing > LISTING_MAX_CHARS:
        drifts.append(
            f"SKILL SHAPE {name} (30d): description + when_to_use is {listing} "
            f"chars, over {LISTING_MAX_CHARS}"
        )

    raw_tools = _field(frontmatter, "allowed-tools")
    for tool in _list_items(raw_tools) if raw_tools is not None else []:
        if tool == "Bash" or any(cmd in tool for cmd in MUTATION_COMMANDS):
            drifts.append(
                f"SKILL SHAPE {name} (30e): allowed-tools pre-approves {tool!r} - "
                "list specific Bash(cmd *) prefixes and never a mutation"
            )

    missing_done = _steps_without_done_when(body)
    if name in DONE_WHEN_BASELINE:
        drifts.extend(_baseline_drift("30f", name, not missing_done))
    elif missing_done:
        drifts.append(
            f"SKILL SHAPE {name} (30f): no `Done when:` line in "
            + ", ".join(missing_done)
        )

    refs_dir = skill_dir / "references"
    split_ok = len(raw_bytes) <= BODY_SPLIT_BYTES or (
        refs_dir.is_dir() and any(refs_dir.glob("*.md"))
    )
    if name in SPLIT_BASELINE:
        drifts.extend(_baseline_drift("30g", name, split_ok))
    elif not split_ok:
        drifts.append(
            f"SKILL SHAPE {name} (30g): SKILL.md is {len(raw_bytes)} bytes, over "
            f"{BODY_SPLIT_BYTES}, with no references/*.md - split per "
            "docs/skill-authoring.md *Progressive disclosure*"
        )

    if model_routed:
        missing_sections = _missing_sections(body)
        if name in SECTIONS_BASELINE:
            drifts.extend(_baseline_drift("30h", name, not missing_sections))
        elif missing_sections:
            drifts.append(
                f"SKILL SHAPE {name} (30h): missing section(s) "
                + ", ".join(f"## {s}" for s in missing_sections)
            )

        count = len(_trigger_keywords(when_to_use))
        low, high = TRIGGER_KEYWORD_RANGE
        if not low <= count <= high:
            warnings.append(
                f"SKILL SHAPE {name}: {count} trigger keywords, outside the "
                f"{low}-{high} target range"
            )

    return drifts, warnings


def baseline_orphans(skill_names: set[str]) -> list[str]:
    """Baseline entries naming a skill that no longer exists."""
    out: list[str] = []
    for rule, baseline in (
        ("30f", DONE_WHEN_BASELINE),
        ("30g", SPLIT_BASELINE),
        ("30h", SECTIONS_BASELINE),
    ):
        for skill in sorted(baseline - skill_names):
            out.append(
                f"SKILL SHAPE BASELINE {skill}: listed in the Rule {rule} baseline "
                "but no such skill - remove it"
            )
    return out
