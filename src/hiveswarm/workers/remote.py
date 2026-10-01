from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path
from typing import Any

import httpx

from .. import netlog
from . import adapters
from .session_host import SessionHost, hook_settings, kill_window, window_alive

log = logging.getLogger("hiveswarm.worker")
SESSION_HOST: SessionHost | None = None


def _load_cfg(path: str | None) -> dict[str, Any]:
    candidates = [path, os.environ.get("HIVESWARM_WORKER_CONFIG"), str(Path.home() / ".hiveswarm" / "worker.toml"),
                  str(Path.home() / ".config" / "hiveswarm" / "worker.toml"), str(Path.home() / ".config" / "hivemind" / "worker.toml")]
    for c in candidates:
        if c and Path(c).is_file():
            with open(c, "rb") as f:
                return tomllib.load(f)
    raise FileNotFoundError("no worker.toml found; run `hm init` or see docs/config.md")


class Client:
    """One pooled, keep-alive connection set to the hub daemon for the whole worker. Every call used to open a
    fresh TCP connection (and resolve the hostname again); with several lanes shipping logs twice a second that was
    thousands of short-lived connections an hour through WSL2's NAT, which is exactly the kind of churn that upsets
    Windows networking. Now a handful of connections stay open and are reused."""

    def __init__(self, url: str, token: str | None):
        self.url = url.rstrip("/")
        self.h = {"Authorization": f"Bearer {token}"} if token else {}
        self.logs_supported = True
        self._warned = False
        self.netlog = netlog.NetLog("worker")
        self._http = netlog.LoggedClient(self.netlog, base_url=self.url, headers=self.h, timeout=60,
                                         limits=httpx.Limits(max_connections=12, max_keepalive_connections=8, keepalive_expiry=120))

    def post(self, path: str, body: dict[str, Any], timeout: int = 60) -> Any:
        r = self._http.post(path, json=body, timeout=timeout)
        r.raise_for_status()
        return r.json() if r.content else None

    def get(self, path: str, timeout: int = 60, **params: Any) -> Any:
        r = self._http.get(path, params={k: v for k, v in params.items() if v is not None}, timeout=timeout)
        r.raise_for_status()
        return r.json() if r.content else None

    def log_batch(self, task_id: str, entries: list[dict[str, Any]]) -> None:
        if not self.logs_supported or not entries:
            return
        try:
            r = self._http.post(f"/tasks/{task_id}/log", json={"entries": entries}, timeout=10)
            if r.status_code in (404, 405, 422) and "no such task" not in r.text:
                self.logs_supported = False
                if not self._warned:
                    self._warned = True
                    log.warning("the hub daemon doesn't accept live logs yet (HTTP %s); "
                                "update it with the TUI runbook's Phase 1 to see agent output live", r.status_code)
        except Exception:
            pass


class LogShipper:
    def __init__(self, client: Client, task_id: str, agent: str, interval: float = 0.5):
        self.client = client
        self.task_id = task_id
        self.agent = agent
        self.interval = interval
        self._buf: list[dict[str, str]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def emit(self, kind: str, text: str) -> None:
        with self._lock:
            self._buf.append({"source": f"{self.agent}:{kind}", "chunk": text, "ts": round(time.time(), 3)})

    def _flush(self) -> None:
        with self._lock:
            batch, self._buf = self._buf, []
        for i in range(0, len(batch), 200):
            self.client.log_batch(self.task_id, batch[i:i + 200])

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self._flush()

    def close(self) -> None:
        self._stop.set()
        self._t.join(timeout=2)
        self._flush()


_GIT_SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]
_mirror_locks: dict[str, threading.Lock] = {}
_mirror_guard = threading.Lock()


def _mirror_lock(project: str) -> threading.Lock:
    with _mirror_guard:
        return _mirror_locks.setdefault(project, threading.Lock())


def _git(*args: str, cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *_GIT_SAFE, *args], cwd=cwd, capture_output=True, text=True, timeout=300, check=check)


_JUNK = ["__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/", ".venv/", "venv/",
         "node_modules/", ".hiveswarm.*", "*.egg-info/", ".coverage", "htmlcov/", ".cache/"]


