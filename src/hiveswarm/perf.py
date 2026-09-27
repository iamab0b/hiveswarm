"""How fast each agent actually works.

Every agent's log already records what it did and when (tool calls, results, messages). From that this module
derives per-attempt step timings (how long each tool call took, how long the agent sat silent, how long until it
first acted), stores them on the attempt when it ends, watches live work for a step that has stalled, and folds
everything into a per-agent × task-type table with a plain status — healthy, slow, failing, unknown — that the
router, the lead (hm_stats / hm_advise) and the Stats page all read. A slow or failing agent goes on probation:
it stops getting hard work of that type but keeps getting easy pieces, so its numbers can recover.
"""
from __future__ import annotations

import logging
import statistics
import threading
import time
from typing import Any

from . import db
from .config import load

log = logging.getLogger("hiveswarm.perf")

SLOW_STEP_S = 120          # one tool call taking this long counts as a slow step
MIN_STEP_FOR_SLOW = 30.0   # an agent is only "slow" when its average step is at least this long
STEP_KINDS = {"tool"}
END_KINDS = {"result", "err"}
ACTIVE_KINDS = {"tool", "result", "err", "msg", "out", "think", "info", "handoff"}
STATUS_ORDER = {"failing": 0, "slow": 1, "unknown": 2, "healthy": 3}


def _kind(source: str) -> str:
    return source.split(":", 1)[1] if ":" in source else source


def _agent_rows(tid: str, agent: str | None, start: int | None = None, end: int | None = None,
                limit: int = 4000) -> list[dict[str, Any]]:
    rows = db.all_("SELECT seq, ts, source, chunk FROM task_logs WHERE task_id = ? ORDER BY seq DESC LIMIT ?", tid, limit)
    out: list[dict[str, Any]] = []
    for r in rows:
        src = r["source"]
        if agent and not src.startswith(agent + ":"):
            continue
        if not agent and ":" not in src:
            continue
        if start is not None and r["ts"] < start:
            continue
        if end is not None and r["ts"] > end:
            continue
        out.append({"seq": r["seq"], "ts": r["ts"], "kind": _kind(src), "chunk": r["chunk"] or ""})
    out.reverse()
    return out


def analyze(rows: list[dict[str, Any]], start: int | None = None, now: int | None = None) -> dict[str, Any]:
    """Step timings from one agent's ordered log rows. A step is a tool call until its result (or the next tool
    call when no result was logged); silence is the longest gap between any two entries; think time is the gap
    between a result and the next tool call."""
    now = now or db.now()
    steps: list[tuple[str, float]] = []
    thinks: list[float] = []
    cur: dict[str, Any] | None = None
    last_end_ts: int | None = None
    first_action: float | None = None
    silence_max = 0.0
    last_ts: int | None = None
    active = [r for r in rows if r["kind"] in ACTIVE_KINDS]
    for r in active:
        if last_ts is not None:
            silence_max = max(silence_max, float(r["ts"] - last_ts))
        last_ts = r["ts"]
        if r["kind"] in STEP_KINDS:
            if cur is not None:
                steps.append((cur["chunk"], float(r["ts"] - cur["ts"])))
            if last_end_ts is not None:
                thinks.append(float(r["ts"] - last_end_ts))
            if first_action is None and start is not None:
                first_action = float(max(0, r["ts"] - start))
            cur = r
        elif r["kind"] in END_KINDS and cur is not None:
            steps.append((cur["chunk"], float(r["ts"] - cur["ts"])))
            last_end_ts = r["ts"]
            cur = None
    open_step = None
    if cur is not None:
        open_step = {"tool": cur["chunk"].strip().splitlines()[0][:160] if cur["chunk"].strip() else "tool",
                     "since": cur["ts"], "seq": cur["seq"], "seconds": float(max(0, now - cur["ts"]))}
    durations = sorted(d for _, d in steps)
    p90 = durations[min(len(durations) - 1, int(0.9 * (len(durations) - 1)))] if durations else None
    slowest = sorted(steps, key=lambda s: -s[1])[:3]
    return {
        "steps": len(steps),
        "step_avg_s": round(statistics.fmean(durations), 1) if durations else None,
        "step_p90_s": round(p90, 1) if p90 is not None else None,
        "step_max_s": round(durations[-1], 1) if durations else None,
        "slow_steps": sum(1 for d in durations if d >= SLOW_STEP_S),
        "think_avg_s": round(statistics.fmean(thinks), 1) if thinks else None,
        "silence_max_s": round(silence_max, 1),
        "first_action_s": round(first_action, 1) if first_action is not None else None,
        "last_activity": last_ts,
        "open_step": open_step,
        "slowest": [{"tool": t.strip().splitlines()[0][:120] if t.strip() else "tool", "seconds": round(d, 1)} for t, d in slowest],
    }


