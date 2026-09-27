"""hm ui — serves the Hiveswarm web app, proxies the daemon API, and bridges agent terminals (tmux) to the browser."""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import logging
import os
import pty
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import termios
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from . import netlog

log = logging.getLogger("hiveswarm.ui")

TMUX_SOCKET = "hm"
TMUX_SESSION = "hm"
DIST = Path(__file__).parent / "ui_dist"


def _worker_cfg() -> dict[str, Any]:
    from .workers.remote import _load_cfg
    try:
        return _load_cfg(None)
    except FileNotFoundError:
        return {}


class Settings:
    def __init__(self) -> None:
        cfg = _worker_cfg()
        self.daemon = (os.environ.get("HIVESWARM_URL") or cfg.get("daemon_url") or "http://127.0.0.1:7778").rstrip("/")
        self.token = os.environ.get("HIVESWARM_TOKEN") or cfg.get("token") or ""
        self.hostname = socket.gethostname()
        self.tmux = bool(shutil.which("tmux"))


S = Settings()
app = FastAPI(title="Hiveswarm UI")
_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        headers = {"Authorization": f"Bearer {S.token}"} if S.token else {}
        _client = netlog.LoggedAsyncClient(netlog.NetLog("ui"), base_url=S.daemon, headers=headers, timeout=120,
                                           limits=httpx.Limits(max_connections=16, max_keepalive_connections=8, keepalive_expiry=120))
    return _client


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _client is not None:
        await _client.aclose()


# ── local facts ──────────────────────────────────────────────────────────

@app.get("/api/local")
async def local() -> dict[str, Any]:
    alive = False
    if S.tmux:
        r = subprocess.run(["tmux", "-L", TMUX_SOCKET, "has-session", "-t", TMUX_SESSION], capture_output=True)
        alive = r.returncode == 0
    from . import __version__
    return {"product": "hiveswarm", "version": __version__, "hostname": S.hostname, "tmux": S.tmux,
            "tmux_alive": alive, "daemon": S.daemon}


# ── SSE: feed + state snapshots ──────────────────────────────────────────

async def _fetch(path: str, **params: Any) -> Any:
    r = await client().get(path, params={k: v for k, v in params.items() if v is not None})
    r.raise_for_status()
    return r.json()


