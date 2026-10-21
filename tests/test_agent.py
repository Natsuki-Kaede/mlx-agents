"""The agent loop with a scripted model: approvals, tools, history, sub-agents, loop hardening and goal mode."""

import json
import threading

from conftest import FakeEngine, call, new_goal, request, wait_for

from backend import agent as agent_mod
from backend import main
from backend import store


def tools_of(run):
    return [(t["name"], t["status"]) for t in run.tools]


# ----- approvals and basics -----


def test_write_waits_for_approval_and_deny_is_reported(run_script):
    r, engine, _ = run_script(
        [("", call("write_file", path="a.py", content="print(1)")), ("", call("run_command", command="python3 a.py")), ("完成", [])],
        wait=False,
    )
    assert wait_for(lambda: any(t["status"] == "awaiting_approval" for t in r.tools))
    assert r.phase == "approval" and r.public()["phase"] == "approval"
    r.decide(r.tools[0]["id"], "allow")
    assert wait_for(lambda: any(t["name"] == "run_command" and t["status"] == "awaiting_approval" for t in r.tools))
    r.decide(r.tools[1]["id"], "deny")
    assert wait_for(lambda: r.finished)
    assert tools_of(r) == [("write_file", "done"), ("run_command", "denied")]
    assert (store.workspace() / "a.py").exists()
    assert any("用户拒绝" in m["content"] for m in engine.prompts[-1] if m["role"] == "tool")


def test_always_turns_on_auto_approve(run_script):
    r, _, _ = run_script([("", call("run_command", command="echo hi")), ("", call("run_command", command="echo again")), ("好", [])], wait=False)
    assert wait_for(lambda: any(t["status"] == "awaiting_approval" for t in r.tools))
    r.decide(r.tools[0]["id"], "always")
    assert wait_for(lambda: r.finished)
    assert [t["output"].strip() for t in r.tools] == ["hi", "again"]
    assert store.settings()["auto_approve"] is True


def test_cancel_while_waiting_for_approval(run_script):
    r, _, _ = run_script([("", call("write_file", path="b.py", content="x"))], wait=False)
    assert wait_for(lambda: any(t["status"] == "awaiting_approval" for t in r.tools))
    r.cancel()
    assert wait_for(lambda: r.finished)
    assert r.status == "cancelled" and tools_of(r) == [("write_file", "denied")]
    assert store.load_chat("c1")["turns"][-1]["content"] == "（已停止）"


def test_switched_off_group_is_refused(run_script):
    r, _, _ = run_script([("", call("web_search", query="x")), ("没有联网", [])])
    assert r.tools[0]["status"] == "error" and "没有开启" in r.tools[0]["error"]


def test_history_replays_tool_calls_as_messages(run_script, approve_all):
    run_script([("", call("write_file", path="h.txt", content="x")), ("写好了", [])])
    history = agent_mod.history_for(store.load_chat("c1")["turns"])
    assert [m["role"] for m in history] == ["user", "assistant", "tool", "assistant"]
    assert history[1]["tool_calls"][0]["function"]["name"] == "write_file"
    assert "本轮调用了工具" not in str(history)


# ----- sub-agents -----


def test_sub_agent_gets_chat_tools_but_cannot_delegate_or_finish_goals(run_script, approve_all):
    (store.workspace()).mkdir(exist_ok=True)
    (store.workspace() / "a.py").write_text("print(1)\n")
    r, engine, _ = run_script(
        [("", call("delegate_task", task="读 a.py")), ("", call("read_file", path="a.py")), ("打印 1", []), ("好", [])],
        permissions={"files": True},  # sub-agents need no switch
    )
    names = [n for n, _ in tools_of(r)]
    assert names == ["delegate_task", "read_file"] and r.tools[1]["parent"] == r.tools[0]["id"]
    sub_offered = engine.offered[1]
    assert "read_file" in sub_offered and "delegate_task" not in sub_offered and "update_plan" not in sub_offered


