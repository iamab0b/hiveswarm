"""Agent profiles: several `[agents.*]` tables behind one adapter with their own model and effort, applied by the
worker when worker.toml changes, shown to the daemon and the app, honoured by routing (rosters) and advice."""
from __future__ import annotations

import tempfile
from pathlib import Path

import httpx

from hiveswarm import tomledit


def _agent(stack, name: str):
    return next((a for a in stack.get("/agents") if a["agent_id"] == name), None)


def test_tomledit_keeps_the_rest_of_the_file():
    p = Path(tempfile.mkdtemp()) / "w.toml"
    p.write_text('daemon_url = "http://h:1"  # hub\n\n[agents.claude_code]\nadapter = "claude_code"\nconcurrency = 2\n[agents.claude_code.keys]\napprove = ["1"]\n\n[agents.codex]\nadapter = "codex"\n')
    tomledit.update_section(p, "agents.claude_code", {"model": "opus", "effort": "high", "concurrency": 3})
    tomledit.write_section(p, "agents.opus_max", {"adapter": "claude_code", "model": "opus", "effort": "max", "concurrency": 1})
    tomledit.write_section(p, "agents.codex", None)
    text = p.read_text()
    assert text.startswith('daemon_url = "http://h:1"  # hub\n'), "comments and other keys survive"
    data = tomledit.read(p)
    assert data["agents"]["claude_code"] == {"adapter": "claude_code", "concurrency": 3, "model": "opus", "effort": "high", "keys": {"approve": ["1"]}}
    assert data["agents"]["opus_max"]["effort"] == "max" and "codex" not in data["agents"]
    tomledit.update_section(p, "agents.opus_max", {"effort": None})
    assert "effort" not in tomledit.read(p)["agents"]["opus_max"]


def test_profile_added_to_worker_toml_registers_with_model_and_effort(stack):
    wt = Path(stack.env["HIVESWARM_WORKER_CONFIG"])
    tomledit.write_section(wt, "agents.opus_max", {"adapter": "claude_code", "model": "opus", "effort": "max", "concurrency": 1})
    try:
        stack.wait_for(lambda: (_agent(stack, "opus_max") or {}).get("alive"), 40, "the worker to pick up the new profile")
        a = _agent(stack, "opus_max")
        assert a["provider"] == "claude_code" and a["model"] == "opus" and a["effort"] == "max" and a["capacity"] == 1
        tid = stack.post("/tasks", {"project": "demo", "spec": "Add a docstring to add()", "acceptance": "python3 -c 'import add'",
                                    "origin": "test", "agent": "opus_max"})["id"]
        stack.wait_for(lambda: stack.state(tid) == "done", 120, "the profile's task to finish")
        log = stack.log_text(tid)
        assert "model opus" in log and "effort max" in log, "the CLI got --model and --effort"
        att = [x for x in stack.task(tid)["attempts"] if x["outcome"]][-1]
        assert att["agent"] == "opus_max"
        adv = stack.post("/advise", {"project": "demo", "tasks": [{"spec": "Add subtract() with a test"}]})
        assert "opus_max" in adv["lanes"] and adv["lanes"]["opus_max"]["model"] == "opus"
        assert adv["providers"]["claude_code"]["capacity"] >= 3 and "opus_max" in adv["providers"]["claude_code"]["agents"]
        assert "sharing one sign-in" in adv["summary"]
        tomledit.update_section(wt, "agents.opus_max", {"effort": "high", "concurrency": 2})
        stack.wait_for(lambda: (_agent(stack, "opus_max") or {}).get("effort") == "high" and _agent(stack, "opus_max")["capacity"] == 2, 40, "the change to apply")
    finally:
        tomledit.write_section(wt, "agents.opus_max", None)
        stack.wait_for(lambda: _agent(stack, "opus_max") is None or not _agent(stack, "opus_max")["alive"], 40, "the profile to retire")


def test_project_roster_restricts_routing(stack):
    assert stack.get("/projects/demo/settings")["agents"] == []
    r = stack.post("/projects/demo/settings", {"agents": ["codex"]})
    assert r["agents"] == ["codex"]
    try:
        tid = stack.post("/tasks", {"project": "demo", "spec": "Add a docstring to add()", "acceptance": "python3 -c 'import add'", "origin": "test"})["id"]
        stack.wait_for(lambda: stack.task(tid)["task"]["claimed_by"] == "codex", 60, "the roster to route the task to codex")
        adv = stack.post("/advise", {"project": "demo", "tasks": [{"spec": "Add subtract() with a test"}]})
        assert adv["roster"] == ["codex"] and adv["tasks"][0]["agent"] == "codex" and "roster: codex" in adv["summary"]
        assert "claude_code" not in {a["agent"] for a in adv["tasks"][0]["alternatives"]}
        stack.wait_for(lambda: stack.state(tid) == "done", 120, "the task to finish")
    finally:
        stack.post("/projects/demo/settings", {"agents": []})
        assert stack.get("/projects/demo/settings")["agents"] == []
    stack.post("/projects/demo/settings", {"agents": ["nobody_here"]})
    try:
        tid = stack.post("/tasks", {"project": "demo", "spec": "Add a docstring to add()", "acceptance": "python3 -c 'import add'", "origin": "test"})["id"]
        stack.wait_for(lambda: stack.task(tid)["task"]["claimed_by"] is not None, 60, "an empty roster to fall back to any agent")
        assert "roster: none of nobody_here is alive" in stack.log_text(tid)
        stack.wait_for(lambda: stack.state(tid) == "done", 120, "the task to finish")
    finally:
        stack.post("/projects/demo/settings", {"agents": []})


def test_app_edits_profiles_on_this_machine(stack, app_server):
    http = httpx.Client(base_url=app_server.url, timeout=30)
    before = http.get("/api/local/profiles").json()
    assert before["ok"] and {p["name"] for p in before["profiles"]} >= {"claude_code", "codex"}
    assert next(a for a in before["adapters"] if a["name"] == "claude_code")["effort_supported"]
    r = http.post("/api/local/profiles", json={"name": "fast", "adapter": "codex", "model": "gpt-5-codex", "effort": "low", "concurrency": 1}).json()
    assert r["ok"], r
    try:
        assert next(p for p in http.get("/api/local/profiles").json()["profiles"] if p["name"] == "fast")["effort"] == "low"
        stack.wait_for(lambda: (_agent(stack, "fast") or {}).get("alive"), 40, "the worker to register the profile")
        assert _agent(stack, "fast")["model"] == "gpt-5-codex"
        bad = http.post("/api/local/profiles", json={"name": "fast", "effort": "ultra"}).json()
        assert not bad["ok"] and "effort" in bad["reason"]
        assert not http.post("/api/local/profiles", json={"name": "bad name!"}).json()["ok"]
    finally:
        assert http.delete("/api/local/profiles/fast").json()["ok"]
        stack.wait_for(lambda: _agent(stack, "fast") is None or not _agent(stack, "fast")["alive"], 40, "the profile to retire")
