"""Memory across tasks: what the swarm learned, recalled before it plans again.

Three backends, chosen by `[memory] backend`:
- `none` (default): nothing is kept; hm_recall returns nothing and says so.
- `local`: a full-text index (SQLite FTS5) in the daemon's own database. No model, no network; keyword search
  with BM25 ranking, which is enough for "did we hit this before".
- `hindsight`: a Hindsight server (https://github.com/vectorize-io/hindsight, MIT): retain and recall through its
  REST API, one bank per scope, so recall is semantic and the server can reflect over everything it has.

Two scopes: a project's own memories and `global` (every project). The daemon retains a lesson when an attempt
fails (what was tried, what the verifier said) and a short note when work lands; the lead and the advisor can
remember and recall explicitly (hm_remember / hm_recall); recall for the lead's goal goes into its prompt.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from . import db
from .config import load_current

log = logging.getLogger("hiveswarm.memory")

GLOBAL = "global"
BACKENDS = ("none", "local", "hindsight")


def scopes_for(project: str | None) -> list[str]:
    return [project, GLOBAL] if project else [GLOBAL]


class Memory:
    name = "none"

    def remember(self, scope: str, text: str, tags: list[str] | None = None, by: str | None = None) -> dict[str, Any]:
        return {"ok": False, "reason": "memory is off ([memory] backend = \"local\" or \"hindsight\" in config.toml)"}

    def recall(self, query: str, scopes: list[str], limit: int = 8) -> list[dict[str, Any]]:
        return []

    def status(self) -> dict[str, Any]:
        return {"backend": self.name, "ok": True}


# ── local: SQLite FTS5 ───────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
  id          TEXT PRIMARY KEY,
  scope       TEXT NOT NULL,
  text        TEXT NOT NULL,
  tags        TEXT,
  created_by  TEXT,
  created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope, created_at);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(text, tags, content='memories', content_rowid='rowid');
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, text, tags) VALUES (new.rowid, new.text, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, text, tags) VALUES ('delete', old.rowid, old.text, old.tags);
END;
"""

_WORD = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


def _fts_query(query: str) -> str:
    """A tolerant FTS5 query: every word quoted and OR-ed, so punctuation and stray operators cannot break it."""
    words = [w for w in _WORD.findall(query or "") if len(w) > 1][:24]
    return " OR ".join('"' + w.replace('"', '') + '"' for w in words)


class LocalMemory(Memory):
    name = "local"

    def __init__(self) -> None:
        db.conn().executescript(_SCHEMA)

    def remember(self, scope: str, text: str, tags: list[str] | None = None, by: str | None = None) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "reason": "nothing to remember"}
        mid = db.new_id()
        db.run("INSERT INTO memories (id, scope, text, tags, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
               mid, scope, text[:2000], " ".join(tags or []), by, db.now())
        return {"ok": True, "id": mid, "scope": scope}

    def recall(self, query: str, scopes: list[str], limit: int = 8) -> list[dict[str, Any]]:
        q = _fts_query(query)
        if not q or not scopes:
            return []
        marks = ",".join("?" for _ in scopes)
        rows = db.all_(f"""SELECT m.id, m.scope, m.text, m.tags, m.created_by, m.created_at, bm25(memories_fts) AS rank
                           FROM memories_fts JOIN memories m ON m.rowid = memories_fts.rowid
                           WHERE memories_fts MATCH ? AND m.scope IN ({marks})
                           ORDER BY rank LIMIT ?""", q, *scopes, limit)
        return [{"id": r["id"], "scope": r["scope"], "text": r["text"], "tags": (r["tags"] or "").split(), "by": r["created_by"],
                 "at": r["created_at"], "score": round(-float(r["rank"]), 3)} for r in rows]

    def status(self) -> dict[str, Any]:
        r = db.one("SELECT COUNT(*) AS n FROM memories")
        return {"backend": self.name, "ok": True, "count": r["n"] if r else 0}


# ── hindsight ────────────────────────────────────────────────────────────

