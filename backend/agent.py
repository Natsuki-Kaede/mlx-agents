"""Agent runs: one user message → a model/tool loop on its own thread, owned by the backend. Model work is
queued on the engine's MLX thread; tools and approval waits run on the run's thread, so other chats keep going.

A run records its events (streamed text, tool cards, plan, approvals) so clients can attach, detach and
replay at any time; it saves the user turn when it starts and the assistant turn when it ends, so closing the
window or switching chats never loses work. Writes and commands wait for the user's approval unless the chat
auto-approves them. Long tasks keep the context bounded by trimming older tool results.
"""

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from backend import mcp_servers, store
from backend.engine import Cancelled
from backend.tools import TOOLS, Tool, ToolError, available

MAX_TOOL_ROUNDS = 30  # the round after the last one offers no tools, so the model has to answer
MAX_SUB_ROUNDS = 12  # a sub-agent's budget for one delegated task
SUB_AGENT_EXCLUDED = {"update_plan", "delegate_task"}  # the plan belongs to the main agent; no nested delegation
CONTEXT_CHARS = 40000  # beyond this, older tool results are trimmed (a 16 GB Mac cannot hold a long KV cache)
KEEP_FULL_TOOL_RESULTS = 3
TRIMMED_TOOL_CHARS = 500
APPROVAL_TIMEOUT = 1800
KEEP_FINISHED_RUNS = 50

SYSTEM = """你是「{name}」，一个运行在用户自己 Mac 上的本地 AI agent（MLX Agents），可以调用工具完成任务。当前时间：{now}。{role}
工作方式：
- 3 步以上的任务先用 update_plan 列出步骤，开始和完成每一步时更新状态；简单问题直接回答，不用列计划。
- 只调用真正需要的工具，读到结果后再决定下一步；不要编造工具没有返回的信息，出错时如实说明并换个办法。
- 用户表达了值得长期记住的偏好或背景时，用 remember 保存。
{rules}{memory}"""
RULES = {
    "web": "- 需要最新信息、具体数据或你不确定的事实时，先 web_search 再 read_page 阅读最相关的 1–3 个结果，基于读到的内容回答，并用 Markdown 链接 [标题](网址) 标注来源。",
    "image": "- 画图用 generate_image：用户给了现成提示词就原样使用，否则写出具体详细的提示词（主体、场景、构图、光线、风格）；图片会自动展示给用户，不要在回答里贴图片地址。\n- 以附图或作品库里的图为基础复刻、改图时设置 reference_index 或 reference_file，并按改动大小设置 strength：这个模型会牢牢保留底图的构图和光线，换场景、时间、风格等大改动用 0.05–0.15，中等改动 0.2–0.4，只做微调才用 0.5 以上；改动特别大时也可以不用底图。",
    "files": "- 文件工具只能访问工作目录 {workspace}。处理文件前先了解现状：find_files 按文件名找、search_files 按内容搜、list_dir 看目录、read_file 读内容；新文件用 write_file，修改用 edit_file（old_text 必须与原文完全一致）。写入前用户会审批，被拒绝就换个做法或询问用户。",
    "exec": "- run_command 在工作目录 {workspace} 用 zsh 运行命令（脚本、测试、命令行工具等），运行前用户会审批；命令要能直接执行、尽量非交互，长时间任务设置合适的 timeout。",
    "mcp": "- 名字以 mcp__ 开头的工具来自用户接入的 MCP 服务，按工具说明传参；调用前用户会审批（除非已设为自动批准）。",
    "agents": "- 可以独立完成、需要大量搜索或阅读的子任务，用 delegate_task 交给子 agent：它在独立的上下文里工作，只把结果报告给你，能让你的上下文保持简洁。任务描述要完整具体（目标、范围、期望的产出），子 agent 看不到这段对话，但和你用同样的工具。它的报告里说没能确认的内容，不要当作事实转述。可选的 agent（只决定子 agent 的角色指令）：{agents}。",
}
SUB_AGENT_NOTE = """

你是主 agent 派来完成一个子任务的子 agent。专注完成任务，不要向用户提问。
只依据工具实际返回的内容作答：需要的工具不可用、调用出错或信息不足时，如实说明哪些没能完成、缺什么，不要猜测或编造内容。
完成后给出简洁完整的结果报告——主 agent 只能看到你的最终回复，看不到你的工具调用过程。"""


def system_prompt(groups: set[str], workspace: Path, profile: dict | None = None) -> str:
    profile = profile or store.get_agent(None)
    role = f"\n\n你的角色和要求：\n{profile['instructions'].strip()}\n" if profile.get("instructions", "").strip() else ""
    agents = "、".join(f"{a['id']}（{a['name']}：{a['description']}）" for a in store.agents())
    rules = "\n".join(RULES[g].format(workspace=workspace, agents=agents) for g in RULES if g in groups)
    items = store.memories()
    memory = ("\n\n关于用户的长期记忆（过时或错误时用 forget 删除）：\n" + "\n".join(f"- [{m['id']}] {m['text']}" for m in items)) if items else ""
    return SYSTEM.format(name=profile["name"], role=role, now=datetime.now().strftime("%Y-%m-%d %H:%M"), rules=rules, memory=memory)


