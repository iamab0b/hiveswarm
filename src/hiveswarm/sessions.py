from __future__ import annotations

import json
import logging
import re
import shlex
from typing import Any

import httpx

from . import db
from .config import load

log = logging.getLogger("hiveswarm.sessions")

PERMISSION_MODES = ("auto", "acceptEdits", "bypass")
TURNS = ("starting", "working", "waiting", "idle", "exited")
ATTENTION_KINDS = ("permission", "question", "usage_limit", "input", "failed", "handoff")

RISK_QUESTIONS: dict[str, Any] = {
    "risk": {
        "type": "score",
        "instructions": (
            "How risky is it to let an autonomous coding agent run this command inside a sandboxed git worktree "
            "that only it can write to, with internet access but no access to the user's other files? "
            "low = reads, builds, tests, linters, installing packages, git operations inside the worktree; "
            "medium = deletes or rewrites many files, changes CI/config, network calls that publish or send data; "
            "high = anything destructive, secrets, credentials, force-push, curl|sh installers, modifying "
            "files outside the worktree, spending money."
        ),
        "criteria": ["low", "medium", "high"],
    },
}

_SECRET = re.compile(r"(?i)\b(secret|token|password|passwd|api[_-]?key|credential|\.ssh|\.env)\b")
_DESTRUCTIVE = re.compile(r"(?i)(rm\s+-rf\s+[~/]|git\s+push\s+.*--force|git\s+reset\s+--hard|curl[^|]*\|\s*(ba)?sh|"
                          r"wget[^|]*\|\s*(ba)?sh|sudo\b|chmod\s+-R\s+777|mkfs|dd\s+if=|:\(\)\s*\{)")


def default_session() -> dict[str, Any]:
    return {
        "agent": None, "permission_mode": "auto", "turn": "starting", "attention": None,
        "host": None, "tmux": None, "name": None, "claude_session_id": None, "transcript_path": None,
        "unread": 0, "last_viewed_seq": 0, "prompt": "", "started_at": None, "last_event_at": None,
        "turns": 0, "asks": 0, "auto_approved": 0, "lead": False, "persistent": False,
    }


def get(tid: str) -> dict[str, Any] | None:
    t = db.task_get(tid)
    if t is None or t["kind"] != "session":
        return None
    s = default_session()
    try:
        s.update(json.loads(t["session"] or "{}"))
    except Exception:
        pass
    return s


def put(tid: str, s: dict[str, Any]) -> None:
    db.run("UPDATE tasks SET session = ?, updated_at = ? WHERE id = ?", json.dumps(s), db.now(), tid)


def update(tid: str, **fields: Any) -> dict[str, Any] | None:
    s = get(tid)
    if s is None:
        return None
    s.update(fields)
    s["last_event_at"] = db.now()
    put(tid, s)
    return s


def create(project: str, spec: str, acceptance: str | None, repo_path: str, base_ref: str,
           agent: str, permission_mode: str = "auto", name: str | None = None, lead: bool = False,
           origin: str | None = None, persistent: bool = False) -> str:
    if permission_mode not in PERMISSION_MODES:
        raise ValueError(f"permission_mode must be one of {PERMISSION_MODES}")
    tid = db.task_insert(project, spec, acceptance, repo_path, base_ref)
    s = default_session()
    s.update({"agent": agent, "permission_mode": permission_mode, "prompt": spec,
              "name": name or f"hm-{tid[:8]}", "started_at": None, "lead": bool(lead), "persistent": bool(persistent)})
    db.run("UPDATE tasks SET kind = 'session', session = ?, max_attempts = 1 WHERE id = ?", json.dumps(s), tid)
    first = (spec.strip().splitlines() or [""])[0][:120]
    what = ("standing lead" if persistent else "lead session") if lead else "session"
    by = f" by {origin}" if origin else ""
    db.log_append(tid, "daemon", f"{what} requested for {agent} ({permission_mode}) in {project}{by}: {first}")
    return tid


def enqueue_command(tid: str, kind: str, payload: dict[str, Any] | None, host: str | None) -> str:
    cid = db.new_id()
    db.run("INSERT INTO session_commands (id, task_id, host, kind, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
           cid, tid, host, kind, json.dumps(payload or {}), db.now())
    return cid


def take_commands(host: str, limit: int = 50) -> list[dict[str, Any]]:
    t = db.now()
    with db.tx() as c:
        rows = c.execute("""SELECT sc.* FROM session_commands sc JOIN tasks t ON t.id = sc.task_id
                            WHERE (sc.taken_at IS NULL OR (sc.done_at IS NULL AND sc.taken_at < ?))
                              AND (sc.host = ? OR sc.host IS NULL) AND sc.created_at > ?
                            ORDER BY sc.created_at LIMIT ?""", (t - 45, host, t - 3600, limit)).fetchall()
        out = []
        for r in rows:
            c.execute("UPDATE session_commands SET taken_at = ? WHERE id = ?", (t, r["id"]))
            d = dict(r)
            try:
                d["payload"] = json.loads(d.get("payload") or "{}")
            except Exception:
                d["payload"] = {}
            out.append(d)
    return out


