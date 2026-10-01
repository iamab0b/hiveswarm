"""Test harness: a whole Hiveswarm stack (decide with the mock backend, the daemon, one worker) in a temporary home,
with fake `claude` and `codex` executables on PATH that behave like the real CLIs (hooks, transcripts, prompts)
without calling any model."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
FAKES = Path(__file__).resolve().parent / "fakes"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True)


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "main", cwd=path)
    (path / "add.py").write_text("def add(a, b):\n    return a + b\n")
    (path / "test_add.py").write_text("from add import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    git("add", "-A", cwd=path)
    git("commit", "-q", "-m", "init", cwd=path)
    return path


class Stack:
    def __init__(self, home: Path):
        self.home = home
        self.repo = make_repo(home / "repos" / "demo")
        self.port = free_port()
        self.decide_port = free_port()
        self.hook_port = free_port()
        self.token = "test-token"
        self.claude_cfg = home / "claude-cfg"
        self.claude_cfg.mkdir()
        (home / "config.toml").write_text(f"""
[paths]
projects_root = "{home / 'repos'}"

[daemon]
host = "127.0.0.1"
port = {self.port}
poll_seconds = 1
access_log = false

[decide]
backend = "mock"
port = {self.decide_port}
url = "http://127.0.0.1:{self.decide_port}"
timeout_seconds = 10

[verify]
mode = "local"
timeout_seconds = 120

[sessions]
risk_gate = "pattern"
auto_approve_max_risk = "low"

[watch]
stall_minutes = 1
silence_minutes = 2

[workers.prime_agent]
enabled = false

[memory]
backend = "local"

[projects.demo]
repo_path = "{self.repo}"
""")
        (home / "worker.toml").write_text(f"""
daemon_url = "http://127.0.0.1:{self.port}"
root = "{home / 'work'}"
local_projects = "{home / 'local'}"
local_sync_seconds = 5
poll_seconds = 1
hook_port = {self.hook_port}

[agents.claude_code]
adapter = "claude_code"
config_dir = "{self.claude_cfg}"
dangerously_skip_permissions = true
concurrency = 2
timeout = 300

[agents.codex]
adapter = "codex"
concurrency = 1
timeout = 300
""")
        self.env = {
            **os.environ,
            "HIVESWARM_HOME": str(home),
            "HIVESWARM_CONFIG": str(home / "config.toml"),
            "HIVESWARM_WORKER_CONFIG": str(home / "worker.toml"),
            "HIVESWARM_TOKEN": self.token,
            "HIVESWARM_URL": f"http://127.0.0.1:{self.port}",
            "PATH": f"{FAKES}:{Path(sys.executable).parent}:{os.environ.get('PATH', '')}",
            "PYTHONPATH": str(ROOT / "src"),
            "FAKE_STEP": "0.2",
            "PYTHONUNBUFFERED": "1",
        }
        for k in ("HIVEMIND_URL", "HIVEMIND_TOKEN", "HIVEMIND_CONFIG"):
            self.env.pop(k, None)
        self.procs: list[subprocess.Popen[bytes]] = []
        self.logs = home / "stack.log"
        self.http = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", headers={"Authorization": f"Bearer {self.token}"}, timeout=30)

    def _spawn(self, *argv: str) -> subprocess.Popen[bytes]:
        out = open(self.logs, "ab")
        p = subprocess.Popen([sys.executable, *argv], env=self.env, stdout=out, stderr=subprocess.STDOUT)
        self.procs.append(p)
        return p

    def start(self) -> None:
        self._spawn("-m", "hiveswarm.decide")
        self._spawn("-m", "hiveswarm.daemon")
        self.wait_http("/health")
        self._spawn("-m", "hiveswarm.workers.remote", "--config", str(self.home / "worker.toml"))
        self.wait_for(lambda: any(a["alive"] for a in self.get("/agents")), 30, "worker registration")

    def stop(self) -> None:
        for p in reversed(self.procs):
            if p.poll() is None:
                p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        subprocess.run(["tmux", "-L", "hm", "kill-server"], capture_output=True)

    # ── helpers ──
    def wait_http(self, path: str, timeout: float = 30) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.http.get(path).status_code < 500:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        raise RuntimeError(f"daemon did not answer {path}; log:\n{self.logs.read_text()[-3000:]}")

    def wait_for(self, cond: Any, timeout: float, what: str = "condition") -> Any:
        deadline = time.time() + timeout
        last: Any = None
        while time.time() < deadline:
            last = cond()
            if last:
                return last
            time.sleep(0.5)
        raise AssertionError(f"timed out waiting for {what}; stack log tail:\n{self.logs.read_text()[-6000:]}")

    def get(self, path: str, **params: Any) -> Any:
        r = self.http.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict[str, Any] | None = None, **params: Any) -> Any:
        r = self.http.post(path, json=body or {}, params=params)
        r.raise_for_status()
        return r.json()

    def put(self, path: str, body: dict[str, Any] | None = None, **params: Any) -> Any:
        r = self.http.put(path, json=body or {}, params=params)
        r.raise_for_status()
        return r.json()

    def delete(self, path: str, **params: Any) -> Any:
        r = self.http.delete(path, params=params)
        r.raise_for_status()
        return r.json()

    def task(self, tid: str) -> dict[str, Any]:
        return self.get(f"/tasks/{tid}")

    def state(self, tid: str) -> str:
        return self.task(tid)["task"]["state"]

    def session(self, tid: str) -> dict[str, Any]:
        import json
        s = self.task(tid)["task"].get("session") or "{}"
        return json.loads(s) if isinstance(s, str) else s

    def log_text(self, tid: str) -> str:
        return "\n".join(e["chunk"] for e in self.get(f"/tasks/{tid}/log", tail=400)["entries"])


@pytest.fixture(scope="session")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Any:
    home = tmp_path_factory.mktemp("hs")
    st = Stack(home)
    st.start()
    try:
        yield st
    finally:
        st.stop()


@pytest.fixture(scope="session")
def app_server(stack: Stack) -> Any:
    """The packaged web app served against the test stack; `app_server.url` is its address."""
    port = free_port()
    out = open(stack.home / "ui.log", "ab")
    p = subprocess.Popen([sys.executable, "-c", f"from hiveswarm.ui_server import main; main(port={port}, open_browser=False)"],
                         env=stack.env, stdout=out, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if httpx.get(url + "/", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.3)
    try:
        yield type("AppServer", (), {"url": url, "port": port, "proc": p})()
    finally:
        p.terminate()


@pytest.fixture
def page(app_server: Any) -> Any:
    """A Playwright page on the app (e2e tests; needs Chromium or HIVESWARM_E2E_CHROMIUM)."""
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as play:
        exe = os.environ.get("HIVESWARM_E2E_CHROMIUM")
        b = play.chromium.launch(executable_path=exe) if exe else play.chromium.launch()
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.errors = errors  # type: ignore[attr-defined]
        pg.base = app_server.url  # type: ignore[attr-defined]
        try:
            yield pg
        finally:
            b.close()
        assert not errors, errors


@pytest.fixture(scope="session")
def tmux_available() -> bool:
    return shutil.which("tmux") is not None


needs_tmux = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is required for interactive sessions")