def test_old_settings_map_to_the_new_reasoning_scale_and_drop_the_sub_agent_switch():
    store.SETTINGS_FILE.write_text(json.dumps({"reasoning": "low", "permissions": {"agents": False, "files": True}}))
    assert store.settings()["reasoning"] == "medium" and "agents" not in store.settings()["permissions"]
    for _ in range(3):  # saving records the new scale, so the level does not move again
        store.update_settings({"auto_approve": False})
        assert store.settings()["reasoning"] == "medium"
    store.update_settings({"reasoning": "high"})
    assert store.settings()["reasoning"] == "high"


# ----- loop hardening -----


def test_malformed_tool_call_is_sent_back_for_a_retry(run_script, approve_all):
    r, engine, _ = run_script([("我来写。", [], "<tool_call>\n<function=write_file>\n<parameter=pa"), ("", call("list_dir", path=".")), ("好了", [])])
    assert tools_of(r) == [("list_dir", "done")]
    assert any(m["role"] == "user" and m["content"].startswith("上面的工具调用格式") for m in engine.prompts[1])
    assert any("格式有误" in (e.get("note") or "") for e in r.events)


def test_identical_call_is_refused_the_third_time(run_script, approve_all):
    r, _, _ = run_script([("", call("list_dir", path=".")) for _ in range(3)] + [("结束", [])])
    assert [t["status"] for t in r.tools] == ["done", "done", "error"]
    assert "完全相同的参数" in r.tools[2]["error"]


def test_repeat_count_resets_after_a_change(run_script, approve_all):
    script = [
        ("", call("read_file", path="r.txt")), ("", call("read_file", path="r.txt")),
        ("", call("write_file", path="r.txt", content="new")), ("", call("read_file", path="r.txt")), ("好", []),
    ]
    store.workspace().mkdir(exist_ok=True)
    (store.workspace() / "r.txt").write_text("old")
    r, _, _ = run_script(script)
    assert [t["status"] for t in r.tools] == ["done", "done", "done", "done"]


def test_long_context_is_summarised(run_script, approve_all, monkeypatch):
    monkeypatch.setattr(agent_mod, "CONTEXT_CHARS", 6000)
    monkeypatch.setattr(agent_mod, "SUMMARY_KEEP", 2)
    store.workspace().mkdir(exist_ok=True)
    for i in range(3):
        (store.workspace() / f"big{i}.txt").write_text(chr(65 + i) * 9000)
    r, engine, _ = run_script(
        [("", call("read_file", path=f"big{i}.txt")) for i in range(3)]
        + [("- 读了 big0 和 big1", []), ("", call("finish_goal", summary="读完", verification="read_file 读回"))],
        permissions={"files": True}, goal=new_goal("读大文件"),
    )
    goal = store.load_chat("c1")["goal"]
    assert goal["status"] == "done" and goal["progress"].startswith("- 读了")
    assert any("较早的工作已压缩" in str(m["content"]) for m in engine.prompts[-1])
    assert engine.offered[3] == []  # the summary call offers no tools


# ----- goal mode -----


def test_finish_goal_requires_a_command_after_the_last_edit(run_script, approve_all):
    r, engine, _ = run_script(
        [
            ("", call("write_file", path="g.txt", content="v1")),
            ("", call("finish_goal", summary="写好了", verification="看过了")),
            ("", call("run_command", command="cat g.txt")),
            ("", call("finish_goal", summary="写好 g.txt", verification="cat 输出 v1")),
        ],
        goal=new_goal("写 g.txt 并验证"),
    )
    assert [s for _, s in tools_of(r)] == ["done", "error", "done", "done"]
    goal = store.load_chat("c1")["goal"]
    assert goal["status"] == "done" and goal["summary"] == "写好 g.txt" and goal["rounds"] == 4
    assert store.load_chat("c1")["turns"][0]["mode"] == "goal"
    assert "finish_goal" in engine.offered[0] and "ask_user" in engine.offered[0]


