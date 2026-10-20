"""Tools from Model Context Protocol servers, configured in data/mcp.json in the common format

    {"mcpServers": {"name": {"command": "npx", "args": [...], "env": {...}},      # local, over stdio
                    "name": {"url": "https://…/mcp", "headers": {...}},           # remote, streamable HTTP
                    ...: {..., "disabled": true}}}

Each server keeps one session open on a private asyncio loop (its own thread), so tool calls from the MLX
thread are cheap. Servers connect lazily, the first time a chat with MCP switched on needs their tools, and
are restarted whenever the configuration is saved. Local servers start in the agent's workspace.
"""

import asyncio
import json
import re
import threading
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field

from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from backend import store
from backend.tools import Tool, ToolContext, ToolError

CONFIG_FILE = store.DATA / "mcp.json"
CONNECT_TIMEOUT = 30
CALL_TIMEOUT = 300
RESULT_CHARS = 8000
SERVER_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {"mcpServers": {}}
    except json.JSONDecodeError:
        return {"mcpServers": {}}


def validate(config: dict) -> dict:
    """Check the shape of a configuration; raises ValueError with a message for the user."""
    servers = config.get("mcpServers") if isinstance(config, dict) else None
    if not isinstance(servers, dict):
        raise ValueError('配置需要是 {"mcpServers": {...}} 的形式')
    for name, spec in servers.items():
        if not SERVER_NAME.match(name):
            raise ValueError(f"服务名 {name!r} 只能用字母、数字、_ 和 -，最长 32 个字符")
        if not isinstance(spec, dict) or not (isinstance(spec.get("command"), str) or isinstance(spec.get("url"), str)):
            raise ValueError(f"{name}：需要 command（本地服务）或 url（远程服务）")
        if "args" in spec and not (isinstance(spec["args"], list) and all(isinstance(a, str) for a in spec["args"])):
            raise ValueError(f"{name}：args 需要是字符串列表")
        for key in ("env", "headers"):
            if key in spec and not (isinstance(spec[key], dict) and all(isinstance(v, str) for v in spec[key].values())):
                raise ValueError(f"{name}：{key} 需要是字符串到字符串的映射")
        if "url" in spec and not re.match(r"^https?://", spec["url"]):
            raise ValueError(f"{name}：url 需要以 http:// 或 https:// 开头")
    return {"mcpServers": servers}


