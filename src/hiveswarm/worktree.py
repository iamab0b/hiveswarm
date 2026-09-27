from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import load


def _git(*args: str, cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120, check=check)


BRANCH_PREFIXES = ("hiveswarm", "hivemind")


def task_branch(task_id: str, repo_path: str) -> str:
    """The task's branch on the hub. Workers from before the rename pushed hivemind/<id>; prefer the current name."""
    for prefix in BRANCH_PREFIXES:
        name = f"{prefix}/{task_id}"
        r = _git("rev-parse", "--verify", "--quiet", f"refs/heads/{name}", cwd=repo_path, check=False)
        if r.returncode == 0:
            return name
    return f"{BRANCH_PREFIXES[0]}/{task_id}"


def create(task_id: str, repo_path: str, base_ref: str) -> str:
    root = Path(load().get("paths.worktrees"))
    root.mkdir(parents=True, exist_ok=True)
    wt = root / task_id
    if wt.exists():
        remove(task_id, repo_path)
    branch = f"hiveswarm/{task_id}"
    r = _git("worktree", "add", "-B", branch, str(wt), base_ref, cwd=repo_path, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"git worktree add {wt} failed: {(r.stderr or r.stdout).strip()[-400:]}")
    return str(wt)


def create_from_branch(task_id: str, repo_path: str, branch: str) -> str:
    root = Path(load().get("paths.worktrees"))
    root.mkdir(parents=True, exist_ok=True)
    wt = root / task_id
    if wt.exists():
        _clear_dir(wt, repo_path)
    r = _git("worktree", "add", "--detach", str(wt), branch, cwd=repo_path, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"git worktree add {wt} {branch} failed: {(r.stderr or r.stdout).strip()[-400:]}")
    return str(wt)


def _clear_dir(wt: Path, repo_path: str) -> None:
    _git("worktree", "remove", "--force", str(wt), cwd=repo_path, check=False)
    if wt.exists():
        shutil.rmtree(wt, ignore_errors=True)
    if wt.exists():
        cfg = load()
        image = cfg.get("verify.default_image", "python:3.12-slim")
        subprocess.run(["docker", "run", "--rm", "--network", "none", "-v", f"{wt.parent}:/p", image,
                        "rm", "-rf", f"/p/{wt.name}"], capture_output=True, timeout=120)
    _git("worktree", "prune", cwd=repo_path, check=False)


def remove(task_id: str, repo_path: str) -> None:
    root = Path(load().get("paths.worktrees"))
    wt = root / task_id
    _clear_dir(wt, repo_path)
    for prefix in BRANCH_PREFIXES:
        _git("branch", "-D", f"{prefix}/{task_id}", cwd=repo_path, check=False)


def diff_stat(wt: str) -> str:
    r = _git("diff", "--stat", "HEAD~1", cwd=wt, check=False)
    if r.returncode != 0 or not r.stdout.strip():
        r = _git("diff", "--stat", "HEAD", cwd=wt, check=False)
    return r.stdout.strip()


def _normalize_patch(patch: str) -> str:
    patch = patch.replace("\r\n", "\n")
    if not patch.endswith("\n"):
        patch += "\n"
    return patch


def apply_patch(wt: str, patch: str) -> tuple[bool, str]:
    p = Path(wt) / ".hiveswarm.patch"
    p.write_text(_normalize_patch(patch))
    strategies = [
        ["--whitespace=nowarn"],
        ["--whitespace=nowarn", "--recount"],
        ["--whitespace=nowarn", "--recount", "--ignore-space-change"],
        ["--whitespace=nowarn", "--recount", "--ignore-space-change", "--unidiff-zero"],
    ]
    last = ""
    for flags in strategies:
        chk = _git("apply", "--check", *flags, str(p), cwd=wt, check=False)
        if chk.returncode == 0:
            ap = _git("apply", *flags, str(p), cwd=wt, check=False)
            p.unlink(missing_ok=True)
            if ap.returncode == 0:
                return True, ""
            return False, ap.stderr.strip()
        last = chk.stderr.strip()
    p.unlink(missing_ok=True)
    return False, last


_JUNK = ["__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/",
         "node_modules/", ".hiveswarm.*", "*.egg-info/", ".coverage", "htmlcov/"]


def _ensure_exclude(wt: str) -> None:
    info = Path(wt) / ".git"
    if info.is_file():
        gitdir = info.read_text().split(":", 1)[1].strip()
        info = Path(wt) / gitdir if not Path(gitdir).is_absolute() else Path(gitdir)
    exclude = info / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text() if exclude.exists() else ""
    missing = [j for j in _JUNK if j not in existing]
    if missing:
        exclude.write_text(existing.rstrip("\n") + "\n" + "\n".join(missing) + "\n")


def commit_all(wt: str, message: str) -> None:
    _ensure_exclude(wt)
    _git("add", "-A", cwd=wt)
    _git("-c", "user.name=hiveswarm", "-c", "user.email=hiveswarm@localhost",
         "commit", "-q", "-m", message, "--allow-empty", cwd=wt, check=False)
