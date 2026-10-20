"""Model registry: what is in models/, which model a chat uses, and downloads from Hugging Face.

Each sub-folder of models/ is one model, recognised by its files:
- config.json: a chat model. With a vision tower (vision_config) it runs on mlx-vlm and can see images and
  video; otherwise on mlx-lm (text only). Weights must already be in MLX format (e.g. mlx-community repos).
- transformer/ and vae/: an image model for mflux. Only the Qwen-Image 2.1 family is wired up.
"""

import importlib.util
import json
import os
import re
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from backend import store

ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "models"
# Only what MLX loaders read; skips PyTorch/GGUF duplicates and custom code that MLX never executes.
DOWNLOAD_PATTERNS = ["*.json", "*.safetensors", "*.jinja", "*.txt", "*.model", "*.tiktoken"]
REPO_ID = re.compile(r"^[\w-][\w.-]*/[\w-][\w.-]*$")  # owner/name; no leading dot, so never "." or ".."


@dataclass
class ModelInfo:
    id: str  # folder name under models/
    kind: str  # "chat" | "image"
    runtime: str  # "mlx-lm" | "mlx-vlm" | "mflux"
    path: Path
    vision: bool = False
    model_type: str = ""
    bits: int | None = None
    size_gb: float = 0.0
    supported: bool = True
    note: str = ""

    def public(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k != "path"}


def _size_gb(path: Path) -> float:
    total = 0
    for root, _, files in os.walk(path, followlinks=True):
        total += sum((Path(root) / f).stat().st_size for f in files if (Path(root) / f).is_file())
    return round(total / 1e9, 2)


def _has_module(runtime: str, model_type: str) -> bool:
    """Whether the runtime has an implementation for this model_type (after its own aliases)."""
    package = runtime.replace("-", "_")
    remapping = importlib.import_module(f"{package}.utils").MODEL_REMAPPING
    name = remapping.get(model_type, model_type).replace("-", "_")
    return bool(name) and importlib.util.find_spec(f"{package}.models.{name}") is not None


def _inspect(path: Path) -> ModelInfo | None:
    if (path / "transformer").is_dir() and (path / "vae").is_dir():
        return ModelInfo(path.name, "image", "mflux", path, size_gb=_size_gb(path), model_type="qwen-image-2.1")
    config_file = path / "config.json"
    if not config_file.is_file():
        return None
    try:
        config = json.loads(config_file.read_text())
    except (OSError, json.JSONDecodeError):
        return ModelInfo(path.name, "chat", "mlx-lm", path, supported=False, note="config.json 无法读取")
    model_type = str(config.get("model_type", ""))
    vision = "vision_config" in config
    runtime = "mlx-vlm" if vision else "mlx-lm"
    quant = config.get("quantization") or config.get("quantization_config") or {}
    info = ModelInfo(path.name, "chat", runtime, path, vision=vision, model_type=model_type, bits=quant.get("bits"), size_gb=_size_gb(path))
    if not _has_module(runtime, model_type):
        info.supported, info.note = False, f"{runtime} 不支持模型类型 {model_type or '（未知）'}"
    elif not list(path.glob("*.safetensors")):
        info.supported, info.note = False, "缺少 safetensors 权重"
    return info


def scan() -> list[ModelInfo]:
    MODELS_DIR.mkdir(exist_ok=True)
    found = [_inspect(p) for p in sorted(MODELS_DIR.iterdir(), key=lambda p: p.name.lower()) if p.is_dir() and not p.name.startswith(".")]
    return [m for m in found if m is not None]


def get(model_id: str) -> ModelInfo | None:
    if not model_id or "/" in model_id or model_id.startswith("."):
        return None
    path = MODELS_DIR / model_id
    return _inspect(path) if path.is_dir() else None


def default(kind: str) -> ModelInfo | None:
    """The model set in settings, else the first usable one (vision chat models first)."""
    chosen = get(store.settings().get(f"{kind}_model") or "")
    if chosen and chosen.kind == kind and chosen.supported:
        return chosen
    usable = [m for m in scan() if m.kind == kind and m.supported]
    usable.sort(key=lambda m: not m.vision)
    return usable[0] if usable else None


def resolve(kind: str, model_id: str | None) -> ModelInfo:
    """The model a request names, or the default; raises ValueError with a message for the user."""
    info = get(model_id) if model_id else default(kind)
    label = "对话" if kind == "chat" else "生图"
    if info is None:
        raise ValueError(f"没有可用的{label}模型{f'：{model_id}' if model_id else ''}，请在设置里下载或选择模型")
    if info.kind != kind:
        raise ValueError(f"{info.id} 不是{label}模型")
    if not info.supported:
        raise ValueError(f"{info.id} 无法使用：{info.note}")
    return info


# ----- downloads -----


@dataclass
class Download:
    repo: str
    dest: Path
    status: str = "running"  # running | done | error
    total: int = 0
    error: str | None = None
    started: float = field(default_factory=time.time)

    def public(self) -> dict:
        done = sum(f.stat().st_size for f in self.dest.rglob("*") if f.is_file()) if self.dest.exists() else 0
        return {"repo": self.repo, "id": self.dest.name, "status": self.status, "total": self.total, "done": min(done, self.total) if self.total else done, "error": self.error}


downloads: dict[str, Download] = {}


def start_download(repo: str) -> Download:
    """Download an MLX model repo into models/<repo name> on a background thread."""
    repo = repo.strip().removeprefix("https://huggingface.co/").strip("/")
    if not REPO_ID.match(repo):
        raise ValueError("请填写 Hugging Face 仓库名，例如 mlx-community/Qwen3-8B-4bit")
    dest = MODELS_DIR / repo.split("/")[1]
    running = downloads.get(dest.name)
    if running and running.status == "running":
        raise ValueError(f"{dest.name} 正在下载")
    if dest.exists() and _inspect(dest):
        raise ValueError(f"models/{dest.name} 已存在")
    job = Download(repo, dest)
    downloads[dest.name] = job
    threading.Thread(target=_download, args=(job,), name=f"download-{dest.name}", daemon=True).start()
    return job


def _download(job: Download) -> None:
    from fnmatch import fnmatch

    from huggingface_hub import HfApi, constants, snapshot_download

    # Xet transfers have stalled at 0 bytes on some networks (seen behind a fake-IP proxy); plain HTTP
    # downloads are slower in theory but always progress. Read at call time, so this applies here.
    constants.HF_HUB_DISABLE_XET = True

    try:
        info = HfApi().model_info(job.repo, files_metadata=True)
        files = [s for s in info.siblings or [] if any(fnmatch(s.rfilename, p) for p in DOWNLOAD_PATTERNS)]
        job.total = sum(s.size or 0 for s in files)
        if not any(s.rfilename == "config.json" or s.rfilename.startswith("transformer/") for s in files):
            raise ValueError("这个仓库里没有可识别的模型文件")
        free = shutil.disk_usage(MODELS_DIR).free
        if job.total > free - 2e9:  # keep 2 GB spare for swap and outputs
            raise ValueError(f"磁盘空间不足：需要 {job.total / 1e9:.1f} GB，剩余 {free / 1e9:.1f} GB")
        snapshot_download(job.repo, local_dir=job.dest, allow_patterns=DOWNLOAD_PATTERNS)
        found = _inspect(job.dest)
        if found is None:
            raise ValueError("下载完成，但没有识别出模型")
        job.status = "done"
        if not found.supported:
            job.error = found.note
    except Exception as exc:  # shown in the settings panel
        job.status, job.error = "error", f"{type(exc).__name__}: {exc}" if not isinstance(exc, ValueError) else str(exc)