def record_attempt(aid: str) -> dict[str, Any] | None:
    """Store the step metrics of a finished attempt on its row (called when an attempt ends)."""
    att = db.one("SELECT * FROM attempts WHERE id = ?", aid)
    if att is None:
        return None
    end = att["ended_at"] or db.now()
    rows = _agent_rows(att["task_id"], att["agent"], att["started_at"] - 5, end + 5)
    m = analyze(rows, start=att["started_at"], now=end)
    db.run("""UPDATE attempts SET steps = ?, step_avg_s = ?, step_p90_s = ?, step_max_s = ?, slow_steps = ?,
              silence_max_s = ?, first_action_s = ?, think_avg_s = ? WHERE id = ?""",
           m["steps"], m["step_avg_s"], m["step_p90_s"], m["step_max_s"], m["slow_steps"], m["silence_max_s"],
           m["first_action_s"], m["think_avg_s"], aid)
    return m


def live(tid: str, agent: str | None) -> dict[str, Any]:
    """What a running lane is doing right now: its open step (if any) and when it last did anything."""
    rows = _agent_rows(tid, agent, limit=120)
    if not rows:
        return {"open_step": None, "last_activity": None, "idle_s": None}
    m = analyze(rows)
    idle = float(max(0, db.now() - m["last_activity"])) if m["last_activity"] else None
    return {"open_step": m["open_step"], "last_activity": m["last_activity"], "idle_s": idle,
            "steps": m["steps"], "slow_steps": m["slow_steps"]}


# ── stall watch ────────────────────────────────────────────────────────────

def _running_lanes() -> list[tuple[dict[str, Any], str]]:
    """(task, agent) for every lane that should be producing output right now."""
    out: list[tuple[dict[str, Any], str]] = []
    for t in db.all_("SELECT * FROM tasks WHERE state IN ('running', 'claimed', 'verifying')"):
        task = dict(t)
        if task["state"] == "verifying":
            continue
        agent = task.get("claimed_by")
        if task.get("kind") == "session":
            try:
                import json
                sess = json.loads(task.get("session") or "{}")
            except Exception:
                sess = {}
            agent = agent or sess.get("agent")
            if sess.get("turn") != "working":
                continue
        if agent:
            out.append((task, agent))
    return out


