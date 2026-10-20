"""Agent runs: one user message → a model/tool loop on the MLX thread, owned by the backend.

A run records its events (streamed text, tool cards, plan, approvals) so clients can attach, detach and
replay at any time; it saves the user turn when it starts and the assistant turn when it ends, so closing the
window or switching chats never loses work. Writes and commands wait for the user's approval unless the chat
auto-approves them. Long tasks keep the context bounded by trimming older tool results.
"""

import re
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from mlx_vlm.tools.parsers.qwen3_coder import parse_tool_call

from backend import store
from backend.tools import TOOLS, Tool, ToolError, available

MAX_TOOL_ROUNDS = 30  # the round after the last one offers no tools, so the model has to answer
CONTEXT_CHARS = 40000  # beyond this, older tool results are trimmed (a 16 GB Mac cannot hold a long KV cache)
KEEP_FULL_TOOL_RESULTS = 3
TRIMMED_TOOL_CHARS = 500
APPROVAL_TIMEOUT = 1800
KEEP_FINISHED_RUNS = 50

SYSTEM = """你是 MLX Agents 里的本地 AI 助手，运行在用户自己的 Mac 上，可以调用工具完成任务。当前时间：{now}。
工作方式：
- 3 步以上的任务先用 update_plan 列出步骤，开始和完成每一步时更新状态；简单问题直接回答，不用列计划。
- 只调用真正需要的工具，读到结果后再决定下一步；不要编造工具没有返回的信息，出错时如实说明并换个办法。
- 用户表达了值得长期记住的偏好或背景时，用 remember 保存。
{rules}{memory}"""
RULES = {
    "web": "- 需要最新信息、具体数据或你不确定的事实时，先 web_search 再 read_page 阅读最相关的 1–3 个结果，基于读到的内容回答，并用 Markdown 链接 [标题](网址) 标注来源。",
    "image": "- 画图用 generate_image：用户给了现成提示词就原样使用，否则写出具体详细的提示词（主体、场景、构图、光线、风格）；图片会自动展示给用户，不要在回答里贴图片地址。\n- 以附图或作品库里的图为基础复刻、改图时设置 reference_index 或 reference_file，并按改动大小设置 strength：这个模型会牢牢保留底图的构图和光线，换场景、时间、风格等大改动用 0.05–0.15，中等改动 0.2–0.4，只做微调才用 0.5 以上；改动特别大时也可以不用底图。",
    "files": "- 文件工具只能访问工作目录 {workspace}。处理文件前先 list_dir / read_file 了解现状；新文件用 write_file，修改用 edit_file（old_text 必须与原文完全一致）。写入前用户会审批，被拒绝就换个做法或询问用户。",
    "exec": "- run_command 在工作目录 {workspace} 用 zsh 运行命令（脚本、测试、命令行工具等），运行前用户会审批；命令要能直接执行、尽量非交互，长时间任务设置合适的 timeout。",
}


def system_prompt(groups: set[str], workspace: Path) -> str:
    rules = "\n".join(RULES[g].format(workspace=workspace) for g in ("web", "image", "files", "exec") if g in groups)
    items = store.memories()
    memory = ("\n\n关于用户的长期记忆（过时或错误时用 forget 删除）：\n" + "\n".join(f"- [{m['id']}] {m['text']}" for m in items)) if items else ""
    return SYSTEM.format(now=datetime.now().strftime("%Y-%m-%d %H:%M"), rules=rules, memory=memory)


def _brief(tool: dict) -> str:
    args = tool.get("args") or {}
    key = next((args[k] for k in ("query", "url", "path", "command", "prompt") if args.get(k)), "")
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
        tools = [x for x in t.get("tools") or [] if x.get("status") == "done"]
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


TOOL_BLOCK = re.compile(r"<tool_call>(.*?)(?:</tool_call>|$)", re.S)


def parse_tool_calls(text: str, schemas) -> list[dict]:
    calls = []
    for block in TOOL_BLOCK.findall(text):
        try:
            calls.append(parse_tool_call(block, schemas))
        except (ValueError, SyntaxError):
            continue  # a malformed call is dropped; the model then answers without that tool's result
    return calls


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


class _Context:
    """What a tool sees of the run: the engine, attached images, the workspace, its own card and the plan."""

    def __init__(self, agent: "Agent", run: Run, card: dict, images: list[str], workspace: Path) -> None:
        self.engine, self._run, self._card, self.images, self.workspace = agent.engine, run, card, images, workspace

    def cancelled(self) -> bool:
        return self._run.cancelled()

    def update(self, **fields) -> None:
        self._card.update(fields)
        self._run.emit({"type": "tool", "tool": dict(self._card)})

    def set_plan(self, steps: list[dict]) -> None:
        self._run.plan = steps
        self._run.emit({"type": "plan", "plan": steps})


