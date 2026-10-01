"""Live agent counts: capacity set from the app changes the worker's lanes without a restart."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _capacity(stack, agent: str) -> int:
    return next(a["capacity"] for a in stack.get("/agents") if a["agent_id"] == agent)


def test_capacity_grows_and_tasks_run_in_parallel(stack):
    assert _capacity(stack, "codex") == 1
    r = stack.post("/agents/codex/capacity", {"capacity": 2})
    assert r["desired_capacity"] == 2
    stack.wait_for(lambda: _capacity(stack, "codex") == 2, 30, "the worker to add a codex lane")
    ids = [stack.post("/tasks", {"project": "demo", "spec": f"Add a docstring to add() #{i}", "agent": "codex",
                                 "acceptance": "sleep 4; python3 -c 'import add'", "origin": "test"})["id"] for i in range(2)]
    active = {"claimed", "running", "verifying"}

    def both_in_flight() -> bool:
        states = [stack.state(t) for t in ids]
        return all(s in active for s in states)
    stack.wait_for(both_in_flight, 40, "both codex tasks to be in flight at once")
    for t in ids:
        stack.wait_for(lambda t=t: stack.state(t) == "done", 90, "task to finish")


def test_capacity_zero_pauses_an_agent_and_auto_restores(stack):
    stack.post("/agents/codex/capacity", {"capacity": 0})
    stack.wait_for(lambda: _capacity(stack, "codex") == 0, 30, "the worker to retire codex lanes")
    tid = stack.post("/tasks", {"project": "demo", "spec": "Add a docstring to add()", "agent": "codex",
                                "acceptance": "python3 -c 'import add'", "origin": "test"})["id"]
    time.sleep(8)
    assert stack.state(tid) in ("pending", "classified"), "nothing claims for an agent with no lanes"
    stack.post("/agents/codex/capacity", {"capacity": None})
    stack.wait_for(lambda: _capacity(stack, "codex") == 1, 30, "the worker to go back to worker.toml's one lane")
    stack.wait_for(lambda: stack.state(tid) == "done", 90, "the queued task to run once a lane is back")
    env = {**stack.env, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{stack.env['PATH']}"}
    out = subprocess.run([sys.executable, "-m", "hiveswarm.cli", "agents"], env=env, capture_output=True, text=True)
    assert out.returncode == 0 and "codex" in out.stdout and "wanted" in out.stdout


def test_capacity_is_validated(stack):
    assert stack.http.post("/agents/codex/capacity", json={"capacity": 99}).status_code == 400
    assert stack.http.post("/agents/nope/capacity", json={"capacity": 1}).status_code == 404


@pytest.mark.e2e
def test_agents_page_stepper_changes_lanes(stack, page):
    """The +/− control on the Agents page sets the agent's lanes and shows when the worker has applied it."""
    def desired(agent: str):
        return next(a["desired_capacity"] for a in stack.get("/agents") if a["agent_id"] == agent)
    page.goto(page.base + "/agents", wait_until="load")
    card = page.locator(".card", has_text="codex").first
    card.locator("button[aria-label='more lanes']").wait_for(timeout=10000)
    card.locator("button[aria-label='more lanes']").click()
    stack.wait_for(lambda: desired("codex") == 2, 10, "the app to post the new lane count")
    stack.wait_for(lambda: _capacity(stack, "codex") == 2, 30, "the worker to add a lane")
    page.wait_for_timeout(2500)
    assert "applying" not in card.inner_text()
    assert "2 / 2" not in card.inner_text() and "/ 2 busy" in card.inner_text()
    card.locator("text=back to worker.toml").click()
    stack.wait_for(lambda: desired("codex") is None, 10, "the app to clear the override")
    stack.wait_for(lambda: _capacity(stack, "codex") == 1, 30, "the worker to retire the extra lane")
