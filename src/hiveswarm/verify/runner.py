from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

from ..config import load


def _docker_usable() -> bool:
    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=20)
    return r.returncode == 0


def run(task: Any, wt: str) -> dict[str, Any]:
    """Run the task's acceptance command against its worktree.

    verify.mode = "docker": inside the project's verify image, on its own network, as an unprivileged user (the
    default when docker works). "local": as a plain subprocess in the worktree — the single-machine default when
    docker is absent; it trusts the acceptance command as much as you trust the agent that made the change.
    "auto" picks docker when it is usable, local otherwise.
    """
    cfg = load()
    acceptance = task["acceptance"]
    if not acceptance:
        return {"outcome": "soft", "log": "no acceptance command; soft-verified", "weight": 0.0}

    proj = cfg.project(task["project"])
    timeout = int(cfg.get("verify.timeout_seconds", 600))
    mode = str(proj.get("verify_mode") or cfg.get("verify.mode", "auto"))
    if mode == "auto":
        mode = "docker" if _docker_usable() else "local"

    if mode == "local":
        env = {**os.environ, "HOME": os.environ.get("HOME", "/tmp"), "PYTHONDONTWRITEBYTECODE": "1",
               "PYTEST_ADDOPTS": "-p no:cacheprovider", "HIVESWARM_VERIFY": "local"}
        cmd = ["sh", "-c", acceptance]
        cwd = wt
    else:
        image = proj.get("verify_image") or cfg.get("verify.default_image", "python:3.12-slim")
        network = proj.get("verify_network") or cfg.get("verify.network", "bridge")
        memory = cfg.get("verify.memory", "4g")
        env = None
        cwd = None
        cmd = [
            "docker", "run", "--rm",
            "--network", network,
            "--memory", memory,
            "--cpus", "4",
            "--security-opt", "no-new-privileges:true",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "-e", "HOME=/tmp",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-e", "PYTEST_ADDOPTS=-p no:cacheprovider",
            "-v", f"{wt}:/work",
            "-w", "/work",
            image,
            "sh", "-c", acceptance,
        ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
    except subprocess.TimeoutExpired as e:
        log = (e.stdout or "") + "\n" + (e.stderr or "")
        return {"outcome": "timeout", "log": f"[{mode}] " + log[-4000:], "weight": 1.0}

    log = (r.stdout or "") + "\n" + (r.stderr or "")
    outcome = "pass" if r.returncode == 0 else "fail"
    return {"outcome": outcome, "log": f"[verify: {mode}]\n" + log[-4000:], "weight": 1.0, "exit": r.returncode}
