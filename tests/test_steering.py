"""Steering a project while its lead works: the plan, pauses and briefs (the chat agent and the lead share them;
the chat agent itself is tested in test_chat.py)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _mcp_env(stack, role: str | None) -> dict[str, str]:
    env = {**stack.env, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{stack.env['PATH']}"}
    if role:
        env["HIVESWARM_ROLE"] = role
    return env


async def _with_mcp(env: dict[str, str], fn):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=sys.executable, args=["-m", "hiveswarm.mcp_server"], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await fn(s)


def _text(res) -> str:
    return "\n".join(c.text for c in res.content if getattr(c, "text", None))


def test_pause_holds_new_work_and_merges_until_resume(stack):
    assert stack.put("/projects/demo/plan", {"text": "## Plan\n1. ship add()\n2. ship subtract()", "by": "test"})["plan"]["text"].startswith("## Plan")
    assert stack.get("/projects/demo/plan")["plan"]["updated_by"] == "test"
    done = stack.post("/tasks", {"project": "demo", "spec": "Add a docstring to add()", "acceptance": "python3 -c 'import add'", "origin": "test"})["id"]
    stack.wait_for(lambda: stack.state(done) == "done", 120, "a task to finish before pausing")
    stack.post("/projects/demo/pause", {"by": "test", "reason": "changing course"})
    assert stack.get("/projects")["demo"]["paused"] is True
    held = stack.post("/tasks", {"project": "demo", "spec": "Add a docstring to add()", "acceptance": "python3 -c 'import add'", "origin": "test"})["id"]
    time.sleep(8)
    assert stack.state(held) in ("pending", "classified"), "nothing of a paused project is routed"
    m = stack.post(f"/tasks/{done}/merge")
    assert not m["ok"] and m.get("paused")
    assert stack.post("/projects/demo/resume")["was_paused"] is True
    stack.wait_for(lambda: stack.state(held) == "done", 120, "the held task to run after resume")
    assert stack.post(f"/tasks/{done}/merge")["ok"]


def test_brief_reaches_the_lead_through_hm_wait(stack):
    tid = stack.post("/tasks", {"project": "demo", "spec": "Add a docstring to add()", "acceptance": "sleep 6; python3 -c 'import add'", "origin": "test"})["id"]
    b = stack.post("/projects/demo/briefs", {"text": "What happened: priorities changed.\nHow to proceed: finish add() only.", "by": "chat"})
    assert b["ok"] and b["sent_to"] is None, "no lead session is running, so it waits for hm_wait"
    assert stack.get("/projects/demo/briefs", undelivered="true")["briefs"][0]["id"] == b["brief"]["id"]

    async def wait(s):
        return json.loads(_text(await s.call_tool("hm_wait", {"task_ids": [tid], "timeout_seconds": 30})))
    out = asyncio.run(_with_mcp(_mcp_env(stack, None), wait))
    assert out["briefs"] and "priorities changed" in out["briefs"][0]["text"] and "hm_resume" in out["note"]
    assert not stack.get("/projects/demo/briefs", undelivered="true")["briefs"], "delivered once"
    stack.wait_for(lambda: stack.state(tid) == "done", 120, "the task to finish")
