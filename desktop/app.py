"""MLX Agents desktop client: a native window around the local backend, with no separately run service.

The backend (backend.main) runs in a background thread on a fixed loopback port and is shut down with the
window. Run from the project root: .venv/bin/python -m desktop.app (the .app bundle does exactly this).
"""

import fcntl
import os
import secrets
import subprocess
import sys
import threading
import time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs

import uvicorn
import webview

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
DATA = ROOT / "data"
ICON = HERE / "icon.png"
# Fixed so the window's origin, and with it the localStorage drafts and preferences, survives restarts.
PORT = 17860
COOKIE = "mlx_agents_token"


class TokenGate:
    """Only the client window may use the backend. The window opens /?token=…, which is exchanged for a
    SameSite=Strict cookie; any request without it (other local processes, pages from other sites) gets 403."""

    def __init__(self, app, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        query = parse_qs(scope.get("query_string", b"").decode())
        if query.get("token", [None])[0] == self.token:
            cookie = f"{COOKIE}={self.token}; Path=/; HttpOnly; SameSite=Strict"
            await send({"type": "http.response.start", "status": 303, "headers": [(b"location", b"/"), (b"set-cookie", cookie.encode())]})
            return await send({"type": "http.response.body", "body": b""})
        header = dict(scope["headers"]).get(b"cookie", b"").decode()
        morsel = SimpleCookie(header).get(COOKIE)
        if morsel is None or not secrets.compare_digest(morsel.value, self.token):
            await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"text/plain")]})
            return await send({"type": "http.response.body", "body": b"forbidden"})
        return await self.app(scope, receive, send)


class Backend:
    """backend.main served on PORT from a background thread; heavy imports happen here, behind the splash."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.ready = threading.Event()
        self.error: str | None = None
        self.server: uvicorn.Server | None = None
        self.agent = None
        self.thread = threading.Thread(target=self._run, name="backend", daemon=True)

    def _run(self) -> None:
        try:
            from backend.main import agent, app

            self.agent = agent
            config = uvicorn.Config(TokenGate(app, self.token), host="127.0.0.1", port=PORT, log_level="warning")
            self.server = uvicorn.Server(config)
            threading.Thread(target=self._signal_when_started, daemon=True).start()
            self.server.run()
        except BaseException as exc:  # shown in the window instead of a silent failure
            self.error = f"{type(exc).__name__}: {exc}"
            self.ready.set()
            raise

    def _signal_when_started(self) -> None:
        while not (self.server and self.server.started) and self.thread.is_alive():
            time.sleep(0.05)
        if not self.thread.is_alive() and not self.error:
            self.error = f"后端未能启动，端口 {PORT} 可能被占用"
        self.ready.set()

    def busy(self) -> bool:
        return bool(self.agent and self.agent.active())

    def stop(self) -> None:
        if self.server:
            self.server.should_exit = True
            self.thread.join(timeout=5)


def dark_mode() -> bool:
    out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True)
    return out.stdout.strip() == "Dark"


# The app icon (desktop/make_icon.py) as vector art: gradient squircle, a large and a small four-point sparkle.
LOGO = """<svg class="logo" viewBox="0 0 100 100" aria-hidden="true"><defs>
<linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#5b6cff"/><stop offset="1" stop-color="#b25bff"/></linearGradient>
<radialGradient id="s" cx=".5" cy="0" r=".75"><stop offset="0" stop-color="#fff" stop-opacity=".22"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient></defs>
<rect width="100" height="100" rx="22.5" fill="url(#g)"/><rect width="100" height="100" rx="22.5" fill="url(#s)"/>
<path fill="#fff" fill-opacity=".97" d="M80 53Q49.33 55.33 47 86Q44.67 55.33 14 53Q44.67 50.67 47 20Q49.33 50.67 80 53Z"/>
<path fill="#fff" fill-opacity=".84" d="M86 25Q75.78 25.78 75 36Q74.22 25.78 64 25Q74.22 24.22 75 14Q75.78 24.22 86 25Z"/></svg>"""


def page(title: str, detail: str, dark: bool, spinner: bool) -> str:
    bg, ink, soft = ("#07090f", "#eef1fb", "rgba(238,241,251,.6)") if dark else ("#e9edf5", "#0f1424", "rgba(15,20,36,.55)")
    ring = '<div class="ring"></div>' if spinner else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{height:100%;margin:0;background:{bg};color:{ink};font-family:-apple-system,"PingFang SC",sans-serif;overflow:hidden}}
.blob{{position:fixed;width:60vmax;height:60vmax;border-radius:50%;filter:blur(80px);opacity:.55}}
.b1{{background:#5b6cff;top:-20vmax;left:-15vmax}}.b2{{background:#b25bff;bottom:-25vmax;right:-15vmax}}
.c{{position:relative;height:100%;display:grid;place-items:center;text-align:center}}
.logo{{display:block;width:72px;height:72px;margin:0 auto 18px;filter:drop-shadow(0 10px 24px rgba(91,108,255,.45))}}
h1{{font-size:22px;font-weight:600;margin:0}}p{{color:{soft};font-size:13px;margin:8px 0 0;max-width:520px}}
.ring{{width:22px;height:22px;margin:22px auto 0;border-radius:50%;border:3px solid rgba(127,127,127,.25);border-top-color:#7c8bff;animation:s 1s linear infinite}}
@keyframes s{{to{{transform:rotate(360deg)}}}}</style></head>
<body><div class="blob b1"></div><div class="blob b2"></div><div class="c"><div>{LOGO}
<h1>{title}</h1><p>{detail}</p>{ring}</div></div></body></html>"""