def _brief(tool: dict) -> str:
    args = tool.get("args") or {}
    key = next((args[k] for k in ("query", "url", "path", "command", "prompt", "task", "pattern") if args.get(k)), "")
    return f"{tool['name']}({str(key)[:60]})"


def history_for(turns: list[dict]) -> list[dict]:
    """Earlier turns as the model sees them: text, with tool use noted and older composer modes summarised."""
    out = []
    for t in turns:
        if t["role"] == "user":
            prefix = {"image": "（直接生图）提示词：", "describe": "（请根据附件反推生图提示词）"}.get(t.get("mode") or "", "")
            out.append({"role": "user", "content": prefix + t["content"]})
            continue
        content = t["content"]
        if t.get("describe"):
            content = f"反推得到的提示词：{t['describe'].get('prompt', '')}"
        tools = [x for x in t.get("tools") or [] if x.get("status") == "done" and not x.get("parent")]
        if tools:
            content = f"（本轮调用了工具：{'、'.join(_brief(x) for x in tools[:8])}）\n{content}"
        out.append({"role": "assistant", "content": content or "（没有文字回复）"})
    return out


def _compact(convo: list[dict]) -> None:
    """Trim all but the latest tool results once the conversation gets long."""
    if sum(len(str(m.get("content", ""))) for m in convo) <= CONTEXT_CHARS:
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
    auto_approve: set[str] = field(default_factory=set)
    created: float = field(default_factory=time.time)
    history: list[dict] = field(default_factory=list)
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
        return {"id": self.id, "chat_id": self.chat_id, "status": self.status, "events": len(self.events), "created": self.created}


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

    def delegate(self, task: str, agent_id: str | None, context: str) -> tuple[str, dict]:
        return self._agent._delegate(self._run, self, self._scope, task, agent_id, context)


