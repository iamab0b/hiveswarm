"""Headless tasks: classify → route → run in a worktree → verify → branch, with step metrics recorded."""
from __future__ import annotations

import subprocess


def test_task_passes_and_records_step_metrics(stack):
    r = stack.post("/tasks", {"project": "demo", "spec": "Add a docstring to add()",
                              "acceptance": "python3 -c 'from add import add; assert add(1, 2) == 3'", "origin": "test"})
    tid = r["id"]
    stack.wait_for(lambda: stack.state(tid) == "done", 90, "task to finish")
    d = stack.task(tid)
    att = d["attempts"][-1]
    assert att["outcome"] == "pass"
    assert att["steps"] and att["steps"] >= 2, att
    assert att["step_avg_s"] is not None
    log = stack.log_text(tid)
    assert "routed to" in log and "verifier result: pass" in log
    branches = subprocess.run(["git", "-C", str(stack.repo), "branch", "--list", f"hiveswarm/{tid}"], capture_output=True, text=True).stdout
    assert f"hiveswarm/{tid}" in branches


def test_failing_acceptance_retries_then_fails(stack):
    r = stack.post("/tasks", {"project": "demo", "spec": "Make the tests fail on purpose", "acceptance": "false", "origin": "test"})
    tid = r["id"]
    stack.wait_for(lambda: stack.state(tid) == "failed", 150, "task to exhaust its attempts")
    d = stack.task(tid)
    assert len(d["attempts"]) == d["task"]["max_attempts"]
    agents = {a["agent"] for a in d["attempts"]}
    assert len(agents) >= 2, "a failed task should be handed to a different agent"
    assert "handoff to" in stack.log_text(tid)


def test_merge_lands_on_hub_branch(stack):
    r = stack.post("/tasks", {"project": "demo", "spec": "Add a docstring to add()",
                              "acceptance": "python3 -c 'import add'", "origin": "test"})
    tid = r["id"]
    stack.wait_for(lambda: stack.state(tid) == "done", 90, "task to finish")
    before = subprocess.run(["git", "-C", str(stack.repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    m = stack.post(f"/tasks/{tid}/merge")
    assert m["ok"], m
    after = subprocess.run(["git", "-C", str(stack.repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert before != after


def test_stats_table_has_agents(stack):
    t = stack.get("/stats/agents")
    assert t["cells"] and t["agents"]
    assert {a["status"] for a in t["agents"]} <= {"healthy", "slow", "failing", "unknown"}
    assert all("pass_rate" in c and "avg_step_s" in c for c in t["cells"])
