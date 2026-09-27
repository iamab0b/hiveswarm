"""One sign-in for every Claude Code lane.

Lanes run under their own CLAUDE_CONFIG_DIR (default ~/.claude-worker), not your personal ~/.claude, so they
never share your login. Instead the worker hands each lane a long-lived token from `claude setup-token`, kept in
a file it reads at every launch, and pre-writes the first-run and folder-trust answers into that config dir so
no lane ever stops at a theme picker, a login screen, or a "do you trust this folder" dialog.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

TOKEN_FILE = "~/.hiveswarm/claude-token"
DEFAULT_CONFIG_DIR = "~/.claude-worker"
TOKEN_PREFIX = "sk-ant-"


def config_dir(acfg: dict[str, Any] | None = None) -> str:
    return os.path.expanduser((acfg or {}).get("config_dir") or DEFAULT_CONFIG_DIR)


def token_file(acfg: dict[str, Any] | None = None) -> Path:
    if (acfg or {}).get("oauth_token_file"):
        return Path(os.path.expanduser(acfg["oauth_token_file"]))
    from ..config import home
    p = home() / "claude-token"
    legacy = Path(os.path.expanduser("~/.config/hiveswarm/claude-token"))
    return legacy if (not p.exists() and legacy.exists()) else p


def read_token(acfg: dict[str, Any] | None = None) -> str | None:
    """The worker's own environment wins (a service can set CLAUDE_CODE_OAUTH_TOKEN), then the token file."""
    env = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if env:
        return env
    try:
        tok = token_file(acfg).read_text().strip()
    except OSError:
        return None
    return tok if tok.startswith(TOKEN_PREFIX) else None


def save_token(token: str, acfg: dict[str, Any] | None = None) -> Path:
    token = token.strip()
    if not token.startswith(TOKEN_PREFIX) or len(token) < 40:
        raise ValueError("that does not look like a token from `claude setup-token` (they start with sk-ant-oat)")
    p = token_file(acfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    os.chmod(p, 0o600)
    return p


def auth_env(acfg: dict[str, Any] | None = None) -> dict[str, str]:
    """Environment for a Claude Code launch: its config dir plus the shared token when there is one."""
    env = {"CLAUDE_CONFIG_DIR": config_dir(acfg)}
    tok = read_token(acfg)
    if tok:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    return env


def seed_config(cfg_dir: str, project_dirs: list[str] | None = None, theme: str = "dark") -> None:
    """Write the answers Claude Code would otherwise ask for on first run into <config_dir>/.claude.json:
    onboarding done, a theme, and folder trust for each project directory a lane is about to run in."""
    d = Path(cfg_dir)
    d.mkdir(parents=True, exist_ok=True)
    cfg_path = d / ".claude.json"
    lock_path = d / ".claude.json.hiveswarm-lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data: dict[str, Any] = {}
        try:
            data = json.loads(cfg_path.read_text() or "{}")
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        changed = False
        if not data.get("hasCompletedOnboarding"):
            data["hasCompletedOnboarding"] = True
            changed = True
        if not data.get("theme"):
            data["theme"] = theme
            changed = True
        projects = data.get("projects")
        if not isinstance(projects, dict):
            projects = {}
            data["projects"] = projects
            changed = True
        for raw in project_dirs or []:
            for key in {raw, os.path.realpath(raw)}:
                entry = projects.get(key)
                if not isinstance(entry, dict):
                    entry = {}
                    projects[key] = entry
                    changed = True
                for k in ("hasTrustDialogAccepted", "hasCompletedProjectOnboarding"):
                    if entry.get(k) is not True:
                        entry[k] = True
                        changed = True
        if changed:
            fd, tmp = tempfile.mkstemp(dir=str(d), prefix=".claude.json.", suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, cfg_path)


def forget_projects(cfg_dir: str, project_dirs: list[str]) -> None:
    d = Path(cfg_dir)
    cfg_path = d / ".claude.json"
    if not cfg_path.is_file():
        return
    with open(d / ".claude.json.hiveswarm-lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(cfg_path.read_text() or "{}")
        except (OSError, ValueError):
            return
        projects = data.get("projects") if isinstance(data, dict) else None
        if not isinstance(projects, dict):
            return
        drop = {p for raw in project_dirs for p in (raw, os.path.realpath(raw))}
        if not any(k in projects for k in drop):
            return
        for k in drop:
            projects.pop(k, None)
        fd, tmp = tempfile.mkstemp(dir=str(d), prefix=".claude.json.", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, cfg_path)


def status(acfg: dict[str, Any] | None = None) -> dict[str, Any]:
    tok = read_token(acfg)
    cdir = Path(config_dir(acfg))
    out: dict[str, Any] = {"config_dir": str(cdir), "token_file": str(token_file(acfg)), "token": bool(tok),
                           "token_source": "env" if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") else ("file" if tok else None),
                           "login_in_config_dir": (cdir / ".credentials.json").is_file(),
                           "onboarded": False, "binary": shutil.which("claude")}
    try:
        data = json.loads((cdir / ".claude.json").read_text())
        out["onboarded"] = bool(data.get("hasCompletedOnboarding"))
    except (OSError, ValueError):
        pass
    return out


def check(acfg: dict[str, Any] | None = None, timeout: int = 120) -> tuple[bool, str]:
    """Run one tiny headless turn with the lane's environment to prove the credential works."""
    if not shutil.which("claude"):
        return False, "claude is not on PATH"
    env = {**os.environ, **auth_env(acfg)}
    with tempfile.TemporaryDirectory(prefix="hm-login-") as tmp:
        seed_config(env["CLAUDE_CONFIG_DIR"], [tmp])
        cmd = ["claude", "-p", "Reply with exactly the word OK and nothing else.", "--output-format", "json", "--max-turns", "1"]
        try:
            r = subprocess.run(cmd, cwd=tmp, env=env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, f"claude did not answer within {timeout}s"
        finally:
            try:
                forget_projects(env["CLAUDE_CONFIG_DIR"], [tmp])
            except Exception:
                pass
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip().splitlines()
        return False, (err[-1] if err else f"claude exited {r.returncode}")[:300]
    try:
        body = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return r.returncode == 0, (r.stdout or "").strip()[-200:] or "no output"
    if body.get("is_error"):
        return False, str(body.get("result") or body)[:300]
    return True, f"model answered: {str(body.get('result', '')).strip()[:60] or 'ok'}"
