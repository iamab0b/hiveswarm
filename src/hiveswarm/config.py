from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any


def home() -> Path:
    """Where Hiveswarm keeps its state by default: $HIVESWARM_HOME, else ~/.hiveswarm."""
    return Path(os.path.expanduser(os.environ.get("HIVESWARM_HOME") or "~/.hiveswarm"))


def candidate_paths() -> list[str]:
    return [
        os.environ.get("HIVESWARM_CONFIG", ""),
        str(home() / "config.toml"),
        str(Path.home() / ".config" / "hiveswarm" / "config.toml"),
        "/opt/hivemind/config/config.toml",
        str(Path.home() / ".config" / "hivemind" / "config.toml"),
    ]


DEFAULTS: dict[str, Any] = {
    "paths.db": None,           # <home>/db/hiveswarm.db
    "paths.worktrees": None,    # <home>/worktrees
    "paths.logs": None,         # <home>/logs
    "paths.projects_root": None,  # <home>/repos
    "daemon.host": "127.0.0.1",
    "daemon.port": 7778,
    "daemon.poll_seconds": 5,
    "daemon.lease_seconds": 1800,
    "daemon.claim_seconds": 60,
    "decide.host": "127.0.0.1",
    "decide.port": 9000,
    "decide.url": "http://127.0.0.1:9000",
    "decide.backend": "mock",
    "decide.timeout_seconds": 15,
    "verify.mode": "auto",
    "verify.default_image": "python:3.12-slim",
    "verify.timeout_seconds": 600,
    "sessions.risk_gate": "pattern",
    "sessions.auto_approve_max_risk": "low",
    "workers.prime_agent.enabled": False,
    "rulesets.default": "craft",
    "rulesets.intensity": "standard",
    "memory.backend": "none",
    "memory.hindsight.url": "http://127.0.0.1:8888",
    "memory.hindsight.budget": "low",
    "memory.hindsight.bank_prefix": "hiveswarm",
}


class Config:
    def __init__(self, data: dict[str, Any]):
        self._d = data

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self._d
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return self._default(dotted, default)
            cur = cur[part]
        return cur

    @staticmethod
    def _default(dotted: str, default: Any) -> Any:
        if default is not None:
            return default
        if dotted == "paths.db":
            return str(home() / "db" / "hiveswarm.db")
        if dotted == "paths.worktrees":
            return str(home() / "worktrees")
        if dotted == "paths.logs":
            return str(home() / "logs")
        if dotted == "paths.projects_root":
            return str(home() / "repos")
        return DEFAULTS.get(dotted, default)

    def project(self, name: str) -> dict[str, Any]:
        return self.get(f"projects.{name}", {}) or {}

    @property
    def raw(self) -> dict[str, Any]:
        return self._d


def config_path() -> Path:
    for candidate in candidate_paths():
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise FileNotFoundError("No config found. Run `hm init`, or set HIVESWARM_CONFIG")


def load_fresh() -> Config:
    for candidate in candidate_paths():
        if candidate and Path(candidate).is_file():
            with open(candidate, "rb") as f:
                return Config(tomllib.load(f))
    raise FileNotFoundError(
        "No config found. Run `hm init` (writes ~/.hiveswarm/config.toml), or set HIVESWARM_CONFIG"
    )


@lru_cache(maxsize=1)
def load() -> Config:
    return load_fresh()


_current: tuple[str, tuple[int, int], Config] | None = None


def load_current() -> Config:
    """The config as it is on disk now: re-read when the file changed, otherwise the cached copy.

    For settings a running daemon should pick up without a restart (a project's ruleset, say)."""
    global _current
    try:
        path = config_path()
        st = path.stat()
        stamp = (st.st_mtime_ns, st.st_size)
    except (FileNotFoundError, OSError):
        return load()
    if _current and _current[0] == str(path) and _current[1] == stamp:
        return _current[2]
    try:
        cfg = load_fresh()
    except Exception:  # mid-write by another process: serve what we had rather than an empty config
        return _current[2] if _current else load()
    _current = (str(path), stamp, cfg)
    return cfg


def env_secret(name: str) -> str | None:
    val = os.environ.get(name)
    return val if val else None