def lock_single_instance():
    DATA.mkdir(exist_ok=True)
    handle = open(DATA / "desktop.lock", "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    return handle


def name_process() -> None:
    """Show "MLX Agents" (not "Python") in the menu bar; must run before the Cocoa app starts."""
    try:
        from Foundation import NSBundle

        info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
        info["CFBundleName"] = "MLX Agents"
    except Exception:  # cosmetic only
        pass


def main() -> None:
    if not sys.stdout.isatty():  # launched from the .app: keep a log instead of losing output
        DATA.mkdir(exist_ok=True)
        log = open(DATA / "desktop.log", "a", buffering=1)
        sys.stdout = sys.stderr = log
        print(f"--- launch {time.strftime('%Y-%m-%d %H:%M:%S')}")

    lock = lock_single_instance()
    if lock is None:
        subprocess.run(["osascript", "-e", 'display alert "MLX Agents 已在运行" message "请切换到已经打开的窗口。"'])
        return

    name_process()
    dark = dark_mode()
    backend = Backend(secrets.token_urlsafe(24))
    backend.thread.start()

    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window(
        "MLX Agents",
        html=page("MLX Agents", "正在启动本地服务，首次加载组件需要几秒…", dark, spinner=True),
        width=1320,
        height=880,
        min_size=(420, 600),
        background_color="#07090f" if dark else "#e9edf5",
        text_select=True,
        localization={
            "global.quitConfirmation": "还有任务在生成，退出会中断它。确定退出吗？",
            "global.quit": "退出",
            "global.cancel": "取消",
        },
    )

    def on_closing() -> None:
        # Ask only when something is running; an idle window closes at once.
        window.confirm_close = backend.busy()

    def boot() -> None:
        backend.ready.wait()
        if backend.error:
            window.load_html(page("启动失败", f"{backend.error}。详细日志见 data/desktop.log。", dark, spinner=False))
            return
        window.load_url(f"http://127.0.0.1:{PORT}/?token={backend.token}")

    window.events.closing += on_closing
    webview.start(boot, private_mode=False, storage_path=str(DATA / "webview"), icon=str(ICON) if ICON.exists() else None)

    backend.stop()
    lock.close()
    # The model thread is not a daemon and may be mid-generation after a confirmed quit; end the process here.
    os._exit(0)


if __name__ == "__main__":
    main()
