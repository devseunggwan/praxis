#!/usr/bin/env python3
"""Verify generated plugin manifests are in sync with the canonical source.

Runs the build logic in dry mode: re-render every output, compare to the
committed file, and exit non-zero on any drift. Also validates the
Phase 2 (ADR-0001) invariants. The rule numbers below are the load-bearing
IDs used by drift messages, comments, tests, and docs — every rule block in
main() is labeled with its number, and this list is the canonical roster
(renumbered once, coherently, in #1172):

  1. Every hooks/<role>/<name>/ directory has ≥1 manifest entry
     (opt-in carve-out: external-write-falsify-check), and every manifest
     entry has a backing directory.
  2. Every manifest entry's `role` field matches the parent directory name.
  3. The impl file (`impl.py` or the body-as-sh `impl.sh`) exists on disk.
  4. completion-verify Stop ordering matches manifest array order.
  5. Generated `.claude-plugin/hooks/hooks.json` (+ peers) are byte-
     identical to what `build-plugin-manifests.py` would emit (this is
     the runtime contract — Claude Code reads the generated file).
  6. Generated runtime wrappers under hooks/*.sh are byte-identical to
     the generator output (Phase 2 strict diff replaces Phase 1's exec-
     target parity check). Dispatch-only members carry NO wrapper and are
     asserted absent from disk (Rule 6b, ADR-0002 Phase 4 / #618). Opt-in
     wrappers (OPT_IN_HOOKS, not in the manifest) are byte-identity checked
     too (Rule 6c, #605). Every expected wrapper on disk carries the
     executable bit and, when tracked, a 100755 git index mode (Rule 6d,
     #1172); and no orphan hooks/*.sh outside the generated set survives
     (Rule 6e, #1172 — the reverse sweep Rule 6 lacked).
  7. INDEX.md ↔ manifest entry cross-check (#1306: ARCHITECTURE.md no
     longer carries a per-hook table, so only docs/hook/INDEX.md is
     checked; the operating matrix is generated and drift-checked
     separately).
  8. Spec `Supported hosts:` ↔ manifest `hosts` cross-check.
  9. Release version wiring (#1172): `VERSION` equals the "." version in
     `.release-please-manifest.json`, and every versioned platform artifact
     (plugin, marketplace kinds — marketplace carries
     version fields too) is listed in release-please-config.json
     `extra-files` so release-please bumps its embedded versions.
  10. spec.md exists at hooks/<role>/<name>/spec.md for every registered
      hook (Phase 3, ADR-0001 §5.3 — specs collocated with impl), and a
      manifest `hosts` field, when present, is a non-empty list of strings.
  11. Runtime-sensitive skills carry runtime verification frontmatter
      (`verified-against-runtime`, `runtime-verified-at`,
      `runtime-verified-note`).
  12. skills/<skill-name>/ on disk matches the EXPECTED_SKILLS frozen set
     (issue #465 — surface freeze gate against silent skill proliferation).
  13. Doc drift invariants (issues #498, #1177): AGENTS.md "## Skills (N)"
     count and per-skill backtick tokens, docs/skills.md per-skill table
     rows, and the skills/using-praxis/SKILL.md routing tables (every skill
     routed by a table row, no phantom skill names in category rows or
     scenario routing cells) all match EXPECTED_SKILLS; docs/skills.md
     trigger-keyword cells quote keywords verbatim from each skill's
     frontmatter `when_to_use` (or `description`, #1331); and the
     compatibility-tier tables in
     README.md, AGENTS.md, and using-praxis stay normalized-identical.
  14. Each manifest `dispatch_groups` (event, matcher) collapses to exactly
     one dispatcher node per platform hooks.json (no member silently left as
     its own node, no second dispatcher node), and the runtime resolver
     `_dispatch.group_members` resolves the same member set the build
     collapsed (ADR-0002, #617 — ties build path and runtime path together).
  15. docs/hook/<name>.md redirect-stub parity (#606): every hook dir owns a
      byte-identical 1-line stub, and no orphan stub survives (INDEX.md and the
      hand-written NON_HOOK_DOCS allowlist excepted).
  16. Standalone hooks (any registration outside the dispatch-covered
      PreToolUse/Bash shape, plus opt-in hooks) apply `@fail_open` in
      impl.py — AST-checked, not substring (#645).
  17. Per-hook `mode` metadata in hooks/manifest.json (strict env, bypass env,
      state/path vars, read-only external commands) ↔ the human-readable views
      in docs/bypass-vars.md and SECURITY.md, cross-checked in both directions
      so neither can drift (#688). Opt-in hooks and shared/non-manifest rows are
      exempt (they own no manifest `mode` block).
  18. impl.sh hooks arm fire-ledger instrumentation with a line-anchored
      `praxis_fire_arm <name>` call (#848).
  19. Every `mktemp -d` assignment in tests/*.sh guards its own failure on
      the same line (#897).
  20. Spec `Requires:` ↔ manifest `requires` cross-check (#1158): a hook whose
      matcher is dead without an external component (cmux, zsh, codex plugin,
      hookable memory store, slack/notion MCP) declares it in both places or
      neither — the Rule 8 contract, applied to component dependencies.
  21. docs/hook-operating-matrix.md parity: generated hook operating surface
      summary is byte-identical to the manifest/env/security source render
      (#672). Numbered after 20 because its original label collided with
      Rule 16 (@fail_open); it took the next free number in #1172.
  22. Spec referenced-path existence (#1179): every repo-path-looking token in
      hooks/*/*/spec.md (backticked spans and fenced code blocks; contains a
      `/`, ends in .sh/.py/.md/.json, first segment is a checked top-level
      dir) must exist on disk, except deliberate phantom-path examples in
      SPEC_PATH_EXEMPT. (Numbered 22 to stay clear of rules added by
      parallel PRs.)
  23. README.md hook-aggregate counts (#1176): total hook dirs, manifest
      registration count, per-role dir counts, the number of distinct hooks
      carrying a variable in docs/bypass-vars.md, and how many advisory hooks
      a strict variable there can promote into blocking must all match the
      hand-written numbers at their anchored README phrases — the Rule 13
      doc-drift contract, applied to the hook surface. (Numbered 23 to stay
      clear of rules 21/22 added by a parallel PR.)

  24. Canonical matcher token order (#1168): every plain pipe-joined tool-name
      matcher (hooks and dispatch_groups alike) spells its tokens in sorted
      order with no duplicates, so identical matcher SETS share one literal
      spelling and can coalesce into one hooks.json group / dispatch group.
      Regex-y matchers (any token with characters outside [A-Za-z0-9]) are
      exempt, but an empty alternation token (`Edit|`) — which matches every
      tool — is drift. (Numbered 24 on rebase: this rule was authored as 21
      before rules 21-23 landed on main.)
  25. Generated dispatcher commands are shell-safe (#1198): every hooks.json
      `command` that invokes the dispatch wrapper, token-split the way `sh -c`
      would, yields no bare shell control-operator token — an unquoted
      pipe-carrying dispatch matcher would otherwise run as a pipeline and
      silently disable the whole group. Non-dispatcher commands are out of
      scope (a deliberate compound command is legitimate shell). (Numbered 25
      on rebase: authored as 22 before rules 21-23 landed on main.)
  26. Sunset review (#1300): every hook NAME carries a well-formed
      `review_by` date (YYYY-MM-DD; the first registration carries it and
      multi-event siblings may omit it, exactly like `hosts`) — REVIEW_BY
      MISSING / MALFORMED / CONFLICT — and that date is not in the past
      (REVIEW_BY OVERDUE). An overdue hook is re-audited, then either the
      date is bumped or the verdict is recorded in docs/hook-prune-audit.md;
      the rule exists because the audit found zero drops and cannot rank
      hooks by value, so without a deadline the roster only grows. The
      field never reaches a platform hooks.json (the node builder copies
      only command/timeout/hosts).
  27. README hook-dependency table (#1332): the `### Hook dependencies`
      table in README.md is the reader's view of the manifest `requires`
      field (Rule 20). Every component some hook declares has exactly one
      row, every row names a declared component, each row's hook cell is the
      exact set of hooks declaring it, and the install cell is non-empty —
      HOOK DEPS MISSING / ORPHAN / DRIFT / NO INSTALL. The audit that added
      `requires` (docs/hook-suitability-audit.md §B) found the README's tier
      table covered skills only, so a user without oh-my-claudecode or the
      codex plugin had nowhere to read which hooks were inert; `plugin.json`
      `dependencies` was rejected for this (no optional form — see
      ARCHITECTURE.md), so the declaration is documentary and this rule keeps
      it true.

An unnumbered auxiliary check verifies the Codex adapter symlinks
(plugins/praxis/{skills,hooks,scripts} → repo root).

  Schema gate (unnumbered — rule renumbering is owned by #1172): before any
      numbered rule runs, hooks/manifest.json is validated against
      hooks/manifest.schema.json via the stdlib walker in
      build-plugin-manifests.py (#1173, shared — the build runs the same gate
      before rendering), so a typo'd optional key, a wrong type, or an
      unknown enum value fails with a file+entry+key diagnostic instead of a
      KeyError deeper in the pipeline. Platform files
      (manifests/platforms/*.json) get the same treatment through
      _build.load_platform's checked access, including host_id membership in
      the schema's closed hosts enum; the reverse direction (stale enum value
      with no platform file) is checked here.

CI invokes this; developers can too, via `./scripts/check-plugin-manifests.py`.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Set as AbstractSet
from datetime import date
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "build_plugin_manifests", REPO_ROOT / "scripts" / "build-plugin-manifests.py"
)
assert _spec and _spec.loader
_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_build)

# ADR-0002 (#617): the runtime dispatch resolver. Rule 14 cross-checks that the
# build collapse (filter_hooks_for_host → committed hooks.json) and the runtime
# resolution (group_members) agree on each dispatch group's members.
_disp_spec = importlib.util.spec_from_file_location(
    "praxis_dispatch", REPO_ROOT / "hooks" / "_lib" / "_dispatch.py"
)
assert _disp_spec and _disp_spec.loader
_dispatch = importlib.util.module_from_spec(_disp_spec)
_disp_spec.loader.exec_module(_dispatch)

from constants import EXPECTED_SKILLS, OPT_IN_HOOKS, VALID_ROLES  # noqa: E402


# release-please rewrites only the version fields its jsonpath reaches, and the
# marketplace outputs carry one at the top level and one inside plugins[0], so
# the recursive form is the only one that leaves nothing stale (Rule 9, #1172).
RELEASE_JSONPATH = "$..version"

RUNTIME_METADATA_REQUIRED_FIELDS = (
    "verified-against-runtime",
    "runtime-verified-at",
    "runtime-verified-note",
)
RUNTIME_METADATA_PLACEHOLDERS = {
    "runtime-verified-at": {"YYYY-MM-DD"},
    "runtime-verified-note": {
        "<cli-name> <version> — one-line observed behavior",
        '"<cli-name> <version> — one-line observed behavior"',
    },
}
RUNTIME_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REVIEW_BY_OVERDUE_REMEDY = (
    "re-audit the hook, then either bump review_by or record the verdict "
    "in docs/hook-prune-audit.md"
)


def review_by_drifts(manifest: dict, today: date) -> list[str]:
    """Rule 26 (#1300): every hook NAME carries a well-formed, unexpired `review_by`.

    `review_by` is required per hook name, not per registration: the first
    registration carries it and multi-event siblings may omit it, mirroring
    how `hosts` is read ("first registration wins"). Two registrations that
    disagree are a CONFLICT — silently preferring one would let the other
    rot. A date strictly before `today` is OVERDUE: the sunset review is due,
    and the way out is a re-audit, not a bare date bump. `today` is a
    parameter so tests can pin it.
    """
    declared: dict[str, list] = {}
    for entry in manifest["hooks"]:
        values = declared.setdefault(entry["name"], [])
        if "review_by" in entry:
            values.append(entry["review_by"])

    drifts: list[str] = []
    for name, values in declared.items():
        if not values:
            drifts.append(
                f"REVIEW_BY MISSING {name}: add review_by YYYY-MM-DD to its "
                "first manifest registration — merge date + 90 days for a new "
                "hook (#1300)"
            )
            continue
        distinct = list(dict.fromkeys(values))
        if len(distinct) > 1:
            drifts.append(
                f"REVIEW_BY CONFLICT {name}: registrations disagree "
                f"{distinct!r} — keep one value on the first registration and "
                "drop the rest (#1300)"
            )
            continue
        value = distinct[0]
        parsed: date | None = None
        if isinstance(value, str) and RUNTIME_DATE_RE.fullmatch(value):
            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                parsed = None
        if parsed is None:
            drifts.append(
                f"REVIEW_BY MALFORMED {name}: {value!r} — expected a real "
                "calendar date as YYYY-MM-DD (#1300)"
            )
            continue
        if parsed < today:
            drifts.append(
                f"REVIEW_BY OVERDUE {name}: {value} is past — "
                f"{REVIEW_BY_OVERDUE_REMEDY} (#1300)"
            )
    return drifts


SKILL_CALL_RE = re.compile(r"\bSkill\(\s*(?:(?:\"|')|skill\s*=)")
ASK_USER_QUESTION_CALL_RE = re.compile(
    r"\bAskUserQuestion\s*\("
    r"|AskUserQuestion:"
    r"|(?:ask|call|use|offer|confirm|show|surface|present|emit)"
    r"[^.\n]{0,80}`?AskUserQuestion`?",
    re.IGNORECASE,
)
NEGATED_ASK_USER_QUESTION_RE = re.compile(
    r"\b(?:do\s+not|don't|never|must\s+not|should\s+not|cannot|can't|avoid)"
    r"\s+(?:ask|call|use|offer|confirm|show|surface|present|emit)?"
    r"[^.\n]{0,80}`?AskUserQuestion`?",
    re.IGNORECASE,
)
INLINE_EXECUTION_CONTEXT_RE = re.compile(
    r"(?:^|\b)(?:run|execute|call|invoke|use|start|launch)"
    r"(?:\s+(?:(?:the|this)\s+)?(?:command|template|snippet))?"
    r"\s*:?\s+$",
    re.IGNORECASE,
)
SHELL_COMMAND_TOKEN_RE = re.compile(
    r"(?:^|[|;&(]\s*|\$\(\s*)"
    r"(?:[$#]\s+)?"
    r"(?:(?:if|while|until|elif|then|do|command|exec)\s+)?"
    r"(?:(?:[A-Za-z_][A-Za-z0-9_]*=\S+)\s+)*"
    r"(?:\{[a-zA-Z_][a-zA-Z0-9_]*\}\s+)?"
    r"(?:(?:/|\.{1,2}/|~/)?(?:[A-Za-z0-9_.+-]+/)*)"
    r"([A-Za-z_][A-Za-z0-9_.+-]*)(?=\s|$)"
)
ROOT_PROMPT_COMMAND_RE = re.compile(
    r"^(?:(?:[A-Za-z_][A-Za-z0-9_]*=\S+)\s+)*"
    r"(?:(?:/|\.{1,2}/|~/)?(?:[A-Za-z0-9_.+-]+/)*)"
    r"([A-Za-z_][A-Za-z0-9_.+-]*)(?=\s|$)"
)
KNOWN_EXTERNAL_CLI_COMMANDS = {
    "airflow",
    "aws",
    "claude",
    "cmux",
    "codex",
    "curl",
    "docker",
    "gh",
    "git",
    "jq",
    "kubectl",
    "node",
    "npm",
    "pnpm",
    "python",
    "python3",
    "wget",
    "yarn",
}
SHELL_NON_EXTERNAL_COMMANDS = {
    "alias",
    "break",
    "case",
    "cd",
    "command",
    "continue",
    "declare",
    "do",
    "done",
    "echo",
    "elif",
    "else",
    "esac",
    "eval",
    "exec",
    "exit",
    "export",
    "fi",
    "for",
    "function",
    "if",
    "local",
    "popd",
    "printf",
    "pushd",
    "read",
    "readonly",
    "return",
    "set",
    "shift",
    "source",
    "then",
    "trap",
    "type",
    "typeset",
    "unset",
    "while",
}
EXTERNAL_CLI_WRAPPER_SKILLS = {
    # Backstop for wrappers whose runtime-sensitive command templates are not
    # visible to the static fenced-shell scan.
    "cmux-delegate",
}

# Rule 22 (#1179) — spec.md tokens that look like repo paths but are
# DELIBERATELY nonexistent: phantom-path examples in the spec of a hook whose
# whole job is detecting phantom paths, and generic illustration filenames in
# example command lines. Keyed by spec path relative to REPO_ROOT.
SPEC_PATH_EXEMPT: dict[str, set[str]] = {
    "hooks/advisory-nudge/external-write-path-existence-check/spec.md": {
        "hooks/pre-tool-use/external-write-path-existence-check.sh",
        "hooks/pre-tool-use/fake.sh",
        "hooks/foo.sh",
        "docs/hook/nonexistent-spec.md",
    },
    "hooks/advisory-nudge/pytest-direct-exec-advisory/spec.md": {
        "tests/test_api.py",
    },
    "hooks/advisory-nudge/count-assertion-verify/spec.md": {
        "tests/file.sh",
    },
}

# Rule 22 — first path segment must be one of these top-level repo dirs for a
# token to be treated as a repo-relative path claim. Everything else
# (`src/loader.py`, `vault/note.md`, `.claude/settings.json`, sibling-relative
# `foo/spec.md` references, `$VAR/...` templates) is illustration or
# out-of-repo config, not a checkable reference.
SPEC_PATH_CHECKED_ROOTS = {"hooks", "tests", "scripts", "docs", "skills", "manifests"}

# Rule 22 — strict path charset (letters/digits/_/./-, `/` separators) ending
# in a checked extension. Tokens carrying globs (`*`), placeholders (`<name>`),
# env expansions (`${...}`), or spaces never match, so command examples and
# templates are not false-flagged.
SPEC_PATH_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./-]+\.(?:sh|py|md|json)$")

SPEC_BACKTICK_RE = re.compile(r"`([^`\n]+)`")


def _spec_referenced_paths(text: str) -> set[str]:
    """Repo-path-looking tokens claimed by one spec.md (Rule 22, #1179).

    Candidate words come from backticked inline spans and from every line
    inside fenced code blocks (both split on whitespace, so `bash tests/...`
    command examples contribute their path arguments). A candidate is kept iff,
    after stripping surrounding punctuation and a leading `./`, it contains a
    `/`, has no `..` segment (out-of-tree examples), matches
    SPEC_PATH_TOKEN_RE, and its first segment is in SPEC_PATH_CHECKED_ROOTS.
    """
    words: list[str] = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            words.extend(stripped.split())
        else:
            for m in SPEC_BACKTICK_RE.finditer(line):
                words.extend(m.group(1).split())

    paths: set[str] = set()
    for word in words:
        tok = word.strip("\"'()[]{},:;")
        if tok.startswith("./"):
            tok = tok[2:]
        if "/" not in tok or ".." in tok:
            continue
        if not SPEC_PATH_TOKEN_RE.match(tok):
            continue
        if tok.split("/", 1)[0] not in SPEC_PATH_CHECKED_ROOTS:
            continue
        paths.add(tok)
    return paths


def dispatch_node_drifts(
    hooks_json: dict,
    event: str,
    matcher: str | None,
    host_id: str,
    expected_members: set[str],
    dispatch_wrapper_name: str,
    args_wrappers: AbstractSet[str] = frozenset(),
) -> list[str]:
    """Drift strings for one (event, matcher) group's node shape in a hooks.json.

    Pure function (no I/O) so the node-shape half of Rule 14 is unit-testable in
    isolation from the runtime resolver. `expected_members` is the host-kept
    COLLAPSIBLE member set: non-empty → the group must hold exactly ONE
    dispatcher node carrying `event matcher host_id` args; empty (the host
    filtered every member) → no dispatcher node. `args_members` are the group's
    args-declaring members: the build keeps them as STANDALONE nodes (the
    dispatcher cannot forward argv; the runtime resolver excludes them the same
    way — issue #1199 review), so their per-member nodes are expected, not a
    leak. A matcher-less group renders `_build.DISPATCH_NO_MATCHER_ARG` in the
    matcher argv slot.
    """
    groups = [
        g
        for g in hooks_json.get("hooks", {}).get(event, [])
        if g.get("matcher") == matcher
    ]
    nodes = [n for g in groups for n in g.get("hooks", [])]
    dispatch_nodes = [
        n for n in nodes if dispatch_wrapper_name in n.get("command", "")
    ]
    member_nodes = [
        n for n in nodes if dispatch_wrapper_name not in n.get("command", "")
    ]
    # Match the node's wrapper BASENAME against the exact set the manifest
    # says should stay standalone. A substring probe answered two different
    # questions wrong at once: a `wrapper_suffix` node never matched its own
    # bare name (false leak), and a member whose node vanished entirely left
    # nothing to test, so its silent disappearance read as clean.
    def _basename(node) -> str:
        cmd = node.get("command", "")
        head = cmd.split()[0] if cmd.split() else cmd
        return head.rsplit("/", 1)[-1]

    leaked = [n for n in member_nodes if _basename(n) not in args_wrappers]
    present = {_basename(n) for n in member_nodes}
    missing = sorted(args_wrappers - present)
    out: list[str] = []
    want = 1 if expected_members else 0
    if len(dispatch_nodes) != want:
        out.append(
            f"DISPATCH NODE COUNT {event}/{matcher} host={host_id}: expected "
            f"{want} dispatcher node(s), found {len(dispatch_nodes)}"
        )
    if missing:
        out.append(
            f"DISPATCH ARGS NODE MISSING {event}/{matcher} host={host_id}: "
            f"{missing} declared 'args' (so the build must keep each one as a "
            "standalone node) but no such node exists — the hook is silently "
            "disabled"
        )
    if leaked:
        out.append(
            f"DISPATCH MEMBER LEAK {event}/{matcher} host={host_id}: "
            f"{len(leaked)} non-dispatcher node(s) in a collapsed group: "
            f"{[n.get('command', '') for n in leaked]} "
            f"(args-declaring members may stay standalone; declared here: "
            f"{sorted(args_wrappers)})"
        )
    # Args are shlex-quoted in the generated command (PR #1198: the host runs
    # it via `sh -c`, so a pipe-carrying matcher MUST be quoted or it parses
    # as a pipeline). Expect the quoted spelling — a no-op for plain tokens.
    # A matcher-less group renders `_build.DISPATCH_NO_MATCHER_ARG` in the
    # matcher slot (issue #1199 review) — see `_build._dispatcher_node`.
    matcher_arg = _build.DISPATCH_NO_MATCHER_ARG if matcher is None else matcher
    expected_args = (
        f"{shlex.quote(event)} {shlex.quote(matcher_arg)} {shlex.quote(host_id)}"
    )
    for n in dispatch_nodes:
        cmd = n.get("command", "")
        if expected_args not in cmd:
            out.append(
                f"DISPATCH ARGS {event}/{matcher} host={host_id}: dispatcher "
                f"command {cmd!r} missing '{expected_args}' args"
            )
    return out


def _skill_dirs() -> list[Path]:
    """Return all skill directories under skills/<skill-name>/.

    Mirrors `_hook_dirs()` convention: files (SKILL.md.tmpl) and underscore-
    prefixed entries (future internal layout) are excluded automatically.

    A directory counts as a skill only if it carries a `SKILL.md`. Binary-only
    dirs (e.g. `bypass-review`, which ships a CLI under `skills/` but has no
    `SKILL.md` and cannot be invoked as `/praxis:*`) are excluded so they are
    not double-counted against the skill surface (issue #582).
    """
    skills_root = REPO_ROOT / "skills"
    dirs: list[Path] = []
    if not skills_root.is_dir():
        return dirs
    for entry in sorted(skills_root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("_") or entry.name == "__pycache__":
            continue
        if not (entry / "SKILL.md").exists():
            continue
        dirs.append(entry)
    return dirs


def _parse_skill_frontmatter(skill_path: Path) -> dict[str, object]:
    """Best-effort parse of the top-level SKILL.md frontmatter.

    The skill spec frontmatter is intentionally simple: top-level scalar keys
    plus YAML folded/literal blocks for `description`. We only need scalar
    extraction for the runtime-verification fields, so this parser ignores
    nested YAML and continuation lines once it has recorded the parent key.
    """
    try:
        text = skill_path.read_text()
    except OSError:
        return {}
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return {}

    data: dict[str, object] = {}
    i = 1
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if stripped == "---":
            return data
        if not raw or raw[0].isspace() or ":" not in raw:
            i += 1
            continue
        key, value = raw.split(":", 1)
        key = key.strip()
        value = value.strip()
        if re.fullmatch(r"[>|][+-]?", value):
            data[key] = value
            i += 1
            while i < len(lines):
                cont = lines[i]
                if cont.strip() == "---":
                    return data
                if cont and not cont[0].isspace():
                    break
                i += 1
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if value.lower() == "true":
            data[key] = True
        elif value.lower() == "false":
            data[key] = False
        else:
            data[key] = value
        i += 1
    return {}


def _skill_has_helper_executable(skill_dir: Path) -> bool:
    """True iff the skill ships a runtime helper binary/script beside SKILL.md."""
    for entry in skill_dir.iterdir():
        if entry.name == "SKILL.md" or not entry.is_file():
            continue
        if entry.name.startswith("."):
            continue
        if os.access(entry, os.X_OK):
            return True
    return False


def _skill_has_external_cli_template(text: str) -> bool:
    """Return True when fenced shell templates invoke external CLI binaries."""
    in_shell_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            language = stripped[3:].strip().lower()
            if in_shell_fence:
                in_shell_fence = False
            else:
                in_shell_fence = language in {"", "bash", "sh", "shell", "zsh"}
            continue
        if not in_shell_fence or not stripped:
            continue
        line_to_scan = stripped
        if stripped.startswith("#"):
            prompt_body = stripped[1:].lstrip()
            if not _looks_like_external_cli_invocation(prompt_body):
                continue
            line_to_scan = prompt_body
        for match in SHELL_COMMAND_TOKEN_RE.finditer(line_to_scan):
            command = match.group(1)
            if command not in SHELL_NON_EXTERNAL_COMMANDS:
                return True
    return False


def _looks_like_external_cli_invocation(
    snippet: str, *, allow_unknown: bool = False
) -> bool:
    """Heuristic for prompt-prefixed or inline command snippets."""
    stripped = snippet.strip()
    if " " not in stripped:
        return False
    match = ROOT_PROMPT_COMMAND_RE.match(stripped)
    if not match:
        return False
    command = match.group(1)
    first_token = stripped.split()[0]
    if command in SHELL_NON_EXTERNAL_COMMANDS:
        return False
    return (
        command in KNOWN_EXTERNAL_CLI_COMMANDS
        or "/" in first_token
        or re.search(r"(^|\s)--[A-Za-z0-9][A-Za-z0-9_-]*", stripped) is not None
        or allow_unknown
    )


def _skill_has_inline_external_cli(text: str) -> bool:
    """Return True when inline code snippets show external CLI invocations."""
    for match in re.finditer(r"`([^`\n]+)`", text):
        context = text[max(0, match.start() - 40) : match.start()]
        if not INLINE_EXECUTION_CONTEXT_RE.search(context):
            continue
        if _looks_like_external_cli_invocation(match.group(1), allow_unknown=True):
            return True
    return False


def _skill_has_operative_ask_user_question(text: str) -> bool:
    """True when prose instructs the skill to use AskUserQuestion at runtime."""
    for match in ASK_USER_QUESTION_CALL_RE.finditer(text):
        context = text[max(0, match.start() - 80) : match.end()]
        if NEGATED_ASK_USER_QUESTION_RE.search(context):
            continue
        return True
    return False


def _skill_runtime_verification_reasons(skill_dir: Path) -> list[str]:
    """Return the runtime-sensitive reasons that require frontmatter metadata.

    The rule intentionally prefers low-ambiguity, static signals already
    codified in CONTRIBUTING.md:

      - `AskUserQuestion(...)` appears in the spec (interactive runtime call)
      - `Skill(...)` appears in the spec (delegation runtime call)
      - the skill wraps external CLI command templates in SKILL.md or references/*.md
      - the skill ships an executable helper beside SKILL.md (wrapper/runtime
        surface the prose depends on)

    Scanning includes SKILL.md and all references/*.md so that operative signals
    that live exclusively in reference files are not missed (false negative).
    This keeps the gate conservative for prose-only docs skills while still
    catching the repo's current runtime-sensitive surfaces.
    """
    skill_path = skill_dir / "SKILL.md"
    # Read with errors="replace": a non-UTF-8 byte is replaced, never raises.
    # This is deliberate over `except UnicodeError: return []` — swallowing a
    # decode failure on SKILL.md would exclude the skill from the gate entirely
    # (helper-executable / missing-metadata skills would pass undetected). With
    # replacement the file is still scanned, so ASCII signals survive and the
    # gate is never silently bypassed. OSError (truly unreadable) still skips.
    try:
        skill_text = skill_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    # Collect reference texts (references/*.md alongside SKILL.md).
    ref_texts: list[str] = []
    refs_dir = skill_dir / "references"
    if refs_dir.is_dir():
        for ref_path in sorted(refs_dir.glob("*.md")):
            try:
                ref_texts.append(ref_path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass

    # Combined text for signal detection across all prose files
    all_texts = [skill_text] + ref_texts

    reasons: list[str] = []
    if any(_skill_has_operative_ask_user_question(t) for t in all_texts):
        reasons.append("AskUserQuestion")
    if any(SKILL_CALL_RE.search(t) for t in all_texts):
        reasons.append("Skill(...)")
    if (
        skill_dir.name in EXTERNAL_CLI_WRAPPER_SKILLS
        or any(_skill_has_external_cli_template(t) for t in all_texts)
        or any(_skill_has_inline_external_cli(t) for t in all_texts)
    ):
        reasons.append("external-cli-wrapper")
    if _skill_has_helper_executable(skill_dir):
        reasons.append("helper-executable")
    return reasons


def _skill_runtime_metadata_drifts(skill_dir: Path) -> list[str]:
    """Drift strings for one runtime-sensitive skill's verification metadata."""
    reasons = _skill_runtime_verification_reasons(skill_dir)
    if not reasons:
        return []

    skill_path = skill_dir / "SKILL.md"
    frontmatter = _parse_skill_frontmatter(skill_path)
    drifts: list[str] = []
    reason_text = ", ".join(reasons)

    verified = frontmatter.get("verified-against-runtime")
    if verified is not True:
        drifts.append(
            f"SKILL RUNTIME METADATA {skill_dir.name}: runtime-sensitive "
            f"({reason_text}) but missing `verified-against-runtime: true`"
        )

    verified_at = frontmatter.get("runtime-verified-at")
    if not isinstance(verified_at, str) or not verified_at.strip():
        drifts.append(
            f"SKILL RUNTIME METADATA {skill_dir.name}: runtime-sensitive "
            f"({reason_text}) but missing `runtime-verified-at`"
        )
    else:
        normalized = verified_at.strip()
        if normalized in RUNTIME_METADATA_PLACEHOLDERS["runtime-verified-at"]:
            drifts.append(
                f"SKILL RUNTIME METADATA {skill_dir.name}: "
                "`runtime-verified-at` still carries a template placeholder"
            )
        elif not RUNTIME_DATE_RE.fullmatch(normalized):
            drifts.append(
                f"SKILL RUNTIME METADATA {skill_dir.name}: "
                f"`runtime-verified-at` must be YYYY-MM-DD, got {normalized!r}"
            )

    verified_note = frontmatter.get("runtime-verified-note")
    if not isinstance(verified_note, str) or not verified_note.strip():
        drifts.append(
            f"SKILL RUNTIME METADATA {skill_dir.name}: runtime-sensitive "
            f"({reason_text}) but missing `runtime-verified-note`"
        )
    else:
        normalized_note = verified_note.strip()
        if re.fullmatch(r"[>|][+-]?", normalized_note):
            drifts.append(
                f"SKILL RUNTIME METADATA {skill_dir.name}: "
                "`runtime-verified-note` must be an inline verification note, "
                "not a block scalar"
            )
        elif normalized_note in RUNTIME_METADATA_PLACEHOLDERS["runtime-verified-note"]:
            drifts.append(
                f"SKILL RUNTIME METADATA {skill_dir.name}: "
                "`runtime-verified-note` still carries a template placeholder"
            )

    return drifts


def runs_standalone(entries, dispatch_groups) -> bool:
    """True if any of a hook's manifest entries runs it outside the dispatcher.

    Two ways that happens, and the second is the one a plain (event, matcher)
    test misses: an entry outside every collapsed dispatch group, and an entry
    INSIDE one that declares `args` — `_dispatch.group_members` excludes such
    a member from the group, so the build keeps it as its own node and it
    runs on its own. Judged by (event, matcher) alone it looks
    dispatch-wrapped, and the @fail_open requirement was skipped for a hook
    that does need it (issue #1199 review). The groups come from the
    manifest, never from a literal here: a hardcoded (PreToolUse, Bash) kept
    treating every later group's members as standalone.
    """
    for e in entries:
        if e.get("args"):
            return True
        if (e.get("event"), e.get("matcher")) not in dispatch_groups:
            return True
    return False


def _has_fail_open_decorator(impl_path: Path) -> bool:
    """True iff some function in impl.py carries an `@fail_open` decorator.

    AST-based on purpose (Rule 16, #645): a substring scan would accept
    `fail_open` mentioned in a comment/docstring or imported-but-unapplied,
    none of which actually wraps the entrypoint at runtime. A syntactically
    invalid impl.py returns False — the hook cannot run at all, and the
    drift message points at the right file either way.
    """
    try:
        tree = ast.parse(impl_path.read_text())
    except (OSError, SyntaxError, ValueError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Name) and dec.id == "fail_open":
                    return True
                if isinstance(dec, ast.Attribute) and dec.attr == "fail_open":
                    return True
    return False


def _git_index_wrapper_modes() -> dict[str, str]:
    """Map top-level hooks/<file>.sh names to their git index mode string.

    Returns e.g. {"strike-counter.sh": "100755"}. Fails open to an empty dict
    when git is unavailable, times out, or the tree is not a git checkout
    (e.g. a marketplace install running the check by hand) — the filesystem
    half of Rule 6d still runs; only the committed-mode half is skipped.
    Untracked files simply do not appear (git's 755/644 dichotomy applies
    only to tracked content).
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-s", "--", "hooks"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0:
        return {}
    modes: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        # Format: "<mode> <sha> <stage>\t<path>"
        meta, _, path = line.partition("\t")
        if not re.fullmatch(r"hooks/[^/]+\.sh", path):
            continue
        fields = meta.split()
        if fields:
            modes[path[len("hooks/"):]] = fields[0]
    return modes


def _hook_dirs() -> list[Path]:
    """Return all per-hook directories under hooks/<role>/<name>/."""
    dirs: list[Path] = []
    hooks_root = _build.HOOKS_DIR
    for role_dir in sorted(hooks_root.iterdir()):
        if not role_dir.is_dir():
            continue
        if role_dir.name in {"_lib", "_generated", "__pycache__"}:
            continue
        if role_dir.name not in VALID_ROLES:
            continue
        for hook_dir in sorted(role_dir.iterdir()):
            if hook_dir.is_dir() and not hook_dir.name.startswith("_"):
                dirs.append(hook_dir)
    return dirs


def main() -> int:
    base = _build.load_base()
    manifest = _build.load_manifest()

    # ------------------------------------------------------------------
    # Manifest schema gate (#1173) — deliberately unnumbered: rule
    # renumbering is owned by the parallel #1172 change. Runs before every
    # numbered rule (and before expand_to_hooks_json) because they all
    # index into the manifest raw; a malformed manifest must fail here
    # with a file+entry+key diagnostic, not a KeyError traceback. The
    # validation itself lives in build-plugin-manifests.py (shared: the
    # build refuses to render from a malformed manifest with the same
    # gate). A malformed SCHEMA raises ValueError from
    # _build.assert_schema_supported — developer error, fail loud.
    # ------------------------------------------------------------------
    schema_drifts = _build.manifest_schema_drifts(manifest)
    if schema_drifts:
        print("plugin-manifest check FAILED:")
        for d in schema_drifts:
            print(f"  - {d}")
        return 1

    # Reverse hosts cross-check: load_platform validates each platform's
    # host_id against the schema's closed hosts enum; this direction catches
    # a stale enum value with no backing platform file.
    schema_hosts = set(_build.manifest_hosts_enum())
    platform_hosts = {
        p.get("host_id", p["platform"])
        for p in (
            _build.load_platform(f)
            for f in sorted(_build.PLATFORMS_DIR.glob("*.json"))
        )
    }
    stale_hosts = schema_hosts - platform_hosts
    if stale_hosts:
        drifts_early = ", ".join(sorted(stale_hosts))
        print("plugin-manifest check FAILED:")
        print(
            f"  - SCHEMA HOSTS ENUM hooks/manifest.schema.json: value(s) "
            f"{drifts_early} have no manifests/platforms/*.json with that "
            "host_id — remove them or add the platform file"
        )
        return 1

    hooks_source = _build.expand_to_hooks_json(manifest)
    # ADR-0002: must mirror build-plugin-manifests.main() so the expected
    # hooks.json collapses dispatch groups identically to the committed output.
    dispatch_groups = frozenset(
        (g["event"], g.get("matcher")) for g in manifest.get("dispatch_groups", [])
    )
    drifts: list[str] = []

    # ------------------------------------------------------------------
    # Rule 5 — generated artifacts byte-identical (drift check)
    # ------------------------------------------------------------------
    for platform_file in sorted(_build.PLATFORMS_DIR.glob("*.json")):
        platform = _build.load_platform(platform_file)
        host_id = platform.get("host_id", platform["platform"])
        for output in platform["outputs"]:
            out_path = REPO_ROOT / output["path"]
            expected_text = (
                json.dumps(
                    _build.render_output(
                        base, output, hooks_source, host_id, dispatch_groups
                    ),
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n"
            )
            actual = out_path.read_text() if out_path.exists() else ""
            if expected_text != actual:
                drifts.append(
                    f"DRIFT {output['path']}: regenerate with "
                    "./scripts/build-plugin-manifests.py"
                )

    # ------------------------------------------------------------------
    # Rule 1 — directory ↔ manifest cross-check
    # Rule 2 — role field matches parent directory name
    # Rule 3 — impl file exists on disk
    # ------------------------------------------------------------------
    manifest_by_name: dict[str, list[dict]] = {}
    for entry in manifest["hooks"]:
        manifest_by_name.setdefault(entry["name"], []).append(entry)

    on_disk: dict[str, Path] = {}
    for hook_dir in _hook_dirs():
        on_disk[hook_dir.name] = hook_dir
        role_on_disk = hook_dir.parent.name
        if hook_dir.name in OPT_IN_HOOKS:
            continue  # opt-in: no manifest entry expected
        if hook_dir.name not in manifest_by_name:
            drifts.append(
                f"UNREGISTERED hooks/{role_on_disk}/{hook_dir.name}: "
                f"directory exists but no manifest.json entry"
            )
            continue
        for entry in manifest_by_name[hook_dir.name]:
            if entry["role"] != role_on_disk:
                drifts.append(
                    f"ROLE MISMATCH {hook_dir.name}: directory says "
                    f"{role_on_disk!r}, manifest says {entry['role']!r}"
                )
            impl_file = "impl.sh" if entry.get("body") == "impl.sh" else "impl.py"
            if not (hook_dir / impl_file).exists():
                drifts.append(
                    f"MISSING IMPL hooks/{role_on_disk}/{hook_dir.name}/{impl_file}"
                )

    # Reverse: every manifest entry must have a directory
    for name in manifest_by_name:
        if name not in on_disk:
            drifts.append(
                f"MANIFEST GHOST {name}: manifest.json entry has no "
                f"hooks/<role>/{name}/ directory"
            )

    # ------------------------------------------------------------------
    # Rule 4 — completion-verify Stop ordering
    # ------------------------------------------------------------------
    expected_stop = ["completion-verify", "retrospect-mix-check",
                     "completion-signal-gate", "readonly-verify-deferral-gate",
                     "merge-state-claim-gate", "runtime-state-claim-gate",
                     "negative-existence-verdict-gate",
                     "artifact-verdict-evidence-gate",
                     "pr-report-destination-gate",
                     "pr-claim-mutation-gate",
                     "pr-anchor-existence-gate",
                     "proposal-premise-gate",
                     "strike-counter"]
    actual_stop: list[str] = []
    for entry in manifest["hooks"]:
        if entry["event"] == "Stop":
            actual_stop.append(entry["name"])
    if actual_stop != expected_stop:
        drifts.append(
            f"STOP ORDERING: manifest Stop order {actual_stop!r} != "
            f"expected {expected_stop!r}"
        )

    # ------------------------------------------------------------------
    # Rule 6 — runtime wrapper byte-identity
    #
    # ADR-0002 Phase 4 (#618): a member whose every registration is collapsed
    # into a dispatch group has no wrapper (invoked via _dispatch.sh). Those
    # filenames are excluded from the expected set AND asserted absent from disk
    # below, so a re-emitted orphan wrapper fails CI.
    # ------------------------------------------------------------------
    dispatch_only = _build.dispatch_only_wrappers(manifest)
    expected_wrappers: dict[str, str] = {}
    for entry in manifest["hooks"]:
        fname = _build._wrapper_filename(entry)
        if fname in dispatch_only:
            continue
        body = _build._wrapper_body(entry)
        if fname in expected_wrappers and expected_wrappers[fname] != body:
            drifts.append(
                f"WRAPPER BODY CONFLICT {fname}: manifest yields two "
                "different bodies for the same wrapper"
            )
            continue
        expected_wrappers[fname] = body

    # ADR-0002: the dispatch runner wrapper is emitted by emit_wrappers outside
    # the manifest hook loop (it has no manifest entry), so add it here or a
    # stale/missing hooks/_dispatch.sh slips through CI.
    expected_wrappers[_build.DISPATCH_WRAPPER_NAME] = _build.WRAPPER_DISPATCH_TEMPLATE

    # Rule 6c — opt-in hooks (#605): not in the manifest, but emit_wrappers still
    # generates hooks/<name>.sh for their documented invocation path
    # (OPT_IN_HOOKS). Mirror that emit so the existence + byte-identity loop below
    # covers the opt-in wrapper class too — otherwise a stale/missing opt-in
    # wrapper slips through CI (Rule 6 derived expected_wrappers only from manifest
    # entries). A manifest entry of the same name wins (matches emit_wrappers).
    for opt_in_name, opt_in_role in _build.OPT_IN_HOOKS.items():
        fname = f"{opt_in_name}.sh"
        if fname in expected_wrappers:
            continue
        expected_wrappers[fname] = _build.WRAPPER_PY_TEMPLATE.format(
            role=opt_in_role, name=opt_in_name, baked_args=""
        )

    # Rule 6d (#1172): the build chmods every emitted wrapper to 0o755
    # (emit_wrappers), but Rule 6 compared content only — a 644 wrapper was
    # byte-identical and passed while being un-executable at runtime. Assert
    # the executable bit on disk, and (when the tree is a git checkout) the
    # committed index mode, respecting git's 100755/100644 dichotomy.
    git_index_modes = _git_index_wrapper_modes()
    for fname, expected_body in expected_wrappers.items():
        wrapper_path = _build.HOOKS_DIR / fname
        if not wrapper_path.exists():
            drifts.append(
                f"WRAPPER MISSING hooks/{fname}: run "
                "./scripts/build-plugin-manifests.py"
            )
            continue
        actual_body = wrapper_path.read_text()
        if actual_body != expected_body:
            drifts.append(
                f"WRAPPER DRIFT hooks/{fname}: regenerate with "
                "./scripts/build-plugin-manifests.py"
            )
        if not os.access(wrapper_path, os.X_OK):
            drifts.append(
                f"WRAPPER MODE hooks/{fname}: missing executable bit on disk — "
                f"`chmod 755 hooks/{fname}` or re-run "
                "./scripts/build-plugin-manifests.py (Rule 6d, #1172)"
            )
        index_mode = git_index_modes.get(fname)
        if index_mode is not None and index_mode != "100755":
            drifts.append(
                f"WRAPPER MODE hooks/{fname}: committed git index mode is "
                f"{index_mode}, expected 100755 — "
                f"`git update-index --chmod=+x hooks/{fname}` (Rule 6d, #1172)"
            )

    # Rule 6b — dispatch-only members must NOT carry a wrapper on disk. The
    # dispatcher imports their impl.py directly; a lingering hooks/<name>.sh is
    # dead weight and re-introduces the Approach-A drift #618 removed.
    for fname in sorted(dispatch_only):
        if (_build.HOOKS_DIR / fname).exists():
            drifts.append(
                f"ORPHAN WRAPPER hooks/{fname}: dispatch-only member must not "
                "carry a wrapper — remove it (invoked via _dispatch.sh)"
            )

    # Rule 6e (#1172) — reverse sweep: every hooks/*.sh on disk must be in the
    # generator's output set. Rule 6 walks the expected wrappers forward and
    # Rule 6b pins the dispatch-only names, but a stray file matching neither
    # set — a renamed hook's leftover, a hand-written wrapper — passed
    # silently. Mirrors the ORPHAN STUB reverse direction of Rule 15.
    for wrapper_path in sorted(_build.HOOKS_DIR.glob("*.sh")):
        fname = wrapper_path.name
        if fname in expected_wrappers:
            continue
        if fname in dispatch_only:
            continue  # already flagged as ORPHAN WRAPPER by Rule 6b above
        drifts.append(
            f"ORPHAN WRAPPER hooks/{fname}: not a generated wrapper (manifest "
            "entry, opt-in hook, or dispatch runner) — remove it (stale output "
            "of a renamed or deleted hook?)"
        )

    # ------------------------------------------------------------------
    # Rule 10 — spec.md existence + manifest `hosts` shape validation
    # ------------------------------------------------------------------
    for entry in manifest["hooks"]:
        hosts = entry.get("hosts")
        if hosts is not None:
            if not isinstance(hosts, list):
                drifts.append(
                    f"INVALID hosts {entry['name']}: must be a list of strings, "
                    f"got {type(hosts).__name__}"
                )
            elif len(hosts) == 0:
                drifts.append(
                    f"INVALID hosts {entry['name']}: empty list drops the hook "
                    "from every platform — omit the field to mean 'all hosts'"
                )
            elif not all(isinstance(h, str) for h in hosts):
                drifts.append(
                    f"INVALID hosts {entry['name']}: every entry must be a string"
                )
        spec = REPO_ROOT / "hooks" / entry["role"] / entry["name"] / "spec.md"
        if not spec.exists():
            drifts.append(
                f"MISSING SPEC hooks/{entry['role']}/{entry['name']}/spec.md "
                "(hook registered in manifest.json)"
            )

    # ------------------------------------------------------------------
    # Codex adapter symlinks (unnumbered auxiliary check)
    # ------------------------------------------------------------------
    for name in _build.FORWARDED_DIRS:
        link = _build.ADAPTER_SHELL / name
        if not link.is_symlink():
            drifts.append(
                f"MISSING plugins/praxis/{name}: expected symlink → ../../{name}"
            )
            continue
        target = os.readlink(link)
        if target != f"../../{name}":
            drifts.append(
                f"BAD LINK plugins/praxis/{name}: points at {target!r}, "
                f"expected '../../{name}'"
            )

    # ------------------------------------------------------------------
    # Rule 7 — INDEX.md cross-check
    #
    # docs/hook/INDEX.md is the only hand-maintained per-hook list left
    # (#1306). ARCHITECTURE.md → Hook index is a pointer to it and to the
    # generated operating matrix, so it is no longer required to name every
    # hook — the matrix is drift-checked as a generated artifact instead.
    # ------------------------------------------------------------------
    index_md = (REPO_ROOT / "docs" / "hook" / "INDEX.md").read_text()
    seen_names: set[str] = set()
    for entry in manifest["hooks"]:
        name = entry["name"]
        if name in seen_names:
            continue
        seen_names.add(name)
        if name not in index_md:
            drifts.append(
                f"MISSING INDEX docs/hook/INDEX.md: {name} "
                "(registered in manifest.json but not in INDEX.md)"
            )

    # ------------------------------------------------------------------
    # Rule 8 — Supported hosts cross-check
    # ------------------------------------------------------------------
    manifest_hosts: dict[str, list[str] | None] = {}
    for entry in manifest["hooks"]:
        # First registration wins (multi-event entries share the same hosts)
        if entry["name"] not in manifest_hosts:
            manifest_hosts[entry["name"]] = entry.get("hosts")

    for hook_dir in _hook_dirs():
        spec_file = hook_dir / "spec.md"
        if not spec_file.exists():
            continue
        hook_name = hook_dir.name
        if hook_name not in manifest_hosts:
            continue
        spec_text = spec_file.read_text()
        hosts_value: str | None = None
        for line in spec_text.splitlines()[:10]:
            if line.strip().lower().startswith("supported hosts:"):
                hosts_value = line.split(":", 1)[1].strip()
                break
        if hosts_value is None:
            continue
        json_hosts = manifest_hosts[hook_name]
        if hosts_value.lower() == "all":
            if json_hosts is not None:
                drifts.append(
                    f"FAIL hosts mismatch {hook_name}: spec='all' "
                    f"manifest={json_hosts!r} (remove hosts to mean all)"
                )
        else:
            raw_tokens = hosts_value.split(",")
            spec_set = {
                t.split("(")[0].strip()
                for t in raw_tokens
                if t.split("(")[0].strip()
            }
            json_set = set(json_hosts) if json_hosts is not None else set()
            if spec_set != json_set:
                drifts.append(
                    f"FAIL hosts mismatch {hook_name}: spec={sorted(spec_set)!r} "
                    f"manifest={sorted(json_set)!r}"
                )

    # ------------------------------------------------------------------
    # Rule 9 — Release version wiring (#1172)
    #
    # The previous incarnation cross-compared the version fields embedded in
    # the committed versioned artifacts and flagged disagreement. That check
    # was provably vacuous: Rule 5 renders every artifact from the single
    # VERSION source and demands byte-identity (a missing file reads as ""
    # and drifts too), so two artifacts can only disagree when Rule 5 has
    # already failed — the compare could never fire on its own, and its
    # marketplace fallback branch was dead code (marketplace was not even in
    # its versioned-kinds set). Deleted; replaced with checks that CAN fire:
    #
    #   (a) VERSION (the authoritative build input, see load_base) equals the
    #       "." version release-please tracks in .release-please-manifest.json
    #       — a mismatch means the next release PR computes its bump from a
    #       different version than the one the artifacts embed.
    #   (b) every platform output of a versioned kind (plugin, marketplace,
    #       agent-plugin — marketplace embeds version fields too) is listed
    #       in release-please-config.json `extra-files`, so a release
    #       bump rewrites its embedded versions. A new platform output added
    #       without the extra-files entry would otherwise ship stale versions
    #       on the first release after merge, with no gate noticing.
    # ------------------------------------------------------------------
    version_file = (REPO_ROOT / "VERSION").read_text().strip()
    rp_manifest_path = REPO_ROOT / ".release-please-manifest.json"
    try:
        rp_manifest = json.loads(rp_manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        drifts.append(
            f"RELEASE WIRING .release-please-manifest.json: unreadable ({exc})"
        )
    else:
        rp_version = rp_manifest.get(".")
        if rp_version != version_file:
            drifts.append(
                f"VERSION MISMATCH: VERSION file says {version_file!r} but "
                f".release-please-manifest.json \".\" says {rp_version!r} — "
                "the two must agree (Rule 9, #1172)"
            )

    rp_config_path = REPO_ROOT / "release-please-config.json"
    try:
        rp_config = json.loads(rp_config_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        drifts.append(
            f"RELEASE WIRING release-please-config.json: unreadable ({exc})"
        )
    else:
        extra_files = (
            rp_config.get("packages", {}).get(".", {}).get("extra-files", [])
        )
        # Keyed by path but keeping the whole entry: a marketplace output carries
        # `version` both at the top level and inside plugins[0], so a narrowed
        # `$.version` updates one and leaves the other stale while the path is
        # still listed. Checking presence alone cannot see that.
        extra_specs = {}
        for entry in extra_files:
            path = entry.get("path") if isinstance(entry, dict) else entry
            if not isinstance(path, str):
                # Reported rather than raised: a KeyError here aborts the whole
                # checker, so the one diagnostic that would name the malformed
                # entry never prints.
                drifts.append(
                    f"RELEASE WIRING release-please-config.json: extra-files "
                    f"entry {entry!r} has no string 'path' — release-please "
                    "cannot resolve it (Rule 9, #1172)"
                )
                continue
            extra_specs[path] = entry
        versioned_kinds = {"plugin", "marketplace", "agent-plugin"}
        for platform_file in sorted(_build.PLATFORMS_DIR.glob("*.json")):
            platform = json.loads(platform_file.read_text())
            for output in platform["outputs"]:
                if output["kind"] not in versioned_kinds:
                    continue
                path = output["path"]
                if path not in extra_specs:
                    drifts.append(
                        f"RELEASE WIRING {path}: versioned artifact "
                        f"(kind={output['kind']}) is not listed in "
                        "release-please-config.json extra-files — release-please "
                        "would leave its embedded version fields stale "
                        "(Rule 9, #1172)"
                    )
                    continue
                entry = extra_specs[path]
                if not isinstance(entry, dict):
                    drifts.append(
                        f"RELEASE WIRING {path}: extra-files entry is a bare "
                        "string — release-please needs an object carrying "
                        'type and jsonpath (Rule 9, #1172)'
                    )
                    continue
                if entry.get("type") != "json":
                    drifts.append(
                        f"RELEASE WIRING {path}: extra-files type is "
                        f"{entry.get('type')!r}, expected 'json' — every "
                        "versioned artifact here is a JSON document "
                        "(Rule 9, #1172)"
                    )
                if entry.get("jsonpath") != RELEASE_JSONPATH:
                    drifts.append(
                        f"RELEASE WIRING {path}: extra-files jsonpath is "
                        f"{entry.get('jsonpath')!r}, expected "
                        f"{RELEASE_JSONPATH!r} — a narrower path updates only "
                        "the version fields it names and silently leaves its "
                        "siblings stale (Rule 9, #1172)"
                    )

    # ------------------------------------------------------------------
    # Rule 26 — Agent Plugins portable manifest shape (#1219)
    #
    # Two failures Rule 5's byte-identity check cannot see, because both stay
    # reproducible: the build renders them, the checker re-renders the same
    # thing, and the diff is clean while the manifest is still wrong.
    #
    #   (a) $schema drifts off 1.0.0, the only published spec version. The
    #       spec makes that fatal with no way back — "A missing or unsupported
    #       `$schema` rejects the plugin" — and clients find this file by path
    #       (plugin.json at the plugin root), so a newer pin does not route
    #       around it; it just fails to load.
    #   (b) a host-specific key (skills, hooks, mcpServers, apps, interface)
    #       reaches the portable manifest. This one is repo policy, NOT a spec
    #       requirement: the spec says to "report and ignore each unknown
    #       top-level field, then continue if the manifest is otherwise
    #       valid". We reject it because a host path that silently does
    #       nothing on every conformant client is worse than a failed check.
    #
    # Spec quotes: agent-plugins.org/client-implementers/loading-and-discovery
    # ------------------------------------------------------------------
    for platform_file in sorted(_build.PLATFORMS_DIR.glob("*.json")):
        platform = json.loads(platform_file.read_text())
        for output in platform["outputs"]:
            if output["kind"] != "agent-plugin":
                continue
            path = output["path"]
            try:
                rendered = json.loads((REPO_ROOT / path).read_text())
            except (OSError, json.JSONDecodeError) as exc:
                drifts.append(f"AGENT PLUGIN {path}: unreadable ({exc})")
                continue
            # Reported rather than raised: a top-level array or scalar makes
            # the .get() below an AttributeError, which aborts the checker and
            # takes every other rule's diagnostics down with it.
            if not isinstance(rendered, dict):
                drifts.append(
                    f"AGENT PLUGIN {path}: top-level JSON is "
                    f"{type(rendered).__name__}, expected an object — the "
                    "spec requires a JSON object manifest (Rule 26, #1219)"
                )
                continue
            schema = rendered.get("$schema")
            if schema != _build.AGENT_PLUGIN_SCHEMA_URI:
                drifts.append(
                    f"AGENT PLUGIN {path}: $schema is {schema!r}, expected "
                    f"{_build.AGENT_PLUGIN_SCHEMA_URI!r} — 1.0.0 is the only "
                    "published spec version, and an unsupported $schema "
                    "rejects the plugin outright (Rule 26, #1219)"
                )
            for key in ("skills", "hooks", "mcpServers", "apps", "interface"):
                if key in rendered:
                    drifts.append(
                        f"AGENT PLUGIN {path}: carries host-specific key "
                        f"{key!r} — conformant clients ignore unknown "
                        "top-level fields, so this path would silently do "
                        "nothing; put it in the host's own manifest or under "
                        "extensions (Rule 26, #1219)"
                    )

    # ------------------------------------------------------------------
    # Rule 11 — runtime-sensitive skill metadata
    # ------------------------------------------------------------------
    for skill_dir in _skill_dirs():
        drifts.extend(_skill_runtime_metadata_drifts(skill_dir))

    # ------------------------------------------------------------------
    # Rule 12 — Skill surface freeze (#465)
    # ------------------------------------------------------------------
    on_disk_skills = {d.name for d in _skill_dirs()}
    unexpected = on_disk_skills - EXPECTED_SKILLS
    removed = EXPECTED_SKILLS - on_disk_skills
    if unexpected:
        drifts.append(
            f"UNEXPECTED SKILL(S): {sorted(unexpected)!r} — present on disk "
            "but not declared in EXPECTED_SKILLS. If intentional, update "
            "EXPECTED_SKILLS in scripts/constants.py."
        )
    if removed:
        drifts.append(
            f"REMOVED SKILL(S): {sorted(removed)!r} — declared in "
            "EXPECTED_SKILLS but missing on disk. If intentional, update "
            "EXPECTED_SKILLS in scripts/constants.py."
        )

    # ------------------------------------------------------------------
    # Rule 13 — Doc skill-count invariant (#498, #1177)
    #
    # AGENTS.md carries an explicit "## Skills (N)" header whose count must
    # equal len(EXPECTED_SKILLS).  AGENTS.md, docs/skills.md, and the
    # using-praxis onboarding skill embed skill names inside table cells as
    # `backtick` tokens; every EXPECTED_SKILLS member must appear in each
    # document (13b/13c/13d), using-praxis table rows must not name phantom
    # skills (13d), docs/skills.md keyword cells must quote the frontmatter
    # verbatim (13e), and the three tier-table copies must stay
    # normalized-identical (13f).
    #
    # Parsing is intentionally coarse — we match the pattern
    # `skill-name` (backtick-delimited) so the check is robust to table
    # reformatting while still catching silent drift.
    # ------------------------------------------------------------------
    import re as _re  # local import — avoids polluting module scope

    agents_md_path = REPO_ROOT / "AGENTS.md"
    agents_text = agents_md_path.read_text()

    # Rule 13a — AGENTS.md "## Skills (N)" count header must match
    count_match = _re.search(r"^##\s+Skills\s+\((\d+)\)", agents_text, _re.MULTILINE)
    if count_match is None:
        drifts.append(
            "DOC SKILL COUNT MISSING AGENTS.md: expected '## Skills (N)' header — "
            "add it to keep the count in sync with EXPECTED_SKILLS"
        )
    else:
        declared_count = int(count_match.group(1))
        expected_count = len(EXPECTED_SKILLS)
        if declared_count != expected_count:
            drifts.append(
                f"DOC SKILL COUNT AGENTS.md: header says {declared_count} but "
                f"EXPECTED_SKILLS has {expected_count} — update the header"
            )

    # Shared plumbing for Rules 13b-13f — each doc surface reduces to token
    # sets, and the two drift directions (missing skill / phantom skill) are
    # reported identically for every surface.
    def _norm_ws(s: str) -> str:
        return _re.sub(r"\s+", " ", s).strip()

    def _table_cells(line: str) -> list[str]:
        """`| a | b |` → ["a", "b"]; non-table lines → []."""
        if not line.lstrip().startswith("|"):
            return []
        return [c.strip() for c in line.strip().strip("|").split("|")]

    def _doc_roster_drifts(
        doc_label: str,
        present_tokens: set[str],
        missing_hint: str,
        listed_tokens: set[str] | None = None,
        phantom_hint: str | None = None,
    ) -> list[str]:
        out: list[str] = []
        missing = EXPECTED_SKILLS - present_tokens
        if missing:
            out.append(
                f"DOC SKILL LIST {doc_label}: {sorted(missing)!r} declared in "
                f"EXPECTED_SKILLS but {missing_hint}"
            )
        if listed_tokens is not None:
            phantom = listed_tokens - EXPECTED_SKILLS
            if phantom:
                out.append(
                    f"DOC SKILL LIST {doc_label}: {sorted(phantom)!r} {phantom_hint}"
                )
        return out

    # Rule 13b — every EXPECTED_SKILLS member appears in AGENTS.md
    agents_backtick_skills = set(_re.findall(r"`([^`]+)`", agents_text))
    drifts.extend(
        _doc_roster_drifts(
            "AGENTS.md",
            agents_backtick_skills,
            "not found as `backtick` tokens — add them to the skill table",
        )
    )

    # Rule 13c — every EXPECTED_SKILLS member appears as the first column of
    # a docs/skills.md table row.  We match `| \`skill-name\` |` so that a
    # skill name mentioned only in a description cell (cross-reference noise)
    # does not satisfy the check.  The table moved out of README.md when the
    # README became a landing document (#1088); README.md now carries only a
    # pointer, so enforcing the roster there would fight that split.
    skills_doc_path = REPO_ROOT / "docs" / "skills.md"
    if not skills_doc_path.exists():
        drifts.append(
            "DOC SKILL LIST docs/skills.md: file missing — it is the skill "
            "roster README.md points at"
        )
        skills_doc_text = ""
    else:
        skills_doc_text = skills_doc_path.read_text()
    skills_doc_table = set(
        _re.findall(r"^\|\s*`([^`]+)`\s*\|", skills_doc_text, _re.MULTILINE)
    )
    drifts.extend(
        _doc_roster_drifts(
            "docs/skills.md",
            skills_doc_table,
            "not found as a first-column `backtick` token in a table row — "
            "add them to the skill table",
        )
    )

    # Rule 13d — the using-praxis onboarding entry point routes every skill
    # (#1177).  A skill counts as routed only when a table row names it:
    # either the first column of a category table, or a `backtick` token in
    # a routing cell of the Common Scenarios table (rows whose first cell is
    # a "quoted situation").  Prose mentions do not satisfy the check.  The
    # reverse direction runs over the same token set, so a typo in either a
    # category row or a scenario routing cell is caught as a phantom skill.
    using_praxis_path = REPO_ROOT / "skills" / "using-praxis" / "SKILL.md"
    if not using_praxis_path.exists():
        drifts.append(
            "DOC SKILL LIST skills/using-praxis/SKILL.md: file missing — it is "
            "the onboarding entry point that must route every skill"
        )
        using_praxis_text = ""
    else:
        using_praxis_text = using_praxis_path.read_text()
    using_praxis_routed: set[str] = set()
    for line in using_praxis_text.splitlines():
        cells = _table_cells(line)
        if not cells:
            continue
        first_col = _re.fullmatch(r"`([^`]+)`", cells[0])
        if first_col:
            # category-table row — the first column is the skill name
            using_praxis_routed.add(first_col.group(1))
        elif cells[0].startswith('"'):
            # scenario-table row — every token in the routing cells is a skill
            for cell in cells[1:]:
                using_praxis_routed.update(_re.findall(r"`([^`]+)`", cell))
    if using_praxis_path.exists():
        drifts.extend(
            _doc_roster_drifts(
                "skills/using-praxis/SKILL.md",
                using_praxis_routed,
                "not routed by any table row — the onboarding entry point must "
                "route every skill (a prose mention does not count); add them "
                "to a category table",
                listed_tokens=using_praxis_routed,
                phantom_hint="named in a category-table first column or a "
                "scenario routing cell but not declared in EXPECTED_SKILLS — "
                "fix the typo or remove the stale row",
            )
        )

    # Rule 13e — docs/skills.md trigger-keyword cells mirror the skill's
    # frontmatter trigger fields verbatim (#1177).  Every `backtick` keyword
    # in a roster row's second column must appear double-quoted in that
    # skill's frontmatter `when_to_use` or `description` (whitespace-
    # normalized, so YAML `>` folding does not count as drift).  Quoted
    # match, not substring — `skill spec` must not pass just because
    # `praxis skill spec` is quoted.
    #
    # Since #1331 the trigger phrases live in `when_to_use` (the documented
    # field the runtime appends to `description` in the skill listing) and
    # `description` says only what the skill does.  Both fields are read so
    # a phrase quoted in either counts, and so a skill that has not moved its
    # clause yet is still mirrored rather than silently exempt.
    def _frontmatter_triggers(skill: str) -> str:
        try:
            text = (REPO_ROOT / "skills" / skill / "SKILL.md").read_text()
        except OSError:
            return ""
        fm = _re.match(r"---\n(.*?)\n---\n", text, _re.DOTALL)
        if not fm:
            return ""
        parts: list[str] = []
        for key in ("description", "when_to_use"):
            field = _re.search(
                rf"^{key}:(.*?)(?=^\S|\Z)", fm.group(1), _re.DOTALL | _re.MULTILINE
            )
            if not field:
                continue
            # Everything from `Do NOT activate on` onward lists phrases that
            # must NOT route to the skill. Searching the whole field would
            # accept one of those as a valid trigger keyword, so a roster row
            # could list `strike a balance` and pass. The cut is per field:
            # a negative clause in `description` must not swallow the
            # positive triggers that follow in `when_to_use`.
            parts.append(_norm_ws(field.group(1)).split("Do NOT activate on")[0].rstrip())
        return " ".join(parts)

    for line in skills_doc_text.splitlines():
        cells = _table_cells(line)
        if len(cells) < 2:
            continue
        first_col = _re.fullmatch(r"`([^`]+)`", cells[0])
        if not first_col or first_col.group(1) not in EXPECTED_SKILLS:
            continue
        skill_name = first_col.group(1)
        triggers = _frontmatter_triggers(skill_name)
        # Both directions, because a mirror contract broken either way is still
        # broken: a row listing a phrase the frontmatter never claims routes
        # readers to a keyword that does not trigger, and a frontmatter quoting
        # a phrase the row omits hides a live trigger from the roster. Only the
        # first direction has a failing test upstream of it, so the second is
        # the one that silently drifts.
        documented = {_norm_ws(k) for k in _re.findall(r"`([^`]+)`", cells[1])}
        # Every quoted phrase left in the trigger fields is a trigger: the
        # negative clause was already cut above, so no positive-clause parse
        # is needed.
        quoted = {_norm_ws(k) for k in _re.findall(r'"([^"]+)"', triggers)}
        for keyword in sorted(documented - quoted):
            drifts.append(
                f"DOC KEYWORD DRIFT docs/skills.md: `{skill_name}` row lists "
                f"{keyword!r} but the skill's frontmatter (when_to_use / "
                "description) does not quote it — keyword cells mirror the "
                "frontmatter verbatim; fix the row or the frontmatter"
            )
        for keyword in sorted(quoted - documented):
            drifts.append(
                f"DOC KEYWORD DRIFT docs/skills.md: `{skill_name}` frontmatter "
                f"quotes {keyword!r} but the row does not list it — keyword "
                "cells mirror the frontmatter (when_to_use / description) "
                "verbatim; fix the row or the frontmatter"
            )

    # Rule 13f — the compatibility-tier table is maintained in three places
    # (README.md, AGENTS.md, skills/using-praxis/SKILL.md); their data rows
    # must stay identical after normalization (backticks stripped, whitespace
    # collapsed) so tier membership cannot drift between copies (#1177).
    # README.md is the reference copy (canonical per issue #1177).
    def _tier_rows(text: str) -> list[tuple[str, ...]]:
        rows = []
        for line in text.splitlines():
            cells = _table_cells(line)
            if len(cells) >= 3 and _re.fullmatch(
                r"\*\*(Standalone|Enhanced|Full|Multi-provider)\*\*", cells[0]
            ):
                rows.append(tuple(_norm_ws(c.replace("`", "")) for c in cells[:3]))
        return rows

    readme_text = (REPO_ROOT / "README.md").read_text()
    reference_tiers = _tier_rows(readme_text)
    if not reference_tiers:
        drifts.append(
            "TIER TABLE MISSING README.md: no compatibility-tier rows found — "
            "it is the reference copy the other two are checked against"
        )
    else:
        for label, text in (
            ("AGENTS.md", agents_text),
            ("skills/using-praxis/SKILL.md", using_praxis_text),
        ):
            rows = _tier_rows(text)
            if rows != reference_tiers:
                drifts.append(
                    f"TIER TABLE DRIFT {label}: normalized tier rows differ "
                    f"from README.md's — got {rows!r}, expected "
                    f"{reference_tiers!r}"
                )

    # ------------------------------------------------------------------
    # Rule 14 — dispatch-group ↔ build/runtime consistency (ADR-0002, #617)
    #
    # Each (event, matcher) in manifest.dispatch_groups is collapsed by the
    # build (filter_hooks_for_host) into ONE dispatcher node, and resolved at
    # runtime by _dispatch.group_members. Those are two independent readers of
    # the manifest; this rule ties them — and the committed hooks.json artifact —
    # together so a future manifest/schema edit cannot silently break the
    # collapse (drop a member, leave a stray per-member node, emit two dispatcher
    # nodes, or bake the wrong host into the dispatcher command). For every
    # platform that emits a hooks.json, per host:
    #   (a) the (event, matcher) group holds exactly ONE node and it is the
    #       dispatcher wrapper carrying `event matcher host` args when ≥1 member
    #       survives the host filter; ZERO nodes when the host filters all
    #       members — verified by dispatch_node_drifts() above;
    #   (b) group_members(event, matcher, host) equals the manifest-derived
    #       member set for that host, with no duplicates, and every resolved
    #       impl.py exists on disk.
    # ------------------------------------------------------------------
    def _manifest_members_for(
        event: str, matcher: str | None, host: str
    ) -> tuple[set[str], set[str], set[str]]:
        """Return `(collapsible_names, args_names, args_wrappers)` for a group.

        args-declaring entries are excluded from the dispatch member set on
        BOTH sides (the build keeps them standalone in `filter_hooks_for_host`;
        the runtime excludes them in `group_members` — issue #1199 review), so
        they are returned separately for the node-shape check. Multi-event
        hooks are flat sibling entries (one object per event); the nested
        "entries" form had zero manifest uses and was removed (issue #1169).
        """
        names: set[str] = set()
        args_names: set[str] = set()
        args_wrappers: set[str] = set()
        for hook in manifest["hooks"]:
            hosts = hook.get("hosts")
            if hosts is not None and host not in hosts:
                continue
            if hook.get("event") == event and hook.get("matcher") == matcher:
                if hook.get("args"):
                    args_names.add(hook["name"])
                    # The node carries the WRAPPER filename, which bakes in
                    # `wrapper_suffix` — deriving it from the bare name made a
                    # correct `<name>-pre.sh` node read as a leak.
                    args_wrappers.add(_build._wrapper_filename(hook))
                else:
                    names.add(hook["name"])
        return names, args_names, args_wrappers

    # Sentinel canary: the build renders DISPATCH_NO_MATCHER_ARG into a
    # matcher-less dispatcher node's command; the runtime maps NO_MATCHER_ARG
    # back to None in main(). If the two constants ever diverge, a matcher-less
    # group resolves ZERO members at runtime — errorlessly disabling every
    # member — so the pairing is pinned here.
    if _build.DISPATCH_NO_MATCHER_ARG != _dispatch.NO_MATCHER_ARG:
        drifts.append(
            "DISPATCH SENTINEL DRIFT: build DISPATCH_NO_MATCHER_ARG="
            f"{_build.DISPATCH_NO_MATCHER_ARG!r} != runtime "
            f"_dispatch.NO_MATCHER_ARG={_dispatch.NO_MATCHER_ARG!r} — a "
            "matcher-less dispatcher node would resolve zero members at runtime"
        )

    hooks_outputs: list[tuple[str, Path]] = []
    for platform_file in sorted(_build.PLATFORMS_DIR.glob("*.json")):
        platform = _build.load_platform(platform_file)
        host_id = platform.get("host_id", platform["platform"])
        for output in platform["outputs"]:
            if output["kind"] == "hooks":
                hooks_outputs.append((host_id, REPO_ROOT / output["path"]))

    for event, matcher in sorted(dispatch_groups, key=lambda em: (em[0], em[1] or "")):
        for host_id, hooks_path in hooks_outputs:
            expected_members, args_excluded, args_wrappers = _manifest_members_for(
                event, matcher, host_id
            )

            # (b) runtime resolution must match the manifest, with no dup, and
            #     every resolved impl on disk.
            resolved = _dispatch.group_members(event, matcher, host_id)
            resolved_names = [name for _role, name, _impl in resolved]
            if len(resolved_names) != len(set(resolved_names)):
                drifts.append(
                    f"DISPATCH DUP {event}/{matcher} host={host_id}: "
                    f"group_members resolves a hook more than once "
                    f"({sorted(resolved_names)})"
                )
            if set(resolved_names) != expected_members:
                drifts.append(
                    f"DISPATCH MEMBER DRIFT {event}/{matcher} host={host_id}: "
                    f"group_members={sorted(set(resolved_names))} != "
                    f"manifest={sorted(expected_members)} (args-declaring members are "
                    f"excluded on both sides — excluded here: "
                    f"{sorted(args_excluded)})"
                )
            for _role, name, impl in resolved:
                if not impl.exists():
                    drifts.append(
                        f"DISPATCH IMPL MISSING {event}/{matcher} host={host_id}: "
                        f"{name} -> {impl} does not exist on disk"
                    )

            # (a) committed hooks.json node shape — exactly one dispatcher node,
            #     no leaked member node, correct host args.
            if not hooks_path.exists():
                drifts.append(
                    f"DISPATCH HOOKS MISSING {hooks_path}: cannot verify "
                    f"{event}/{matcher} collapse"
                )
                continue
            hooks_json = json.loads(hooks_path.read_text())
            drifts.extend(
                dispatch_node_drifts(
                    hooks_json,
                    event,
                    matcher,
                    host_id,
                    expected_members,
                    _build.DISPATCH_WRAPPER_NAME,
                    args_wrappers=args_wrappers,
                )
            )

    # ------------------------------------------------------------------
    # Rule 15 — docs/hook stub parity (#606)
    #
    # Every hook dir (manifest entries + OPT_IN_HOOKS) owns a 1-line
    # docs/hook/<name>.md redirect stub (ADR-0001 §337-338), byte-identical to
    # the generator output. The reverse direction catches an orphan stub — a
    # docs/hook/*.md with no backing hook dir — except INDEX.md and the
    # hand-written NON_HOOK_DOCS allowlist.
    # ------------------------------------------------------------------
    expected_stubs = {
        name: _build.doc_stub_body(role, name)
        for name, role in _build.hook_identities(manifest).items()
    }
    for name, expected_body in sorted(expected_stubs.items()):
        stub_path = _build.DOCS_HOOK_DIR / f"{name}.md"
        if not stub_path.exists():
            drifts.append(
                f"STUB MISSING docs/hook/{name}.md: run "
                "./scripts/build-plugin-manifests.py"
            )
            continue
        if stub_path.read_text() != expected_body:
            drifts.append(
                f"STUB DRIFT docs/hook/{name}.md: regenerate with "
                "./scripts/build-plugin-manifests.py"
            )
    for stub_path in sorted(_build.DOCS_HOOK_DIR.glob("*.md")):
        stem = stub_path.stem
        if stem == "INDEX" or stem in _build.NON_HOOK_DOCS:
            continue
        if stem not in expected_stubs:
            drifts.append(
                f"ORPHAN STUB docs/hook/{stem}.md: no backing hook dir — "
                "remove it, or add to NON_HOOK_DOCS if it is a real doc"
            )

    # ------------------------------------------------------------------
    # Rule 16 — standalone hooks must apply @fail_open (#645)
    #
    # Dispatch-group members get `_hook_runtime.fail_open` wrapping at
    # runtime (`_dispatch.run_one`); every other execution path runs
    # impl.py bare, so the impl itself must carry an `@fail_open`
    # decorator (on main(), or on a zero-arg `_entry()` for argv-style
    # mains). A hook is dispatch-covered iff ALL its manifest entries
    # sit in a `dispatch_groups` pair; opt-in hooks are standalone by
    # definition. impl.sh bodies are exempt (no Python entrypoint).
    # The rule is one-directional: a redundant decorator in a dispatched
    # member is harmless (double-wrap is a no-op) and not flagged.
    # Detection is AST-based (decorator on some function), not substring:
    # `fail_open` appearing only in a comment/docstring, or imported but
    # never applied, must NOT satisfy the invariant (codex review P2).
    # ------------------------------------------------------------------
    for name, role in sorted(_build.hook_identities(manifest).items()):
        impl_path = _build.HOOKS_DIR / role / name / "impl.py"
        if not impl_path.exists():
            continue  # impl.sh body or missing impl — Rule 3 covers the latter
        if name in OPT_IN_HOOKS:
            standalone = True
        else:
            standalone = runs_standalone(manifest_by_name.get(name, []), dispatch_groups)
        if standalone and not _has_fail_open_decorator(impl_path):
            drifts.append(
                f"FAIL-OPEN MISSING hooks/{role}/{name}/impl.py: hook runs "
                "standalone (not dispatch-wrapped) but no function carries "
                "the @fail_open decorator — decorate main() (DESIGN.md, #645)"
            )

    # ------------------------------------------------------------------
    # Rule 17 — hook `mode` metadata ↔ doc cross-check (#688)
    #
    # hooks/manifest.json's per-hook `mode` block is the single source of truth
    # for strict env, bypass env, state-path vars, and read-only external
    # commands. docs/bypass-vars.md and SECURITY.md are human-readable views.
    # This rule ties them together in BOTH directions so neither can drift:
    #
    #   (a) Manifest → doc: every value declared in a hook's `mode` must appear
    #       in the matching doc table (no manifest-only value silently undocumented).
    #   (b) Doc → manifest: every env-var/external-command row in the docs that
    #       maps to a MANIFEST hook must appear in that hook's `mode` (no doc-only
    #       orphan). Opt-in hooks (OPT_IN_HOOKS, no manifest entry) and shared
    #       rows that name no manifest hook are exempt — they have no `mode` block.
    # ------------------------------------------------------------------
    mode_by_name: dict[str, dict] = {}
    for name, entries in manifest_by_name.items():
        mode = _build.hook_mode(entries)
        if mode:
            mode_by_name[name] = mode

    all_hook_names = sorted(manifest_by_name)
    doc_strict = _build.parse_doc_env_table("Strict", all_hook_names)
    doc_bypass = _build.parse_doc_env_table("Opt-out", all_hook_names)
    doc_state = _build.parse_doc_state_vars(all_hook_names)
    doc_external = _build.parse_doc_external_commands()

    # Field → (manifest accessor, doc map, human label, doc source).
    # strict_env is scalar in the manifest; normalize to a list for comparison.
    def _mode_list(mode: dict, key: str) -> list[str]:
        val = mode.get(key)
        if val is None:
            return []
        return [val] if isinstance(val, str) else list(val)

    field_specs = [
        ("strict_env", doc_strict, "strict env", "docs/bypass-vars.md (## Strict)"),
        ("bypass_env", doc_bypass, "bypass env", "docs/bypass-vars.md (## Opt-out)"),
        ("state_paths", doc_state, "state/path var", "docs/bypass-vars.md (## Path / test)"),
        ("external_commands", doc_external, "external command", "SECURITY.md"),
    ]

    # (a) Manifest → doc
    for name in sorted(mode_by_name):
        mode = mode_by_name[name]
        for field, doc_map, label, source in field_specs:
            manifest_vals = set(_mode_list(mode, field))
            doc_vals = set(doc_map.get(name, []))
            for missing in sorted(manifest_vals - doc_vals):
                drifts.append(
                    f"MODE DOC MISSING {name}: {label} {missing!r} declared in "
                    f"manifest `mode` but absent from {source}"
                )

    # (b) Doc → manifest. Opt-in hooks own no manifest `mode` and are exempt.
    # Any other doc-named hook that is absent from the manifest is an ORPHAN —
    # a typo or a stale doc row (e.g. a misspelled `hooks/.../impl.py` path in
    # SECURITY.md, which parse_doc_external_commands derives a name from without
    # pre-filtering). Surfacing it is the whole point of the bidirectional gate;
    # a blanket `continue` would silently swallow exactly the drift it guards.
    for field, doc_map, label, source in field_specs:
        for name, doc_vals in doc_map.items():
            if name in OPT_IN_HOOKS:
                continue  # opt-in hook — no manifest `mode` expected
            if name not in manifest_by_name:
                drifts.append(
                    f"MODE DOC ORPHAN {name}: {label} {sorted(doc_vals)!r} "
                    f"documented in {source} but {name!r} is not a manifest hook "
                    f"or an opt-in hook (typo or stale doc row?)"
                )
                continue
            manifest_vals = set(_mode_list(mode_by_name.get(name, {}), field))
            for orphan in sorted(set(doc_vals) - manifest_vals):
                drifts.append(
                    f"MODE MANIFEST MISSING {name}: {label} {orphan!r} documented "
                    f"in {source} but absent from the hook's manifest `mode` block"
                )

    # ------------------------------------------------------------------
    # Rule 18 — impl.sh hooks must reach the fire ledger (#848)
    #
    # Rule 16 above exempts impl.sh bodies because they have no Python
    # entrypoint for @fail_open to decorate — but that exemption is exactly
    # what left four shell hooks writing zero ledger records while an audit
    # reading the ledger scored the silence as "never fires". A shell hook
    # instead sources `_lib/record_fire.sh` and arms the EXIT trap. The match
    # is the ARM CALL carrying the hook's own manifest name — not a bare
    # `praxis_fire_arm` substring, which the `command -v praxis_fire_arm`
    # availability guard satisfies on its own even after the actual arm call
    # is deleted. A shell hook that legitimately must not record belongs in
    # this rule as an explicit exemption, not as silent absence.
    # ------------------------------------------------------------------
    for name, role in sorted(_build.hook_identities(manifest).items()):
        sh_path = _build.HOOKS_DIR / role / name / "impl.sh"
        if not sh_path.exists():
            continue
        body = sh_path.read_text(encoding="utf-8", errors="replace")
        # The match must look like an executable call, not a mention of one:
        # anchored at the start of its own line, so a comment, an `echo
        # "praxis_fire_arm ..."`, or an assignment holding the same text no
        # longer satisfies the rule (coderabbit + codex, PR #892). All four
        # instrumented hooks put the call at line start, after the `command -v`
        # guard's line continuation.
        #
        # Deliberately not a shell parser: a heredoc body line that itself
        # begins with a bare call still passes. Closing that residue means
        # tracking quoting and heredoc state, which in this codebase has
        # repeatedly traded one corner case for the next. The rule guards
        # against the instrumentation being dropped, not against someone
        # disguising its absence.
        if not re.search(
            rf"(?m)^[ \t]*praxis_fire_arm\s+{re.escape(name)}\b", body
        ):
            drifts.append(
                f"FIRE-LEDGER MISSING hooks/{role}/{name}/impl.sh: shell hook "
                "does not arm fire-ledger instrumentation — source "
                "_lib/record_fire.sh and call praxis_fire_arm after "
                "session_id is parsed (#848)"
            )

    # ------------------------------------------------------------------
    # Rule 19 — test temp-dir creation must be guarded (#897)
    #
    # The suite runs under `set +e`, so an unguarded `mktemp -d` failure
    # leaves its variable empty and every path built from it re-anchors at
    # the filesystem root: `$TMP/cr-repo` becomes `/cr-repo`, and
    # `mkdir -p "$PROBE_ROOT/_lib"` becomes `mkdir -p /_lib`. Observed for
    # real in a codex sandbox that blocks DARWIN_USER_TEMP_DIR — one
    # unusable temp dir surfaced as eleven unrelated assertion failures,
    # which reads as a defect in the code under test.
    # ------------------------------------------------------------------
    # Matches the call shape only — an assignment whose value is a command
    # substitution running `mktemp -d`. Anything else mentioning the text (a
    # comment, or this rule's own fixture passing candidate lines as string
    # arguments) is not an invocation and must not be flagged.
    mktemp_site_re = re.compile(
        r"^[ \t]*(?:local [A-Za-z_][A-Za-z0-9_]*; *)?"
        r"[A-Za-z_][A-Za-z0-9_]*=\"?\$\(.*mktemp -d.*"
    )
    for sh_file in sorted((REPO_ROOT / "tests").rglob("*.sh")):
        for lineno, line in enumerate(
            sh_file.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if not mktemp_site_re.match(line):
                continue
            # The guard has to sit on the same line: `VAR=$(mktemp -d)`
            # returns the substitution's status, and that status is the only
            # signal available before the empty value is used.
            if "||" not in line:
                rel = sh_file.relative_to(REPO_ROOT)
                drifts.append(
                    f"UNGUARDED MKTEMP {rel}:{lineno}: `mktemp -d` failure is "
                    "not checked — append "
                    '`|| { echo "FATAL: mktemp -d failed" >&2; exit 1; }` so a '
                    "missing temp dir fails once instead of re-anchoring every "
                    "derived path at / (#897)"
                )

    # ------------------------------------------------------------------
    # Rule 20 — Spec `Requires:` ↔ manifest `requires` cross-check (#1158)
    #
    # A hook whose matcher can never fire without an external component
    # (cmux, zsh, the openai-codex plugin, a hookable memory store, a
    # slack/notion MCP server) declares that component in an optional
    # per-entry `requires` array, mirrored by a `Requires:` line in the
    # spec header — the same both-directions contract Rule 8 enforces for
    # `Supported hosts:`. Runtime behavior is unaffected (these hooks
    # already fail open); the field exists so dead-matcher cost is
    # declared, not discovered.
    # ------------------------------------------------------------------
    manifest_requires: dict[str, set[str]] = {}
    for entry in manifest["hooks"]:
        if entry.get("requires"):
            manifest_requires.setdefault(entry["name"], set()).update(
                entry["requires"]
            )

    for hook_dir in _hook_dirs():
        spec_file = hook_dir / "spec.md"
        if not spec_file.exists():
            continue
        hook_name = hook_dir.name
        requires_value: str | None = None
        for line in spec_file.read_text().splitlines()[:10]:
            if line.strip().lower().startswith("requires:"):
                requires_value = line.split(":", 1)[1].strip()
                break
        spec_set = (
            {
                t.split("(")[0].strip()
                for t in requires_value.split(",")
                if t.split("(")[0].strip()
            }
            if requires_value is not None
            else set()
        )
        json_set = manifest_requires.get(hook_name, set())
        if spec_set != json_set:
            drifts.append(
                f"FAIL requires mismatch {hook_name}: spec={sorted(spec_set)!r} "
                f"manifest={sorted(json_set)!r} (declare the dependency in both "
                "hooks/manifest.json `requires` and the spec's `Requires:` "
                "header line, or in neither — #1158)"
            )

    # ------------------------------------------------------------------
    # Rule 21 — hook operating matrix byte-identity (#672)
    #
    # The matrix is intentionally generated from structured sources only:
    # manifest registration shape, bypass-vars registry, and SECURITY.md
    # external-command declarations. This keeps Track 1 behavior-preserving
    # while still giving users a drift-checked operating surface.
    # (Renumbered from a duplicate "Rule 16" in #1172 — that label collided
    # with the @fail_open rule above, so this one took the next free number.)
    # ------------------------------------------------------------------
    expected_matrix = _build.render_hook_operating_matrix(manifest)
    matrix_path = _build.HOOK_OPERATING_MATRIX_PATH
    if not matrix_path.exists():
        drifts.append(
            "MATRIX MISSING docs/hook-operating-matrix.md: run "
            "./scripts/build-plugin-manifests.py"
        )
    elif matrix_path.read_text() != expected_matrix:
        drifts.append(
            "MATRIX DRIFT docs/hook-operating-matrix.md: regenerate with "
            "./scripts/build-plugin-manifests.py"
        )

    # ------------------------------------------------------------------
    # Rule 22 — spec referenced-path existence (#1179)
    #
    # A spec.md that cites `hooks/<name>.py`, `hooks/test-<name>.sh`, or
    # `tests/test_<name>.sh` after the layout moved to
    # hooks/<role>/<name>/impl.py + tests/hooks/<role>/test_<name>.sh sends
    # readers (and agents running the cited test command) to a dead path.
    # Every repo-path-looking token extracted by _spec_referenced_paths()
    # must exist on disk, except the deliberate phantom examples listed in
    # SPEC_PATH_EXEMPT. The heuristic is documented on the helper and the
    # constants above.
    # ------------------------------------------------------------------
    for hook_dir in _hook_dirs():
        spec_file = hook_dir / "spec.md"
        if not spec_file.exists():
            continue
        spec_rel = str(spec_file.relative_to(REPO_ROOT))
        exempt = SPEC_PATH_EXEMPT.get(spec_rel, set())
        spec_text = spec_file.read_text(encoding="utf-8", errors="replace")
        for tok in sorted(_spec_referenced_paths(spec_text)):
            if tok in exempt:
                continue
            if not (REPO_ROOT / tok).exists():
                drifts.append(
                    f"SPEC DANGLING PATH {spec_rel}: `{tok}` does not exist on "
                    "disk — update the reference to the real path, or add it to "
                    "SPEC_PATH_EXEMPT if it is a deliberate phantom example "
                    "(#1179)"
                )

    # ------------------------------------------------------------------
    # Rule 23 — README.md hook-aggregate counts (#1176)
    #
    # README.md's Hooks section carries hand-written aggregate numbers: the
    # total hook count, the number of manifest registration points, the
    # per-role counts in the role table, and how many hooks declare a
    # variable in docs/bypass-vars.md. Each is derived here from the same
    # sources the prose describes (hook dirs on disk, hooks/manifest.json,
    # the bypass-vars registry) and asserted at an anchored phrase — regex
    # on the surrounding fixed text, never a line number, so prose reflow
    # does not break the gate but a stale number does.
    # ------------------------------------------------------------------
    role_dir_counts: dict[str, int] = {role: 0 for role in VALID_ROLES}
    for hook_dir in _hook_dirs():
        role_dir_counts[hook_dir.parent.name] += 1
    total_hook_dirs = sum(role_dir_counts.values())
    manifest_entry_count = len(manifest["hooks"])

    # Distinct hooks declaring a variable in docs/bypass-vars.md: walk every
    # `## <section>` table, take the hook column (`Hook(s)` is the third cell
    # in the Path / test table, the second everywhere else — mirroring
    # parse_doc_env_table / parse_doc_state_vars in build-plugin-manifests.py),
    # and keep backtick-delimited tokens that name a real hook dir. Prose
    # mentions outside backticks (e.g. shared-row parentheticals) are not a
    # declaration and do not count.
    hook_dir_names = {d.name for d in _hook_dirs()}
    bypass_vars_text = (REPO_ROOT / "docs" / "bypass-vars.md").read_text()
    bypass_var_hooks: set[str] = set()
    strict_hooks: set[str] = set()
    current_section: str | None = None
    for line in bypass_vars_text.splitlines():
        header = re.match(r"^##\s+(.+)$", line)
        if header:
            current_section = header.group(1).strip()
            continue
        if current_section is None or not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        hook_col = 2 if current_section.startswith("Path / test") else 1
        if len(cells) <= hook_col:
            continue
        named = [
            token
            for token in re.findall(r"`([^`]+)`", cells[hook_col])
            if token in hook_dir_names
        ]
        bypass_var_hooks.update(named)
        if current_section.startswith("Strict"):
            strict_hooks.update(named)

    # The role table's `advisory-nudge` row says how many of those hooks a
    # `PRAXIS_*_STRICT` variable can promote into blocking. Derived from the
    # same registry as the opt-out count above so the two cannot disagree,
    # and restricted to advisory-nudge because that is the row it sits in —
    # a strict var on a gate role does not promote anything.
    strict_advisory_count = sum(
        1
        for name in strict_hooks
        if (_build.HOOKS_DIR / "advisory-nudge" / name).is_dir()
    )

    readme_text = (REPO_ROOT / "README.md").read_text()
    readme_count_specs: list[tuple[str, str, tuple[int, ...]]] = [
        # `\s+` between words: a prose reflow may move a line break inside the
        # phrase, and the anchor must survive that (the numbers, not the
        # wrapping, are what this rule pins down).
        (
            "total hooks / registration points",
            r"\*\*(\d+) hooks\*\*,\s+registered\s+at\s+(\d+)\s+points",
            (total_hook_dirs, manifest_entry_count),
        ),
        (
            "opt-out/tuning variable coverage",
            r"(\d+)\s+of\s+the\s+(\d+)\s+hooks\s+declare\s+an\s+opt-out\s+"
            r"or\s+tuning\s+variable",
            (len(bypass_var_hooks), total_hook_dirs),
        ),
        (
            "advisory strict-variable coverage",
            r"(\d+)\s+read\s+a\s+`PRAXIS_\*_STRICT`\s+variable",
            (strict_advisory_count,),
        ),
    ]
    for role in sorted(VALID_ROLES):
        readme_count_specs.append(
            (
                f"role table row `{role}`",
                rf"^\|\s*`{re.escape(role)}`\s*\|\s*(\d+)\s*\|",
                (role_dir_counts[role],),
            )
        )
    for label, pattern, expected_counts in readme_count_specs:
        match = re.search(pattern, readme_text, re.MULTILINE)
        if match is None:
            drifts.append(
                f"README COUNT ANCHOR MISSING ({label}): no match for "
                f"{pattern!r} in README.md — restore the anchored phrase or "
                "update this rule alongside the prose (#1176)"
            )
            continue
        found = tuple(int(g) for g in match.groups())
        if found != expected_counts:
            drifts.append(
                f"README COUNT DRIFT ({label}): README.md says {found}, "
                f"derived {expected_counts} — update the number(s) at "
                f"{match.group(0)!r} (#1176)"
            )

    # Rule 24 — canonical matcher token order (#1168)
    #
    # Dispatch (and hooks.json grouping) key on the LITERAL matcher string, so
    # the same tool set spelled two ways (`Edit|Write` vs `Write|Edit`) can
    # never coalesce into one group — each spelling cold-starts its own
    # process chain. Canonical spelling: the pipe-joined tokens sorted
    # lexicographically, no duplicates. Only plain tool-name matchers are
    # normalized: a matcher with any token containing characters outside
    # [A-Za-z0-9] (e.g. `mcp__.*`, `mcp__.*slack.*`) is regex-y and exempt —
    # reordering regex alternations is not guaranteed meaning-preserving.
    # Applies to hook entries AND dispatch_groups so a group declaration can
    # never drift from the spelling its members use.
    # ------------------------------------------------------------------
    _PLAIN_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")

    def _canonical_matcher_drift(matcher: str | None, where: str) -> str | None:
        if not matcher or "|" not in matcher:
            return None
        tokens = matcher.split("|")
        # An EMPTY token is never a legitimate regex-y spelling: as a hook
        # matcher regex, an empty alternation (`Edit|`, `Edit||Write`) matches
        # EVERY tool name, silently widening the hook to all tools. Report it
        # as drift instead of exempting it (PR #1198 review).
        if any(t == "" for t in tokens):
            return (
                f"MATCHER ORDER {where}: {matcher!r} contains an empty "
                "alternation token, which matches EVERY tool — remove the "
                "stray '|' (#1168)"
            )
        if not all(_PLAIN_TOKEN_RE.fullmatch(t) for t in tokens):
            return None  # regex-y matcher — exempt
        canonical = "|".join(sorted(set(tokens)))
        if matcher != canonical:
            return (
                f"MATCHER ORDER {where}: {matcher!r} is not in canonical "
                f"(sorted, deduplicated) order — spell it {canonical!r} (#1168)"
            )
        return None

    for entry in manifest["hooks"]:
        entries = entry.get("entries") or [
            {"event": entry.get("event"), "matcher": entry.get("matcher")}
        ]
        for e in entries:
            drift = _canonical_matcher_drift(
                e.get("matcher"), f"hook {entry['name']} ({e.get('event')})"
            )
            if drift:
                drifts.append(drift)
    for group in manifest.get("dispatch_groups", []):
        drift = _canonical_matcher_drift(
            group.get("matcher"), f"dispatch_groups ({group.get('event')})"
        )
        if drift:
            drifts.append(drift)

    # ------------------------------------------------------------------
    # Rule 25 — generated dispatcher commands must be shell-safe (#1198)
    #
    # The host executes every hooks.json `command` string via `sh -c`. An
    # UNQUOTED shell control operator in an interpolated value turns the
    # command into something else entirely: the first pipe-carrying dispatch
    # matcher (`... _dispatch.sh PreToolUse Edit|NotebookEdit|Write claude`)
    # was parsed as a 3-command PIPELINE — the dispatcher ran with matcher
    # 'Edit' (the wrong group) and its deny JSON was swallowed by the pipe,
    # so all nine grouped hooks never fired. Rule 14 and the pytest parity
    # suite both invoke the dispatcher directly and bypassed the shell, which
    # is why neither caught it. This rule simulates the shell's token split
    # (shlex with punctuation_chars — quoted operators stay inside their
    # token) and flags a bare control-operator token.
    #
    # Scope: ONLY commands that invoke the dispatch wrapper. Those are the
    # commands whose args the build interpolates from manifest data (matcher /
    # host), which is the injection surface this rule guards. A per-hook
    # wrapper command with a deliberate compound form (`... 2>&1`, `a && b`)
    # is legitimate shell and must not fail CI with a quote-the-matcher
    # message (round-2 review).
    # ------------------------------------------------------------------
    _SH_CONTROL_CHARS = set("|&;()<>")

    def _sh_control_tokens(cmd: str) -> list[str]:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        try:
            tokens = list(lex)
        except ValueError:
            return ["<unparseable: unbalanced quoting>"]
        return [t for t in tokens if t and set(t) <= _SH_CONTROL_CHARS]

    for host_id, hooks_path in hooks_outputs:
        if not hooks_path.exists():
            continue  # the drift/Rule 14 checks already report the missing file
        hooks_json = json.loads(hooks_path.read_text())
        for event_name, event_groups in hooks_json.get("hooks", {}).items():
            for group in event_groups:
                for node in group.get("hooks", []):
                    cmd = node.get("command", "")
                    if _build.DISPATCH_WRAPPER_NAME not in cmd:
                        continue  # non-dispatcher command — out of scope
                    bad = _sh_control_tokens(cmd)
                    if bad:
                        rel = hooks_path.relative_to(REPO_ROOT)
                        drifts.append(
                            f"UNQUOTED SHELL OPERATOR {rel} [{event_name}/"
                            f"{group.get('matcher')}]: dispatcher command "
                            f"{cmd!r} parses under `sh -c` with bare operator "
                            f"token(s) {bad!r} — the interpolated matcher/host "
                            "must be shlex-quoted (#1198)"
                        )

    # ------------------------------------------------------------------
    # Rule 26 — sunset review: review_by present, well-formed, not overdue
    # (#1300)
    #
    # docs/hook-prune-audit.md found zero drops on a 30-day ledger and says
    # it cannot rank hooks by value, so left alone the roster only grows. A
    # per-hook review_by date is the structural counterweight: CI fails once
    # the date passes, and the only ways out are a re-audit that bumps the
    # date or a verdict recorded in the audit. The logic lives in
    # review_by_drifts() so tests can drive it with a pinned `today`.
    # ------------------------------------------------------------------
    drifts.extend(review_by_drifts(manifest, date.today()))

    # ------------------------------------------------------------------
    # Rule 27 — README hook-dependency table ↔ manifest `requires` (#1332)
    #
    # docs/hook-suitability-audit.md §B: the README's tier table covers
    # skills, so a user without oh-my-claudecode or the codex plugin has
    # nowhere to read which hooks are inert. The `requires` field (Rule 20)
    # is the declaration; README.md's `### Hook dependencies` table is the
    # reader's view of it. Tie them in both directions, the way Rule 17 ties
    # `mode` to docs/bypass-vars.md: every declared component has one row
    # (MISSING), every row names a declared component (ORPHAN), each row's
    # hook cell equals the declaring set (DRIFT), and the install cell is
    # non-empty (NO INSTALL) — an install column with a blank cell says
    # nothing to the reader the table exists for.
    # ------------------------------------------------------------------
    readme_lines = (REPO_ROOT / "README.md").read_text().splitlines()
    doc_deps: dict[str, tuple[set[str], str]] = {}
    in_section = False
    for line in readme_lines:
        if line.startswith("#"):
            in_section = line.strip() == "### Hook dependencies"
            continue
        if not in_section:
            continue
        cells = _table_cells(line)
        if len(cells) < 3 or not cells[0].startswith("`"):
            continue
        component_tokens = re.findall(r"`([^`]+)`", cells[0])
        if not component_tokens:
            continue
        component = component_tokens[0]
        if component in doc_deps:
            drifts.append(
                f"HOOK DEPS DUPLICATE README.md: {component!r} has two rows "
                "in the Hook dependencies table (#1332)"
            )
            continue
        doc_deps[component] = (set(re.findall(r"`([^`]+)`", cells[1])), cells[2])
    if not doc_deps:
        drifts.append(
            "HOOK DEPS MISSING README.md: no `### Hook dependencies` table "
            "rows found — it is the reader's view of the manifest `requires` "
            "field (#1332)"
        )
    manifest_deps: dict[str, set[str]] = {}
    for name, comps in manifest_requires.items():
        for comp in comps:
            manifest_deps.setdefault(comp, set()).add(name)
    for comp in sorted(set(manifest_deps) - set(doc_deps)):
        drifts.append(
            f"HOOK DEPS MISSING README.md: {comp!r} is declared in `requires` "
            f"by {sorted(manifest_deps[comp])!r} but has no row in the Hook "
            "dependencies table (#1332)"
        )
    for comp in sorted(set(doc_deps) - set(manifest_deps)):
        drifts.append(
            f"HOOK DEPS ORPHAN README.md: {comp!r} has a Hook dependencies "
            "row but no manifest hook declares it in `requires` (#1332)"
        )
    for comp in sorted(set(doc_deps) & set(manifest_deps)):
        doc_hooks, install = doc_deps[comp]
        if doc_hooks != manifest_deps[comp]:
            drifts.append(
                f"HOOK DEPS DRIFT README.md: {comp!r} row lists "
                f"{sorted(doc_hooks)!r} but the manifest declares it for "
                f"{sorted(manifest_deps[comp])!r} (#1332)"
            )
        if not install.strip():
            drifts.append(
                f"HOOK DEPS NO INSTALL README.md: {comp!r} row has an empty "
                "install cell (#1332)"
            )

    if drifts:
        print("plugin-manifest check FAILED:")
        for d in drifts:
            print(f"  - {d}")
        return 1
    print("plugin-manifest check OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
