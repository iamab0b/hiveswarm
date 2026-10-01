"""hiveswarm-mcp — an MCP server that lets a Claude Code session (the "lead") command Hiveswarm.

Runs over stdio. Talks to the Hiveswarm daemon with HIVESWARM_URL / HIVESWARM_TOKEN.
Register it once:  claude mcp add hiveswarm -e HIVESWARM_URL=http://<hub>:7778 -e HIVESWARM_TOKEN=<token> -- hiveswarm-mcp
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx

try:
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:
    from mcp.server.fastmcp import FastMCP as _Server

URL = os.environ.get("HIVESWARM_URL", "http://127.0.0.1:7778").rstrip("/")
TOKEN = os.environ.get("HIVESWARM_TOKEN", "")
ORIGIN = os.environ.get("HIVESWARM_ORIGIN", "")
ROLE = os.environ.get("HIVESWARM_ROLE", "lead")  # "advisor": read-only views plus the plan, pause/resume and briefs
_HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}

logging.getLogger("httpx").setLevel(logging.WARNING)

mcp = _Server(
    "hiveswarm",
    log_level="WARNING",
    instructions=(
        "Hiveswarm is a quota-aware task router across coding agents (Claude Code, Codex, Cursor, Gemini, and local models, "
        "Cursor and others on worker machines). Headless TASKS are planned, routed, run in isolated git worktrees, verified "
        "against an acceptance command and land as branches hiveswarm/<id>. Interactive SESSIONS are agents in their own "
        "tmux terminal that the human can watch and steer; you can steer them too (send, approve, deny, answer, finish). "
        "Typical loop: hm_projects → hm_add_tasks (2-8 independent, verifiable tasks) or hm_new_session → hm_wait → "
        "hm_diff / hm_tail → hm_merge, hm_retry with a better spec, or hm_cancel. Keep the human informed: they see "
        "everything you do in the Hiveswarm app. Save the plan with hm_plan; when hm_wait returns briefs from the "
        "advisor, act on them and hm_resume."
        + (" This server runs in the ADVISOR role: read-only views, the plan, pause/resume and briefs; dispatching "
           "and merging are the lead's." if ROLE == "advisor" else "")
    ),
)


class HmError(Exception):
    pass


def tool(advisor: bool = False):
    """Register a tool for the lead; with advisor=True also for the advisor role (which gets nothing else)."""
    if ROLE == "advisor" and not advisor:
        return lambda fn: fn
    return mcp.tool()


def _req(method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 60, **params: Any) -> Any:
    q = {k: v for k, v in params.items() if v is not None}
    try:
        r = httpx.request(method, URL + path, json=body, params=q, headers=_HEADERS, timeout=timeout)
    except httpx.HTTPError as e:
        raise HmError(f"cannot reach the Hiveswarm daemon at {URL}: {type(e).__name__}") from e
    if r.status_code == 401:
        raise HmError("unauthorized: HIVESWARM_TOKEN is missing or wrong")
    if r.status_code >= 400:
        raise HmError(f"{r.status_code}: {r.text[:300]}")
    return r.json() if r.content else None


def _get(path: str, **params: Any) -> Any:
    return _req("GET", path, **params)


def _post(path: str, body: dict[str, Any] | None = None, timeout: float = 60) -> Any:
    return _req("POST", path, body or {}, timeout=timeout)


def _session(t: dict[str, Any]) -> dict[str, Any]:
    s = t.get("session")
    if isinstance(s, dict):
        return s
    try:
        return json.loads(s or "{}")
    except Exception:
        return {}


def _row(t: dict[str, Any]) -> dict[str, Any]:
    s = _session(t)
    out = {"id": t["id"], "kind": t.get("kind", "task"), "state": t["state"], "project": t["project"],
           "agent": t.get("claimed_by") or s.get("agent"), "attempts": f"{t.get('attempts', 0)}/{t.get('max_attempts', 3)}",
           "spec": (t.get("spec") or "").strip().splitlines()[0][:120] if (t.get("spec") or "").strip() else "",
           "acceptance": t.get("acceptance"), "updated_at": t.get("updated_at")}
    if t["state"] == "done":
        out["branch"] = f"hiveswarm/{t['id']}"
    if s:
        out["turn"] = s.get("turn")
        if s.get("attention") and t["state"] not in ("done", "failed", "abandoned"):
            out["attention"] = s["attention"]
    fl = t.get("flags")
    if fl and t["state"] not in ("failed", "abandoned"):
        try:
            flags = json.loads(fl) if isinstance(fl, str) else list(fl)
        except Exception:
            flags = []
        if t["state"] == "done":  # only the untested flag outlives a task; it blocks hm_merge until reviewed
            flags = [f for f in flags if f.get("kind") == "untested"]
        if flags:
            out["flags"] = flags
            # a standing-order flag outranks "waiting for input"; a pending permission or question stays in front
            if not out.get("attention") or out["attention"].get("kind") == "input":
                out["attention"] = {"kind": flags[-1].get("kind", "directive"), "summary": flags[-1].get("summary", ""),
                                    "detail": flags[-1].get("detail", ""), "ref": flags[-1].get("ref")}
    return out


def _fmt_log(entries: list[dict[str, Any]]) -> str:
    lines = []
    for e in entries:
        ts = time.strftime("%H:%M:%S", time.localtime(int(e.get("ts") or 0)))
        chunk = (e.get("chunk") or "").rstrip()
        lines.append(f"{ts} {e.get('source', ''):<18} {chunk}")
    return "\n".join(lines)


# ── overview ────────────────────────────────────────────────────────────

@tool(advisor=True)
def hm_status() -> dict[str, Any]:
    """Overall state: task counts by state, agents alive (with capacity and busy lanes), and how many items need a human."""
    s = _get("/summary")
    agents = _get("/agents")
    inbox = _get("/inbox")
    return {"tasks": s.get("tasks", {}), "dispatcher_alive": s.get("dispatcher_alive"),
            "agents": [{"agent": a["agent_id"], "host": a.get("host"), "alive": a.get("alive"), "capacity": a.get("capacity", 1),
                        "busy": a.get("busy", 0), "capabilities": a.get("capabilities")} for a in agents],
            "needs_human": inbox.get("count", 0), "attempts_24h": s.get("attempts_24h", {})}


@tool(advisor=True)
def hm_projects() -> dict[str, Any]:
    """List projects Hiveswarm knows (name → repo path on the hub, verify image, acceptance templates)."""
    return _get("/projects")


@tool()
def hm_delete_project(project: str, purge: bool = False) -> dict[str, Any]:
    """Remove a project from Hiveswarm: its tasks, sessions, logs, standing orders and worktrees. Only when the human
    asked for it. purge=True also deletes the repository on the hub (and local copies go to trash) — never
    set it unless the human said to delete the project's files too."""
    return _req("DELETE", f"/projects/{project}?purge={'true' if purge else 'false'}")


