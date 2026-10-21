"""Agent runs: one user message → a model/tool loop on its own thread, owned by the backend. Model work is
queued on the engine's MLX thread; tools and approval waits run on the run's thread, so other chats keep going.

A run records its events (streamed text, tool cards, plan, approvals) so clients can attach, detach and
replay at any time; it saves the user turn when it starts and the assistant turn when it ends, so closing the
window or switching chats never loses work. Writes and commands wait for the user's approval unless the
auto-approve mode is on. Long tasks keep the context bounded by trimming older tool results and, when that is not
enough, summarising the earlier work.

In goal mode the run keeps going until the model calls finish_goal (refused while edits are unverified) or
ask_user, a budget runs out, or it stalls; the goal and its progress live in the chat, so it can be resumed.
"""

import json
import re
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from backend import mcp_servers, store
from backend.engine import Cancelled
from backend.tools import Tool, ToolError, available, check_args, unknown_tool

MAX_TOOL_ROUNDS = 30  # the round after the last one offers no tools, so the model has to answer
MAX_SUB_ROUNDS = 12  # a sub-agent's budget for one delegated task
SUB_AGENT_EXCLUDED = {"update_plan", "delegate_task"}  # the plan belongs to the main agent; no nested delegation
CONTEXT_CHARS = 40000  # beyond this, older tool results are trimmed (a 16 GB Mac cannot hold a long KV cache)
KEEP_FULL_TOOL_RESULTS = 3
TRIMMED_TOOL_CHARS = 500
APPROVAL_TIMEOUT = 1800
KEEP_FINISHED_RUNS = 50
MAX_FORMAT_RETRIES = 2  # malformed tool-call markup is sent back for another try this often in a row
MAX_REPEATS = 2  # an identical call (same tool and arguments, nothing changed since) runs at most this often
GOAL_NUDGE_LIMIT = 3  # replies in a row without a tool call before a goal pauses as stalled
PROJECT_DOC_CHARS = 8000
SUMMARY_KEEP = 6  # latest messages kept verbatim when earlier work is summarised
WRITE_TOOLS = {"write_file", "edit_file", "apply_patch"}