class Agent:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.runs: dict[str, Run] = {}

    def start(self, request: dict) -> Run:
        """Queue a run on the MLX thread. `request`: chat_id, content, attachments ({id, kind, url, path}),
        params, permissions {web, image, files, exec}, auto_approve, system, model (chat model id), agent (agent id)."""
        run = Run(id=uuid.uuid4().hex[:12], chat_id=request["chat_id"], request=request, auto_approve=set(request.get("auto_approve") or []))
        # Save the user turn now, so a queued run already shows in the chat list and on reopening the chat.
        before = store.load_chat(run.chat_id) or {"turns": []}
        run.history = history_for(before["turns"])
        attachments = [{k: a[k] for k in ("id", "kind", "url")} for a in request.get("attachments") or []]
        user_turn = {"role": "user", "mode": "chat", "content": request["content"], "attachments": attachments}

        def add_user(c: dict) -> dict:
            title = c.get("title") or (request["content"].replace("\n", " ").strip() or "图片 / 视频对话")[:28]
            settings = (c.get("settings") or {}) | (request.get("permissions") or {}) | {"autoApprove": sorted(run.auto_approve), "model": request.get("model"), "agent": request.get("agent")}
            return c | {"title": title, "turns": [*c["turns"], user_turn], "settings": settings}

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
        groups = {g for g, on in (req.get("permissions") or {}).items() if on}
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
        system = system_prompt(groups, scope.workspace, store.get_agent(req.get("agent"))) + (f"\n\n用户的额外要求：\n{req['system']}" if req.get("system") else "")
        convo = [{"role": "system", "content": system}, *run.history, {"role": "user", "content": req["content"]}]
        started = time.time()
        result, error = {"answer": "", "thinking": "", "tokens": {"thinking": 0, "answer": 0}, "stats": {}, "peak": 0.0, "load": 0.0}, None

        def emit_text(kind: str, text: str, counts: dict) -> None:
            run.emit({"type": kind, "text": text, "tokens": counts})

        try:
            self._loop(run, scope, convo, emit_text, MAX_TOOL_ROUNDS, result)
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
        store.update_chat(run.chat_id, lambda c: c | {"turns": [*c["turns"], assistant_turn]})
        run.emit({"type": "end", "status": run.status, "stats": final_stats, "error": error})

    def _loop(self, run: Run, scope: Scope, convo: list[dict], emit_text, max_rounds: int, result: dict) -> None:
        """Alternate model rounds and tool calls until the model answers without calling a tool, the round
        budget runs out (the last round offers no tools) or the run is cancelled. Accumulates into `result`."""
        schemas = [t.schema() for t in scope.tools.values()]
        for round_ in range(max_rounds + 1):
            if run.cancelled():
                break  # also covers a run stopped while still queued, before any model is loaded
            offered = schemas if round_ < max_rounds and schemas else None
            if round_ and result["answer"] and not result["answer"].endswith("\n\n"):
                emit_text("answer", "\n\n", result["tokens"])  # keep text from separate rounds in separate paragraphs
                result["answer"] += "\n\n"
            waited = []

            def queued() -> None:
                waited.append(True)
                run.emit({"type": "status", "status": "queued"})

            try:
                stats = self.engine.call(
                    "chat", self.engine.stream, convo, scope.images, scope.videos, scope.params, emit_text,
                    tools=offered, token_base=result["tokens"], cancelled=run.cancelled, model_id=scope.model,
                    abort=run.cancelled, on_queued=queued, on_started=lambda: waited and run.emit({"type": "status", "status": "running"}),
                )
            except Cancelled:
                break
            if offered and not stats["tools_supported"] and round_ == 0 and scope.parent is None:
                run.emit({"type": "status", "status": "running", "note": f"{stats['model']} 没有工具调用格式，这次只能直接回答"})
            result["stats"], result["tokens"], result["answer"] = stats, stats["tokens"], result["answer"] + stats["text"]
            result["thinking"] += ("\n\n" if result["thinking"] and stats["thinking"] else "") + stats["thinking"]
            result["peak"], result["load"] = max(result["peak"], stats["peak_mlx_gb"]), result["load"] + stats["load_seconds"]
            if stats["stopped"] or not stats["tool_calls"]:
                break
            calls = stats["tool_calls"]
            convo.append({"role": "assistant", "content": stats["text"], "tool_calls": [{"type": "function", "function": c} for c in calls]})
            for call in calls:
                convo.append({"role": "tool", "content": self._call_tool(run, call, scope)})
                if run.cancelled():
                    break
            _compact(convo)

    def _delegate(self, run: Run, ctx: "_Context", scope: Scope, task: str, agent_id: str | None, context: str) -> tuple[str, dict]:
        """Run a sub-agent on `task` in a fresh context; its tool cards nest under this call's card."""
        if agent_id and not any(a["id"] == agent_id for a in store.agents()):
            raise ToolError(f"没有 id 为 {agent_id} 的 agent")
        profile = store.get_agent(agent_id or run.request.get("agent"))
        # The sub-agent works with the chat's switches. A named agent contributes its instructions and model only:
        # an agent's permissions are the starting switches of a chat, not limits on work done inside one.
        groups = set(scope.groups) - {"agents"}
        sub = Scope(
            groups=groups, tools={n: t for n, t in scope.tools.items() if n not in SUB_AGENT_EXCLUDED and (t.group == "core" or t.group in groups)},
            images=[], videos=[], workspace=scope.workspace, params=scope.params,
            model=(profile.get("model") if agent_id else None) or scope.model, parent=ctx._card["id"],
        )
        ctx.update(agent={"id": profile["id"], "name": profile["name"], "icon": profile["icon"]}, output="")
        convo = [
            {"role": "system", "content": system_prompt(groups, sub.workspace, profile) + SUB_AGENT_NOTE},
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
        return f"子 agent「{profile['name']}」的结果：\n{answer}", {"output": answer, "tokens": result["tokens"]["answer"]}

    def _call_tool(self, run: Run, call: dict, scope: Scope) -> str:
        """Run one tool call (asking for approval when needed); returns the text the model sees."""
        name, args = call["name"], call.get("arguments") or {}
        card = {"id": uuid.uuid4().hex[:8], "name": name, "args": args, "status": "running"} | ({"parent": scope.parent} if scope.parent else {})
        run.tools.append(card)
        tool: Tool | None = scope.tools.get(name)
        ctx = _Context(self, run, card, scope)
        try:
            if tool is None:
                raise ToolError(f"工具 {name} 在这段对话里没有开启" if name in TOOLS else f"没有叫 {name} 的工具")
            if tool.approval and tool.approval not in run.auto_approve:
                card.update(status="awaiting_approval", approval=tool.preview(ctx, args) if tool.preview else {"summary": name, "detail": ""})
                run.emit({"type": "tool", "tool": dict(card)})
                decision = run.wait_decision(card["id"])
                if decision == "always":
                    run.auto_approve.add(tool.approval)
                    store.update_chat(run.chat_id, lambda c: c | {"settings": (c.get("settings") or {}) | {"autoApprove": sorted(run.auto_approve)}})
                    run.emit({"type": "auto_approve", "auto_approve": sorted(run.auto_approve)})
                elif decision != "allow":
                    card.update(status="denied", decision=decision)
                    run.emit({"type": "tool", "tool": dict(card)})
                    return {"deny": "用户拒绝了这次操作。请换个做法，或先询问用户。", "cancelled": "任务已被用户停止。"}.get(decision, "等待审批超时，操作没有执行。")
                card.update(status="running", decision=decision)
            run.emit({"type": "tool", "tool": dict(card)})
            text, extra = tool.handler(ctx, args)
            card.update(extra, status="done")
        except ToolError as exc:
            card.update(status="error", error=str(exc))
            text = f"工具出错：{exc}"
        except Exception as exc:  # an unexpected failure inside a tool is reported, not fatal to the run
            traceback.print_exc()
            card.update(status="error", error=f"{type(exc).__name__}: {exc}")
            text = f"工具出错：{type(exc).__name__}: {exc}"
        run.emit({"type": "tool", "tool": dict(card)})
        return text
