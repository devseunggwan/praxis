"""Gates read the text of an image-attached Hermes message (#1620).

Hermes stores non-string content (a user message with an image is a list of
parts) as `"\\x00json:" + json.dumps(parts)`, and passes the parts list itself
as `pre_llm_call`'s `user_message`.

Run: python3 -m pytest tests/hooks/_lib/test_hermes_multimodal_content.py -q
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


base = _load("test_hermes_base_for_multimodal", HERE / "test_hermes.py")
hermes = base.hermes

PREFIX = "\x00json:"  # Hermes `_CONTENT_JSON_PREFIX`


def _with_image(text: str) -> list:
    """The parts Hermes's `build_native_content_parts` builds for text plus one image."""
    return [{"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]


def _stored(content) -> str:
    """`_encode_content`: non-strings, and strings carrying the prefix, become prefix + JSON."""
    return PREFIX + json.dumps(content)


# --- content_text --------------------------------------------------------------

def test_stored_parts_decode_to_their_text():
    assert hermes.content_text(_stored(_with_image("merge"))) == "merge"
    # json.dumps escapes non-ASCII; decoding restores it.
    assert hermes.content_text(_stored(_with_image("머지해주세요"))) == "머지해주세요"


def test_plain_and_edge_forms():
    assert hermes.content_text("merge") == "merge"
    assert hermes.content_text(_with_image("merge")) == "merge"  # pre_llm_call's raw list
    assert hermes.content_text(_stored(PREFIX + "literal")) == PREFIX + "literal"  # double-encoded literal
    assert hermes.content_text(PREFIX + "{not json") == PREFIX + "{not json"  # Hermes keeps it raw
    assert hermes.content_text(None) == ""
    assert hermes.content_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "a\nb"


# --- transcript ----------------------------------------------------------------

def _merge_rows(approval: str) -> list:
    return [(1, "s1", "user", "브리핑 해줘", None, None, None),
            (2, "s1", "assistant", base._BRIEF_7, None, None, None),
            (3, "s1", "user", approval, None, None, None),
            (4, "s1", "assistant", "", None,
             json.dumps([base._call("m1", "terminal", {"command": "gh pr merge 7 --squash"})]), None)]


def test_transcript_carries_the_user_text_not_the_stored_json(tmp_path):
    db = base._db(tmp_path / "state.db", _merge_rows(_stored(_with_image("merge"))))
    out = hermes.sync_transcript("s1", tmp_path / "t", db)
    users = [e for e in map(json.loads, out.read_text().splitlines()) if e["type"] == "user"]
    assert users[-1]["message"]["content"] == "merge"


def test_merge_gate_reads_an_image_attached_approval(tmp_path):
    db = base._db(tmp_path / "state.db", _merge_rows(_stored(_with_image("merge"))))
    assert '"deny"' not in base._merge_gate(hermes.sync_transcript("s1", tmp_path / "t", db))


def test_plain_approval_control(tmp_path):
    db = base._db(tmp_path / "state.db", _merge_rows("merge"))
    assert '"deny"' not in base._merge_gate(hermes.sync_transcript("s1", tmp_path / "t", db))


def test_stored_tool_and_assistant_content_is_decoded(tmp_path):
    db = base._db(tmp_path / "state.db", [
        (1, "s1", "user", "go", None, None, None),
        (2, "s1", "assistant", _stored([{"type": "text", "text": "looking"}]), None,
         json.dumps([base._call("c1", "terminal", {"command": "false"})]), None),
        (3, "s1", "tool", _stored([{"type": "text", "text": json.dumps({"exit_code": 1})}]), "c1", None, "terminal"),
    ])
    events = [json.loads(line) for line in hermes.sync_transcript("s1", tmp_path / "t", db).read_text().splitlines()]
    assert events[1]["message"]["content"][0] == {"type": "text", "text": "looking"}
    assert events[2]["message"]["content"][0]["is_error"] is True


# --- pre_llm_call --------------------------------------------------------------

def _prompt_payload(user_message) -> dict:
    return {"hook_event_name": "pre_llm_call", "session_id": "s1", "extra": {"user_message": user_message}}


def test_prompt_gates_run_on_an_image_attached_prompt(tmp_path):
    """session-intent records a read-intent opener; it must see the text beside the image."""
    intent = tmp_path / "intent.json"
    payload = dict(_prompt_payload(_with_image("두 PR을 비교해줘")), cwd=str(tmp_path))
    base._bridge(payload, tmp_path, {"PRAXIS_SESSION_INTENT_FILE": str(intent)})
    assert intent.is_file()
    assert json.loads(intent.read_text())["read_intent_anchored"] is True


def test_plain_prompt_control(tmp_path):
    intent = tmp_path / "intent.json"
    payload = dict(_prompt_payload("두 PR을 비교해줘"), cwd=str(tmp_path))
    base._bridge(payload, tmp_path, {"PRAXIS_SESSION_INTENT_FILE": str(intent)})
    assert json.loads(intent.read_text())["read_intent_anchored"] is True


def test_missing_prompt_runs_nothing(tmp_path):
    assert base._bridge(dict(_prompt_payload(None), cwd=str(tmp_path)), tmp_path) == {}




# --- content with no text part (review follow-up) --------------------------------

def test_image_only_approval_does_not_reach_back_to_an_older_one(tmp_path):
    """An image-only reply has no text; it must not be read as an empty message that
    the last-user-message reader skips, which would surface the earlier 'merge'."""
    image_only = _stored([{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}])
    rows = _merge_rows("merge")[:3] + [
        (4, "s1", "assistant", "Wait, one more check. PR #7 을 머지할까요?", None, None, None),
        (5, "s1", "user", image_only, None, None, None),
        (6, "s1", "assistant", "", None,
         json.dumps([base._call("m1", "terminal", {"command": "gh pr merge 7 --squash"})]), None)]
    db = base._db(tmp_path / "state.db", rows)
    assert '"deny"' in base._merge_gate(hermes.sync_transcript("s1", tmp_path / "t", db))


def test_a_stored_object_keeps_its_json():
    """A non-text tool result must stay parseable, so a failure is still marked is_error."""
    assert json.loads(hermes.content_text(_stored({"exit_code": 1, "error": "boom"}))) == {
        "exit_code": 1, "error": "boom"}
    assert hermes.content_text(_stored(True)) == "true"


def test_only_text_parts_are_read():
    """Same filter as `_transcript.read_last_user_record`: a typed non-text part is not text."""
    parts = [{"type": "reasoning", "text": "hidden"}, {"type": "text", "text": "shown"}, {"text": "untyped"}]
    assert hermes.content_text(parts) == "shown\nuntyped"


def test_image_only_or_odd_prompt_runs_no_prompt_gate(tmp_path):
    intent = tmp_path / "intent.json"
    env = {"PRAXIS_SESSION_INTENT_FILE": str(intent)}
    image_only = [{"type": "image_url", "image_url": {"url": "data:x"}}]
    for odd in (image_only, {"role": "user"}, 5):
        assert base._bridge(dict(_prompt_payload(odd), cwd=str(tmp_path)), tmp_path, env) == {}
    assert not intent.exists()
