"""`hm init`, `hm up` and `hm migrate`: getting a machine running.

`hm init` writes ~/.hiveswarm/config.toml and worker.toml with sane defaults, a random token, the agents it finds
on PATH, and a first project. `hm up` runs what this machine needs in one terminal: on a single machine (or a hub
that also runs agents) the decide service, the daemon, a worker and the web app; on a laptop whose worker points at
a remote hub, only the worker and the web app. `hm migrate` moves a pre-rename Hivemind laptop setup
(~/.config/hivemind, the hivemind-* user services) into ~/.hiveswarm without touching its data.
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
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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


def worker_config_candidates() -> list[Path]:
    env = os.environ.get("HIVESWARM_WORKER_CONFIG")
    out = [Path(env)] if env else []
    return out + [home() / "worker.toml", Path.home() / ".config" / "hiveswarm" / "worker.toml",
                  Path.home() / ".config" / "hivemind" / "worker.toml"]


def worker_config_path() -> Path | None:
    for c in worker_config_candidates():
        if c.is_file():
            return c
    return None


def hub_config_path() -> Path | None:
    from .config import config_path
    try:
        return config_path()
    except FileNotFoundError:
        return None


def is_local_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host in ("", "127.0.0.1", "localhost", "::1", "0.0.0.0"):
        return True
    try:
        return host in (socket.gethostname().lower(), socket.getfqdn().lower())
    except Exception:
        return False


def other_worker_running() -> str | None:
    """A worker already running on this machine (a systemd service, another terminal): its command line, or None."""
    if not shutil.which("pgrep"):
        return None
    r = subprocess.run(["pgrep", "-af", r"hiveswarm\.workers\.remote|hiveswarm-worker|hivemind-worker|hivemind\.workers\.remote"],
                       capture_output=True, text=True)
    me = {str(os.getpid()), str(os.getppid())}
    my_home = str(Path.home())
    for line in r.stdout.splitlines():
        pid, _, cmd = line.partition(" ")
        if pid in me or "pgrep" in cmd:
            continue
        try:
            env = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
            home_of = next((e[5:].decode(errors="replace") for e in env if e.startswith(b"HOME=")), None)
            if home_of is not None and home_of != my_home:
                continue  # another user's (or another test home's) worker
        except OSError:
            pass
        return cmd.strip()
    return None


def plan(ui: bool = True) -> dict[str, Any]:
    """What `hm up` will start on this machine, and why."""
    load_env_file()
    wpath = worker_config_path()
    wcfg: dict[str, Any] = {}
    if wpath:
        with open(wpath, "rb") as f:
            wcfg = tomllib.load(f)
    daemon_url = os.environ.get("HIVESWARM_URL") or wcfg.get("daemon_url") or ""
    remote = bool(daemon_url) and not is_local_url(daemon_url)
    hub_cfg = hub_config_path()
    services: list[str] = []
    notes: list[str] = []
    if remote:
        notes.append(f"hub mode: the daemon runs at {daemon_url}; starting only this machine's worker and the app")
        if not os.environ.get("HIVESWARM_TOKEN") and not wcfg.get("token"):
            notes.append("warning: no HIVESWARM_TOKEN in ~/.hiveswarm/env or token in worker.toml; the hub will refuse this worker")
    else:
        if hub_cfg is None:
            return {"error": "no config.toml and no remote hub in worker.toml — run: hm init (or hm migrate for a Hivemind setup)"}
        services += ["decide", "daemon"]
    if wpath is None:
        notes.append("no worker.toml: this machine runs no agents (hub only)")
    else:
        running = other_worker_running()
        if running:
            notes.append(f"a worker is already running on this machine ({running[:120]}); not starting a second one")
        else:
            services.append("worker")
    if ui:
        services.append("ui")
    return {"services": services, "notes": notes, "remote": remote, "daemon_url": daemon_url,
            "worker_config": str(wpath) if wpath else None, "hub_config": str(hub_cfg) if hub_cfg else None}


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
    existing = worker_config_path()
    if existing and not force:
        hint = "run: hm migrate" if "hivemind" in str(existing) else "run: hm up"
        raise FileExistsError(f"{existing} already exists — this machine is already set up as a worker; {hint} (or --force to start over)")
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
    """Run what this machine needs (see plan()) in this terminal until Ctrl-C."""
    pl = plan(ui)
    if pl.get("error"):
        print(pl["error"], file=sys.stderr)
        return 2
    for n in pl["notes"]:
        print(n, flush=True)
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    if pl["hub_config"]:
        env["HIVESWARM_CONFIG"] = pl["hub_config"]
    if pl["worker_config"]:
        env["HIVESWARM_WORKER_CONFIG"] = pl["worker_config"]
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

    if "decide" in pl["services"]:
        start("decide", [py, "-m", "hiveswarm.decide"])
        time.sleep(1.0)
    if "daemon" in pl["services"]:
        start("daemon", [py, "-m", "hiveswarm.daemon"])
        time.sleep(1.5)
    if "worker" in pl["services"]:
        start("worker", [py, "-m", "hiveswarm.workers.remote", "--config", pl["worker_config"]])
    if "ui" in pl["services"]:
        start("ui", [py, "-c", f"from hiveswarm.ui_server import main; main(port={int(ui_port)}, open_browser={bool(open_browser)!r})"])
    if not procs:
        print("nothing to start on this machine", file=sys.stderr)
        return 2
    print(f"\nhiveswarm is up · {', '.join(pl['services'])} · app http://127.0.0.1:{ui_port} · daemon {pl['daemon_url'] or env.get('HIVESWARM_URL', '')} · Ctrl-C stops everything\n", flush=True)

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
        for name, p in list(procs):
            if p.poll() is not None:
                if name == "worker" and len(procs) > 1:
                    print(f"[{name:6}] exited with {p.returncode}; the app keeps running without agents on this machine "
                          "(check the lines above, fix worker.toml, then restart)", flush=True)
                    procs.remove((name, p))
                    continue
                print(f"[{name:6}] exited with {p.returncode}; stopping the rest", flush=True)
                stop()
                return p.returncode or 1
        time.sleep(1)


# ── hm migrate: a pre-rename Hivemind laptop → ~/.hiveswarm ──────────────────

_LEGACY = Path.home() / ".config" / "hivemind"
_LEGACY_SERVICES = ("hivemind-worker", "hivemind-ui")


def _set_top_key(text: str, key: str, value: str) -> str:
    """Set a top-level `key = "value"` in TOML text (before the first [table]), replacing it if present."""
    line = f'{key} = "{value}"'
    pat = re.compile(rf"^{re.escape(key)}\s*=.*$", re.M)
    first_table = re.search(r"^\[", text, re.M)
    head_end = first_table.start() if first_table else len(text)
    m = pat.search(text, 0, head_end)
    if m:
        return text[:m.start()] + line + text[m.end():]
    return text[:head_end].rstrip("\n") + ("\n" if head_end else "") + line + "\n\n" + text[head_end:].lstrip("\n")


def _unit_env(name: str) -> dict[str, str]:
    p = Path.home() / ".config" / "systemd" / "user" / f"{name}.service"
    out: dict[str, str] = {}
    if not p.is_file():
        return out
    for line in p.read_text().splitlines():
        m = re.match(r"\s*Environment=\"?([A-Z_]+)=(.*?)\"?\s*$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _systemctl(*args: str) -> subprocess.CompletedProcess[str] | None:
    if not shutil.which("systemctl"):
        return None
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=30)


def migrate(keep_services: bool = False, quiet: bool = False) -> dict[str, Any]:
    """Copy a Hivemind laptop setup into ~/.hiveswarm. Copies, never moves or deletes: the old folders stay until you
    remove them. Mirrors (root) and local project copies keep their current locations so nothing is cloned again."""
    h = home()
    h.mkdir(parents=True, exist_ok=True)
    did: list[str] = []
    notes: list[str] = []

    def say(msg: str) -> None:
        did.append(msg)
        if not quiet:
            print(msg, flush=True)

    unit = {**_unit_env("hivemind-ui"), **_unit_env("hivemind-worker")}

    src_w, dst_w = _LEGACY / "worker.toml", h / "worker.toml"
    wtext = ""
    if dst_w.exists():
        wtext = dst_w.read_text()
        say(f"kept {dst_w} (already there)")
    elif src_w.is_file():
        wtext = src_w.read_text()
        wcfg = tomllib.loads(wtext)
        if "root" not in wcfg and (Path.home() / "hivemind-work").is_dir():
            wtext = _set_top_key(wtext, "root", "~/hivemind-work")
        if "local_projects" not in wcfg and (Path.home() / "hivemind").is_dir():
            wtext = _set_top_key(wtext, "local_projects", "~/hivemind")
        if "hivemind" in str(wcfg.get("oauth_token_file", "")):
            wtext = _set_top_key(wtext, "oauth_token_file", "~/.hiveswarm/claude-token")
        if "spark_ssh" in wcfg and "hub_ssh" not in wcfg:
            wtext = _set_top_key(wtext, "hub_ssh", str(wcfg["spark_ssh"]))
        dst_w.write_text(wtext)
        say(f"copied {src_w} → {dst_w} (agents, lanes and folders unchanged: mirrors and local copies are reused, nothing is cloned again)")
    else:
        notes.append(f"no {src_w}; nothing to migrate for the worker")

    src_c, dst_c = _LEGACY / "config.toml", h / "config.toml"
    if src_c.is_file() and not dst_c.exists():
        shutil.copy2(src_c, dst_c)
        say(f"copied {src_c} → {dst_c}")

    src_t, dst_t = _LEGACY / "claude-token", h / "claude-token"
    if src_t.is_file() and not dst_t.exists():
        shutil.copy2(src_t, dst_t)
        os.chmod(dst_t, 0o600)
        say(f"copied the Claude sign-in token → {dst_t}")

    wcfg = tomllib.loads(wtext) if wtext else {}
    url = os.environ.get("HIVESWARM_URL") or unit.get("HIVEMIND_URL") or wcfg.get("daemon_url") or ""
    token = os.environ.get("HIVESWARM_TOKEN") or unit.get("HIVEMIND_TOKEN") or wcfg.get("token") or ""
    env_path = h / "env"
    existing: dict[str, str] = {}
    if env_path.is_file():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                existing[k.strip()] = v.strip()
    want = dict(existing)
    if url and not want.get("HIVESWARM_URL"):
        want["HIVESWARM_URL"] = url
    if token and not want.get("HIVESWARM_TOKEN"):
        want["HIVESWARM_TOKEN"] = token
    if want != existing:
        fd = os.open(str(env_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("".join(f"{k}={v}\n" for k, v in want.items()))
        say(f"wrote {env_path} (daemon {want.get('HIVESWARM_URL', '?')}, token {'set' if want.get('HIVESWARM_TOKEN') else 'MISSING'})")
    if not want.get("HIVESWARM_TOKEN"):
        notes.append("no token found (HIVEMIND_TOKEN in this shell, the old services, or worker.toml); add HIVESWARM_TOKEN=… to ~/.hiveswarm/env")

    if not keep_services:
        for svc in _LEGACY_SERVICES:
            st = _systemctl("is-enabled", svc)
            act = _systemctl("is-active", svc)
            if st is None:
                break
            if st.stdout.strip() in ("enabled", "enabled-runtime") or act.stdout.strip() == "active":
                _systemctl("disable", "--now", svc)
                say(f"stopped and disabled the old {svc} service (its unit file is left in ~/.config/systemd/user)")

    notes.append("the old program can go: uv tool uninstall hivemind")
    notes.append("if your own Claude Code uses the old MCP server: claude mcp remove hivemind -s user && claude mcp add hiveswarm -s user -- hiveswarm-mcp")
    if not quiet:
        for n in notes:
            print("note: " + n, flush=True)
    return {"did": did, "notes": notes, "home": str(h)}
