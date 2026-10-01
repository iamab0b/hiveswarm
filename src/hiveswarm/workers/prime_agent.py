from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from .. import db, worktree
from ..config import load, load_current
from .adapters import _fmt, prime_parser, watch_changes

AGENT = "prime_agent"

_running: dict[str, subprocess.Popen[str]] = {}
_running_lock = threading.Lock()
_json_support: dict[str, bool] = {}


def _use_json(name: str) -> bool:
    forced = load().get("workers.prime_agent.json_events")
    if forced is not None:
        return bool(forced)
    if name not in _json_support:
        try:
            r = subprocess.run(["docker", "exec", name, "prime-agent", "--help"],
                               capture_output=True, text=True, timeout=30)
            text = (r.stdout or "") + (r.stderr or "")
            _json_support[name] = "--mode" in text and "json" in text
        except Exception:
            _json_support[name] = False
    return _json_support[name]


def available() -> bool:
    cfg = load_current()
    if cfg.get("workers.prime_agent.enabled", True) is False:
        return False
    name = cfg.get("workers.prime_agent.container", "prime-agent")
    try:
        r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", name],
                           capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


_avail: tuple[float, bool] = (0.0, False)


def available_cached(ttl: float = 10.0) -> bool:
    """`available()` at most once per `ttl` seconds, for views that are polled every second."""
    global _avail
    import time
    now = time.monotonic()
    if now - _avail[0] >= ttl:
        _avail = (now, available())
    return _avail[1]


def _prompt(task: Any, wt: str | None = None) -> str:
    from .. import directives as _directives
    block = _directives.prompt_block(_directives.for_task(task["id"]))
    parts = ([block] if block else []) + ["## Task", task["spec"]]
    if task.get("acceptance"):
        parts.append(f"\n## Acceptance\nThis must exit 0 when you are done:\n`{task['acceptance']}`\n"
                     "Run it yourself to confirm before you finish.")
    parts.append("\nYou are in the repository root. Make the change directly in the files here. "
                 "Do not create a git commit; the harness commits for you. "
                 "You have internet access: if a tool, library, or test runner is missing, install it "
                 "(pip install --user, npm install, cargo add, rustup target add, and so on). "
                 "Never replace the real test runner with a stub, mock, or wrapper script. "
                 "When the acceptance command passes, stop.")
    if wt and os.path.isdir(os.path.join(wt, ".wiki")):
        from .adapters import WIKI_NOTE
        parts.append(WIKI_NOTE)
    return "\n".join(parts)


def _kill_in_container(name: str) -> None:
    subprocess.run(["docker", "exec", name, "sh", "-c",
                    "pkill -f 'prime-agent .*--no-session' 2>/dev/null || true"],
                   capture_output=True, timeout=15)


def kill_all() -> None:
    cfg = load()
    _kill_in_container(cfg.get("workers.prime_agent.container", "prime-agent"))


def kill(task_id: str) -> bool:
    cfg = load()
    name = cfg.get("workers.prime_agent.container", "prime-agent")
    with _running_lock:
        proc = _running.get(task_id)
    if proc is None:
        return False
    try:
        proc.kill()
    except Exception:
        pass
    _kill_in_container(name)
    return True


def _pump(stream: Any, tid: str, kind: str, sink: list[str], parser: Any = None) -> None:
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            clean = line.rstrip("\n")
            events = parser(clean) if parser else ([(kind, clean)] if clean.strip() else [])
            for k, text in events:
                sink.append(_fmt(k, text))
                try:
                    db.log_append(tid, f"{AGENT}:{k}", text)
                except Exception:
                    pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


def run(task: Any, wt: str) -> dict[str, Any]:
    cfg = load()
    name = cfg.get("workers.prime_agent.container", "prime-agent")
    mount = cfg.get("workers.prime_agent.worktree_mount", "/worktrees")
    timeout = int(cfg.get("workers.prime_agent.timeout_seconds", 1800))
    extra = cfg.get("workers.prime_agent.extra_args", []) or []
    tid = task["id"]

    inner = str(Path(mount) / Path(wt).name)
    json_mode = _use_json(name)
    mode = ["--mode", "json"] if json_mode else ["-p"]
    cmd = ["docker", "exec", "-w", inner, name, "prime-agent", *mode, "--no-session", *extra, _prompt(task, wt)]
    db.log_append(tid, f"{AGENT}:status",
                  f"started on the hub in {inner}" + ("" if json_mode else " (this prime-agent build prints only at the end)"))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            text=True, bufsize=1)
    with _running_lock:
        _running[tid] = proc

    out_lines: list[str] = []
    err_lines: list[str] = []
    t_out = threading.Thread(target=_pump, args=(proc.stdout, tid, "out", out_lines,
                                                 prime_parser(inner) if json_mode else None), daemon=True)
    t_err = threading.Thread(target=_pump, args=(proc.stderr, tid, "err", err_lines), daemon=True)
    t_out.start()
    t_err.start()
    watch_stop = threading.Event()
    def _pulse(k: str, t: str) -> None:
        db.log_append(tid, f"{AGENT}:{k}", t)
        if t.startswith("files: "):
            from .. import overlaps
            overlaps.note_files(tid, task["project"], t[7:])

    threading.Thread(target=watch_changes, args=(wt, _pulse, watch_stop),
                     daemon=True).start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        _kill_in_container(name)
        proc.wait()
    finally:
        watch_stop.set()
        t_out.join(timeout=5)
        t_err.join(timeout=5)
        with _running_lock:
            _running.pop(tid, None)

    log = ("".join(out_lines) + "\n" + "".join(err_lines))[-6000:]
    if timed_out:
        db.log_append(tid, f"{AGENT}:status", f"timed out after {timeout}s; killed")
        return {"ok": False, "error": f"prime-agent timed out after {timeout}s\n{log}", "model": "coder"}
    if proc.returncode != 0:
        db.log_append(tid, f"{AGENT}:status", f"exited with code {proc.returncode}")
        return {"ok": False, "error": f"prime-agent exit {proc.returncode}\n{log}", "model": "coder"}

    status = subprocess.run(["git", "status", "--porcelain"], cwd=wt, capture_output=True, text=True, timeout=30).stdout.strip()
    if not status:
        db.log_append(tid, f"{AGENT}:status", "finished but made no changes")
        return {"ok": False, "error": "prime-agent finished but made no changes\n" + log, "model": "coder"}

    worktree.commit_all(wt, f"hiveswarm: {tid} (prime-agent)")
    db.log_append(tid, f"{AGENT}:status", "finished; committed changes for the verifier")
    return {"ok": True, "model": "coder", "diff_stat": worktree.diff_stat(wt), "log": log}