class Agent:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.runs: dict[str, Run] = {}

    def start(self, request: dict) -> Run:
        """Queue a run on the MLX thread. `request`: chat_id, content, attachments ({id, kind, url, path}),
        params, permissions {web, image, files, exec}, auto_approve, system."""
        run = Run(id=uuid.uuid4().hex[:12], chat_id=request["chat_id"], request=request, auto_approve=set(request.get("auto_approve") or []))
        # Save the user turn now, so a queued run already shows in the chat list and on reopening the chat.
        before = store.load_chat(run.chat_id) or {"turns": []}
        run.history = history_for(before["turns"])
        attachments = [{k: a[k] for k in ("id", "kind", "url")} for a in request.get("attachments") or []]
        user_turn = {"role": "user", "mode": "chat", "content": request["content"], "attachments": attachments}

        def add_user(c: dict) -> dict:
            title = c.get("title") or (request["content"].replace("\n", " ").strip() or "图片 / 视频对话")[:28]
            settings = (c.get("settings") or {}) | (request.get("permissions") or {}) | {"autoApprove": sorted(run.auto_approve)}
            return c | {"title": title, "turns": [*c["turns"], user_turn], "settings": settings}

        store.update_chat(run.chat_id, add_user)
        self.runs[run.id] = run
        finished = [r for r in self.runs.values() if r.finished]
        for old in sorted(finished, key=lambda r: r.created)[:-KEEP_FINISHED_RUNS]:
            self.runs.pop(old.id, None)
        self.engine.submit("agent", self._execute, run)
        return run

    def active_run(self, chat_id: str) -> Run | None:
        live = [r for r in self.runs.values() if r.chat_id == chat_id and not r.finished]
        return max(live, key=lambda r: r.created) if live else None

    # ----- the loop (MLX thread) -----

    def _execute(self, run: Run) -> None:
        req = run.request
        groups = {g for g, on in (req.get("permissions") or {}).items() if on}
        attachments = req.get("attachments") or []
        images = [a["path"] for a in attachments if a["kind"] == "image"]
        videos = [a["path"] for a in attachments if a["kind"] == "video"]
        workspace = store.workspace()
        history = run.history
        run.status = "running"
        run.emit({"type": "status", "status": "running"})

        tools = available(groups)
        schemas = [t.schema() for t in tools]
        system = system_prompt(groups, workspace) + (f"\n\n用户的额外要求：\n{req['system']}" if req.get("system") else "")
        convo = [{"role": "system", "content": system}, *history, {"role": "user", "content": req["content"]}]
        answer, thinking, tokens, stats, peak, load = "", "", {"thinking": 0, "answer": 0}, {}, 0.0, 0.0
        started = time.time()
        error = None

        def emit_text(kind: str, text: str, counts: dict) -> None:
            run.emit({"type": kind, "text": text, "tokens": counts})

        try:
            for round_ in range(MAX_TOOL_ROUNDS + 1):
                if run.cancelled():
                    break  # also covers a run stopped while still queued, before any model is loaded
                offered = schemas if round_ < MAX_TOOL_ROUNDS else None
                if round_ and answer and not answer.endswith("\n\n"):
                    emit_text("answer", "\n\n", tokens)  # keep text from separate rounds in separate paragraphs
                    answer += "\n\n"
                stats = self.engine.stream(convo, images, videos, req["params"], emit_text, tools=offered, token_base=tokens, cancelled=run.cancelled)
                tokens, answer = stats["tokens"], answer + stats["text"]
                thinking += ("\n\n" if thinking and stats["thinking"] else "") + stats["thinking"]
                peak, load = max(peak, stats["peak_mlx_gb"]), load + stats["load_seconds"]
                if stats["stopped"]:
                    break
                calls = parse_tool_calls(stats["tool_text"], offered) if offered else []
                if not calls:
                    break
                convo.append({"role": "assistant", "content": stats["text"], "tool_calls": [{"type": "function", "function": c} for c in calls]})
                for call in calls:
                    convo.append({"role": "tool", "content": self._call_tool(run, call, groups, images, workspace)})
                    if run.cancelled():
                        break
                if run.cancelled():
                    break
                _compact(convo)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()

        run.status = "error" if error else "cancelled" if run.cancelled() else "done"
        final_stats = {k: v for k, v in stats.items() if k not in ("text", "thinking", "tool_text", "stopped")}
        final_stats |= {"seconds": round(time.time() - started, 1), "load_seconds": round(load, 1), "peak_mlx_gb": peak, "tokens": tokens}
        note = f"\n\n（出错：{error}）" if error else "\n\n（已停止）" if run.status == "cancelled" else ""
        assistant_turn = {
            "role": "assistant", "mode": "chat", "content": (answer.strip() + note).strip(), "thinking": thinking,
            "tokens": tokens, "tools": run.tools, "plan": run.plan, "stats": final_stats,
        }
        store.update_chat(run.chat_id, lambda c: c | {"turns": [*c["turns"], assistant_turn]})
        run.emit({"type": "end", "status": run.status, "stats": final_stats, "error": error})

    def _call_tool(self, run: Run, call: dict, groups: set[str], images: list[str], workspace: Path) -> str:
        """Run one tool call (asking for approval when needed); returns the text the model sees."""
        name, args = call["name"], call.get("arguments") or {}
        card = {"id": uuid.uuid4().hex[:8], "name": name, "args": args, "status": "running"}
        run.tools.append(card)
        tool: Tool | None = TOOLS.get(name)
        ctx = _Context(self, run, card, images, workspace)
        try:
            if tool is None or (tool.group != "core" and tool.group not in groups):
                raise ToolError(f"工具 {name} 在这段对话里没有开启")
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