def finish_command(cid: str, result: str | None) -> None:
    db.run("UPDATE session_commands SET done_at = ?, result = ? WHERE id = ?", db.now(), (result or "")[:12000], cid)


def commands_for(tid: str, limit: int = 20) -> list[dict[str, Any]]:
    return [dict(r) for r in db.all_("SELECT * FROM session_commands WHERE task_id = ? ORDER BY created_at DESC LIMIT ?",
                                     tid, limit)]


def _risk_local(command: str) -> str:
    if _DESTRUCTIVE.search(command) or _SECRET.search(command):
        return "high"
    return "low"


def classify_risk(agent: str, tool: str, command: str, cwd: str | None = None) -> tuple[str, str]:
    cfg = load()
    mode = cfg.get("sessions.risk_gate", "pattern")
    quick = _risk_local(command)
    if quick == "high":
        return "high", "pattern"
    if mode == "off":
        return "medium", "off"
    if mode == "pattern":
        return quick, "pattern"
    url = cfg.get("decide.url", "http://127.0.0.1:9000").rstrip("/") + "/decide"
    state = f"agent: {agent}\ntool: {tool}\ncwd: {cwd or '(worktree)'}\ncommand:\n{command[:1500]}"
    try:
        r = httpx.post(url, json={"state": state, "questions": RISK_QUESTIONS}, timeout=20)
        r.raise_for_status()
        ans = r.json().get("answers", {}).get("risk", {})
        level = ans.get("level")
        if level not in ("low", "medium", "high"):
            score = float(ans.get("score", 1.0))
            level = "low" if score < 0.5 else ("medium" if score < 1.5 else "high")
        return level, r.json().get("source", "decide")
    except Exception as e:
        log.warning("risk classification failed (%s); treating as medium", e)
        return "medium", "fallback"


def auto_approve_allowed(agent: str, permission_mode: str, risk: str) -> bool:
    cfg = load()
    if permission_mode == "bypass":
        return True
    threshold = cfg.get("sessions.auto_approve_max_risk", "low")
    order = {"low": 0, "medium": 1, "high": 2}
    return order.get(risk, 2) <= order.get(threshold, 0)


