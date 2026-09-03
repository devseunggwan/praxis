#!/usr/bin/env python3
"""Deterministic Stage 2.5 gate audit for the retrospect skill (issue #774).

Producer-side pre-flight: parses a Stage 3 draft (unified findings table +
memory_scan evidence blocks) and runs every mechanically-checkable Stage 2.5
gate, mirroring the Stop hook's parsing semantics
(hooks/completion-verify/retrospect-mix-check/impl.sh) so a clean audit here
cannot be blocked later by the hook on the same checks.

Semantic gates stay with the agent: Gate-3 (evidence robustness) and Gate-6
(oracle match) verdicts are supplied via --gate3 / --gate6. The script refuses
to leave Gate-3 unevaluated (None or NA) when compound findings exist; Gate-6
applicability (stored-value corrections) is not mechanically detectable, so
its verdict is fully agent-trusted. Session-level Stop-hook gates (7-10:
transcript receipt, suppression ledger, silent-pass coverage, critic roots)
are out of scope here — a script-clean draft can still be hook-blocked on
those.

Usage:
  audit-distribution-gates.py --draft <stage3-draft.md>
      [--signals <pre-scan-signal-text.txt>]
      [--gate3 PASS|FAIL|NA] [--gate6 PASS|FAIL|NA]
      [--reinforce-memory N]

Output (stdout): violations + advisories + rendered distribution card.
Exit codes: 0 = no violations, 1 = violations found, 2 = usage/input error.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

TABLE_HEADER = (
    "| # | Category | Tool Layer | Pattern | Root Cause | Rule / Gap "
    "| Repeat? | Proposed Actions (1~2) | Rationale | Priority |"
)

VALID_CATEGORIES = {
    "behavioral", "tool", "workflow", "spec-gap", "memory_hygiene",
    "output_quality",
}
VALID_TOOL_LAYERS = {"mcp", "cli", "builtin", "skill"}
VALID_ACTIONS = {
    "memory", "issue", "claude_md_draft", "skill_idea", "hook_code",
    "upstream_feedback",
}
NONBEHAVIORAL = {"tool", "workflow", "spec-gap"}

# Mirrors impl.sh:357 (Schema A) and impl.sh:358 (Schema B) verbatim.
SCHEMA_A_RE = re.compile(
    r"^\s*not (issue|claude_md_draft|skill_idea|hook_code|upstream_feedback): .+"
)
SCHEMA_B_RE = re.compile(r"^\s*not-others: .+")
# Mirrors impl.sh:381 verbatim.
BACKING_REPO_RE = re.compile(
    r"^\s*backing_repo: ([A-Za-z0-9_][A-Za-z0-9_.-]*)/[A-Za-z0-9_][A-Za-z0-9_.-]*"
)
# Literal Stage 4 warning prefix required on every cross-boundary row
# (stage2.5-audit.md Gate-4); Stage 4 scans this exact string — a paraphrase
# disables the gate.
EXTERNAL_WARNING = "⚠ EXTERNAL: per-action approval required at Stage 4"
# Declared visibility of the backing repo (issue #993). Gate-4 keys on
# visibility, not ownership: a *public* repo is a cross-boundary write even
# when its owner is the user's own org, so only `private`/`internal` stays
# unescalated. An absent declaration is treated as public (fail closed).
REPO_VISIBILITY_RE = re.compile(
    r"^\s*repo_visibility: (public|private|internal)\s*$"
)
UNESCALATED_VISIBILITY = {"private", "internal"}
VALID_VISIBILITY = {"public", "private", "internal"}

# Gate-1 behavioral-only safeguard keyword set (stage2.5-audit.md Gate-1).
SAFEGUARD_KEYWORDS = [
    "gh", "kubectl", "MCP", "--state", "permission denied", "timeout",
    "--help", "flag",
]
# Per-finding behavioral-label falsification keywords (stage2.5-audit.md
# Gate-1). `--<opt>` is expressed as a regex over long-option tokens.
LABEL_FALSIFY_KEYWORDS = [
    "probe", "scan scope", "skill", "hook", "missing", "gap", "scope",
    "미구현", "누락", "flag", "spec", "rule absent",
]
LONG_OPT_RE = re.compile(r"--[A-Za-z][A-Za-z0-9-]*")


def keyword_hit(keyword: str, text: str) -> bool:
    """ASCII keywords match on ASCII-letter boundaries (`spec` must not match
    `respect`/`expected`); multi-word/symbol phrases and Korean tokens match as
    substrings. `\\b` is useless between Hangul and ASCII, hence lookarounds."""
    if re.fullmatch(r"[A-Za-z]+", keyword):
        return re.search(
            rf"(?<![A-Za-z]){re.escape(keyword)}(?![A-Za-z])", text,
            re.IGNORECASE) is not None
    return keyword in text

PIPE_SENTINEL = "\x01PIPE\x01"
BR_RE = re.compile(r"<br */?>")

MEMORY_SCAN_BLOCK_RE = re.compile(
    r"<!--\s*memory_scan finding #(\d+):(.*?)-->", re.DOTALL
)


class Finding:
    def __init__(self, num: str, categories: list[str], tool_layer: str,
                 pattern: str, root_cause: str, repeat_cell: str,
                 actions: list[str], rationale: str) -> None:
        self.num = num
        self.categories = categories
        self.tool_layer = tool_layer
        self.pattern = pattern
        self.root_cause = root_cause
        self.repeat_cell = repeat_cell
        self.actions = actions
        self.rationale = rationale

    @property
    def is_memory_only(self) -> bool:
        return self.actions == ["memory"]

    @property
    def rationale_lines(self) -> list[str]:
        return BR_RE.sub("\n", self.rationale).splitlines()


def parse_table(draft_text: str) -> tuple[list[Finding] | None, list[str]]:
    """Extract findings rows using the hook's parsing semantics."""
    lines = draft_text.splitlines()
    try:
        header_idx = next(
            i for i, ln in enumerate(lines) if ln.strip() == TABLE_HEADER
        )
    except StopIteration:
        return None, ["draft contains no unified findings table "
                      f"(fixed header not found: `{TABLE_HEADER[:40]}…`)"]

    violations = []
    findings = []
    for ln in lines[header_idx + 1:]:
        if not ln.strip().startswith("|"):
            break
        if ln.strip().startswith("|---") or set(ln.strip()) <= {"|", "-", " "}:
            continue
        protected = ln.replace("\\|", PIPE_SENTINEL)
        trimmed = protected.strip()
        trimmed = trimmed[1:] if trimmed.startswith("|") else trimmed
        trimmed = trimmed[:-1] if trimmed.endswith("|") else trimmed
        cells = [c.strip().replace(PIPE_SENTINEL, "|") for c in trimmed.split("|")]
        if len(cells) != 10:
            violations.append(
                f"finding #{cells[0] if cells else '?'}: row has {len(cells)} "
                "cells, expected 10 — table schema violation (an unescaped "
                "'|' adds cells; escape it as '\\|')"
            )
            continue
        actions = []
        for tok in cells[7].split(","):
            t = tok.strip()
            if t and t not in actions:
                actions.append(t)
        categories = [c.strip() for c in cells[1].split(",") if c.strip()]
        findings.append(Finding(
            num=cells[0], categories=categories, tool_layer=cells[2],
            pattern=cells[3], root_cause=cells[4], repeat_cell=cells[6],
            actions=actions, rationale=cells[8],
        ))
    return findings, violations


