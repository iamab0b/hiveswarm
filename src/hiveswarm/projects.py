"""Projects as a whole: their current commit, where each worker keeps its copy, deleting one, and the tombstones
a worker reads to tidy up its copy after a project is gone."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from . import db, worktree
from .config import config_path, load, load_fresh

log = logging.getLogger("hiveswarm.projects")


def head(repo_path: str | None) -> str | None:
    if not repo_path or not os.path.isdir(repo_path):
        return None
    r = subprocess.run(["git", "-C", repo_path, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
    return r.stdout.strip() if r.returncode == 0 else None


def branch(repo_path: str | None) -> str | None:
    if not repo_path or not os.path.isdir(repo_path):
        return None
    r = subprocess.run(["git", "-C", repo_path, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=10)
    return r.stdout.strip() if r.returncode == 0 else None


def listing() -> dict[str, dict[str, Any]]:
    try:
        cfg = load_fresh()
    except Exception:
        cfg = load()
    out: dict[str, dict[str, Any]] = {}
    local = {r["project"]: dict(r) for r in db.all_("SELECT * FROM project_local")}
    for name, p in (cfg.raw.get("projects", {}) or {}).items():
        d = dict(p)
        d["head"] = head(p.get("repo_path"))
        d["branch"] = branch(p.get("repo_path"))
        lp = local.get(name)
        if lp:
            lp["dirty"] = bool(lp.get("dirty"))
            d["local"] = lp
        out[name] = d
    return out


def record_local(name: str, host: str, path: str, win_path: str | None, head_: str | None, dirty: bool,
                 ahead: int, behind: int, note: str | None) -> None:
    db.run("""INSERT INTO project_local (project, host, path, win_path, head, synced_at, dirty, ahead, behind, note)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
              ON CONFLICT(project) DO UPDATE SET host=excluded.host, path=excluded.path, win_path=excluded.win_path,
                head=excluded.head, synced_at=excluded.synced_at, dirty=excluded.dirty, ahead=excluded.ahead,
                behind=excluded.behind, note=excluded.note""",
           name, host, path, win_path, head_, db.now(), 1 if dirty else 0, ahead, behind, note)


def tombstones() -> list[dict[str, Any]]:
    return [dict(r) for r in db.all_("SELECT * FROM project_tombstones ORDER BY at DESC LIMIT 50")]


def _remove_config_section(name: str) -> bool:
    path = config_path()
    text = path.read_text()
    m = re.search(r"^\[projects\." + re.escape(name) + r"\][ \t]*\n", text, re.M)
    if not m:
        return False
    nxt = re.search(r"^\[", text[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(text)
    new = (text[:m.start()].rstrip("\n") + "\n\n" + text[end:].lstrip("\n")).rstrip("\n") + "\n"
    tomllib.loads(new)
    path.write_text(new)
    load.cache_clear()
    return True


def delete(name: str, purge: bool = False) -> dict[str, Any]:
    """Remove a project from Hiveswarm: its tasks, sessions, logs, flags and standing orders, the worktrees on the
    hub, and its config entry. With purge, the repository on the hub goes too (only when it sits under the
    configured projects root, so a repo someone pointed Hiveswarm at is never destroyed); each worker moves its own
    copy to a trash folder when it reads the tombstone."""
    from .daemon import dispatcher
    cfg = load_fresh()
    proj = (cfg.raw.get("projects") or {}).get(name)
    if proj is None:
        raise KeyError(name)
    repo_path = proj.get("repo_path")
    tasks = db.all_("SELECT * FROM tasks WHERE project = ?", name)
    cancelled = 0
    for t in tasks:
        if t["state"] not in ("done", "failed", "abandoned"):
            try:
                dispatcher.kill_running(t["id"])
            except Exception:
                pass
            db.task_set_state(t["id"], "abandoned", claimed_by=None, lease_expires=None)
            cancelled += 1
    for t in tasks:
        try:
            if t["worktree"] and repo_path:
                worktree.remove(t["id"], repo_path)
        except Exception as e:
            log.debug("worktree cleanup for %s: %s", t["id"], e)
        db.task_delete(t["id"])
    db.run("DELETE FROM directives WHERE project = ?", name)
    db.run("DELETE FROM project_local WHERE project = ?", name)
    removed_repo = False
    if purge and repo_path:
        root = Path(os.path.expanduser(cfg.get("paths.projects_root", "~/repos"))).resolve()
        rp = Path(repo_path).resolve()
        if rp != root and root in rp.parents and rp.is_dir():
            shutil.rmtree(rp, ignore_errors=True)
            removed_repo = not rp.exists()
        else:
            log.warning("not deleting %s: it is outside the projects root %s", rp, root)
    _remove_config_section(name)
    db.run("INSERT INTO project_tombstones (name, purge, repo_path, at) VALUES (?, ?, ?, ?) "
           "ON CONFLICT(name) DO UPDATE SET purge=excluded.purge, repo_path=excluded.repo_path, at=excluded.at",
           name, 1 if purge else 0, repo_path, db.now())
    return {"ok": True, "name": name, "tasks_removed": len(tasks), "cancelled": cancelled, "repo_removed": removed_repo,
            "repo_path": repo_path, "purge": purge}
