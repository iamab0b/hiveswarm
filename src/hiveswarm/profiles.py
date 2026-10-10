"""Profiles: a named set of models for the three roles of a project's swarm, saved in the hub's config.toml.

A profile says which model leads (the goal session), which one chats (the advisor), and which coders take the
tasks, each with a count of lanes:

    [profiles.deep]
    note = "Opus leads, two Opus medium lanes, Codex for tests"

    [profiles.deep.lead]
    adapter = "claude_code"
    model = "opus"
    effort = "max"

    [profiles.deep.chat]
    model = "sonnet"

    [profiles.deep.coders.claude-opus-medium]
    adapter = "claude_code"
    model = "opus"
    effort = "medium"
    count = 2

A project picks a profile (`projects.<name>.profile`) and from its first task on that choice is fixed; only the
lane counts stay editable, per project (`projects.<name>.lanes = { "deep-claude-opus-medium" = 3 }`). The lead
and every coder become `[agents.*]` tables in the worker.toml of the machine running the app, named after the
profile (`deep-lead`, `deep-claude-opus-medium`), which the app writes and keeps in step (`ui_server.materialize`).
A project without a profile works as before: any agent, or the roster in `projects.<name>.agents`.
"""
from __future__ import annotations

import re
from typing import Any

from . import db
from .config import Config

NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,24}")
MAX_COUNT = 32
HUB_ADAPTERS = ("prime_agent", "local_direct")  # lanes the hub runs itself: one each, nothing to write to a worker.toml
SHORT = {"claude_code": "claude", "antigravity": "agy", "prime_agent": "prime", "local_direct": "direct"}


class ProfileError(ValueError):
    pass


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def coder_key(adapter: str, model: str | None, effort: str | None) -> str:
    """The table key of a coder inside its profile, from what it runs: `claude-opus-medium`, `codex-gpt-5-5-high`."""
    parts = [SHORT.get(adapter, adapter).replace("_", "-")]
    if model:
        parts.append(_slug(model))
    if effort:
        parts.append(_slug(effort))
    return "-".join(p for p in parts if p)


def lead_id(profile: str) -> str:
    return f"{profile}-lead"


def coder_id(profile: str, key: str) -> str:
    return f"{profile}-{key}"


