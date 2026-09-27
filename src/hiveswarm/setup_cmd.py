"""`hm init` and `hm up`: the single-machine path.

`hm init` writes ~/.hiveswarm/config.toml and worker.toml with sane defaults, a random token, the agents it finds
on PATH, and a first project. `hm up` runs the decide service, the daemon, the worker and the web app in one
terminal, so a stranger goes from install to a running swarm in one command each.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .config import home
from .workers import adapters

TEMPLATE_CONFIG = """# Hiveswarm hub configuration (written by `hm init`). Every key is optional; see docs/config.md.

[paths]
projects_root = "{projects_root}"

[daemon]
host = "{host}"
port = {port}
poll_seconds = 2

[decide]
backend = "{backend}"
fallback = "mock"
url = "http://127.0.0.1:{decide_port}"
port = {decide_port}
timeout_seconds = 20

[decide.openai]
url = "{openai_url}"
model = "{openai_model}"

[decide.local]
url = "http://127.0.0.1:8080/v1/chat/completions"
model = "coder"

[verify]
mode = "auto"
default_image = "python:3.12-slim"
timeout_seconds = 600

[sessions]
risk_gate = "{risk_gate}"
auto_approve_max_risk = "low"

[watch]
stall_minutes = 3
silence_minutes = 6

[routing]
probation = true

[workers.prime_agent]
enabled = false