def parse_memory_scan_blocks(draft_text: str) -> dict[str, dict[str, str]]:
    """Map finding number -> dict of memory_scan fields."""
    blocks = {}
    for m in MEMORY_SCAN_BLOCK_RE.finditer(draft_text):
        fields = {}
        for ln in m.group(2).splitlines():
            if ":" in ln:
                k, v = ln.split(":", 1)
                fields[k.strip()] = v.strip()
        blocks[m.group(1)] = fields
    return blocks


def resolve_own_orgs() -> tuple[set[str], bool]:
    """Own-org handle resolution per stage2.5-audit.md Gate-4 priority order.

    Still called after issue #993, but own-org membership is no longer an
    exemption on its own — it is only the first half of the (own-org AND
    private/internal) exemption. A public own-org repo is escalated exactly
    like a third-party one.

    Returns (set of lowercase handles, fallback_used).
    """
    env = os.environ.get("PRAXIS_OWN_ORGS", "").strip()
    if env:
        return {h.strip().lower() for h in env.split(",") if h.strip()}, False
    try:
        out = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True, text=True, timeout=10,
        )
        login = out.stdout.strip()
        if out.returncode == 0 and login:
            return {login.lower()}, False
    except (OSError, subprocess.TimeoutExpired):
        pass
    return set(), True  # conservative fallback: all owners external


