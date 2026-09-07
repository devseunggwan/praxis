"""Verify dispatch budget behaviour under a *real* starved group (issue #1216).

Issue #1195 fixed Bash dispatch-group budget handling and verified it with a
simulated clock (synthetic fake members that ``time.sleep`` inside the group).
This file adds the missing *real-sleep* coverage:

1. **Starved path**: an early fixture member eats nearly all the group budget
   by sleeping with ``time.sleep``.  The real ``pre-gh-pr-create-dedup-gate``
   impl is placed after it.  With the budget gone the gate must be:

   - Skipped fail-open (group rc = 0, not 2 — never a block)
   - Recorded as ``decision="skip"`` in the fire ledger
   - Reported on stderr with the ``[dispatch] budget-skip`` marker

   The issue's question: "does ``pre-gh-pr-create-dedup-gate`` really pass
   through when starved?" — the answer must be observable from fire-ledger
   records and gate stderr, not just from a member deadline set by hand.

2. **Positive control**: same gate, full budget, ``gh pr create`` payload.
   The gate must *run* (not be skipped), and whatever it does under ``gh``
   absence/timeout must produce a ``decision`` that is NOT ``"skip"``.
   Without a positive control "gate was skipped" is indistinguishable from
   "gate was never in the group at all".

Design decisions
----------------
- ``monkeypatch.setattr(_dispatch, "load_group", ...)`` injects a custom
  roster so the test is independent of the live manifest and does not drag
  along the 49-member Bash group.
- The delay fixture uses a **real** ``time.sleep`` (not mocked) so wall-clock
  budget is genuinely consumed.
- ``PRAXIS_FIRE_TELEMETRY_FILE`` routes records to a tmp file; the test reads
  them back as JSONL to check ``decision`` values.
- ``_fire_ledger._DISPATCHER_PROCESS`` is reset to ``False`` before each test:
  ``run_group`` sets it to ``True`` on first call (mark_dispatcher_process),
  and a subsequent call in the same process would skip all coarse recording
  and leave the dispatcher's group records un-written.
- The gate needs ``--repo owner/repo`` in the payload so it skips the
  ``git remote get-url origin`` lookup, which would fail in a no-repo tmp dir.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "hooks" / "_lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import _dispatch  # noqa: E402
import _fire_ledger  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Shared between tests — a ``gh pr create`` payload with an explicit ``--repo``
# so the gate skips the ``git remote get-url origin`` fallback (unavailable in
# the tmp working directory).
_GH_PR_CREATE_PAYLOAD = json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": "gh pr create --repo owner/repo --title 'test: add coverage'"},
    "cwd": str(REPO_ROOT),
    "session_id": "test-starved-group",
})

# The real impl path.  Resolved once at import time so all tests share it.
_GATE_IMPL = (
    REPO_ROOT / "hooks" / "preflight-gate"
    / "pre-gh-pr-create-dedup-gate" / "impl.py"
)

# How long the delay fixture sleeps.  Must be long enough to eat most of the
# tight test budget but short enough not to make the test suite crawl.
_DELAY_SLEEP_SEC = 0.8

# Group budget used in the starved-path test.
# deadline = budget - _GROUP_BUDGET_MARGIN_SEC (1.0) ⇒ ~0.6 s runway.
# The delay fixture sleeps _DELAY_SLEEP_SEC (0.8 s) which is > 0.6 s, so the
# gate arrives after the floor and is skipped.
_STARVED_BUDGET_SEC = 1.6

# Group budget used in the positive-control test: large enough that the gate
# always has room to run regardless of host load.
_FULL_BUDGET_SEC = 15.0


def _write_delay_fixture(tmp_path: Path) -> Path:
    """Write a fixture ``impl.py`` that sleeps to consume real wall-clock time."""
    d = tmp_path / "delay-fixture"
    d.mkdir(parents=True, exist_ok=True)
    impl = d / "impl.py"
    impl.write_text(
        f"import time\n"
        f"def main():\n"
        f"    time.sleep({_DELAY_SLEEP_SEC})\n"
        f"    return 0\n"
    )
    return impl


def _ledger_decisions(path: Path) -> dict[str, str]:
    """Parse ``{hook: decision}`` from a fire ledger and its `pass` counters.

    Both files, because since issue #1238 a `pass` is not a row in ``path`` at
    all — it is counted into a per-session ``fire-counts-*.jsonl`` sibling. A
    reader that opens only the events file sees a hook that passed as a hook
    that never fired, which is exactly the distinction this test is making.
    """
    _fire_ledger.flush_pass_counts()
    result: dict[str, str] = {}
    sources = [path, *sorted(path.parent.glob("fire-counts-*.jsonl"))]
    for source in sources:
        if not source.exists():
            continue
        for line in source.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            result[rec["hook"]] = rec["decision"]
    return result


def _patch_group(monkeypatch, members, budget, timeouts=None):
    """Inject a fake roster into ``_dispatch.load_group``."""
    monkeypatch.setattr(
        _dispatch,
        "load_group",
        lambda _e, _m, _h=None: (members, budget, dict(timeouts or {})),
    )


@pytest.fixture(autouse=True)
def _reset_dispatcher_flag():
    """Ensure both dispatcher flags are False before and after each test.

    ``run_group`` calls ``mark_dispatcher_process()``, which sets
    ``_DISPATCHER_PROCESS`` *and* ``_IN_DISPATCHER`` for the lifetime of the
    process. Resetting only the first leaves exactly the leak this fixture
    exists to stop: ``record_session_fire`` gates on ``_IN_DISPATCHER`` and
    nothing else — "Gated on ``_IN_DISPATCHER``, never ``_DISPATCHER_PROCESS``"
    (`hooks/_lib/_fire_ledger.py`) — so a stale True silently drops the
    fire-ledger record of every later test sharing this process.
    """
    _fire_ledger._DISPATCHER_PROCESS = False
    _fire_ledger._IN_DISPATCHER = False
    yield
    _fire_ledger._DISPATCHER_PROCESS = False
    _fire_ledger._IN_DISPATCHER = False


# ---------------------------------------------------------------------------
# Test 1 — starved path
# ---------------------------------------------------------------------------

def test_starved_group_gate_is_skipped_fail_open(tmp_path, monkeypatch, capsys):
    """A real sleep consumes the group budget; the gate is skipped, not blocked.

    Oracles:
    - Group exit code is 0 (fail-open, never blocks the tool call).
    - ``[dispatch] budget-skip pre-gh-pr-create-dedup-gate`` appears in stderr.
    - Fire-ledger records ``decision="skip"`` for the gate.
    - Fire-ledger records something other than "skip" for the delay fixture
      (it actually ran).
    """
    assert _GATE_IMPL.exists(), f"gate impl missing: {_GATE_IMPL}"

    delay_impl = _write_delay_fixture(tmp_path)

    members = [
        ("advisory-nudge", "delay-fixture", delay_impl),
        ("preflight-gate", "pre-gh-pr-create-dedup-gate", _GATE_IMPL),
    ]

    # Only the gate has a declared manifest timeout; the fixture has none.
    # That matches the real manifest shape (timeout required for skip-floor
    # arithmetic, but the dispatcher skips any member below the floor regardless
    # of whether a timeout is declared).
    timeouts = {("preflight-gate", "pre-gh-pr-create-dedup-gate"): 4.0}

    _patch_group(monkeypatch, members, _STARVED_BUDGET_SEC, timeouts)

    ledger = tmp_path / "fire-events-starved.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(ledger))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)

    t0 = time.monotonic()
    rc = _dispatch.run_group("PreToolUse", "Bash", _GH_PR_CREATE_PAYLOAD)
    elapsed = time.monotonic() - t0

    captured = capsys.readouterr()

    # --- oracle 1: group exit code ---
    assert rc == 0, (
        f"Starved gate must fail-open (rc 0), got rc={rc}. "
        f"stderr: {captured.err!r}"
    )

    # --- oracle 2: skip marker on stderr ---
    skip_needle = f"{_dispatch._SKIP_MARKER} preflight-gate/pre-gh-pr-create-dedup-gate"
    assert skip_needle in captured.err, (
        f"Expected budget-skip marker for the gate in stderr.\n"
        f"  needle: {skip_needle!r}\n"
        f"  stderr: {captured.err!r}"
    )

    # --- oracle 3: fire-ledger decision ---
    assert ledger.exists(), "Fire-ledger file was not created."
    decisions = _ledger_decisions(ledger)

    assert "pre-gh-pr-create-dedup-gate" in decisions, (
        f"Gate has no fire-ledger record. Recorded hooks: {list(decisions)}"
    )
    assert decisions["pre-gh-pr-create-dedup-gate"] == "skip", (
        f"Gate ledger decision must be 'skip', got {decisions['pre-gh-pr-create-dedup-gate']!r}"
    )

    # The delay fixture ran — its decision is anything other than "skip".
    assert "delay-fixture" in decisions, (
        f"Delay fixture has no fire-ledger record. Recorded hooks: {list(decisions)}"
    )
    assert decisions["delay-fixture"] != "skip", (
        f"Delay fixture must have run (not skipped), got {decisions['delay-fixture']!r}"
    )

    # Sanity: elapsed time reflects the real sleep (fixture ran, gate did not).
    assert elapsed >= _DELAY_SLEEP_SEC * 0.8, (
        f"Elapsed {elapsed:.3f}s is suspiciously short; "
        f"the delay fixture may not have run."
    )


# ---------------------------------------------------------------------------
# Test 2 — positive control (full budget, gate actually runs)
# ---------------------------------------------------------------------------

def test_full_budget_gate_is_not_skipped(tmp_path, monkeypatch, capsys):
    """With a full budget the gate runs; its decision is never 'skip'.

    Without this control "gate skipped" and "gate never registered" are
    indistinguishable from the ledger alone.

    The gate will either:
    - Pass fail-open (``gh`` binary absent / auth failure / no keywords) — rc 0.
    - Attempt a real ``gh pr list`` — we let it time-out or fail; the exit code
      from the group must still be 0 or 2 but the *ledger* decision must not be
      "skip".

    We do NOT assert a specific non-skip decision value because the gate's
    behaviour under real-``gh`` absence (pass, advise, block) is environment-
    dependent and out of scope for this test.
    """
    assert _GATE_IMPL.exists(), f"gate impl missing: {_GATE_IMPL}"

    members = [
        ("preflight-gate", "pre-gh-pr-create-dedup-gate", _GATE_IMPL),
    ]
    timeouts = {("preflight-gate", "pre-gh-pr-create-dedup-gate"): 4.0}

    _patch_group(monkeypatch, members, _FULL_BUDGET_SEC, timeouts)

    ledger = tmp_path / "fire-events-full.jsonl"
    monkeypatch.setenv("PRAXIS_FIRE_TELEMETRY_FILE", str(ledger))
    monkeypatch.delenv("PRAXIS_FIRE_TELEMETRY_DISABLE", raising=False)

    rc = _dispatch.run_group("PreToolUse", "Bash", _GH_PR_CREATE_PAYLOAD)

    captured = capsys.readouterr()

    # --- oracle 0: the exit-code contract this test's own header states ---
    # Discarding rc would let any return value pass, including the codes the
    # host reads as something other than allow/deny.
    assert rc in (0, 2), f"group exit code must be 0 or 2, got {rc}"

    # --- oracle 1: no skip marker ---
    skip_needle = f"{_dispatch._SKIP_MARKER} preflight-gate/pre-gh-pr-create-dedup-gate"
    assert skip_needle not in captured.err, (
        f"Gate must NOT be skipped under full budget.\n"
        f"  stderr: {captured.err!r}"
    )

    # --- oracle 2: fire-ledger decision is not "skip" ---
    assert ledger.exists(), "Fire-ledger file was not created."
    decisions = _ledger_decisions(ledger)

    assert "pre-gh-pr-create-dedup-gate" in decisions, (
        f"Gate has no fire-ledger record. Recorded hooks: {list(decisions)}"
    )
    assert decisions["pre-gh-pr-create-dedup-gate"] != "skip", (
        f"Gate must not be 'skip' under full budget, "
        f"got {decisions['pre-gh-pr-create-dedup-gate']!r}"
    )