@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    async def gen():
        last_feed: int | None = None
        last_state_hash = ""
        last_state_at = 0.0
        last_change_at = time.monotonic()
        yield "retry: 2000\n\n"
        while True:
            if await request.is_disconnected():
                return
            # Poll fast while something is happening, slowly once the swarm has been quiet for a while, so an open
            # tab on an idle swarm costs a request every few seconds rather than several per second.
            idle = time.monotonic() - last_change_at > 30
            feed_every, state_every = (3.0, 6.0) if idle else (1.0, 1.5)
            try:
                if last_feed is None:
                    d = await _fetch("/feed", tail=2000)
                else:
                    d = await _fetch("/feed", since=last_feed)
                entries = d.get("entries", [])
                if entries:
                    last_change_at = time.monotonic()
                    yield f"event: feed\ndata: {json.dumps(entries)}\n\n"
                last_feed = max(last_feed or 0, int(d.get("last_id") or 0), *[int(e["id"]) for e in entries] or [0])
                now = time.monotonic()
                if now - last_state_at >= state_every:
                    last_state_at = now
                    summary, tasks, inbox, agents = await asyncio.gather(
                        _fetch("/summary"), _fetch("/tasks", limit=300), _fetch("/inbox"), _fetch("/agents"))
                    state = {"summary": summary, "tasks": tasks, "inbox": inbox.get("items", []), "agents": agents,
                             "ts": int(time.time())}
                    h = hashlib.md5(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()
                    if h != last_state_hash:
                        if last_state_hash:
                            last_change_at = time.monotonic()
                        last_state_hash = h
                        yield f"event: state\ndata: {json.dumps(state, default=str)}\n\n"
                yield ": ping\n\n"
            except Exception as e:
                yield f"event: error\ndata: {json.dumps({'error': str(e)[:200]})}\n\n"
                await asyncio.sleep(2)
            await asyncio.sleep(feed_every)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── local project copies (this machine) ───────────────────────────────────

@app.post("/api/local/open")
async def local_open(request: Request) -> dict[str, Any]:
    """Reveal a project's local copy in the file manager (Explorer from WSL)."""
    from .workers.local_copy import open_folder
    body = await request.json()
    name = str(body.get("project") or "")
    projects = await _fetch("/projects")
    local = (projects.get(name) or {}).get("local") or {}
    path = local.get("path")
    if not path or local.get("host") != S.hostname:
        return {"ok": False, "reason": "no copy of this project on this machine yet"}
    if not os.path.isdir(path):
        return {"ok": False, "reason": f"{path} does not exist"}
    return {"ok": open_folder(path), "path": path, "win_path": local.get("win_path")}


@app.post("/api/local/sync")
async def local_sync(request: Request) -> dict[str, Any]:
    """Fast-forward this machine's copy of a project now instead of waiting for the worker's next pass."""
    from .workers.local_copy import LocalCopies

    class _Client:
        def get(self, path: str, timeout: int = 30, **params: Any) -> Any:
            import httpx as _h
            r = _h.get(S.daemon + path, params=params, headers={"Authorization": f"Bearer {S.token}"} if S.token else {}, timeout=timeout)
            r.raise_for_status()
            return r.json()

        def post(self, path: str, body: dict[str, Any], timeout: int = 30) -> Any:
            import httpx as _h
            r = _h.post(S.daemon + path, json=body, headers={"Authorization": f"Bearer {S.token}"} if S.token else {}, timeout=timeout)
            r.raise_for_status()
            return r.json()

    body = await request.json()
    name = str(body.get("project") or "")
    copies = LocalCopies(_Client(), _worker_cfg(), S.hostname)
    if not copies.enabled:
        return {"ok": False, "reason": "local copies are off in worker.toml"}
    projects = await _fetch("/projects")
    if name not in projects:
        return {"ok": False, "reason": "no such project"}
    return await asyncio.to_thread(copies.sync_one, name, projects[name], True)


# ── planning (runs Claude Code on this machine) ──────────────────────────

@app.post("/api/plan")
async def plan(request: Request) -> dict[str, Any]:
    from .tui import decompose as _decompose
    body = await request.json()
    goal = str(body.get("goal") or "").strip()
    project = str(body.get("project") or "")
    new_repo = bool(body.get("new_repo"))
    if not goal:
        return {"tasks": [], "error": "empty goal"}
    repo = ""
    templates: list[str] = []
    try:
        projects = await _fetch("/projects")
        p = projects.get(project) or {}
        repo = p.get("repo_path") or ""
        templates = list(p.get("acceptance_templates") or [])
    except Exception:
        pass
    tasks, err = await _decompose.decompose(goal, project, repo, templates, new_repo=new_repo)
    return {"tasks": tasks, "error": err}


# ── API proxy ────────────────────────────────────────────────────────────

@app.api_route("/api/{path:path}", methods=["GET", "POST", "DELETE", "PUT", "PATCH"])
async def proxy(path: str, request: Request) -> Response:
    body = await request.body()
    try:
        r = await client().request(request.method, "/" + path, params=dict(request.query_params), content=body or None,
                                   headers={"Content-Type": request.headers.get("content-type", "application/json")})
    except httpx.HTTPError as e:
        return JSONResponse({"detail": f"cannot reach the daemon at {S.daemon}: {type(e).__name__}"}, status_code=502)
    return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json"))


# ── terminal bridge ──────────────────────────────────────────────────────

def _tmux(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["tmux", "-L", TMUX_SOCKET, *args], capture_output=True, text=True, timeout=10)


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", max(rows, 2), max(cols, 10), 0, 0))


_ANSI_RE = __import__("re").compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][A-Za-z0-9]|\x1b[=>]")


def _crop_ansi(line: str, cols: int) -> str:
    out: list[str] = []
    visible = 0
    i = 0
    n = len(line)
    while i < n:
        m = _ANSI_RE.match(line, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        ch = line[i]
        w = 2 if __import__("unicodedata").east_asian_width(ch) in ("W", "F") else 1
        if visible + w > cols:
            break
        out.append(ch)
        visible += w
        i += 1
    return "".join(out) + "\x1b[0m"


async def _snapshot_loop(ws: WebSocket, win: str, cols: int, rows: int) -> None:
    last = ""
    while True:
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=0.45)
            if msg.get("type") == "websocket.disconnect":
                return
            if msg.get("text"):
                try:
                    m = json.loads(msg["text"])
                    if m.get("type") == "resize":
                        cols, rows = int(m["cols"]), int(m["rows"])
                        last = ""
                except Exception:
                    pass
        except TimeoutError:
            pass
        r = _tmux("capture-pane", "-e", "-p", "-t", win, "-S", f"-{max(rows * 3, 60)}")
        if r.returncode != 0:
            await ws.send_text(json.dumps({"type": "error", "message": "the terminal window is gone"}))
            return
        lines = r.stdout.rstrip("\n").split("\n")
        while lines and not _ANSI_RE.sub("", lines[-1]).strip():
            lines.pop()
        shown = lines[-rows:] if rows > 0 else lines
        frame = "\x1b[2J\x1b[H" + "\r\n".join(_crop_ansi(ln, cols) for ln in shown)
        if frame != last:
            last = frame
            await ws.send_bytes(frame.encode())


