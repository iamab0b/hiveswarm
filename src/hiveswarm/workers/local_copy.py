"""Every project's files live where you work.

The hub keeps the canonical repository (agents push branches there; merges happen there), and this keeps a normal
git clone of each project under ~/hiveswarm/<project> on the worker machine, fast-forwarded whenever the hub's branch
moves — so the files you interact with are always local and current. On a single machine the hub is this machine
and the copy is simply a second checkout. Your own edits are never touched: a dirty
checkout or local commits are reported, not overwritten, and `git push` from that folder lands on the hub.
When a project is deleted with its files, the copy is moved to ~/hiveswarm/.trash rather than removed.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("hiveswarm.local")

DEFAULT_ROOT = "~/hiveswarm"


def _git(*args: str, cwd: str | None = None, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def windows_path(path: str) -> str | None:
    if not is_wsl():
        return None
    r = subprocess.run(["wslpath", "-w", path], capture_output=True, text=True, timeout=10)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def open_folder(path: str) -> bool:
    """Reveal a folder in the user's file manager: Explorer from WSL, the desktop's opener elsewhere."""
    if is_wsl() and shutil.which("explorer.exe"):
        win = windows_path(path)
        if win:
            subprocess.Popen(["explorer.exe", win], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    for opener in ("xdg-open", "open", "wslview"):
        if shutil.which(opener):
            subprocess.Popen([opener, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    return False


class LocalCopies:
    def __init__(self, client: Any, cfg: dict[str, Any], host: str | None = None):
        self.client = client
        self.cfg = cfg
        self.host = host or socket.gethostname()
        raw = cfg.get("local_projects", DEFAULT_ROOT)
        self.enabled = bool(raw)
        self.root = Path(os.path.expanduser(raw or DEFAULT_ROOT)).resolve()
        self.hub_ssh = cfg.get("hub_ssh") or cfg.get("spark_ssh") or ""
        self._state_path = self.root / ".hiveswarm-sync.json"
        self._state: dict[str, Any] = {"heads": {}, "tombstones": {}}
        self._lock = threading.Lock()
        self._load_state()

    # ── state ────────────────────────────────────────────────────────────

    def _load_state(self) -> None:
        try:
            self._state = json.loads(self._state_path.read_text())
        except Exception:
            self._state = {"heads": {}, "tombstones": {}}

    def _save_state(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(self._state, indent=1))
        except OSError as e:
            log.debug("could not save sync state: %s", e)

    # ── one project ──────────────────────────────────────────────────────

    def path_for(self, name: str) -> Path:
        return self.root / name

    def sync_one(self, name: str, proj: dict[str, Any], force: bool = False) -> dict[str, Any]:
        repo = proj.get("repo_path")
        if not repo:
            return {"ok": False, "note": "project has no repo_path"}
        remote = f"{self.hub_ssh}:{repo}" if self.hub_ssh else repo
        path = self.path_for(name)
        branch = proj.get("branch") or "main"
        hub_head = proj.get("head")
        note: str | None = None
        with self._lock:
            fresh = not (path / ".git").exists()
            if fresh:
                self.root.mkdir(parents=True, exist_ok=True)
                r = _git("clone", "--branch", branch, remote, str(path))
                if r.returncode != 0:
                    r = _git("clone", remote, str(path))
                if r.returncode != 0:
                    err = (r.stderr or "").strip().splitlines()
                    note = "clone failed: " + (err[-1] if err else "unknown error")[:200]
                    self._report(name, path, None, False, 0, 0, note)
                    return {"ok": False, "note": note}
                note = "copied to this machine"
            else:
                known = self._state["heads"].get(name)
                if not force and hub_head and known == hub_head and time.time() - float(self._state.get("checked", {}).get(name, 0)) < 900:
                    return {"ok": True, "note": "up to date", "skipped": True}
                r = _git("fetch", "--prune", "origin", cwd=str(path))
                if r.returncode != 0:
                    note = "fetch failed: " + ((r.stderr or "").strip().splitlines() or ["?"])[-1][:200]
            dirty = bool(_git("status", "--porcelain", cwd=str(path)).stdout.strip())
            cur = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=str(path)).stdout.strip() or branch
            ahead = behind = 0
            r = _git("rev-list", "--left-right", "--count", f"HEAD...origin/{branch}", cwd=str(path))
            if r.returncode == 0 and r.stdout.strip():
                a, b = r.stdout.split()
                ahead, behind = int(a), int(b)
            if not fresh and note is None:
                if cur != branch:
                    note = f"you are on branch {cur}; the hub's {branch} is not applied here until you switch back"
                elif dirty:
                    note = "you have uncommitted changes here; the hub's new commits will be applied once they are committed or stashed" if behind else "you have uncommitted changes here"
                elif ahead and behind:
                    note = f"you have {ahead} local commit{'s' if ahead != 1 else ''} and the hub has {behind} new — pull with rebase, then push"
                elif ahead:
                    note = f"{ahead} local commit{'s' if ahead != 1 else ''} not on the hub yet — git push when ready"
                elif behind:
                    r = _git("merge", "--ff-only", f"origin/{branch}", cwd=str(path))
                    if r.returncode == 0:
                        note = f"updated with {behind} new commit{'s' if behind != 1 else ''} from the hub"
                        behind = 0
                    else:
                        note = "could not fast-forward: " + ((r.stderr or "").strip().splitlines() or ["?"])[-1][:160]
                else:
                    note = "up to date"
            head = _git("rev-parse", "HEAD", cwd=str(path)).stdout.strip() or None
            self._state["heads"][name] = hub_head or head
            self._state.setdefault("checked", {})[name] = time.time()
            self._save_state()
        self._report(name, path, head, dirty, ahead, behind, note)
        return {"ok": True, "note": note, "head": head, "dirty": dirty, "ahead": ahead, "behind": behind, "path": str(path)}

    def _report(self, name: str, path: Path, head: str | None, dirty: bool, ahead: int, behind: int, note: str | None) -> None:
        try:
            self.client.post(f"/projects/{name}/local", {"host": self.host, "path": str(path), "win_path": windows_path(str(path)),
                                                          "head": head, "dirty": dirty, "ahead": ahead, "behind": behind, "note": note}, timeout=15)
        except Exception as e:
            log.debug("local copy report for %s failed: %s", name, e)

    # ── all projects + tombstones ─────────────────────────────────────────

    def sync_all(self, force: bool = False) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        try:
            projects = self.client.get("/projects", timeout=30) or {}
        except Exception as e:
            log.debug("could not list projects: %s", e)
            return out
        for name, proj in projects.items():
            try:
                out[name] = self.sync_one(name, proj, force=force)
            except Exception as e:
                log.warning("local copy of %s: %s", name, e)
                out[name] = {"ok": False, "note": str(e)[:200]}
        self._handle_tombstones(set(projects))
        return out

    def _handle_tombstones(self, live: set[str]) -> None:
        try:
            stones = self.client.get("/projects/tombstones", timeout=15) or []
        except Exception:
            return
        seen = self._state.setdefault("tombstones", {})
        changed = False
        for st in stones:
            name, at = st.get("name"), st.get("at")
            if not name or name in live or seen.get(name) == at:
                continue
            seen[name] = at
            changed = True
            path = self.path_for(name)
            if st.get("purge") and (path / ".git").exists():
                trash = self.root / ".trash"
                trash.mkdir(parents=True, exist_ok=True)
                dest = trash / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}"
                shutil.move(str(path), str(dest))
                log.info("project %s was deleted with its files: moved %s to %s", name, path, dest)
            elif (path / ".git").exists():
                log.info("project %s was removed from Hiveswarm; its copy stays at %s", name, path)
            self._state["heads"].pop(name, None)
        if changed:
            self._save_state()

    def loop(self, stop: threading.Event | None = None, every: float = 60.0) -> None:
        stop = stop or threading.Event()
        while not stop.is_set():
            try:
                self.sync_all()
            except Exception as e:
                log.warning("local copy sync failed: %s", e)
            stop.wait(every)