_VISIBILITY_CACHE: dict[str, str | None] = {}


def resolve_repo_visibility(repo: str) -> str | None:
    """Resolve `<owner>/<repo>`'s real visibility, mirroring resolve_own_orgs().

    The declared `repo_visibility:` line is written by the same agent Gate-4
    constrains, so it cannot be the only oracle for the own-org exemption
    (issue #1150). The live API therefore wins over `PRAXIS_REPO_VISIBILITY`
    — the env var is another value the same author controls, and letting it
    take precedence would rebuild the hole one layer down. It applies only
    where the API produced no answer, which is the offline / CI case it
    exists for. One `gh` call per repo per run.

    Returns "public"/"private"/"internal", or None when unresolved — callers
    treat None as public, the same fail-closed reading an absent declaration
    already gets.
    """
    key = repo.lower()
    if key in _VISIBILITY_CACHE:
        return _VISIBILITY_CACHE[key]
    resolved = None
    try:
        out = subprocess.run(
            ["gh", "api", f"repos/{repo}", "--jq", ".visibility"],
            capture_output=True, text=True, timeout=10,
        )
        value = out.stdout.strip()
        if out.returncode == 0 and value in VALID_VISIBILITY:
            resolved = value
    except (OSError, subprocess.TimeoutExpired):
        pass
    if resolved is None:
        for entry in os.environ.get("PRAXIS_REPO_VISIBILITY", "").split(","):
            name, _, value = entry.strip().partition("=")
            if name.strip().lower() == key and value.strip() in VALID_VISIBILITY:
                resolved = value.strip()
                break
    _VISIBILITY_CACHE[key] = resolved
    return resolved