@tool()
def hm_new_project(name: str) -> dict[str, Any]:
    """Create a new empty git project on the hub under projects_root and register it. Name: letters, digits, - and _."""
    return _post("/projects", {"name": name}, timeout=120)


@tool(advisor=True)
def hm_agents() -> list[dict[str, Any]]:
    """Agents registered with the daemon and the lanes the hub runs itself (`local: true`): liveness, host, capacity
    (lanes running), `configured` (lanes in the worker's worker.toml), busy lanes, provider/model/effort, capabilities
    (e.g. 'sessions')."""
    return _get("/agents")


@tool(advisor=True)
def hm_stats() -> dict[str, Any]:
    """How each agent performs, per kind of work: pass rate, average wall time, average and p90 seconds per tool
    step, slow steps (over 120s), trend, and a status — healthy, slow, failing, unknown. `agents` is the per-agent
    summary with its probation list; `cells` is agent × task_type. A slow or failing agent is on probation for that
    task type: Hiveswarm stops routing it hard work of that type but keeps giving it easy pieces so it can recover —
    do the same when you pick agents, and prefer another agent for anything that matters."""
    return _get("/stats/agents")


@tool(advisor=True)
def hm_live(task_id: str) -> dict[str, Any]:
    """What a running lane is doing right now: its open tool step (and how long it has taken), when it last
    produced output, and how many steps it has done. Use it when hm_wait reports a `stalled` lane before deciding
    to cancel it."""
    return _get(f"/tasks/{task_id}/live")


# ── headless tasks ──────────────────────────────────────────────────────

