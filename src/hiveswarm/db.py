from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import load

_local = threading.local()
_SCHEMA = Path(__file__).resolve().parent / "schema.sql"


def now() -> int:
    return int(time.time())


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def _connect() -> sqlite3.Connection:
    cfg = load()
    path = cfg.get("paths.db")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        c = _connect()
        _local.conn = c
    return c


_COLUMNS = {
    "agents": [("capacity", "INTEGER NOT NULL DEFAULT 1")],
    "tasks": [("kind", "TEXT NOT NULL DEFAULT 'task'"), ("session", "TEXT"), ("preferred_agent", "TEXT"), ("flags", "TEXT")],
    "attempts": [("steps", "INTEGER"), ("step_avg_s", "REAL"), ("step_p90_s", "REAL"), ("step_max_s", "REAL"),
                 ("slow_steps", "INTEGER"), ("silence_max_s", "REAL"), ("first_action_s", "REAL"), ("think_avg_s", "REAL")],
}


def migrate() -> None:
    sql = _SCHEMA.read_text()
    c = conn()
    c.executescript(sql)
    for table, cols in _COLUMNS.items():
        have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, decl in cols:
            if name not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    c = conn()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise


def one(sql: str, *params: Any) -> sqlite3.Row | None:
    return conn().execute(sql, params).fetchone()


def all_(sql: str, *params: Any) -> list[sqlite3.Row]:
    return conn().execute(sql, params).fetchall()


def run(sql: str, *params: Any) -> None:
    conn().execute(sql, params)


