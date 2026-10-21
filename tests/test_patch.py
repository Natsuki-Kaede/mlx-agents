"""apply_patch (Codex format), tolerant edit_file matching, argument fitting and tool-name hints."""

import pytest

from backend import store, tools
from tests.test_tools import run


def write(path, text):
    target = store.workspace() / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


def test_patch_updates_adds_deletes_and_moves():
    write("app.py", "def main():\n    print('hi')\n    return 0\n\n\ndef other():\n    pass\n")
    write("old.txt", "bye\n")
    write("move.txt", "a\nb\n")
    text, extra = run("apply_patch", patch="""*** Begin Patch
*** Update File: app.py
@@ def main():
-    print('hi')
+    print('hello')
     return 0
@@ def other():
-    pass
+    return 1
*** Add File: docs/new.md
+# Title
+body
*** Delete File: old.txt
*** Update File: move.txt
*** Move to: moved/move.txt
 a
-b
+c
*** End Patch""")
    ws = store.workspace()
    assert (ws / "app.py").read_text() == "def main():\n    print('hello')\n    return 0\n\n\ndef other():\n    return 1\n"
    assert (ws / "docs/new.md").read_text() == "# Title\nbody\n"
    assert not (ws / "old.txt").exists() and not (ws / "move.txt").exists()
    assert (ws / "moved/move.txt").read_text() == "a\nc\n"
    assert extra["count"] == 4 and "A docs/new.md" in text


def test_patch_tolerates_whitespace_and_reports_the_nearest_lines():
    write("w.py", "x = 1   \n\ty = 2\n")
    run("apply_patch", patch="*** Begin Patch\n*** Update File: w.py\n-x = 1\n+x = 3\n y = 2\n*** End Patch")
    assert (store.workspace() / "w.py").read_text() == "x = 3\n\ty = 2\n"
    with pytest.raises(tools.ToolError, match="最接近") as err:
        run("apply_patch", patch="*** Begin Patch\n*** Update File: w.py\n-x = 4\n+x = 5\n y = 2\n*** End Patch")
    assert "    1  x = 3" in str(err.value)


def test_a_failing_patch_changes_nothing():
    write("a.txt", "one\n")
    with pytest.raises(tools.ToolError, match="补丁没有应用"):
        run("apply_patch", patch="*** Begin Patch\n*** Add File: b.txt\n+new\n*** Update File: a.txt\n-missing\n+x\n*** End Patch")
    assert not (store.workspace() / "b.txt").exists()
    with pytest.raises(tools.ToolError, match="第一行"):
        run("apply_patch", patch="--- a/a.txt\n+++ b/a.txt")
    with pytest.raises(tools.ToolError, match="只能访问工作目录"):
        run("apply_patch", patch="*** Begin Patch\n*** Add File: ../x.txt\n+x\n*** End Patch")


def test_edit_file_matches_despite_indentation_slips():
    write("e.py", "def f():\n        return 1\n")
    text, _ = run("edit_file", path="e.py", old_text="def f():\n    return 1", new_text="def f():\n    return 2")
    assert "按行匹配" in text and (store.workspace() / "e.py").read_text() == "def f():\n    return 2\n"
    with pytest.raises(tools.ToolError, match="最接近"):
        run("edit_file", path="e.py", old_text="def f():\n    return 3", new_text="x")


def test_arguments_are_fitted_to_the_schema():
    tool = tools.TOOLS["read_file"]
    assert tools.check_args(tool, {"path": "a", "offset": "3", "limit": 10.0}) == {"path": "a", "offset": 3, "limit": 10}
    assert tools.check_args(tools.TOOLS["search_files"], {"query": "x", "regex": "true"})["regex"] is True
    steps = tools.check_args(tools.TOOLS["update_plan"], {"steps": '[{"text": "a", "status": "pending"}]'})["steps"]
    assert steps[0]["text"] == "a"
    with pytest.raises(tools.ToolError, match="缺少参数 path.*不认识的参数：file_path"):
        tools.check_args(tool, {"file_path": "a"})
    assert tools.check_args(tools.TOOLS["write_file"], {"path": "a", "content": ""})["content"] == ""


def test_unknown_tools_point_at_the_right_one():
    assert "run_command" in tools.unknown_tool("bash", ["read_file", "run_command"])
    assert "read_file" in tools.unknown_tool("read_fille", ["read_file", "run_command"])
    assert "没有开启" in tools.unknown_tool("web_search", ["read_file"])
