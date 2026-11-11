from __future__ import annotations

import json
import threading
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from .. import advisor, db, directives, memory, overlaps, perf, profiles, rulesets, sessions, worktree
from .. import chat as chat_mod
from .. import projects as projects_mod
from ..config import env_secret, load, load_current
from . import dispatcher

app = FastAPI(title="hiveswarm")
_stop = threading.Event()
_thread: threading.Thread | None = None


def _auth(authorization: str | None = Header(default=None)) -> None:
    token = env_secret("HIVESWARM_TOKEN")
    if not token:
        return
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="unauthorized")


class TaskAdd(BaseModel):
    project: str
    spec: str
    acceptance: str | None = None
    repo_path: str | None = None
    base_ref: str = "HEAD"
    origin: str | None = None
    agent: str | None = None


class AdviseIn(BaseModel):
    project: str
    tasks: list[dict[str, Any]]


class Register(BaseModel):
    agent_id: str
    host: str | None = None
    capabilities: list[str] = []
    capacity: int = 1
    provider: str | None = None  # the adapter behind this agent; lanes of one provider share its usage limit
    model: str | None = None
    effort: str | None = None
    configured: int | None = None  # lanes the worker's own worker.toml asks for (`concurrency`)
    profile: str | None = None     # the profile that wrote this agent's table, when one did


class CapacityIn(BaseModel):
    capacity: int | None = None  # 0..32 lanes; null = back to the worker's own config


class Claim(BaseModel):
    agent_id: str
    wait: float = 0  # long-poll: hold the request up to this many seconds (max 25) until a task is assigned


class Complete(BaseModel):
    task_id: str
    attempt_id: str
    outcome: str
    branch: str | None = None
    diff_stat: str | None = None
    vault_refs: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    log: str | None = None


class Block(BaseModel):
    task_id: str
    attempt_id: str
    reason_code: str
    needs: str | None = None


class Heartbeat(BaseModel):
    agent_id: str
    task_id: str | None = None


class LogEntry(BaseModel):
    source: str = "worker"
    chunk: str
    ts: float | None = None


class LogAppend(BaseModel):
    source: str | None = None
    chunk: str | None = None
    entries: list[LogEntry] | None = None


class ProjectAdd(BaseModel):
    name: str
    repo_path: str | None = None
    verify_image: str | None = None
    acceptance_templates: list[str] | None = None


class Retry(BaseModel):
    spec: str | None = None
    acceptance: str | None = None


class SessionAdd(BaseModel):
    project: str
    spec: str
    agent: str = "claude_code"
    permission_mode: str = "auto"
    acceptance: str | None = None
    repo_path: str | None = None
    base_ref: str = "HEAD"
    name: str | None = None
    count: int = 1
    lead: bool = False
    origin: str | None = None
    persistent: bool = False


class LeadIn(BaseModel):
    project: str
    permission_mode: str = "auto"
    message: str | None = None


class DirectiveIn(BaseModel):
    text: str
    task_id: str | None = None
    project: str | None = None
    every_tools: int = 8
    every_minutes: int = 10
    check: bool = True
    created_by: str | None = None


class DirectiveFired(BaseModel):
    task_id: str
    how: str = "hook"
    screen: str | None = None


class SessionSend(BaseModel):
    text: str | None = None
    keys: list[str] | None = None
    enter: bool = True
    screen: bool = False
    lines: int = 40


class SessionAnswer(BaseModel):
    choice: str
    text: str | None = None


class SessionContinue(BaseModel):
    agent: str = "claude_code"
    permission_mode: str | None = None


class SessionEvent(BaseModel):
    kind: str
    branch: str | None = None
    host: str | None = None
    tmux: str | None = None
    turn: str | None = None
    attention: dict[str, Any] | None = None
    claude_session_id: str | None = None
    transcript_path: str | None = None
    summary: str | None = None
    tool: str | None = None
    tool_input: Any = None
    options: list[str] | None = None
    question: str | None = None
    exit_code: int | None = None
    can_decide: bool = False


class CommandDone(BaseModel):
    result: str | None = None


@app.on_event("startup")
def _startup() -> None:
    global _thread
    db.migrate()
    dispatcher.recover_after_restart()
    _thread = threading.Thread(target=dispatcher.loop, args=(_stop,), daemon=True, name="dispatcher")
    _thread.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    _stop.set()


@app.get("/health")
def health() -> dict[str, Any]:
    counts = {r["state"]: r["n"] for r in db.all_("SELECT state, COUNT(*) AS n FROM tasks GROUP BY state")}
    agents = [{"agent_id": a["agent_id"], "host": a["host"], "capabilities": json.loads(a["capabilities"])}
              for a in db.agents_alive()]
    return {"ok": True, "dispatcher_alive": bool(_thread and _thread.is_alive()), "tasks": counts, "agents_alive": agents}


@app.post("/tasks", dependencies=[Depends(_auth)])
def task_add(t: TaskAdd) -> dict[str, Any]:
    cfg = load()
    repo = t.repo_path or cfg.project(t.project).get("repo_path")
    if not repo:
        raise HTTPException(status_code=400, detail="repo_path required (or configure projects.<name>.repo_path)")
    tid = db.task_insert(t.project, t.spec, t.acceptance, repo, t.base_ref, t.agent or None)
    first = (t.spec.strip().splitlines() or [""])[0][:120]
    by = f" by {t.origin}" if t.origin else ""
    pref = f" (prefer {t.agent})" if t.agent else ""
    db.log_append(tid, "daemon", f"enqueued in {t.project}{by}{pref}: {first}")
    return {"id": tid, "state": "pending"}


@app.post("/advise", dependencies=[Depends(_auth)])
def advise_route(a: AdviseIn) -> dict[str, Any]:
    from .. import advise as _advise
    return _advise.advise(a.project, a.tasks[:20])


@app.get("/tasks", dependencies=[Depends(_auth)])
def task_list(project: str | None = None, state: str | None = None, limit: int = 50, live: bool = False) -> list[dict[str, Any]]:
    """The newest tasks. With `live`, every task that has not ended is included however old it is: the app draws its
    workspaces from this list, and a project's chat agent or lead can be far older than the newest few hundred."""
    rows = [dict(r) for r in db.task_list(project, state, limit)]
    if live and not state:
        have = {r["id"] for r in rows}
        where = "state NOT IN ('done','failed','abandoned')" + (" AND project = ?" if project else "")
        extra = db.all_(f"SELECT * FROM tasks WHERE {where} ORDER BY created_at DESC LIMIT 500", *([project] if project else []))
        rows += [dict(r) for r in extra if r["id"] not in have]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
    return rows


@app.get("/tasks/{tid}", dependencies=[Depends(_auth)])
def task_get(tid: str) -> dict[str, Any]:
    t = db.task_get(tid)
    if not t:
        raise HTTPException(status_code=404, detail="no such task")
    c = db.classification_get(tid)
    return {"task": dict(t), "classification": dict(c) if c else None,
            "attempts": [dict(a) for a in db.attempts_for(tid)]}


@app.get("/stats", dependencies=[Depends(_auth)])
def stats() -> list[dict[str, Any]]:
    return [dict(r) for r in db.stats_all()]


@app.get("/stats/agents", dependencies=[Depends(_auth)])
def stats_agents() -> dict[str, Any]:
    """Per agent × task type: pass rate, wall time, step timings, slow steps, trend and a status
    (healthy / slow / failing / unknown); agents on probation get only easy work of that type."""
    return perf.agent_table()


@app.get("/tasks/{tid}/live", dependencies=[Depends(_auth)])
def task_live(tid: str) -> dict[str, Any]:
    t = db.task_get(tid)
    if not t:
        raise HTTPException(status_code=404, detail="no such task")
    agent = t["claimed_by"]
    if not agent and t["kind"] == "session" and t["session"]:
        try:
            agent = json.loads(t["session"]).get("agent")
        except Exception:
            agent = None
    return perf.live(tid, agent)


def _hub_lanes(busy: dict[str, int], now: float) -> list[dict[str, Any]]:
    """The lanes the hub runs itself (`[workers.prime_agent]`, `[workers.local_direct]` in config.toml). They never
    register like a worker's agents do, so GET /agents builds their rows here: one lane each, configured in a file
    on the hub rather than in a worker.toml, hence `local: true` and no capacity control."""
    import socket

    from ..workers import local_direct, prime_agent
    cfg = load_current()
    host = socket.gethostname()
    rows: list[dict[str, Any]] = []
    prime = (cfg.raw.get("workers") or {}).get("prime_agent") or {}
    if prime.get("enabled") is True or prime_agent.available_cached():
        alive = prime_agent.available_cached()
        container = prime.get("container", "prime-agent")
        rows.append({"agent_id": prime_agent.AGENT, "host": host, "capabilities": ["tools", "agent_loop"],
                     "capacity": 1, "desired_capacity": None, "last_seen": now if alive else 0, "alive": alive,
                     "busy": busy.get(prime_agent.AGENT, 0), "provider": "prime_agent",
                     "model": cfg.get("inference.model", "coder"), "effort": None, "local": True,
                     "where": f"[workers.prime_agent] in the hub's config.toml (container {container})"})
    direct = (cfg.raw.get("workers") or {}).get("local_direct") or {}
    if direct.get("enabled") is True:
        rows.append({"agent_id": local_direct.AGENT, "host": host, "capabilities": [],
                     "capacity": 1, "desired_capacity": None, "last_seen": now, "alive": True,
                     "busy": busy.get(local_direct.AGENT, 0), "provider": "local_direct",
                     "model": cfg.get("inference.model", "coder"), "effort": None, "local": True,
                     "where": f"[workers.local_direct] in the hub's config.toml ({cfg.get('inference.url') or 'inference.url unset'})"})
    return rows


