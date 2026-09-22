#!/usr/bin/env python3
"""Pick a delegation worker for a task with jev, or say to use the current rule (#1481).

Usage: jev-route.py "<task text>"

Prints one JSON line and always exits 0:
  {"source": "jev", "provider": "claude", "tier": "opus", "nouls": {...}}
  {"source": "fallback", "reason": "...", "nouls": {...}}

On fallback the caller applies the routing it already had; this script does
not know whether that is the single-delegation default or distribute mode.

The four questions, their order, the 3-sample mean and the 0.10 band are the
configuration that passed the #1481 gate (30 labelled tasks, 13 matches
against 9 for both current baselines). Change them and the gate no longer
speaks for the result.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hooks" / "_lib"))
import _jev  # type: ignore[import-not-found]  # noqa: E402

SAMPLES = 3
# Measured drift on identical input reached 0.12, so a mean this close to 0.5
# could land on either side on the next call.
AMBIGUITY_BAND = 0.10

QUESTIONS = {
    "deep": {"type": "noul", "instructions": "Does this task require architecture, policy or security design, or weighing cross-cutting trade-offs?"},
    "corpus": {"type": "noul", "instructions": "Is this task mainly searching, analyzing or summarizing a large body of existing material?"},
    "code": {"type": "noul", "instructions": "Is this task mainly writing or changing code against a clear, already-decided spec?"},
    "trivial": {"type": "noul", "instructions": "Is this task mechanical, such as removing, pinning, renaming, or fixing lint, with no real decisions?"},
}
DECISION_ORDER = (
    ("deep", "claude", "opus"),
    ("corpus", "gemini", ""),
    ("code", "codex", ""),
    ("trivial", "claude", "haiku"),
)


def route(task: str) -> dict[str, object]:
    samples = _jev.ask_samples(task, QUESTIONS, SAMPLES)
    if len(samples) < SAMPLES:
        return {"source": "fallback", "reason": f"{len(samples)}/{SAMPLES} samples returned"}
    try:
        values = {k: [float(s[k]["noul"]) for s in samples] for k in QUESTIONS}
    except (KeyError, TypeError, ValueError):
        return {"source": "fallback", "reason": "unexpected answer shape"}
    # A noul is a probability; NaN fails both comparisons and is rejected too.
    if not all(0.0 <= v <= 1.0 for vs in values.values() for v in vs):
        return {"source": "fallback", "reason": "noul out of range"}
    raw = {k: sum(vs) / SAMPLES for k, vs in values.items()}
    means = {k: round(v, 3) for k, v in raw.items()}
    for key, provider, tier in DECISION_ORDER:
        # Round only away float noise (0.6 - 0.5 is 0.0999...), not real distance.
        if round(abs(raw[key] - 0.5), 9) < AMBIGUITY_BAND:
            return {"source": "fallback", "reason": f"{key} ambiguous", "nouls": means}
        if raw[key] >= 0.5:
            return {"source": "jev", "provider": provider, "tier": tier, "nouls": means}
    return {"source": "jev", "provider": "claude", "tier": "sonnet", "nouls": means}


def main() -> int:
    task = " ".join(sys.argv[1:]).strip()
    result = route(task) if task else {"source": "fallback", "reason": "empty task"}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
