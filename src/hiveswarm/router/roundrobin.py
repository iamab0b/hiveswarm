from __future__ import annotations

import json
import threading
from typing import Any

from .. import db, perf
from ..classify import diff_band
from ..config import load
from ..workers import local_direct, prime_agent

_lock = threading.Lock()
_cursor = 0
REASONS: dict[str, str] = {}


def _probation_filter(task_id: str, candidates: list[str], cls: dict[str, Any] | None) -> list[str]:
    """Agents that are slow or failing on this kind of work lose the hard tasks of that type but keep the easy
    ones (difficulty band <= 2), so their numbers can recover without risking the important work."""
    cfg = load()
    if not cls or not cfg.get("routing.probation", True):
        return candidates
    try:
        table = perf.statuses()
    except Exception:
        return candidates
    tt = cls.get("task_type") or "unknown"
    band = diff_band(float(cls.get("difficulty") or 2.5))
    demoted = {a: table[(a, tt)] for a in candidates if (a, tt) in table and table[(a, tt)]["probation"]}
    if not demoted:
        return candidates
    if band <= 2:
        REASONS[task_id] = "probation: " + ", ".join(f"{a} is {c['status']} on {tt}, easy task so it stays in the pool" for a, c in demoted.items())
        return candidates
    kept = [a for a in candidates if a not in demoted]
    if not kept:
        REASONS[task_id] = "probation: every candidate is slow or failing on " + tt + "; using them anyway"
        return candidates
    REASONS[task_id] = "probation: skipped " + ", ".join(f"{a} ({c['status']} on {tt}: {c['why']})" for a, c in demoted.items())
    return kept


def _local_lanes(cls: dict[str, Any] | None) -> list[str]:
    cfg = load()
    lanes: list[str] = []
    if prime_agent.available():
        lanes.append(prime_agent.AGENT)
    if cfg.get("workers.local_direct.enabled", False):
        simple = True
        if cls and ((cls.get("is_multistep") or 0) > 0.7 or (cls.get("needs_tools") or 0) > 0.7):
            simple = False
        if simple:
            lanes.append(local_direct.AGENT)
    return lanes


def _capable(agent_row: Any, cls: dict[str, Any] | None) -> bool:
    caps = set(json.loads(agent_row["capabilities"] or "[]"))
    if not cls:
        return True
    if (cls.get("needs_vision") or 0) > 0.5 and "vision" not in caps:
        return False
    if (cls.get("is_multistep") or 0) > 0.7 and "agent_loop" not in caps:
        return False
    if (cls.get("needs_tools") or 0) > 0.7 and "tools" not in caps:
        return False
    return True


def choose(task: Any, cls: dict[str, Any] | None, busy: set[str] | None = None) -> str | None:
    global _cursor
    busy = busy or set()
    alive = db.agents_alive()
    candidates = [a["agent_id"] for a in alive if _capable(a, cls)]
    candidates += _local_lanes(cls)

    if not candidates:
        candidates = [prime_agent.AGENT] if prime_agent.available() else [local_direct.AGENT]

    tried = {a["agent"] for a in db.attempts_for(task["id"]) if a["outcome"] in ("fail", "error", "timeout")}
    preferred = task["preferred_agent"] if "preferred_agent" in task.keys() else None
    if preferred and preferred not in tried:
        if preferred in candidates:
            return None if preferred in busy else preferred
    candidates = _probation_filter(task["id"], candidates, cls)
    untried = [c for c in candidates if c not in tried]
    pool = untried if untried else candidates
    idle = [c for c in pool if c not in busy]
    if not idle:
        return None
    with _lock:
        pick = idle[_cursor % len(idle)]
        _cursor += 1
    return pick
