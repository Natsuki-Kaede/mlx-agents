"""The agent's tools. Each tool belongs to a permission group the chat switches on (web, image, files, exec;
core tools are always available) and may need the user's approval before it runs (writes and commands).

Handlers take (ctx, args) and return (text the model sees, fields added to the tool card). They raise ToolError
for failures the model should hear about. File and command tools are confined to the agent's workspace.
"""

import fnmatch
import ipaddress
import json
import math
import os
import re
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

import httpx
import trafilatura
from ddgs import DDGS
from PIL import Image

from backend import store

SEARCH_RESULTS = 6
PAGE_CHARS = 6000  # ~4k tokens of page text per read keeps the context within what a 16 GB Mac holds
MAX_PAGE_BYTES = 3_000_000
FILE_CHARS = 20000
SEARCH_MAX_RESULTS = 100
SEARCH_MAX_FILE_BYTES = 1_000_000
SEARCH_SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", "target", ".cache"}
COMMAND_OUTPUT_CHARS = 8000
COMMAND_TIMEOUT = 120
MAX_COMMAND_TIMEOUT = 600
# Proxies in fake-IP mode (Surge, Clash) resolve every public domain into this benchmarking range and connect
# to the real host by name, so it stands for "public" here rather than "private".
PROXY_FAKE_IP = ipaddress.ip_network("198.18.0.0/15")
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
ASPECTS = {"1:1": (1024, 1024), "3:4": (864, 1152), "4:3": (1152, 864), "9:16": (768, 1344), "16:9": (1344, 768)}
PLAN_STATUSES = {"pending", "in_progress", "done"}


class ToolError(Exception):
    """A tool failure reported back to the model as the tool's result, so it can recover or explain."""


class ToolContext(Protocol):
    engine: Any
    images: list[str]  # paths of images attached to the current message, for reference_index
    workspace: Path

    def cancelled(self) -> bool: ...
    def update(self, **fields) -> None: ...  # merge fields into this call's card and push it to the client
    def set_plan(self, steps: list[dict]) -> None: ...


@dataclass
class Tool:
    name: str
    group: str  # "core" | "web" | "image" | "files" | "exec": the chat switch that offers it
    description: str
    parameters: dict
    handler: Callable[[ToolContext, dict], tuple[str, dict]]
    approval: str | None = None  # "write" | "exec": asks the user first unless auto-approved for the chat
    preview: Callable[[ToolContext, dict], dict] | None = None  # what the approval card shows

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