@app.get("/agents", dependencies=[Depends(_auth)])
def agents() -> list[dict[str, Any]]:
    now = db.now()
    busy = db.busy_counts()
    rows = [{**dict(a), "capabilities": json.loads(a["capabilities"]), "alive": (now - a["last_seen"]) < 120,
             "busy": busy.get(a["agent_id"], 0), "local": False}
            for a in db.agents_all() if not (a["last_seen"] == 0 and (a["capacity"] or 0) == 0)]  # retired on purpose
    for r in rows:
        if r.get("profile") == sessions.CHAT_PROFILE:
            r["desired_capacity"] = _chat_lanes(r["agent_id"])
    return rows + _hub_lanes(busy, now)


def _chat_lanes(agent_id: str) -> int:
    """Lanes a chat agent table should run: one per chat agent that is open plus a spare, so the next one (a fresh
    Home chat after this one joined a project) starts at once. The worker sizes itself to this like to any count
    set from the app."""
    return max(2, min(32, sessions.chats_live(agent_id) + 1))


@app.post("/register", dependencies=[Depends(_auth)])
def register(r: Register) -> dict[str, Any]:
    row = db.agent_register(r.agent_id, r.host, r.capabilities, r.capacity, r.provider, r.model, r.effort, r.configured, r.profile)
    if r.profile == sessions.CHAT_PROFILE:
        return {"ok": True, "desired_capacity": _chat_lanes(r.agent_id)}
    return {"ok": True, "desired_capacity": row["desired_capacity"] if row else None}


@app.post("/agents/{agent_id}/capacity", dependencies=[Depends(_auth)])
def agent_capacity(agent_id: str, c: CapacityIn) -> dict[str, Any]:
    """Set how many lanes an agent should run. The worker picks it up within a few seconds, starts or retires lanes
    (a retiring lane finishes its current task first) and re-registers; `capacity` in GET /agents shows what is
    actually running, `desired_capacity` what was asked for."""
    if agent_id in dispatcher.LOCAL_WORKERS:
        raise HTTPException(status_code=400, detail=f"{agent_id} is a hub lane configured in the hub's config.toml "
                                                    f"([workers.{agent_id}]); it runs one lane and has no live count")
    if c.capacity is not None and not (0 <= c.capacity <= 32):
        raise HTTPException(status_code=400, detail="capacity must be between 0 and 32 lanes")
    cur = db.one("SELECT profile FROM agents WHERE agent_id = ?", agent_id)
    if cur and cur["profile"] == sessions.CHAT_PROFILE:
        raise HTTPException(status_code=400, detail=f"{agent_id} is the chat agent; it runs one lane per open chat plus a spare")
    row = db.agent_set_desired_capacity(agent_id, c.capacity)
    if row is None:
        raise HTTPException(status_code=404, detail="no such agent")
    return {"ok": True, "agent_id": agent_id, "capacity": row["capacity"], "desired_capacity": row["desired_capacity"]}


@app.post("/unregister", dependencies=[Depends(_auth)])
def unregister(r: Register) -> dict[str, Any]:
    """A worker shutting down cleanly; its agents stop being routing candidates immediately."""
    db.agent_retire(r.agent_id)
    return {"ok": True}


@app.post("/claim", dependencies=[Depends(_auth)])
async def claim(c: Claim, request: Request) -> Any:
    """A worker lane asks for work. With `wait`, the daemon holds the request until something is assigned to that
    agent or the time is up, so an idle worker makes a few requests a minute instead of one every few seconds."""
    import asyncio
    import time as _time
    cfg = load()
    lease = int(cfg.get("daemon.lease_seconds", 1800))
    deadline = _time.time() + min(max(c.wait, 0), 25)
    db.agent_touch(c.agent_id)
    while True:
        t = await asyncio.to_thread(db.task_claim_assigned, c.agent_id, lease)
        if t or _time.time() >= deadline:
            break
        if await request.is_disconnected():
            return None
        await asyncio.sleep(0.5)
    if not t:
        db.agent_touch(c.agent_id)
        return None
    return await asyncio.to_thread(_claimed, c.agent_id, t, cfg)


def _claimed(agent_id: str, t: Any, cfg: Any) -> dict[str, Any]:
    aid = db.attempt_start(t["id"], agent_id, None)
    db.task_set_state(t["id"], "running")
    cls = db.classification_get(t["id"])
    handoff = dispatcher.handoff_context(t["id"])
    if handoff:
        db.log_append(t["id"], "daemon", f"handoff to {agent_id}:\n{handoff.strip()}")
    rs = rulesets.resolve(load_current(), t["project"])
    db.attempt_update(aid, ruleset=rulesets.label(rs["name"], rs["intensity"]))
    return {"task": dict(t), "attempt_id": aid, "classification": dict(cls) if cls else None,
            "handoff": handoff, "ruleset": rs, "hub_ssh": cfg.get("daemon.hub_ssh") or cfg.get("daemon.spark_ssh", ""),
            "spark_ssh": cfg.get("daemon.hub_ssh") or cfg.get("daemon.spark_ssh", "")}


@app.post("/complete", dependencies=[Depends(_auth)])
def complete(c: Complete) -> dict[str, Any]:
    t = db.task_get(c.task_id)
    if not t:
        raise HTTPException(status_code=404, detail="no such task")
    if t["kind"] == "session":
        sessions.update(c.task_id, attention=None, turn="exited")
    if t["state"] == "abandoned":
        db.attempt_finish(c.attempt_id, "abandoned", verifier_log="cancelled before completion", branch=c.branch)
        return {"ok": True, "verified": False, "discarded": True}
    cls_row = db.classification_get(c.task_id)
    cls = dict(cls_row) if cls_row else None
    agent = t["claimed_by"] or "unknown"

    if t["kind"] == "session" and (sessions.get(c.task_id) or {}).get("role") == "chat":
        ok = c.outcome == "pass"
        db.attempt_finish(c.attempt_id, "pass" if ok else "error", verifier_log=c.log)
        db.task_set_state(c.task_id, "done" if ok else "failed", claimed_by=None, lease_expires=None)
        db.log_append(c.task_id, "daemon", "chat agent closed" if ok else f"chat agent stopped with an error: {(c.log or '')[:200]}")
        return {"ok": True, "verified": False, "state": "done" if ok else "failed"}
    if c.outcome == "pass" and not c.branch and t["kind"] == "session" and (sessions.get(c.task_id) or {}).get("lead"):
        db.attempt_finish(c.attempt_id, "pass", verifier_log=c.log)
        db.task_set_state(c.task_id, "done", claimed_by=None, lease_expires=None)
        db.log_append(c.task_id, "daemon", "done — lead session finished")
        return {"ok": True, "verified": False, "state": "done"}
    if c.outcome != "pass" or not c.branch:
        db.attempt_finish(c.attempt_id, "error" if c.outcome == "error" else "fail",
                          verifier_log=c.log, tokens_in=c.tokens_in, tokens_out=c.tokens_out, branch=c.branch)
        dispatcher.finalize(c.task_id, cls, agent, passed=False, weight=1.0)
        return {"ok": True, "verified": False}

    try:
        wt = worktree.create_from_branch(c.task_id, t["repo_path"], c.branch)
    except Exception as e:
        db.attempt_finish(c.attempt_id, "error", verifier_log=f"branch fetch failed: {e}", branch=c.branch)
        dispatcher.finalize(c.task_id, cls, agent, passed=False, weight=1.0)
        return {"ok": True, "verified": False, "error": str(e)}

    db.task_set_state(c.task_id, "verifying", worktree=wt, vault_refs=c.vault_refs)
    res = {"diff_stat": c.diff_stat or worktree.diff_stat(wt), "tokens_in": c.tokens_in,
           "tokens_out": c.tokens_out, "branch": c.branch}
    dispatcher.verify_and_finalize(c.task_id, c.attempt_id, wt, cls, agent, res)
    final = db.task_get(c.task_id)
    return {"ok": True, "verified": True, "state": final["state"]}


@app.post("/block", dependencies=[Depends(_auth)])
def block(b: Block) -> dict[str, Any]:
    t = db.task_get(b.task_id)
    cls_row = db.classification_get(b.task_id)
    cls = dict(cls_row) if cls_row else None
    db.attempt_finish(b.attempt_id, "fail", verifier_log=f"blocked: {b.reason_code}\n{b.needs or ''}")
    dispatcher.finalize(b.task_id, cls, t["claimed_by"] or "unknown", passed=False, weight=0.5)
    return {"ok": True, "reason_code": b.reason_code}


@app.get("/summary", dependencies=[Depends(_auth)])
def summary() -> dict[str, Any]:
    out = db.summary()
    out["dispatcher_alive"] = bool(_thread and _thread.is_alive())
    out["agents_alive"] = [a["agent_id"] for a in db.agents_alive()]
    return out


@app.get("/projects", dependencies=[Depends(_auth)])
def projects() -> dict[str, Any]:
    out = projects_mod.listing()
    cfg = load_current()
    counts = db.deferred_counts()
    tasks = db.task_counts_by_project()
    paused = advisor.paused_projects()
    for name, p in out.items():
        rs = rulesets.resolve(cfg, name)
        p["ruleset"] = rulesets.label(rs["name"], rs["intensity"])
        p["deferred_open"] = counts.get(name, 0)
        p["paused"] = name in paused
        p["profile"] = p.get("profile") or None
        p["started"] = tasks.get(name, 0) > 0
    return out