[projects.{project}]
repo_path = "{repo_path}"
verify_image = "python:3.12-slim"
"""

TEMPLATE_WORKER = """# Hiveswarm worker configuration (written by `hm init`).
daemon_url = "http://127.0.0.1:{port}"
root = "{work_root}"
local_projects = "{local_projects}"
poll_seconds = 3
hook_port = 7791
{agents}"""

AGENT_BLOCKS = {
    "claude_code": '[agents.claude_code]\nadapter = "claude_code"\nconfig_dir = "~/.claude-worker"\ndangerously_skip_permissions = true\nconcurrency = 2\ntimeout = 1800\n',
    "codex": '[agents.codex]\nadapter = "codex"\nsandbox = "workspace-write"\nconcurrency = 1\ntimeout = 1800\n',
    "cursor": '[agents.cursor]\nadapter = "cursor"\nforce = true\nconcurrency = 1\ntimeout = 1800\n',
    "gemini": '[agents.gemini]\nadapter = "gemini"\nconcurrency = 1\ntimeout = 1800\n',
    "antigravity": '[agents.antigravity]\nadapter = "antigravity"\ndangerously_skip_permissions = true\npty = true\nconcurrency = 1\ntimeout = 1800\n',
    "opencode": '[agents.opencode]\nadapter = "opencode"\nconcurrency = 1\ntimeout = 1800\nenabled = false\n',
}


def detect_agents() -> dict[str, str | None]:
    return {name: shutil.which(binary) for name, binary in adapters.BINARIES.items()}


def _free_port(preferred: int) -> int:
    for port in (preferred, preferred + 1000, preferred + 2000):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


def init(project: str | None, repo_path: str | None, backend: str | None, force: bool = False,
         openai_url: str | None = None, openai_model: str | None = None, quiet: bool = False) -> dict[str, Any]:
    h = home()
    cfg_path = h / "config.toml"
    worker_path = h / "worker.toml"
    env_path = h / "env"
    if cfg_path.exists() and not force:
        raise FileExistsError(f"{cfg_path} already exists (use --force to overwrite)")
    for d in ("db", "logs", "worktrees", "repos", "work"):
        (h / d).mkdir(parents=True, exist_ok=True)
    project = project or "demo"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", project):
        raise ValueError("project name: letters, digits, - and _ only")
    repo = Path(os.path.expanduser(repo_path)) if repo_path else h / "repos" / project
    if not (repo / ".git").exists():
        repo.mkdir(parents=True, exist_ok=True)
        ident = ["-c", "user.name=hiveswarm", "-c", "user.email=hiveswarm@localhost"]
        subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
        if not any(repo.iterdir()) or not (repo / "README.md").exists():
            (repo / "README.md").write_text(f"# {project}\n\nA project managed by Hiveswarm.\n")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), *ident, "commit", "-q", "-m", f"init {project}"], check=False)
    if backend is None:
        backend = "openai" if (os.environ.get("OPENAI_API_KEY") or os.environ.get("HIVESWARM_DECIDE_API_KEY")) else "mock"
    port = _free_port(7778)
    decide_port = _free_port(9000)
    found = detect_agents()
    agent_names = [n for n, path in found.items() if path] or ["claude_code"]
    agents_toml = "\n".join(AGENT_BLOCKS[n] for n in agent_names if n in AGENT_BLOCKS)
    token = secrets.token_urlsafe(24)
    cfg_path.write_text(TEMPLATE_CONFIG.format(
        projects_root=str(h / "repos"), host="127.0.0.1", port=port, backend=backend, decide_port=decide_port,
        openai_url=openai_url or "https://api.openai.com/v1", openai_model=openai_model or "gpt-4o-mini",
        risk_gate="pattern", project=project, repo_path=str(repo)))
    worker_path.write_text(TEMPLATE_WORKER.format(port=port, work_root=str(h / "work"), local_projects=str(Path.home() / "hiveswarm"), agents=agents_toml))
    fd = os.open(str(env_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"HIVESWARM_URL=http://127.0.0.1:{port}\nHIVESWARM_TOKEN={token}\n")
    out = {"home": str(h), "config": str(cfg_path), "worker": str(worker_path), "env": str(env_path), "token": token,
           "port": port, "decide_port": decide_port, "backend": backend, "project": project, "repo": str(repo),
           "agents": {n: found.get(n) for n in agent_names}, "missing": [n for n, p in found.items() if not p]}
    if not quiet:
        print(f"wrote {cfg_path}")
        print(f"wrote {worker_path}")
        print(f"wrote {env_path} (daemon URL and token; `hm` reads it automatically)")
        print(f"project {project} → {repo}")
        print("agents found: " + (", ".join(f"{n} ({p})" for n, p in out['agents'].items() if p) or "none — install Claude Code, Codex, Cursor or Gemini and rerun hm init --force"))
        if backend == "mock":
            print("decide backend: mock (fixed answers). For real classification set OPENAI_API_KEY (or any OpenAI-compatible endpoint) and rerun with --backend openai, or point [decide.local] at a local model.")
        else:
            print(f"decide backend: {backend}")
        print("next: hm up")
    return out


def load_env_file() -> None:
    """`hm` commands pick up ~/.hiveswarm/env so nobody has to export the token by hand."""
    p = home() / "env"
    if not p.is_file():
        return
    for line in p.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def up(ui: bool = True, open_browser: bool = True, ui_port: int = 7790) -> int:
    """Run decide + daemon + worker (+ web app) in this terminal until Ctrl-C."""
    h = home()
    if not (h / "config.toml").is_file():
        print("no ~/.hiveswarm/config.toml yet — run: hm init", file=sys.stderr)
        return 2
    load_env_file()
    env = {**os.environ, "HIVESWARM_CONFIG": str(h / "config.toml"), "HIVESWARM_WORKER_CONFIG": str(h / "worker.toml"),
           "PYTHONUNBUFFERED": "1"}
    py = sys.executable
    procs: list[tuple[str, subprocess.Popen[bytes]]] = []

    def start(name: str, argv: list[str]) -> None:
        p = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        procs.append((name, p))
        import threading

        def pump() -> None:
            assert p.stdout is not None
            for raw in p.stdout:
                line = raw.decode(errors="replace").rstrip()
                if line:
                    print(f"[{name:6}] {line}", flush=True)
        threading.Thread(target=pump, daemon=True).start()

    start("decide", [py, "-m", "hiveswarm.decide"])
    time.sleep(1.0)
    start("daemon", [py, "-m", "hiveswarm.daemon"])
    time.sleep(1.5)
    start("worker", [py, "-m", "hiveswarm.workers.remote", "--config", str(h / "worker.toml")])
    if ui:
        start("ui", [py, "-c", f"from hiveswarm.ui_server import main; main(port={int(ui_port)}, open_browser={bool(open_browser)!r})"])
    print(f"\nhiveswarm is up · app http://127.0.0.1:{ui_port} · daemon {env.get('HIVESWARM_URL', '')} · Ctrl-C stops everything\n", flush=True)

    def stop(*_: Any) -> None:
        for name, p in reversed(procs):
            if p.poll() is None:
                p.send_signal(signal.SIGINT)
        deadline = time.time() + 8
        for _, p in procs:
            try:
                p.wait(timeout=max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                p.kill()

    signal.signal(signal.SIGINT, lambda *_: (stop(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda *_: (stop(), sys.exit(0)))
    while True:
        for name, p in procs:
            if p.poll() is not None:
                print(f"[{name:6}] exited with {p.returncode}; stopping the rest", flush=True)
                stop()
                return p.returncode or 1
        time.sleep(1)
