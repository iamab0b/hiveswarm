"""Swarm advice: for a list of planned tasks, decide with the decide model and the routing data
which should run headless vs. interactive, which agent fits each, and how many waves the free lanes allow."""
from __future__ import annotations

import json
import logging
import math
from typing import Any

import httpx

from . import db, perf
from .classify import QUESTIONS, TASK_TYPES, _repo_summary, diff_band
from .config import load, load_current
from .workers import prime_agent

log = logging.getLogger("hiveswarm.advise")

ADVISE_QUESTIONS: dict[str, Any] = {
    **QUESTIONS,
    "needs_human": {
        "type": "noul",
        "instructions": "Finishing this task well requires design choices, taste, or clarification that an autonomous agent should check with a human before proceeding",
        "criteria": {
            "true": "Ambiguous scope, UX or architecture decisions, several defensible approaches",
            "false": "The spec and the codebase determine the answer; an agent can verify it alone",
        },
    },
}

PRIOR: dict[str, dict[str, float]] = {
    "claude_code": {"default": 0.72, "hard": 0.75},
    "codex": {"default": 0.66, "refactor": 0.72, "test_write": 0.70},
    "cursor": {"default": 0.58, "frontend": 0.68},
    "antigravity": {"default": 0.55},
    "gemini": {"default": 0.52},
    "opencode": {"default": 0.5},
    "prime_agent": {"default": 0.45, "easy": 0.62, "trivial": 0.66, "docs": 0.6},
    "local_direct": {"default": 0.3, "trivial": 0.55, "docs": 0.5},
}


def _prior(agent: str, task_type: str, band: int, provider: str | None = None) -> float:
    """The default pass estimate for an agent: its own table, else its provider's (a `[agents.opus_max]` profile
    behind the claude_code adapter starts from claude_code's prior), else 0.5."""
    p = PRIOR.get(agent) or PRIOR.get(provider or "") or {"default": 0.5}
    val = p.get("default", 0.5)
    if task_type in p:
        val = max(val, p[task_type])
    if band <= 1 and "trivial" in p:
        val = max(val, p["trivial"])
    elif band == 2 and "easy" in p:
        val = max(val, p["easy"])
    elif band >= 4 and "hard" in p:
        val = max(val, p["hard"])
    return val


def _decide(state: str) -> tuple[dict[str, Any] | None, str]:
    cfg = load()
    url = cfg.get("decide.url", "http://127.0.0.1:9000").rstrip("/") + "/decide"
    timeout = int(cfg.get("decide.timeout_seconds", 15)) * 4
    try:
        r = httpx.post(url, json={"state": state, "questions": ADVISE_QUESTIONS}, timeout=timeout)
        r.raise_for_status()
        body = r.json()
        return body.get("answers") or None, body.get("source", "decide")
    except Exception as e:
        log.warning("advise decide failed: %s", e)
        return None, "fallback"


def _classify(spec: str, repo_summary: str) -> dict[str, Any]:
    state = f"task:\n{spec.strip()[:3000]}\n\nrepo:\n{repo_summary}"
    answers, source = _decide(state)
    if not answers:
        return {"task_type": "investigate", "difficulty": 2.5, "reasoning": 0.5, "needs_tools": 0.5,
                "is_multistep": 0.5, "needs_human": 0.3, "confidence": 0.0, "source": "fallback"}
    tt = answers.get("task_type", {})
    dif = answers.get("difficulty", {})
    rsn = answers.get("reasoning", {})
    return {
        "task_type": tt.get("choice") if tt.get("choice") in TASK_TYPES else "investigate",
        "difficulty": float(dif.get("score", 1.5)) + 1.0,
        "reasoning": float(rsn.get("score", 1.0)) / 2.0 if rsn else 0.5,
        "needs_tools": float(answers.get("needs_tools", {}).get("noul", 0.5)),
        "is_multistep": float(answers.get("is_multistep", {}).get("noul", 0.5)),
        "needs_human": float(answers.get("needs_human", {}).get("noul", 0.3)),
        "confidence": float(tt.get("confidence", 0.0)),
        "source": "fallback" if source == "local" else source,
    }


