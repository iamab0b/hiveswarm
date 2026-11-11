"""The chat agent: a Claude Code session with no project that becomes a project's chat agent when it starts one."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

import httpx

from conftest import make_repo, needs_tmux

HOST = socket.gethostname()


def _mcp_env(stack, role: str | None, chat: str | None = None) -> dict[str, str]:
    env = {**stack.env, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{stack.env['PATH']}"}
    if role:
        env["HIVESWARM_ROLE"] = role
    if chat:
        env["HIVESWARM_CHAT_SESSION"] = chat
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


def _chat_agent(stack) -> dict | None:
    return next((a for a in stack.get("/agents") if a.get("profile") == "_chat"), None)


def _screen(stack, tid: str) -> str:
    cid = stack.post(f"/sessions/{tid}/send", {"screen": True, "lines": 60})["command_id"]
    for _ in range(40):
        c = stack.get(f"/sessions/commands/{cid}")
        if c.get("done_at"):
            return c.get("result") or ""
        time.sleep(0.25)
    return ""


def _running(stack, tid: str) -> bool:
    return stack.state(tid) == "running" and bool(stack.session(tid).get("tmux")) and stack.session(tid).get("turn") in ("working", "idle")


def test_chat_role_tools(stack):
    async def names(s):
        return {t.name for t in (await s.list_tools()).tools}
    chat = asyncio.run(_with_mcp(_mcp_env(stack, "chat"), names))
    old = asyncio.run(_with_mcp(_mcp_env(stack, "advisor"), names))
    lead = asyncio.run(_with_mcp(_mcp_env(stack, None), names))
    views = {"hm_status", "hm_list", "hm_get", "hm_tail", "hm_diff", "hm_deferred", "hm_plan", "hm_plan_get", "hm_pause", "hm_resume", "hm_brief", "hm_profiles"}
    assert views | {"hm_start_project", "hm_start_lead"} <= chat
    assert not ({"hm_add_tasks", "hm_merge", "hm_retry", "hm_cancel", "hm_new_session", "hm_send", "hm_approve", "hm_directive", "hm_new_project"} & chat)
    assert views <= old and not ({"hm_start_project", "hm_start_lead", "hm_add_tasks"} & old), "the 0.3 advisor role still works, without the new tools"
    assert {"hm_add_tasks", "hm_merge", "hm_plan", "hm_briefs", "hm_profiles"} <= lead and not ({"hm_start_project", "hm_start_lead"} & lead)


@needs_tmux
def test_worker_registers_a_chat_agent_that_takes_no_tasks(stack):
    a = stack.wait_for(lambda: _chat_agent(stack), 30, "the worker's chat agent")
    assert a["agent_id"].startswith("chat-") and a["provider"] == "claude_code" and "chat" in a["capabilities"]
    assert a["alive"] and a["capacity"] >= 2, "one lane for the chat that is open and a spare"
    r = stack.http.post(f"/agents/{a['agent_id']}/capacity", json={"capacity": 5})
    assert r.status_code == 400 and "chat agent" in r.text
    tid = stack.post("/tasks", {"project": "demo", "spec": "Add a docstring to add()", "acceptance": "python3 -c 'import add'",
                                "agent": a["agent_id"], "origin": "test"})["id"]
    stack.wait_for(lambda: stack.state(tid) in ("done", "failed"), 120, "a task that prefers the chat agent to run elsewhere")
    assert stack.task(tid)["attempts"][-1]["agent"] != a["agent_id"]


@needs_tmux
def test_home_chat_is_a_terminal_without_a_project_and_fresh_replaces_it(stack):
    stack.wait_for(lambda: _chat_agent(stack), 30, "the worker's chat agent")
    one = stack.post("/chats/home", {"fresh": True, "host": HOST})
    assert one["created"] and one["project"] is None
    stack.wait_for(lambda: _running(stack, one["id"]), 40, "the chat agent's terminal")
    t = stack.task(one["id"])["task"]
    assert t["project"] == "_home" and t["kind"] == "session"
    s = stack.session(one["id"])
    assert s["role"] == "chat" and s["host"] == HOST and s["agent"].startswith("chat-")
    screen = stack.wait_for(lambda: (lambda x: x if "hiveswarm tools" in x else "")(_screen(stack, one["id"])), 30, "the chat agent's prompt")
    assert "chat agent at Home" in screen and "fresh" in screen
    assert "denied=Bash,Edit,Write" in screen.replace("\n", ""), "it cannot edit or run commands"
    assert "_home" not in stack.get("/projects"), "Home is not a project"
    assert stack.post("/chats/home", {"host": HOST})["id"] == one["id"], "asking again returns the chat that is open"
    newer = stack.post("/tasks", {"project": "demo", "spec": "A newer task than the chat", "acceptance": "true", "origin": "test"})["id"]
    assert [t["id"] for t in stack.get("/tasks", limit=1)] == [newer]
    assert one["id"] in [t["id"] for t in stack.get("/tasks", limit=1, live="true")], "the app's list keeps every live task, however old"
    stack.post(f"/tasks/{newer}/cancel")

    stack.post(f"/sessions/{one['id']}/send", {"text": "hello there"})
    stack.wait_for(lambda: "You said: hello there" in _screen(stack, one["id"]), 30, "an answer")
    stack.wait_for(lambda: stack.session(one["id"]).get("turn") == "idle", 20, "the turn to end")
    assert stack.session(one["id"]).get("attention") is None, "a chat agent at its prompt is not an inbox item"
    assert one["id"] not in [i["id"] for i in stack.get("/inbox")["items"]]

    two = stack.post("/chats/home", {"fresh": True, "host": HOST})
    assert two["created"] and two["id"] != one["id"]
    stack.wait_for(lambda: stack.state(one["id"]) == "abandoned", 20, "the old Home chat to close")
    stack.wait_for(lambda: _running(stack, two["id"]), 40, "the fresh chat agent")
    assert "hello there" not in _screen(stack, two["id"]), "nothing of the old conversation"
    gone = stack.home / "work" / "worktrees" / f"chat-{one['id']}"
    stack.wait_for(lambda: not gone.exists(), 20, "the old Home chat's directory to go")
    assert (stack.home / "work" / "worktrees" / f"chat-{two['id']}").is_dir()


@needs_tmux
def test_chat_starts_a_project_and_joins_it(stack, app_server):
    stack.post("/profiles", {"name": "duo", "note": "for the chat test", "lead": {"adapter": "claude_code", "model": "opus"},
                             "coders": [{"adapter": "claude_code", "model": "sonnet", "count": 1}]})
    assert stack.post("/settings", {"default_profile": "duo", "chat_model": "haiku", "chat_effort": "low"})["default_profile"] == "duo"
    chat = stack.post("/chats/home", {"fresh": True, "host": HOST})
    cid = chat["id"]
    stack.wait_for(lambda: _running(stack, cid), 40, "the chat agent")
    screen = stack.wait_for(lambda: (lambda x: x if "hiveswarm tools" in x else "")(_screen(stack, cid)), 30, "the chat agent's prompt")
    assert "model=haiku" in screen and "effort=low" in screen, "the chat model is the app-wide setting"
    prompt = stack.get(f"/chats/{cid}/prompt")["system_prompt"]
    assert "hm_start_project" in prompt and "duo (default)" in prompt and "demo" in prompt

    stack.post(f"/sessions/{cid}/send", {"text": "start orbit: Build a tiny orbit calculator with tests"})
    stack.wait_for(lambda: stack.task(cid)["task"]["project"] == "orbit", 40, "the chat agent to join the project it started")
    projects = stack.get("/projects")
    assert "orbit" in projects and projects["orbit"]["profile"] == "duo", "the default profile"
    assert (stack.home / "repos" / "orbit" / ".git").exists()
    assert stack.session(cid)["name"] == "chat-orbit" and stack.session(cid)["role"] == "chat"
    lead = stack.wait_for(lambda: next((x for x in stack.get("/leads") if x["project"] == "orbit" and x["lead"]), None), 20, "the lead")["lead"]
    assert "orbit calculator" in lead["spec"] and lead["session"]["agent"] == "duo-lead"
    httpx.post(app_server.url + "/api/local/materialize", timeout=60)  # the app writes the profile's agent tables (it also does so by itself)
    stack.wait_for(lambda: _running(stack, lead["id"]), 60, "the lead to start on the profile's lead agent")
    stack.wait_for(lambda: "Started orbit on duo" in _screen(stack, cid), 30, "the chat agent's confirmation")
    assert stack.get("/projects/orbit/settings")["task_count"] >= 1, "the lead started the project"

    r = stack.http.post(f"/chats/{cid}/start_project", json={"name": "second", "brief": "x"})
    assert r.status_code == 409 and "belongs to project orbit" in r.text, "a chat agent starts one project"
    assert "second" not in stack.get("/projects")
    assert stack.post("/chats/project/orbit", {"host": HOST})["id"] == cid, "the project's chat agent is the one that started it"

    home = stack.post("/chats/home", {"host": HOST})
    assert home["created"] and home["id"] != cid, "Home gets a chat agent of its own again"
    stack.wait_for(lambda: _running(stack, home["id"]), 40, "the next Home chat (the spare lane)")
    assert stack.state(cid) == "running", "and the project's chat agent stays"
    stack.wait_for(lambda: (_chat_agent(stack) or {}).get("capacity", 0) >= 3, 40, "a lane per open chat plus a spare")
    stack.post("/settings", {"default_profile": "", "chat_model": "", "chat_effort": ""})


@needs_tmux
def test_project_chat_briefs_the_lead_and_reopens_with_its_conversation(stack, app_server):
    cid = stack.post("/chats/project/orbit", {"host": HOST})["id"]
    lead = stack.post("/leads", {"project": "orbit"})["id"]
    stack.wait_for(lambda: (stack.session(lead).get("attention") or {}).get("kind") == "question", 90, "the lead to plan and ask")
    stack.post(f"/sessions/{lead}/answer", {"choice": "2"})
    stack.wait_for(lambda: stack.session(lead).get("turn") == "idle", 180, "the lead to finish its turn")

    stack.post(f"/sessions/{cid}/send", {"text": "change of plan: pause and use ellipses instead of circles"})
    stack.wait_for(lambda: stack.get("/projects")["orbit"]["paused"], 30, "the chat agent to pause the project")
    stack.wait_for(lambda: "ellipses instead of circles" in stack.log_text(lead), 60, "the brief to reach the lead's terminal")
    assert not stack.get("/projects/orbit/briefs", undelivered="true")["briefs"]
    stack.post("/projects/orbit/resume")

    sid = stack.session(cid)["claude_session_id"]
    assert sid
    stack.post(f"/sessions/{cid}/send", {"text": "exit"})
    stack.wait_for(lambda: stack.state(cid) == "done", 40, "the chat agent to close")
    again = stack.post("/chats/project/orbit", {"host": HOST})
    assert again["id"] == cid and again["reopened"]
    stack.wait_for(lambda: _running(stack, cid), 40, "the chat agent to reopen")
    screen = stack.wait_for(lambda: (lambda x: x if "hiveswarm tools" in x else "")(_screen(stack, cid)), 30, "its prompt")
    assert f"resumed {sid[:8]}" in screen and "chat agent of orbit" in screen and "resumed" in screen
    prompt = stack.get(f"/chats/{cid}/prompt")["system_prompt"]
    assert "chat agent of project `orbit`" in prompt and "hm_start_project" not in prompt.split("## How to answer")[0].split("cannot start another project")[0]
    for t in stack.get("/tasks", project="orbit"):
        stack.post(f"/tasks/{t['id']}/cancel")
    stack.http.delete("/projects/orbit")
    httpx.post(app_server.url + "/api/local/materialize", timeout=60)
    assert stack.http.delete("/profiles/duo").json()["ok"]
    for t in stack.get("/tasks", project="_home"):
        stack.post(f"/tasks/{t['id']}/cancel")


@needs_tmux
def test_a_chat_agent_whose_command_line_is_longer_than_tmux_takes_still_starts(stack):
    """tmux refuses a command of more than 16 KB. A plan full of quotes makes the chat agent's quoted system prompt
    longer than that, so the worker starts it from a script."""
    import hashlib
    import shlex
    repo = make_repo(stack.home / "repos" / "longplan")
    stack.post("/projects", {"name": "longplan", "repo_path": str(repo)})
    try:
        stack.put("/projects/longplan/plan", {"text": "## Plan\n" + ("- keep the user's " + "'" * 40 + "quoted" + "'" * 40 + " names\n") * 80, "by": "test"})
        cid = stack.post("/chats/project/longplan", {"host": HOST})["id"]
        stack.wait_for(lambda: _running(stack, cid), 60, "the chat agent to start")
        prompt = stack.get(f"/chats/{cid}/prompt")["system_prompt"]
        assert len(shlex.quote(prompt).encode()) > 16384, "the test needs a command line tmux would refuse"
        stack.post(f"/sessions/{cid}/send", {"text": "prompt?"})
        want = f"system prompt: {len(prompt)} chars, sha1 {hashlib.sha1(prompt.encode()).hexdigest()[:12]}"
        stack.wait_for(lambda: want in _screen(stack, cid).replace("\n", ""), 30, f"the whole system prompt to arrive ({want})")
        script = Path(tempfile.gettempdir()) / f"hiveswarm-launch-{os.getuid()}" / f"{cid}.sh"
        assert script.exists() and oct(script.stat().st_mode & 0o777) == "0o600", "the script holds the hub token: only its owner reads it"
        stack.post(f"/tasks/{cid}/cancel")
        stack.wait_for(lambda: not script.exists(), 30, "the script to be removed when the chat agent ends")
    finally:
        for t in stack.get("/tasks", project="longplan"):
            stack.post(f"/tasks/{t['id']}/cancel")
        stack.http.delete("/projects/longplan")


@needs_tmux
def test_a_chat_agent_closed_and_reopened_at_once_stays_open(stack):
    """Closing a chat agent and reopening it straight away puts two lanes on one session for a moment: the one still
    closing the last run must not close the window of the new one."""
    repo = make_repo(stack.home / "repos" / "bounce")
    stack.post("/projects", {"name": "bounce", "repo_path": str(repo)})
    try:
        cid = stack.post("/chats/project/bounce", {"host": HOST})["id"]
        stack.wait_for(lambda: _running(stack, cid), 60, "the chat agent to start")
        for i in range(3):
            win = stack.session(cid)["tmux"]
            stack.post(f"/tasks/{cid}/cancel")
            again = stack.post("/chats/project/bounce", {"host": HOST})
            assert again["id"] == cid and again["reopened"], again
            stack.wait_for(lambda win=win: _running(stack, cid) and stack.session(cid)["tmux"] != win, 60, f"the reopened chat agent (round {i})")
            time.sleep(4)  # the run it replaced has finished closing by now
            assert _running(stack, cid), f"round {i}: {stack.state(cid)} {stack.session(cid).get('turn')}\n{stack.log_text(cid)[-800:]}"
            assert "resumed" in _screen(stack, cid)
    finally:
        for t in stack.get("/tasks", project="bounce"):
            stack.post(f"/tasks/{t['id']}/cancel")
        stack.http.delete("/projects/bounce")


def test_attach_to_an_existing_project_and_reserved_names(stack):
    r = stack.http.post("/projects", json={"name": "_home"})
    assert r.status_code == 400
    r = stack.http.post("/chats/project/nope", json={})
    assert r.status_code == 404
    r = stack.http.post("/settings", json={"default_profile": "no-such"})
    assert r.status_code == 400
    r = stack.http.post("/settings", json={"chat_effort": "bogus"})
    assert r.status_code == 400

    async def start(s):
        return _text(await s.call_tool("hm_start_project", {"name": "x", "brief": "y"}))
    out = asyncio.run(_with_mcp(_mcp_env(stack, "chat"), start))
    assert "not started by Hiveswarm" in out


def test_profiles_have_no_chat_role_and_tolerate_the_old_table(stack):
    p = stack.post("/profiles", {"name": "legacy", "lead": {"adapter": "claude_code"}, "chat": {"model": "sonnet"},
                                 "coders": [{"adapter": "codex", "count": 1}]})
    assert "chat" not in p and p["lead"]["agent_id"] == "legacy-lead"
    cfg = (stack.home / "config.toml").read_text()
    assert "[profiles.legacy.chat]" not in cfg
    with open(stack.home / "config.toml", "a") as f:
        f.write('\n[profiles.legacy.chat]\nmodel = "sonnet"\n')
    time.sleep(1.1)
    got = next(x for x in stack.get("/profiles")["profiles"] if x["name"] == "legacy")
    assert not got.get("error"), "a chat table written by 0.3 is read without complaint"
    stack.delete("/profiles/legacy")
    s = stack.get("/settings")
    assert s["default_profile"] is None and "chat" in s and json.dumps(s)