def _ensure_mirror(root: Path, project: str, remote: str) -> Path:
    mirror = root / "mirrors" / project
    if not (mirror / ".git").exists():
        mirror.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--no-checkout", remote, str(mirror))
    else:
        _git("fetch", "--prune", "origin", cwd=str(mirror))
    exclude = mirror / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text() if exclude.exists() else ""
    missing = [j for j in _JUNK if j not in existing.splitlines()]
    if missing:
        exclude.write_text(existing.rstrip("\n") + "\n" + "\n".join(missing) + "\n")
    return mirror


def _worktree(root: Path, mirror: Path, task_id: str, base_ref: str, keep: bool = False) -> Path:
    wt = root / "worktrees" / task_id
    if keep and (wt / ".git").exists():
        return wt
    if wt.exists():
        _git("worktree", "remove", "--force", str(wt), cwd=str(mirror), check=False)
        shutil.rmtree(wt, ignore_errors=True)
    wt.parent.mkdir(parents=True, exist_ok=True)
    ref = base_ref if base_ref != "HEAD" else "origin/HEAD"
    if base_ref != "HEAD" and _git("rev-parse", "--verify", "--quiet", ref, cwd=str(mirror), check=False).returncode != 0:
        if _git("rev-parse", "--verify", "--quiet", f"origin/{base_ref}", cwd=str(mirror), check=False).returncode == 0:
            ref = f"origin/{base_ref}"
    _git("worktree", "add", "-B", f"hiveswarm/{task_id}", str(wt), ref, cwd=str(mirror))
    return wt


def _commit_and_push(mirror: Path, wt: Path, task_id: str) -> tuple[str | None, str]:
    _git("add", "-A", cwd=str(wt))
    status = _git("status", "--porcelain", cwd=str(wt)).stdout.strip()
    if not status:
        return None, ""
    _git("-c", "user.name=hiveswarm", "-c", "user.email=hiveswarm@localhost",
         "commit", "-q", "-m", f"hiveswarm: {task_id}", cwd=str(wt))
    branch = f"hiveswarm/{task_id}"
    _git("push", "-f", "origin", f"{branch}:{branch}", cwd=str(wt))
    stat = _git("diff", "--stat", "HEAD~1", cwd=str(wt), check=False).stdout.strip()
    return branch, stat


def _heartbeat_loop(client: Client, agent_id: str, task_id: str, stop: threading.Event,
                    cancel: threading.Event) -> None:
    while not stop.wait(15):
        try:
            out = client.post("/heartbeat", {"agent_id": agent_id, "task_id": task_id}, timeout=15)
            if isinstance(out, dict) and out.get("cancelled"):
                log.info("task %s cancelled from daemon; stopping %s", task_id, agent_id)
                cancel.set()
                return
        except Exception as e:
            log.warning("heartbeat failed: %s", e)