def _lanes() -> dict[str, dict[str, Any]]:
    caps = db.agent_capacities()
    busy = db.busy_counts()
    out: dict[str, dict[str, Any]] = {}
    for a in db.agents_alive():
        aid = a["agent_id"]
        capabilities = json.loads(a["capabilities"] or "[]")
        cap = int(caps.get(aid, 1))
        b = int(busy.get(aid, 0))
        out[aid] = {"capacity": cap, "busy": b, "free": max(0, cap - b), "sessions": "sessions" in capabilities,
                    "host": a["host"], "capabilities": capabilities, "provider": a["provider"] or aid,
                    "model": a["model"], "effort": a["effort"]}
    if prime_agent.available():
        b = int(busy.get(prime_agent.AGENT, 0))
        out[prime_agent.AGENT] = {"capacity": 1, "busy": b, "free": max(0, 1 - b), "sessions": False, "host": "hub",
                                  "capabilities": ["tools", "agent_loop"]}
    return out


def _stats() -> dict[tuple[str, str, int], dict[str, Any]]:
    out: dict[tuple[str, str, int], dict[str, Any]] = {}
    for r in db.stats_all():
        out[(r["agent"], r["task_type"], int(r["diff_band"]))] = dict(r)
    return out


def _score(agent: str, task_type: str, band: int, stats: dict[tuple[str, str, int], dict[str, Any]],
           provider: str | None = None, siblings: list[str] | None = None) -> tuple[float, str]:
    """Pass estimate for an agent on a kind of work: its own cell, else its own history on that type, else the
    history of its provider's other profiles (`siblings`), else the prior."""
    row = stats.get((agent, task_type, band))
    if row is None:
        near = [v for (a, t, b), v in stats.items() if a == agent and t == task_type]
        if near:
            alpha = sum(v["alpha"] for v in near)
            beta = sum(v["beta"] for v in near)
            n = sum(v["n"] for v in near)
            if n >= 2:
                return alpha / (alpha + beta), f"{int(n)} past {task_type} attempts"
        kin = [v for (a, t, b), v in stats.items() if siblings and a in siblings and a != agent and t == task_type]
        if kin:
            alpha = sum(v["alpha"] for v in kin)
            beta = sum(v["beta"] for v in kin)
            n = sum(v["n"] for v in kin)
            if n >= 2:
                return alpha / (alpha + beta), f"no history of its own; {int(n)} past {task_type} attempts by other {provider} profiles"
        return _prior(agent, task_type, band, provider), "no history yet, default"
    n = int(row["n"])
    p = row["alpha"] / (row["alpha"] + row["beta"])
    if n < 3:
        p = 0.5 * p + 0.5 * _prior(agent, task_type, band, provider)
    return p, f"{n} past {task_type} attempts at this difficulty"


