"""The advisor: a chat about a project's plan and its swarm that runs beside the lead instead of interrupting it.

The human talks to the advisor in the app; each message becomes an *advisor turn* that a worker with Claude Code
runs headless (`claude -p … --resume`), with the hiveswarm MCP server in its advisor role: read-only tools, the
plan, pause/resume and briefs. Talking never touches the lead. When the human wants the swarm to change course the
advisor pauses the project (the dispatcher stops routing its tasks, merges wait), writes a brief for the lead (what
happened, what is in flight, what waits on whom, the human's update, how to proceed) and the lead picks it up
through hm_wait or its terminal, adjusts, and resumes.

The advisor answers in the i-have-adhd style (https://github.com/ayghri/i-have-adhd, MIT): next action first,
numbered steps, state restated, no preamble, no recap, no closers.
"""
from __future__ import annotations

import json
from typing import Any

from . import db

ADVISOR_STYLE = """## How to answer
Adapted from i-have-adhd (MIT). The human is busy and the swarm is running; every message must be usable at a glance.
1. Lead with the next action. The first line is what to do or what changed, not context.
2. Number multi-step things. Cap any list at 5 items.
3. Restate state every turn in one line: what the swarm is doing, what is paused, what waits on the human.
4. Give specific time estimates (minutes) when you estimate at all.
5. Make wins visible: merged, verified, done, in that order.
6. Errors matter-of-fact: what failed, the one line that explains it, the next step.
7. Suppress tangents. If something is interesting but not needed now, one line under "Later".
8. End with one concrete next step the human can take or approve.
9. No preamble, no recap, no closers, no apologies, no emoji."""


def system_prompt(project: str, plan: str | None, paused: dict[str, Any] | None, learned: str = "") -> str:
    parts = [
        f"You are the Hiveswarm advisor for project `{project}`. The human talks to you in the Hiveswarm app about the "
        "plan and the swarm's work while the lead agent keeps running. You never edit code and never dispatch work; the "
        "lead does that. Your tools are read-only views of the swarm (hm_status, hm_list, hm_get, hm_tail, hm_feed, "
        "hm_diff, hm_stats, hm_agents, hm_inbox, hm_deferred, hm_plan_get) plus hm_plan (save the plan), hm_pause / "
        "hm_resume, and hm_brief (hand the lead an update).",
        "",
        "Discussing is free: answer from the tools, keep the plan current with hm_plan when the human and you agree on "
        "a change to it, and do not touch the lead for that.",
        "",
        "Changing course: when the human wants the swarm to do something different from what it is doing (a new "
        "priority, a task to drop, a decision that affects work in flight), do this in order: 1. hm_pause(project, "
        "reason) so no new task starts; 2. look at hm_list / hm_inbox for what is in flight and what waits on whom; "
        "3. hm_brief(project, text) with five short sections: What happened, What is in flight, Waiting on, The "
        "human's update, How to proceed; 4. tell the human the lead has the brief and will resume once it has read "
        "it (the lead calls hm_resume). If no lead is running, say so and that the brief waits for the next lead.",
        "",
        ADVISOR_STYLE,
    ]
    if plan:
        parts += ["", "## Current plan", plan.strip()[:6000]]
    if paused:
        parts += ["", f"## The project is paused (by {paused.get('paused_by') or 'someone'}: {paused.get('reason') or 'no reason given'})"]
    if learned:
        parts += ["", learned, "hm_recall(query, project) finds more; hm_remember keeps a lesson the human states."]
    return "\n".join(parts)


# ── plan ────────────────────────────────────────────────────────────────

def plan_get(project: str) -> dict[str, Any] | None:
    r = db.one("SELECT * FROM plans WHERE project = ?", project)
    return dict(r) if r else None


def plan_set(project: str, text: str, by: str | None) -> dict[str, Any]:
    db.run("INSERT INTO plans (project, text, updated_at, updated_by) VALUES (?, ?, ?, ?) "
           "ON CONFLICT(project) DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at, updated_by = excluded.updated_by",
           project, text.strip(), db.now(), by)
    return plan_get(project) or {}


# ── pause ───────────────────────────────────────────────────────────────

def paused(project: str) -> dict[str, Any] | None:
    r = db.one("SELECT * FROM pauses WHERE project = ?", project)
    return dict(r) if r else None


def paused_projects() -> set[str]:
    return {r["project"] for r in db.all_("SELECT project FROM pauses")}


def pause(project: str, by: str | None, reason: str | None) -> dict[str, Any]:
    db.run("INSERT INTO pauses (project, paused_at, paused_by, reason) VALUES (?, ?, ?, ?) "
           "ON CONFLICT(project) DO UPDATE SET paused_by = excluded.paused_by, reason = excluded.reason",
           project, db.now(), by, reason)
    return paused(project) or {}


def resume(project: str) -> bool:
    was = paused(project)
    db.run("DELETE FROM pauses WHERE project = ?", project)
    return was is not None


# ── briefs ──────────────────────────────────────────────────────────────

