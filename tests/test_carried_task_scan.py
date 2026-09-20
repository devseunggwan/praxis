"""Coverage for skills/retrospect/carried-task-scan.py (issue #1427).

The fixtures carry the real cursor shapes: `note` as a newline-joined string
(praxis), a cursor with neither note field (another local project), and the
verbatim cycle-82 line whose thirteen unconsumed cycles are what the issue is
about.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "retrospect" / "carried-task-scan.py"

# The line as cycle 82 (2026-08-05) actually wrote it, quoted from the cursor
# note in the originating session's transcript.
CYCLE_82_LINE = "- hookable:true 인 다른 메모리 전수 점검이 다음 패스 과제"


def _load():
    spec = importlib.util.spec_from_file_location("carried_task_scan", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load()


def write_cursor(tmp_path: Path, cursor: object) -> Path:
    path = tmp_path / "retrospect-hygiene-cursor.json"
    path.write_text(json.dumps(cursor, ensure_ascii=False), encoding="utf-8")
    return path


def run(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--cursor", str(path), *args],
        capture_output=True, text=True, check=False,
    )


class TestAcceptance:
    """The issue's two acceptance cases."""

    def test_one_carried_line_yields_one_candidate(self, tmp_path: Path):
        path = write_cursor(tmp_path, {
            "note": "\n".join(["[82nd 2026-08-05] Stage 4 실행 사이클", CYCLE_82_LINE]),
        })
        result = run(path)
        assert result.returncode == 0, result.stderr
        tasks = json.loads(result.stdout)
        assert len(tasks) == 1
        assert tasks[0]["cycle"] == "82nd 2026-08-05"
        assert tasks[0]["text"] == CYCLE_82_LINE.lstrip("- ")
        assert tasks[0]["field"] == "note"

    def test_same_line_after_a_disposition_yields_none(self, tmp_path: Path):
        path = write_cursor(tmp_path, {
            "note": "\n".join([
                "[82nd 2026-08-05] Stage 4 실행 사이클",
                CYCLE_82_LINE + " [carried-done 95th 2026-09-16]",
            ]),
        })
        result = run(path)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == []


class TestMarkers:
    def test_english_next_pass(self):
        tasks = mod.carried_tasks({"note": "- audit the remaining hooks, next pass task"})
        assert len(tasks) == 1

    def test_explicit_carried_marker(self):
        tasks = mod.carried_tasks({"note": "carried: re-run the corpus scan on the new build"})
        assert len(tasks) == 1

    def test_anchor_carried_vocabulary_is_not_a_task(self):
        """`Carried: #N` / `Carried: none` is the PR-anchor merge-gap record."""
        for line in ("Carried: #1094", "Carried: none — 후속 이슈 생성 지시 없음"):
            assert mod.carried_tasks({"note": line}) == []

    def test_skipped_disposition_also_silences(self):
        line = "- 전수 점검이 다음 패스 과제 [carried-skipped 96th: 상위 이슈로 이관]"
        assert mod.carried_tasks({"note": line}) == []

    def test_line_without_a_marker_is_not_a_task(self):
        line = "cycle 24: concurrent advance 없음 — union-merge 불필요."
        assert mod.carried_tasks({"note": line}) == []

    def test_short_line_is_ignored(self):
        assert mod.carried_tasks({"note": "다음 패스"}) == []


