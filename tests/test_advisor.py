"""The advisor: a chat beside the lead with read-only tools, the plan, pause/resume and briefs."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

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


def test_advisor_role_gets_read_only_tools_plus_plan_pause_brief(stack):
    async def names(s):
        return {t.name for t in (await s.list_tools()).tools}
    advisor = asyncio.run(_with_mcp(_mcp_env(stack, "advisor"), names))
    lead = asyncio.run(_with_mcp(_mcp_env(stack, None), names))
    assert {"hm_status", "hm_list", "hm_get", "hm_tail", "hm_diff", "hm_deferred", "hm_plan", "hm_plan_get", "hm_pause", "hm_resume", "hm_brief"} <= advisor
    assert not ({"hm_add_tasks", "hm_merge", "hm_retry", "hm_cancel", "hm_new_session", "hm_send", "hm_approve", "hm_directive"} & advisor)
    assert {"hm_add_tasks", "hm_merge", "hm_plan", "hm_pause", "hm_resume", "hm_brief", "hm_briefs"} <= lead


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
    b = stack.post("/projects/demo/briefs", {"text": "What happened: priorities changed.\nHow to proceed: finish add() only.", "by": "advisor"})
    assert b["ok"] and b["sent_to"] is None, "no lead session is running, so it waits for hm_wait"
    assert stack.get("/projects/demo/briefs", undelivered="true")["briefs"][0]["id"] == b["brief"]["id"]

    async def wait(s):
        return json.loads(_text(await s.call_tool("hm_wait", {"task_ids": [tid], "timeout_seconds": 30})))
    out = asyncio.run(_with_mcp(_mcp_env(stack, None), wait))
    assert out["briefs"] and "priorities changed" in out["briefs"][0]["text"] and "hm_resume" in out["note"]
    assert not stack.get("/projects/demo/briefs", undelivered="true")["briefs"], "delivered once"
    stack.wait_for(lambda: stack.state(tid) == "done", 120, "the task to finish")


def _advisor(stack):
    return stack.get("/advisor/demo")


def _texts(stack) -> list[str]:
    return [m["text"] for m in _advisor(stack)["messages"] if m["role"] == "advisor" and m["kind"] == "text"]


def test_advisor_turns_run_on_a_worker_with_memory(stack):
    stack.http.delete("/advisor/demo")
    r = stack.post("/advisor/demo/talk", {"text": "What is the swarm doing?"})
    assert r["ok"]
    assert _advisor(stack)["thinking"] is True
    stack.wait_for(lambda: _texts(stack) and not _advisor(stack)["thinking"], 60, "the advisor's first answer")
    first = _texts(stack)[-1]
    assert first.startswith("1.") and "Next:" in first, "i-have-adhd style: next action first, one next step last"
    assert "Fresh session" in first
    tools = [m["text"] for m in _advisor(stack)["messages"] if m["kind"] == "tool"]
    assert any("hm_status" in t for t in tools) and any("hm_plan_get" in t for t in tools)
    assert _advisor(stack)["has_memory"] is True
    stack.post("/advisor/demo/talk", {"text": "plan: ## Plan\n1. ship add()\n2. tests"})
    stack.wait_for(lambda: len(_texts(stack)) >= 2 and not _advisor(stack)["thinking"], 60, "the second answer")
    assert "Plan saved" in _texts(stack)[-1]
    assert stack.get("/projects/demo/plan")["plan"]["text"].startswith("## Plan") and stack.get("/projects/demo/plan")["plan"]["updated_by"] == "advisor"
    stack.post("/advisor/demo/talk", {"text": "please pause, we need to change course: ship tests first"})
    stack.wait_for(lambda: len(_texts(stack)) >= 3 and not _advisor(stack)["thinking"], 60, "the third answer")
    assert "resumed session" in _texts(stack)[-2], "the second turn resumed the first turn's Claude session"
    assert stack.get("/projects")["demo"]["paused"] is True
    briefs = stack.get("/projects/demo/briefs")["briefs"]
    assert briefs and "change course" in briefs[0]["text"] and briefs[0]["created_by"] == "advisor"
    assert _advisor(stack)["briefs_pending"] >= 1
    stack.post("/projects/demo/resume")
    assert stack.get("/projects")["demo"]["paused"] is False
    stack.post("/briefs/" + briefs[0]["id"] + "/delivered")


@pytest.mark.e2e
def test_lead_page_shows_plan_pause_and_advisor(stack, page):
    stack.http.delete("/advisor/demo")
    stack.put("/projects/demo/plan", {"text": "## Plan\n1. ship add()\n2. ship subtract()", "by": "test"})
    stack.post("/projects/demo/pause", {"by": "advisor", "reason": "changing course"})
    try:
        page.goto(page.base + "/lead/demo", wait_until="load")
        page.locator("[data-plan]").wait_for(timeout=15000)
        assert "ship subtract()" in page.locator("[data-plan]").inner_text()
        banner = page.locator("[data-paused]")
        banner.wait_for(timeout=10000)
        assert "changing course" in banner.inner_text()
        banner.locator("button", has_text="Resume").click()
        stack.wait_for(lambda: stack.get("/projects")["demo"]["paused"] is False, 10, "the resume button")
        page.locator("button", has_text="Advisor").first.click()
        box = page.locator("textarea[data-advisor-input]")
        box.wait_for(timeout=10000)
        box.fill("What is the swarm doing?")
        box.press("Enter")
        page.locator("[data-advisor-chat] .prose-md", has_text="Next:").first.wait_for(timeout=60000)
        assert not page.errors
    finally:
        stack.post("/projects/demo/resume")
