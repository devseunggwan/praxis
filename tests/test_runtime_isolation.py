"""Test-run isolation from the real ~/.praxis (#1241).

`~/.praxis/logs/hook-errors.jsonl` received every crash a test provoked on
purpose (1,034 of 3,383 lines from one monkeypatched `TimeoutExpired`) because
only the fire ledger had a per-test root (#849). `tests/conftest.py` now roots
the error log, state and cache under a per-test directory as well.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_LIB = _REPO / "hooks" / "_lib"
sys.path.insert(0, str(_LIB))

import _hook_runtime  # noqa: E402
import _paths  # noqa: E402


# ---------------------------------------------------------------------------
# conftest: every runtime root is per-test
# ---------------------------------------------------------------------------

def test_conftest_points_every_runtime_root_at_the_test_tmp(tmp_path, tmp_path_factory):
    real = Path.home() / ".praxis"
    home = Path(_paths.praxis_home())
    assert home.is_relative_to(tmp_path_factory.getbasetemp())
    assert not home.is_relative_to(tmp_path)  # tests assert tmp_path holds only their own files
    assert real not in Path(_hook_runtime._error_log_path()).parents
    assert Path(_hook_runtime._error_log_path()).is_relative_to(home)
    assert "PRAXIS_STATE_DIR" not in os.environ  # an inherited override would win
    assert Path(_paths.praxis_state_dir()).is_relative_to(home)
    assert Path(_paths.praxis_cache_dir()).is_relative_to(home)
    # The pre-#527 read fallback expands ~, so HOME is per-test as well.
    assert Path(_paths.legacy_state_dir()).is_relative_to(tmp_path_factory.getbasetemp())


def test_a_hook_crash_provoked_in_process_lands_in_the_test_log(tmp_path_factory, monkeypatch):
    """The exact leak: a `@fail_open` main that raises writes the error log.

    The logger is configured once per process with the path current at that
    moment, so it is reset here to bind this test's path rather than the one
    of whichever test crashed first.
    """
    import logging
    logger = logging.getLogger(_hook_runtime._LOGGER_NAME)
    monkeypatch.setattr(logger, "handlers", [])
    monkeypatch.setattr(logger, "_praxis_configured", False, raising=False)

    @_hook_runtime.fail_open
    def main():
        raise RuntimeError("boom_1241")

    main()
    log = Path(_hook_runtime._error_log_path())
    assert log.is_file() and "boom_1241" in log.read_text(encoding="utf-8")
    assert log.is_relative_to(tmp_path_factory.getbasetemp())


def test_a_hook_spawned_as_a_subprocess_inherits_the_isolated_roots(tmp_path_factory):
    """`monkeypatch.setenv` writes os.environ, which is what a child inherits."""
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import _hook_runtime, _paths; "
        "print(_hook_runtime._error_log_path()); print(_paths.praxis_home())"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, str(_LIB)], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    base = tmp_path_factory.getbasetemp()
    assert all(Path(line).is_relative_to(base) for line in out), out
