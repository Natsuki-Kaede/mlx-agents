"""Shared fixtures: a throwaway data folder and workspace, and a scripted stand-in for the model.

No MLX model is loaded by these tests; FakeEngine replays a script of (text, tool calls[, raw markup]) replies.
"""

import threading
import time

import pytest

from backend import agent as agent_mod
from backend import mcp_servers, store


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    """Point every on-disk file of the app at a temporary folder."""
    monkeypatch.setattr(store, "CHATS", tmp_path / "chats")
    monkeypatch.setattr(store, "MEMORY_FILE", tmp_path / "memory.json")
    monkeypatch.setattr(store, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(mcp_servers, "CONFIG_FILE", tmp_path / "mcp.json")
    store.CHATS.mkdir()
    store.update_settings({"workspace": str(tmp_path / "ws")})
    return tmp_path


def call(name, **arguments):
    """One parsed tool call, as the engine returns it."""
    return [{"name": name, "arguments": arguments}]


class FakeEngine:
    """Plays back `script`: each model call takes the next (text, calls[, markup]) entry."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.prompts = []  # every conversation the model was shown
        self.offered = []  # tool names offered on each call

    def call(self, label, fn, *args, abort=None, on_queued=None, on_started=None, **kwargs):
        if on_started:
            on_started()
        return fn(*args, **kwargs)

    def stream(self, convo, images, videos, params, emit, tools=None, token_base=None, cancelled=None, model_id=None):
        self.prompts.append([dict(m) for m in convo])
        self.offered.append(sorted(t["function"]["name"] for t in tools or []))
        entry = self.script.pop(0)
        text, calls = entry[0], entry[1] or []
        markup = entry[2] if len(entry) > 2 else ""
        if emit:
            for ch in text:
                emit("answer", ch, {"thinking": 0, "answer": 1})
        tokens = dict(token_base or {"thinking": 0, "answer": 0})
        tokens["answer"] += len(text)
        return {
            "text": text, "thinking": "", "tool_calls": calls if tools else [], "tool_markup": markup if tools else "",
            "tools_supported": True, "model": "fake", "tokens": tokens, "stopped": bool(cancelled and cancelled()),
            "seconds": 0.0, "load_seconds": 0.0, "prompt_tokens": 1, "generation_tokens": 1, "generation_tps": 1.0, "peak_mlx_gb": 0.0,
        }


def wait_for(predicate, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def request(chat_id="c1", content="做事", permissions=None, goal=None, **extra):
    """A run request as main.start_run builds it."""
    return {
        "chat_id": chat_id, "content": content, "attachments": [], "model": None,
        "params": {"reasoning": "low", "max_tokens": 256, "temperature": 0.7, "repetition_penalty": 1.0},
        "permissions": permissions or {"files": True, "exec": True}, "goal": goal,
        "goal_budget": extra.pop("goal_budget", {"rounds": 20, "minutes": 5}), **extra,
    }


def new_goal(text):
    return {"text": text, "status": "active", "rounds": 0, "seconds": 0, "created": 0, "updated": 0, "new": True}


@pytest.fixture
def run_script():
    """Start a run against a fresh FakeEngine with `script` and wait for it to finish."""

    def run(script, wait=True, **req):
        engine = FakeEngine(script)
        agent = agent_mod.Agent(engine)
        r = agent.start(request(**req))
        if wait:
            assert wait_for(lambda: r.finished), "run did not finish"
        return r, engine, agent

    return run


@pytest.fixture
def approve_all():
    store.update_settings({"auto_approve": True})


__all__ = ["call", "wait_for", "request", "new_goal", "FakeEngine", "threading"]