def _tool_name(server: str, tool: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", f"mcp__{server}__{tool}")[:64]


@dataclass
class _Server:
    name: str
    spec: dict
    state: str = "connecting"  # connecting | connected | error | stopped
    error: str | None = None
    tools: list = field(default_factory=list)
    session: ClientSession | None = None
    ready: threading.Event = field(default_factory=threading.Event)
    stop: asyncio.Event | None = None
    closed: bool = False  # set by restart(); a session that comes up afterwards closes right away

    def public(self) -> dict:
        return {"name": self.name, "state": self.state, "error": self.error, "tools": [t.name for t in self.tools]}


class McpManager:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._servers: dict[str, _Server] = {}
        self._lock = threading.Lock()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            threading.Thread(target=self._loop.run_forever, name="mcp", daemon=True).start()
        return self._loop

    # ----- connections -----

    async def _serve(self, server: _Server) -> None:
        """Hold one session open until asked to stop (the transport's context must be entered and left in
        the same task)."""
        server.stop = asyncio.Event()
        if server.closed:
            return
        try:
            async with AsyncExitStack() as stack:
                spec = server.spec
                if "url" in spec:
                    import httpx2

                    client = await stack.enter_async_context(httpx2.AsyncClient(headers=spec.get("headers") or {}, timeout=CONNECT_TIMEOUT))
                    read, write = await stack.enter_async_context(streamable_http_client(spec["url"], http_client=client))
                else:
                    params = StdioServerParameters(command=spec["command"], args=spec.get("args") or [], env=spec.get("env") or None, cwd=str(store.workspace()))
                    read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await asyncio.wait_for(session.initialize(), CONNECT_TIMEOUT)
                listed = await asyncio.wait_for(session.list_tools(), CONNECT_TIMEOUT)
                if server.closed:
                    return
                server.session, server.tools, server.state = session, list(listed.tools), "connected"
                server.ready.set()
                await server.stop.wait()
        except BaseException as exc:  # includes cancellation and exception groups from the transport
            if not server.closed:
                server.state, server.error = "error", _describe(exc)
        finally:
            server.session = None
            if server.closed or server.state == "connected":
                server.state = "stopped"
            server.ready.set()

    def connect(self, timeout: float = CONNECT_TIMEOUT) -> list[dict]:
        """Start every enabled server that is not running yet and wait for them to connect (or fail)."""
        loop = self._ensure_loop()
        servers = load_config()["mcpServers"]
        with self._lock:
            for name, spec in servers.items():
                current = self._servers.get(name)
                if spec.get("disabled") or (current and current.state in ("connecting", "connected")):
                    continue
                server = self._servers[name] = _Server(name, spec)
                asyncio.run_coroutine_threadsafe(self._serve(server), loop)
            pending = [s for s in self._servers.values() if s.state == "connecting"]
        deadline = time.time() + timeout
        for server in pending:
            if not server.ready.wait(max(0.0, deadline - time.time())):
                server.state, server.error = "error", f"{timeout:.0f} 秒内没有连上"
        return self.status()

    def restart(self) -> None:
        """Stop all sessions; they reconnect with the current configuration when next needed."""
        with self._lock:
            servers, self._servers = list(self._servers.values()), {}
        for server in servers:
            server.closed = True
            if server.stop is not None and self._loop is not None:
                self._loop.call_soon_threadsafe(server.stop.set)

    def status(self) -> list[dict]:
        config = load_config()["mcpServers"]
        out = []
        for name, spec in config.items():
            server = self._servers.get(name)
            if spec.get("disabled"):
                out.append({"name": name, "state": "disabled", "error": None, "tools": []})
            else:
                out.append(server.public() if server else {"name": name, "state": "idle", "error": None, "tools": []})
        return out

    # ----- tools -----

    def tools(self) -> tuple[list[Tool], list[str]]:
        """Tools of all connected servers (connecting them first), and messages for servers that failed."""
        self.connect()
        tools, failures = [], []
        for server in list(self._servers.values()):
            if server.state != "connected":
                if server.state == "error":
                    failures.append(f"{server.name}：{server.error}")
                continue
            for tool in server.tools:
                tools.append(Tool(
                    name=_tool_name(server.name, tool.name),
                    group="mcp",
                    description=f"[MCP · {server.name}] {tool.description or tool.title or tool.name}",
                    parameters=tool.input_schema or {"type": "object", "properties": {}},
                    handler=lambda ctx, args, s=server.name, t=tool.name: self._call(ctx, s, t, args),
                    approval="mcp",
                    preview=lambda ctx, args, s=server.name, t=tool.name: {
                        "summary": f"调用 MCP 服务 {s} 的工具 {t}", "detail": json.dumps(args, ensure_ascii=False, indent=2)[:3000],
                    },
                ))
        return tools, failures

    def _call(self, ctx: ToolContext, server_name: str, tool: str, args: dict) -> tuple[str, dict]:
        server = self._servers.get(server_name)
        if server is None or server.session is None:
            raise ToolError(f"MCP 服务 {server_name} 已断开，请在设置里重新连接")
        future = asyncio.run_coroutine_threadsafe(server.session.call_tool(tool, args), self._loop)
        deadline = time.time() + CALL_TIMEOUT
        while not future.done():
            if ctx.cancelled() or time.time() > deadline:
                future.cancel()
                raise ToolError("已取消" if ctx.cancelled() else f"超过 {CALL_TIMEOUT} 秒没有返回")
            time.sleep(0.1)
        try:
            result = future.result()
        except Exception as exc:
            raise ToolError(f"调用失败：{_describe(exc)}") from exc
        text = _content_text(result.content, result.structured_content)
        if result.is_error:
            raise ToolError(text or "工具返回了错误")
        if len(text) > RESULT_CHARS:
            text = text[:RESULT_CHARS] + f"\n…（结果较长，已截断，共 {len(text)} 字符）"
        return text or "（工具没有返回内容）", {"server": server_name, "tool": tool, "output": text[:3000]}


def _content_text(content: list, structured) -> str:
    parts = []
    for item in content or []:
        kind = getattr(item, "type", "")
        if kind == "text":
            parts.append(item.text)
        elif kind == "resource_link":
            parts.append(f"[资源] {getattr(item, 'name', '')} {item.uri}")
        elif kind == "resource":
            resource = item.resource
            parts.append(getattr(resource, "text", None) or f"[资源] {resource.uri}")
        else:
            parts.append(f"[{kind or '未知'} 内容，无法以文本显示]")
    if not parts and structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False, indent=2))
    return "\n".join(parts).strip()


def _describe(exc: BaseException) -> str:
    """A readable message, unwrapping the exception groups that anyio task groups raise."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "连接超时"
    if isinstance(exc, FileNotFoundError):
        return f"找不到命令 {exc.filename or ''}".strip()
    return f"{type(exc).__name__}: {exc}"[:500]


manager = McpManager()