def brief_add(project: str, text: str, by: str | None) -> dict[str, Any]:
    bid = db.new_id()
    db.run("INSERT INTO briefs (id, project, text, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
           bid, project, text.strip(), by, db.now())
    return dict(db.one("SELECT * FROM briefs WHERE id = ?", bid) or {})


def briefs(project: str, undelivered_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
    where = "AND delivered_at IS NULL" if undelivered_only else ""
    return [dict(r) for r in db.all_(f"SELECT * FROM briefs WHERE project = ? {where} ORDER BY created_at DESC LIMIT ?", project, limit)]


def brief_delivered(bid: str, via: str) -> None:
    db.run("UPDATE briefs SET delivered_at = ?, delivered_via = ? WHERE id = ? AND delivered_at IS NULL", db.now(), via, bid)


def brief_text_for_lead(b: dict[str, Any]) -> str:
    return f"[Brief from the advisor, {b.get('created_by') or 'advisor'}]\n{b['text'].strip()}\n\nRead it, adjust the plan and the swarm, then call hm_resume(project) so new work can start."


# ── conversation ────────────────────────────────────────────────────────

def messages(project: str, limit: int = 300) -> list[dict[str, Any]]:
    rows = db.all_("SELECT * FROM advisor_messages WHERE project = ? ORDER BY ts DESC, rowid DESC LIMIT ?", project, limit)
    return [dict(r) for r in reversed(rows)]


def message_add(project: str, role: str, text: str, kind: str = "text", turn_id: str | None = None, ts: float | None = None) -> str:
    import time as _time
    mid = db.new_id()
    db.run("INSERT INTO advisor_messages (id, project, turn_id, role, kind, text, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
           mid, project, turn_id, role, kind, text, ts if ts is not None else _time.time())
    return mid


def turn_add(project: str, text: str) -> str:
    tid = db.new_id()
    db.run("INSERT INTO advisor_turns (id, project, text, created_at) VALUES (?, ?, ?, ?)", tid, project, text, db.now())
    return tid


def turn_take(host: str, limit: int = 3) -> list[dict[str, Any]]:
    """Turns no worker has taken (or one took but never finished): oldest first, at most one per project."""
    t = db.now()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    with db.tx() as c:
        rows = c.execute("""SELECT * FROM advisor_turns WHERE done_at IS NULL AND (taken_at IS NULL OR taken_at < ?)
                            AND created_at > ? ORDER BY created_at LIMIT 20""", (t - 600, t - 3600)).fetchall()
        for r in rows:
            if r["project"] in seen or len(out) >= limit:
                continue
            busy = c.execute("SELECT 1 FROM advisor_turns WHERE project = ? AND taken_at IS NOT NULL AND done_at IS NULL AND taken_at >= ? AND id != ?",
                             (r["project"], t - 600, r["id"])).fetchone()
            if busy:
                continue
            c.execute("UPDATE advisor_turns SET taken_at = ?, host = ? WHERE id = ?", (t, host, r["id"]))
            d = dict(r)
            d["taken_at"] = t
            adv = c.execute("SELECT claude_session_id FROM advisors WHERE project = ?", (r["project"],)).fetchone()
            d["claude_session_id"] = adv["claude_session_id"] if adv else None
            out.append(d)
            seen.add(r["project"])
    return out


def turn_done(turn_id: str, host: str | None, claude_session_id: str | None, error: str | None) -> dict[str, Any] | None:
    r = db.one("SELECT * FROM advisor_turns WHERE id = ?", turn_id)
    if r is None:
        return None
    db.run("UPDATE advisor_turns SET done_at = ?, error = ? WHERE id = ?", db.now(), error, turn_id)
    if claude_session_id:
        db.run("INSERT INTO advisors (project, claude_session_id, host, updated_at) VALUES (?, ?, ?, ?) "
               "ON CONFLICT(project) DO UPDATE SET claude_session_id = excluded.claude_session_id, host = excluded.host, updated_at = excluded.updated_at",
               r["project"], claude_session_id, host, db.now())
    return dict(db.one("SELECT * FROM advisor_turns WHERE id = ?", turn_id) or {})


def state(project: str) -> dict[str, Any]:
    open_turn = db.one("SELECT id, created_at, taken_at, host FROM advisor_turns WHERE project = ? AND done_at IS NULL ORDER BY created_at DESC LIMIT 1", project)
    adv = db.one("SELECT * FROM advisors WHERE project = ?", project)
    return {"project": project, "thinking": bool(open_turn), "turn": dict(open_turn) if open_turn else None,
            "has_memory": bool(adv and adv["claude_session_id"]), "host": adv["host"] if adv else None,
            "plan": plan_get(project), "paused": paused(project),
            "briefs_pending": len(briefs(project, undelivered_only=True))}


def forget(project: str) -> None:
    db.run("DELETE FROM advisors WHERE project = ?", project)
    db.run("DELETE FROM advisor_messages WHERE project = ?", project)
    db.run("DELETE FROM advisor_turns WHERE project = ?", project)


def payload_json(d: dict[str, Any]) -> str:
    return json.dumps(d)