def task_insert(project: str, spec: str, acceptance: str | None, repo_path: str, base_ref: str,
                preferred_agent: str | None = None) -> str:
    tid = new_id()
    t = now()
    with tx() as c:
        c.execute(
            """INSERT INTO tasks (id, project, spec, acceptance, repo_path, base_ref, state, created_at, updated_at, preferred_agent)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (tid, project, spec, acceptance, repo_path, base_ref, t, t, preferred_agent),
        )
    return tid


def task_get(tid: str) -> sqlite3.Row | None:
    return one("SELECT * FROM tasks WHERE id = ?", tid)


def task_list(project: str | None = None, state: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
    clauses, params = [], []
    if project:
        clauses.append("project = ?")
        params.append(project)
    if state:
        clauses.append("state = ?")
        params.append(state)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return all_(f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ?", *params, limit)


def task_set_state(tid: str, state: str, **fields: Any) -> None:
    sets = ["state = ?", "updated_at = ?"]
    params: list[Any] = [state, now()]
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        params.append(v)
    params.append(tid)
    with tx() as c:
        c.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)


def task_classified_queue(limit: int = 25) -> list[sqlite3.Row]:
    return all_("SELECT * FROM tasks WHERE state = 'classified' ORDER BY created_at ASC LIMIT ?", limit)


def task_next_classified() -> sqlite3.Row | None:
    return one("SELECT * FROM tasks WHERE state = 'classified' ORDER BY created_at ASC LIMIT 1")


def task_assign(tid: str, agent: str, lease_seconds: int) -> None:
    t = now()
    with tx() as c:
        c.execute(
            """UPDATE tasks SET state = 'assigned', claimed_by = ?, lease_expires = ?,
               attempts = attempts + 1, updated_at = ? WHERE id = ? AND state = 'classified'""",
            (agent, t + lease_seconds, t, tid),
        )


def task_claim_assigned(agent: str, lease_seconds: int) -> sqlite3.Row | None:
    t = now()
    with tx() as c:
        row = c.execute(
            """SELECT * FROM tasks WHERE state = 'assigned' AND claimed_by = ?
               ORDER BY created_at ASC LIMIT 1""",
            (agent,),
        ).fetchone()
        if not row:
            return None
        c.execute(
            "UPDATE tasks SET state = 'claimed', lease_expires = ?, updated_at = ? WHERE id = ?",
            (t + lease_seconds, t, row["id"]),
        )
    return task_get(row["id"])


def task_expire_leases() -> int:
    t = now()
    with tx() as c:
        cur = c.execute(
            """UPDATE tasks SET state = 'classified', claimed_by = NULL, lease_expires = NULL,
               attempts = MAX(attempts - 1, 0), updated_at = ?
               WHERE state IN ('assigned', 'claimed', 'running') AND lease_expires IS NOT NULL AND lease_expires < ?
               AND kind != 'session'""",
            (t, t),
        )
        n = cur.rowcount
        cur2 = c.execute(
            """UPDATE tasks SET state = 'failed', claimed_by = NULL, lease_expires = NULL, updated_at = ?
               WHERE state IN ('assigned', 'claimed', 'running') AND lease_expires IS NOT NULL AND lease_expires < ?
               AND kind = 'session'""",
            (t, t),
        )
        return n + cur2.rowcount


def task_expire_unclaimed(claim_seconds: int) -> list[str]:
    """Assigned tasks the worker never picked up (it died, or was stopped) go back to the queue without
    counting an attempt. Returns the ids that were requeued."""
    t = now()
    with tx() as c:
        rows = c.execute(
            """SELECT id, claimed_by FROM tasks WHERE state = 'assigned' AND kind != 'session'
               AND updated_at < ?""", (t - claim_seconds,)).fetchall()
        ids = [r["id"] for r in rows]
        for tid in ids:
            c.execute(
                """UPDATE tasks SET state = 'classified', claimed_by = NULL, lease_expires = NULL,
                   attempts = MAX(attempts - 1, 0), updated_at = ? WHERE id = ? AND state = 'assigned'""",
                (t, tid))
    return ids


def agent_retire(agent_id: str) -> None:
    """A worker going away on purpose: the agent stops counting as alive at once instead of after the heartbeat window."""
    run("UPDATE agents SET last_seen = 0 WHERE agent_id = ?", agent_id)


def agent_register(agent_id: str, host: str | None, capabilities: list[str], capacity: int = 1) -> None:
    t = now()
    import json as _json
    with tx() as c:
        c.execute(
            """INSERT INTO agents (agent_id, host, capabilities, last_seen, registered_at, capacity)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(agent_id) DO UPDATE SET host=excluded.host, capabilities=excluded.capabilities,
                 last_seen=excluded.last_seen, capacity=excluded.capacity""",
            (agent_id, host, _json.dumps(capabilities), t, t, max(1, int(capacity))),
        )


def agent_touch(agent_id: str) -> None:
    run("UPDATE agents SET last_seen = ? WHERE agent_id = ?", now(), agent_id)


def agents_alive(within_seconds: int = 120) -> list[sqlite3.Row]:
    return all_("SELECT * FROM agents WHERE last_seen >= ? ORDER BY agent_id", now() - within_seconds)


def agents_all() -> list[sqlite3.Row]:
    return all_("SELECT * FROM agents ORDER BY agent_id")


def classification_upsert(tid: str, c: dict[str, Any]) -> None:
    with tx() as db:
        db.execute(
            """INSERT INTO classifications
               (task_id, task_type, type_conf, difficulty, difficulty_conf, reasoning,
                needs_tools, needs_vision, is_multistep, source, raw, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(task_id) DO UPDATE SET
                 task_type=excluded.task_type, type_conf=excluded.type_conf,
                 difficulty=excluded.difficulty, difficulty_conf=excluded.difficulty_conf,
                 reasoning=excluded.reasoning, needs_tools=excluded.needs_tools,
                 needs_vision=excluded.needs_vision, is_multistep=excluded.is_multistep,
                 source=excluded.source, raw=excluded.raw, created_at=excluded.created_at""",
            (
                tid, c["task_type"], c["type_conf"], c["difficulty"], c["difficulty_conf"],
                c.get("reasoning"), c.get("needs_tools"), c.get("needs_vision"),
                c.get("is_multistep"), c["source"], c.get("raw"), now(),
            ),
        )


def classification_get(tid: str) -> sqlite3.Row | None:
    return one("SELECT * FROM classifications WHERE task_id = ?", tid)


def attempt_start(tid: str, agent: str, model: str | None) -> str:
    aid = new_id()
    with tx() as c:
        c.execute(
            "INSERT INTO attempts (id, task_id, agent, model, started_at) VALUES (?, ?, ?, ?, ?)",
            (aid, tid, agent, model, now()),
        )
    return aid


def attempt_latest(tid: str) -> sqlite3.Row | None:
    return one("SELECT * FROM attempts WHERE task_id = ? ORDER BY started_at DESC LIMIT 1", tid)


def attempt_finish(aid: str, outcome: str, **fields: Any) -> None:
    t = now()
    row = one("SELECT started_at FROM attempts WHERE id = ?", aid)
    wall = (t - row["started_at"]) if row else None
    sets = ["outcome = ?", "ended_at = ?", "wall_seconds = ?"]
    params: list[Any] = [outcome, t, wall]
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        params.append(v)
    params.append(aid)
    with tx() as c:
        c.execute(f"UPDATE attempts SET {', '.join(sets)} WHERE id = ?", params)
    try:
        from . import perf
        perf.record_attempt(aid)
        perf.invalidate()
    except Exception as e:  # metrics are best-effort; never block the outcome
        import logging
        logging.getLogger("hiveswarm.db").debug("step metrics for %s: %s", aid, e)


def attempts_for(tid: str) -> list[sqlite3.Row]:
    return all_("SELECT * FROM attempts WHERE task_id = ? ORDER BY started_at", tid)


def stats_update(agent: str, task_type: str, diff_band: int, passed: bool, weight: float, usd: float | None, quota: float | None, seconds: float | None) -> None:
    with tx() as c:
        row = c.execute(
            "SELECT * FROM agent_stats WHERE agent=? AND task_type=? AND diff_band=?",
            (agent, task_type, diff_band),
        ).fetchone()
        if row is None:
            alpha, beta, n = 1.0, 1.0, 0
            mu, mq, ms = usd, quota, seconds
        else:
            alpha, beta, n = row["alpha"], row["beta"], row["n"]
            mu, mq, ms = row["mean_usd"], row["mean_quota"], row["mean_seconds"]
        if passed:
            alpha += weight
        else:
            beta += weight
        n2 = n + 1

        def upd(old: float | None, new: float | None) -> float | None:
            if new is None:
                return old
            if old is None:
                return new
            return old + (new - old) / n2

        c.execute(
            """INSERT INTO agent_stats (agent, task_type, diff_band, alpha, beta, n, mean_usd, mean_quota, mean_seconds, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(agent, task_type, diff_band) DO UPDATE SET
                 alpha=excluded.alpha, beta=excluded.beta, n=excluded.n,
                 mean_usd=excluded.mean_usd, mean_quota=excluded.mean_quota,
                 mean_seconds=excluded.mean_seconds, updated_at=excluded.updated_at""",
            (agent, task_type, diff_band, alpha, beta, n2, upd(mu, usd), upd(mq, quota), upd(ms, seconds), now()),
        )


def stats_all() -> list[sqlite3.Row]:
    return all_("SELECT * FROM agent_stats ORDER BY agent, task_type, diff_band")


def log_append(tid: str, source: str, chunk: str) -> int:
    with tx() as c:
        row = c.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM task_logs WHERE task_id = ?", (tid,)).fetchone()
        seq = row["m"] + 1
        c.execute("INSERT INTO task_logs (task_id, seq, ts, source, chunk) VALUES (?, ?, ?, ?, ?)",
                  (tid, seq, now(), source, chunk))
    return seq


def log_append_many(tid: str, entries: list[tuple[str, str] | tuple[str, str, float | None]]) -> int:
    """Entries may carry the worker's own timestamp (third item); it is used when it is sane, so step timings
    reflect when the agent did something rather than when the batch arrived."""
    if not entries:
        return 0
    t = now()
    with tx() as c:
        row = c.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM task_logs WHERE task_id = ?", (tid,)).fetchone()
        seq = row["m"]
        for item in entries:
            source, chunk = item[0], item[1]
            ts = item[2] if len(item) > 2 else None
            stamp = int(ts) if isinstance(ts, (int, float)) and abs(ts - t) < 3600 else t
            seq += 1
            c.execute("INSERT INTO task_logs (task_id, seq, ts, source, chunk) VALUES (?, ?, ?, ?, ?)",
                      (tid, seq, stamp, source, chunk))
    return seq


def feed_since(since: int, limit: int = 1000) -> list[sqlite3.Row]:
    return all_("SELECT rowid AS id, task_id, seq, ts, source, chunk FROM task_logs WHERE rowid > ? "
                "ORDER BY rowid LIMIT ?", since, limit)


def feed_tail(n: int) -> list[sqlite3.Row]:
    rows = all_("SELECT rowid AS id, task_id, seq, ts, source, chunk FROM task_logs ORDER BY rowid DESC LIMIT ?", n)
    return list(reversed(rows))


def busy_counts() -> dict[str, int]:
    rows = all_("""SELECT claimed_by, COUNT(*) AS n FROM tasks
                   WHERE state IN ('assigned', 'claimed', 'running', 'verifying') AND claimed_by IS NOT NULL
                   GROUP BY claimed_by""")
    return {r["claimed_by"]: r["n"] for r in rows}


def agent_capacities() -> dict[str, int]:
    return {r["agent_id"]: int(r["capacity"] or 1) for r in all_("SELECT agent_id, capacity FROM agents")}


def busy_agents() -> set[str]:
    caps = agent_capacities()
    return {a for a, n in busy_counts().items() if n >= caps.get(a, 1)}


def requeue_orphans(agents: list[str]) -> list[str]:
    if not agents:
        return []
    marks = ",".join("?" * len(agents))
    rows = all_(f"""SELECT id FROM tasks WHERE state IN ('assigned', 'claimed', 'running', 'verifying')
                    AND claimed_by IN ({marks})""", *agents)
    out = []
    for r in rows:
        att = attempt_latest(r["id"])
        if att and not att["outcome"]:
            attempt_finish(att["id"], "abandoned", verifier_log="daemon restarted mid-run")
        with tx() as c:
            c.execute("""UPDATE tasks SET state = 'classified', claimed_by = NULL, lease_expires = NULL,
                         worktree = NULL, attempts = MAX(attempts - 1, 0), updated_at = ? WHERE id = ?""",
                      (now(), r["id"]))
        out.append(r["id"])
    return out


def log_since(tid: str, since: int = 0, limit: int = 500) -> list[sqlite3.Row]:
    return all_("SELECT * FROM task_logs WHERE task_id = ? AND seq > ? ORDER BY seq LIMIT ?", tid, since, limit)


def log_clear(tid: str) -> None:
    run("DELETE FROM task_logs WHERE task_id = ?", tid)


def task_delete(tid: str) -> None:
    with tx() as c:
        c.execute("DELETE FROM task_logs WHERE task_id = ?", (tid,))
        c.execute("DELETE FROM attempts WHERE task_id = ?", (tid,))
        c.execute("DELETE FROM classifications WHERE task_id = ?", (tid,))
        c.execute("DELETE FROM session_commands WHERE task_id = ?", (tid,))
        c.execute("DELETE FROM directives WHERE task_id = ?", (tid,))
        c.execute("DELETE FROM tasks WHERE id = ?", (tid,))


def task_update_spec(tid: str, spec: str, acceptance: str | None) -> None:
    with tx() as c:
        c.execute("UPDATE tasks SET spec = ?, acceptance = ?, updated_at = ? WHERE id = ?", (spec, acceptance, now(), tid))


def summary() -> dict[str, Any]:
    counts = {r["state"]: r["n"] for r in all_("SELECT state, COUNT(*) AS n FROM tasks GROUP BY state")}
    recent = all_("""SELECT outcome, COUNT(*) AS n FROM attempts
                     WHERE ended_at IS NOT NULL AND ended_at > ? GROUP BY outcome""", now() - 86400)
    by_agent = all_("""SELECT agent, outcome, COUNT(*) AS n FROM attempts
                       WHERE ended_at IS NOT NULL AND ended_at > ? GROUP BY agent, outcome""", now() - 86400)
    agents: dict[str, dict[str, int]] = {}
    for r in by_agent:
        agents.setdefault(r["agent"], {})[r["outcome"]] = r["n"]
    return {"tasks": counts, "attempts_24h": {r["outcome"]: r["n"] for r in recent},
            "by_agent_24h": agents, "cache": cache_stats()}


def cache_get(key: str) -> sqlite3.Row | None:
    row = one("SELECT * FROM decision_cache WHERE key = ?", key)
    if row:
        run("UPDATE decision_cache SET hits = hits + 1 WHERE key = ?", key)
    return row


def cache_put(key: str, model: str, answers: str, source: str) -> None:
    with tx() as c:
        c.execute(
            """INSERT OR REPLACE INTO decision_cache (key, model, answers, source, created_at, hits)
               VALUES (?, ?, ?, ?, ?, 0)""",
            (key, model, answers, source, now()),
        )


def cache_stats() -> dict[str, Any]:
    r = one("SELECT COUNT(*) AS n, COALESCE(SUM(hits),0) AS hits FROM decision_cache")
    return {"entries": r["n"], "hits": r["hits"]}


def flags_get(tid: str) -> list[dict[str, Any]]:
    import json as _json
    row = one("SELECT flags FROM tasks WHERE id = ?", tid)
    if not row or not row["flags"]:
        return []
    try:
        return list(_json.loads(row["flags"]))
    except Exception:
        return []


def flag_add(tid: str, kind: str, summary: str, detail: str | None = None, ref: str | None = None) -> None:
    import json as _json
    flags = [f for f in flags_get(tid) if not (f.get("kind") == kind and f.get("ref") == ref)]
    flags.append({"kind": kind, "summary": summary[:300], "detail": (detail or "")[:2000], "ref": ref, "since": now()})
    run("UPDATE tasks SET flags = ?, updated_at = ? WHERE id = ?", _json.dumps(flags[-5:]), now(), tid)


def flags_clear(tid: str, kind: str | None = None, ref: str | None = None) -> None:
    import json as _json
    if kind is None and ref is None:
        run("UPDATE tasks SET flags = NULL, updated_at = ? WHERE id = ?", now(), tid)
        return
    keep = [f for f in flags_get(tid) if not ((kind is None or f.get("kind") == kind) and (ref is None or f.get("ref") == ref))]
    run("UPDATE tasks SET flags = ?, updated_at = ? WHERE id = ?", _json.dumps(keep) if keep else None, now(), tid)