SYSTEM = """你是 MLX Agents，一个运行在用户自己 Mac 上的本地 AI agent，可以调用工具完成任务。当前时间：{now}。
工作方式：
- 用户要你做事时，直接调用工具去做并做完：不要只给出方案或说“我将会……”就停下；除非用户只是提问、讨论或要求先出方案。遇到障碍先自己想办法解决。
- 3 步以上的任务先用 update_plan 列出步骤，开始和完成每一步时更新状态；简单问题直接回答，不用列计划，也不要列只有一步的计划。
- 只调用真正需要的工具，读到结果后再决定下一步；不要编造工具没有返回的信息。工具出错时读懂错误信息，修正参数或换个办法再试，不要原样重复同一个调用。
- 用户表达了值得长期记住的偏好或背景时，用 remember 保存。
{rules}{instructions}{memory}"""
RULES = {
    "web": "- 需要最新信息、具体数据或你不确定的事实时，先 web_search 再 read_page 阅读最相关的 1–3 个结果，基于读到的内容回答，并用 Markdown 链接 [标题](网址) 标注来源。",
    "image": "- 画图用 generate_image：用户给了现成提示词就原样使用，否则写出具体详细的提示词（主体、场景、构图、光线、风格）；图片会自动展示给用户，不要在回答里贴图片地址。\n- 以附图或作品库里的图为基础复刻、改图时设置 reference_index 或 reference_file，并按改动大小设置 strength：这个模型会牢牢保留底图的构图和光线，换场景、时间、风格等大改动用 0.05–0.15，中等改动 0.2–0.4，只做微调才用 0.5 以上；改动特别大时也可以不用底图。",
    "files": "- 文件工具只能访问工作目录 {workspace}。处理文件前先了解现状：find_files 按文件名找、search_files 按内容搜、list_dir 看目录、read_file 读内容；新文件用 write_file，改一处用 edit_file（old_text 从 read_file 的结果里照抄，带几行上下文），改多处或多个文件用 apply_patch。写入前用户会审批，被拒绝就换个做法或询问用户。\n- 改代码前先弄清相关代码和约定，不要凭猜测修改；从根源解决问题，改动保持最小、贴合现有风格，不顺手修无关的问题。写入成功后不必再读一遍确认（失败时工具会报错）。\n- 改完运行测试、构建或相关命令验证，最后简要说明改了什么、怎么验证的。不要撤销不是你做的改动，不要执行 git reset --hard 这类破坏性命令，除非用户明确要求。",
    "exec": "- run_command 在工作目录 {workspace} 用 zsh 运行命令（脚本、测试、构建、git、命令行工具等），可用 workdir 指定子目录，运行前用户会审批；命令要非交互、能直接执行，长时间任务设置合适的 timeout。用户的简单请求能用一条命令完成时（比如查时间、看 git 状态），直接运行。\n- 看结果先看退出码：非 0 说明失败，读输出里的错误，修正后重新运行。",
    "mcp": "- 名字以 mcp__ 开头的工具来自用户接入的 MCP 服务，按工具说明传参；调用前用户会审批（除非已设为自动批准）。",
    "agents": "- 可以独立完成、需要大量搜索或阅读的子任务，用 delegate_task 交给子 agent：它在独立的上下文里工作，只把结果报告给你，能让你的上下文保持简洁。任务描述要完整具体（目标、范围、期望的产出），子 agent 看不到这段对话，但和你用同样的工具。它的报告里说没能确认的内容，不要当作事实转述。",
}
GOAL_RULES = """

## 目标模式
用户设定的目标：
{goal}{progress}
你在目标模式下自主工作，直到目标完成：
- 先弄清现状，用 update_plan 列出步骤，然后一步步执行；每一轮都调用工具推进，不要只描述打算做什么。
- 每做完一步都要验证（运行测试、构建、命令或读回文件）；出错就分析原因，修正后再验证。
- 全部完成并验证后调用 finish_goal，写清做了什么、怎么验证的。没有验证过的结果不算完成。
- 只有必须由用户决定或提供的信息才调用 ask_user，其余情况自己判断并继续。"""
GOAL_NUDGE = "目标还没有完成。请直接调用工具继续下一步；如果已经全部完成并验证，调用 finish_goal；如果必须由用户提供信息，调用 ask_user。"
FORMAT_RETRY = "上面的工具调用格式无法解析，没有执行。请严格按照工具调用格式重新调用，工具名和参数都要完整。"
SUMMARY_SYSTEM = """你负责为一个正在工作的 AI agent 压缩上下文。阅读下面的工作记录，写一份进度摘要，让 agent 只看摘要就能接着干：
- 已经完成的事，以及验证结果；
- 关键事实：涉及的文件路径、命令及输出要点、错误信息、做出的决定；
- 还没完成的事和下一步。
只写记录里确实发生过的事，不要猜测。用中文，分条列出，不超过 500 字。"""

SUB_AGENT_NOTE = """

你是主 agent 派来完成一个子任务的子 agent。专注完成任务，不要向用户提问。
只依据工具实际返回的内容作答：需要的工具不可用、调用出错或信息不足时，如实说明哪些没能完成、缺什么，不要猜测或编造内容。
完成后给出简洁完整的结果报告——主 agent 只能看到你的最终回复，看不到你的工具调用过程。"""


def system_prompt(groups: set[str], workspace: Path, goal: dict | None = None) -> str:
    rules = "\n".join(RULES[g].format(workspace=workspace) for g in RULES if g in groups)
    if goal is not None:
        progress = f"\n\n之前的进度：\n{goal['progress']}" if goal.get("progress") else ""
        rules += GOAL_RULES.format(goal=goal["text"], progress=progress)
    text = store.settings()["instructions"].strip()
    instructions = f"\n\n用户的全局指令，务必遵守：\n{text}" if text else ""
    # Like Codex, the workspace's own AGENTS.md describes the project (conventions, commands, checks).
    project = workspace / "AGENTS.md"
    if "files" in groups and project.is_file():
        instructions += f"\n\n工作目录里的 AGENTS.md（项目说明，务必遵守）：\n{project.read_text(errors='replace')[:PROJECT_DOC_CHARS]}"
    items = store.memories()
    memory = ("\n\n关于用户的长期记忆（过时或错误时用 forget 删除）：\n" + "\n".join(f"- [{m['id']}] {m['text']}" for m in items)) if items else ""
    return SYSTEM.format(now=datetime.now().strftime("%Y-%m-%d %H:%M"), rules=rules, instructions=instructions, memory=memory)


