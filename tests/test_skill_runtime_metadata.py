"""Rule 11: runtime-sensitive SKILL.md frontmatter metadata gate.

Locks the static signals used by ``check-plugin-manifests.py`` so the merge-time
gate stays conservative but non-trivial:

  - operative ``AskUserQuestion(...)`` call prose triggers the metadata requirement;
  - operative ``Skill(...)`` delegation prose triggers it too;
  - known external CLI wrapper skills trigger even without a helper executable;
  - executable helper files beside ``SKILL.md`` trigger it for wrapper-backed
    skills even when the prose itself stays terse;
  - missing / placeholder metadata fields are reported as drift;
  - prose-only docs skills without those signals stay out of scope.
"""
from __future__ import annotations

import importlib.util
import stat
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "check_plugin_manifests", REPO_ROOT / "scripts" / "check-plugin-manifests.py"
)
assert _spec and _spec.loader
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def _write_skill(
    skill_dir: Path,
    *,
    body: str,
    verified: bool | None = None,
    verified_at: str | None = None,
    verified_note: str | None = None,
    quote_verified_note: bool = True,
    helper_name: str | None = None,
    helper_executable: bool = True,
) -> Path:
    skill_dir.mkdir()
    lines = [
        "---",
        "name: fixture-skill",
        'description: "fixture"',
    ]
    if verified is not None:
        lines.append(f"verified-against-runtime: {'true' if verified else 'false'}")
    if verified_at is not None:
        lines.append(f"runtime-verified-at: {verified_at}")
    if verified_note is not None:
        if quote_verified_note:
            lines.append(f'runtime-verified-note: "{verified_note}"')
        else:
            lines.append(f"runtime-verified-note: {verified_note}")
    lines.extend(["---", "", body])
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if helper_name is not None:
        helper = skill_dir / helper_name
        helper.write_text("#!/bin/sh\necho helper\n", encoding="utf-8")
        mode = helper.stat().st_mode
        if helper_executable:
            helper.chmod(mode | stat.S_IXUSR)
        else:
            helper.chmod(mode & ~stat.S_IXUSR)
    return skill_path


