"""A V4A `patch` reaches the edit gates once per file it touches (#1619).

Hermes's `patch` tool takes a V4A multi-file patch (`mode: "patch"`) whose
targets live in `*** Update/Add/Delete/Move File:` headers, not in `path`.

Run: python3 -m pytest tests/hooks/_lib/test_hermes_v4a_patch.py -q
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


base = _load("test_hermes_base_for_v4a", HERE / "test_hermes.py")
hermes = base.hermes


def _v4a(body: str) -> dict:
    return {"mode": "patch", "patch": f"*** Begin Patch\n{body}\n*** End Patch"}


# --- translation ---------------------------------------------------------------

def test_update_becomes_an_edit_of_its_hunk_lines():
    args = _v4a("*** Update File: src/a.py\n@@ def f @@\n keep\n-old\n+new")
    assert hermes.claude_calls("patch", args) == [
        ("Edit", {"file_path": "src/a.py", "old_string": "keep\nold", "new_string": "keep\nnew",
                  "replace_all": False})]


def test_each_operation_is_its_own_call_in_patch_order():
    args = _v4a("*** Add File: new.txt\n+hello\n+world\n"
                "*** Update File: a.py\n-x\n+y\n"
                "*** Delete File: gone.txt\n"
                "*** Move File: old/p.py -> new/p.py")
    assert hermes.claude_calls("patch", args) == [
        ("Write", {"file_path": "new.txt", "content": "hello\nworld"}),
        ("Edit", {"file_path": "a.py", "old_string": "x", "new_string": "y", "replace_all": False}),
        ("Edit", {"file_path": "gone.txt", "old_string": "", "new_string": "", "replace_all": False}),
        ("Edit", {"file_path": "old/p.py", "old_string": "", "new_string": "", "replace_all": False}),
        ("Edit", {"file_path": "new/p.py", "old_string": "", "new_string": "", "replace_all": False}),
    ]


def test_markers_are_read_as_hermes_reads_them():
    """CRLF is tolerated; a +/- line quoting the end marker does not end the patch;
    a patch with no Begin marker is read from the top."""
    crlf = "*** Begin Patch\r\n*** Update File: a.py\r\n-*** End Patch\r\n+x\r\n*** End Patch\r\n"
    assert hermes.claude_calls("patch", {"mode": "patch", "patch": crlf}) == [
        ("Edit", {"file_path": "a.py", "old_string": "*** End Patch", "new_string": "x", "replace_all": False})]
    bare = {"mode": "patch", "patch": "*** Update File: b.py\n-1\n+2"}
    assert [c[1]["file_path"] for c in hermes.claude_calls("patch", bare)] == ["b.py"]


def test_replace_mode_is_unchanged():
    replace = {"path": "/a", "old_string": "o", "new_string": "n"}
    expected = [("Edit", {"file_path": "/a", "old_string": "o", "new_string": "n", "replace_all": False})]
    assert hermes.claude_calls("patch", replace) == expected
    assert hermes.claude_calls("patch", dict(replace, mode="replace")) == expected


def test_transcript_renders_one_block_per_file(tmp_path):
    call = base._call("p1", "patch", _v4a("*** Update File: a.py\n-x\n+y\n*** Delete File: b.py"))
    db = base._db(tmp_path / "state.db", [
        (1, "s1", "user", "edit", None, None, None),
        (2, "s1", "assistant", "", None, json.dumps([call]), None),
    ])
    out = hermes.sync_transcript("s1", tmp_path / "t", db)
    blocks = json.loads(out.read_text().splitlines()[-1])["message"]["content"]
    assert [(b["id"], b["name"], b["input"]["file_path"]) for b in blocks] == [
        ("p1", "Edit", "a.py"), ("p1#1", "Edit", "b.py")]


# --- through the bridge --------------------------------------------------------

def _dirty_main_checkout(path: Path) -> Path:
    """`main` with an uncommitted change elsewhere: pre-edit-protected-branch-guard's block case."""
    path.mkdir()
    git = ["git", "-C", str(path), "-c", "user.email=t@example.com", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "a.txt").write_text("a\n")
    (path / "b.txt").write_text("b\n")
    subprocess.run(git + ["add", "."], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "init"], check=True)
    (path / "b.txt").write_text("changed\n")
    return path


def _patch_payload(args: dict, cwd: Path) -> dict:
    return {"hook_event_name": "pre_tool_call", "tool_name": "patch", "tool_input": args,
            "session_id": "", "cwd": str(cwd), "extra": {"tool_call_id": "p1"}}