HISTORY_TOOL_TURNS = 6  # the latest assistant turns whose tool calls are replayed as real calls
HISTORY_ARG_CHARS = 300
HISTORY_RESULT_CHARS = 400
# Earlier versions noted tool use as text in the reply, and the model copied that note instead of calling tools.
OLD_TOOL_NOTE = re.compile(r"^（本轮调用了工具：[^\n]*）\s*", re.M)
DENIAL_TEXT = {"deny": "用户拒绝了这次操作。请换个做法，或先询问用户。", "cancelled": "任务已被用户停止。"}


def _short(value, limit: int):
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"…（共 {len(value)} 字符，已省略）"
    return value


def _past_result(card: dict) -> str:
    """What a replayed tool call returned, rebuilt from its card (the full text is not kept)."""
    if card.get("status") == "error":
        return f"工具出错：{card.get('error')}"
    if card.get("status") == "denied":
        return DENIAL_TEXT.get(card.get("decision") or "deny", "等待审批超时，操作没有执行。")
    if card.get("status") != "done":
        return "（这次调用没有执行完）"
    if card.get("output"):
        return str(card["output"])
    if card.get("results"):
        return "\n".join(f"- {r.get('title', '')} {r.get('url', '')}".strip() for r in card["results"])
    keys = ("path", "lines", "count", "chars", "title", "url", "file", "width", "height", "seed", "exit_code")
    facts = {k: card[k] for k in keys if card.get(k) not in (None, "")}
    if card.get("memory"):
        facts["memory"] = card["memory"].get("text")
    return "完成。" + (" ".join(f"{k}={v}" for k, v in facts.items()) if facts else "")


def history_for(turns: list[dict]) -> list[dict]:
    """Earlier turns as the model sees them. The latest turns' tool calls are replayed as real tool-call and
    tool-result messages (with arguments and results shortened), so the model sees how tools were actually
    called; older turns keep only their text. Older composer modes are summarised."""
    replay = {i for i, t in enumerate(turns) if t["role"] == "assistant"}
    replay = set(sorted(replay)[-HISTORY_TOOL_TURNS:])
    out = []
    for i, t in enumerate(turns):
        if t["role"] == "user":
            prefix = {"image": "（直接生图）提示词：", "describe": "（请根据附件反推生图提示词）"}.get(t.get("mode") or "", "")
            out.append({"role": "user", "content": prefix + t["content"]})
            continue
        content = OLD_TOOL_NOTE.sub("", t["content"]).strip()
        if t.get("describe"):
            content = f"反推得到的提示词：{t['describe'].get('prompt', '')}"
        if i in replay:
            for card in t.get("tools") or []:
                if card.get("parent") or card["name"] == "update_plan":
                    continue  # a sub-agent's own calls stay inside its report; plans are not worth the context
                args = {k: _short(v, HISTORY_ARG_CHARS) for k, v in (card.get("args") or {}).items()}
                out.append({"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": card["name"], "arguments": args}}]})
                out.append({"role": "tool", "content": _short(_past_result(card), HISTORY_RESULT_CHARS)})
        out.append({"role": "assistant", "content": content or "（没有文字回复）"})
    return out


def _chars(convo: list[dict]) -> int:
    return sum(len(str(m.get("content", ""))) + len(json.dumps(m.get("tool_calls", ""), ensure_ascii=False)) for m in convo)


def _render(message: dict) -> str:
    """One message of the work record, as text for the summariser."""
    if message.get("tool_calls"):
        calls = "；".join(f"{c['function']['name']}({json.dumps(c['function'].get('arguments'), ensure_ascii=False)[:300]})" for c in message["tool_calls"])
        return f"[agent 调用工具] {calls}" + (f"\n{message['content']}" if message.get("content") else "")
    label = {"tool": "工具结果", "assistant": "agent", "user": "用户/系统"}.get(message["role"], message["role"])
    return f"[{label}] {str(message.get('content', ''))[:1500]}"


