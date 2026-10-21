"""File tools stay inside the workspace and behave as the agent expects."""

import os

import pytest

from backend import store, tools


class Ctx:
    images: list = []
    engine = None

    def __init__(self):
        self.workspace = store.workspace()

    def cancelled(self):
        return False


def run(name, **args):
    return tools.TOOLS[name].handler(Ctx(), args)


def test_paths_outside_the_workspace_are_refused():
    for path in ("../../etc/passwd", "/tmp/x.txt"):
        with pytest.raises(tools.ToolError, match="只能访问工作目录"):
            run("read_file", path=path)
    with pytest.raises(tools.ToolError):
        run("write_file", path="/tmp/x.txt", content="x")


def test_write_read_and_exact_edit():
    run("write_file", path="d/hello.py", content="print('hi')\nx = 1\n")
    assert "x = 1" in run("read_file", path="d/hello.py")[0]
    run("edit_file", path="d/hello.py", old_text="x = 1", new_text="x = 2")
    assert (store.workspace() / "d/hello.py").read_text().endswith("x = 2\n")
    with pytest.raises(tools.ToolError, match="没有找到"):
        run("edit_file", path="d/hello.py", old_text="nope", new_text="y")


def test_search_skips_dependency_folders_binaries_and_outside_links(tmp_path):
    ws = store.workspace()
    (ws / "src").mkdir()
    (ws / "node_modules/x").mkdir(parents=True)
    (ws / "src/app.py").write_text("return Helper()\n")
    (ws / "node_modules/x/index.js").write_text("Helper")
    (ws / "blob.bin").write_bytes(b"\0Helper")
    secret = tmp_path / "secret.txt"
    secret.write_text("Helper TOKEN")
    os.symlink(secret, ws / "link.txt")
    text, extra = run("search_files", query="Helper")
    assert text.splitlines() == ["src/app.py:1: return Helper()"] and extra["files"] == 1
    assert run("find_files", pattern="src/**/*.py")[0] == "src/app.py"


def test_command_times_out():
    text, extra = run("run_command", command="sleep 5", timeout=1)
    assert "超时" in text and extra["stopped"]
