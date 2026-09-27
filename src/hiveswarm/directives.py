"""Standing orders: a directive attached to a task, a session, or a whole project, re-injected into the agent on a
cadence and (optionally) audited by the decide model against the agent's recent activity."""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from . import db
from .config import load

log = logging.getLogger("hiveswarm.directives")

AUDIT_QUESTIONS: dict[str, Any] = {
    "violation": {
        "type": "noul",
        "instructions": "The agent's recent activity violates, or is clearly about to violate, the standing order",
        "criteria": {
            "true": "An action, file content, command, or stated plan in the recent activity breaks the order",
            "false": "The recent activity is consistent with the order, or unrelated to it",
        },
    },
}

VIOLATION_THRESHOLD = 0.6


def create(text: str, task_id: str | None, project: str | None, every_tools: int = 8, every_minutes: int = 10,
           check: bool = True, created_by: str | None = None) -> str:
    text = text.strip()
    if not text:
        raise ValueError("directive text is empty")
    if not task_id and not project:
        raise ValueError("a directive needs a task_id or a project")
    if task_id:
        t = db.task_get(task_id)
        if t is None:
            raise ValueError(f"no such task {task_id}")
        project = project or t["project"]
    did = db.new_id()
    db.run("""INSERT INTO directives (id, task_id, project, text, every_tools, every_minutes, check_jev, created_by, created_at)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
           did, task_id, None if task_id else project, text, max(1, int(every_tools)), max(1, int(every_minutes)),
           1 if check else 0, created_by, db.now())
    scope = f"this {'session' if (task_id and (db.task_get(task_id) or {})['kind'] == 'session') else 'task'}" if task_id else f"every agent in {project}"
    by = f" by {created_by}" if created_by else ""
    line = (f"standing order{by} for {scope} (every {every_tools} tools / {every_minutes} min"
            f"{', audited' if check else ''}): {text[:300]}")
    if task_id:
        db.log_append(task_id, "daemon", line)
    else:
        for r in db.all_("SELECT id FROM tasks WHERE project = ? AND state NOT IN ('done','failed','abandoned')", project):
            db.log_append(r["id"], "daemon", line)
    return did


def deactivate(did: str) -> bool:
    row = db.one("SELECT * FROM directives WHERE id = ?", did)
    if not row:
        return False
    db.run("UPDATE directives SET active = 0 WHERE id = ?", did)
    if row["task_id"]:
        db.log_append(row["task_id"], "daemon", f"standing order cleared: {row['text'][:120]}")
    return True


def _row(r: Any) -> dict[str, Any]:
    d = dict(r)
    d["check"] = bool(d.pop("check_jev", 1))
    d["active"] = bool(d.get("active", 1))
    return d


def for_task(tid: str) -> list[dict[str, Any]]:
    t = db.task_get(tid)
    if t is None:
        return []
    rows = db.all_("""SELECT * FROM directives WHERE active = 1 AND (task_id = ? OR (task_id IS NULL AND project = ?))
                      ORDER BY created_at""", tid, t["project"])
    return [_row(r) for r in rows]


def list_(project: str | None = None, task_id: str | None = None, include_inactive: bool = False) -> list[dict[str, Any]]:
    clauses, params = [], []
    if task_id:
        clauses.append("task_id = ?"); params.append(task_id)
    elif project:
        clauses.append("(project = ? OR task_id IN (SELECT id FROM tasks WHERE project = ?))"); params.extend([project, project])
    if not include_inactive:
        clauses.append("active = 1")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return [_row(r) for r in db.all_(f"SELECT * FROM directives {where} ORDER BY created_at DESC LIMIT 200", *params)]


def reminder_text(directives: list[dict[str, Any]]) -> str:
    if not directives:
        return ""
    if len(directives) == 1:
        return ("Reminder from the Hiveswarm lead — a standing order for this work, which overrides convenience: "
                f"{directives[0]['text'].strip()} Check your last steps against it before continuing, and say so in one line if you had to change course.")
    lines = "\n".join(f"{i}. {d['text'].strip()}" for i, d in enumerate(directives, 1))
    return ("Reminder from the Hiveswarm lead — standing orders for this work, which override convenience:\n"
            f"{lines}\nCheck your last steps against them before continuing, and say so in one line if you had to change course.")


def prompt_block(directives: list[dict[str, Any]]) -> str:
    if not directives:
        return ""
    lines = "\n".join(f"- {d['text'].strip()}" for d in directives)
    return ("## Standing orders (paramount — they override everything below)\n"
            f"{lines}\nHiveswarm will remind you of these as you work; if a step would break one, stop and ask instead.\n")


def _recent_activity(tid: str, lines: int = 30) -> str:
    rows = db.all_("SELECT source, chunk FROM task_logs WHERE task_id = ? ORDER BY seq DESC LIMIT 200", tid)
    keep: list[str] = []
    for r in rows:
        src = r["source"]
        kind = src.split(":", 1)[1] if ":" in src else src
        if kind in ("msg", "tool", "result", "out", "you", "think"):
            keep.append(f"[{kind}] {(r['chunk'] or '').strip()[:500]}")
        if len(keep) >= lines:
            break
    keep.reverse()
    return "\n".join(keep)[-8000:]


def audit(did: str, tid: str, screen: str | None = None) -> dict[str, Any]:
    """Ask the decide model whether the agent's recent activity (its log, plus the terminal screen for agents without hooks)
    breaks the standing order; on a likely violation, flag the task so the lead and the inbox see it."""
    row = db.one("SELECT * FROM directives WHERE id = ?", did)
    if not row:
        return {"checked": False, "reason": "no such directive"}
    recent = _recent_activity(tid)
    if screen and screen.strip():
        recent = (recent + "\n[screen]\n" + screen.strip()[-4000:]).strip()
    if not recent.strip():
        return {"checked": False, "reason": "no activity yet"}
    cfg = load()
    url = cfg.get("decide.url", "http://127.0.0.1:9000").rstrip("/") + "/decide"
    state = f"Standing order for the agent:\n{row['text']}\n\nAgent's recent activity (newest last):\n{recent}"
    try:
        r = httpx.post(url, json={"state": state, "questions": AUDIT_QUESTIONS}, timeout=30)
        r.raise_for_status()
        body = r.json()
        ans = (body.get("answers") or {}).get("violation") or {}
        p = float(ans.get("noul", ans.get("probability", 0.0)))
        source = body.get("source", "decide")
    except Exception as e:
        log.warning("directive audit failed: %s", e)
        return {"checked": False, "reason": f"audit unavailable: {e}"}
    violated = p >= VIOLATION_THRESHOLD
    db.run("UPDATE directives SET last_result = ? WHERE id = ?", json.dumps({"p": p, "violated": violated, "at": db.now()}), did)
    if violated:
        db.run("UPDATE directives SET violations = violations + 1 WHERE id = ?", did)
        summary = f"may be breaking a standing order ({int(p * 100)}% via {source}): {row['text'][:160]}"
        db.flag_add(tid, "directive", summary, detail=recent[-1500:], ref=did)
        db.log_append(tid, "daemon", f"directive check failed: {summary}")
    else:
        db.log_append(tid, "daemon", f"directive check passed ({int((1 - p) * 100)}% via {source}): {row['text'][:80]}")
    return {"checked": True, "violated": violated, "p": p, "source": source}


def fired(did: str, tid: str, how: str, screen: str | None = None) -> dict[str, Any]:
    row = db.one("SELECT * FROM directives WHERE id = ?", did)
    if not row:
        return {"ok": False}
    db.run("UPDATE directives SET fires = fires + 1, last_fired_at = ? WHERE id = ?", db.now(), did)
    db.log_append(tid, "daemon", f"reminded ({how}) of the standing order: {row['text'][:120]}")
    out: dict[str, Any] = {"ok": True, "fires": int(row["fires"]) + 1}
    if row["check_jev"]:
        out["audit"] = audit(did, tid, screen=screen)
    return out
