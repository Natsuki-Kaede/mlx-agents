"""MLX Agents backend API: agent runs (chat with tools, images, files and commands), uploads, image job progress,
models and downloads, the gallery, long-term memory and settings.

Served only by the desktop client (desktop/app.py), which runs it on a loopback port behind a token gate.
"""

import asyncio
import json
import time
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Literal

import psutil
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import mcp_servers, models, store
from backend.agent import Agent
from backend.engine import OUTPUTS, ROOT, UPLOADS, VIDEO_EXTS, Engine

OUTPUTS.mkdir(exist_ok=True)
UPLOADS.mkdir(exist_ok=True)
WEB_DIST = ROOT / "desktop" / "dist"  # the client UI, built from ui/
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".heic"}

engine = Engine()
agent = Agent(engine)
app = FastAPI(title="MLX Agents")
app.mount("/media/outputs", StaticFiles(directory=OUTPUTS), name="outputs")
app.mount("/media/uploads", StaticFiles(directory=UPLOADS), name="uploads")


class GenParams(BaseModel):
    max_tokens: int = Field(1024, ge=16, le=8192)
    temperature: float = Field(0.7, ge=0, le=2)
    repetition_penalty: float = Field(1.1, ge=1, le=2)
    top_p: float = Field(0.95, ge=0, le=1)
    top_k: int = Field(20, ge=0, le=200)


class Permissions(BaseModel):
    web: bool = False
    image: bool = True
    files: bool = False
    exec: bool = False
    mcp: bool = False
    agents: bool = False


class RunRequest(BaseModel):
    """A message for the agent; the switches, model and parameters come from the settings."""

    chat_id: str
    content: str = ""
    attachments: list[str] = []
    goal: bool = False  # the message sets a new goal for the chat


class Decision(BaseModel):
    tool_id: str
    decision: Literal["allow", "always", "deny"]