class HindsightMemory(Memory):
    name = "hindsight"

    def __init__(self, url: str, token: str | None = None, budget: str = "low", bank_prefix: str = "hiveswarm", timeout: float = 20.0):
        self.url = url.rstrip("/")
        self.token = token
        self.budget = budget
        self.prefix = bank_prefix
        self.timeout = timeout

    def _bank(self, scope: str) -> str:
        return f"{self.prefix}-{re.sub(r'[^A-Za-z0-9_-]', '-', scope)}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def remember(self, scope: str, text: str, tags: list[str] | None = None, by: str | None = None) -> dict[str, Any]:
        import httpx
        text = (text or "").strip()
        if not text:
            return {"ok": False, "reason": "nothing to remember"}
        body = {"items": [{"content": text[:4000], "context": f"hiveswarm {scope}" + (f", by {by}" if by else ""), "tags": list(tags or [])}], "async": False}
        try:
            r = httpx.post(f"{self.url}/v1/default/banks/{self._bank(scope)}/memories", json=body, headers=self._headers(), timeout=self.timeout)
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("hindsight retain failed: %s", e)
            return {"ok": False, "reason": f"hindsight: {e}"}
        return {"ok": True, "scope": scope, "bank": self._bank(scope)}

    def recall(self, query: str, scopes: list[str], limit: int = 8) -> list[dict[str, Any]]:
        import httpx
        out: list[dict[str, Any]] = []
        for scope in scopes:
            try:
                r = httpx.post(f"{self.url}/v1/default/banks/{self._bank(scope)}/memories/recall",
                               json={"query": query, "budget": self.budget, "max_tokens": 1500}, headers=self._headers(), timeout=self.timeout)
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                for i, item in enumerate((r.json() or {}).get("results") or []):
                    out.append({"id": item.get("id"), "scope": scope, "text": item.get("text") or "", "tags": [],
                                "by": None, "at": None, "score": round(1.0 / (1 + i), 3), "type": item.get("type")})
            except httpx.HTTPError as e:
                log.warning("hindsight recall failed for %s: %s", scope, e)
        out.sort(key=lambda x: -x["score"])
        return out[:limit]

    def status(self) -> dict[str, Any]:
        import httpx
        try:
            r = httpx.get(f"{self.url}/health", headers=self._headers(), timeout=5)
            return {"backend": self.name, "ok": r.status_code < 500, "url": self.url}
        except httpx.HTTPError as e:
            return {"backend": self.name, "ok": False, "url": self.url, "error": str(e)}


# ── selection ────────────────────────────────────────────────────────────

_instance: Memory | None = None
_instance_key: tuple[Any, ...] | None = None


def get() -> Memory:
    """The configured backend; re-created when `[memory]` in config.toml changes."""
    global _instance, _instance_key
    cfg = load_current()
    backend = str(cfg.get("memory.backend", "none") or "none").lower()
    key: tuple[Any, ...] = (backend,)
    if backend == "hindsight":
        key = (backend, cfg.get("memory.hindsight.url", "http://127.0.0.1:8888"), cfg.get("memory.hindsight.token"),
               cfg.get("memory.hindsight.budget", "low"), cfg.get("memory.hindsight.bank_prefix", "hiveswarm"))
    if _instance is not None and _instance_key == key:
        return _instance
    if backend == "local":
        _instance = LocalMemory()
    elif backend == "hindsight":
        _instance = HindsightMemory(str(key[1]), key[2], str(key[3]), str(key[4]))
    else:
        _instance = Memory()
    _instance_key = key
    return _instance


def enabled() -> bool:
    return get().name != "none"


def recall(query: str, project: str | None, limit: int = 8) -> list[dict[str, Any]]:
    try:
        return get().recall(query, scopes_for(project), limit)
    except Exception as e:  # recall is advisory; never break a prompt over it
        log.warning("recall failed: %s", e)
        return []


def remember(text: str, project: str | None, global_scope: bool = False, tags: list[str] | None = None, by: str | None = None) -> dict[str, Any]:
    scope = GLOBAL if global_scope or not project else project
    try:
        return get().remember(scope, text, tags, by)
    except Exception as e:
        log.warning("remember failed: %s", e)
        return {"ok": False, "reason": str(e)}


def lesson_from_attempt(task: Any, attempt: Any, cls: dict[str, Any] | None, passed: bool) -> tuple[str, list[str]] | None:
    """The one line worth keeping from a finished attempt, or None when there is nothing to learn."""
    spec = (task["spec"] or "").strip().splitlines()[0][:140] if task["spec"] else ""
    agent = attempt["agent"] if attempt else "?"
    tt = (cls or {}).get("task_type") or "work"
    tags = [task["project"], agent, tt, "done" if passed else "failed"]
    if passed:
        lines = attempt["lines_changed"] if attempt else None
        wall = int(attempt["wall_seconds"] or 0) if attempt else 0
        return (f"Done on {task['project']}: \"{spec}\" by {agent} ({tt}; {lines or '?'} lines changed, {wall}s)"
                + (f"; acceptance: {task['acceptance']}" if task["acceptance"] else ""), tags)
    vlog = (attempt["verifier_log"] or "").strip() if attempt else ""
    tail = [ln.strip() for ln in vlog.splitlines() if ln.strip()][-3:]
    why = " | ".join(tail)[:400] if tail else "no verifier output"
    return (f"Failed on {task['project']}: \"{spec}\" by {agent} ({tt}): {why}", tags)


def prompt_block(items: list[dict[str, Any]], title: str = "What the swarm learned before (hm_recall)") -> str:
    if not items:
        return ""
    lines = [f"## {title}"]
    for it in items[:8]:
        scope = "" if it.get("scope") in (None, GLOBAL) else f" [{it['scope']}]"
        lines.append(f"- {it['text'].strip()[:400]}{scope}")
    return "\n".join(lines)
