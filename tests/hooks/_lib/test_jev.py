"""Tests for hooks/_lib/_jev.py — System One client (#1481).

The server below speaks real HTTP, so `ask` runs its actual urllib path. The
success body is a verbatim transcription of a live `jev-1.13.0` response, not
a shape derived from the client under test.

Coverage:
  - success: `answers` passthrough; request carries bearer key, model, questions
  - no egress without a key, and none under the kill switch
  - HTTP 401, non-JSON body, body without `answers`, timeout -> None
  - ask_samples: all succeed, partial failure, kill switch
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "hooks" / "_lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import _jev  # noqa: E402

LIVE_RESPONSE = {
    "model": "jev-1.13.0",
    "answers": {
        "is_internal": {"type": "noul", "noul": 0.04},
        "audience": {
            "type": "choice", "choice": "customer", "confidence": 0.92,
            "probabilities": {"customer": 0.95, "both": 0.04, "internal": 0.01},
        },
        "blog_worthiness": {
            "type": "score", "score": 1.67, "confidence": 0.51,
            "legend": {"0": "Not suitable at all", "1": "Marginal", "2": "Clearly suitable"},
            "probabilities": {"0": 0.02, "1": 0.3, "2": 0.68},
        },
    },
    "usage": {"input_tokens": 468, "output_tokens": 74},
}

QUESTIONS = {"is_internal": {"type": "noul", "instructions": "Internal tooling?"}}


class _Server:
    """One-route HTTP server whose reply is set per test."""

    def __init__(self) -> None:
        self.status = 200
        self.body: bytes = json.dumps(LIVE_RESPONSE).encode()
        self.delay = 0.0
        self.requests: list[dict[str, Any]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                outer.requests.append({
                    "auth": self.headers.get("Authorization"),
                    "json": json.loads(self.rfile.read(length)),
                })
                time.sleep(outer.delay)
                self.send_response(outer.status)
                self.end_headers()
                self.wfile.write(outer.body)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1/systemone"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()


@pytest.fixture()
def server():
    s = _Server()
    yield s
    s.close()


@pytest.fixture()
def keyed(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.delenv(_jev.SKIP_ENV, raising=False)


@pytest.fixture()
def keyless(monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv(_jev.SKIP_ENV, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))  # no `security` binary reachable


def test_success_returns_answers_and_sends_contract(server, keyed):
    answers = _jev.ask("state text", QUESTIONS, endpoint=server.url)
    assert answers == LIVE_RESPONSE["answers"]
    sent = server.requests[0]
    assert sent["auth"] == "Bearer test-key"
    assert sent["json"] == {"state": "state text", "model": "jev-latest", "questions": QUESTIONS}


def test_no_key_sends_nothing(server, keyless):
    assert _jev.api_key() is None
    assert _jev.ask("s", QUESTIONS, endpoint=server.url) is None
    assert server.requests == []


def test_kill_switch_sends_nothing(server, keyed, monkeypatch):
    monkeypatch.setenv(_jev.SKIP_ENV, "1")
    assert _jev.ask("s", QUESTIONS, endpoint=server.url) is None
    assert _jev.ask_samples("s", QUESTIONS, 3, endpoint=server.url) == []
    assert server.requests == []


@pytest.mark.parametrize("status,body", [
    (401, b'{"error": "unauthorized"}'),
    (200, b"not json"),
    (200, b'{"model": "jev-1.13.0"}'),
    (200, b'["answers"]'),
])
def test_failure_bodies_return_none(server, keyed, status, body):
    server.status, server.body = status, body
    assert _jev.ask("s", QUESTIONS, endpoint=server.url) is None


def test_timeout_returns_none(server, keyed):
    server.delay = 1.5
    assert _jev.ask("s", QUESTIONS, timeout=0.6, endpoint=server.url) is None


def test_ask_samples_all_succeed(server, keyed):
    got = _jev.ask_samples("s", QUESTIONS, 3, endpoint=server.url)
    assert got == [LIVE_RESPONSE["answers"]] * 3
    assert len(server.requests) == 3


def test_ask_samples_drops_failures(server, keyed):
    server.status = 500
    assert _jev.ask_samples("s", QUESTIONS, 3, endpoint=server.url) == []
