"""Model engine: one MLX thread that owns both models and runs every operation in order.

MLX streams belong to the thread that created them and weights are evaluated lazily, so loading and all
inference happen on this single thread. Only one model is resident at a time: the image model
(Qwen-Image-2.1 UC, ~5 GB once warm) and the chat/vision model (Qwen3.5-9B abliterated, ~6 GB) do not
both fit a 16 GB Mac with working memory, so switching unloads the other first.
"""

import gc
import json
import random
import threading
import time
import traceback
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import mlx.core as mx
from mflux.models.common.vae.tiling_config import TilingConfig
from mflux.models.qwen21.model.qwen21_text_encoder.qwen21_prompt_encoder import Qwen21PromptEncoder
from mflux.models.qwen21.model.qwen21_text_encoder.qwen21_text_encoder import Qwen21TextEncoder
from mflux.models.qwen21.qwen21_initializer import Qwen21Initializer
from mflux.models.qwen21.variants.txt2img.qwen_image_21 import QwenImage21
from mflux.models.qwen21.weights.qwen21_weight_definition import Qwen21WeightDefinition
from mflux.utils.exceptions import StopImageGenerationException
from mlx_vlm import load as load_vlm
from mlx_vlm import stream_generate
from mlx_vlm.prompt_utils import apply_chat_template

ROOT = Path(__file__).resolve().parents[1]
IMAGE_MODEL_PATH = ROOT / "models" / "qwen-image-2.1-uc-mflux-q4"
VLM_PATH = ROOT / "models" / "qwen3.5-9b-abliterated-mlx-4bit"
OUTPUTS = ROOT / "outputs"
UPLOADS = ROOT / "uploads"
PROMPT_CACHE_SIZE = 32
# Thinking-token budget per reasoning level; None leaves thinking bounded only by UNBOUNDED_REASONING_TOKENS.
REASONING_BUDGETS = {"low": 512, "medium": 2048, "high": None}
UNBOUNDED_REASONING_TOKENS = 8192
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}

Emit = Callable[[str, str, dict], None]  # (kind "thinking" | "answer", text, running token counts)


@dataclass
class Job:
    id: str
    kind: str
    params: dict
    status: str = "queued"  # queued | running | done | error
    step: int = 0
    total: int = 0
    phase: str = "排队中"
    result: dict | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None

    def public(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "step": self.step,
            "total": self.total,
            "phase": self.phase,
            "result": self.result,
            "error": self.error,
            "elapsed": round((self.finished or time.time()) - (self.started or self.created), 1),
        }


class _Progress:
    """mflux in-loop callback: writes denoising progress onto the current job and stops it when cancelled."""

    job: Job | None = None
    cancelled: Callable[[], bool] | None = None

    def call_in_loop(self, t, seed, prompt, latents, config, time_steps) -> None:
        if self.cancelled and self.cancelled():
            raise StopImageGenerationException("已取消")
        if self.job is not None:
            start = config.init_time_step or 0
            self.job.total = config.num_inference_steps - start
            self.job.step = t - start + 1


class TextEncoderOnly(Qwen21WeightDefinition):
    @staticmethod
    def get_components():
        return [c for c in Qwen21WeightDefinition.get_components() if c.name == "text_encoder"]


