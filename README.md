# MLX Agents

Local LLM agents for Apple silicon. One input box hands your request to an agent that plans, calls tools and
reports back, with every model running on your Mac through [MLX](https://github.com/ml-explore/mlx). Nothing
leaves the machine unless you turn on web search or connect a remote MCP server.

The app is a native macOS window (pywebview) around a local FastAPI backend and a React UI. The UI is in Chinese.

## What it does

- **One general agent** with global switches for its tools, global instructions, and the workspace's own
  `AGENTS.md` read as project instructions (as in Codex).
- **Slash commands** in the input box: `/goal` sets a goal the agent works on autonomously until it calls it done
  (refused while file changes are unverified), asks you something, stalls or runs out of its budget; `/review`,
  `/init` (write `AGENTS.md`), `/auto` (auto-approve) and `/new`.
- **Tools**, with approval before anything that writes or runs:
  - web search (DuckDuckGo) and page reading
  - image generation and image-to-image (Qwen-Image 2.1 via [mflux](https://github.com/filipstrand/mflux))
  - files in a workspace folder: find, search contents, read, write, edit (tolerant of whitespace slips), and
    multi-file patches in Codex's `apply_patch` format
  - shell commands in the workspace (zsh, with timeouts)
  - [MCP](https://modelcontextprotocol.io) servers, local (stdio) or remote (streamable HTTP)
  - sub-agents: hand a self-contained task to an agent that works in a fresh context and reports back
- **Long tasks**: a visible plan the agent keeps up to date, up to 30 tool rounds per message, stop at any time,
  and older tool output trimmed so the context stays within what a 16 GB Mac can hold.
- **Runs live in the backend**: close the window or switch chats and the task keeps going; reopening the chat
  reattaches to it. A run waiting for your approval does not hold up other chats.
- **Long-term memory** the agent can add to (and you can edit), shared by all chats.
- **Model switching**: pick a model per chat or per agent. Text models run on
  [mlx-lm](https://github.com/ml-explore/mlx-lm), vision models (images and video) on
  [mlx-vlm](https://github.com/Blaizzy/mlx-vlm). Thinking and tool-call formats are detected per model, so tool
  calling works with Qwen, Mistral, GLM, Kimi, Gemma and other families the runtimes support.
- **Reasoning levels** (off / low / medium / high) for models that can think, with token and character counts.

## Requirements

- A Mac with Apple silicon and macOS 13 or later; 16 GB of memory is enough for a 4-bit 9B model or the
  4-bit image model (one model is resident at a time and swapped on demand)
- [uv](https://docs.astral.sh/uv/) and Node.js 20.19+ or 22.12+ (for Vite 8)

## Setup

```sh
uv sync                       # Python 3.12 environment with pinned MLX runtimes
(cd ui && npm ci)             # UI dependencies
desktop/build_app.sh          # builds the UI and desktop/build/MLX Agents.app
open "desktop/build/MLX Agents.app"
```

The app bundle starts this folder's virtual environment, so keep the project where it is (or rebuild after
moving it). You can also run it from a terminal with `.venv/bin/python -m desktop.app`.

### Models

Each folder in `models/` is one model. Download MLX-format repositories from Hugging Face in
Settings → 模型 (Models), or place them there yourself:

| Kind | Recognised by | Examples |
| --- | --- | --- |
| Text chat (mlx-lm) | `config.json` without `vision_config` | `mlx-community/Qwen3-8B-4bit` |
| Vision chat (mlx-vlm) | `config.json` with `vision_config` | `mlx-community/Qwen3.5-9B-4bit`, `mlx-community/Qwen2.5-VL-7B-Instruct-4bit` |
| Image (mflux) | `transformer/` and `vae/` folders | `mlx-community/Qwen-Image-2.1-mflux-q4` |

Only the Qwen-Image 2.1 family is wired up for image generation. `scripts/` shows how to assemble an mflux
model from a community MLX transformer plus the mflux companion files.

### MCP servers

Settings → MCP takes the same JSON as Claude Desktop or Cursor:

```json
{
  "mcpServers": {
    "filesystem": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "~/Documents"] },
    "remote": { "url": "https://example.com/mcp", "headers": { "Authorization": "Bearer …" }, "disabled": true }
  }
}
```

Local servers start in the agent's workspace. Their tools appear to the agent as `mcp__<server>__<tool>`
once the chat's MCP switch is on, and each call asks for approval unless you auto-approve MCP for that chat.

## Where things are kept

Everything the app writes stays in the project folder and is ignored by git:

- `data/`: chats, agents, memory, settings, MCP configuration, window storage and the client log
- `models/`, `outputs/` (generated images with JSON sidecars), `uploads/` (attachments)
- the agent's workspace, `~/MLX Agents` by default (Settings → 通用)

The desktop client serves the backend on `127.0.0.1:17860` behind a per-launch token, so other local
processes and web pages cannot call it.

## Layout

```
backend/
  engine.py       MLX thread: loads one model at a time; chat streaming, image jobs
  models.py       model registry, defaults, Hugging Face downloads
  agent.py        runs: model/tool loop, approvals, plan, sub-agents, event replay
  tools.py        built-in tools and their permission groups
  mcp_servers.py  MCP sessions and their tools
  store.py        chats, memory, agents, settings on disk
  main.py         FastAPI API (runs over SSE) and the UI
ui/               React 19 + Vite + Tailwind 4 + Motion; builds into desktop/dist
desktop/          pywebview client, app bundle builder, icon
scripts/          model download and conversion helpers
```

## Limitations

- One model is resident at a time; switching between chat and image models, or between chat models, reloads
  weights (seconds for a cached 4-bit 9B model).
- Tool use is only as reliable as the model. Small local models sometimes skip a tool or call one with
  wrong arguments; the agent reports tool errors back to the model so it can retry.
- Model work runs one call at a time. Several chats can be active at once, but their model calls queue.
- MCP: stdio and streamable HTTP transports only; no OAuth flow (pass tokens in `headers`).