class ProjectSettings(BaseModel):
    agents: list[str] | None = None          # the roster: only these agents get the project's work; [] or null clears it
    ruleset: str | None = None               # "craft" | "off"
    ruleset_intensity: str | None = None     # "standard" | "strict"
    profile: str | None = None               # the profile the project runs on; "" for none; fixed once the project has a task
    lanes: dict[str, int] | None = None      # per-project lane counts for the profile's coders, by agent id; {} clears them


@app.get("/projects/{name}/settings", dependencies=[Depends(_auth)])
def project_settings_get(name: str) -> dict[str, Any]:
    cfg = load_current()
    if name not in (cfg.raw.get("projects") or {}):
        raise HTTPException(status_code=404, detail="no such project")
    p = cfg.project(name)
    rs = rulesets.resolve(cfg, name)
    n = profiles.task_count(name)
    return {"name": name, "agents": list(p.get("agents") or []), "ruleset": rs["name"], "ruleset_intensity": rs["intensity"],
            "ruleset_set": "ruleset" in p or "ruleset_intensity" in p,
            "profile": p.get("profile") or None, "lanes": dict(p.get("lanes") or {}), "started": n > 0, "task_count": n}


@app.post("/projects/{name}/settings", dependencies=[Depends(_auth)])
def project_settings_set(name: str, body: ProjectSettings) -> dict[str, Any]:
    """Edit a project's section in config.toml: its profile (fixed once the project has started; lane counts stay
    editable), its roster (which agents may take its work when it has no profile) and its ruleset."""
    from .. import tomledit
    from ..config import config_path
    cfg = load_current()
    if name not in (cfg.raw.get("projects") or {}):
        raise HTTPException(status_code=404, detail="no such project")
    p = cfg.project(name)
    updates: dict[str, Any] = {}
    pname = p.get("profile") or None
    if body.profile is not None:
        new = body.profile.strip() or None
        if new != pname:
            n = profiles.task_count(name)
            if n:
                raise HTTPException(status_code=409, detail=f"{name} has started ({n} task{'s' if n != 1 else ''}); its profile "
                                                            f"stays {pname or 'any agent'}. Lane counts can still change.")
            if new and new not in profiles.listing(cfg):
                raise HTTPException(status_code=400, detail=f"no profile named {new}")
            updates["profile"] = new
            updates["lanes"] = None
            if new:
                updates["agents"] = None
            pname = new
    if body.lanes is not None:
        if not pname:
            raise HTTPException(status_code=400, detail=f"{name} has no profile; lane counts belong to a profile's coders")
        r = profiles.for_project(cfg, name) if pname == p.get("profile") else None
        valid = {c["agent_id"] for c in (r or {}).get("coders") or [] if not c["hub"]}
        if r is None:
            prof = profiles.get(cfg, pname) or {}
            valid = {profiles.coder_id(pname, k) for k, c in (prof.get("coders") or {}).items() if c["adapter"] not in profiles.HUB_ADAPTERS}
        lanes: dict[str, int] = {}
        for aid, n in body.lanes.items():
            if aid not in valid:
                raise HTTPException(status_code=400, detail=f"{aid} is not a coder of profile {pname}")
            lanes[aid] = max(0, min(profiles.MAX_COUNT, int(n)))
        updates["lanes"] = lanes or None
    if body.agents is not None:
        if pname and body.agents:
            raise HTTPException(status_code=400, detail=f"{name} runs on profile {pname}; its agents come from the profile")
        updates["agents"] = [a for a in body.agents if a] or None
    if body.ruleset is not None:
        if body.ruleset not in ("craft", "off"):
            raise HTTPException(status_code=400, detail="ruleset must be craft or off")
        updates["ruleset"] = body.ruleset
    if body.ruleset_intensity is not None:
        if body.ruleset_intensity not in ("standard", "strict"):
            raise HTTPException(status_code=400, detail="ruleset_intensity must be standard or strict")
        updates["ruleset_intensity"] = body.ruleset_intensity
    if updates:
        tomledit.update_section(config_path(), f"projects.{name}", updates)
        load.cache_clear()
    return project_settings_get(name)


@app.get("/projects/{name}/ruleset", dependencies=[Depends(_auth)])
def project_ruleset(name: str) -> dict[str, Any]:
    """The ruleset a project's agents work under, with the text that is put in their prompts."""
    return rulesets.resolve(load_current(), name)


@app.get("/projects/{name}/profile", dependencies=[Depends(_auth)])
def project_profile(name: str) -> dict[str, Any]:
    """The project's profile resolved to agent ids and effective lane counts, with each agent's live row."""
    cfg = load_current()
    if name not in (cfg.raw.get("projects") or {}):
        raise HTTPException(status_code=404, detail="no such project")
    r = profiles.for_project(cfg, name)
    n = profiles.task_count(name)
    live = {a["agent_id"]: a for a in agents()}
    if r and not r.get("error"):
        r["lead"]["live"] = live.get(r["lead"]["agent_id"])
        for c in r["coders"]:
            c["live"] = live.get(c["agent_id"])
    return {"project": name, "profile": r["name"] if r else None, "started": n > 0, "task_count": n, "locked": n > 0, "resolved": r}


# ── profiles: which models lead and code, saved in config.toml ──

class ProfileIn(BaseModel):
    name: str
    note: str | None = None
    lead: dict[str, Any] = {}
    chat: dict[str, Any] | None = None  # accepted from 0.3 clients and ignored: the chat agent is not part of a profile
    coders: list[dict[str, Any]] = []


def _profile_view(cfg: Any, name: str, p: dict[str, Any]) -> dict[str, Any]:
    used = profiles.users(cfg, name)
    coders = []
    for key, c in (p.get("coders") or {}).items():
        hub = c["adapter"] in profiles.HUB_ADAPTERS
        coders.append({"key": key, "agent_id": c["adapter"] if hub else profiles.coder_id(name, key), "hub": hub, **c})
    return {"name": name, "note": p.get("note") or "", "error": p.get("error"),
            "lead": {"agent_id": profiles.lead_id(name), **p["lead"]} if p.get("lead") else None,
            "coders": coders, "default": profiles.default_profile(cfg) == name,
            "used_by": used, "locked": any(u["started"] for u in used)}


@app.get("/profiles", dependencies=[Depends(_auth)])
def profiles_list() -> dict[str, Any]:
    cfg = load_current()
    return {"profiles": [_profile_view(cfg, n, p) for n, p in profiles.listing(cfg).items()]}


@app.get("/profiles/wanted", dependencies=[Depends(_auth)])
def profiles_wanted() -> dict[str, Any]:
    """The `[agents.*]` tables the profiled projects need on the machine running the app; `ui_server.materialize`
    writes them into that machine's worker.toml."""
    return {"tables": profiles.wanted(load_current())}


@app.post("/profiles", dependencies=[Depends(_auth)])
def profile_set(b: ProfileIn) -> dict[str, Any]:
    """Create or replace a profile. One a started project runs on can only change its note."""
    from .. import tomledit
    from ..config import config_path
    cfg = load_current()
    try:
        new = profiles.normalize(b.name, {"note": b.note, "lead": b.lead, "coders": b.coders})
    except profiles.ProfileError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    current = profiles.get(cfg, b.name)
    if current and not current.get("error") and not profiles.roles_equal(current, new):
        started = [u["project"] for u in profiles.users(cfg, b.name) if u["started"]]
        if started:
            raise HTTPException(status_code=409, detail=f"{b.name} is the profile of {', '.join(started)}, which started; "
                                                        "save a different setup under a new name")
    tomledit.write_section(config_path(), f"profiles.{b.name}", new)
    load.cache_clear()
    return _profile_view(load_current(), b.name, new)


@app.delete("/profiles/{name}", dependencies=[Depends(_auth)])
def profile_delete(name: str) -> dict[str, Any]:
    from .. import tomledit
    from ..config import config_path
    cfg = load_current()
    if name not in profiles.listing(cfg):
        raise HTTPException(status_code=404, detail="no such profile")
    used = [u["project"] for u in profiles.users(cfg, name)]
    if used:
        raise HTTPException(status_code=409, detail=f"{name} is the profile of {', '.join(used)}")
    tomledit.write_section(config_path(), f"profiles.{name}", None)
    load.cache_clear()
    return {"ok": True, "name": name}


@app.get("/projects/{name}/deferred", dependencies=[Depends(_auth)])
def project_deferred(name: str, include_resolved: bool = False, limit: int = 200) -> dict[str, Any]:
    """The project's deferred ledger: shortcuts and loose ends agents reported in their handoffs."""
    items = [dict(r) for r in db.deferred_list(name, include_resolved=include_resolved, limit=limit)]
    return {"project": name, "items": items, "open": sum(1 for i in items if not i["resolved_at"])}


class DeferredResolve(BaseModel):
    by: str | None = None
    note: str | None = None


@app.post("/deferred/{did}/resolve", dependencies=[Depends(_auth)])
def deferred_resolve(did: str, body: DeferredResolve) -> dict[str, Any]:
    row = db.deferred_resolve(did, body.by, body.note)
    if row is None:
        raise HTTPException(status_code=404, detail="no such deferred item")
    if row["task_id"]:
        db.log_append(row["task_id"], "daemon", f"deferred item resolved{' by ' + body.by if body.by else ''}: {row['text'][:120]}")
    return {"ok": True, "item": dict(row)}