def test_docs_only_skill_without_runtime_markers_is_out_of_scope(tmp_path):
    skill_dir = tmp_path / "docs-only"
    _write_skill(
        skill_dir,
        body="# Guide\n\nThis skill explains conventions only.\n",
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == []
    assert check._skill_runtime_metadata_drifts(skill_dir) == []


def test_ask_user_question_requires_runtime_metadata(tmp_path):
    skill_dir = tmp_path / "ask-skill"
    _write_skill(
        skill_dir,
        body=(
            "# Skill\n\n"
            "Ask the user via `AskUserQuestion(...)` before continuing.\n"
        ),
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == ["AskUserQuestion"]
    drifts = check._skill_runtime_metadata_drifts(skill_dir)
    assert any("verified-against-runtime: true" in d for d in drifts), drifts
    assert any("runtime-verified-at" in d for d in drifts), drifts
    assert any("runtime-verified-note" in d for d in drifts), drifts


def test_ask_user_question_mention_only_is_out_of_scope(tmp_path):
    skill_dir = tmp_path / "mention-only"
    _write_skill(
        skill_dir,
        body=(
            "# Skill\n\n"
            "This guide explains why AskUserQuestion is different from prose.\n"
        ),
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == []


def test_negated_ask_user_question_mentions_are_out_of_scope(tmp_path):
    skill_dir = tmp_path / "negated-ask"
    _write_skill(
        skill_dir,
        body=(
            "# Skill\n\n"
            "Do not call `AskUserQuestion(...)` for this docs-only flow.\n"
            "Never use AskUserQuestion when the user already approved.\n"
        ),
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == []


def test_skill_delegation_requires_runtime_metadata(tmp_path):
    skill_dir = tmp_path / "delegate-skill"
    _write_skill(
        skill_dir,
        body='# Skill\n\nRun `Skill("praxis:other-skill")` next.\n',
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == ["Skill(...)"]


def test_skill_keyword_delegation_requires_runtime_metadata(tmp_path):
    skill_dir = tmp_path / "delegate-keyword-skill"
    _write_skill(
        skill_dir,
        body='# Skill\n\nRun `Skill(skill="praxis:other-skill")` next.\n',
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == ["Skill(...)"]


def test_skill_mention_only_is_out_of_scope(tmp_path):
    skill_dir = tmp_path / "skill-mention-only"
    _write_skill(
        skill_dir,
        body="# Skill\n\nThis guide compares Skill delegation with slash commands.\n",
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == []


def test_executable_helper_requires_runtime_metadata(tmp_path):
    skill_dir = tmp_path / "helper-skill"
    _write_skill(
        skill_dir,
        body="# Skill\n\nThin wrapper around the sibling helper.\n",
        helper_name="helper-script",
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == [
        "helper-executable"
    ]


def test_non_executable_helper_does_not_trigger_by_itself(tmp_path):
    skill_dir = tmp_path / "nonexec-helper"
    _write_skill(
        skill_dir,
        body="# Skill\n\nStatic docs only.\n",
        helper_name="helper-script",
        helper_executable=False,
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == []


def test_external_cli_wrapper_template_requires_runtime_metadata(tmp_path):
    skill_dir = tmp_path / "kubectl-wrapper"
    _write_skill(
        skill_dir,
        body=(
            "# Skill\n\n"
            "Run the live command template:\n\n"
            "```bash\n"
            "kubectl get pods\n"
            "```\n"
        ),
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == [
        "external-cli-wrapper"
    ]


def test_external_cli_wrapper_template_detects_unlisted_binary(tmp_path):
    skill_dir = tmp_path / "aws-wrapper"
    _write_skill(
        skill_dir,
        body=(
            "# Skill\n\n"
            "Run the live command template:\n\n"
            "```bash\n"
            "aws sts get-caller-identity\n"
            "```\n"
        ),
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == [
        "external-cli-wrapper"
    ]


def test_external_cli_wrapper_template_detects_shell_prefixes(tmp_path):
    skill_dir = tmp_path / "prefixed-wrapper"
    _write_skill(
        skill_dir,
        body=(
            "# Skill\n\n"
            "Run prefixed command templates:\n\n"
            "```bash\n"
            "command gh pr list\n"
            "exec cmux list-workspaces\n"
            "if aws sts get-caller-identity; then\n"
            "  echo ok\n"
            "fi\n"
            "```\n"
        ),
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == [
        "external-cli-wrapper"
    ]


def test_external_cli_wrapper_template_detects_env_assignments(tmp_path):
    skill_dir = tmp_path / "env-prefixed-wrapper"
    _write_skill(
        skill_dir,
        body=(
            "# Skill\n\n"
            "Run env-prefixed command templates:\n\n"
            "```bash\n"
            "AWS_PROFILE=prod aws sts get-caller-identity\n"
            "CLAUDE_CONFIG_DIR=~/.claude claude --version\n"
            "```\n"
        ),
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == [
        "external-cli-wrapper"
    ]


def test_external_cli_wrapper_template_detects_path_commands(tmp_path):
    skill_dir = tmp_path / "path-wrapper"
    _write_skill(
        skill_dir,
        body=(
            "# Skill\n\n"
            "Run path-qualified command templates:\n\n"
            "```bash\n"
            "/usr/local/bin/cmux list-workspaces\n"
            "./gh pr list\n"
            "```\n"
        ),
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == [
        "external-cli-wrapper"
    ]


def test_external_cli_wrapper_template_detects_prompt_prefix(tmp_path):
    skill_dir = tmp_path / "prompt-wrapper"
    _write_skill(
        skill_dir,
        body=(
            "# Skill\n\n"
            "Run prompt-prefixed command templates:\n\n"
            "```bash\n"
            "$ gh pr list\n"
            "# kubectl get pods\n"
            "```\n"
        ),
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == [
        "external-cli-wrapper"
    ]


def test_external_cli_wrapper_template_detects_root_prompt_only(tmp_path):
    skill_dir = tmp_path / "root-prompt-wrapper"
    _write_skill(
        skill_dir,
        body=(
            "# Skill\n\n"
            "Run root-prompt command templates:\n\n"
            "```bash\n"
            "# kubectl get pods\n"
            "```\n"
        ),
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == [
        "external-cli-wrapper"
    ]


def test_external_cli_wrapper_template_detects_custom_root_prompt(tmp_path):
    skill_dir = tmp_path / "custom-root-prompt-wrapper"
    _write_skill(
        skill_dir,
        body=(
            "# Skill\n\n"
            "Run root-prompt command templates:\n\n"
            "```bash\n"
            "# mycli --help\n"
            "```\n"
        ),
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == [
        "external-cli-wrapper"
    ]


def test_external_cli_wrapper_template_ignores_shell_comments(tmp_path):
    skill_dir = tmp_path / "comment-only-shell"
    _write_skill(
        skill_dir,
        body=(
            "# Skill\n\n"
            "Document a commented shell snippet:\n\n"
            "```bash\n"
            "# install dependencies before continuing\n"
            "echo done\n"
            "```\n"
        ),
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == []


def test_inline_external_cli_requires_runtime_metadata(tmp_path):
    skill_dir = tmp_path / "inline-cli-wrapper"
    _write_skill(
        skill_dir,
        body="# Skill\n\nRun `gh issue create` after preparing the issue body.\n",
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == [
        "external-cli-wrapper"
    ]


def test_inline_external_cli_detects_command_context(tmp_path):
    skill_dir = tmp_path / "inline-cli-command-wrapper"
    _write_skill(
        skill_dir,
        body=(
            "# Skill\n\n"
            "Run the command `gh issue create` after preparing the issue body.\n"
            "Execute this command: `kubectl get pods` before reporting status.\n"
        ),
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == [
        "external-cli-wrapper"
    ]


def test_inline_external_cli_detects_unknown_command(tmp_path):
    skill_dir = tmp_path / "inline-cli-unknown-wrapper"
    _write_skill(
        skill_dir,
        body="# Skill\n\nRun `mycli subcommand` against the live environment.\n",
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == [
        "external-cli-wrapper"
    ]


def test_inline_external_cli_description_is_out_of_scope(tmp_path):
    skill_dir = tmp_path / "inline-cli-description"
    _write_skill(
        skill_dir,
        body=(
            "# Skill\n\n"
            "| Skill | Purpose |\n"
            "| --- | --- |\n"
            "| browser | Browser automation via `cmux browser` CLI |\n"
        ),
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == []


def test_external_cli_wrapper_backstop_requires_runtime_metadata(tmp_path):
    skill_dir = tmp_path / "cmux-delegate"
    _write_skill(
        skill_dir,
        body=(
            "# Skill\n\n"
            "Creates a cmux workspace and sends provider command templates.\n"
        ),
    )
    assert check._skill_runtime_verification_reasons(skill_dir) == [
        "external-cli-wrapper"
    ]
    drifts = check._skill_runtime_metadata_drifts(skill_dir)
    assert any("external-cli-wrapper" in drift for drift in drifts), drifts


def test_placeholder_metadata_is_rejected(tmp_path):
    skill_dir = tmp_path / "placeholder-skill"
    _write_skill(
        skill_dir,
        body="Ask the user via `AskUserQuestion(...)`.\n",
        verified=True,
        verified_at="YYYY-MM-DD",
        verified_note="<cli-name> <version> — one-line observed behavior",
    )
    drifts = check._skill_runtime_metadata_drifts(skill_dir)
    assert any("template placeholder" in d for d in drifts), drifts


def test_unquoted_runtime_note_placeholder_is_rejected(tmp_path):
    skill_dir = tmp_path / "unquoted-placeholder-skill"
    _write_skill(
        skill_dir,
        body="Ask the user via `AskUserQuestion(...)`.\n",
        verified=True,
        verified_at="2026-06-17",
        verified_note="<cli-name> <version> — one-line observed behavior",
        quote_verified_note=False,
    )
    drifts = check._skill_runtime_metadata_drifts(skill_dir)
    assert any("template placeholder" in d for d in drifts), drifts


def test_valid_metadata_clears_runtime_drift(tmp_path):
    skill_dir = tmp_path / "valid-skill"
    _write_skill(
        skill_dir,
        body="Ask the user via `AskUserQuestion(...)`.\n",
        verified=True,
        verified_at="2026-06-16",
        verified_note="cmux 1.x — prompt rendered with 3 options and cancel",
    )
    assert check._skill_runtime_metadata_drifts(skill_dir) == []


def test_quoted_runtime_metadata_scalars_are_accepted(tmp_path):
    skill_dir = tmp_path / "quoted-metadata-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: fixture-skill",
                'description: "fixture"',
                'verified-against-runtime: "true"',
                'runtime-verified-at: "2026-06-17"',
                'runtime-verified-note: "cmux 1.x — verified"',
                "---",
                "",
                "Ask the user via `AskUserQuestion(...)`.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    assert check._skill_runtime_metadata_drifts(skill_dir) == []


def test_runtime_note_block_scalar_is_rejected(tmp_path):
    skill_dir = tmp_path / "block-note-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: fixture-skill",
                'description: "fixture"',
                "verified-against-runtime: true",
                "runtime-verified-at: 2026-06-17",
                "runtime-verified-note: >",
                "  cmux 1.x — verified",
                "---",
                "",
                "Ask the user via `AskUserQuestion(...)`.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    drifts = check._skill_runtime_metadata_drifts(skill_dir)
    assert any("block scalar" in drift for drift in drifts), drifts


def test_runtime_note_chomping_block_scalar_is_rejected(tmp_path):
    skill_dir = tmp_path / "chomping-block-note-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: fixture-skill",
                'description: "fixture"',
                "verified-against-runtime: true",
                "runtime-verified-at: 2026-06-17",
                "runtime-verified-note: >-",
                "  cmux 1.x — verified",
                "---",
                "",
                "Ask the user via `AskUserQuestion(...)`.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    drifts = check._skill_runtime_metadata_drifts(skill_dir)
    assert any("block scalar" in drift for drift in drifts), drifts


def test_multiple_runtime_signals_are_all_reported(tmp_path):
    skill_dir = tmp_path / "multi-signal"
    _write_skill(
        skill_dir,
        body=(
            'Run `Skill("praxis:other")` and then ask via `AskUserQuestion(...)`.\n'
        ),
        helper_name="runtime-helper",
    )
    reasons = check._skill_runtime_verification_reasons(skill_dir)
    assert reasons == ["AskUserQuestion", "Skill(...)", "helper-executable"]


def test_current_repo_runtime_sensitive_skill_set_is_stable():
    actual = {
        skill_dir.name: tuple(check._skill_runtime_verification_reasons(skill_dir))
        for skill_dir in check._skill_dirs()
        if check._skill_runtime_verification_reasons(skill_dir)
    }
    assert actual == {
        "cmux-browser": ("external-cli-wrapper", "helper-executable"),
        "cmux-delegate": ("external-cli-wrapper",),
        "cmux-recover-sessions": (
            "AskUserQuestion",
            "external-cli-wrapper",
            "helper-executable",
        ),
        "cmux-resume-sessions": (
            "AskUserQuestion",
            "external-cli-wrapper",
            "helper-executable",
        ),
        "cmux-save-sessions": (
            "AskUserQuestion",
            "external-cli-wrapper",
            "helper-executable",
        ),
        "cmux-session-manager": (
            "AskUserQuestion",
            "external-cli-wrapper",
            "helper-executable",
        ),
        "codex-review-wrap": (
            "AskUserQuestion",
            "Skill(...)",
            "external-cli-wrapper",
        ),
        "recover-sessions": (
            "AskUserQuestion",
            "external-cli-wrapper",
            "helper-executable",
        ),
        "retrospect": ("external-cli-wrapper",),
        "writing-praxis-skill": (
            "AskUserQuestion",
            "Skill(...)",
            "external-cli-wrapper",
        ),
    }