def _params(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


def _str(args: dict, key: str, required: bool = True) -> str:
    value = args.get(key)
    if value is None or (required and not str(value).strip()):
        if required:
            raise ToolError(f"缺少参数 {key}")
        return ""
    return str(value)


# ----- web -----


def web_search(query: str) -> list[dict]:
    if not query.strip():
        raise ToolError("搜索词为空")
    try:
        hits = DDGS().text(query, max_results=SEARCH_RESULTS, backend="duckduckgo")
    except Exception as exc:  # network errors, rate limits, upstream format changes
        raise ToolError(f"搜索失败：{exc}") from exc
    return [{"title": h.get("title", ""), "url": h.get("href", ""), "snippet": h.get("body", "")} for h in hits]


def _check_public(url: str) -> None:
    """Refuse non-http(s) URLs and hosts that resolve to this machine or a private network."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ToolError("只能读取 http(s) 网址")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, None)}
    except socket.gaierror as exc:
        raise ToolError(f"无法解析域名 {parsed.hostname}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if ip in PROXY_FAKE_IP:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ToolError("不允许读取本机或内网地址")


def read_page(url: str) -> dict:
    _check_public(url)
    try:
        with httpx.Client(follow_redirects=False, timeout=15, headers={"User-Agent": USER_AGENT}) as client:
            # Follow redirects by hand so every hop gets the same public-address check.
            for _ in range(5):
                with client.stream("GET", url) as response:
                    if response.is_redirect:
                        url = str(response.url.join(response.headers["location"]))
                        _check_public(url)
                        continue
                    response.raise_for_status()
                    body = b""
                    for chunk in response.iter_bytes():
                        body += chunk
                        if len(body) > MAX_PAGE_BYTES:
                            break
                    content_type = response.headers.get("content-type", "")
                    break
            else:
                raise ToolError("重定向次数过多")
    except httpx.HTTPError as exc:
        raise ToolError(f"读取失败：{exc}") from exc
    if "html" in content_type or not content_type:
        text = trafilatura.extract(body.decode("utf-8", "replace"), url=url, favor_recall=True, include_tables=True)
        meta = trafilatura.extract_metadata(body.decode("utf-8", "replace"))
        title = meta.title if meta and meta.title else ""
    elif content_type.startswith("text/"):
        text, title = body.decode("utf-8", "replace"), ""
    else:
        raise ToolError(f"不支持的内容类型 {content_type.split(';')[0]}")
    if not text:
        raise ToolError("没能提取到正文")
    truncated = len(text) > PAGE_CHARS
    return {"url": url, "title": title, "text": text[:PAGE_CHARS], "chars": len(text), "truncated": truncated}


def _web_search(ctx: ToolContext, args: dict) -> tuple[str, dict]:
    hits = web_search(_str(args, "query"))
    text = "\n\n".join(f"[{i}] {h['title']}\n{h['url']}\n{h['snippet']}" for i, h in enumerate(hits, 1)) or "没有搜索结果"
    return text, {"results": hits}


def _read_page(ctx: ToolContext, args: dict) -> tuple[str, dict]:
    page = read_page(_str(args, "url"))
    text = f"标题：{page['title']}\n网址：{page['url']}\n\n{page['text']}" + ("\n\n（正文较长，已截断）" if page["truncated"] else "")
    return text, {k: page[k] for k in ("url", "title", "chars", "truncated")}


# ----- image -----


def nearest_aspect(path: str) -> str:
    """The preset aspect ratio closest to an image's shape."""
    with Image.open(path) as img:
        ratio = img.width / img.height
    return min(ASPECTS, key=lambda k: abs(math.log(ASPECTS[k][0] / ASPECTS[k][1] / ratio)))


def _reference_path(ctx: ToolContext, args: dict) -> str | None:
    if args.get("reference_index"):
        index = int(args["reference_index"])
        if not 1 <= index <= len(ctx.images):
            raise ToolError(f"没有第 {index} 张附图（本条消息附了 {len(ctx.images)} 张）")
        return ctx.images[index - 1]
    if args.get("reference_file"):
        name = str(args["reference_file"])
        candidates = [ctx.engine.outputs / name, ctx.engine.uploads / name] if "/" not in name else [_in_workspace(ctx, name)]
        for path in candidates:
            if path.resolve().is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                return str(path.resolve())
        raise ToolError(f"找不到底图 {name}")
    return None


def _generate_image(ctx: ToolContext, args: dict) -> tuple[str, dict]:
    prompt = _str(args, "prompt").strip()
    reference = _reference_path(ctx, args)
    strength = min(0.9, max(0.05, float(args.get("strength") or 0.1))) if reference else None
    aspect = str(args.get("aspect_ratio") or (nearest_aspect(reference) if reference else "1:1"))
    width, height = ASPECTS.get(aspect, ASPECTS["1:1"])
    steps = min(60, max(8, int(args.get("steps") or 40)))
    seed = int(args["seed"]) if args.get("seed") not in (None, "", -1) else None
    job = ctx.engine.generate_inline(
        {"prompt": prompt, "width": width, "height": height, "steps": steps, "seed": seed, "step_cache": 0,
         "init_image": reference, "image_strength": strength, "source": {"agent": True}, "model": None},
        on_start=lambda job_id: ctx.update(job=job_id),
        cancelled=ctx.cancelled,
    )
    if job.status != "done":
        raise ToolError(job.error or "生图失败")
    r = job.result
    card = {
        "image": f"/media/outputs/{r['file']}", "file": r["file"], "width": width, "height": height, "steps": steps,
        "seed": r["seed"], "seconds": r["seconds"], "reference": Path(reference).name if reference else None, "strength": strength,
    }
    return f"图片已生成并已展示给用户：文件 {r['file']}（{width}×{height}，{steps} 步，种子 {r['seed']}，用时 {r['seconds']} 秒）。", card


def _search_gallery(ctx: ToolContext, args: dict) -> tuple[str, dict]:
    words = [w for w in re.split(r"\s+", _str(args, "query", required=False).lower()) if w]
    hits = []
    for meta_file in sorted(ctx.engine.outputs.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        meta = json.loads(meta_file.read_text())
        prompt = str(meta.get("prompt", ""))
        if all(w in prompt.lower() for w in words):
            hits.append({"file": meta["file"], "prompt": prompt[:200], "created": meta.get("created", ""), "url": f"/media/outputs/{meta['file']}"})
        if len(hits) >= int(args.get("limit") or 8):
            break
    text = "\n".join(f"- {h['file']}（{h['created']}）：{h['prompt']}" for h in hits) or "作品库里没有匹配的图片"
    return text, {"results": [{"title": h["prompt"][:60], "url": h["url"], "snippet": h["file"]} for h in hits]}


# ----- files (workspace only) -----


def _in_workspace(ctx: ToolContext, path: str) -> Path:
    target = (ctx.workspace / path).resolve()
    if target != ctx.workspace and ctx.workspace not in target.parents:
        raise ToolError(f"只能访问工作目录 {ctx.workspace} 内的文件")
    return target


def _rel(ctx: ToolContext, path: Path) -> str:
    return str(path.relative_to(ctx.workspace)) or "."


def _list_dir(ctx: ToolContext, args: dict) -> tuple[str, dict]:
    target = _in_workspace(ctx, _str(args, "path", required=False) or ".")
    if not target.is_dir():
        raise ToolError(f"不是目录：{_rel(ctx, target)}")
    entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:200]
    lines = [f"{p.name}/" if p.is_dir() else f"{p.name}  ({p.stat().st_size} B)" for p in entries if not p.name.startswith(".")]
    return f"{_rel(ctx, target)}：\n" + ("\n".join(lines) or "（空目录）"), {"path": _rel(ctx, target), "count": len(lines)}


def _read_file(ctx: ToolContext, args: dict) -> tuple[str, dict]:
    target = _in_workspace(ctx, _str(args, "path"))
    if not target.is_file():
        raise ToolError(f"文件不存在：{_rel(ctx, target)}")
    raw = target.read_bytes()
    if b"\0" in raw[:4096]:
        raise ToolError("这是二进制文件，不能按文本读取")
    lines = raw.decode("utf-8", "replace").splitlines()
    start = max(1, int(args.get("offset") or 1))
    chunk = lines[start - 1 : start - 1 + int(args.get("limit") or 400)]
    text = "\n".join(f"{start + i:>5}  {line}" for i, line in enumerate(chunk))[:FILE_CHARS]
    more = start - 1 + len(chunk) < len(lines)
    note = f"\n（共 {len(lines)} 行，以上为第 {start}–{start + len(chunk) - 1} 行{'，可用 offset 继续读' if more else ''}）"
    return text + note, {"path": _rel(ctx, target), "lines": len(lines)}


def _walk(ctx: ToolContext, root: Path):
    """Files under `root`, skipping hidden folders and dependency or build output folders."""
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SEARCH_SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            yield Path(folder) / name


def _search_root(ctx: ToolContext, args: dict) -> Path:
    root = _in_workspace(ctx, _str(args, "path", required=False) or ".")
    if not root.exists():
        raise ToolError(f"路径不存在：{_rel(ctx, root)}")
    return root


def _find_files(ctx: ToolContext, args: dict) -> tuple[str, dict]:
    pattern = _str(args, "pattern")
    root = _search_root(ctx, args)
    limit = min(500, int(args.get("limit") or 200))
    # "*.py" matches by name anywhere; patterns with a slash ("src/**/*.ts") match the path from `path`.
    hits = []
    for file in _walk(ctx, root) if root.is_dir() else [root]:
        rel = file.relative_to(root)
        # "**/" may also match no folder at all, so "src/**/*.py" includes src/app.py.
        if (fnmatch.fnmatch(str(rel), pattern) or fnmatch.fnmatch(str(rel), pattern.replace("**/", ""))) if "/" in pattern else fnmatch.fnmatch(file.name, pattern):
            hits.append(_rel(ctx, file))
            if len(hits) >= limit:
                break
    more = "\n（结果已达上限，可缩小范围）" if len(hits) >= limit else ""
    text = ("\n".join(hits) or "没有匹配的文件") + more
    return text, {"path": _rel(ctx, root), "count": len(hits), "output": text[:3000]}


def _search_files(ctx: ToolContext, args: dict) -> tuple[str, dict]:
    query = _str(args, "query")
    root = _search_root(ctx, args)
    glob = _str(args, "glob", required=False)
    flags = 0 if args.get("case_sensitive") else re.IGNORECASE
    try:
        regex = re.compile(query if args.get("regex") else re.escape(query), flags)
    except re.error as exc:
        raise ToolError(f"正则表达式有误：{exc}") from exc
    limit = min(SEARCH_MAX_RESULTS, int(args.get("limit") or 50))
    hits, files_hit = [], set()
    for file in _walk(ctx, root) if root.is_dir() else [root]:
        if glob and not fnmatch.fnmatch(file.name, glob):
            continue
        if ctx.workspace not in file.resolve().parents:
            continue  # a symlink leading out of the workspace
        try:
            if file.stat().st_size > SEARCH_MAX_FILE_BYTES:
                continue
            raw = file.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:4096]:
            continue  # binary
        for number, line in enumerate(raw.decode("utf-8", "replace").splitlines(), 1):
            if regex.search(line):
                hits.append(f"{_rel(ctx, file)}:{number}: {line.strip()[:200]}")
                files_hit.add(file)
                if len(hits) >= limit:
                    break
        if len(hits) >= limit:
            break
    more = "\n（结果已达上限，可缩小范围或改用更具体的关键词）" if len(hits) >= limit else ""
    text = ("\n".join(hits) or "没有找到匹配内容") + more
    return text, {"path": _rel(ctx, root), "count": len(hits), "files": len(files_hit), "output": text[:3000]}