def audit(findings: list[Finding], ms_blocks: dict[str, dict[str, str]],
          signal_text: str, gate3: str | None, gate6: str | None,
          reinforce_memory: int) -> tuple[list[str], list[str], str]:
    violations = []
    advisories = []

    # --- row-level enum / schema checks (Gate-1 categorical integrity) ---
    for f in findings:
        bad_cats = [c for c in f.categories if c not in VALID_CATEGORIES]
        if not f.categories or bad_cats:
            violations.append(
                f"finding #{f.num}: invalid category[] "
                f"({', '.join(bad_cats) or 'empty'})"
            )
        if f.tool_layer not in VALID_TOOL_LAYERS | {"—"}:
            violations.append(
                f"finding #{f.num}: Tool Layer = '{f.tool_layer}' "
                "(must be mcp|cli|builtin|skill|—)"
            )
        elif "tool" in f.categories and f.tool_layer not in VALID_TOOL_LAYERS:
            violations.append(
                f"finding #{f.num}: category has tool but Tool Layer = "
                f"'{f.tool_layer}' (must be mcp|cli|builtin|skill)"
            )
        bad_actions = [a for a in f.actions if a not in VALID_ACTIONS]
        if not f.actions or bad_actions:
            violations.append(
                f"finding #{f.num}: invalid Proposed Actions "
                f"({', '.join(bad_actions) or 'empty'})"
            )
        if len(f.actions) > 2:
            violations.append(
                f"finding #{f.num}: {len(f.actions)} proposed actions after "
                "dedupe, max 2"
            )

    gate1_fail = False
    for f in findings:
        # Hook-mirrored: tool/workflow/spec-gap labeled + memory-only action.
        if NONBEHAVIORAL & set(f.categories) and f.is_memory_only:
            violations.append(
                f"finding #{f.num} (category={','.join(f.categories)}): "
                "tool/workflow/spec-gap labeled but Proposed Actions = memory only"
            )
            gate1_fail = True
        # Per-finding behavioral-label falsification keyword scan.
        if f.categories == ["behavioral"]:
            hits = [k for k in LABEL_FALSIFY_KEYWORDS
                    if keyword_hit(k, f.root_cause)]
            hits += LONG_OPT_RE.findall(f.root_cause)
            if hits and "behavioral-label-justify:" not in f.rationale:
                violations.append(
                    f"finding #{f.num}: behavioral-only label but root cause "
                    f"carries signal keywords ({', '.join(sorted(set(hits)))}) — "
                    "add the honest second category or a "
                    "behavioral-label-justify: line"
                )
                gate1_fail = True

    # Behavioral-only safeguard: all findings behavioral-only -> keyword scan.
    if findings and all(f.categories == ["behavioral"] for f in findings):
        corpus = signal_text + "\n" + "\n".join(
            f.pattern + "\n" + f.root_cause for f in findings
        )
        hits = sorted({k for k in SAFEGUARD_KEYWORDS
                       if keyword_hit(k, corpus)})
        if hits:
            advisories.append(
                "behavioral-only safeguard: signal text contains "
                f"tool/workflow indicators ({', '.join(hits)}) — surface the "
                "keyword set to the user and require explicit confirmation "
                "before Stage 3"
            )

    # --- Gate-2: memory-only rationale schema (hook-mirrored) ---
    gate2_applicable = False
    gate2_fail = False
    for f in findings:
        if not f.is_memory_only:
            continue
        gate2_applicable = True
        a_actions = {m.group(1) for ln in f.rationale_lines
                     for m in [SCHEMA_A_RE.match(ln)] if m}
        a = sum(1 for ln in f.rationale_lines if SCHEMA_A_RE.match(ln))
        b = sum(1 for ln in f.rationale_lines if SCHEMA_B_RE.match(ln))
        schema_a = a == 5 and len(a_actions) == 5
        schema_b = 1 <= b <= 2 and a == 0
        if not schema_a and not schema_b:
            violations.append(
                f"finding #{f.num}: memory-only row Rationale matches neither "
                f"schema A (5 'not <action>: ...' lines covering all five "
                f"non-memory types, found {a} lines / {len(a_actions)} "
                f"distinct) nor schema B (1-2 'not-others: ...' lines, "
                f"found {b})"
            )
            gate2_fail = True

    # --- backing_repo declaration for routed actions (hook-mirrored) ---
    for f in findings:
        if not {"upstream_feedback", "issue"} & set(f.actions):
            continue
        if not any(BACKING_REPO_RE.match(ln) for ln in f.rationale_lines):
            violations.append(
                f"finding #{f.num}: Proposed Actions contains "
                "upstream_feedback or issue but Rationale missing "
                "backing_repo: <owner/repo> — return to Stage 2 step 7"
            )

    # --- Gate-4: cross-boundary write authorization pre-check (issue #993) ---
    # Own-org membership no longer exempts on its own: a write to a PUBLIC
    # repo is a cross-boundary write needing per-action prior approval even
    # when the owner is the user's own org/handle. The exemption now requires
    # BOTH halves — own-org owner AND a `repo_visibility: private|internal`
    # declaration. An undeclared repo counts as public, so a missing line can
    # never widen the gate, and a third-party repo stays escalated whatever it
    # declares.
    #
    # Selection mirrors the backing_repo check above (issue #1038): `issue`
    # rows write to the same backing repo as `upstream_feedback` rows, so
    # both action types are in scope — checking only `upstream_feedback`
    # left every `gh issue create` row unaudited. With both in scope, NA now
    # means exactly one thing: no finding proposed either action, i.e. no
    # routed external write exists in this draft at all.
    routed = [f for f in findings
              if {"upstream_feedback", "issue"} & set(f.actions)]
    if not routed:
        gate4 = "NA"
    else:
        own_orgs, fallback = resolve_own_orgs()
        any_external = False
        if fallback:
            advisories.append(
                "gate-4: allowlist unresolved (no PRAXIS_OWN_ORGS, gh "
                "unavailable) — conservative fallback treats all owners as "
                "external"
            )
        for f in routed:
            repo = owner = visibility = None
            for ln in f.rationale_lines:
                m = BACKING_REPO_RE.match(ln)
                if m and repo is None:
                    repo = m.group(0).split("backing_repo:", 1)[1].strip()
                    owner = m.group(1).lower()
                v = REPO_VISIBILITY_RE.match(ln)
                if v and visibility is None:
                    visibility = v.group(1)
            if repo is None:
                continue  # backing_repo violation already recorded above
            own = not fallback and owner in own_orgs
            if own and visibility is None:
                advisories.append(
                    f"gate-4: finding #{f.num} is own-org but declares no "
                    "'repo_visibility: public|private|internal' line — "
                    "conservative fallback treats the backing repo as public"
                )
            # The declaration alone cannot carry the exemption (issue #1150):
            # only an own-org row that BOTH declares private/internal AND is
            # confirmed so by the API stays unescalated. Either half missing
            # escalates, so resolution can only ever add escalation.
            actual = resolve_repo_visibility(repo) if own else None
            if own and actual is None:
                advisories.append(
                    f"gate-4: finding #{f.num} backing_repo '{repo}' "
                    "visibility lookup returned no answer (gh missing, "
                    "unauthenticated, 404, rate-limited, or offline with no "
                    "PRAXIS_REPO_VISIBILITY entry) — conservative fallback "
                    "treats it as public"
                )
            # Only a mismatch that crosses the escalation boundary is a
            # contradiction; private vs internal are the same class here.
            if own and visibility is not None and actual is not None and (
                (visibility in UNESCALATED_VISIBILITY)
                != (actual in UNESCALATED_VISIBILITY)
            ):
                violations.append(
                    f"finding #{f.num}: Rationale declares 'repo_visibility: "
                    f"{visibility}' but GitHub reports '{actual}' for "
                    f"'{repo}' — correct the declaration"
                )
            if own and visibility in UNESCALATED_VISIBILITY \
                    and actual in UNESCALATED_VISIBILITY:
                continue
            any_external = True
            if own and visibility in UNESCALATED_VISIBILITY:
                why = (
                    f"own-org owner '{owner}' declares {visibility} but "
                    f"GitHub reports {actual or 'unresolved'} — a declaration "
                    "the API does not confirm cannot carry the exemption"
                )
            elif own:
                why = (
                    f"own-org owner '{owner}' but {visibility or 'public'} — "
                    "own-org membership no longer exempts a public repo"
                )
            else:
                why = f"owner '{owner}' is outside the own-org allowlist"
            if EXTERNAL_WARNING not in f.rationale:
                violations.append(
                    f"finding #{f.num}: backing_repo '{repo}' needs "
                    f"per-action approval ({why}) but Rationale lacks the "
                    f"literal warning prefix '{EXTERNAL_WARNING}'"
                )
        # `fallback` holds the verdict off PASS on its own. With the allowlist
        # unresolved nothing was actually checked, and a row whose backing_repo
        # never parsed leaves any_external False — the label would then read as
        # a clean gate over an unread one.
        gate4 = "WARN" if any_external or fallback else "PASS"

    # --- Gate-5: memory-scan completeness ---
    memory_findings = [f for f in findings if "memory" in f.actions]
    if not memory_findings:
        gate5 = "NA"
    else:
        gate5 = "PASS"
        for f in memory_findings:
            fields = ms_blocks.get(f.num)
            if fields is None:
                violations.append(
                    f"finding #{f.num}: memory action but no "
                    f"'<!-- memory_scan finding #{f.num}: ... -->' evidence block"
                )
                gate5 = "FAIL"
                continue
            missing = []
            if fields.get("scanned") != "true":
                missing.append("scanned: true")
            if not fields.get("candidates_reviewed"):
                missing.append("candidates_reviewed")
            if fields.get("repeat") not in ("true", "false"):
                missing.append("repeat")
            if not fields.get("repeat_count", "").lstrip("-").isdigit():
                missing.append("repeat_count")
            if missing:
                violations.append(
                    f"finding #{f.num}: memory_scan block incomplete "
                    f"(missing/invalid: {', '.join(missing)})"
                )
                gate5 = "FAIL"

    # --- Gate-3 / Gate-6: semantic verdicts supplied by the agent ---
    compound = [f for f in findings if len(f.actions) == 2]
    if compound and gate3 in (None, "NA"):
        violations.append(
            f"{len(compound)} compound (2-action) finding(s) present but "
            f"--gate3 {'not supplied' if gate3 is None else 'NA claimed'} — "
            "evaluate Gate-3 (a)/(b)/(c) per stage2.5-audit.md and pass "
            "PASS or FAIL"
        )
        gate3 = "FAIL"
    elif compound and gate3 == "FAIL":
        violations.append(
            "gate-3 verdict is FAIL — resolve the compound-finding evidence "
            "per stage2.5-audit.md (downgrade/demote) before Stage 3"
        )
    elif not compound:
        if gate3 not in (None, "NA"):
            advisories.append(
                f"gate-3: no compound findings; overriding supplied "
                f"'{gate3}' to NA"
            )
        gate3 = "NA"
    if gate6 == "FAIL":
        violations.append(
            "gate-6 verdict is FAIL — re-probe with the stored oracle or "
            "reroute per stage2.5-audit.md before Stage 3"
        )
    gate6 = gate6 or "NA"

    gate1 = "FAIL" if gate1_fail else ("PASS" if findings else "NA")
    gate2 = ("FAIL" if gate2_fail else "PASS") if gate2_applicable else "NA"
    if not findings:
        gate2 = gate4 = gate5 = "NA"

    # --- distribution card ---
    counts = {a: 0 for a in
              ("memory", "issue", "claude_md_draft", "skill_idea",
               "hook_code", "upstream_feedback")}
    for f in findings:
        for a in f.actions:
            if a in counts:
                counts[a] += 1
    counts["memory"] += reinforce_memory
    hygiene = sum(1 for f in findings if "memory_hygiene" in f.categories)
    quality = sum(1 for f in findings if "output_quality" in f.categories)

    card = ["<!-- retrospect:distribution begin -->"]
    card += [f"- {k}: {v}" for k, v in counts.items()]
    card += [f"- memory_hygiene: {hygiene}", f"- output_quality: {quality}"]
    card += [
        f"- gate_1_verdict: {gate1}", f"- gate_2_verdict: {gate2}",
        f"- gate_3_verdict: {gate3}", f"- gate_4_verdict: {gate4}",
        f"- gate_5_verdict: {gate5}", f"- gate_6_verdict: {gate6}",
    ]
    card.append("<!-- retrospect:distribution end -->")
    return violations, advisories, "\n".join(card)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Deterministic Stage 2.5 gate audit for the retrospect skill")
    ap.add_argument("--draft", required=True,
                    help="Stage 3 draft markdown (findings table + fences)")
    ap.add_argument("--signals", default=None,
                    help="pre-scan signal text file (Gate-1 safeguard corpus)")
    ap.add_argument("--gate3", choices=["PASS", "FAIL", "NA"], default=None)
    ap.add_argument("--gate6", choices=["PASS", "FAIL", "NA"], default=None)
    ap.add_argument("--reinforce-memory", type=int, default=0,
                    help="successful_patterns rows whose reinforce_action is "
                         "memory (added to the memory count)")
    args = ap.parse_args()

    try:
        with open(args.draft, encoding="utf-8") as fh:
            draft_text = fh.read()
    except OSError as e:
        print(f"error: cannot read draft: {e}", file=sys.stderr)
        return 2

    signal_text = ""
    if args.signals:
        try:
            with open(args.signals, encoding="utf-8") as fh:
                signal_text = fh.read()
        except OSError as e:
            print(f"error: cannot read signals: {e}", file=sys.stderr)
            return 2

    findings, parse_violations = parse_table(draft_text)
    if findings is None:
        print("## gate-audit violations")
        for v in parse_violations:
            print(f"- {v}")
        return 1

    ms_blocks = parse_memory_scan_blocks(draft_text)
    violations, advisories, card = audit(
        findings, ms_blocks, signal_text, args.gate3, args.gate6,
        args.reinforce_memory,
    )
    violations = parse_violations + violations

    if violations:
        print("## gate-audit violations")
        for v in violations:
            print(f"- {v}")
    if advisories:
        print("## gate-audit advisories")
        for a in advisories:
            print(f"- {a}")
    print("## distribution card")
    print(card)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