def upload_path(name: str) -> Path:
    path = (UPLOADS / name).resolve()
    if path.parent != UPLOADS.resolve() or not path.is_file():
        raise HTTPException(404, f"找不到上传文件 {name}")
    return path


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def checked_chat_id(chat_id: str) -> str:
    try:
        store.chat_path(chat_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return chat_id


GPU_UTILIZATION = re.compile(r'"Device Utilization %"=(\d+)')


def gpu_utilization() -> int | None:
    """GPU busy percentage as the GPU driver reports it (ioreg; no special permissions needed)."""
    try:
        out = subprocess.run(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = GPU_UTILIZATION.search(out)
    return int(match.group(1)) if match else None


@app.get("/api/status")
def status():
    # cpu_percent(None) measures since the previous call, i.e. across the client's polling interval.
    return engine.status() | {"runs": agent.active(), "cpu": round(psutil.cpu_percent(None)), "gpu": gpu_utilization()}


# ----- uploads -----


@app.post("/api/uploads")
def upload(file: UploadFile):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in IMAGE_EXTS | VIDEO_EXTS:
        raise HTTPException(415, f"不支持的文件类型 {suffix or '（无扩展名）'}")
    name = f"{uuid.uuid4().hex[:12]}{suffix}"
    with (UPLOADS / name).open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"id": name, "kind": "video" if suffix in VIDEO_EXTS else "image", "url": f"/media/uploads/{name}"}


class FromOutputRequest(BaseModel):
    file: str


@app.post("/api/uploads/from-output")
def upload_from_output(req: FromOutputRequest):
    """Copy a generated image into uploads so it can be attached to a new message."""
    source = (OUTPUTS / req.file).resolve()
    if source.parent != OUTPUTS.resolve() or not source.is_file() or source.suffix.lower() not in IMAGE_EXTS:
        raise HTTPException(404, f"找不到作品 {req.file}")
    name = f"{uuid.uuid4().hex[:12]}{source.suffix.lower()}"
    shutil.copyfile(source, UPLOADS / name)
    return {"id": name, "kind": "image", "url": f"/media/uploads/{name}"}


# ----- agent runs -----


@app.post("/api/runs")
def start_run(req: RunRequest):
    checked_chat_id(req.chat_id)
    if not req.content.strip() and not req.attachments:
        raise HTTPException(400, "消息为空")
    if agent.active_run(req.chat_id):
        raise HTTPException(409, "这段对话还有任务在进行")
    settings = store.settings()
    try:
        model = models.resolve("chat", settings.get("chat_model"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    attachments = []
    for name in req.attachments:
        path = upload_path(name)
        kind = "video" if path.suffix.lower() in VIDEO_EXTS else "image"
        attachments.append({"id": name, "kind": kind, "url": f"/media/uploads/{name}", "path": str(path)})
    if attachments and not model.vision:
        raise HTTPException(400, f"{model.id} 是纯文本模型，看不了图片或视频；请换一个视觉模型")
    content = req.content.strip() or "请描述这个内容。"
    request = req.model_dump() | {
        "attachments": attachments, "content": content, "model": model.id,
        "permissions": settings["permissions"], "params": settings["params"] | {"reasoning": settings["reasoning"]},
        "goal": goal_for_run(req.chat_id, content, req.goal),
        "goal_budget": {"rounds": settings["goal_max_rounds"], "minutes": settings["goal_max_minutes"]},
    }
    return agent.start(request).public()


def goal_for_run(chat_id: str, content: str, new: bool) -> dict | None:
    """The goal a run works on: a new one, or the chat's unfinished goal (a message then continues it)."""
    if new:
        return {"text": content, "status": "active", "rounds": 0, "seconds": 0, "created": time.time(), "updated": time.time(), "new": True}
    current = (store.load_chat(chat_id) or {}).get("goal")
    if current and current.get("status") in ("active", "waiting", "paused", "stalled"):
        return current | {"status": "active", "note": None, "question": None}
    return None


def get_run(run_id: str):
    run = agent.runs.get(run_id)
    if run is None:
        raise HTTPException(404, "任务不存在（客户端重启后不再保留）")
    return run


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, after: int = 0):
    """Replay a run's events from `after`, then stream new ones until it ends."""
    run = get_run(run_id)

    async def events():
        seq = after
        while True:
            batch = await asyncio.to_thread(run.events_after, seq, 15)
            for event in batch:
                yield sse(event)
            seq += len(batch)
            if run.finished and seq >= len(run.events):
                return
            if not batch:
                yield ": keep-alive\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    get_run(run_id).cancel()
    return {"id": run_id}


@app.post("/api/runs/{run_id}/approvals")
def approve(run_id: str, d: Decision):
    get_run(run_id).decide(d.tool_id, d.decision)
    return {"id": run_id}


@app.get("/api/chats/{chat_id}/run")
def chat_run(chat_id: str):
    run = agent.active_run(checked_chat_id(chat_id))
    return run.public() if run else None


# ----- image job progress (jobs run inside agent runs) -----


def get_job(job_id: str):
    job = engine.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return job


@app.get("/api/jobs/{job_id}")
def job_state(job_id: str):
    return get_job(job_id).public()


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str):
    job = get_job(job_id)

    async def events():
        last = None
        while True:
            state = job.public()
            snapshot = (state["status"], state["step"], state["phase"])
            if snapshot != last:
                last = snapshot
                yield sse(state)
            if state["status"] in ("done", "error"):
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(events(), media_type="text/event-stream")


# ----- chats -----


@app.get("/api/chats")
def list_chats():
    return store.list_chats()


@app.delete("/api/chats/{chat_id}/goal")
def clear_goal(chat_id: str):
    if agent.active_run(checked_chat_id(chat_id)):
        raise HTTPException(409, "目标还在运行，先停止再结束")
    store.update_chat(chat_id, lambda c: {k: v for k, v in c.items() if k != "goal"})
    return {"id": chat_id}


@app.get("/api/chats/{chat_id}")
def get_chat(chat_id: str):
    chat = store.load_chat(checked_chat_id(chat_id))
    if chat is None:
        raise HTTPException(404, "对话不存在")
    return {"id": chat_id, **chat}


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: str):
    store.delete_chat(checked_chat_id(chat_id))
    return {"id": chat_id}


