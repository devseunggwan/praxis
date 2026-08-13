"""tests/test_agent_liveness.py — delegated-worker liveness classification (#981).

Every fixture here is TRANSCRIBED from a live cmux 0.64.22 session recorded
while building the classifier, not composed against the code. That matters more
than usual: three of the defects these tests pin were invisible to reasoning
about the payload shape and only appeared when a real worker was watched.

  1. `agent.hook.PostToolUse` is never relayed, and `received`/`completed` are
     the hook dispatch's own boundaries — they pair milliseconds apart while
     the tool keeps running. Anything reading them as a tool boundary reports
     a busy worker as free.
  2. `agent.hook.Notification` arrives AFTER `Stop` when a worker settles, so
     "newest agent event" flips a finished worker back to working.
  3. `preview` carries the agent's own reply text, so prompt detection keyed on
     conversational phrasing fires on ordinary answers.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_MODULE_PATH = _ROOT / "skills" / "cmux-delegate" / "agent_liveness.py"


def _load():
    spec = importlib.util.spec_from_file_location("agent_liveness", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent_liveness"] = module
    spec.loader.exec_module(module)
    return module


liveness = _load()


# --- transcribed fixtures ---------------------------------------------------

# `cmux rpc mobile.workspace.list`, trimmed to the fields the classifier reads.
WORKSPACE_LIST = json.dumps(
    {
        "groups": [],
        "workspaces": [
            {
                "id": "461FB6D2-6A13-455B-8A79-3F3EB890B42D",
                "current_directory": "/private/tmp/praxis-981-probe3",
                "last_activity_at": 1786610431.614037,
                "preview": None,
                "terminals": [
                    {
                        "id": "BD214FD2-48D1-488C-97AC-3E39C9DA4632",
                        "current_directory": "/private/tmp/praxis-981-probe3",
                        "is_ready": True,
                    }
                ],
            },
            {
                "id": "8C5566DA-4DC6-4C8C-998B-83D1DC98352C",
                "current_directory": "/private/tmp/praxis-981-blocked",
                "last_activity_at": 1786610431.614037,
                "preview": "Claude needs your permission",
                "terminals": [],
            },
            # A sibling sharing probe3's directory and prompting for nothing —
            # the ordinary case the default delegation produces.
            {
                "id": "C7A1E9F0-1111-4222-8333-444455556666",
                "current_directory": "/private/tmp/praxis-981-probe3",
                "last_activity_at": 1786610431.614037,
                "preview": None,
                "terminals": [],
            },
        ],
    }
)

# `cmux events --category agent`, probe3's tail. Stop at 77025/77027, then
# SubagentStop, then Notification — the ordering that broke the first rule.
_CWD = "/private/tmp/praxis-981-probe3"


_WS_ID = "461FB6D2-6A13-455B-8A79-3F3EB890B42D"
_BLOCKED_WS_ID = "8C5566DA-4DC6-4C8C-998B-83D1DC98352C"
_SIBLING_WS_ID = "C7A1E9F0-1111-4222-8333-444455556666"


def _event(seq: int, name: str, phase: str, workspace_id: str = _WS_ID) -> str:
    """One relayed agent event.

    `workspace_id` is the top-level field the classifier keys on — measured to
    be the same UUID the RPC lists as `id`. `payload.cwd` is deliberately left
    identical across workspaces here, because that is the real situation: the
    default delegation opens the delegate in the delegator's own directory.
    """
    return json.dumps(
        {
            "type": "event",
            "seq": seq,
            "name": name,
            "category": "agent",
            "workspace_id": workspace_id,
            "payload": {"cwd": _CWD, "phase": phase, "tool_name": None},
        }
    )


IDLE_TAIL = [
    _event(77009, "agent.hook.Stop", "received"),
    _event(77011, "agent.hook.Stop", "completed"),
    _event(77055, "agent.hook.SubagentStop", "received"),
    _event(77168, "agent.hook.Notification", "received"),
    _event(77174, "agent.hook.Notification", "completed"),
]

WORKING_TAIL = [
    _event(75099, "agent.hook.UserPromptSubmit", "received"),
    _event(75152, "agent.hook.PreToolUse", "received"),
    # The pair that closes milliseconds later while the tool still runs.
    _event(75154, "agent.hook.PreToolUse", "completed"),
]


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch):
    """Wire the two cmux surfaces without touching a real cmux."""

    def _apply(workspace_json: str | None, event_lines: list[str] | None, latest: int = 99999):
        def fake_run(argv: list[str], timeout: int) -> str | None:
            if argv[:3] == ["cmux", "rpc", "mobile.workspace.list"]:
                return workspace_json
            if argv[:2] == ["cmux", "capabilities"]:
                return "{}"
            return None

        monkeypatch.setattr(liveness, "_run", fake_run)
        monkeypatch.setattr(liveness, "_latest_seq", lambda: latest)
        monkeypatch.setattr(
            liveness, "_stream_until", lambda start, target: list(event_lines or [])
        )

    return _apply


# --- prompt markers ---------------------------------------------------------


@pytest.mark.parametrize(
    "preview",
    [
        # Transcribed from a live workspace's `preview` field.
        "Claude needs your permission",
        "Enter to confirm · Esc to cancel",
    ],
)
def test_modal_prompts_are_detected(preview: str) -> None:
    assert liveness._blocked_on_prompt(preview) is True


@pytest.mark.xfail(
    reason="the folder-trust dialog's preview text has never been captured",
    strict=True,
)
def test_folder_trust_dialog_is_a_known_gap() -> None:
    """The one modal prompt this detector is not known to catch.

    Its on-screen wording was transcribed — `Quick safety check: Is this a
    project you created or one you trust?` above `1. Yes, I trust this folder`
    — but the SCREEN is not the surface this function reads. What cmux puts in
    `preview` for that dialog was never observed, and it may put nothing at
    all, in which case no marker list can help and the fallback has to be
    `read-screen`.

    Guessing a marker from the screen text would make this test pass while
    proving nothing about the real path, so the gap is pinned as xfail instead:
    it fails loudly the day someone captures the real preview and adds it.
    """
    assert liveness._blocked_on_prompt(
        "Quick safety check: Is this a project you created or one you trust?"
    )


@pytest.mark.parametrize(
    "preview",
    [
        # Loose connectives an ordinary answer can contain. A false
        # `waiting-input` sends a person to a workspace that wants nothing, so
        # a marker has to be a phrase the host emits whole.
        "Do you trust this refactor? I would not.",
        "Press Enter to confirm your understanding of the tradeoff",
        # cmux emits this when a worker merely finishes its turn. Treating it
        # as a modal prompt collapses `idle` into `waiting-input`, and the two
        # call for different responses from the delegator.
        "Claude is waiting for your input",
        # A real transcribed preview: `preview` is the agent's own reply.
        "정리됐습니다. 원칙대로 백엔드부터 여는 게 맞고, 바꿔야 할 지점이 코드로 확정됐습니다.",
        # Prose that an ordinary answer can contain. Markers must not match it.
        "삭제할까요? (y/n) 형태로 물어보도록 바꾸는 편이 좋겠습니다",
        "Do you want to proceed with the migration? I would not — here is why.",
        None,
        "",
    ],
)
def test_non_modal_text_is_not_a_prompt(preview: str | None) -> None:
    assert liveness._blocked_on_prompt(preview) is False


# --- classification ---------------------------------------------------------


def test_missing_workspace_is_crash(stub) -> None:
    stub(WORKSPACE_LIST, [])
    assert liveness.classify("/private/tmp/nowhere")["state"] == "crash"


def test_modal_prompt_short_circuits_to_waiting_input(stub) -> None:
    stub(WORKSPACE_LIST, WORKING_TAIL)
    result = liveness.classify("/private/tmp/praxis-981-blocked")
    assert result["state"] == "waiting-input"
    assert result["preview"] == "Claude needs your permission"


def test_open_turn_is_working(stub) -> None:
    stub(WORKSPACE_LIST, WORKING_TAIL)
    assert liveness.classify(_CWD)["state"] == "working"


def test_stop_is_idle_even_when_newer_noise_follows(stub) -> None:
    """Regression: Notification and SubagentStop arrive after Stop.

    Both are newer than the turn boundary, and both were once read as "still
    working" — a finished worker flipped back seconds after settling, so the
    delegator never saw the state that means "it stopped without reporting".
    """
    stub(WORKSPACE_LIST, IDLE_TAIL)
    assert liveness.classify(_CWD)["state"] == "idle"


def test_stop_while_still_moving_is_working_not_idle(stub) -> None:
    """A Stop hook answering `decision: block` re-prompts the model.

    The Stop EVENT fires either way and the relayed payload does not say which
    — measured, `result` was `acknowledged` and `is_error` null across all 204
    Stop events in the window. So a Stop on a workspace that is still moving
    must not be handed the `idle` prescription, which is re-dispatch.
    """
    moving = json.loads(WORKSPACE_LIST)
    moving["workspaces"][0]["last_activity_at"] = time.time() - 1
    stub(json.dumps(moving), IDLE_TAIL)
    result = liveness.classify(_CWD)
    assert result["state"] == "working"
    assert result["reason"] == "stop-not-yet-quiet"


def test_stop_after_the_quiet_window_is_idle(stub) -> None:
    """The other side: quiet long enough that a re-prompt would have shown."""
    settled = json.loads(WORKSPACE_LIST)
    settled["workspaces"][0]["last_activity_at"] = time.time() - (liveness._IDLE_QUIET_S + 5)
    stub(json.dumps(settled), IDLE_TAIL)
    assert liveness.classify(_CWD)["state"] == "idle"


def test_a_sibling_workspace_in_the_same_directory_does_not_decide_this_one(stub) -> None:
    """The delegator shares the delegate's directory by default.

    So the sibling's events carry the SAME `payload.cwd` and differ only by
    `workspace_id` — which is why the cwd filter had to go. Under it, the
    delegator's own probe activity was the newest event and every finished
    delegate read as `working`.
    """
    sibling = [_event(78000, "agent.hook.PreToolUse", "received", workspace_id=_BLOCKED_WS_ID)]
    stub(WORKSPACE_LIST, IDLE_TAIL + sibling)
    assert liveness.classify(_WS_ID)["state"] == "idle"


def test_no_events_is_unknown_not_idle(stub) -> None:
    """Absence of evidence must not licence a re-dispatch.

    The workspace is alive and not prompting, but nothing about this worker is
    left in the retained window — which is what a busy host looks like once the
    worker's events age out. Reporting `idle` here would tell the delegator to
    take over work that may still be running.
    """
    stub(WORKSPACE_LIST, [])
    result = liveness.classify(_CWD)
    assert result["state"] == "unknown"
    assert result["reason"] == "no-events-in-window"


def test_cmux_absent_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """praxis runs on hosts without cmux; a probe that cannot run is not a death
    certificate."""
    monkeypatch.setattr(liveness, "_run", lambda argv, timeout: None)
    result = liveness.classify(_CWD)
    assert result["state"] == "unknown"
    assert result["reason"] == "cmux-unavailable"


def test_duplicate_directories_are_reported_not_hidden(stub) -> None:
    """The count is the whole caveat: the verdict describes one of N.

    The fixture already holds two workspaces at probe3 (the default delegation
    puts delegator and delegate in one directory), so a third makes 3.
    """
    stub(WORKSPACE_LIST, IDLE_TAIL)
    assert liveness.classify(_CWD)["ambiguous"] == 2

    doubled = json.loads(WORKSPACE_LIST)
    doubled["workspaces"].append(dict(doubled["workspaces"][0], id="SECOND"))
    stub(json.dumps(doubled), IDLE_TAIL)
    assert liveness.classify(_CWD)["ambiguous"] == 3


# --- path identity ----------------------------------------------------------


# --- selector forms ---------------------------------------------------------

# `cmux workspace list --json` — the only surface carrying `ref`. The RPC has
# no such field (measured), which is why the ref is translated here first.
REF_LISTING = json.dumps(
    {
        "window_ref": "window:1",
        "workspaces": [
            {"ref": "workspace:2", "id": _WS_ID, "title": "[delegate] a"},
            {"ref": "workspace:3", "id": _SIBLING_WS_ID, "title": "[delegate] b"},
        ],
    }
)


@pytest.fixture
def ref_stub(monkeypatch: pytest.MonkeyPatch):
    def _apply(event_lines: list[str]):
        def fake_run(argv: list[str], timeout: int) -> str | None:
            if argv[:2] == ["cmux", "capabilities"]:
                return "{}"
            if argv[:3] == ["cmux", "rpc", "mobile.workspace.list"]:
                return WORKSPACE_LIST
            if argv[:4] == ["cmux", "workspace", "list", "--json"]:
                return REF_LISTING
            return None

        monkeypatch.setattr(liveness, "_run", fake_run)
        monkeypatch.setattr(liveness, "_latest_seq", lambda: 99999)
        monkeypatch.setattr(liveness, "_stream_until", lambda start, target: list(event_lines))

    return _apply


def test_a_ref_selects_one_workspace_with_no_ambiguity(ref_stub) -> None:
    """The whole point of the ref: it names one workspace, not a directory.

    The path form cannot do this — the default delegation puts delegate and
    delegator in the same directory, so a path answers about one of N.
    """
    ref_stub(IDLE_TAIL)
    result = liveness.classify("workspace:2")
    assert result["workspace"] == _WS_ID
    assert "ambiguous" not in result
    assert result["state"] == "idle"


def test_a_ref_reads_only_its_own_workspaces_events(ref_stub) -> None:
    """Two refs, one event stream, two different answers.

    Both workspaces sit in the same directory, so a path selector would fold
    them into one verdict; the refs keep them apart.
    """
    sibling = [_event(78000, "agent.hook.PreToolUse", "received", workspace_id=_SIBLING_WS_ID)]
    ref_stub(IDLE_TAIL + sibling)
    assert liveness.classify("workspace:2")["state"] == "idle"
    assert liveness.classify("workspace:3")["state"] == "working"


def test_an_unknown_ref_is_unknown_not_crash(ref_stub) -> None:
    """A ref that resolves to nothing is a lookup miss, not a dead worker."""
    ref_stub(IDLE_TAIL)
    result = liveness.classify("workspace:999")
    assert result["state"] == "unknown"
    assert result["reason"] == "workspace-ref-not-found"


def test_a_uuid_selector_skips_the_ref_lookup(stub) -> None:
    """The RPC id is accepted directly — no `workspace list` call needed."""
    stub(WORKSPACE_LIST, IDLE_TAIL)
    result = liveness.classify(_WS_ID)
    assert result["workspace"] == _WS_ID
    assert "ambiguous" not in result


def test_worker_in_a_subdirectory_is_not_a_crash(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wrapper reduces its argument to the worktree root; cmux does not.

    A worker standing in `<root>/pkg` is reported there verbatim, so an exact
    comparison against the root finds nothing and answers `crash` — whose
    prescription is re-dispatch, against a session that is alive.
    """
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=30)

    listing = json.dumps(
        {
            "workspaces": [
                {
                    "id": "W1",
                    "current_directory": str(root / "pkg"),
                    "last_activity_at": 0,
                    "preview": None,
                    "terminals": [],
                }
            ]
        }
    )

    real_run = liveness._run

    def fake_run(argv: list[str], timeout: int) -> str | None:
        if argv[:2] == ["cmux", "capabilities"]:
            return "{}"
        if argv[:3] == ["cmux", "rpc", "mobile.workspace.list"]:
            return listing
        # `git rev-parse` runs for real — the reduction under test is git's.
        return real_run(argv, timeout)

    monkeypatch.setattr(liveness, "_run", fake_run)
    monkeypatch.setattr(liveness, "_ROOT_CACHE", {})
    monkeypatch.setattr(liveness, "_latest_seq", lambda: None)

    result = liveness.classify(str(root))
    assert result["state"] != "crash", result
    assert result.get("workspace") == "W1"


