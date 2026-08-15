#!/usr/bin/env python3
"""PreToolUse(Bash) advisory: a foreground call whose explicit timeout outlasts
the status-briefing threshold removes the briefing's firing point.

Why this exists (issue #991):

The `Decision-Point Briefing → Proactive-execution trigger` rule forbids 3-5
minutes of silence, but that trigger can only fire BETWEEN turns. A single Bash
call that blocks for 16 minutes emits no assistant text while it runs, so the
rule becomes physically unreachable — the rule body already names this ("Secure
the firing point before a call expected to exceed the threshold — this is a
precondition on the call, not advice about it").

## The trigger is INVERTED relative to the issue's proposal

Issue #991 proposed firing on (a) a configurable long-running-command pattern
list and (b) `run_in_background` false with NO timeout specified. Both were
measured and rejected:

  (b) is backwards. A foreground Bash call that specifies no timeout returns
      control at the 120000ms (120s = 2.0 min) default — measured directly:
      `sleep 300` with no `timeout` field returned control at exactly 120s.
      120s is BELOW the 3-5 min briefing threshold, so signal (b) selects
      exactly the class of call that CANNOT breach the rule, and exempts the
      only class that can — a call carrying an explicit, large timeout. A
      16-minute block needs a timeout of roughly 966000ms or more.

  (a) fired 13 times with 0 true positives on the measured corpus, and hands
      out advice about `gh run watch` that contradicts
      `foreground-poll-loop-guard`, which REDIRECTS callers TO `gh run watch`.

So the trigger here is the inversion: fire only when the caller has explicitly
declared, in `tool_input.timeout`, an intent to block past the threshold. No
command pattern list, no per-repo configuration, no guessing which commands are
"expected to be long" — the caller's own declared ceiling is the evidence.

Design:

  • **Advisory only** — stderr, exit 0, never blocks. No bypass env var: there
    is nothing to bypass. Mirrors `pytest-direct-exec-advisory`'s shape.
  • **Declared-intent keyed** — the hook reads `tool_input.timeout` only. It
    never inspects the command text, so it cannot disagree with
    `foreground-poll-loop-guard` about which commands are legitimate.
  • **Two escape routes named** — `run_in_background: true` (the runtime
    re-invokes on exit, so the turn boundary survives) or a timeout below the
    threshold (control comes back and a briefing can be emitted).

Fail-open contract:

  • malformed / non-dict stdin JSON → exit 0
  • `tool_name != "Bash"` → exit 0
  • `run_in_background` true → exit 0
  • `timeout` absent → exit 0 (the 120s default already returns below threshold)
  • `timeout` non-numeric or non-positive → exit 0
  • `timeout` <= threshold → exit 0
  • any uncaught exception → swallowed, exit 0 via `@fail_open`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HOOK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_HOOK_DIR.parent.parent / "_lib"))
from _hook_runtime import fail_open  # type: ignore[import-not-found]  # noqa: E402

# The briefing rule forbids 3-5 minutes of silence; 180000ms (3 min) is its
# lower edge, so a declared ceiling above it is a declared intent to breach.
# Deliberately NOT the 120s constant `foreground-poll-loop-guard` uses — that
# one is the Bash *default timeout* (a property of the runtime), a different
# quantity from the briefing threshold (a property of the rule).
_BRIEFING_THRESHOLD_MS = 180000

# The Bash default that applies when `timeout` is absent — 2.0 min, already
# below the threshold. Measured, not assumed (see module docstring).
_DEFAULT_TIMEOUT_MS = 120000

# `run_in_background` arrives as a JSON boolean from the runtime. String forms
# are accepted defensively: for an advisory the fail-safe direction is silence,
# so anything that plausibly means "backgrounded" suppresses the nudge.
_TRUTHY_STRINGS = frozenset({"true", "1", "yes"})


def _is_backgrounded(value: object) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY_STRINGS
    return False


def _timeout_ms(value: object) -> float | None:
    """Return the declared timeout in ms, or None when it is absent/unusable.

    `bool` is rejected before `int` because `True` is an `int` in Python and
    would otherwise read as a 1ms timeout.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if number <= 0:
        return None
    return number


def _minutes(ms: float) -> str:
    return f"{ms / 60000:.1f} min"


def _advisory(timeout: float) -> str:
    return (
        "[long-foreground-call-advisory] "
        f"this foreground Bash call declares timeout={int(timeout)}ms "
        f"({_minutes(timeout)}), past the {_BRIEFING_THRESHOLD_MS}ms "
        f"({_minutes(_BRIEFING_THRESHOLD_MS)}) status-briefing threshold. "
        "No assistant text can be emitted while a single call blocks, so the "
        "3-5 min proactive-execution trigger has no firing point.\n"
        "[long-foreground-call-advisory] Secure the firing point first — "
        "either set `run_in_background: true` (the runtime re-invokes on exit, "
        f"so the turn boundary survives) or lower `timeout` below "
        f"{_BRIEFING_THRESHOLD_MS}ms to take control back and brief."
    )


@fail_open
def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0

    # Backgrounding is the correct pattern — it is one of the two escape
    # routes this advisory recommends, so never nudge a call that took it.
    if _is_backgrounded(tool_input.get("run_in_background")):
        return 0

    # Absent timeout is the issue's proposed signal (b), inverted: the
    # _DEFAULT_TIMEOUT_MS default returns control below the threshold on its
    # own, so this class of call structurally cannot breach the rule.
    timeout = _timeout_ms(tool_input.get("timeout"))
    if timeout is None or timeout <= _BRIEFING_THRESHOLD_MS:
        return 0

    sys.stderr.write(_advisory(timeout) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