def test_goal_nudges_then_stalls(run_script):
    r, engine, _ = run_script([("我接下来会读文件。", []), ("好的。", []), ("嗯。", [])], goal=new_goal("做点事"))
    goal = store.load_chat("c1")["goal"]
    assert goal["status"] == "stalled" and "没有调用工具" in goal["note"]
    assert sum(1 for m in engine.prompts[-1] if m["role"] == "user" and m["content"].startswith("目标还没有完成")) == 2


def test_ask_user_waits_and_a_reply_continues_the_goal(run_script):
    run_script([("", call("ask_user", question="用 Python 还是 Go？"))], goal=new_goal("写个脚本"))
    goal = store.load_chat("c1")["goal"]
    assert goal["status"] == "waiting" and goal["question"] == "用 Python 还是 Go？"
    continued = main.goal_for_run("c1", "用 Python", False)
    assert continued["status"] == "active" and continued["text"] == "写个脚本" and not continued.get("new")


def test_finished_goal_is_not_continued(run_script, approve_all):
    run_script([("", call("finish_goal", summary="好了", verification="无需验证"))], goal=new_goal("打个招呼"), permissions={})
    assert store.load_chat("c1")["goal"]["status"] == "done"
    assert main.goal_for_run("c1", "聊点别的", False) is None


def test_goal_pauses_when_the_round_budget_runs_out(run_script, approve_all):
    run_script(
        [("", call("list_dir", path=".")), ("", call("find_files", pattern="*.txt")), ("到预算了", [])],
        goal=new_goal("慢慢做"), goal_budget={"rounds": 2, "minutes": 5},
    )
    goal = store.load_chat("c1")["goal"]
    assert goal["status"] == "paused" and "预算" in goal["note"]


def test_stopping_a_goal_pauses_it(run_script):
    r, _, _ = run_script([("", call("write_file", path="s.txt", content="x"))], goal=new_goal("写文件"), wait=False)
    assert wait_for(lambda: any(t["status"] == "awaiting_approval" for t in r.tools))
    r.cancel()
    assert wait_for(lambda: r.finished)
    goal = store.load_chat("c1")["goal"]
    assert goal["status"] == "paused" and goal["note"] == "已停止"


# ----- scheduling (real Engine queue, no model) -----


def test_waiting_run_does_not_block_another_chat_and_queued_cancel_skips_the_model(monkeypatch):
    from backend.engine import Engine

    scripts = {"A": [("", call("write_file", path="q.txt", content="x")), ("A 完成", [])], "B": [("B 回答", [])], "C": [("C", [])]}
    seen = []

    def scripted(self, convo, images, videos, params, emit, tools=None, token_base=None, cancelled=None, model_id=None):
        key = next(m["content"] for m in reversed(convo) if m["role"] == "user")
        seen.append(key)
        return FakeEngine([scripts[key].pop(0)]).stream(convo, images, videos, params, emit, tools, token_base, cancelled, model_id)

    monkeypatch.setattr(Engine, "stream", scripted)
    engine = Engine()
    agent = agent_mod.Agent(engine)
    a = agent.start(request("ca", "A"))
    assert wait_for(lambda: any(t["status"] == "awaiting_approval" for t in a.tools))
    b = agent.start(request("cb", "B"))
    assert wait_for(lambda: b.finished, 5) and b.status == "done" and not a.finished
    a.decide(a.tools[0]["id"], "allow")
    assert wait_for(lambda: a.finished)

    gate = threading.Event()
    engine.submit("block", gate.wait)
    c = agent.start(request("cc", "C"))
    assert wait_for(lambda: c.phase == "queued", 3)
    c.cancel()
    assert wait_for(lambda: c.finished, 3)
    gate.set()
    assert "C" not in seen and c.status == "cancelled"
