"""Lanes the hub runs itself (prime_agent, local_direct) appear on GET /agents like a worker's agents, flagged
`local`, with their status, and without a live lane count."""
from __future__ import annotations

from hiveswarm import tomledit


def _row(stack, agent: str):
    return next((a for a in stack.get("/agents") if a["agent_id"] == agent), None)


def test_prime_agent_is_listed_with_its_status(stack):
    row = _row(stack, "prime_agent")
    assert row and row["local"] is True and row["alive"] is False, row  # enabled in the test config, no container
    assert row["capacity"] == 1 and row["provider"] == "prime_agent" and row["host"]
    assert "[workers.prime_agent]" in row["where"]
    assert _row(stack, "local_direct") is None, "off unless enabled"


def test_hub_lanes_have_no_live_lane_count(stack):
    r = stack.http.post("/agents/prime_agent/capacity", json={"capacity": 2})
    assert r.status_code == 400 and "config.toml" in r.json()["detail"]


def test_hub_lane_follows_config_toml_without_a_restart(stack):
    path = stack.home / "config.toml"
    tomledit.update_section(path, "workers.prime_agent", {"enabled": False})
    try:
        stack.wait_for(lambda: _row(stack, "prime_agent") is None, 15, "the daemon to drop the disabled hub lane")
        tomledit.update_section(path, "workers.local_direct", {"enabled": True})
        stack.wait_for(lambda: _row(stack, "local_direct") is not None, 15, "the daemon to list local_direct")
        assert "[workers.local_direct]" in _row(stack, "local_direct")["where"]
    finally:
        tomledit.update_section(path, "workers.local_direct", {"enabled": None})
        tomledit.update_section(path, "workers.prime_agent", {"enabled": True})
    stack.wait_for(lambda: _row(stack, "prime_agent") is not None and _row(stack, "local_direct") is None, 15, "the config to be restored")