def advise(project: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    cfg = load()
    proj = cfg.project(project)
    repo = proj.get("repo_path") or ""
    summary = _repo_summary(repo, "HEAD", limit=20) if repo else "(new repository)"
    lanes = _lanes()
    stats = _stats()
    try:
        speed = perf.statuses()
    except Exception:
        speed = {}
    names = [str(a) for a in (load_current().project(project).get("agents") or [])]
    on_roster = {a for a, l in lanes.items() if a in names or l.get("provider") in names} if names else set(lanes)
    roster_note = None
    if names and not on_roster:
        roster_note = "roster " + ", ".join(names) + " has no live agent; advising from every agent"
        on_roster = set(lanes)
    by_provider: dict[str, list[str]] = {}
    for a, l in lanes.items():
        by_provider.setdefault(l.get("provider") or a, []).append(a)
    results: list[dict[str, Any]] = []
    sources: set[str] = set()
    demand: dict[str, int] = {}
    probation_notes: set[str] = set()
    for i, t in enumerate(tasks):
        spec = str(t.get("spec") or "")
        c = _classify(spec, summary)
        sources.add(c["source"])
        band = diff_band(c["difficulty"])
        interactive = c["needs_human"] >= 0.6 or (c["difficulty"] >= 3.5 and c["reasoning"] >= 0.75)
        pool = [a for a, l in lanes.items() if a in on_roster and (l["sessions"] if interactive else True)]
        if c["is_multistep"] > 0.7:
            pool = [a for a in pool if "agent_loop" in lanes[a]["capabilities"]]
        if c["needs_tools"] > 0.7:
            pool = [a for a in pool if "tools" in lanes[a]["capabilities"]]
        scored = []
        for a in pool:
            prov = lanes[a].get("provider") or a
            p, why = _score(a, c["task_type"], band, stats, prov, by_provider.get(prov))
            cell = speed.get((a, c["task_type"]))
            status = cell["status"] if cell else "unknown"
            if cell and cell["probation"]:
                probation_notes.add(f"{a} is {status} on {c['task_type']} ({cell['why']}) — easy {c['task_type']} tasks only until it recovers")
                if band >= 3:
                    p = p * 0.5
                    why += f"; {status} on {c['task_type']}, so ranked down for hard work"
            scored.append((p, a, why, cell))
        scored.sort(key=lambda x: -x[0])
        best = scored[0] if scored else (0.0, None, "no agent alive", None)
        alts = [{"agent": a, "pass_estimate": round(p, 2), "status": (cell or {}).get("status", "unknown"),
                 "avg_step_s": (cell or {}).get("avg_step_s")} for p, a, _, cell in scored[1:3]]
        if best[1]:
            demand[best[1]] = demand.get(best[1], 0) + 1
        why = best[2]
        best_cell = best[3] if len(best) > 3 else None
        if interactive:
            why = ("needs human judgment — " if c["needs_human"] >= 0.6 else "hard and reasoning-heavy — ") + "run as a session; " + why
        results.append({
            "index": i,
            "spec": spec.strip().splitlines()[0][:120] if spec.strip() else "",
            "task_type": c["task_type"],
            "difficulty": round(c["difficulty"], 1),
            "mode": "interactive" if interactive else "headless",
            "agent": best[1],
            "pass_estimate": round(best[0], 2),
            "agent_status": (best_cell or {}).get("status", "unknown"),
            "avg_step_s": (best_cell or {}).get("avg_step_s"),
            "avg_wall_s": (best_cell or {}).get("avg_wall_s"),
            "alternatives": alts,
            "reason": why,
            "classification_source": c["source"],
        })
    total_free = sum(l["free"] for a, l in lanes.items() if a in on_roster)
    n = len(tasks)
    waves = math.ceil(n / total_free) if total_free else None
    over = {a: d - lanes[a]["free"] for a, d in demand.items() if a in lanes and d > lanes[a]["free"]}
    providers = {}
    for prov, ids in by_provider.items():
        ids = [a for a in ids if a in on_roster]
        if ids:
            providers[prov] = {"free": sum(lanes[a]["free"] for a in ids), "capacity": sum(lanes[a]["capacity"] for a in ids), "agents": ids}
    parts = []
    if not lanes:
        parts.append("no agents are alive right now; nothing can start until a worker registers")
    else:
        def lane_label(a: str) -> str:
            l = lanes[a]
            extra = " ".join(x for x in (l.get("model"), f"effort={l['effort']}" if l.get("effort") else None) if x)
            return f"{a} {l['free']}/{l['capacity']}" + (f" ({extra})" if extra else "")
        parts.append(f"{total_free} free lane{'s' if total_free != 1 else ''} across " + ", ".join(
            lane_label(a) for a in lanes if a in on_roster))
        shared = [f"{prov} {v['free']}/{v['capacity']} across {', '.join(v['agents'])}" for prov, v in providers.items() if len(v["agents"]) > 1]
        if shared:
            parts.append("lanes sharing one sign-in: " + "; ".join(shared))
        if names:
            parts.append("roster: " + ", ".join(names))
        if roster_note:
            parts.append(roster_note)
        if n:
            parts.append(f"{n} task{'s' if n != 1 else ''} → about {waves} wave{'s' if (waves or 0) != 1 else ''} at current capacity" if waves else "")
        if over:
            parts.append("more work than free lanes on " + ", ".join(f"{a} (+{k})" for a, k in over.items()) + "; the rest queues or use the alternatives")
    parts.extend(sorted(probation_notes))
    return {
        "project": project,
        "tasks": results,
        "lanes": lanes,
        "providers": providers,
        "roster": names,
        "free_lanes": total_free,
        "waves": waves,
        "probation": sorted(probation_notes),
        "summary": "; ".join(p for p in parts if p),
        "source": "fallback" if sources <= {"fallback"} else (next(iter(sources)) if len(sources) == 1 else "mixed"),
    }
