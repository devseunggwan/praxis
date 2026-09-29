"""docs/rule-noop-audit.md stage 1 stays equal to what the hook bodies emit (#1534).

The audit's `Reach` column is a claim about each hook's output channels, and
the channels are a property of the body, which changes without the audit being
touched. The table is therefore re-derived here from the manifest and
`hook_channels.channels_for_hook`, the function the operating matrix uses, and
compared row for row.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from hook_channels import channels_for_hook, render_channels  # noqa: E402

AUDIT = REPO_ROOT / "docs" / "rule-noop-audit.md"
BEGIN, END = "<!-- rule-noop-audit:begin -->", "<!-- rule-noop-audit:end -->"
STAGE2_RE = re.compile(r"-|measured: (no-op|keep|investigate)|unmeasured: (command|write|event|strict|language)")


def _audit_rows() -> dict[str, list[str]]:
    text = AUDIT.read_text()
    assert text.count(BEGIN) == 1 and text.count(END) == 1
    table = text.split(BEGIN)[1].split(END)[0]
    rows = {}
    for line in table.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows[cells[0].strip("`")] = cells[1:]
    return rows


def _advisory_entries() -> dict[str, list[dict]]:
    by_name: dict[str, list[dict]] = {}
    for entry in json.loads((REPO_ROOT / "hooks" / "manifest.json").read_text())["hooks"]:
        if entry["role"] == "advisory-nudge":
            by_name.setdefault(entry["name"], []).append(entry)
    return by_name


def test_the_table_lists_every_advisory_hook_once() -> None:
    assert sorted(_audit_rows()) == sorted(_advisory_entries())


def test_channels_strict_env_and_reach_match_the_bodies() -> None:
    rows = _audit_rows()
    for name, entries in _advisory_entries().items():
        channels = channels_for_hook(REPO_ROOT, entries)
        strict = next((e["mode"]["strict_env"] for e in entries
                       if e.get("mode", {}).get("strict_env")), None)
        reach = "unreachable" if channels == ["stderr"] else "reachable"
        assert rows[name][:3] == [render_channels(channels),
                                  f"`{strict}`" if strict else "-", reach], name


def test_stage2_is_empty_only_for_unreachable_hooks() -> None:
    for name, (_, _, reach, stage2) in _audit_rows().items():
        assert STAGE2_RE.fullmatch(stage2), (name, stage2)
        assert (stage2 == "-") == (reach == "unreachable"), name
