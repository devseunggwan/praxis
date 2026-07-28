"""Global pytest fixtures — test-write isolation from production state (#849).

Autouse, session-independent: every test gets its OWN per-test fire-ledger
path, so a test that (directly or transitively, e.g. via
`_dispatch.run_group`/`run_one`) exercises a real hook impl can never leak a
record into the production ledger
(`~/.praxis/telemetry/fire-events-YYYY-MM-DD.jsonl`).

Root cause this closes: `tests/hooks/_lib/test_dispatch.py` calls
`_dispatch.run_group(...)` and `_dispatch.run_one(...)` directly against the
REAL hook roster with no `PRAXIS_FIRE_TELEMETRY_FILE`/`_DISABLE` override, so
every parametrized member run wrote a real coarse/rich record — the exact
9,476-record pollution (`_lib`/`boom`/`adv`/`ask`/`deny`/`pass`/`p1`/`p2`/
`real_block`/`allow`) found across 14 production ledger dates. The `_lib`
bucket specifically comes from `_dispatch.run_one`'s intentional
double-`fail_open`-wrap (see its docstring, "double-wrapping an already-
`@fail_open` main is harmless") — the second wrapper function is physically
defined in `hooks/_lib/_hook_runtime.py`, so `_hook_identity()`'s
parent-dir-of-`__code__.co_filename` rule resolves it to `hook="_lib",
role="hooks"` when `run_one` is called standalone (outside `run_group`, whose
`mark_dispatcher_process()` call would otherwise suppress that coarse
record). This is a real attribution quirk of the double-wrap, not a second
bug to fix — it only ever fires this way in tests that skip `run_group`.

A test that needs to assert on ledger CONTENT still sets its own
`PRAXIS_FIRE_TELEMETRY_FILE` (e.g. `tests/test_fire_ledger.py`) — safe to
override here because `monkeypatch` is the same function-scoped stack the
test body's own `monkeypatch.setenv(...)` call also uses; the test's own
value simply wins (last write).
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_fire_telemetry(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "PRAXIS_FIRE_TELEMETRY_FILE", str(tmp_path / "fire-events-test.jsonl")
    )