def _write_preview(ctx: ToolContext, args: dict) -> dict:
    target = _in_workspace(ctx, _str(args, "path"))
    content = _str(args, "content", required=False)
    exists = target.exists()
    return {"summary": f"{'覆盖' if exists else '新建'}文件 {_rel(ctx, target)}（{len(content)} 字符）", "detail": content[:3000]}


def _write_file(ctx: ToolContext, args: dict) -> tuple[str, dict]:
    target = _in_workspace(ctx, _str(args, "path"))
    content = _str(args, "content", required=False)
    existed = target.exists()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return f"已{'覆盖' if existed else '新建'} {_rel(ctx, target)}（{len(content)} 字符）", {"path": _rel(ctx, target), "chars": len(content)}


def _edit_target(ctx: ToolContext, args: dict) -> tuple[Path, str, str, str, int]:
    target = _in_workspace(ctx, _str(args, "path"))
    if not target.is_file():
        raise ToolError(f"文件不存在：{_rel(ctx, target)}")
    old, new = _str(args, "old_text"), _str(args, "new_text", required=False)
    text = target.read_text()
    count = text.count(old)
    if count == 0:
        raise ToolError("没有找到要替换的原文，请先 read_file 确认内容")
    if count > 1 and not args.get("replace_all"):
        raise ToolError(f"原文出现了 {count} 次，请提供更长、唯一的片段，或设置 replace_all")
    return target, text, old, new, count


