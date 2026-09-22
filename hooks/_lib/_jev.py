"""Client for TypeSafe's System One structured-decision API (`jev`), issue #1481.

A System One call evaluates one `state` string against typed `questions`
(`noul`, `choice`, `score`) and returns a value per question with a
probability distribution and a confidence. There is no rationale string.

Contract, mirroring the sibling `_git.py`:

  - `ask` returns the response's `answers` dict, or None on ANY failure:
    no key, kill switch set, network error, timeout, non-2xx status, a body
    that is not JSON or carries no `answers` object, no runway to spawn.
  - Never raises.
  - Nothing is logged. The state is user task text and PRIVACY.md forbids
    persisting it.

Key resolution: `TYPESAFE_API_KEY`, else the macOS Keychain generic password
with service `typesafe-api-key`. Agent shells often do not source the login
profile that exports the variable, so the Keychain read is the path that
actually works there. No key means no egress.

`PRAXIS_SKIP_JEV_ROUTING=1` disables every call.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path as _Path
from typing import Any, Optional

sys.path.insert(0, str(_Path(__file__).resolve().parent))
from _hook_runtime import (  # type: ignore[import-not-found]  # noqa: E402
    MIN_SUBPROC_BUDGET_SEC,
    remaining_budget,
)

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
KEYCHAIN_SERVICE = "typesafe-api-key"
SKIP_ENV = "PRAXIS_SKIP_JEV_ROUTING"


def api_key(timeout: float = 3) -> Optional[str]:
    """Return the TypeSafe API key, or None when none is configured."""
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if key:
        return key
    user = os.environ.get("USER", "")
    if not user:
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", user,
             "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def ask(
    state: str,
    questions: dict[str, Any],
    timeout: float = 5,
    endpoint: str = ENDPOINT,
    key: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Send one System One request; return its `answers` dict or None."""
    if os.environ.get(SKIP_ENV) == "1":
        return None
    budget = remaining_budget(timeout)
    if budget < MIN_SUBPROC_BUDGET_SEC:
        return None
    key = key or api_key()
    if not key:
        return None
    body = json.dumps(
        {"state": state, "model": MODEL, "questions": questions}
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=min(timeout, budget)) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    answers = payload.get("answers") if isinstance(payload, dict) else None
    return answers if isinstance(answers, dict) else None


def ask_samples(
    state: str,
    questions: dict[str, Any],
    n: int,
    timeout: float = 5,
    endpoint: str = ENDPOINT,
) -> list[dict[str, Any]]:
    """Issue `n` identical requests in parallel; return the answers that came back.

    The model is not deterministic, so callers that act on a verdict sample
    it more than once. The key is resolved once and shared across the calls.
    """
    if os.environ.get(SKIP_ENV) == "1" or n < 1:
        return []
    key = api_key()
    if not key:
        return []
    with ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(
            lambda _: ask(state, questions, timeout=timeout,
                          endpoint=endpoint, key=key),
            range(n),
        ))
    return [r for r in results if r is not None]