@tool()
def hm_add_tasks(project: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Enqueue headless tasks. Each item: {"spec": "...", "acceptance": "shell command that exits 0 when done" | null,
    "agent": "claude_code" | "codex" | ... | null}. Pass the agent hm_advise recommended; the router honors it while
    that agent is alive and has a free lane, otherwise it picks. Tasks run independently in their own worktree from the
    same base commit, so they must not depend on each other. The acceptance command runs in a clean container checkout
    (python 3.12+pytest, node 22, rust, C/C++; it must install its own dependencies, e.g. "npm ci && npm test").
    Returns the task ids."""
    ids = []
    for t in tasks:
        spec = (t.get("spec") or "").strip()
        if not spec:
            continue
        r = _post("/tasks", {"project": project, "spec": spec, "acceptance": t.get("acceptance") or None,
                             "agent": t.get("agent") or None, "origin": ORIGIN or None})
        ids.append(r["id"])
    return {"ids": ids, "count": len(ids)}


@tool()
def hm_advise(project: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Size the swarm before dispatching. For each planned task ({"spec": "..."}), the decide model classifies
    it and Hiveswarm's routing data picks the agent: returns per task the mode (headless or interactive), the recommended
    agent with a pass estimate and the reason, alternatives, plus how many lanes are free per agent right now and how
    many waves the plan needs. Call this after planning and before hm_add_tasks / hm_new_session."""
    return _post("/advise", {"project": project, "tasks": [{"spec": t.get("spec") or ""} for t in tasks][:20]}, timeout=180)


@tool(advisor=True)
def hm_list(project: str | None = None, state: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """List tasks and sessions (newest first). state: pending|classified|assigned|running|verifying|done|failed|abandoned."""
    rows = _get("/tasks", project=project, state=state, limit=limit)
    return [_row(t) for t in rows]


@tool(advisor=True)
def hm_get(task_id: str) -> dict[str, Any]:
    """Full detail for one task or session: spec, acceptance, classification, every attempt (agent, outcome,
    verifier log excerpt, branch), and the session state if it is a session."""
    d = _get(f"/tasks/{task_id}")
    t = d["task"]
    out = {"task": _row(t), "spec": t.get("spec"), "classification": d.get("classification"),
           "attempts": [{"agent": a.get("agent"), "outcome": a.get("outcome"), "wall_seconds": a.get("wall_seconds"),
                         "branch": a.get("branch"), "diff_stat": a.get("diff_stat"),
                         "verifier_log": (a.get("verifier_log") or "")[-1500:]} for a in d.get("attempts", [])]}
    if t.get("kind") == "session":
        out["session"] = _session(t)
    return out


@tool(advisor=True)
def hm_tail(task_id: str, lines: int = 60) -> str:
    """The last N lines of a task's or session's live log: what the agent said, tools it ran, verifier output,
    routing decisions and handoffs."""
    d = _get(f"/tasks/{task_id}/log", since=0)
    entries = d.get("entries", [])[-max(1, min(lines, 500)):]
    return _fmt_log(entries) or "(no log yet)"


@tool(advisor=True)
def hm_feed(tail: int = 80) -> str:
    """The Hive feed: the most recent coordination events across all tasks and agents."""
    d = _get("/feed", tail=max(1, min(tail, 1000)))
    entries = d.get("entries", [])
    lines = []
    for e in entries:
        ts = time.strftime("%H:%M:%S", time.localtime(int(e.get("ts") or 0)))
        lines.append(f"{ts} {e['task_id'][:8]} {e.get('source', ''):<18} {(e.get('chunk') or '').rstrip()[:300]}")
    return "\n".join(lines) or "(empty)"


_seen_flags: set[tuple[str, str]] = set()


@tool()
def hm_wait(task_ids: list[str], timeout_seconds: int = 120, stop_on_attention: bool = True) -> dict[str, Any]:
    """Block until every listed task/session is terminal (done/failed/abandoned), or one of them needs attention
    (a question, an approval, a session waiting for input, a `directive` flag from a standing-order audit, or a
    `stalled` lane stuck on one step) when stop_on_attention is true, or the advisor sends a brief, or the timeout
    passes. Call it again to keep waiting. Returns each id's state and, when relevant, its attention item; `briefs`
    carries the advisor's updates (act on them, then hm_resume) and `paused` the projects on hold."""
    deadline = time.time() + max(5, min(timeout_seconds, 290))
    while True:
        rows = {t["id"]: t for t in _get("/tasks", limit=500)}
        out: dict[str, Any] = {}
        pending = False
        attention = False
        briefs: list[dict[str, Any]] = []
        projects = {rows[t]["project"] for t in task_ids if t in rows}
        for proj in sorted(projects):
            for b in (_get(f"/projects/{proj}/briefs", undelivered="true") or {}).get("briefs", []):
                _post(f"/briefs/{b['id']}/delivered")
                briefs.append({"project": proj, "text": b["text"], "from": b.get("created_by"), "at": b.get("created_at")})
        paused = sorted(p for p in projects if (_get("/projects") or {}).get(p, {}).get("paused"))
        for tid in task_ids:
            t = rows.get(tid)
            if t is None:
                out[tid] = {"state": "unknown"}
                continue
            r = _row(t)
            out[tid] = {"state": r["state"], "agent": r.get("agent"), "branch": r.get("branch")}
            if r.get("flags"):
                out[tid]["flags"] = r["flags"]
            if t["state"] not in ("done", "failed", "abandoned"):
                pending = True
            att = r.get("attention")
            if att and att.get("kind") in ("question", "permission", "input", "usage_limit", "directive", "stalled"):
                out[tid]["attention"] = att
                if att.get("kind") in ("directive", "stalled"):
                    # a standing-order flag interrupts the wait once; afterwards it is still listed but the lead
                    # decides (correct, cancel, or hm_clear_flag) without hm_wait returning instantly every call
                    key = (tid, str(att.get("summary", ""))[:200])
                    if key not in _seen_flags:
                        _seen_flags.add(key)
                        attention = True
                else:
                    attention = True
        if briefs or not pending or (attention and stop_on_attention) or time.time() >= deadline:
            res = {"done": not pending, "needs_attention": attention, "timed_out": pending and time.time() >= deadline and not briefs,
                   "tasks": out}
            if briefs:
                res["briefs"] = briefs
                res["note"] = "the advisor briefed you: read it, adjust the plan (hm_plan) and the swarm, then hm_resume(project)"
            if paused:
                res["paused"] = paused
            return res
        time.sleep(3)


@tool(advisor=True)
def hm_diff(task_id: str, max_chars: int = 20000) -> dict[str, Any]:
    """The diff a done task produced on its branch (base...hiveswarm/<id>): stat plus the patch, truncated."""
    r = _get(f"/tasks/{task_id}/diff")
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error")}
    diff = r.get("diff") or ""
    return {"ok": True, "branch": r.get("branch"), "stat": r.get("stat"),
            "diff": diff[:max_chars] + ("\n… (truncated)" if len(diff) > max_chars else "")}


@tool()
def hm_merge(task_id: str, acknowledge_untested: bool = False) -> dict[str, Any]:
    """Merge a done task's branch into the project's current branch on the hub. Review hm_diff first. A task flagged
    `untested` (code changed without a test touched or run) is refused unless acknowledge_untested=True; prefer
    hm_retry asking for the test, and when you do acknowledge, tell the human why it is safe."""
    return _req("POST", f"/tasks/{task_id}/merge", {}, timeout=180, acknowledge_untested="true" if acknowledge_untested else None)


@tool(advisor=True)
def hm_deferred(project: str, include_resolved: bool = False) -> dict[str, Any]:
    """The project's deferred ledger: shortcuts and loose ends agents reported under Deferred in their handoffs,
    newest first. Fold the ones that matter into the next wave; hm_deferred_resolve closes an item."""
    r = _get(f"/projects/{project}/deferred", include_resolved="true" if include_resolved else None)
    items = [{"id": i["id"], "text": i["text"], "task_id": i["task_id"], "agent": i["agent"], "created_at": i["created_at"],
              "resolved": bool(i["resolved_at"])} for i in r.get("items", [])]
    return {"project": project, "open": r.get("open", 0), "items": items}


@tool()
def hm_deferred_resolve(deferred_id: str, note: str | None = None) -> dict[str, Any]:
    """Close a deferred item (it was done, or decided against); say why in note."""
    return _post(f"/deferred/{deferred_id}/resolve", {"by": ORIGIN or "lead", "note": note})


@tool(advisor=True)
def hm_plan_get(project: str) -> dict[str, Any]:
    """The project's stored plan (markdown) and when it was last saved, plus whether the project is paused."""
    r = _get(f"/projects/{project}/plan")
    paused = (_get("/projects") or {}).get(project, {}).get("paused")
    return {"project": project, "plan": (r.get("plan") or {}).get("text"), "updated_at": (r.get("plan") or {}).get("updated_at"),
            "updated_by": (r.get("plan") or {}).get("updated_by"), "paused": bool(paused)}


@tool(advisor=True)
def hm_plan(project: str, text: str) -> dict[str, Any]:
    """Save the project's plan (markdown): goals, the waves and their tasks, decisions taken, what is deferred. The
    lead saves it after planning and updates it as things change; the advisor updates it when the human changes
    it; the human sees it on the Lead page."""
    return _req("PUT", f"/projects/{project}/plan", {"text": text, "by": ORIGIN or ROLE})


@tool(advisor=True)
def hm_pause(project: str, reason: str) -> dict[str, Any]:
    """Pause the project: no new task or session of it starts and nothing merges until hm_resume. Agents already
    working keep going. Use it before changing course so the brief reaches the lead before more work starts."""
    return _post(f"/projects/{project}/pause", {"by": ORIGIN or ROLE, "reason": reason})


@tool(advisor=True)
def hm_resume(project: str) -> dict[str, Any]:
    """Resume a paused project. The lead calls this once it has read a brief and adjusted; the advisor or the human
    can call it too."""
    return _post(f"/projects/{project}/resume")


@tool(advisor=True)
def hm_brief(project: str, text: str) -> dict[str, Any]:
    """Hand the lead an update, in five short sections: What happened, What is in flight, Waiting on, The human's
    update, How to proceed. The lead receives it from hm_wait (and in its terminal when it is idle) and is told to
    hm_resume once it has acted on it."""
    return _post(f"/projects/{project}/briefs", {"text": text, "by": ORIGIN or ROLE})


@tool(advisor=True)
def hm_recall(query: str, project: str | None = None, limit: int = 8) -> dict[str, Any]:
    """What the swarm learned before that matches the query: failed attempts and why, work that landed, lessons
    the human or the lead stored. The project's memories and the global ones, best first. Empty when memory is
    off ([memory] backend in config.toml)."""
    return _get("/memory/recall", q=query, project=project, limit=limit)


@tool(advisor=True)
def hm_remember(text: str, project: str | None = None, global_scope: bool = False) -> dict[str, Any]:
    """Keep a lesson for next time (one or two sentences: the situation and what to do). Project-scoped unless
    global_scope is true; the daemon already stores failures and finished work on its own."""
    return _post("/memory/remember", {"text": text, "project": project, "global_scope": global_scope, "by": ORIGIN or ROLE})


@tool()
def hm_briefs(project: str) -> list[dict[str, Any]]:
    """Briefs for the lead on this project, newest first, with whether each was delivered."""
    return (_get(f"/projects/{project}/briefs") or {}).get("briefs", [])


@tool()
def hm_retry(task_id: str, spec: str | None = None, acceptance: str | None = None) -> dict[str, Any]:
    """Retry a failed/abandoned task (optionally with an amended spec or acceptance), or reopen an ended session
    (a Claude Code session resumes with its previous context and files; spec becomes the new instruction)."""
    return _post(f"/tasks/{task_id}/retry", {"spec": spec, "acceptance": acceptance})


@tool()
def hm_cancel(task_id: str) -> dict[str, Any]:
    """Cancel a running task or session (kills the agent)."""
    return _post(f"/tasks/{task_id}/cancel")


@tool()
def hm_delete(task_id: str) -> dict[str, Any]:
    """Delete a task or session and its worktree. Cannot be undone."""
    return _req("DELETE", f"/tasks/{task_id}")


# ── interactive sessions ────────────────────────────────────────────────

@tool()
def hm_new_session(project: str, prompt: str, agent: str = "claude_code", permission_mode: str = "auto",
                   acceptance: str | None = None, count: int = 1) -> dict[str, Any]:
    """Open interactive agent session(s) in their own tmux terminal on a worker machine. The human can watch and steer
    them in the TUI; so can you (hm_send / hm_approve / hm_deny / hm_answer / hm_finish). permission_mode:
    auto (low-risk actions approved automatically, risky ones and questions come to the inbox), acceptEdits, bypass.
    count > 1 opens several copies of the same prompt (parallel attempts). Returns the session ids."""
    r = _post("/sessions", {"project": project, "spec": prompt, "agent": agent, "permission_mode": permission_mode,
                            "acceptance": acceptance, "count": max(1, min(count, 12)), "origin": ORIGIN or None})
    return {"ids": r.get("ids") or [r.get("id")]}


@tool(advisor=True)
def hm_sessions() -> list[dict[str, Any]]:
    """List interactive sessions with their turn (starting/working/waiting/idle/exited) and any pending attention item."""
    return [_row(t) for t in _get("/sessions", limit=100)]


@tool(advisor=True)
def hm_inbox() -> list[dict[str, Any]]:
    """Everything that needs a decision: questions (with options), permission requests (with risk), sessions waiting
    for input, usage limits, failures. Ordered by urgency."""
    return _get("/inbox").get("items", [])


@tool()
def hm_send(session_id: str, text: str) -> dict[str, Any]:
    """Send a message (an instruction or a free-text answer) to a live session."""
    return _post(f"/sessions/{session_id}/send", {"text": text})


@tool()
def hm_approve(session_id: str) -> dict[str, Any]:
    """Approve the action a session is asking permission for. Check hm_inbox first and think about the risk."""
    return _post(f"/sessions/{session_id}/answer", {"choice": "approve"})


@tool()
def hm_deny(session_id: str, message: str | None = None) -> dict[str, Any]:
    """Deny the action a session is asking permission for, optionally telling it what to do instead."""
    if message:
        return _post(f"/sessions/{session_id}/answer", {"choice": "text", "text": message})
    return _post(f"/sessions/{session_id}/answer", {"choice": "deny"})


@tool()
def hm_answer(session_id: str, option: int | None = None, text: str | None = None) -> dict[str, Any]:
    """Answer a session's multiple-choice question by option number (1-based) or with free text."""
    if option is not None:
        return _post(f"/sessions/{session_id}/answer", {"choice": str(option)})
    return _post(f"/sessions/{session_id}/answer", {"choice": "text", "text": text or ""})


@tool()
def hm_finish(session_id: str) -> dict[str, Any]:
    """Finish a session: its worktree is committed, pushed as hiveswarm/<id>, verified, and the terminal closes."""
    return _post(f"/sessions/{session_id}/finish")


@tool()
def hm_directive(text: str, task_id: str | None = None, project: str | None = None, every_tools: int = 8,
                 every_minutes: int = 10, check: bool = True) -> dict[str, Any]:
    """Set a standing order — a paramount rule the agent must keep following (e.g. "never copy code from a repository
    we do not have permission to copy from; write it yourself", "never touch files outside src/billing"). Attach it to
    one task/session (task_id) or to every current and future agent in a project (project). Hiveswarm re-injects it into
    the agent every `every_tools` tool calls or `every_minutes` minutes, whichever comes first (Claude Code agents get it
    mid-turn through hooks; other agents get it between turns), and with check=True asks the decide model after each reminder whether
    the agent's recent activity breaks the order — a likely violation is flagged to you and the human (hm_wait reports
    it as attention kind "directive"). Returns the directive id."""
    return _post("/directives", {"text": text, "task_id": task_id, "project": project, "every_tools": every_tools,
                                 "every_minutes": every_minutes, "check": check, "created_by": ORIGIN or "lead"})


@tool(advisor=True)
def hm_directives(project: str | None = None, task_id: str | None = None) -> list[dict[str, Any]]:
    """List active standing orders for a project or a task, with how often each fired and the last audit result."""
    return _get("/directives", project=project, task_id=task_id)


@tool()
def hm_directive_clear(directive_id: str) -> dict[str, Any]:
    """Remove a standing order."""
    return _req("DELETE", f"/directives/{directive_id}")


@tool()
def hm_clear_flag(task_id: str, kind: str | None = None) -> dict[str, Any]:
    """Clear a task's or session's flags: kind "directive" (a standing-order audit you reviewed and corrected or
    judged a false alarm), "stalled" (a long step you decided is legitimate), or omit kind to clear all of them."""
    return _post(f"/tasks/{task_id}/flags/clear" + (f"?kind={kind}" if kind else ""))


@tool()
def hm_continue(session_id: str, agent: str = "codex", permission_mode: str | None = None) -> dict[str, Any]:
    """Hand a session over to another agent (e.g. after a usage limit): its worktree is saved, the new agent starts
    from those changes with a summary of what happened, and the old session closes."""
    return _post(f"/sessions/{session_id}/continue", {"agent": agent, "permission_mode": permission_mode})


@tool(advisor=True)
def hm_screen(session_id: str, lines: int = 40) -> str:
    """What a session's terminal currently shows (last N lines), fetched from the worker that hosts it."""
    r = _post(f"/sessions/{session_id}/send", {"screen": True, "lines": max(5, min(lines, 200))})
    cid = r.get("command_id")
    if not cid:
        return "(no screen)"
    for _ in range(20):
        time.sleep(0.5)
        c = _get(f"/sessions/commands/{cid}")
        if c.get("done_at"):
            return c.get("result") or "(empty screen)"
    return "(the session host did not answer in time)"


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