# ── plan, pause, briefs (the chat agent and the lead share these) ────────

class PlanIn(BaseModel):
    text: str
    by: str | None = None


class PauseIn(BaseModel):
    by: str | None = None
    reason: str | None = None


class BriefIn(BaseModel):
    text: str
    by: str | None = None


@app.get("/projects/{name}/plan", dependencies=[Depends(_auth)])
def plan_get(name: str) -> dict[str, Any]:
    return {"project": name, "plan": advisor.plan_get(name)}


@app.put("/projects/{name}/plan", dependencies=[Depends(_auth)])
def plan_put(name: str, body: PlanIn) -> dict[str, Any]:
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="an empty plan")
    return {"project": name, "plan": advisor.plan_set(name, body.text, body.by)}


@app.post("/projects/{name}/pause", dependencies=[Depends(_auth)])
def project_pause(name: str, body: PauseIn) -> dict[str, Any]:
    """No new task of this project starts and nothing merges until it is resumed; running agents continue."""
    p = advisor.pause(name, body.by, body.reason)
    for t in db.task_list(name, None, 50):
        if t["state"] in ("running", "claimed", "assigned"):
            db.log_append(t["id"], "daemon", f"project paused by {body.by or 'someone'}: {body.reason or 'no reason given'}")
    return {"ok": True, "paused": p}


@app.post("/projects/{name}/resume", dependencies=[Depends(_auth)])
def project_resume(name: str, by: str | None = None) -> dict[str, Any]:
    was = advisor.resume(name)
    return {"ok": True, "was_paused": was}


@app.get("/projects/{name}/briefs", dependencies=[Depends(_auth)])
def briefs_list(name: str, undelivered: bool = False) -> dict[str, Any]:
    return {"project": name, "briefs": advisor.briefs(name, undelivered_only=undelivered)}


@app.post("/projects/{name}/briefs", dependencies=[Depends(_auth)])
def brief_create(name: str, body: BriefIn) -> dict[str, Any]:
    """A brief for the lead. It reaches the lead through hm_wait; when a lead session is idle it is also typed
    into its terminal so it acts on it at once."""
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="an empty brief")
    b = advisor.brief_add(name, body.text, body.by)
    lead = _live_lead(name)
    sent_to = None
    if lead:
        s = lead["session"]
        asking = (s.get("attention") or {}).get("kind") in ("permission", "question", "usage_limit")  # typing would answer it
        if s.get("host") and lead["state"] in ("claimed", "running") and s.get("turn") in ("idle", "waiting", None) and not asking:
            sessions.enqueue_command(lead["id"], "send", {"text": advisor.brief_text_for_lead(b), "enter": True}, s.get("host"))
            advisor.brief_delivered(b["id"], "terminal")
            if (s.get("attention") or {}).get("kind") == "input":
                sessions.update(lead["id"], attention=None, turn="working")
            sent_to = lead["id"]
        db.log_append(lead["id"], "daemon", f"brief from {body.by or 'the chat agent'}: {body.text.strip().splitlines()[0][:160]}")
    return {"ok": True, "brief": b, "lead": lead["id"] if lead else None, "sent_to": sent_to}


@app.post("/briefs/{bid}/delivered", dependencies=[Depends(_auth)])
def brief_mark(bid: str, via: str = "hm_wait") -> dict[str, Any]:
    advisor.brief_delivered(bid, via)
    return {"ok": True}


def _live_lead(project: str) -> dict[str, Any] | None:
    for r in db.task_list(project, None, 200):
        if r["kind"] != "session" or r["state"] in ("done", "failed", "abandoned"):
            continue
        s = sessions.get(r["id"]) or {}
        if s.get("lead") and s.get("persistent"):
            return {"id": r["id"], "state": r["state"], "session": s}
    return None


# ── the chat agent ──────────────────────────────────────────────────────

class ChatIn(BaseModel):
    fresh: bool = False          # Home only: close the chat agent that is open there and start a new one
    host: str | None = None      # the machine the app runs on; its chat agent is preferred so the terminal is local


class StartProject(BaseModel):
    name: str
    brief: str = ""
    profile: str | None = None
    repo_path: str | None = None


def _chat_agent(host: str | None) -> str:
    """The agent table a chat session runs on: the chat agent of the app's machine, else any that is alive, else
    the id that machine's worker will register (the session then waits for it)."""
    rows = [a for a in db.agents_alive() if a["profile"] == sessions.CHAT_PROFILE]
    for a in rows:
        if host and a["host"] == host:
            return a["agent_id"]
    if rows:
        return rows[0]["agent_id"]
    return sessions.chat_agent_id(host or "")


def _chat_view(t: dict[str, Any], **extra: Any) -> dict[str, Any]:
    s = sessions.get(t["id"]) or {}
    return {"id": t["id"], "project": None if t["project"] == sessions.HOME else t["project"], "state": t["state"],
            "agent": s.get("agent"), "host": s.get("host"), "tmux": s.get("tmux"), **extra}


def _chat_create(project: str, host: str | None, repo: str = "") -> dict[str, Any]:
    cfg = load_current()
    role = profiles.chat_role(cfg)
    name = "chat" if project == sessions.HOME else f"chat-{project}"[:40]
    tid = sessions.create(project, "Chat agent", None, repo, "HEAD", _chat_agent(host), "auto", name=name,
                          origin="app", persistent=True, role="chat", model=role.get("model"), effort=role.get("effort"))
    dispatcher.route_now(tid)
    return dict(db.task_get(tid))


def _chat_close(t: dict[str, Any], why: str) -> None:
    att = db.attempt_latest(t["id"])
    if att and not att["outcome"]:
        db.attempt_finish(att["id"], "abandoned", verifier_log=why)
    db.task_set_state(t["id"], "abandoned", claimed_by=None, lease_expires=None)
    host = _session_host(t["id"])
    sessions.update(t["id"], attention=None, turn="exited")
    sessions.enqueue_command(t["id"], "kill", {}, host)
    db.log_append(t["id"], "daemon", why)


@app.post("/chats/home", dependencies=[Depends(_auth)])
def chat_home(b: ChatIn) -> dict[str, Any]:
    """The chat agent that belongs to no project: the one that is open, or a new one. `fresh` closes what is open
    first, which is what the app asks for when it launches, so a chat that never started a project leaves nothing."""
    for t in sessions.chats(sessions.HOME, include_ended=True):  # a Home chat that ended leaves nothing behind
        if t["state"] in sessions.ENDED and t["updated_at"] < db.now() - 120:
            db.task_delete(t["id"])
    live = sessions.chats(sessions.HOME)
    if live and not b.fresh:
        return _chat_view(live[0], created=False)
    for t in live:
        _chat_close(t, "closed: the app opened with a fresh chat agent")
    return _chat_view(_chat_create(sessions.HOME, b.host), created=True)


@app.post("/chats/project/{name}", dependencies=[Depends(_auth)])
def chat_project(name: str, b: ChatIn) -> dict[str, Any]:
    """A project's chat agent: the one that is open; else the last one, reopened with its conversation; else a new
    one that starts inside the project."""
    cfg = load_current()
    if name not in (cfg.raw.get("projects") or {}):
        raise HTTPException(status_code=404, detail="no such project")
    live = sessions.find_chat(name)
    if live:
        return _chat_view(live, created=False, reopened=False)
    ended = sessions.find_chat(name, include_ended=True)
    if ended and ended["session"].get("claude_session_id"):
        tid = ended["id"]
        role = profiles.chat_role(cfg)
        db.run("UPDATE tasks SET attempts = 0 WHERE id = ?", tid)
        sessions.drop_untaken_commands(tid)
        sessions.update(tid, turn="starting", attention=None, tmux=None, unread=0, resume=True, agent=_chat_agent(b.host),
                        model=role.get("model"), effort=role.get("effort"))
        db.task_set_state(tid, "classified", claimed_by=None, lease_expires=None)
        db.log_append(tid, "daemon", "chat agent reopened with its conversation")
        dispatcher.route_now(tid)
        return _chat_view(dict(db.task_get(tid)), created=False, reopened=True)
    for t in sessions.chats(name, include_ended=True):  # ones that ended before Claude Code ever started: nothing to resume
        db.task_delete(t["id"])
    return _chat_view(_chat_create(name, b.host, cfg.project(name).get("repo_path") or ""), created=True, reopened=False)


@app.get("/chats/{tid}/prompt", dependencies=[Depends(_auth)])
def chat_prompt(tid: str) -> dict[str, Any]:
    """The system prompt a worker appends when it starts this chat agent, built from what the hub knows now."""
    t = db.task_get(tid)
    s = sessions.get(tid) if t else None
    if not t or not s or s.get("role") != "chat":
        raise HTTPException(status_code=404, detail="no such chat agent")
    cfg = load_current()
    if t["project"] == sessions.HOME:
        text = chat_mod.home_prompt(_profile_briefs(cfg), profiles.default_profile(cfg), sorted((cfg.raw.get("projects") or {}).keys()))
    else:
        plan = advisor.plan_get(t["project"])
        learned = memory.prompt_block(memory.recall(plan["text"][:300] if plan else t["project"], t["project"], 6)) if memory.enabled() else ""
        text = chat_mod.project_prompt(t["project"], plan["text"] if plan else None, advisor.paused(t["project"]), learned)
    return {"id": tid, "project": None if t["project"] == sessions.HOME else t["project"], "system_prompt": text}