class TestCursorShapes:
    def test_note_as_a_list(self):
        tasks = mod.carried_tasks({"note": ["[7th] pass", "- next pass: sweep the specs"]})
        assert len(tasks) == 1
        assert tasks[0]["cycle"] == "7th"

    def test_cycle_note_field_is_scanned_too(self):
        tasks = mod.carried_tasks({"cycle_note": "signal 4 미발화. 전수 점검이 다음 패스 과제."})
        assert len(tasks) == 1
        assert tasks[0]["field"] == "cycle_note"

    def test_cursor_without_any_note_field(self):
        """Another local project's cursor has last_pass/scanned/findings only."""
        assert mod.carried_tasks(
            {"last_pass": "2026-09-01", "scanned": [], "next_batch_pointer": "a.md",
             "findings": []}
        ) == []

    def test_note_of_an_unexpected_type_is_skipped(self):
        assert mod.carried_tasks({"note": {"nested": "다음 패스 과제"}}) == []

    def test_cycle_tag_carries_down_to_later_lines(self):
        tasks = mod.carried_tasks({"note": "\n".join([
            "[90th 2026-09-01] 사이클",
            "- 첫 과제가 다음 패스 과제",
            "- 둘째 과제도 다음 패스 과제",
        ])})
        assert [t["cycle"] for t in tasks] == ["90th 2026-09-01", "90th 2026-09-01"]

    def test_a_cycle_tag_does_not_reach_the_next_field(self):
        """`note` and `cycle_note` are separate blocks of prose.

        A header is provenance for the lines under it, and `cycle_note` starts
        a new block — so the last header in `note` says nothing about it. The
        first row is the control: the reset must not cost `note` its own tag.
        """
        tasks = mod.carried_tasks({
            "note": "\n".join([
                "[90th 2026-09-01] 사이클",
                "- 첫 과제가 다음 패스 과제",
            ]),
            "cycle_note": "- 헤더 없는 과제가 다음 패스 과제",
        })
        assert [t["cycle"] for t in tasks] == ["90th 2026-09-01", None]
        assert [t["field"] for t in tasks] == ["note", "cycle_note"]


class TestFailureModes:
    def test_missing_cursor_is_not_an_error(self, tmp_path: Path):
        result = run(tmp_path / "absent.json")
        assert result.returncode == 0
        assert json.loads(result.stdout) == []

    def test_malformed_json_exits_2(self, tmp_path: Path):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        result = run(path)
        assert result.returncode == 2
        assert "cursor unreadable" in result.stderr

    def test_non_object_json_exits_2(self, tmp_path: Path):
        path = write_cursor(tmp_path, ["a list, not a cursor"])
        result = run(path)
        assert result.returncode == 2
        assert "not a JSON object" in result.stderr

    def test_an_unreadable_cursor_exits_2_rather_than_reporting_none(
        self, tmp_path: Path
    ):
        """A cursor that cannot be READ is not a cursor that is not THERE.

        Both come back as an empty list and exit 0 if the check is
        `Path.exists()`, which answers False for anything it cannot stat — and
        Stage 1.5 reads that output as "nothing carried".
        """
        locked = tmp_path / "locked"
        locked.mkdir()
        path = write_cursor(locked, {"note": "- 과제 하나가 다음 패스 과제"})

        # Positive control: while it is readable, that task IS reported. Without
        # this the assertion below cannot tell a fixed gate from a bad fixture.
        before = run(path)
        assert before.returncode == 0
        assert len(json.loads(before.stdout)) == 1

        locked.chmod(0o000)
        try:
            if Path(path).exists():  # pragma: no cover - root, or a lenient FS
                pytest.skip("the directory mode did not make the cursor unstattable")
            result = run(path)
        finally:
            locked.chmod(0o700)
        assert result.returncode == 2
        assert "cursor unreadable" in result.stderr


def test_text_format(tmp_path: Path):
    path = write_cursor(tmp_path, {"note": "\n".join(["[82nd 2026-08-05] x", CYCLE_82_LINE])})
    result = run(path, "--format", "text")
    assert result.returncode == 0
    assert result.stdout.strip() == f"[82nd 2026-08-05] {CYCLE_82_LINE.lstrip('- ')}"


@pytest.mark.parametrize("marker", ["다음 패스", "다음패스", "next pass", "Next Pass"])
def test_marker_spellings(marker: str):
    assert len(mod.carried_tasks({"note": f"- 전수 점검이 {marker} 과제입니다"})) == 1