class Engine:
    outputs = OUTPUTS
    uploads = UPLOADS

    def __init__(self) -> None:
        self._thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx")
        self._models: dict = {"image": None, "vlm": None}
        self._progress = _Progress()
        self._pending = 0
        self._lock = threading.Lock()
        self.jobs: dict[str, Job] = {}
        self.current: str | None = None
        self._thread.submit(mx.set_cache_limit, 1000**3).result()

    # ----- scheduling -----

    def submit(self, label: str, fn, *args) -> Future:
        with self._lock:
            self._pending += 1

        def run():
            self.current = label
            try:
                return fn(*args)
            finally:
                self.current = None
                with self._lock:
                    self._pending -= 1

        return self._thread.submit(run)

    def status(self) -> dict:
        loaded = next((k for k, v in self._models.items() if v is not None), None)
        return {
            "loaded": loaded,
            "busy": self.current,
            "pending": self._pending,
            "active_gb": round(mx.get_active_memory() / 1e9, 2),
        }

    # ----- model residency (mlx thread only) -----

    def _unload(self, kind: str) -> None:
        if self._models[kind] is not None:
            self._models[kind] = None
            gc.collect()
            mx.clear_cache()

    def _image_model(self) -> QwenImage21:
        self._unload("vlm")
        if self._models["image"] is None:
            model = QwenImage21(model_path=str(IMAGE_MODEL_PATH))
            model.tiling_config = TilingConfig()
            model.callbacks.register(self._progress)
            self._models["image"] = model
        return self._models["image"]

    def _vlm(self):
        self._unload("image")
        if self._models["vlm"] is None:
            self._models["vlm"] = load_vlm(str(VLM_PATH))
        return self._models["vlm"]

    # ----- image generation (mlx thread only) -----

    def _encode(self, qwen: QwenImage21, prompt: str) -> None:
        """Fill the prompt cache, loading the text encoder only when needed and freeing it right after."""
        if prompt in qwen.prompt_cache:
            return
        if qwen.text_encoder is None:
            qwen.text_encoder = Qwen21TextEncoder()
            Qwen21Initializer.load_components(qwen, IMAGE_MODEL_PATH, TextEncoderOnly, None)
        embeds, mask = Qwen21PromptEncoder.encode_prompt(
            prompt=prompt,
            prompt_cache=qwen.prompt_cache,
            tokenizer=qwen.tokenizers["qwen21"],
            text_encoder=qwen.text_encoder,
        )
        mx.eval(embeds, mask)
        while len(qwen.prompt_cache) > PROMPT_CACHE_SIZE:
            qwen.prompt_cache.pop(next(iter(qwen.prompt_cache)))
        qwen.text_encoder = None
        gc.collect()
        mx.clear_cache()

    def generate_inline(self, params: dict, on_start: Callable[[str], None], cancelled: Callable[[], bool]) -> Job:
        """Run an image job on the calling (MLX) thread; the job is registered first so clients can follow it."""
        params = params | {"seed": params["seed"] if params.get("seed") is not None else random.randint(0, 2**31 - 1)}
        job = Job(id=uuid.uuid4().hex[:12], kind="image", params=params)
        self.jobs[job.id] = job
        on_start(job.id)
        self._progress.cancelled = cancelled
        try:
            self._run_image_job(job)
        finally:
            self._progress.cancelled = None
        return job

    def _run_image_job(self, job: Job) -> None:
        p = job.params
        job.status, job.started, job.phase = "running", time.time(), "加载模型与编码提示词"
        try:
            mx.reset_peak_memory()
            qwen = self._image_model()
            self._encode(qwen, p["prompt"])
            encode_seconds = time.time() - job.started
            job.phase = "生成中"
            self._progress.job = job
            image = qwen.generate_image(
                seed=p["seed"],
                prompt=p["prompt"],
                width=p["width"],
                height=p["height"],
                num_inference_steps=p["steps"],
                step_cache_ratio=p["step_cache"] or None,
                image_path=p.get("init_image"),
                image_strength=p.get("image_strength") if p.get("init_image") else None,
            )
            job.phase = "保存"
            name = f"img-{datetime.now():%Y%m%d-%H%M%S}-{p['seed']}"
            path = OUTPUTS / f"{name}.png"
            image.save(path=str(path))
            seconds = round(time.time() - job.started, 1)
            meta = {
                "file": path.name,
                "created": datetime.now().isoformat(timespec="seconds"),
                "seconds": seconds,
                "encode_seconds": round(encode_seconds, 1),
                "peak_mlx_gb": round(mx.get_peak_memory() / 1e9, 2),
                **{k: v for k, v in p.items() if k != "init_image"},
                "init_image": Path(p["init_image"]).name if p.get("init_image") else None,
            }
            (OUTPUTS / f"{name}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
            job.result = meta
            job.status, job.phase = "done", "完成"
        except StopImageGenerationException:
            job.status, job.error, job.phase = "error", "已取消", "已取消"
        except Exception as exc:  # surfaced to the client as the job's error
            job.status, job.error, job.phase = "error", f"{type(exc).__name__}: {exc}", "失败"
            traceback.print_exc()
        finally:
            self._progress.job = None
            job.finished = time.time()
            gc.collect()
            mx.clear_cache()

    # ----- chat / vision (mlx thread only) -----

    def stream(self, messages, images, videos, params, emit: Emit | None, tools=None, token_base=None, cancelled=None) -> dict:
        """Generate a reply. With reasoning on, the template pre-opens <think>, so the stream is thinking text up
        to </think>, then the answer, then any <tool_call> blocks; they come back separately, and thinking and
        answer text are also pushed to `emit` as they stream. Stops early when `cancelled()` turns true."""
        mx.reset_peak_memory()
        start = time.time()
        model, processor = self._vlm()
        load_seconds = time.time() - start
        level = params.get("reasoning", "off")
        reasoning = level != "off"
        template_kwargs = {"enable_thinking": reasoning}
        if tools:
            template_kwargs["tools"] = tools
        if videos:
            template_kwargs.update(video=videos, fps=params["fps"])
        prompt = apply_chat_template(processor, model.config, messages, num_images=len(images), **template_kwargs)
        max_tokens, extra = params["max_tokens"], {}
        if reasoning:
            budget = REASONING_BUDGETS[level]
            # max_tokens counts thinking tokens too, so the answer keeps its own allowance on top of the budget.
            max_tokens += budget or UNBOUNDED_REASONING_TOKENS
            extra = {"enable_thinking": True} | ({"thinking_budget": budget} if budget else {})
        split = _StreamRouter(reasoning)
        tokens = dict(token_base or {"thinking": 0, "answer": 0})  # running totals, sent with each piece for live counters
        last, stopped = None, False
        try:
            for chunk in stream_generate(
                model,
                processor,
                prompt,
                image=images or None,
                video=videos or None,
                max_tokens=max_tokens,
                temperature=params["temperature"],
                repetition_penalty=params["repetition_penalty"],
                fps=params["fps"],
                max_frames=params["max_frames"],
                **extra,
            ):
                last = chunk
                # Each chunk is one generated token; </think> itself counts toward thinking.
                tokens["thinking" if split.in_thinking else "answer"] += 1
                for kind, piece in split.feed(chunk.text):
                    if emit:
                        emit(kind, piece, dict(tokens))
                if cancelled and cancelled():
                    stopped = True
                    break
            for kind, piece in split.flush():
                if emit:
                    emit(kind, piece, dict(tokens))
        finally:
            gc.collect()
            mx.clear_cache()
        return {
            "text": split.answer,
            "thinking": split.thinking,
            "tool_text": split.tool_text,
            "tokens": tokens,
            "stopped": stopped,
            "seconds": round(time.time() - start, 1),
            "load_seconds": round(load_seconds, 1),
            "prompt_tokens": last.prompt_tokens if last else 0,
            "generation_tokens": last.generation_tokens if last else 0,
            "generation_tps": round(last.generation_tps, 1) if last else 0,
            "peak_mlx_gb": round(mx.get_peak_memory() / 1e9, 2),
        }


THINK_END = "</think>"
TOOL_START = "<tool_call>"


def _held_back(text: str, marker: str) -> int:
    """Length of the longest tail of `text` that could be the start of `marker` split across pieces."""
    return next((n for n in range(len(marker) - 1, 0, -1) if text.endswith(marker[:n])), 0)


class _StreamRouter:
    """Route streamed text to "thinking" until </think>, then to "answer" until <tool_call>; everything from
    <tool_call> on is collected (not streamed) for parsing. A tail that could be a split marker is held back."""

    def __init__(self, thinking: bool) -> None:
        self.state = "thinking" if thinking else "answer"
        self.pending = ""
        self.thinking = ""
        self.answer = ""
        self.tool_text = ""

    @property
    def in_thinking(self) -> bool:
        return self.state == "thinking"

    def feed(self, piece: str) -> list[tuple[str, str]]:
        if self.state == "tool":
            self.tool_text += piece
            return []
        self.pending += piece
        out = []
        if self.state == "thinking":
            if THINK_END not in self.pending:
                return self._release("thinking", _held_back(self.pending, THINK_END))
            before, self.pending = self.pending.split(THINK_END, 1)
            out += self._emit("thinking", before)
            self.state = "answer"
        if TOOL_START in self.pending:
            before, after = self.pending.split(TOOL_START, 1)
            self.pending, self.state, self.tool_text = "", "tool", TOOL_START + after
            return out + self._emit("answer", before)
        return out + self._release("answer", _held_back(self.pending, TOOL_START))

    def flush(self) -> list[tuple[str, str]]:
        # Generation ended mid-marker (e.g. max_tokens): whatever is held back belongs to the current state.
        if self.state == "tool":
            return []
        ready, self.pending = self.pending, ""
        return self._emit(self.state, ready)

    def _release(self, kind: str, keep: int) -> list[tuple[str, str]]:
        ready, self.pending = self.pending[: len(self.pending) - keep], self.pending[len(self.pending) - keep :]
        return self._emit(kind, ready)

    def _emit(self, kind: str, text: str) -> list[tuple[str, str]]:
        if not getattr(self, kind):
            text = text.lstrip("\n")
        setattr(self, kind, getattr(self, kind) + text)
        return [(kind, text)] if text else []