def _trim(convo: list[dict]) -> None:
    """Trim all but the latest tool results once the conversation gets long."""
    if _chars(convo) <= CONTEXT_CHARS:
        return
    tool_indexes = [i for i, m in enumerate(convo) if m["role"] == "tool"]
    for i in tool_indexes[:-KEEP_FULL_TOOL_RESULTS]:
        content = str(convo[i]["content"])
        if len(content) > TRIMMED_TOOL_CHARS:
            convo[i]["content"] = content[:TRIMMED_TOOL_CHARS] + "\n…（较早的工具结果已截断，需要时可重新调用工具）"


@dataclass
class Run:
    id: str
    chat_id: str
    request: dict
    status: str = "queued"  # queued | running | done | error | cancelled
    events: list[dict] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    plan: list[dict] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    history: list[dict] = field(default_factory=list)
    phase: str = "starting"  # queued (waiting for the model) | running | tool | approval
    rounds: int = 0  # model calls made, sub-agents included
    goal_outcome: str | None = None  # done | waiting | stalled | budget, set during a goal run
    goal_result: dict = field(default_factory=dict)
    progress: str | None = None  # the latest summary of earlier work, kept with the goal
    tool_seq: int = 0
    wrote_at: int = 0  # tool_seq of the last file change, and of the last command run (to check verification)
    ran_at: int = 0
    call_counts: dict[str, int] = field(default_factory=dict)
    _cancel: bool = False
    _cond: threading.Condition = field(default_factory=threading.Condition)
    _decisions: dict[str, str] = field(default_factory=dict)

    def emit(self, event: dict) -> None:
        with self._cond:
            self.events.append(event | {"seq": len(self.events)})
            self._cond.notify_all()

    def events_after(self, after: int, timeout: float) -> list[dict]:
        """Events with seq >= after, waiting up to `timeout` for new ones (call from a worker thread)."""
        with self._cond:
            if len(self.events) <= after and not self.finished:
                self._cond.wait(timeout)
            return self.events[after:]

    @property
    def finished(self) -> bool:
        return self.status in ("done", "error", "cancelled")

    def cancelled(self) -> bool:
        return self._cancel

    def cancel(self) -> None:
        with self._cond:
            self._cancel = True
            self._cond.notify_all()

    def decide(self, tool_id: str, decision: str) -> None:
        with self._cond:
            self._decisions[tool_id] = decision
            self._cond.notify_all()

    def wait_decision(self, tool_id: str) -> str:
        deadline = time.time() + APPROVAL_TIMEOUT
        with self._cond:
            while tool_id not in self._decisions and not self._cancel and time.time() < deadline:
                self._cond.wait(1)
            return self._decisions.get(tool_id, "cancelled" if self._cancel else "timeout")

    def public(self) -> dict:
        return {"id": self.id, "chat_id": self.chat_id, "status": self.status, "phase": self.phase, "events": len(self.events), "created": self.created}


@dataclass
class Scope:
    """What one agent loop works with: the switched-on tool groups and the tools on offer, attachments,
    workspace, model and generation parameters. A sub-agent gets its own, narrower scope."""

    groups: set[str]
    tools: dict[str, Tool]
    images: list[str]
    videos: list[str]
    workspace: Path
    params: dict
    model: str | None
    parent: str | None = None  # for a sub-agent: the delegate_task card its tool cards belong to
    goal: dict | None = None  # set for the main loop of a goal run
    deadline: float | None = None


class _Context:
    """What a tool sees of the run: the engine, attached images, the workspace, its own card and the plan."""

    def __init__(self, agent: "Agent", run: Run, card: dict, scope: Scope) -> None:
        self.engine, self._agent, self._run, self._card, self._scope = agent.engine, agent, run, card, scope
        self.images, self.workspace = scope.images, scope.workspace

    def cancelled(self) -> bool:
        return self._run.cancelled()

    def update(self, **fields) -> None:
        self._card.update(fields)
        self._run.emit({"type": "tool", "tool": dict(self._card)})

    def set_plan(self, steps: list[dict]) -> None:
        self._run.plan = steps
        self._run.emit({"type": "plan", "plan": steps})

    def delegate(self, task: str, context: str) -> tuple[str, dict]:
        return self._agent._delegate(self._run, self, self._scope, task, context)

    def finish_goal(self, summary: str, verification: str) -> tuple[str, dict]:
        if self._scope.goal is None:
            raise ToolError("现在不是目标模式")
        run = self._run
        if run.wrote_at > run.ran_at and "exec" in self._scope.groups:
            raise ToolError("最后一次修改文件之后还没有运行任何命令验证。请先运行测试、构建或相关命令确认结果，再调用 finish_goal。")
        run.goal_outcome, run.goal_result = "done", {"summary": summary, "verification": verification}
        return "目标已标记为完成。", {"summary": summary, "verification": verification}

    def ask_user(self, question: str) -> tuple[str, dict]:
        if self._scope.goal is None:
            raise ToolError("现在不是目标模式，直接在回复里提问即可")
        self._run.goal_outcome, self._run.goal_result = "waiting", {"question": question}
        return "问题已转给用户，目标暂停等待回复。", {"question": question}


