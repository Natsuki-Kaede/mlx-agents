"""On-disk state: chats (data/chats/*.json), long-term memory (data/memory.json), agents (data/agents.json) and
settings (data/settings.json).

Writes go through a temp file and an atomic rename, so a crash never leaves a truncated file. A lock serialises
read-modify-write updates, since agent runs (MLX thread) and API requests both touch chats and memory.
"""

import json
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CHATS = DATA / "chats"
MEMORY_FILE = DATA / "memory.json"
SETTINGS_FILE = DATA / "settings.json"
AGENTS_FILE = DATA / "agents.json"
DEFAULT_WORKSPACE = Path.home() / "MLX Agents"
CHATS.mkdir(parents=True, exist_ok=True)

_lock = threading.RLock()


def _write(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    tmp.replace(path)


def _read(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


# ----- chats -----


def chat_path(chat_id: str) -> Path:
    if not chat_id.isalnum() or len(chat_id) > 32:
        raise ValueError("对话 id 不合法")
    return CHATS / f"{chat_id}.json"


def load_chat(chat_id: str) -> dict | None:
    with _lock:
        return _read(chat_path(chat_id), None)


def save_chat(chat_id: str, chat: dict) -> dict:
    with _lock:
        chat = chat | {"updated": time.time()}
        _write(chat_path(chat_id), chat)
        return chat


def update_chat(chat_id: str, fn) -> dict:
    """Read-modify-write a chat (created empty if missing) under the lock."""
    with _lock:
        chat = load_chat(chat_id) or {"title": "", "turns": [], "settings": {}}
        return save_chat(chat_id, fn(chat))


def list_chats() -> list[dict]:
    chats = []
    for f in sorted(CHATS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        data = json.loads(f.read_text())
        chats.append({
            "id": f.stem, "title": data["title"], "updated": data["updated"], "turns": len(data["turns"]),
            "agent": (data.get("settings") or {}).get("agent"),
        })
    return chats


def delete_chat(chat_id: str) -> None:
    chat_path(chat_id).unlink(missing_ok=True)


# ----- long-term memory -----


def memories() -> list[dict]:
    with _lock:
        return _read(MEMORY_FILE, [])


def remember(text: str) -> dict:
    with _lock:
        items = memories()
        item = {"id": uuid.uuid4().hex[:8], "text": text.strip(), "created": time.time()}
        _write(MEMORY_FILE, [*items, item])
        return item


def forget(memory_id: str) -> bool:
    with _lock:
        items = memories()
        kept = [m for m in items if m["id"] != memory_id]
        _write(MEMORY_FILE, kept)
        return len(kept) != len(items)


# ----- settings -----


def settings() -> dict:
    with _lock:
        return {"workspace": str(DEFAULT_WORKSPACE)} | _read(SETTINGS_FILE, {})


def update_settings(patch: dict) -> dict:
    with _lock:
        merged = settings() | patch
        _write(SETTINGS_FILE, merged)
        return merged


def workspace() -> Path:
    """The agent's working directory, created on first use."""
    path = Path(settings()["workspace"]).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


# ----- agents -----

DEFAULT_AGENT = "general"
# Seeded into data/agents.json on first use; afterwards the user's copies are the source of truth.
BUILTIN_AGENTS = [
    {
        "id": "general", "name": "通用助手", "icon": "✨", "model": None,
        "description": "问答、写作、看图、画图，什么都可以先问它",
        "instructions": "",
        "permissions": {"web": False, "image": True, "files": False, "exec": False},
    },
    {
        "id": "coder", "name": "编程助手", "icon": "💻", "model": None,
        "description": "读懂并修改工作目录里的代码，运行命令验证",
        "instructions": (
            "你是严谨的编程助手。\n"
            "- 动手前先用 find_files / search_files / read_file 弄清相关代码和约定，不要凭猜测修改。\n"
            "- 改动保持最小、贴合现有风格；用 edit_file 精确修改，新文件才用 write_file。\n"
            "- 改完运行测试、构建或相关命令验证，失败就读输出修正。\n"
            "- 最后简要说明改了什么、为什么、怎么验证的。"
        ),
        "permissions": {"web": False, "image": False, "files": True, "exec": True},
    },
    {
        "id": "researcher", "name": "研究员", "icon": "🔎", "model": None,
        "description": "联网查资料，多来源交叉验证并给出处",
        "instructions": (
            "你是细致的研究员。\n"
            "- 先拆解问题，再用 web_search 多角度检索，阅读 2–4 个可靠来源（官方文档、原始数据、权威媒体）。\n"
            "- 交叉核对关键事实，有分歧时说明各方说法。\n"
            "- 回答先给结论，再列要点和依据，每个关键事实用 [标题](网址) 标注来源。"
        ),
        "permissions": {"web": True, "image": False, "files": False, "exec": False},
    },
    {
        "id": "artist", "name": "画师", "icon": "🎨", "model": None,
        "description": "把想法变成详细的提示词并生成图片",
        "instructions": (
            "你是富有想象力的画师。\n"
            "- 先理解用户想要的主体、氛围和用途，必要时补全细节，写出具体的提示词（主体、场景、构图、镜头、光线、色彩、风格）。\n"
            "- 生成后简短说明画面构思，并给出 1–2 个可以继续调整的方向。"
        ),
        "permissions": {"web": False, "image": True, "files": False, "exec": False},
    },
]


def agents() -> list[dict]:
    with _lock:
        if not AGENTS_FILE.exists():
            _write(AGENTS_FILE, BUILTIN_AGENTS)
        return _read(AGENTS_FILE, BUILTIN_AGENTS)


def get_agent(agent_id: str | None) -> dict:
    """The agent with this id, falling back to the default one (also when it was deleted)."""
    items = agents()
    return next((a for a in items if a["id"] == agent_id), None) or next((a for a in items if a["id"] == DEFAULT_AGENT), BUILTIN_AGENTS[0])


def save_agent(profile: dict) -> dict:
    """Create (no id) or replace an agent."""
    with _lock:
        items = agents()
        if not profile.get("id"):
            profile = profile | {"id": uuid.uuid4().hex[:8]}
            items.append(profile)
        elif any(a["id"] == profile["id"] for a in items):
            items = [profile if a["id"] == profile["id"] else a for a in items]
        else:
            raise KeyError(profile["id"])
        _write(AGENTS_FILE, items)
        return profile


def delete_agent(agent_id: str) -> None:
    if agent_id == DEFAULT_AGENT:
        raise ValueError("通用助手是默认 agent，不能删除")
    with _lock:
        items = agents()
        kept = [a for a in items if a["id"] != agent_id]
        if len(kept) == len(items):
            raise KeyError(agent_id)
        _write(AGENTS_FILE, kept)
