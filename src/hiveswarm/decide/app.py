from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .. import db
from ..config import load
from . import backends
from .cache import cache_key

log = logging.getLogger("hiveswarm.decide")
app = FastAPI(title="hiveswarm-decide")


class DecideRequest(BaseModel):
    state: Any
    questions: dict[str, Any]
    model: str | None = None
    backend: str | None = None
    bypass_cache: bool = False


class RecallRequest(BaseModel):
    state: Any
    questions: dict[str, Any]
    model: str | None = None


@app.on_event("startup")
def _startup() -> None:
    db.migrate()


def _model_name(cfg: Any) -> str:
    """The cache key's model component: whatever model the active backend is configured with."""
    backend = cfg.get("decide.backend", "mock")
    return str(cfg.get("decide.model") or cfg.get(f"decide.{backend}.model") or backend)


@app.get("/health")
def health() -> dict[str, Any]:
    cfg = load()
    return {"ok": True, "backend": cfg.get("decide.backend", "mock"), "cache": db.cache_stats()}


@app.post("/recall")
def recall(req: RecallRequest) -> dict[str, Any]:
    cfg = load()
    model = req.model or _model_name(cfg)
    key = cache_key(model, req.questions, req.state)
    row = db.cache_get(key)
    if not row:
        raise HTTPException(status_code=404, detail="miss")
    return {"hit": True, "answers": json.loads(row["answers"]), "source": row["source"]}


@app.post("/decide")
def decide(req: DecideRequest) -> dict[str, Any]:
    cfg = load()
    model = req.model or _model_name(cfg)
    key = cache_key(model, req.questions, req.state)

    if not req.bypass_cache:
        row = db.cache_get(key)
        if row:
            return {"answers": json.loads(row["answers"]), "cached": True, "source": row["source"]}

    primary = cfg.get("decide.backend", "mock")
    fallback = cfg.get("decide.fallback", "mock" if primary != "mock" else None)
    order = [req.backend] if req.backend else [primary, fallback]
    seen: set[str] = set()
    last_err: str | None = None
    for name in order:
        if not name or name in seen or name not in backends.BACKENDS:
            continue
        seen.add(name)
        try:
            answers = backends.BACKENDS[name](model, req.state, req.questions)
            db.cache_put(key, model, json.dumps(answers), name)
            return {"answers": answers, "cached": False, "source": name}
        except Exception as e:
            last_err = f"{name}: {e}"
            log.warning("backend %s failed: %s", name, e)

    raise HTTPException(status_code=502, detail=last_err or "no backend available")
