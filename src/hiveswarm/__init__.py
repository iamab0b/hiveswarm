"""Hiveswarm: a quota-aware task router and control plane for swarms of coding agents.

Environment variables are read as HIVESWARM_*; the older HIVEMIND_* names still work.
"""
from __future__ import annotations

import os as _os

for _k, _v in list(_os.environ.items()):
    if _k.startswith("HIVEMIND_") and ("HIVESWARM_" + _k[len("HIVEMIND_"):]) not in _os.environ:
        _os.environ["HIVESWARM_" + _k[len("HIVEMIND_"):]] = _v

def _load_env_file() -> None:
    """~/.hiveswarm/env (written by `hm init`) supplies HIVESWARM_URL and HIVESWARM_TOKEN to every command;
    variables already in the environment win."""
    import pathlib as _pl
    p = _pl.Path(_os.path.expanduser(_os.environ.get("HIVESWARM_HOME") or "~/.hiveswarm")) / "env"
    try:
        for line in p.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                _os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass


_load_env_file()

__version__ = "0.1.0"
