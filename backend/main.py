"""MLX Agents backend API: agent runs (chat with tools, images, files and commands), uploads, image job progress,
models and downloads, the gallery, long-term memory and settings.

Served only by the desktop client (desktop/app.py), which runs it on a loopback port behind a token gate.
"""

import asyncio
import json
import shutil
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import models, store
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


class VlmParams(BaseModel):
    max_tokens: int = Field(1024, ge=16, le=8192)
    temperature: float = Field(0.7, ge=0, le=2)
    repetition_penalty: float = Field(1.1, ge=1, le=2)
    fps: float = Field(2.0, gt=0, le=8)
    max_frames: int = Field(32, ge=2, le=128)
    reasoning: Literal["off", "low", "medium", "high"] = "off"


class Permissions(BaseModel):
    web: bool = False
    image: bool = True
    files: bool = False
    exec: bool = False
    agents: bool = False


class RunRequest(BaseModel):
    chat_id: str
    content: str = ""
    attachments: list[str] = []
    params: VlmParams = VlmParams()
    permissions: Permissions = Permissions()
    auto_approve: list[Literal["write", "exec"]] = []
    system: str = ""
    model: str | None = None  # chat model id (folder under models/); None uses the default
    agent: str | None = None  # agent id; None uses the default agent


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


@app.get("/api/status")
def status():
    return engine.status()


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
    try:
        model = models.resolve("chat", req.model)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    attachments = []
    for name in req.attachments:
        path = upload_path(name)
        kind = "video" if path.suffix.lower() in VIDEO_EXTS else "image"
        attachments.append({"id": name, "kind": kind, "url": f"/media/uploads/{name}", "path": str(path)})
    if attachments and not model.vision:
        raise HTTPException(400, f"{model.id} 是纯文本模型，看不了图片或视频；请换一个视觉模型")
    request = req.model_dump() | {"attachments": attachments, "content": req.content.strip() or "请描述这个内容。"}
    request["permissions"] = req.permissions.model_dump()
    return agent.start(request).public()


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


@app.get("/api/chats/{chat_id}")
def get_chat(chat_id: str):
    chat = store.load_chat(checked_chat_id(chat_id))
    if chat is None:
        raise HTTPException(404, "对话不存在")
    return {"id": chat_id, **chat}


@app.patch("/api/chats/{chat_id}/settings")
def patch_chat_settings(chat_id: str, patch: dict):
    """Per-chat switches (web, image, files, exec, autoApprove); a chat without turns yet is created."""
    chat = store.update_chat(checked_chat_id(chat_id), lambda c: c | {"settings": (c.get("settings") or {}) | patch})
    return chat["settings"]


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: str):
    store.delete_chat(checked_chat_id(chat_id))
    return {"id": chat_id}


# ----- agents -----


class AgentProfile(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    icon: str = Field("🤖", min_length=1, max_length=8)
    description: str = Field("", max_length=200)
    instructions: str = Field("", max_length=8000)
    permissions: Permissions = Permissions()
    model: str | None = None


def checked_profile(profile: AgentProfile) -> dict:
    if profile.model:
        try:
            models.resolve("chat", profile.model)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return profile.model_dump()


@app.get("/api/agents")
def list_agents():
    return store.agents()


@app.post("/api/agents")
def create_agent(profile: AgentProfile):
    return store.save_agent(checked_profile(profile))


@app.put("/api/agents/{agent_id}")
def update_agent(agent_id: str, profile: AgentProfile):
    try:
        return store.save_agent(checked_profile(profile) | {"id": agent_id})
    except KeyError as exc:
        raise HTTPException(404, "agent 不存在") from exc


@app.delete("/api/agents/{agent_id}")
def delete_agent(agent_id: str):
    try:
        store.delete_agent(agent_id)
    except KeyError as exc:
        raise HTTPException(404, "agent 不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": agent_id}


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