def _profile_briefs(cfg: Any) -> list[dict[str, Any]]:
    """Each saved profile as one line (`lead claude_code opus max · codex ×2`), for the chat agent and hm_profiles."""
    out = []
    for n, p in profiles.listing(cfg).items():
        if p.get("error"):
            continue
        line = profiles.describe({"name": n, "lead": p.get("lead"), "coders": list((p.get("coders") or {}).values())}).split(": ", 1)[1]
        out.append({"name": n, "summary": line + (f" ({p['note']})" if p.get("note") else ""), "default": profiles.default_profile(cfg) == n})
    return out


@app.post("/chats/{tid}/start_project", dependencies=[Depends(_auth)])
def chat_start_project(tid: str, b: StartProject) -> dict[str, Any]:
    """A Home chat agent starts a project: create it (or take an existing one that has no chat agent), give it its
    profile, move this chat session into it and start its lead with the brief. A chat agent does this once."""
    from .. import tomledit
    from ..config import config_path
    t = db.task_get(tid)
    s = sessions.get(tid) if t else None
    if not t or not s or s.get("role") != "chat":
        raise HTTPException(status_code=404, detail="no such chat agent")
    if t["project"] != sessions.HOME:
        raise HTTPException(status_code=409, detail=f"this chat agent belongs to project {t['project']}; the human starts "
                                                    "another project from the chat agent at Home")
    if t["state"] in sessions.ENDED:
        raise HTTPException(status_code=409, detail="this chat agent has ended")
    name = b.name.strip()
    cfg = load_current()
    existing = name in (cfg.raw.get("projects") or {})
    if existing and sessions.find_chat(name):
        raise HTTPException(status_code=409, detail=f"project {name} already has a chat agent; the human opens its workspace "
                                                    "from the Control tab or with Ctrl+Shift+1..9")
    want = (b.profile or "").strip() or None
    if want and want not in profiles.listing(cfg):
        raise HTTPException(status_code=400, detail=f"no profile named {want}; hm_profiles lists them")
    if not existing:
        made = _project_create(name, b.repo_path)
        repo = made["repo_path"]
        cfg = load_current()
    else:
        repo = cfg.project(name).get("repo_path") or ""
    current = cfg.project(name).get("profile") or None
    profile = current
    if not profiles.started(name):
        profile = want or current or profiles.default_profile(cfg)
        if profile != current:
            tomledit.update_section(config_path(), f"projects.{name}", {"profile": profile, "lanes": None, "agents": None} if profile else {"profile": None})
            load.cache_clear()
    elif want and want != current:
        raise HTTPException(status_code=409, detail=f"{name} has started on {current or 'any agent'}; its profile cannot change")
    db.run("UPDATE tasks SET project = ?, repo_path = ?, updated_at = ? WHERE id = ?", name, repo, db.now(), tid)
    sessions.update(tid, name=f"chat-{name}"[:40])
    db.log_append(tid, "daemon", f"chat agent joined project {name}" + ("" if existing else " (created)") + (f" on profile {profile}" if profile else ""))
    brief = b.brief.strip()
    lead = lead_get_or_create(LeadIn(project=name, message=brief or None))
    return {"ok": True, "project": name, "created": not existing, "profile": profile, "repo_path": repo, "chat": tid,
            "lead": lead, "instructions": chat_mod.bound_instructions(name, profile, lead["id"], existing)}


# ── app-wide settings (config.toml on the hub) ──────────────────────────

class SettingsIn(BaseModel):
    default_profile: str | None = None       # "" clears it
    chat_model: str | None = None            # "" for Claude Code's own default
    chat_effort: str | None = None


@app.get("/settings", dependencies=[Depends(_auth)])
def settings_get() -> dict[str, Any]:
    from ..workers import adapters
    cfg = load_current()
    role = profiles.chat_role(cfg)
    return {"default_profile": profiles.default_profile(cfg), "chat": {"model": role.get("model"), "effort": role.get("effort"),
                                                                       "error": role.get("error")},
            "chat_efforts": list(adapters.EFFORTS.get("claude_code") or [])}


@app.post("/settings", dependencies=[Depends(_auth)])
def settings_set(b: SettingsIn) -> dict[str, Any]:
    from .. import tomledit
    from ..config import config_path
    from ..workers import adapters
    cfg = load_current()
    if b.default_profile is not None:
        name = b.default_profile.strip() or None
        if name and name not in profiles.listing(cfg):
            raise HTTPException(status_code=400, detail=f"no profile named {name}")
        tomledit.update_section(config_path(), "defaults", {"profile": name})
    chat: dict[str, Any] = {}
    if b.chat_model is not None:
        chat["model"] = b.chat_model.strip() or None
    if b.chat_effort is not None:
        effort = b.chat_effort.strip() or None
        if effort and effort not in (adapters.EFFORTS.get("claude_code") or []):
            raise HTTPException(status_code=400, detail="effort for the chat agent is one of " + ", ".join(adapters.EFFORTS.get("claude_code") or []))
        chat["effort"] = effort
    if chat:
        tomledit.update_section(config_path(), "chat", chat)
    load.cache_clear()
    return settings_get()


# ── memory ──────────────────────────────────────────────────────────────

class RememberIn(BaseModel):
    text: str
    project: str | None = None
    global_scope: bool = False
    tags: list[str] = []
    by: str | None = None


@app.get("/memory", dependencies=[Depends(_auth)])
def memory_status() -> dict[str, Any]:
    return memory.get().status()


@app.get("/memory/recall", dependencies=[Depends(_auth)])
def memory_recall(q: str, project: str | None = None, limit: int = 8) -> dict[str, Any]:
    """What the swarm learned that matches `q`: the project's memories and the global ones, best first."""
    return {"backend": memory.get().name, "items": memory.recall(q, project, max(1, min(limit, 50)))}


@app.post("/memory/remember", dependencies=[Depends(_auth)])
def memory_remember(body: RememberIn) -> dict[str, Any]:
    r = memory.remember(body.text, body.project, body.global_scope, body.tags, body.by)
    return {**r, "backend": memory.get().name}


@app.get("/stats/rulesets", dependencies=[Depends(_auth)])
def stats_rulesets() -> dict[str, Any]:
    """Finished attempts with and without a ruleset: pass rate, lines changed, wall time, untested count."""
    return {"rows": db.ruleset_stats()}


class LocalCopy(BaseModel):
    host: str
    path: str
    win_path: str | None = None
    head: str | None = None
    dirty: bool = False
    ahead: int = 0
    behind: int = 0
    note: str | None = None


@app.post("/projects/{name}/local", dependencies=[Depends(_auth)])
def project_local(name: str, lc: LocalCopy) -> dict[str, Any]:
    if name not in (projects_mod.listing()):
        raise HTTPException(status_code=404, detail="no such project")
    projects_mod.record_local(name, lc.host, lc.path, lc.win_path, lc.head, lc.dirty, lc.ahead, lc.behind, lc.note)
    return {"ok": True}


@app.get("/projects/tombstones", dependencies=[Depends(_auth)])
def project_tombstones() -> list[dict[str, Any]]:
    return projects_mod.tombstones()


@app.delete("/projects/{name}", dependencies=[Depends(_auth)])
def project_delete(name: str, purge: bool = False) -> dict[str, Any]:
    """Remove a project from Hiveswarm (its tasks, sessions, logs, standing orders, hub worktrees and config entry).
    purge=true also deletes its repository on the hub and tells workers to move their local copies to trash."""
    try:
        return projects_mod.delete(name, purge=purge)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no project '{name}'")


def _project_create(name: str, repo_path: str | None = None, verify_image: str | None = None,
                    acceptance_templates: list[str] | None = None) -> dict[str, Any]:
    import os
    import re
    import subprocess
    import tomllib
    from pathlib import Path

    from ..config import config_path, load_fresh

    name = name.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
        raise HTTPException(status_code=400, detail="project name: letters, digits, - and _ only, starting with a letter or digit")
    cfg = load_fresh()
    if name in (cfg.raw.get("projects") or {}):
        raise HTTPException(status_code=409, detail=f"project '{name}' already exists")
    root = Path(os.path.expanduser(cfg.get("paths.projects_root", "~/repos")))
    repo = Path(os.path.expanduser(repo_path)) if repo_path else root / name
    created = False
    if (repo / ".git").exists():
        pass
    elif repo.exists() and any(repo.iterdir()):
        raise HTTPException(status_code=400, detail=f"{repo} exists and is not a git repository")
    else:
        repo.mkdir(parents=True, exist_ok=True)
        ident = ["-c", "user.name=hiveswarm", "-c", "user.email=hiveswarm@localhost"]
        for args in (["init", "-q", "-b", "main"], [*ident, "commit", "-q", "--allow-empty", "-m", f"init {name}"]):
            r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                raise HTTPException(status_code=500, detail=f"git {' '.join(args[:2])} failed: {r.stderr.strip()[:300]}")
        created = True
    image = verify_image or cfg.get("verify.default_image", "python:3.12-slim")
    block = f'[projects.{name}]\nrepo_path = "{repo}"\nverify_image = "{image}"\n'
    if acceptance_templates:
        block += f"acceptance_templates = {json.dumps(acceptance_templates)}\n"
    from .. import tomledit
    path = config_path()
    text = path.read_text()
    new = text.rstrip("\n") + "\n\n" + block
    tomllib.loads(new)
    tomledit.atomic_write(path, new)
    load.cache_clear()
    return {"ok": True, "name": name, "repo_path": str(repo), "created": created, "verify_image": image}