def check_stalls() -> int:
    """Flag lanes stuck on one step or silent for too long; clear the flag as soon as they move again."""
    cfg = load()
    stall = float(cfg.get("watch.stall_minutes", 3)) * 60
    silence = float(cfg.get("watch.silence_minutes", 6)) * 60
    flagged = 0
    for task, agent in _running_lanes():
        tid = task["id"]
        st = live(tid, agent)
        existing = [f for f in db.flags_get(tid) if f.get("kind") == "stalled"]
        ref = None
        summary = None
        detail = None
        step = st.get("open_step")
        if step and step["seconds"] >= stall:
            ref = f"step:{step['seq']}"
            mins = int(step["seconds"] // 60)
            summary = f"{agent} has been on one step for {mins} min: {step['tool']}"
            detail = ("A single tool call has taken longer than the stall limit. Either it is a legitimately long command "
                      "(a full test suite, an install) or the agent is stuck. If it is stuck, stop it and retry on another agent.")
        elif step is None and st.get("idle_s") is not None and st["idle_s"] >= silence and st.get("last_activity"):
            ref = f"silence:{st['last_activity']}"
            mins = int(st["idle_s"] // 60)
            summary = f"{agent} has produced no output for {mins} min"
            detail = "Nothing has been logged from the agent for a while: it may be waiting on something it cannot get, or thinking for a very long time."
        for f in existing:
            if f.get("ref") != ref:
                db.flags_clear(tid, "stalled", f.get("ref"))
                if f.get("ref"):
                    db.log_append(tid, "daemon", "moving again — stall flag cleared")
        if ref and not any(f.get("ref") == ref for f in existing):
            db.flag_add(tid, "stalled", summary or "stalled", detail=detail, ref=ref)
            db.log_append(tid, "daemon", f"stalled: {summary}")
            flagged += 1
    return flagged


# ── the agent table ────────────────────────────────────────────────────────

_cache: dict[str, Any] = {"at": 0.0, "table": None}
_cache_lock = threading.Lock()


def _trend(walls: list[float]) -> float | None:
    """Ratio of the last few attempts' wall time to the ones before: > 1 means getting slower."""
    if len(walls) < 4:
        return None
    k = min(5, len(walls) // 2)
    recent = statistics.fmean(walls[-k:])
    before = statistics.fmean(walls[-2 * k:-k])
    if before <= 0:
        return None
    return round(recent / before, 2)


def agent_table(max_age: float = 20.0) -> dict[str, Any]:
    with _cache_lock:
        if _cache["table"] is not None and time.monotonic() - _cache["at"] < max_age:
            return _cache["table"]
    rows = db.all_("""SELECT a.agent, a.outcome, a.wall_seconds, a.steps, a.step_avg_s, a.step_p90_s, a.step_max_s,
                             a.slow_steps, a.silence_max_s, a.first_action_s, a.started_at, c.task_type, c.difficulty
                      FROM attempts a LEFT JOIN classifications c ON c.task_id = a.task_id
                      WHERE a.outcome IS NOT NULL AND a.outcome != 'abandoned' ORDER BY a.started_at""")
    posterior = {(r["agent"], r["task_type"]): r for r in db.all_(
        "SELECT agent, task_type, SUM(alpha) AS alpha, SUM(beta) AS beta, SUM(n) AS n FROM agent_stats GROUP BY agent, task_type")}
    groups: dict[tuple[str, str], list[Any]] = {}
    for r in rows:
        groups.setdefault((r["agent"], r["task_type"] or "unknown"), []).append(r)
    cells: list[dict[str, Any]] = []
    for (agent, tt), rs in groups.items():
        n = len(rs)
        passes = sum(1 for r in rs if r["outcome"] == "pass")
        walls = [float(r["wall_seconds"]) for r in rs if r["wall_seconds"] is not None]
        total_steps = sum(int(r["steps"] or 0) for r in rs)
        step_time = sum(float(r["step_avg_s"] or 0) * int(r["steps"] or 0) for r in rs)
        p90s = [float(r["step_p90_s"]) for r in rs if r["step_p90_s"] is not None]
        slow = sum(int(r["slow_steps"] or 0) for r in rs)
        silences = [float(r["silence_max_s"]) for r in rs if r["silence_max_s"] is not None]
        firsts = [float(r["first_action_s"]) for r in rs if r["first_action_s"] is not None]
        post = posterior.get((agent, tt))
        pass_est = (post["alpha"] / (post["alpha"] + post["beta"])) if post and (post["alpha"] + post["beta"]) else None
        cells.append({
            "agent": agent, "task_type": tt, "n": n, "passes": passes, "fails": n - passes,
            "pass_rate": round(passes / n, 2) if n else None,
            "pass_estimate": round(pass_est, 2) if pass_est is not None else None,
            "avg_wall_s": round(statistics.fmean(walls), 1) if walls else None,
            "avg_step_s": round(step_time / total_steps, 1) if total_steps else None,
            "p90_step_s": round(statistics.median(p90s), 1) if p90s else None,
            "max_step_s": round(max(float(r["step_max_s"] or 0) for r in rs), 1) if rs else None,
            "steps": total_steps,
            "slow_steps": slow,
            "slow_step_rate": round(slow / total_steps, 2) if total_steps else None,
            "silence_max_s": round(max(silences), 1) if silences else None,
            "first_action_s": round(statistics.fmean(firsts), 1) if firsts else None,
            "trend": _trend(walls),
            "last_at": max(int(r["started_at"]) for r in rs),
        })
    # status relative to the best agent on the same kind of work
    best_step: dict[str, float] = {}
    for c in cells:
        if c["n"] >= 2 and c["avg_step_s"] is not None:
            best_step[c["task_type"]] = min(best_step.get(c["task_type"], 1e9), c["avg_step_s"])
    for c in cells:
        status, why = "healthy", ""
        if c["n"] < 2:
            status, why = "unknown", f"only {c['n']} attempt{'s' if c['n'] != 1 else ''} so far"
        elif c["n"] >= 3 and (c["pass_rate"] or 0) < 0.4:
            status, why = "failing", f"passes {c['passes']} of {c['n']}"
        elif c["n"] == 2 and c["passes"] == 0:
            status, why = "unknown", "0 of 2 so far — one more failure and it goes on probation"
        else:
            b = best_step.get(c["task_type"])
            if c["avg_step_s"] is not None and b is not None and c["avg_step_s"] >= MIN_STEP_FOR_SLOW and c["avg_step_s"] >= 2 * b:
                status, why = "slow", f"average step {c['avg_step_s']:.0f}s vs {b:.0f}s for the best agent on {c['task_type']}"
            elif (c["slow_step_rate"] or 0) >= 0.3 and c["steps"] >= 5:
                status, why = "slow", f"{int((c['slow_step_rate'] or 0) * 100)}% of its steps take over {SLOW_STEP_S}s"
            elif c["trend"] is not None and c["trend"] >= 1.8:
                status, why = "slow", f"recent attempts take {c['trend']:.1f}× longer than before"
        c["status"] = status
        c["why"] = why
        c["probation"] = status in ("slow", "failing")
    cells.sort(key=lambda c: (c["agent"], c["task_type"]))
    agents: dict[str, dict[str, Any]] = {}
    for c in cells:
        a = agents.setdefault(c["agent"], {"agent": c["agent"], "n": 0, "passes": 0, "walls": [], "steps": 0, "step_time": 0.0,
                                           "slow_steps": 0, "probation": [], "worst": "healthy"})
        a["n"] += c["n"]
        a["passes"] += c["passes"]
        if c["avg_wall_s"] is not None:
            a["walls"].append(c["avg_wall_s"] * c["n"])
        a["steps"] += c["steps"]
        a["step_time"] += (c["avg_step_s"] or 0) * c["steps"]
        a["slow_steps"] += c["slow_steps"]
        if c["probation"]:
            a["probation"].append({"task_type": c["task_type"], "status": c["status"], "why": c["why"]})
        if STATUS_ORDER[c["status"]] < STATUS_ORDER[a["worst"]]:
            a["worst"] = c["status"]
    summary = []
    for a in agents.values():
        summary.append({
            "agent": a["agent"], "n": a["n"], "passes": a["passes"],
            "pass_rate": round(a["passes"] / a["n"], 2) if a["n"] else None,
            "avg_wall_s": round(sum(a["walls"]) / a["n"], 1) if a["n"] and a["walls"] else None,
            "avg_step_s": round(a["step_time"] / a["steps"], 1) if a["steps"] else None,
            "steps": a["steps"], "slow_steps": a["slow_steps"],
            "slow_step_rate": round(a["slow_steps"] / a["steps"], 2) if a["steps"] else None,
            "status": a["worst"], "probation": a["probation"],
        })
    summary.sort(key=lambda a: (STATUS_ORDER[a["status"]], a["agent"]))
    table = {"cells": cells, "agents": summary, "slow_step_s": SLOW_STEP_S, "at": db.now()}
    with _cache_lock:
        _cache["table"] = table
        _cache["at"] = time.monotonic()
    return table


def statuses() -> dict[tuple[str, str], dict[str, Any]]:
    return {(c["agent"], c["task_type"]): c for c in agent_table()["cells"]}


def invalidate() -> None:
    with _cache_lock:
        _cache["table"] = None