def _blocked_by_branch_guard(response: dict) -> bool:
    return response.get("action") == "block" and "protected-branch-guard" in response.get("message", "")


def test_v4a_update_on_a_protected_branch_is_blocked(tmp_path):
    repo = _dirty_main_checkout(tmp_path / "repo")
    target = repo / "a.txt"
    v4a = _v4a(f"*** Update File: {target}\n-a\n+z")
    assert _blocked_by_branch_guard(base._bridge(_patch_payload(v4a, repo), tmp_path))


def test_replace_mode_control_is_blocked_the_same_way(tmp_path):
    repo = _dirty_main_checkout(tmp_path / "repo")
    replace = {"path": str(repo / "a.txt"), "old_string": "a", "new_string": "z"}
    assert _blocked_by_branch_guard(base._bridge(_patch_payload(replace, repo), tmp_path))


def test_second_file_of_a_v4a_patch_is_checked_too(tmp_path):
    """The first file is already in the dirty diff (allowed); the second is not."""
    repo = _dirty_main_checkout(tmp_path / "repo")
    v4a = _v4a(f"*** Update File: {repo / 'b.txt'}\n-changed\n+again\n"
               f"*** Update File: {repo / 'a.txt'}\n-a\n+z")
    assert _blocked_by_branch_guard(base._bridge(_patch_payload(v4a, repo), tmp_path))


def test_each_hunk_of_an_update_is_its_own_edit():
    args = _v4a("*** Update File: a.py\n@@ def f @@\n-x\n+y\n@@ def g @@\n keep\n+added")
    assert hermes.claude_calls("patch", args) == [
        ("Edit", {"file_path": "a.py", "old_string": "x", "new_string": "y", "replace_all": False}),
        ("Edit", {"file_path": "a.py", "old_string": "keep", "new_string": "keep\nadded", "replace_all": False}),
    ]


def test_bare_markers_follow_hermes_start_and_stop():
    """Hermes parses from the LAST Begin line before the first End line, so a second
    Begin restarts the patch and an End before any Begin leaves nothing to apply."""
    restarted = {"mode": "patch", "patch": "*** Begin Patch\n*** Update File: dropped.py\n-1\n+2\n"
                 "*** Begin Patch\n*** Update File: kept.py\n-1\n+2\n*** End Patch"}
    assert [c[1]["file_path"] for c in hermes.claude_calls("patch", restarted)] == ["kept.py"]
    early_end = {"mode": "patch", "patch": "*** End Patch\n*** Begin Patch\n*** Update File: a.py\n-1\n+2"}
    assert hermes.claude_calls("patch", early_end) == []


def test_a_patch_hermes_rejects_becomes_no_call():
    """Hermes applies nothing from a patch with a parse error."""
    no_hunk = _v4a("*** Update File: a.py\n*** Update File: b.py\n-1\n+2")
    assert hermes.claude_calls("patch", no_hunk) == []


def _memory_payload(args: dict, memory_dir: Path) -> dict:
    return {"hook_event_name": "pre_tool_call", "tool_name": "patch", "tool_input": args,
            "session_id": "", "cwd": str(memory_dir), "extra": {"tool_call_id": "p1"}}


def test_deleting_a_memory_entry_is_not_read_as_an_empty_write(tmp_path):
    """memory-distillation-fields-gate checks Write content for frontmatter; a delete
    must not reach it as a Write of nothing."""
    memory = tmp_path / "memory"
    memory.mkdir()
    entry = memory / "feedback_x.md"
    entry.write_text("---\nname: x\n---\nbody\n")
    env = {"PRAXIS_MEMORY_DIR": str(memory)}
    def memory_gate(response: dict) -> bool:
        return "MEMORY DISTILLATION" in str(response.get("message", ""))

    # Control: a Write of nothing to the same entry is what the gate blocks.
    empty_write = {"hook_event_name": "pre_tool_call", "tool_name": "write_file",
                   "tool_input": {"path": str(entry), "content": ""}, "session_id": "",
                   "cwd": str(memory), "extra": {"tool_call_id": "w1"}}
    assert memory_gate(base._bridge(empty_write, tmp_path, env))
    delete = _v4a(f"*** Delete File: {entry}")
    assert not memory_gate(base._bridge(_memory_payload(delete, memory), tmp_path, env))
