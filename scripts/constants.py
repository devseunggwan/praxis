"""Single source of truth for plugin-manifest constants.

Both `build-plugin-manifests.py` (wrapper emission) and
`check-plugin-manifests.py` (CI gate) import from this module so the same
declarations cannot drift across the build/check boundary.

Add entries here — never re-declare these constants in the build or check
scripts.
"""
from __future__ import annotations


# Valid hook role names — must match the four ADR-0001 categories.
# Used by check-plugin-manifests.py to filter `hooks/<role>/` directory
# enumeration.
VALID_ROLES: frozenset[str] = frozenset({
    "preflight-gate",
    "advisory-nudge",
    "postuse-correction",
    "completion-verify",
})


# Opt-in hooks: live on disk under hooks/<role>/<name>/ but are NOT
# registered in manifest.json (user must add a hooks entry to their own
# settings.json or hooks.json fragment to activate). The build still
# emits a wrapper at hooks/<name>.sh so the documented opt-in command
# `${CLAUDE_PLUGIN_ROOT}/hooks/<name>.sh` resolves — without this,
# hooks/<role>/<name>/spec.md guidance would break on first user attempt.
#
# Dict form (name → role) is canonical — build needs the role for wrapper
# emission. check uses the keys only (treat as a set via `set(OPT_IN_HOOKS)`
# or `name in OPT_IN_HOOKS`).
OPT_IN_HOOKS: dict[str, str] = {
    "external-write-falsify-check": "advisory-nudge",
}


# docs/hook/*.md files that are real, hand-written documentation — NOT the
# 1-line "Moved to …/spec.md" redirect stubs that build emits per hook dir
# (ADR-0001 §337-338, #606). The stub-parity rule (check Rule 14) skips these
# in its reverse direction so a genuine shared-format doc is not flagged as an
# orphan stub. `INDEX.md` is excluded structurally (it is the index itself).
NON_HOOK_DOCS: frozenset[str] = frozenset({
    "block-message-format",
    "RULE-BACKSTOP-GAPS",
})


# Frozen skill surface (issue #465). Adding or removing a skill directory
# under skills/ requires updating this set in the same commit. Prevents
# silent skill proliferation; every intentional surface change is paired
# with an explicit declaration here.
EXPECTED_SKILLS: frozenset[str] = frozenset({
    "audit",
    "cmux-delegate",
    "cmux-recover-sessions",
    "cmux-resume-sessions",
    "cmux-save-sessions",
    "cmux-session-manager",
    "codex-review-wrap",
    "critique",
    "debt",
    "interview",
    "recover-sessions",
    "reset-strikes",
    "retrospect",
    "strike",
    "strikes",
    "surface-enumeration",
    "trace",
    "using-praxis",
    "worktree-merge-cleanup",
    "writing-praxis-skill",
})
