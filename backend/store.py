"""On-disk state: chats (data/chats/*.json), long-term memory (data/memory.json) and settings (data/settings.json).

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
        chats.append({"id": f.stem, "title": data["title"], "updated": data["updated"], "turns": len(data["turns"])})
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