def _edit_preview(ctx: ToolContext, args: dict) -> dict:
    target, _, old, new, count = _edit_target(ctx, args)
    return {"summary": f"修改 {_rel(ctx, target)}（替换 {count} 处）", "detail": f"- {old[:1500]}\n+ {new[:1500]}"}


def _edit_file(ctx: ToolContext, args: dict) -> tuple[str, dict]:
    target, text, old, new, count = _edit_target(ctx, args)
    target.write_text(text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1))
    return f"已修改 {_rel(ctx, target)}（替换 {count if args.get('replace_all') else 1} 处）", {"path": _rel(ctx, target)}


# ----- commands -----


def _command_preview(ctx: ToolContext, args: dict) -> dict:
    return {"summary": f"在工作目录 {ctx.workspace} 运行命令", "detail": _str(args, "command")}


def _run_command(ctx: ToolContext, args: dict) -> tuple[str, dict]:
    command = _str(args, "command")
    timeout = min(MAX_COMMAND_TIMEOUT, max(1, int(args.get("timeout") or COMMAND_TIMEOUT)))
    started = time.time()
    proc = subprocess.Popen(
        ["/bin/zsh", "-c", command], cwd=ctx.workspace, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    output = bytearray()
    os.set_blocking(proc.stdout.fileno(), False)
    reason = None
    while True:
        chunk = proc.stdout.read()
        if chunk:
            output.extend(chunk)
        if proc.poll() is not None:
            output.extend(proc.stdout.read() or b"")
            break
        if ctx.cancelled():
            reason = "已取消"
        elif time.time() - started > timeout:
            reason = f"超过 {timeout} 秒超时"
        if reason:
            os.killpg(proc.pid, signal.SIGKILL)  # the whole process group, so child processes stop too
            proc.wait()
            break
        time.sleep(0.1)
    text = output.decode("utf-8", "replace")
    if len(text) > COMMAND_OUTPUT_CHARS:
        half = COMMAND_OUTPUT_CHARS // 2
        text = f"{text[:half]}\n…（中间省略 {len(text) - COMMAND_OUTPUT_CHARS} 字符）…\n{text[-half:]}"
    seconds = round(time.time() - started, 1)
    status = reason or f"退出码 {proc.returncode}"
    return f"{status}，用时 {seconds} 秒。输出：\n{text or '（无输出）'}", {"exit_code": proc.returncode, "output": text[-3000:], "seconds": seconds, "stopped": reason}


# ----- core: plan, memory, history -----


def _update_plan(ctx: ToolContext, args: dict) -> tuple[str, dict]:
    steps = args.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ToolError("steps 需要是非空列表")
    clean = []
    for step in steps:
        text = str(step.get("text", "")).strip() if isinstance(step, dict) else str(step).strip()
        status = step.get("status", "pending") if isinstance(step, dict) else "pending"
        if text:
            clean.append({"text": text, "status": status if status in PLAN_STATUSES else "pending"})
    ctx.set_plan(clean)
    done = sum(s["status"] == "done" for s in clean)
    return f"计划已更新：{done}/{len(clean)} 步完成。", {"count": len(clean)}


def _remember(ctx: ToolContext, args: dict) -> tuple[str, dict]:
    item = store.remember(_str(args, "text"))
    return f"已记住（id {item['id']}）：{item['text']}", {"memory": item}


def _forget(ctx: ToolContext, args: dict) -> tuple[str, dict]:
    memory_id = _str(args, "id")
    if not store.forget(memory_id):
        raise ToolError(f"没有 id 为 {memory_id} 的记忆")
    return f"已删除记忆 {memory_id}", {"id": memory_id}


def _search_chats(ctx: ToolContext, args: dict) -> tuple[str, dict]:
    query = _str(args, "query").lower()
    hits = []
    for summary in store.list_chats():
        chat = store.load_chat(summary["id"]) or {}
        for turn in chat.get("turns", []):
            content = str(turn.get("content", ""))
            at = content.lower().find(query)
            if at >= 0 or query in summary["title"].lower():
                snippet = content[max(0, at - 60) : at + 140] if at >= 0 else content[:200]
                hits.append({"id": summary["id"], "title": summary["title"], "snippet": snippet.replace("\n", " ")})
                break
        if len(hits) >= 8:
            break
    text = "\n".join(f"- 《{h['title']}》（{h['id']}）：…{h['snippet']}…" for h in hits) or "历史对话里没有找到"
    return text, {"results": [{"title": h["title"], "url": "", "snippet": h["snippet"]} for h in hits]}


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool("update_plan", "core", "Write or update the step-by-step plan for a multi-step task, shown to the user as a checklist. Call it before starting a task with 3+ steps and again whenever a step starts or finishes.",
             _params({"steps": {"type": "array", "items": {"type": "object", "properties": {"text": {"type": "string"}, "status": {"type": "string", "enum": sorted(PLAN_STATUSES)}}, "required": ["text", "status"]}, "description": "All steps in order with their current status"}}, ["steps"]),
             _update_plan),
        Tool("remember", "core", "Save a durable fact about the user (preferences, recurring settings, background) to long-term memory, so later chats can use it. Only for things worth keeping across conversations.",
             _params({"text": {"type": "string", "description": "One concise fact, in the user's language"}}, ["text"]), _remember),
        Tool("forget", "core", "Delete a long-term memory by its id (ids are listed in the system prompt).",
             _params({"id": {"type": "string"}}, ["id"]), _forget),
        Tool("search_chats", "core", "Search earlier conversations by keyword; returns titles and snippets.",
             _params({"query": {"type": "string"}}, ["query"]), _search_chats),
        Tool("search_gallery", "core", "Search previously generated images by words in their prompts; returns file names usable as reference_file for generate_image.",
             _params({"query": {"type": "string", "description": "Words that must appear in the prompt; empty lists the newest"}, "limit": {"type": "integer"}}, []), _search_gallery),
        Tool("web_search", "web", "Search the web with DuckDuckGo. Returns titles, URLs and snippets. Use for current events, facts you are unsure of, or anything after your training data.",
             _params({"query": {"type": "string", "description": "Search keywords"}}, ["query"]), _web_search),
        Tool("read_page", "web", "Fetch a web page and return its main text (truncated). Use after web_search to read a promising result in full.",
             _params({"url": {"type": "string", "description": "http(s) URL to read"}}, ["url"]), _read_page),
        Tool("generate_image", "image", "Generate an image with the local image model. Takes several minutes. Write a detailed, concrete prompt (subject, scene, composition, lighting, style); if the user gives an exact prompt, use it verbatim.",
             _params({
                 "prompt": {"type": "string", "description": "Detailed image description"},
                 "aspect_ratio": {"type": "string", "enum": list(ASPECTS), "description": "Defaults to the reference image's shape, else 1:1"},
                 "reference_index": {"type": "integer", "description": "To re-create or edit an image attached to this message: its 1-based position among the attached images (image-to-image)"},
                 "reference_file": {"type": "string", "description": "Alternatively, a generated image's file name (from search_gallery) or a workspace path to start from"},
                 "strength": {"type": "number", "description": "With a reference: how much of it to keep, 0.05–0.9. The model keeps the reference's composition and lighting strongly: 0.05–0.15 for big changes (scene, style, time of day), 0.2–0.4 for moderate edits, 0.5+ only for touch-ups. Default 0.1"},
                 "steps": {"type": "integer", "description": "Denoising steps 8–60, default 40"},
                 "seed": {"type": "integer", "description": "Fixed seed to reproduce an image; omit for random"},
             }, ["prompt"]), _generate_image),
        Tool("list_dir", "files", "List files in a workspace directory.",
             _params({"path": {"type": "string", "description": "Path relative to the workspace, default ."}}, []), _list_dir),
        Tool("read_file", "files", "Read a text file in the workspace, with line numbers. Use offset/limit for long files.",
             _params({"path": {"type": "string"}, "offset": {"type": "integer", "description": "First line, 1-based"}, "limit": {"type": "integer", "description": "Max lines, default 400"}}, ["path"]),
             _read_file),
        Tool("find_files", "files", "Find files in the workspace by name pattern, e.g. \"*.py\" (any folder) or \"src/**/*.ts\" (path from `path`). Skips hidden, dependency and build folders.",
             _params({"pattern": {"type": "string", "description": "Glob pattern"}, "path": {"type": "string", "description": "Folder to search, relative to the workspace, default ."}, "limit": {"type": "integer"}}, ["pattern"]),
             _find_files),
        Tool("search_files", "files", "Search file contents in the workspace (like grep). Returns path:line: text for each matching line. Use it to locate code, definitions and usages before reading files.",
             _params({
                 "query": {"type": "string", "description": "Text to find, or a regular expression with regex=true"},
                 "path": {"type": "string", "description": "Folder or file to search, relative to the workspace, default ."},
                 "glob": {"type": "string", "description": "Only files whose name matches, e.g. \"*.py\""},
                 "regex": {"type": "boolean"},
                 "case_sensitive": {"type": "boolean"},
                 "limit": {"type": "integer", "description": f"Max matching lines, default 50, max {SEARCH_MAX_RESULTS}"},
             }, ["query"]),
             _search_files),
        Tool("write_file", "files", "Create or overwrite a text file in the workspace (the user approves it first).",
             _params({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]), _write_file, approval="write", preview=_write_preview),
        Tool("edit_file", "files", "Replace an exact snippet in a workspace file (the user approves it first). Read the file first; old_text must match exactly and be unique unless replace_all.",
             _params({"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}, "replace_all": {"type": "boolean"}}, ["path", "old_text", "new_text"]),
             _edit_file, approval="write", preview=_edit_preview),
        Tool("run_command", "exec", "Run a shell command (zsh) in the workspace directory and return its output (the user approves it first). Use for running scripts, tests, package tools, file conversions.",
             _params({"command": {"type": "string"}, "timeout": {"type": "integer", "description": f"Seconds, default {COMMAND_TIMEOUT}, max {MAX_COMMAND_TIMEOUT}"}}, ["command"]),
             _run_command, approval="exec", preview=_command_preview),
    ]
}


def available(groups: set[str]) -> list[Tool]:
    """Tools offered to the model for a chat with these switches on (core tools always)."""
    return [t for t in TOOLS.values() if t.group == "core" or t.group in groups]