class Agent:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.runs: dict[str, Run] = {}

    def start(self, request: dict) -> Run:
        """Start a run on its own thread. `request`: chat_id, content, attachments ({id, kind, url, path}), and the
        settings it runs with: params (generation and reasoning), permissions (tool groups), model (chat model id)."""
        run = Run(id=uuid.uuid4().hex[:12], chat_id=request["chat_id"], request=request)
        # Save the user turn now, so a queued run already shows in the chat list and on reopening the chat.
        before = store.load_chat(run.chat_id) or {"turns": []}
        run.history = history_for(before["turns"])
        attachments = [{k: a[k] for k in ("id", "kind", "url")} for a in request.get("attachments") or []]
        goal = request.get("goal")
        mode = "goal" if goal is not None and goal.get("new") else "chat"
        user_turn = {"role": "user", "mode": mode, "content": request["content"], "attachments": attachments}

        def add_user(c: dict) -> dict:
            title = c.get("title") or (request["content"].replace("\n", " ").strip() or "图片对话")[:28]
            return c | {"title": title, "turns": [*c["turns"], user_turn]} | ({"goal": _public_goal(goal)} if goal is not None else {})

        store.update_chat(run.chat_id, add_user)
        self.runs[run.id] = run
        finished = [r for r in self.runs.values() if r.finished]
        for old in sorted(finished, key=lambda r: r.created)[:-KEEP_FINISHED_RUNS]:
            self.runs.pop(old.id, None)
        threading.Thread(target=self._execute, args=(run,), name=f"run-{run.id}", daemon=True).start()
        return run

    def active(self) -> int:
        return sum(not r.finished for r in self.runs.values())

    def active_run(self, chat_id: str) -> Run | None:
        live = [r for r in self.runs.values() if r.chat_id == chat_id and not r.finished]
        return max(live, key=lambda r: r.created) if live else None

    # ----- the loop (the run's own thread) -----

    def _execute(self, run: Run) -> None:
        req = run.request
        # Sub-agents are always offered; the model decides when a task is worth delegating.
        groups = {g for g, on in (req.get("permissions") or {}).items() if on} | {"agents"}
        attachments = req.get("attachments") or []
        run.status = "running"
        run.emit({"type": "status", "status": "running"})
        tools = available(groups)
        if "mcp" in groups:
            mcp_tools, failures = mcp_servers.manager.tools()
            tools += mcp_tools
            if failures:
                run.emit({"type": "status", "status": "running", "note": "有 MCP 服务没连上：" + "；".join(failures)})
        scope = Scope(
            groups=groups, tools={t.name: t for t in tools},
            images=[a["path"] for a in attachments if a["kind"] == "image"], videos=[a["path"] for a in attachments if a["kind"] == "video"],
            workspace=store.workspace(), params=req["params"], model=req.get("model"),
        )
        goal = req.get("goal")
        max_rounds = MAX_TOOL_ROUNDS
        if goal is not None:
            budget = req.get("goal_budget") or {}
            max_rounds = budget.get("rounds", 120)
            scope.goal, scope.deadline = goal, time.time() + 60 * budget.get("minutes", 60)
            scope.groups.add("goal")
            scope.tools |= {t.name: t for t in available({"goal"}) if t.group == "goal"}
            run.emit({"type": "goal", "goal": _public_goal(goal | {"status": "active"})})
        system = system_prompt(groups, scope.workspace, goal)
        convo = [{"role": "system", "content": system}, *run.history, {"role": "user", "content": req["content"]}]
        started = time.time()
        result, error = {"answer": "", "thinking": "", "tokens": {"thinking": 0, "answer": 0}, "stats": {}, "peak": 0.0, "load": 0.0}, None

        def emit_text(kind: str, text: str, counts: dict) -> None:
            run.emit({"type": kind, "text": text, "tokens": counts})

        try:
            self._loop(run, scope, convo, emit_text, max_rounds, result)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()

        run.status = "error" if error else "cancelled" if run.cancelled() else "done"
        tokens = result["tokens"]
        final_stats = {k: v for k, v in result["stats"].items() if k not in ("text", "thinking", "tool_calls", "tools_supported", "stopped")}
        final_stats |= {"seconds": round(time.time() - started, 1), "load_seconds": round(result["load"], 1), "peak_mlx_gb": result["peak"], "tokens": tokens}
        note = f"\n\n（出错：{error}）" if error else "\n\n（已停止）" if run.status == "cancelled" else ""
        assistant_turn = {
            "role": "assistant", "mode": "chat", "content": (result["answer"].strip() + note).strip(), "thinking": result["thinking"],
            "tokens": tokens, "tools": run.tools, "plan": run.plan, "stats": final_stats,
        }
        update = {}
        if goal is not None:
            update["goal"] = self._goal_after_run(run, goal, error, time.time() - started)
            run.emit({"type": "goal", "goal": update["goal"]})
        store.update_chat(run.chat_id, lambda c: c | {"turns": [*c["turns"], assistant_turn]} | update)
        run.emit({"type": "end", "status": run.status, "stats": final_stats, "error": error})

    def _goal_after_run(self, run: Run, goal: dict, error: str | None, seconds: float) -> dict:
        """The goal's state once a goal run ends: done, waiting for an answer, or paused (stalled, out of
        budget, stopped or failed) with a note on why."""
        outcome = "cancelled" if run.cancelled() else "error" if error else run.goal_outcome or "budget"
        status = {"done": "done", "waiting": "waiting", "stalled": "stalled"}.get(outcome, "paused")
        note = {
            "stalled": f"连续 {GOAL_NUDGE_LIMIT} 轮没有调用工具推进，已暂停",
            "budget": "本次运行的轮数或时长预算用完，已暂停",
            "cancelled": "已停止",
            "error": f"出错暂停：{error}",
        }.get(outcome)
        return _public_goal(goal | {
            "status": status, "note": note, "question": run.goal_result.get("question"),
            "summary": run.goal_result.get("summary"), "verification": run.goal_result.get("verification"),
            "rounds": goal.get("rounds", 0) + run.rounds, "seconds": round(goal.get("seconds", 0) + seconds),
            "progress": run.progress or goal.get("progress"), "updated": time.time(),
        })

    def _loop(self, run: Run, scope: Scope, convo: list[dict], emit_text, max_rounds: int, result: dict) -> None:
        """Alternate model rounds and tool calls until the model answers without calling a tool, the round
        budget runs out (the last round offers no tools) or the run is cancelled. Malformed tool-call markup is
        sent back for another try. In goal mode a reply without a tool call gets a nudge to continue, and the
        loop ends when the goal is finished, waits for the user, stalls or runs out of time. Accumulates into
        `result`."""
        schemas = [t.schema() for t in scope.tools.values()]
        anchor = len(convo)  # messages before this loop's own work: never summarised away
        main_goal = scope.goal is not None and scope.parent is None
        format_retries = idle = 0
        for round_ in range(max_rounds + 1):
            if run.cancelled():
                break  # also covers a run stopped while still queued, before any model is loaded
            if scope.deadline and time.time() > scope.deadline:
                break
            offered = schemas if round_ < max_rounds and schemas else None
            if round_ and result["answer"] and not result["answer"].endswith("\n\n"):
                emit_text("answer", "\n\n", result["tokens"])  # keep text from separate rounds in separate paragraphs
                result["answer"] += "\n\n"
            stats = self._generate(run, scope, convo, emit_text, offered, result["tokens"])
            if stats is None:
                break
            run.rounds += 1
            if offered and not stats["tools_supported"] and round_ == 0 and scope.parent is None:
                run.emit({"type": "status", "status": "running", "note": f"{stats['model']} 没有工具调用格式，这次只能直接回答"})
            result["stats"], result["tokens"], result["answer"] = stats, stats["tokens"], result["answer"] + stats["text"]
            result["thinking"] += ("\n\n" if result["thinking"] and stats["thinking"] else "") + stats["thinking"]
            result["peak"], result["load"] = max(result["peak"], stats["peak_mlx_gb"]), result["load"] + stats["load_seconds"]
            if stats["stopped"]:
                break
            calls = stats["tool_calls"]
            if not calls and offered and stats.get("tool_markup", "").strip() and format_retries < MAX_FORMAT_RETRIES:
                format_retries += 1
                convo.append({"role": "assistant", "content": stats["text"] + stats["tool_markup"]})
                convo.append({"role": "user", "content": FORMAT_RETRY})
                run.emit({"type": "status", "status": "running", "note": "模型的工具调用格式有误，已让它重试"})
                continue
            if not calls:
                if main_goal and offered and run.goal_outcome is None:
                    idle += 1
                    if idle >= GOAL_NUDGE_LIMIT:
                        run.goal_outcome = "stalled"
                        break
                    convo.append({"role": "assistant", "content": stats["text"] or "（没有回复）"})
                    convo.append({"role": "user", "content": GOAL_NUDGE})
                    continue
                break
            format_retries = idle = 0
            convo.append({"role": "assistant", "content": stats["text"], "tool_calls": [{"type": "function", "function": c} for c in calls]})
            for call in calls:
                convo.append({"role": "tool", "content": self._call_tool(run, call, scope)})
                if run.cancelled():
                    break
            if main_goal and run.goal_outcome in ("done", "waiting"):
                break
            self._compact(run, scope, convo, anchor)
            if scope.parent is None:
                run.phase = "running"

    def _generate(self, run: Run, scope: Scope, convo: list[dict], emit_text, tools, token_base) -> dict | None:
        """One model call on the MLX thread; None when the run was cancelled while it waited."""
        waited = []

        def queued() -> None:
            waited.append(True)
            run.phase = "queued"
            run.emit({"type": "status", "status": "queued"})

        def started() -> None:
            run.phase = "running"
            if waited:
                run.emit({"type": "status", "status": "running"})

        try:
            return self.engine.call(
                "chat", self.engine.stream, convo, scope.images, scope.videos, scope.params, emit_text,
                tools=tools, token_base=token_base, cancelled=run.cancelled, model_id=scope.model,
                abort=run.cancelled, on_queued=queued, on_started=started,
            )
        except Cancelled:
            return None

    def _compact(self, run: Run, scope: Scope, convo: list[dict], anchor: int) -> None:
        """Keep the context bounded: trim older tool results, and if that is not enough, replace the earlier
        part of this loop's work with a summary written by the model."""
        _trim(convo)
        if _chars(convo) <= CONTEXT_CHARS:
            return
        cut = len(convo) - SUMMARY_KEEP
        while cut > anchor and convo[cut]["role"] == "tool":
            cut -= 1  # keep tool results together with the call that produced them
        if cut - anchor < 4:
            return
        transcript = "\n\n".join(_render(m) for m in convo[anchor:cut])[-30000:]
        params = scope.params | {"reasoning": "off", "max_tokens": 900}
        prompt = [{"role": "system", "content": SUMMARY_SYSTEM}, {"role": "user", "content": transcript}]
        stats = self._generate(run, Scope(set(), {}, [], [], scope.workspace, params, scope.model, parent=scope.parent), prompt, None, None, None)
        summary = (stats or {}).get("text", "").strip()
        if not summary:
            return
        convo[anchor:cut] = [{"role": "user", "content": f"【较早的工作已压缩成进度摘要】\n{summary}"}]
        if scope.parent is None:
            run.progress = summary
            run.emit({"type": "status", "status": "running", "note": "上下文较长，已把较早的过程压缩成进度摘要"})

    def _delegate(self, run: Run, ctx: "_Context", scope: Scope, task: str, context: str) -> tuple[str, dict]:
        """Run a sub-agent on `task` in a fresh context with the same tools; its tool cards nest under this call's card."""
        groups = set(scope.groups) - {"agents", "goal"}  # no nested delegation; the goal is the main agent's
        sub = Scope(
            groups=groups, tools={n: t for n, t in scope.tools.items() if n not in SUB_AGENT_EXCLUDED and (t.group == "core" or t.group in groups)},
            images=[], videos=[], workspace=scope.workspace, params=scope.params,
            model=scope.model, parent=ctx._card["id"],
        )
        ctx.update(output="")
        convo = [
            {"role": "system", "content": system_prompt(groups, sub.workspace) + SUB_AGENT_NOTE},
            {"role": "user", "content": task + (f"\n\n背景信息：\n{context}" if context else "")},
        ]
        result = {"answer": "", "thinking": "", "tokens": {"thinking": 0, "answer": 0}, "stats": {}, "peak": 0.0, "load": 0.0}
        shown = [time.time()]

        def emit_text(kind: str, text: str, counts: dict) -> None:
            # Stream the sub-agent's report into its card, a few times a second.
            if kind == "answer" and time.time() - shown[0] > 0.4:
                shown[0] = time.time()
                ctx.update(output=result["answer"] + text)

        self._loop(run, sub, convo, emit_text, MAX_SUB_ROUNDS, result)
        answer = result["answer"].strip() or "（子 agent 没有给出结果）"
        ctx.update(output=answer)
        if run.cancelled():
            raise ToolError("任务已被用户停止")
        return f"子 agent 的结果：\n{answer}", {"output": answer, "tokens": result["tokens"]["answer"]}

    def _call_tool(self, run: Run, call: dict, scope: Scope) -> str:
        """Run one tool call (asking for approval when needed); returns the text the model sees."""
        name, args = call["name"], call.get("arguments") or {}
        card = {"id": uuid.uuid4().hex[:8], "name": name, "args": args, "status": "running"} | ({"parent": scope.parent} if scope.parent else {})
        run.tools.append(card)
        tool: Tool | None = scope.tools.get(name)
        ctx = _Context(self, run, card, scope)
        try:
            if tool is None:
                raise ToolError(unknown_tool(name, list(scope.tools)))
            args = card["args"] = check_args(tool, args)
            signature = name + json.dumps(args, sort_keys=True, ensure_ascii=False)
            count = run.call_counts[signature] = run.call_counts.get(signature, 0) + 1
            if count > MAX_REPEATS and name != "update_plan":
                raise ToolError(f"这个调用已经用完全相同的参数执行过 {count - 1} 次，期间没有任何修改，结果不会变。请换个做法，或根据已有结果继续。")
            # Read live, so switching the auto-approve mode on during a run applies to its next call.
            if tool.approval and not store.settings()["auto_approve"]:
                card.update(status="awaiting_approval", approval=tool.preview(ctx, args) if tool.preview else {"summary": name, "detail": ""})
                run.emit({"type": "tool", "tool": dict(card)})
                run.phase = "approval"
                decision = run.wait_decision(card["id"])
                run.phase = "tool"
                if decision == "always":
                    store.update_settings({"auto_approve": True})
                    run.emit({"type": "auto_approve", "auto_approve": True})
                elif decision != "allow":
                    card.update(status="denied", decision=decision)
                    run.emit({"type": "tool", "tool": dict(card)})
                    return DENIAL_TEXT.get(decision, "等待审批超时，操作没有执行。")
                card.update(status="running", decision=decision)
            run.emit({"type": "tool", "tool": dict(card)})
            if run.phase != "approval":
                run.phase = "tool"
            text, extra = tool.handler(ctx, args)
            card.update(extra, status="done")
            run.tool_seq += 1
            if name in WRITE_TOOLS or name == "run_command" or tool.group == "mcp":
                run.call_counts.clear()  # something may have changed, so repeating a read is legitimate again
            if name in WRITE_TOOLS:
                run.wrote_at = run.tool_seq
            elif name == "run_command":
                run.ran_at = run.tool_seq
        except ToolError as exc:
            card.update(status="error", error=str(exc))
            text = f"工具出错：{exc}"
        except Exception as exc:  # an unexpected failure inside a tool is reported, not fatal to the run
            traceback.print_exc()
            card.update(status="error", error=f"{type(exc).__name__}: {exc}")
            text = f"工具出错：{type(exc).__name__}: {exc}"
        run.emit({"type": "tool", "tool": dict(card)})
        return text


GOAL_FIELDS = ("text", "status", "note", "question", "summary", "verification", "rounds", "seconds", "progress", "created", "updated")


def _public_goal(goal: dict) -> dict:
    return {k: goal.get(k) for k in GOAL_FIELDS}