# ----- MCP servers -----


@app.get("/api/mcp")
def get_mcp():
    return {"config": mcp_servers.load_config(), "status": mcp_servers.manager.status()}


@app.put("/api/mcp")
def put_mcp(config: dict):
    try:
        clean = mcp_servers.validate(config)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    mcp_servers.CONFIG_FILE.write_text(json.dumps(clean, ensure_ascii=False, indent=2))
    mcp_servers.manager.restart()
    return get_mcp()


@app.post("/api/mcp/connect")
def connect_mcp():
    """Connect every enabled server now (they otherwise connect on first use) and report each one."""
    return {"config": mcp_servers.load_config(), "status": mcp_servers.manager.connect()}


# ----- models -----


@app.get("/api/models")
def list_models():
    chat, image = models.default("chat"), models.default("image")
    return {
        "models": [m.public() for m in models.scan()],
        "default_chat": chat.id if chat else None,
        "default_image": image.id if image else None,
        "downloads": [d.public() for d in models.downloads.values()],
    }


class DownloadRequest(BaseModel):
    repo: str


@app.post("/api/models/download")
def download_model(req: DownloadRequest):
    try:
        return models.start_download(req.repo).public()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# ----- gallery, memory, settings -----


@app.get("/api/gallery")
def gallery():
    items = []
    for png in sorted(OUTPUTS.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True):
        sidecar = png.with_suffix(".json")
        meta = json.loads(sidecar.read_text()) if sidecar.exists() else {"file": png.name}
        meta["url"] = f"/media/outputs/{png.name}"
        items.append(meta)
    return items


class MemoryItem(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@app.get("/api/memory")
def list_memory():
    return store.memories()


@app.post("/api/memory")
def add_memory(item: MemoryItem):
    return store.remember(item.text)


@app.delete("/api/memory/{memory_id}")
def delete_memory(memory_id: str):
    if not store.forget(memory_id):
        raise HTTPException(404, "记忆不存在")
    return {"id": memory_id}


class SettingsPatch(BaseModel):
    workspace: str | None = None
    chat_model: str | None = None
    image_model: str | None = None
    permissions: Permissions | None = None
    auto_approve: bool | None = None
    reasoning: Literal["off", "low", "medium", "high"] | None = None
    params: GenParams | None = None
    instructions: str | None = Field(None, max_length=20000)
    goal_max_rounds: int | None = Field(None, ge=10, le=1000)
    goal_max_minutes: int | None = Field(None, ge=5, le=600)


@app.get("/api/settings")
def get_settings():
    return store.settings() | {"workspace_resolved": str(store.workspace())}


@app.put("/api/settings")
def put_settings(patch: SettingsPatch):
    if patch.workspace is not None:
        path = Path(patch.workspace).expanduser()
        if not path.is_absolute():
            raise HTTPException(400, "工作目录需要是绝对路径（可以用 ~ 开头）")
        if path.exists() and not path.is_dir():
            raise HTTPException(400, "这个路径不是文件夹")
        if not path.parent.exists():
            raise HTTPException(400, "上一级文件夹不存在")
    for kind in ("chat", "image"):
        model_id = getattr(patch, f"{kind}_model")
        if model_id is not None:
            try:
                models.resolve(kind, model_id)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
    store.update_settings({k: v for k, v in patch.model_dump().items() if v is not None})
    return get_settings()


if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        file = (WEB_DIST / path).resolve()
        if path and file.is_file() and WEB_DIST.resolve() in file.parents:
            return FileResponse(file)
        return FileResponse(WEB_DIST / "index.html")