def summarize_command(tool: str, tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        for k in ("command", "cmd", "file_path", "path", "pattern", "url", "prompt", "description"):
            v = tool_input.get(k)
            if v:
                return f"{tool}: {str(v).strip().splitlines()[0][:300]}"
        return f"{tool}: {json.dumps(tool_input)[:300]}"
    return f"{tool}: {str(tool_input)[:300]}"


def _fail_reason(tid: str, default: str) -> str:
    a = db.attempt_latest(tid)
    if a is None:
        return default
    log_ = (a["verifier_log"] or "").strip()
    first = next((ln.strip() for ln in log_.splitlines() if ln.strip()), "")
    if a["outcome"] == "fail" and a["branch"]:
        return f"verifier failed: {first[:160]}" if first else "verifier failed"
    if first:
        return f"{a['outcome'] or 'failed'}: {first[:160]}"
    return default


def inbox(limit: int = 100) -> list[dict[str, Any]]:
    rows = db.all_("""SELECT * FROM tasks WHERE (kind = 'session' AND session IS NOT NULL AND state NOT IN ('done','abandoned'))
                      OR state = 'failed' OR (flags IS NOT NULL AND state NOT IN ('done','failed','abandoned'))
                      ORDER BY updated_at DESC LIMIT 500""")
    out = []
    for r in rows:
        item = {"id": r["id"], "kind": r["kind"], "project": r["project"], "state": r["state"],
                "agent": r["claimed_by"], "spec": r["spec"], "updated_at": r["updated_at"]}
        flags = []
        if r["flags"] and r["state"] not in ("done", "failed", "abandoned"):
            try:
                flags = list(json.loads(r["flags"]))
            except Exception:
                flags = []
        if flags:
            f = flags[-1]
            fitem = dict(item)
            if r["kind"] == "session":
                s0 = default_session()
                try:
                    s0.update(json.loads(r["session"] or "{}"))
                except Exception:
                    pass
                fitem["agent"] = s0.get("agent") or r["claimed_by"]
                fitem["turn"] = s0.get("turn")
            fitem["attention"] = {"kind": f.get("kind", "directive"), "summary": f.get("summary", ""), "detail": f.get("detail", ""),
                                  "ref": f.get("ref"), "since": f.get("since") or r["updated_at"]}
            out.append(fitem)
        if r["kind"] == "session":
            s = default_session()
            try:
                s.update(json.loads(r["session"] or "{}"))
            except Exception:
                pass
            item["agent"] = s.get("agent") or r["claimed_by"]
            item["turn"] = s.get("turn")
            item["unread"] = s.get("unread", 0)
            if r["state"] == "failed":
                item["attention"] = {"kind": "failed", "summary": _fail_reason(r["id"], "session failed"),
                                     "since": r["updated_at"]}
                out.append(item)
            elif s.get("attention"):
                item["attention"] = s["attention"]
                out.append(item)
        elif r["state"] == "failed":
            item["attention"] = {"kind": "failed", "summary": _fail_reason(r["id"], "task failed after all attempts"),
                                 "since": r["updated_at"]}
            out.append(item)
    order = {"question": 0, "permission": 1, "directive": 2, "stalled": 3, "input": 4, "usage_limit": 5, "handoff": 6, "failed": 7}
    out.sort(key=lambda x: (order.get(x["attention"]["kind"], 9), x["attention"].get("since") or 0))
    return out[:limit]


def keys_for(agent: str, action: str, cfg_keys: dict[str, Any] | None = None) -> list[str]:
    table = {
        "claude_code": {"approve": ["1", "Enter"], "deny": ["3", "Enter"]},
        "codex": {"approve": ["Enter"], "deny": ["Down", "Enter"]},
    }
    default = {"approve": ["y", "Enter"], "deny": ["n", "Enter"]}
    keys = (cfg_keys or {}).get(action)
    if isinstance(keys, list) and keys:
        return [str(k) for k in keys]
    if isinstance(keys, str) and keys:
        return shlex.split(keys)
    return table.get(agent, default).get(action, default[action])


def progress_context(tid: str, max_lines: int = 40, max_chars: int = 6000) -> str:
    rows = db.all_("SELECT source, chunk FROM task_logs WHERE task_id = ? ORDER BY seq DESC LIMIT 400", tid)
    keep: list[str] = []
    for r in rows:
        src, chunk = r["source"], (r["chunk"] or "").strip()
        if not chunk:
            continue
        kind = src.split(":", 1)[1] if ":" in src else src
        if kind in ("msg", "tool", "you", "result"):
            keep.append(f"[{kind}] {chunk[:200 if kind == 'you' else 400]}")
        elif src == "daemon" and chunk.startswith(("question for you", "you chose", "you answered", "you denied", "you:")):
            keep.append(f"[{'human' if chunk.startswith('you') else 'ask'}] {chunk[:300]}")
        if len(keep) >= max_lines:
            break
    keep.reverse()
    text = "\n".join(keep)
    return text[-max_chars:]


def continue_session(tid: str, agent: str, permission_mode: str | None, branch: str | None) -> str:
    t = db.task_get(tid)
    s = get(tid) or default_session()
    ctx = progress_context(tid)
    prev_agent = s.get("agent") or "an agent"
    spec = t["spec"].strip()
    parts = [spec, "",
             f"This continues a session that {prev_agent} started but could not finish (usage limit or handoff)."]
    if branch:
        parts.append("Your worktree already contains its changes; review them before continuing.")
    else:
        parts.append("Its changes did not carry over, so start from the repository as it is.")
    if ctx:
        parts.append("\nWhat happened so far:\n" + ctx)
    parts.append("\nContinue from there.")
    pm = permission_mode or s.get("permission_mode") or "auto"
    new = create(t["project"], "\n".join(parts), t["acceptance"], t["repo_path"], branch or t["base_ref"], agent, pm,
                 lead=bool(s.get("lead")), origin=f"continued from {tid[:8]}")
    update(new, continued_from=tid)
    att = db.attempt_latest(tid)
    if att is not None and not att["outcome"]:
        db.attempt_finish(att["id"], "abandoned", verifier_log=f"handed off to {agent} in {new}", branch=branch)
    db.run("UPDATE tasks SET state = 'abandoned', claimed_by = NULL, lease_expires = NULL, updated_at = ? WHERE id = ?",
           db.now(), tid)
    update(tid, attention=None, turn="exited", pending_continue=None, continued_in=new)
    db.log_append(tid, "daemon", f"continued in {new[:8]} on {agent}" + (f" from branch {branch}" if branch else ""))
    return new


def find_lead(project: str, include_ended: bool = False) -> dict[str, Any] | None:
    if include_ended:
        rows = db.all_("SELECT * FROM tasks WHERE kind = 'session' AND project = ? ORDER BY created_at DESC", project)
    else:
        rows = db.all_("""SELECT * FROM tasks WHERE kind = 'session' AND project = ? AND state NOT IN ('done','failed','abandoned')
                          ORDER BY created_at DESC""", project)
    for r in rows:
        try:
            s = json.loads(r["session"] or "{}")
        except Exception:
            continue
        if s.get("lead") and s.get("persistent"):
            d = dict(r)
            d["session"] = {**default_session(), **s}
            return d
    return None