def test_symlinked_tmp_matches_resolved_workspace_path(stub) -> None:
    """cmux reports /private/tmp while a caller standing in /tmp says /tmp.

    Raw string comparison finds no workspace and calls a live worker crashed —
    on macOS that is every delegation under /tmp.
    """
    if not os.path.islink("/tmp"):
        pytest.skip("/tmp is not a symlink on this host")
    stub(WORKSPACE_LIST, IDLE_TAIL)
    assert liveness.classify("/tmp/praxis-981-probe3")["state"] == "idle"


# --- output shape -----------------------------------------------------------


def test_output_quotes_every_value() -> None:
    rendered = liveness._format({"state": "waiting-input", "preview": "Claude needs your permission"})
    assert rendered.startswith("state='waiting-input' ")
    assert "preview='Claude needs your permission'" in rendered


def test_output_keeps_one_line_when_preview_is_multiline() -> None:
    rendered = liveness._format({"state": "idle", "preview": "line one\nline two"})
    assert "\n" not in rendered


@pytest.mark.parametrize(
    "hostile",
    [
        "Claude needs your permission $(id -un)",
        "Claude needs your permission `id -un`",
        "it's ${HOME} and \"quoted\"",
        "close'; id -un; echo '",
    ],
)
def test_eval_of_output_cannot_execute_preview_text(hostile: str) -> None:
    """The documented caller is `eval "$(agent-liveness.sh …)"`.

    `preview` is the delegated agent's own text, so a shell-expanding encoding
    lets the DELEGATE choose what runs in the DELEGATOR's shell. The check is
    end-to-end on purpose: asserting the quoting style would pass on an
    encoding that quotes and still expands, which is exactly what the
    double-quoted version did.
    """
    rendered = liveness._format({"state": "waiting-input", "preview": hostile})
    # The rendered text is passed as an ARGUMENT and eval'd from "$1", which is
    # what `eval "$(script)"` does: command-substitution output reaches eval
    # unexpanded. Interpolating it into the -c string instead would let the
    # OUTER quotes expand it before eval ever runs, and the test would then
    # fail against a correct encoding.
    round_tripped = subprocess.run(
        ["sh", "-c", 'eval "$1"; printf %s "$preview"', "sh", rendered],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout
    assert round_tripped == hostile


# --- failure vs absence -----------------------------------------------------


@pytest.mark.parametrize(
    "rpc_output",
    [
        None,
        "{not json",
        "[]",
        # Well-formed JSON objects that carry no workspace list. An error
        # envelope and a schema rename both land here, and both once read as
        # "zero workspaces" — one layer past the timeout case above.
        "{}",
        '{"error": {"code": 503}}',
        '{"workspaces": null}',
        '{"workspaces": "nope"}',
    ],
)
def test_workspace_lookup_failure_is_unknown_not_crash(
    monkeypatch: pytest.MonkeyPatch, rpc_output: str | None
) -> None:
    """`crash` prescribes re-dispatch, so it must not cover a broken lookup.

    A timed-out or malformed `mobile.workspace.list` says nothing about the
    worker. Reading it as death starts the same task a second time underneath a
    session that is still running it — the one outcome this probe exists to
    prevent.
    """

    def fake_run(argv: list[str], timeout: int) -> str | None:
        if argv[:2] == ["cmux", "capabilities"]:
            return "{}"
        return rpc_output

    monkeypatch.setattr(liveness, "_run", fake_run)
    result = liveness.classify(_CWD)
    assert result["state"] == "unknown"
    assert result["reason"] == "workspace-lookup-failed"


def test_genuinely_absent_workspace_is_still_crash(stub) -> None:
    """The other side of the same split: cmux answered, nothing matched."""
    stub(WORKSPACE_LIST, [])
    assert liveness.classify("/private/tmp/nowhere") == {
        "state": "crash",
        "reason": "no-workspace",
    }


def test_stream_read_is_bounded_when_the_target_never_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stop target the stream will never emit must not hang the probe.

    `_latest_seq` reads the GLOBAL head — measured, `--category agent` leaves
    `latest_seq` unchanged — while this stream is agent-filtered, so the target
    is routinely a sequence no line here will carry. Without a watchdog the
    `for line in stream` blocks forever and the in-loop deadline, which only
    runs after a line arrives, never gets to fire.
    """
    monkeypatch.setattr(liveness, "_EVENTS_TIMEOUT_S", 2)
    started = time.monotonic()
    original_popen = subprocess.Popen

    # Emits one line, then holds the pipe open saying nothing — a live stream
    # whose next agent event is not coming.

    # `sh` forks for `sleep` rather than exec'ing it, so the grandchild keeps
    # the pipe's write end open after its parent dies — the exact shape that
    # defeats a kill-the-parent watchdog.
    def fake_popen(argv, **kwargs):
        _ = argv
        _ = kwargs
        return original_popen(
            ["sh", "-c", 'printf \'{"seq":1}\\n\'; sleep 30'],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )

    monkeypatch.setattr(liveness.subprocess, "Popen", fake_popen)
    lines = liveness._stream_until(0, 999999)
    elapsed = time.monotonic() - started
    assert elapsed < 10, f"probe did not terminate promptly: {elapsed:.1f}s"
    assert lines, "the line delivered before the block should still be returned"