@app.post("/projects", dependencies=[Depends(_auth)])
def project_add(p: ProjectAdd) -> dict[str, Any]:
    return _project_create(p.name, p.repo_path, p.verify_image, p.acceptance_templates)


def _session_coder(cfg: Any, project: str, wanted: str) -> str | None:
    """The coder of a project's profile that an interactive session should run on."""
    r = profiles.for_project(cfg, project)
    coders = [c for c in (r or {}).get("coders") or [] if not c["hub"] and c["count"] > 0]
    if not coders:
        return None
    alive = {a["agent_id"] for a in db.agents_alive()}
    caps = db.agent_capacities()
    busy = db.busy_counts()

    def free(c: dict[str, Any]) -> int:
        return (caps.get(c["agent_id"], 0) - busy.get(c["agent_id"], 0)) if c["agent_id"] in alive else -1
    same = [c for c in coders if c["adapter"] == wanted]
    return max(same or coders, key=free)["agent_id"]


@app.post("/sessions", dependencies=[Depends(_auth)])
def session_add(sa: SessionAdd) -> dict[str, Any]:
    cfg = load()
    repo = sa.repo_path or cfg.project(sa.project).get("repo_path")
    if not repo:
        raise HTTPException(status_code=400, detail="repo_path required (or configure projects.<name>.repo_path)")
    if sa.permission_mode not in sessions.PERMISSION_MODES:
        raise HTTPException(status_code=400, detail=f"permission_mode must be one of {sessions.PERMISSION_MODES}")
    agent = sa.agent
    pcfg = load_current()
    lead_agent = profiles.lead_agent(pcfg, sa.project)
    if lead_agent:
        # A profiled project's lead runs on the profile's lead agent, which has one lane and the lead is in it. An
        # agent the lead spawns in its own terminal runs on one of the profile's coders: the one asked for, else the
        # one behind the CLI that was named, else the one with the most free lanes.
        if sa.lead:
            agent = lead_agent
        elif sa.agent == lead_agent or sa.agent not in profiles.agent_ids(pcfg, sa.project):
            agent = _session_coder(pcfg, sa.project, sa.agent) or lead_agent
    ids = []
    for i in range(max(1, min(sa.count, 12))):
        name = sa.name if (sa.name and sa.count == 1) else None
        ids.append(sessions.create(sa.project, sa.spec, sa.acceptance, repo, sa.base_ref, agent, sa.permission_mode, name,
                                   lead=sa.lead, origin=sa.origin, persistent=sa.persistent and sa.lead))
    return {"ids": ids, "id": ids[0], "state": "pending"}


@app.get("/leads", dependencies=[Depends(_auth)])
def leads_list() -> list[dict[str, Any]]:
    cfg = load()
    out = []
    for name in (cfg.get("projects", {}) or {}).keys():
        lead = sessions.find_lead(name)
        out.append({"project": name, "lead": lead})
    return out


@app.post("/leads", dependencies=[Depends(_auth)])
def lead_get_or_create(b: LeadIn) -> dict[str, Any]:
    cfg = load()
    repo = cfg.project(b.project).get("repo_path")
    if not repo:
        raise HTTPException(status_code=404, detail=f"no project {b.project}")
    existing = sessions.find_lead(b.project)
    ended = None if existing else sessions.find_lead(b.project, include_ended=True)
    if ended and ended["session"].get("claude_session_id"):
        tid = ended["id"]
        if ended["worktree"]:
            worktree.remove(tid, ended["repo_path"])
        db.run("UPDATE tasks SET attempts = 0, worktree = NULL WHERE id = ?", tid)
        sessions.drop_untaken_commands(tid)
        sessions.update(tid, turn="starting", attention=None, tmux=None, unread=0, resume=True,
                        resume_note=(b.message or "").strip())
        db.task_set_state(tid, "pending", claimed_by=None, lease_expires=None)
        first = (b.message or "").strip().splitlines()[0][:200] if (b.message or "").strip() else ""
        db.log_append(tid, "daemon", "lead reopened" + (f" — you: {first}" if first else ""))
        return {"id": tid, "created": False, "reopened": True, "state": "pending"}
    if existing:
        if b.message:
            s = existing["session"]
            first = b.message.strip().splitlines()[0][:200] if b.message.strip() else ""
            if s.get("host") and existing["state"] in ("claimed", "running"):
                sessions.enqueue_command(existing["id"], "send", {"text": b.message, "enter": True}, s.get("host"))
                db.log_append(existing["id"], "daemon", f"you: {first}")
                if (s.get("attention") or {}).get("kind") in ("question", "input"):
                    sessions.update(existing["id"], attention=None, turn="working")
            else:
                spec = existing["spec"]
                if spec.strip() == "Standing by for the human's first request.":
                    spec = b.message.strip()
                else:
                    spec = spec.rstrip() + "\n\n" + b.message.strip()
                db.task_update_spec(existing["id"], spec, None)
                db.log_append(existing["id"], "daemon", f"you (queued until the lead starts): {first}")
        return {"id": existing["id"], "created": False, "state": existing["state"]}
    spec = (b.message or "").strip() or "Standing by for the human's first request."
    agent = profiles.lead_agent(load_current(), b.project) or "claude_code"
    tid = sessions.create(b.project, spec, None, repo, "HEAD", agent, b.permission_mode,
                          name=f"lead-{b.project}"[:40], lead=True, origin="app", persistent=True)
    return {"id": tid, "created": True, "state": "pending"}