def _role(raw: Any, what: str, adapters: dict[str, Any], efforts: dict[str, list[str]], effort_flags: dict[str, str],
          fixed_adapter: str | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ProfileError(f"{what}: expected a table with adapter, model and effort")
    adapter = str(raw.get("adapter") or fixed_adapter or "claude_code")
    if fixed_adapter and adapter != fixed_adapter:
        raise ProfileError(f"{what}: only {fixed_adapter} can take this role")
    if adapter not in adapters and adapter not in HUB_ADAPTERS:
        raise ProfileError(f"{what}: unknown adapter {adapter}")
    model = (str(raw.get("model") or "").strip() or None)
    effort = (str(raw.get("effort") or "").strip() or None)
    if effort:
        if adapter not in effort_flags:
            raise ProfileError(f"{what}: {adapter} has no effort setting")
        if effort not in (efforts.get(adapter) or []):
            raise ProfileError(f"{what}: effort for {adapter} is one of {', '.join(efforts.get(adapter) or [])}")
    return {"adapter": adapter, "model": model, "effort": effort}


def normalize(name: str, body: dict[str, Any]) -> dict[str, Any]:
    """Validate a profile as the API or config.toml gives it and return the canonical form that is stored:
    coders keyed by what they run (two entries that run the same thing merge, counts added)."""
    from .workers import adapters
    adapters.load_plugins()
    if not NAME_RE.fullmatch(name or ""):
        raise ProfileError("name: letters, digits, - and _ only, up to 24 characters")
    if not isinstance(body, dict):
        raise ProfileError("expected a table")
    lead = _role(body.get("lead") or {}, "lead", adapters.ADAPTERS, adapters.EFFORTS, adapters.EFFORT_FLAGS)
    if lead["adapter"] in HUB_ADAPTERS:
        raise ProfileError("lead: a hub lane cannot run the goal session; pick a CLI agent")
    chat = _role(body.get("chat") or {}, "chat", adapters.ADAPTERS, adapters.EFFORTS, adapters.EFFORT_FLAGS, fixed_adapter="claude_code")
    raw_coders = body.get("coders") or {}
    items: list[Any] = list(raw_coders.values()) if isinstance(raw_coders, dict) else list(raw_coders) if isinstance(raw_coders, list) else []
    coders: dict[str, dict[str, Any]] = {}
    for i, c in enumerate(items):
        role = _role(c, f"coders[{i}]", adapters.ADAPTERS, adapters.EFFORTS, adapters.EFFORT_FLAGS)
        try:
            count = int((c or {}).get("count", 1))
        except (TypeError, ValueError) as e:
            raise ProfileError(f"coders[{i}]: count must be a number") from e
        if role["adapter"] in HUB_ADAPTERS:
            count = 1
        if not 1 <= count <= MAX_COUNT:
            raise ProfileError(f"coders[{i}]: count is between 1 and {MAX_COUNT}")
        key = coder_key(role["adapter"], role["model"], role["effort"])
        if key in coders:
            coders[key]["count"] = min(MAX_COUNT, coders[key]["count"] + count) if role["adapter"] not in HUB_ADAPTERS else 1
        else:
            coders[key] = {**role, "count": count}
    if not coders:
        raise ProfileError("a profile needs at least one coder")
    note = str(body.get("note") or "").strip()[:200]
    out: dict[str, Any] = {"lead": lead, "chat": chat, "coders": coders}
    if note:
        out["note"] = note
    return out


def roles_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Whether two canonical profiles run the same thing (the note aside)."""
    return a.get("lead") == b.get("lead") and a.get("chat") == b.get("chat") and a.get("coders") == b.get("coders")


# ── reading what is on file ─────────────────────────────────────────────

def listing(cfg: Config) -> dict[str, dict[str, Any]]:
    """Every `[profiles.*]` table in canonical form; a table that does not validate is kept with an `error`."""
    out: dict[str, dict[str, Any]] = {}
    for name, raw in (cfg.raw.get("profiles") or {}).items():
        try:
            out[name] = normalize(name, raw if isinstance(raw, dict) else {})
        except ProfileError as e:
            out[name] = {"lead": {}, "chat": {}, "coders": {}, "error": str(e)}
    return out


def get(cfg: Config, name: str) -> dict[str, Any] | None:
    return listing(cfg).get(name)


def users(cfg: Config, name: str) -> list[dict[str, Any]]:
    """The projects that reference a profile, with whether each has started."""
    out = []
    for pname, p in (cfg.raw.get("projects") or {}).items():
        if (p or {}).get("profile") == name:
            out.append({"project": pname, "started": started(pname)})
    return out


def started(project: str) -> bool:
    """A project has started once it has a task (a goal session counts); from then on its profile is fixed."""
    return db.one("SELECT 1 FROM tasks WHERE project = ? LIMIT 1", project) is not None


def task_count(project: str) -> int:
    r = db.one("SELECT COUNT(*) AS n FROM tasks WHERE project = ?", project)
    return int(r["n"]) if r else 0


def for_project(cfg: Config, project: str) -> dict[str, Any] | None:
    """The project's profile resolved to agent ids and effective lane counts, or None when it has no profile."""
    p = cfg.project(project)
    name = p.get("profile")
    if not name:
        return None
    prof = get(cfg, name)
    if prof is None:
        return {"name": name, "error": f"no profile named {name} in config.toml", "lead": None, "chat": None, "coders": []}
    if prof.get("error"):
        return {"name": name, "error": prof["error"], "lead": None, "chat": None, "coders": []}
    overrides = p.get("lanes") or {}
    coders = []
    for key, c in prof["coders"].items():
        hub = c["adapter"] in HUB_ADAPTERS
        aid = c["adapter"] if hub else coder_id(name, key)
        count = c["count"]
        if not hub and aid in overrides:
            try:
                count = max(0, min(MAX_COUNT, int(overrides[aid])))
            except (TypeError, ValueError):
                pass
        coders.append({"key": key, "agent_id": aid, "adapter": c["adapter"], "model": c["model"], "effort": c["effort"],
                       "count": count, "default_count": c["count"], "hub": hub})
    return {"name": name, "note": prof.get("note") or "",
            "lead": {"agent_id": lead_id(name), **prof["lead"]},
            "chat": dict(prof["chat"]),
            "coders": coders}


def roster_for(cfg: Config, project: str) -> list[str]:
    """Which agents may take a project's work: its profile's coders, else `projects.<name>.agents`, else anyone."""
    r = for_project(cfg, project)
    if r and not r.get("error"):
        return [c["agent_id"] for c in r["coders"] if c["count"] > 0]
    return [str(a) for a in (cfg.project(project).get("agents") or [])]


def lead_agent(cfg: Config, project: str) -> str | None:
    """The agent id a profiled project's goal session runs on, or None when the project has no profile."""
    r = for_project(cfg, project)
    return r["lead"]["agent_id"] if r and r.get("lead") else None


def agent_ids(cfg: Config, project: str) -> set[str]:
    r = for_project(cfg, project)
    if not r or r.get("error"):
        return set()
    return {r["lead"]["agent_id"], *(c["agent_id"] for c in r["coders"])}


def wanted(cfg: Config) -> dict[str, dict[str, Any]]:
    """The `[agents.*]` tables every profiled project needs, as the machine running the app should have them:
    the lead with one lane per project on the profile, each coder with the largest count any of those projects
    asks for. Hub lanes are not tables and are left out."""
    tables: dict[str, dict[str, Any]] = {}
    for pname in (cfg.raw.get("projects") or {}):
        r = for_project(cfg, pname)
        if not r or r.get("error"):
            continue
        lead = r["lead"]
        t = tables.get(lead["agent_id"])
        if t is None:
            tables[lead["agent_id"]] = {"adapter": lead["adapter"], "model": lead["model"], "effort": lead["effort"],
                                        "concurrency": 1, "profile": r["name"], "role": "lead"}
        else:
            t["concurrency"] = min(MAX_COUNT, t["concurrency"] + 1)
        for c in r["coders"]:
            if c["hub"]:
                continue
            t = tables.get(c["agent_id"])
            if t is None:
                tables[c["agent_id"]] = {"adapter": c["adapter"], "model": c["model"], "effort": c["effort"],
                                         "concurrency": c["count"], "profile": r["name"], "role": "coder"}
            else:
                t["concurrency"] = max(t["concurrency"], c["count"])
    return tables


def describe(r: dict[str, Any]) -> str:
    """One line for logs and the CLI: `deep: lead claude opus max · chat sonnet · claude opus medium ×2, codex ×1`."""
    def role(x: dict[str, Any] | None) -> str:
        if not x:
            return "?"
        return " ".join(s for s in (x.get("adapter"), x.get("model"), x.get("effort")) if s)
    if r.get("error"):
        return f"{r.get('name')}: {r['error']}"
    coders = ", ".join(f"{role(c)} ×{c['count']}" for c in r.get("coders") or [])
    return f"{r['name']}: lead {role(r.get('lead'))} · chat {role(r.get('chat'))} · {coders}"