def _session_of(task: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(task.get("session") or "{}")
    except Exception:
        return {}


def run_session_task(client: Client, cfg: dict[str, Any], agent_id: str, claim: dict[str, Any]) -> None:
    task = claim["task"]
    aid = claim["attempt_id"]
    sess = _session_of(task)
    root = Path(os.path.expanduser(cfg.get("root", "~/.hiveswarm"))).resolve()
    hub_ssh = cfg.get("hub_ssh") or cfg.get("spark_ssh") or claim.get("hub_ssh") or claim.get("spark_ssh") or ""
    remote = f"{hub_ssh}:{task['repo_path']}" if hub_ssh else task["repo_path"]
    acfg = (cfg.get("agents") or {}).get(agent_id, {})
    if SESSION_HOST is None:
        client.post("/complete", {"task_id": task["id"], "attempt_id": aid, "outcome": "error",
                                  "log": "this worker has no tmux; interactive sessions need tmux installed"})
        return
    stop = threading.Event()
    cancel = adapters.cancel_event()
    cancel.clear()
    ship = LogShipper(client, task["id"], agent_id)
    hb = threading.Thread(target=_heartbeat_loop, args=(client, agent_id, task["id"], stop, cancel), daemon=True)
    hb.start()
    live_ref: dict[str, Any] = {}
    try:
        with _mirror_lock(task["project"]):
            mirror = _ensure_mirror(root, task["project"], remote)
            wt = _worktree(root, mirror, task["id"], task["base_ref"], keep=bool(sess.get("resume")))
        log.info("session %s: %s starting in %s", task["id"], agent_id, wt)
        watch_stop = threading.Event()
        threading.Thread(target=adapters.watch_changes, args=(str(wt), ship.emit, watch_stop), daemon=True).start()
        try:
            outcome = SESSION_HOST.run_session(agent_id, acfg, task, sess, aid, wt, mirror, ship, cancel)
        finally:
            watch_stop.set()
        live_ref["win"] = (sess.get("tmux") or "")
        log.info("session %s: %s -> %s", task["id"], agent_id, outcome)
        _end_session(client, task, aid, agent_id, outcome, mirror, wt, ship)
    except Exception as e:
        log.error("session %s: crashed: %s", task["id"], e)
        ship.emit("status", f"session host crashed: {e}")
        ship.close()
        try:
            client.post("/complete", {"task_id": task["id"], "attempt_id": aid, "outcome": "error", "log": str(e)[:2000]})
        except Exception:
            pass
    finally:
        stop.set()
        ship.close()


def _end_session(client: Client, task: dict[str, Any], aid: str, agent_id: str, outcome: str,
                 mirror: Path, wt: Path, ship: LogShipper) -> None:
    win = None
    lead = False
    try:
        sess_now = client.get(f"/sessions/{task['id']}")["session"] or {}
        win = sess_now.get("tmux")
        lead = bool(sess_now.get("lead"))
    except Exception:
        pass
    if outcome == "cancelled":
        ship.emit("status", "stopped (cancelled)")
        ship.close()
        if win:
            kill_window(win)
        return
    if outcome == "handoff":
        with _mirror_lock(task["project"]):
            branch, stat = _commit_and_push(mirror, wt, task["id"])
        ship.emit("status", f"handing off; saved {branch}" if branch else "handing off; no changes to save")
        ship.close()
        client.post(f"/sessions/{task['id']}/event", {"kind": "handed_off", "branch": branch, "host": socket.gethostname()})
        if win:
            kill_window(win)
        return
    if outcome == "exited":
        ship.emit("status", "agent exited with an error; worktree kept for retry")
        ship.close()
        client.post("/complete", {"task_id": task["id"], "attempt_id": aid, "outcome": "error",
                                  "log": "interactive agent exited with a non-zero code"})
        if win:
            kill_window(win)
        return
    with _mirror_lock(task["project"]):
        branch, stat = _commit_and_push(mirror, wt, task["id"])
    if not branch and lead:
        ship.emit("status", "lead session finished")
        ship.close()
        client.post("/complete", {"task_id": task["id"], "attempt_id": aid, "outcome": "pass",
                                  "log": "lead session finished (it dispatches work; it does not produce a branch itself)"})
    elif not branch:
        ship.emit("status", "finished, but the session made no changes")
        ship.close()
        client.post("/complete", {"task_id": task["id"], "attempt_id": aid, "outcome": "fail",
                                  "log": "session finished but made no changes"})
    else:
        ship.emit("status", f"finished; pushed {branch}: {stat.splitlines()[-1].strip() if stat else ''}")
        ship.close()
        out = client.post("/complete", {"task_id": task["id"], "attempt_id": aid, "outcome": "pass",
                                        "branch": branch, "diff_stat": stat, "log": "interactive session finished"},
                          timeout=900)
        log.info("session %s: pushed %s -> %s", task["id"], branch, (out or {}).get("state"))
    if win:
        kill_window(win)


def run_one(client: Client, cfg: dict[str, Any], agent_id: str, claim: dict[str, Any]) -> None:
    task = claim["task"]
    if task.get("kind") == "session":
        run_session_task(client, cfg, agent_id, claim)
        return
    aid = claim["attempt_id"]
    handoff = claim.get("handoff", "")
    try:
        from .. import directives as _directives
        block = _directives.prompt_block(client.get("/directives", for_task=task["id"]) or [])
        if block:
            handoff = block + "\n" + handoff
    except Exception as e:
        log.debug("directives for %s: %s", task["id"][:8], e)
    root = Path(os.path.expanduser(cfg.get("root", "~/.hiveswarm"))).resolve()
    hub_ssh = cfg.get("hub_ssh") or cfg.get("spark_ssh") or claim.get("hub_ssh") or claim.get("spark_ssh") or ""
    remote = f"{hub_ssh}:{task['repo_path']}" if hub_ssh else task["repo_path"]
    adapter_cfg = (cfg.get("agents") or {}).get(agent_id, {})
    adapter = adapters.ADAPTERS[adapter_cfg.get("adapter", agent_id)]

    stop = threading.Event()
    cancel = adapters.cancel_event()
    cancel.clear()
    ship = LogShipper(client, task["id"], agent_id)
    hb = threading.Thread(target=_heartbeat_loop, args=(client, agent_id, task["id"], stop, cancel), daemon=True)
    hb.start()
    started = time.monotonic()
    try:
        with _mirror_lock(task["project"]):
            mirror = _ensure_mirror(root, task["project"], remote)
            wt = _worktree(root, mirror, task["id"], task["base_ref"])
        log.info("task %s: %s started in %s", task["id"], agent_id, wt)
        ship.emit("status", f"started on {socket.gethostname()} in {str(wt).replace(str(Path.home()), '~', 1)}")
        watch_stop = threading.Event()
        threading.Thread(target=adapters.watch_changes, args=(str(wt), ship.emit, watch_stop), daemon=True).start()
        try:
            res = adapter(task, str(wt), handoff, adapter_cfg, emit=ship.emit)
        finally:
            watch_stop.set()
        secs = time.monotonic() - started
        if res.get("outcome") == "cancelled" or cancel.is_set():
            log.info("task %s: %s cancelled; discarding", task["id"], agent_id)
            ship.emit("status", "stopped (cancelled)")
            return
        if not res.get("ok"):
            log.warning("task %s: %s reported %s after %.0fs", task["id"], agent_id, res.get("outcome"), secs)
            ship.emit("status", f"exited with {res.get('outcome')} after {secs:.0f}s")
            ship.close()
            client.post("/complete", {"task_id": task["id"], "attempt_id": aid, "outcome": res.get("outcome", "error"),
                                      "log": res.get("log"), "tokens_in": res.get("tokens_in"),
                                      "tokens_out": res.get("tokens_out")})
            return
        ship.emit("status", f"finished in {secs:.0f}s; committing and pushing")
        with _mirror_lock(task["project"]):
            branch, stat = _commit_and_push(mirror, wt, task["id"])
        if not branch:
            log.warning("task %s: %s produced no changes", task["id"], agent_id)
            ship.emit("status", "finished but made no changes")
            ship.close()
            client.post("/complete", {"task_id": task["id"], "attempt_id": aid, "outcome": "fail",
                                      "log": "agent finished but made no changes\n" + (res.get("log") or "")[-2000:],
                                      "tokens_in": res.get("tokens_in"), "tokens_out": res.get("tokens_out")})
            return
        ship.emit("status", f"pushed {branch}: {stat.splitlines()[-1].strip() if stat else ''}")
        ship.close()
        out = client.post("/complete", {"task_id": task["id"], "attempt_id": aid, "outcome": "pass",
                                        "branch": branch, "diff_stat": stat, "log": (res.get("log") or "")[-2000:],
                                        "tokens_in": res.get("tokens_in"), "tokens_out": res.get("tokens_out")},
                          timeout=900)
        log.info("task %s: %s pushed %s -> %s", task["id"], agent_id, branch, (out or {}).get("state"))
    except Exception as e:
        log.error("task %s: %s crashed: %s", task["id"], agent_id, e)
        ship.emit("status", f"worker crashed: {e}")
        ship.close()
        try:
            client.post("/complete", {"task_id": task["id"], "attempt_id": aid, "outcome": "error", "log": str(e)[:2000]})
        except Exception:
            pass
    finally:
        stop.set()
        ship.close()
        if cfg.get("keep_worktrees", False) is False:
            wt_path = root / "worktrees" / task["id"]
            with _mirror_lock(task["project"]):
                _git("worktree", "remove", "--force", str(wt_path), cwd=str(root / "mirrors" / task["project"]), check=False)


def _adopt_sessions(client: Client, cfg: dict[str, Any], root: Path, host: str) -> None:
    try:
        rows = client.get("/sessions", limit=200)
    except Exception as e:
        log.warning("could not list sessions to re-adopt: %s", e)
        return
    for r in rows or []:
        sess = r.get("session") or {}
        if sess.get("host") != host or r.get("state") not in ("claimed", "running"):
            continue
        win = sess.get("tmux")
        tid = r["id"]
        if not win or not window_alive(win):
            log.info("session %s: tmux window gone; reporting exit", tid[:8])
            try:
                client.post(f"/sessions/{tid}/event", {"kind": "exited", "exit_code": 1, "host": host,
                                                      "summary": "worker restarted and the tmux window was gone"})
            except Exception:
                pass
            continue
        mirror = root / "mirrors" / r["project"]
        wt = root / "worktrees" / tid
        agent = sess.get("agent") or r.get("claimed_by") or "agent"
        aid = ""
        try:
            attempts = client.get(f"/sessions/{tid}").get("attempts") or []
            open_ = [x for x in attempts if not x.get("ended_at")]
            aid = (open_ or attempts or [{}])[-1].get("id") or ""
        except Exception:
            pass

        def _run(task=dict(r), sess=sess, mirror=mirror, wt=wt, agent=agent, aid=aid):
            stop = threading.Event()
            cancel = adapters.cancel_event()
            cancel.clear()
            ship = LogShipper(client, task["id"], agent)
            hb = threading.Thread(target=_heartbeat_loop, args=(client, agent, task["id"], stop, cancel), daemon=True)
            hb.start()
            try:
                outcome = SESSION_HOST.adopt(task, sess, str(wt), str(mirror), ship, cancel)
                _end_session(client, task, aid, agent, outcome, mirror, wt, ship)
            finally:
                stop.set()
                ship.close()

        threading.Thread(target=_run, daemon=True, name=f"adopt-{tid[:8]}").start()
        log.info("session %s: re-adopted tmux window %s", tid[:8], win)


def _register(client: Client, body: dict[str, Any]) -> dict[str, Any]:
    """Register an agent, waiting for the hub if it is not reachable yet (a laptop that just woke up, a Wi-Fi
    reconnect, the hub rebooting) instead of exiting. Returns the daemon's answer (it carries `desired_capacity`)."""
    delay = 2.0
    while True:
        try:
            out = client.post("/register", body, timeout=30)
            return out if isinstance(out, dict) else {}
        except Exception as e:
            log.warning("hub not reachable to register %s (%s); retrying in %.0f s", body["agent_id"], str(e)[:120], delay)
            time.sleep(delay)
            delay = min(delay * 2, 60.0)


CLAIM_WAIT = 20  # seconds the daemon holds an idle claim before answering "nothing"; keeps idle traffic to ~3 requests/min/lane


def _lane(client: Client, cfg: dict[str, Any], agent_id: str, poll: int, once: bool,
          retire: threading.Event | None = None) -> None:
    """One polling lane. `retire` (set by the lane manager when capacity shrinks) ends the loop at the next idle
    moment: a task that was already claimed still runs to completion."""
    wait = 0 if once else int(cfg.get("claim_wait_seconds", CLAIM_WAIT))
    retire = retire or threading.Event()
    while not retire.is_set():
        t0 = time.monotonic()
        try:
            claim = client.post("/claim", {"agent_id": agent_id, "wait": wait}, timeout=wait + 30)
        except Exception as e:
            log.warning("claim failed for %s: %s", agent_id, e)
            claim = None
            if once:
                return
            retire.wait(poll)
            continue
        if claim:
            run_one(client, cfg, agent_id, claim)
            if once:
                return
            continue
        if once:
            return
        held = time.monotonic() - t0
        # A daemon from before long-polling answers at once; fall back to plain polling instead of hammering it.
        retire.wait(1.0 if (wait and held >= wait / 2) else poll)


class LaneManager:
    """Keeps each agent at its target number of lanes while the worker runs, so the count can be changed from the
    app (POST /agents/{id}/capacity) without a restart. Growing starts threads; shrinking retires the newest lanes,
    each of which finishes its current task before exiting. Every change is re-registered with the daemon."""

    def __init__(self, client: Client, cfg: dict[str, Any], host: str, poll: int, once: bool):
        self.client = client
        self.cfg = cfg
        self.host = host
        self.poll = poll
        self.once = once
        self.lanes: dict[str, list[tuple[threading.Thread, threading.Event]]] = {}
        self.meta: dict[str, dict[str, Any]] = {}  # agent_id -> {"caps": [...], "provider": str, "configured": int}
        self.lock = threading.Lock()

    def add_agent(self, agent_id: str, caps: list[str], provider: str, configured: int) -> None:
        self.meta[agent_id] = {"caps": caps, "provider": provider, "configured": configured}
        self.lanes.setdefault(agent_id, [])

    def count(self, agent_id: str) -> int:
        return sum(1 for t, r in self.lanes.get(agent_id, []) if t.is_alive() and not r.is_set())

    def register(self, agent_id: str) -> dict[str, Any]:
        m = self.meta[agent_id]
        return _register(self.client, {"agent_id": agent_id, "host": self.host, "capabilities": m["caps"],
                                       "capacity": self.count(agent_id), "provider": m["provider"]})

    def set_target(self, agent_id: str, target: int) -> None:
        target = max(0, min(int(target), 32))
        with self.lock:
            live = [(t, r) for t, r in self.lanes.get(agent_id, []) if t.is_alive() and not r.is_set()]
            while len(live) < target:
                r = threading.Event()
                i = len(live)
                t = threading.Thread(target=_lane, args=(self.client, self.cfg, agent_id, self.poll, self.once, r),
                                     name=f"lane-{agent_id}-{i}", daemon=True)
                t.start()
                live.append((t, r))
                time.sleep(0.2)
            while len(live) > target:
                t, r = live.pop()
                r.set()
            self.lanes[agent_id] = live

    def apply(self, agent_id: str, desired: int | None, announce: bool = True) -> None:
        target = int(desired) if desired is not None else int(self.meta[agent_id]["configured"])
        before = self.count(agent_id)
        if before == target:
            return
        self.set_target(agent_id, target)
        self.register(agent_id)
        if announce:
            log.info("%s: %d → %d lanes (%s)", agent_id, before, target,
                     "set from the app" if desired is not None else "back to worker.toml")

    def watch(self, stop: threading.Event, every: float = 10.0) -> None:
        """Poll the daemon for desired capacities; one small request every `every` seconds."""
        while not stop.wait(every):
            try:
                rows = self.client.get("/agents", timeout=20) or []
            except Exception:
                continue
            for row in rows:
                aid = row.get("agent_id")
                if aid in self.meta:
                    self.apply(aid, row.get("desired_capacity"))

    def threads(self) -> list[threading.Thread]:
        return [t for ls in self.lanes.values() for t, _ in ls]


def main() -> None:
    p = argparse.ArgumentParser(prog="hiveswarm-worker")
    p.add_argument("--config")
    p.add_argument("--agent", action="append", help="agent id to serve (repeatable); default: all with a binary on PATH")
    p.add_argument("--once", action="store_true")
    a = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    cfg = _load_cfg(a.config)
    extra = adapters.load_plugins()
    if extra:
        log.info("adapter plugins: %s", ", ".join(extra))
    client = Client(cfg["daemon_url"], cfg.get("token") or os.environ.get("HIVESWARM_TOKEN"))
    host = socket.gethostname()
    poll = int(cfg.get("poll_seconds", 10))

    root = Path(os.path.expanduser(cfg.get("root", "~/.hiveswarm"))).resolve()
    root.mkdir(parents=True, exist_ok=True)
    wrapper = cfg.get("sandbox_wrapper")
    if wrapper:
        wpath = os.path.expanduser(wrapper)
        if not (os.path.isfile(wpath) and os.access(wpath, os.X_OK)):
            log.error("sandbox_wrapper %s is missing or not executable; refusing to run agents unisolated", wpath)
            sys.exit(1)
        if not shutil.which("bwrap"):
            log.error("sandbox_wrapper is set but bwrap is not installed (sudo apt install -y bubblewrap)")
            sys.exit(1)
        os.environ["HM_WORK_ROOT"] = str(root)
        adapters.SANDBOX = [wpath]
        log.info("isolation on: agents can write only %s/worktrees/<task> (wrapper %s)", root, wpath)
    else:
        log.warning("isolation off: agents run with your full user permissions (set sandbox_wrapper in worker.toml)")

    if shutil.which("claude"):
        from . import claude_auth
        st = claude_auth.status((cfg.get("agents") or {}).get("claude_code") or {})
        if st["token"]:
            log.info("claude_code: signed in for every lane (token from %s)", st["token_source"] == "env" and "CLAUDE_CODE_OAUTH_TOKEN" or st["token_file"])
        elif st["login_in_config_dir"]:
            log.info("claude_code: using the login stored in %s (hm login claude gives lanes a one-year token instead)", st["config_dir"])
        else:
            log.warning("claude_code: no sign-in for lanes — sessions will stop at Claude's login screen; run: hm login claude")

    global SESSION_HOST
    if shutil.which("tmux"):
        try:
            SESSION_HOST = SessionHost(client, cfg, root, host, int(cfg.get("hook_port", 7791)))
            SESSION_HOST.start()
            _sh = SESSION_HOST
            adapters.HOOK_SETTINGS = lambda tid: hook_settings(_sh.script, _sh.hook_port, tid, headless=True)
        except Exception as e:
            log.warning("session host disabled: %s", e)
            SESSION_HOST = None
    else:
        log.warning("tmux not found; interactive sessions disabled on this machine (install tmux)")

    wanted = a.agent or list((cfg.get("agents") or {}).keys()) or list(adapters.ADAPTERS.keys())
    serving: list[str] = []
    manager = LaneManager(client, cfg, host, poll, a.once)
    for agent_id in wanted:
        acfg = (cfg.get("agents") or {}).get(agent_id, {})
        if acfg.get("enabled", True) is False:
            continue
        adapter_name = acfg.get("adapter", agent_id)
        if adapter_name not in adapters.ADAPTERS:
            log.warning("skipping %s: unknown adapter %r (available: %s)", agent_id, adapter_name, ", ".join(sorted(adapters.ADAPTERS)))
            continue
        binary = acfg.get("binary") or adapters.BINARIES.get(adapter_name, agent_id)
        if not shutil.which(binary):
            log.warning("skipping %s: %s not on PATH", agent_id, binary)
            continue
        caps = list(adapters.CAPABILITIES.get(acfg.get("adapter", agent_id), []))
        if SESSION_HOST is not None:
            caps.append("sessions")
        cap = max(0, int(acfg.get("concurrency", 1)))
        manager.add_agent(agent_id, caps, adapter_name, cap)
        ans = _register(client, {"agent_id": agent_id, "host": host, "capabilities": caps, "capacity": cap, "provider": adapter_name})
        desired = ans.get("desired_capacity")
        if desired is not None and int(desired) != cap:
            log.info("%s: %d lanes in worker.toml, %d set from the app; using %d", agent_id, cap, int(desired), int(desired))
            cap = int(desired)
        manager.meta[agent_id]["start"] = cap
        serving.append(agent_id)
        log.info("registered %s ×%d (%s)", agent_id, cap, ", ".join(caps))

    if not serving:
        log.error("no agents available; nothing to do")
        return

    if SESSION_HOST is not None:
        _adopt_sessions(client, cfg, root, host)

    total = sum(int(manager.meta[aid]["start"]) for aid in serving)
    log.info("polling %s every %ss; %d parallel lanes: %s", cfg["daemon_url"], poll, total,
             ", ".join(f"{aid}×{manager.meta[aid]['start']}" for aid in serving))
    from .local_copy import LocalCopies
    copies = LocalCopies(client, cfg, host)
    if copies.enabled and not a.once:
        log.info("local copies of every project under %s (fast-forwarded when the hub's branch moves)", copies.root)
        threading.Thread(target=copies.loop, kwargs={"every": float(cfg.get("local_sync_seconds", 60))},
                         name="local-copies", daemon=True).start()
    else:
        log.info("local project copies are off (local_projects = \"\" in worker.toml)")
    def _retire(*_: Any) -> None:
        for agent_id in serving:
            try:
                client.post("/unregister", {"agent_id": agent_id})
            except Exception:
                pass

    def _on_term(*_: Any) -> None:
        log.info("stopping")
        _retire()
        os._exit(0)

    import signal as _signal
    _signal.signal(_signal.SIGTERM, _on_term)
    try:
        for agent_id in serving:
            manager.set_target(agent_id, int(manager.meta[agent_id]["start"]))
            manager.register(agent_id)
        stop_watch = threading.Event()
        if not a.once:
            threading.Thread(target=manager.watch, args=(stop_watch,), name="lane-manager", daemon=True).start()
        if a.once:
            for t in manager.threads():
                t.join()
        else:
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        log.info("stopping")
        _retire()


if __name__ == "__main__":
    main()
