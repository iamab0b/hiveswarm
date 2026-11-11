"""Steering a project while its lead works: the plan, pauses and briefs.

The chat agent (see chat.py) and the lead share these. The plan is the project's stored markdown plan. A pause
stops the dispatcher from routing the project's tasks and holds its merges. A brief is an update for the lead (what
happened, what is in flight, what waits on whom, the human's update, how to proceed); the lead picks it up through
hm_wait or, when it sits at its prompt, in its terminal, adjusts, and resumes the project.

Until 0.3 this module also ran "the advisor", a headless chat with a conversation stored on the hub. The chat agent
replaced it in 0.4: a real Claude Code session in a terminal. The module keeps its name, and the answer style
(i-have-adhd, https://github.com/ayghri/i-have-adhd, MIT: next action first, numbered steps, state restated, no
preamble, no recap, no closers) that the chat agent uses.
"""
from __future__ import annotations

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
    return f"[Brief from the human, through the chat agent]\n{b['text'].strip()}\n\nRead it, adjust the plan and the swarm, then call hm_resume(project) so new work can start."