@app.websocket("/ws/term/{tid}")
async def term(ws: WebSocket, tid: str) -> None:
    await ws.accept()
    try:
        d = await _fetch(f"/sessions/{tid}")
    except Exception as e:
        await ws.send_text(json.dumps({"type": "error", "message": f"no such session: {e}"}))
        await ws.close()
        return
    sess = d.get("session") or {}
    win = sess.get("tmux")
    if not S.tmux or not win:
        await ws.send_text(json.dumps({"type": "error", "message": "this session has no terminal yet"}))
        await ws.close()
        return
    if sess.get("host") != S.hostname:
        await ws.send_text(json.dumps({"type": "error", "message": f"terminal lives on {sess.get('host')}; run hm ui there"}))
        await ws.close()
        return
    if _tmux("display-message", "-p", "-t", str(win), "#{window_id}").returncode != 0:
        await ws.send_text(json.dumps({"type": "error", "message": "the terminal window is gone"}))
        await ws.close()
        return
    if ws.query_params.get("mode") == "snapshot":
        cols = int(ws.query_params.get("cols") or 80)
        rows = int(ws.query_params.get("rows") or 24)
        try:
            await _snapshot_loop(ws, str(win), cols, rows)
        except WebSocketDisconnect:
            pass
        finally:
            try:
                await ws.close()
            except Exception:
                pass
        return
    view = f"v-{secrets.token_hex(3)}"
    r = _tmux("new-session", "-d", "-t", TMUX_SESSION, "-s", view)
    if r.returncode != 0:
        await ws.send_text(json.dumps({"type": "error", "message": f"tmux: {r.stderr.strip()[:200]}"}))
        await ws.close()
        return
    _tmux("select-window", "-t", f"{view}:{win}")
    _tmux("set-option", "-t", view, "status", "off")
    _tune_tmux()
    cols, rows = 120, 36
    try:
        first = await asyncio.wait_for(ws.receive_text(), timeout=2)
        m = json.loads(first)
        if m.get("type") == "resize":
            cols, rows = int(m["cols"]), int(m["rows"])
    except Exception:
        pass
    pid, fd = pty.fork()
    if pid == 0:
        env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor", "LANG": os.environ.get("LANG") or "C.UTF-8"}
        env.pop("TMUX", None)
        os.execvpe("tmux", ["tmux", "-L", TMUX_SOCKET, "attach-session", "-t", view], env)
    _set_winsize(fd, rows, cols)
    loop = asyncio.get_running_loop()
    reader_done = asyncio.Event()

    def _on_readable() -> None:
        try:
            data = os.read(fd, 65536)
        except OSError:
            data = b""
        if not data:
            loop.remove_reader(fd)
            reader_done.set()
            return
        asyncio.ensure_future(ws.send_bytes(data))

    loop.add_reader(fd, _on_readable)
    try:
        while not reader_done.is_set():
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=0.5)
            except TimeoutError:
                continue
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                os.write(fd, msg["bytes"])
            elif msg.get("text") is not None:
                t = msg["text"]
                if t.startswith("{"):
                    try:
                        m = json.loads(t)
                    except Exception:
                        m = {}
                    if m.get("type") == "resize":
                        _set_winsize(fd, int(m["rows"]), int(m["cols"]))
                        continue
                    if m.get("type") == "input":
                        os.write(fd, m.get("data", "").encode())
                        continue
                os.write(fd, t.encode())
    except WebSocketDisconnect:
        pass
    finally:
        try:
            loop.remove_reader(fd)
        except Exception:
            pass
        try:
            os.kill(pid, signal.SIGHUP)
        except Exception:
            pass
        try:
            os.close(fd)
        except Exception:
            pass
        _tmux("kill-session", "-t", view)
        try:
            await ws.close()
        except Exception:
            pass


_TUNED = False


def _tune_tmux() -> None:
    """Same viewer options the worker sets, in case this app is talking to a tmux server an older worker started."""
    global _TUNED
    if _TUNED:
        return
    _tmux("set-option", "-g", "mouse", "on")
    _tmux("set-option", "-as", "terminal-features", "xterm-256color:clipboard")
    _tmux("set-option", "-ga", "terminal-overrides", "xterm-256color:Ms=\\E]52;%p1%s;%p2%s\\007")
    _tmux("unbind", "-n", "MouseDown3Pane")
    _tmux("unbind", "-n", "M-MouseDown3Pane")
    _TUNED = True


# ── static app ───────────────────────────────────────────────────────────

@app.get("/{path:path}")
async def static(path: str) -> Response:
    if not DIST.exists():
        return JSONResponse({"detail": "the web app is not built into this install (missing ui_dist)"}, status_code=503)
    target = (DIST / path).resolve() if path else DIST / "index.html"
    if path and target.is_file() and str(target).startswith(str(DIST.resolve())):
        return FileResponse(target)
    return FileResponse(DIST / "index.html")


def _open_browser(url: str) -> None:
    for cmd in (["wslview", url], ["xdg-open", url], ["cmd.exe", "/c", "start", "", url], ["open", url]):
        if shutil.which(cmd[0]):
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except Exception:
                continue


def main(port: int = 7790, host: str = "127.0.0.1", open_browser: bool = True) -> None:
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    url = f"http://{host}:{port}"
    log.info("Hiveswarm app on %s (daemon %s, terminals on %s)", url, S.daemon, S.hostname)
    if open_browser:
        _open_browser(url)
    uvicorn.run(app, host=host, port=port, log_level="warning")