@app.get("/sessions", dependencies=[Depends(_auth)])
def session_list(project: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    out = []
    for r in db.task_list(project, None, 500):
        if r["kind"] != "session":
            continue
        d = dict(r)
        d["session"] = sessions.get(r["id"])
        out.append(d)
        if len(out) >= limit:
            break
    return out


@app.get("/sessions/commands", dependencies=[Depends(_auth)])
async def session_commands(request: Request, host: str, wait: float = 0, advisor_ok: bool = False) -> dict[str, Any]:
    """Commands for the sessions on `host`, long-polled. (`advisor_ok` and the empty `advisor` list are what a 0.3
    worker still sends and reads; the headless advisor is gone, the chat agent is a session like any other.)"""
    import asyncio
    import time as _time
    deadline = _time.time() + min(max(wait, 0), 25)
    while True:
        if await request.is_disconnected():
            return {"commands": [], "advisor": []}
        cmds = await asyncio.to_thread(sessions.take_commands, host)
        if cmds or _time.time() >= deadline:
            return {"commands": cmds, "advisor": []}
        await asyncio.sleep(0.5)


@app.get("/sessions/commands/{cid}", dependencies=[Depends(_auth)])
def session_command_get(cid: str) -> dict[str, Any]:
    row = db.one("SELECT * FROM session_commands WHERE id = ?", cid)
    if not row:
        raise HTTPException(status_code=404, detail="no such command")
    return dict(row)


@app.post("/sessions/commands/{cid}/done", dependencies=[Depends(_auth)])
def session_command_done(cid: str, d: CommandDone) -> dict[str, Any]:
    sessions.finish_command(cid, d.result)
    return {"ok": True}


@app.get("/sessions/{tid}", dependencies=[Depends(_auth)])
def session_get(tid: str) -> dict[str, Any]:
    t = db.task_get(tid)
    if not t or t["kind"] != "session":
        raise HTTPException(status_code=404, detail="no such session")
    return {"task": dict(t), "session": sessions.get(tid), "commands": sessions.commands_for(tid, 10),
            "attempts": [dict(a) for a in db.attempts_for(tid)]}


def _adapter_of(agent_id: str) -> str:
    """The CLI behind an agent id (`deep-lead` and `chat-laptop` run claude_code), which decides the keys that
    approve and deny in its terminal."""
    r = db.one("SELECT provider FROM agents WHERE agent_id = ?", agent_id)
    return (r["provider"] if r and r["provider"] else None) or agent_id


def _session_host(tid: str) -> str | None:
    s = sessions.get(tid)
    return (s or {}).get("host")


def _live_session(tid: str) -> dict[str, Any]:
    s = sessions.get(tid)
    if s is None:
        raise HTTPException(status_code=404, detail="no such session")
    t = db.task_get(tid)
    if t["state"] in ("done", "failed", "abandoned"):
        raise HTTPException(status_code=409, detail=f"session has ended ({t['state']}); reopen it with retry")
    if not s.get("host"):
        raise HTTPException(status_code=409, detail="session hasn't started yet (waiting for a free lane)")
    return s


@app.post("/sessions/{tid}/send", dependencies=[Depends(_auth)])
def session_send(tid: str, body: SessionSend) -> dict[str, Any]:
    s = _live_session(tid)
    if body.screen:
        cid = sessions.enqueue_command(tid, "screen", {"lines": body.lines}, s.get("host"))
        return {"ok": True, "command_id": cid}
    if body.keys:
        cid = sessions.enqueue_command(tid, "keys", {"keys": body.keys}, s.get("host"))
        db.log_append(tid, "daemon", f"you sent keys: {' '.join(body.keys)}")
    elif body.text is not None:
        cid = sessions.enqueue_command(tid, "send", {"text": body.text, "enter": body.enter}, s.get("host"))
        first = body.text.strip().splitlines()[0][:200] if body.text.strip() else ""
        db.log_append(tid, "daemon", f"you: {first}")
        if s.get("attention") and s["attention"].get("kind") in ("question", "input"):
            sessions.update(tid, attention=None, turn="working")
    else:
        raise HTTPException(status_code=400, detail="text or keys required")
    return {"ok": True, "command_id": cid}


@app.post("/sessions/{tid}/answer", dependencies=[Depends(_auth)])
def session_answer(tid: str, body: SessionAnswer) -> dict[str, Any]:
    s = _live_session(tid)
    att = s.get("attention") or {}
    agent = _adapter_of(s.get("agent") or "claude_code")
    cfg = load()
    cfg_keys = (cfg.get(f"sessions.keys.{agent}") or {})
    if att.get("kind") == "question" and body.choice in ("approve", "deny"):
        raise HTTPException(status_code=400, detail="this session is asking a question: answer with an option number or text")
    if att.get("kind") == "permission" and body.choice not in ("approve", "deny", "text"):
        raise HTTPException(status_code=400, detail="this session is asking for permission: approve, deny, or text")
    if att.get("kind") != "permission" and body.choice in ("approve", "deny"):
        raise HTTPException(status_code=400, detail="this session is not asking for permission right now (send text or keys instead)")
    if body.choice in ("approve", "deny"):
        keys = sessions.keys_for(agent, body.choice, cfg_keys)
        cid = sessions.enqueue_command(tid, "keys", {"keys": keys}, s.get("host"))
        verb = "approved" if body.choice == "approve" else "denied"
        db.log_append(tid, "daemon", f"you {verb}: {att.get('summary', '')}"[:300])
    elif body.choice == "text":
        if att.get("kind") == "question" and att.get("options"):
            cid = sessions.enqueue_command(tid, "answer_other", {"text": body.text or "", "n_options": len(att["options"])}, s.get("host"))
        else:
            cid = sessions.enqueue_command(tid, "send", {"text": body.text or "", "enter": True}, s.get("host"))
        db.log_append(tid, "daemon", f"you answered: {(body.text or '')[:200]}")
    else:
        try:
            idx = int(body.choice)
        except ValueError:
            raise HTTPException(status_code=400, detail="choice must be approve, deny, text, or an option number")
        cid = sessions.enqueue_command(tid, "answer_option", {"index": idx, "n_options": len(att.get("options") or [])}, s.get("host"))
        opts = att.get("options") or []
        label = opts[idx - 1] if 0 < idx <= len(opts) else str(idx)
        db.log_append(tid, "daemon", f"you chose: {label}"[:300])
    sessions.update(tid, attention=None, turn="working")
    return {"ok": True, "command_id": cid}


@app.post("/sessions/{tid}/finish", dependencies=[Depends(_auth)])
def session_finish(tid: str) -> dict[str, Any]:
    s = _live_session(tid)
    cid = sessions.enqueue_command(tid, "finish", {}, s.get("host"))
    db.log_append(tid, "daemon", "finish requested: the worktree will be committed, pushed, and verified")
    return {"ok": True, "command_id": cid}


@app.post("/sessions/{tid}/continue", dependencies=[Depends(_auth)])
def session_continue(tid: str, body: SessionContinue) -> dict[str, Any]:
    s = sessions.get(tid)
    if s is None:
        raise HTTPException(status_code=404, detail="no such session")
    t = db.task_get(tid)
    if t["state"] in ("done", "abandoned"):
        raise HTTPException(status_code=409, detail=f"session is {t['state']}")
    if body.permission_mode and body.permission_mode not in sessions.PERMISSION_MODES:
        raise HTTPException(status_code=400, detail=f"permission_mode must be one of {sessions.PERMISSION_MODES}")
    alive_hosts = {a["host"] for a in db.agents_alive()}
    if t["state"] in ("claimed", "running") and s.get("host") in alive_hosts and s.get("tmux"):
        sessions.update(tid, pending_continue={"agent": body.agent, "permission_mode": body.permission_mode},
                        attention=None, turn="exited")
        cid = sessions.enqueue_command(tid, "handoff", {"agent": body.agent}, s.get("host"))
        db.log_append(tid, "daemon", f"handing off to {body.agent}: saving the worktree first")
        return {"ok": True, "pending": True, "command_id": cid}
    new = sessions.continue_session(tid, body.agent, body.permission_mode, None)
    return {"ok": True, "pending": False, "id": new}


@app.post("/sessions/{tid}/viewed", dependencies=[Depends(_auth)])
def session_viewed(tid: str) -> dict[str, Any]:
    s = sessions.get(tid)
    if s is None:
        raise HTTPException(status_code=404, detail="no such session")
    sessions.update(tid, unread=0)
    return {"ok": True}


@app.post("/sessions/{tid}/event", dependencies=[Depends(_auth)])
def session_event(tid: str, ev: SessionEvent) -> dict[str, Any]:
    s = sessions.get(tid)
    if s is None:
        raise HTTPException(status_code=404, detail="no such session")
    agent = s.get("agent") or "agent"
    fields: dict[str, Any] = {}
    for k in ("host", "tmux", "claude_session_id", "transcript_path"):
        v = getattr(ev, k)
        if v:
            fields[k] = v
    decision: dict[str, Any] = {}
    if ev.kind == "started":
        # a chat agent opens at its own prompt and waits; everything else starts on the task it was given
        fields.update(turn="idle" if s.get("role") == "chat" else "working", started_at=db.now(), attention=None)
        db.log_append(tid, f"{agent}:status", ev.summary or f"session started in tmux {ev.tmux or ''}".strip())
    elif ev.kind == "turn":
        turn = ev.turn or "working"
        fields["turn"] = turn
        if turn == "idle":
            fields["turns"] = int(s.get("turns", 0)) + 1
            if s.get("role") == "chat":  # a chat agent at its prompt is not something that needs you
                fields["attention"] = None
            else:
                fields["attention"] = {"kind": "input", "summary": ev.summary or "finished its turn and is waiting for you",
                                       "since": db.now()}
                db.log_append(tid, f"{agent}:status", "turn finished — waiting for your input")
        elif turn == "working":
            if (s.get("attention") or {}).get("kind") in ("input",):
                fields["attention"] = None
        fields["unread"] = int(s.get("unread", 0)) + 1
    elif ev.kind == "permission":
        summary = ev.summary or sessions.summarize_command(ev.tool or "tool", ev.tool_input)
        cmd = ""
        if isinstance(ev.tool_input, dict):
            cmd = str(ev.tool_input.get("command") or ev.tool_input.get("cmd") or json.dumps(ev.tool_input))
        else:
            cmd = str(ev.tool_input or "")
        fields["asks"] = int(s.get("asks", 0)) + 1
        risk, source = sessions.classify_risk(agent, ev.tool or "tool", cmd or summary)
        if sessions.auto_approve_allowed(agent, s.get("permission_mode", "auto"), risk):
            if not ev.can_decide:
                cli = _adapter_of(agent)
                keys = sessions.keys_for(cli, "approve", load().get(f"sessions.keys.{cli}") or {})
                sessions.enqueue_command(tid, "keys", {"keys": keys}, s.get("host"))
            fields["auto_approved"] = int(s.get("auto_approved", 0)) + 1
            fields["turn"] = "working"
            decision = {"decision": "allow", "risk": risk}
            db.log_append(tid, "daemon", f"auto-approved ({risk} risk via {source}): {summary}"[:400])
        else:
            fields["turn"] = "waiting"
            fields["attention"] = {"kind": "permission", "summary": summary, "detail": cmd[:2000], "risk": risk,
                                   "since": db.now()}
            decision = {"decision": "ask", "risk": risk}
            db.log_append(tid, "daemon", f"needs your approval ({risk} risk via {source}): {summary}"[:400])
    elif ev.kind == "question":
        fields["turn"] = "waiting"
        fields["asks"] = int(s.get("asks", 0)) + 1
        fields["attention"] = {"kind": "question", "summary": (ev.question or ev.summary or "asked a question")[:500],
                               "options": ev.options or [], "since": db.now()}
        opts = ("  options: " + " | ".join(ev.options)) if ev.options else ""
        db.log_append(tid, "daemon", f"question for you: {(ev.question or ev.summary or '')[:300]}{opts}"[:600])
    elif ev.kind == "usage_limit":
        fields["turn"] = "waiting"
        fields["attention"] = {"kind": "usage_limit", "summary": ev.summary or f"{agent} hit a usage limit",
                               "since": db.now()}
        db.log_append(tid, "daemon", f"usage limit: {ev.summary or ''}"[:300])
    elif ev.kind == "clear":
        fields["attention"] = None
        fields["turn"] = ev.turn or "working"
    elif ev.kind == "handed_off":
        pc = s.get("pending_continue") or {}
        new = sessions.continue_session(tid, pc.get("agent") or "claude_code", pc.get("permission_mode"), ev.branch)
        return {"ok": True, "id": new}
    elif ev.kind == "exited":
        fields["turn"] = "exited"
        code = ev.exit_code
        if code not in (None, 0):
            fields["attention"] = {"kind": "failed", "summary": f"{agent} exited with code {code}", "since": db.now()}
        db.log_append(tid, f"{agent}:status", "exited" + (f" with code {code}" if code not in (None, 0) else ""))
    elif ev.kind == "meta":
        if ev.summary:
            db.log_append(tid, f"{agent}:info", ev.summary[:500])
    else:
        raise HTTPException(status_code=400, detail=f"unknown event kind {ev.kind}")
    sessions.update(tid, **fields)
    return {"ok": True, **decision}


@app.get("/inbox", dependencies=[Depends(_auth)])
def inbox(limit: int = 100) -> dict[str, Any]:
    items = sessions.inbox(limit)
    return {"items": items, "count": len(items)}


@app.post("/directives", dependencies=[Depends(_auth)])
def directive_add(d: DirectiveIn) -> dict[str, Any]:
    try:
        did = directives.create(d.text, d.task_id, d.project, d.every_tools, d.every_minutes, d.check, d.created_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": did}


@app.get("/directives", dependencies=[Depends(_auth)])
def directive_list(project: str | None = None, task_id: str | None = None, for_task: str | None = None,
                   include_inactive: bool = False) -> list[dict[str, Any]]:
    if for_task:
        return directives.for_task(for_task)
    return directives.list_(project, task_id, include_inactive)


@app.delete("/directives/{did}", dependencies=[Depends(_auth)])
def directive_delete(did: str) -> dict[str, Any]:
    if not directives.deactivate(did):
        raise HTTPException(status_code=404, detail="no such directive")
    return {"ok": True}


@app.post("/directives/{did}/fired", dependencies=[Depends(_auth)])
def directive_fired(did: str, f: DirectiveFired) -> dict[str, Any]:
    if not db.task_get(f.task_id):
        raise HTTPException(status_code=404, detail="no such task")
    return directives.fired(did, f.task_id, f.how, screen=f.screen)


@app.post("/directives/{did}/check", dependencies=[Depends(_auth)])
def directive_check(did: str, f: DirectiveFired) -> dict[str, Any]:
    if not db.task_get(f.task_id):
        raise HTTPException(status_code=404, detail="no such task")
    return directives.audit(did, f.task_id)


@app.post("/tasks/{tid}/flags/clear", dependencies=[Depends(_auth)])
def flags_clear(tid: str, kind: str | None = None) -> dict[str, Any]:
    if not db.task_get(tid):
        raise HTTPException(status_code=404, detail="no such task")
    db.flags_clear(tid, kind)
    db.log_append(tid, "daemon", "flag cleared by you" if not kind else f"{kind} flag cleared by you")
    return {"ok": True}


@app.post("/tasks/{tid}/log", dependencies=[Depends(_auth)])
def log_append(tid: str, l: LogAppend) -> dict[str, Any]:
    t = db.task_get(tid)
    if not t:
        raise HTTPException(status_code=404, detail="no such task")
    items: list[tuple[str, str, float | None]] = []
    if l.entries:
        items += [(e.source, e.chunk, e.ts) for e in l.entries]
    if l.chunk is not None:
        items.append((l.source or "worker", l.chunk, None))
    seq = db.log_append_many(tid, items)
    for _, chunk, _ts in items:
        if chunk.startswith("files: "):
            overlaps.note_files(tid, t["project"], chunk[7:])
    return {"seq": seq}


@app.get("/feed", dependencies=[Depends(_auth)])
def feed(since: int | None = None, tail: int | None = None, limit: int = 1000) -> dict[str, Any]:
    if since is None:
        rows = db.feed_tail(min(tail or 500, 5000))
    else:
        rows = db.feed_since(since, min(limit, 5000))
    last = rows[-1]["id"] if rows else (since or 0)
    return {"entries": [dict(r) for r in rows], "last_id": last}


@app.get("/tasks/{tid}/log", dependencies=[Depends(_auth)])
def log_read(tid: str, since: int = 0, limit: int = 500) -> dict[str, Any]:
    rows = db.log_since(tid, since, limit)
    return {"entries": [dict(r) for r in rows], "last_seq": rows[-1]["seq"] if rows else since}


@app.post("/tasks/{tid}/cancel", dependencies=[Depends(_auth)])
def cancel(tid: str) -> dict[str, Any]:
    t = db.task_get(tid)
    if not t:
        raise HTTPException(status_code=404, detail="no such task")
    if t["state"] in ("done", "failed", "abandoned"):
        return {"ok": False, "state": t["state"], "reason": "already terminal"}
    agent = t["claimed_by"]
    killed = dispatcher.kill_running(tid)
    att = db.attempt_latest(tid)
    if att and not att["outcome"]:
        db.attempt_finish(att["id"], "abandoned", verifier_log="cancelled from TUI")
    db.task_set_state(tid, "abandoned", claimed_by=None, lease_expires=None)
    if t["kind"] == "session":
        sessions.update(tid, attention=None, turn="exited")
        sessions.enqueue_command(tid, "kill", {}, _session_host(tid))
    if killed:
        msg = f"cancelled — stopped {agent}"
    elif agent:
        msg = f"cancelled — {agent} will stop within 15s"
    else:
        msg = "cancelled before any agent started"
    db.log_append(tid, "daemon", msg)
    return {"ok": True, "state": "abandoned", "killed": killed}


@app.post("/tasks/{tid}/retry", dependencies=[Depends(_auth)])
def retry(tid: str, r: Retry) -> dict[str, Any]:
    t = db.task_get(tid)
    if not t:
        raise HTTPException(status_code=404, detail="no such task")
    if t["state"] not in ("failed", "abandoned", "blocked", "done"):
        return {"ok": False, "state": t["state"], "reason": "task is not in a retryable state"}
    if r.spec is not None or r.acceptance is not None:
        db.task_update_spec(tid, r.spec if r.spec is not None else t["spec"],
                            r.acceptance if r.acceptance is not None else t["acceptance"])
    if t["worktree"]:
        worktree.remove(tid, t["repo_path"])
    db.run("UPDATE tasks SET attempts = 0, max_attempts = ?, worktree = NULL WHERE id = ?", t["max_attempts"], tid)
    db.flags_clear(tid)
    if t["kind"] == "session":
        s = sessions.get(tid) or {}
        sessions.update(tid, turn="starting", attention=None, tmux=None, unread=0,
                        resume=bool(s.get("claude_session_id")), resume_note=(r.spec or "").strip())
    db.task_set_state(tid, "pending", claimed_by=None, lease_expires=None)
    db.log_append(tid, "daemon", ("session reopened" if t["kind"] == "session" else "retried from TUI")
                  + (" with new instructions" if r.spec else ""))
    return {"ok": True, "state": "pending"}


@app.delete("/tasks/{tid}", dependencies=[Depends(_auth)])
def task_delete(tid: str) -> dict[str, Any]:
    t = db.task_get(tid)
    if not t:
        raise HTTPException(status_code=404, detail="no such task")
    if t["state"] in ("running", "verifying", "claimed"):
        dispatcher.kill_running(tid)
    if t["worktree"]:
        worktree.remove(tid, t["repo_path"])
    db.task_delete(tid)
    return {"ok": True}


@app.get("/tasks/{tid}/diff", dependencies=[Depends(_auth)])
def task_diff(tid: str) -> dict[str, Any]:
    t = db.task_get(tid)
    if not t:
        raise HTTPException(status_code=404, detail="no such task")
    branch = worktree.task_branch(tid, t["repo_path"])
    import subprocess
    r = subprocess.run(["git", "-C", t["repo_path"], "diff", f"{t['base_ref']}...{branch}", "--stat"],
                       capture_output=True, text=True, timeout=30)
    full = subprocess.run(["git", "-C", t["repo_path"], "diff", f"{t['base_ref']}...{branch}"],
                          capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return {"ok": False, "error": r.stderr.strip()[:500], "branch": branch}
    return {"ok": True, "branch": branch, "stat": r.stdout, "diff": full.stdout[:200_000]}


@app.post("/tasks/{tid}/merge", dependencies=[Depends(_auth)])
def task_merge(tid: str, acknowledge_untested: bool = False) -> dict[str, Any]:
    t = db.task_get(tid)
    if not t:
        raise HTTPException(status_code=404, detail="no such task")
    if t["state"] != "done":
        return {"ok": False, "reason": "only done tasks can be merged"}
    if advisor.paused(t["project"]):
        return {"ok": False, "reason": f"project {t['project']} is paused; resume it first (hm_resume, or ask the chat agent)", "paused": True}
    untested = [f for f in db.flags_get(tid) if f.get("kind") == "untested"]
    if untested and not acknowledge_untested:
        return {"ok": False, "reason": "untested: " + untested[-1].get("summary", "") +
                "; review the diff, then merge with acknowledge_untested=true (or retry the task asking for tests)",
                "untested": True}
    branch = worktree.task_branch(tid, t["repo_path"])
    import subprocess
    cur = subprocess.run(["git", "-C", t["repo_path"], "rev-parse", "--abbrev-ref", "HEAD"],
                         capture_output=True, text=True, timeout=10).stdout.strip()
    r = subprocess.run(["git", "-C", t["repo_path"], "-c", "user.name=hiveswarm", "-c", "user.email=hiveswarm@localhost",
                        "merge", "--no-ff", "-m", f"Merge {branch}", branch],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        subprocess.run(["git", "-C", t["repo_path"], "merge", "--abort"], capture_output=True, timeout=10)
        return {"ok": False, "error": (r.stdout + r.stderr).strip()[:1000], "into": cur}
    if untested:
        db.flags_clear(tid, "untested")
        db.log_append(tid, "daemon", "merged although untested (acknowledged by the reviewer)")
    db.log_append(tid, "daemon", f"merged {branch} into {cur}")
    return {"ok": True, "into": cur, "output": r.stdout.strip()[:500]}


@app.post("/heartbeat", dependencies=[Depends(_auth)])
def heartbeat(h: Heartbeat) -> dict[str, Any]:
    cfg = load()
    db.agent_touch(h.agent_id)
    if h.task_id:
        t = db.task_get(h.task_id)
        if t is None or t["state"] == "abandoned":
            return {"ok": True, "cancelled": True}
        if t["state"] in ("claimed", "running"):
            db.task_set_state(h.task_id, "running", lease_expires=db.now() + int(cfg.get("daemon.lease_seconds", 1800)))
    return {"ok": True}
