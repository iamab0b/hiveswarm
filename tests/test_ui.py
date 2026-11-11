"""Browser tests of the app against the fake swarm: it opens on one fresh chat terminal, the chat agent starts a
project and the view follows it there, a workspace tiles its agents, and the Control tab holds everything else."""
from __future__ import annotations

import os
import re

import httpx
import pytest

from conftest import make_repo, needs_tmux


def attention(stack, tid: str):
    return (stack.session(tid).get("attention") or {}).get("kind")


def tile(page, role: str):
    return page.locator(f"[data-tile][data-role={role}]")


def screen(stack, tid: str) -> str:
    """What the agent's terminal shows (the page draws it on a canvas, so the text is read from tmux)."""
    import time
    cid = stack.post(f"/sessions/{tid}/send", {"screen": True, "lines": 60})["command_id"]
    for _ in range(40):
        c = stack.get(f"/sessions/commands/{cid}")
        if c.get("done_at"):
            return c.get("result") or ""
        time.sleep(0.25)
    return ""


def wait_chat_terminal(page, workspace: str = "_home", not_id: str | None = None, timeout: int = 40000) -> str:
    """The chat tile of a workspace with its real terminal attached; returns the chat session's id. `not_id` waits
    for a chat agent other than that one (the page may still show the previous workspace for a moment)."""
    sel = f"[data-workspace='{workspace}'] [data-tile][data-role=chat]" + (f":not([data-tile='{not_id}'])" if not_id else "")
    chat = page.locator(sel)
    try:
        chat.locator("[data-term=attach] .xterm-screen").wait_for(timeout=timeout)
    except Exception:
        import json as _j
        api = page.base + "/api"
        agents = [(a["agent_id"], a["alive"], a["capacity"], a["busy"], a.get("desired_capacity")) for a in httpx.get(api + "/agents").json() if a.get("profile") == "_chat"]
        chats = [(t["id"][:8], t["project"], t["state"], _j.loads(t["session"] or "{}").get("tmux"), _j.loads(t["session"] or "{}").get("turn"))
                 for t in httpx.get(api + "/tasks", params={"limit": 300}).json() if '"role": "chat"' in (t.get("session") or "")]
        print("chat agents (id, alive, capacity, busy, desired):", agents)
        print("chat sessions (id, project, state, tmux, turn):", chats)
        print("page:", page.locator("body").inner_text()[:1200])
        raise
    return chat.get_attribute("data-tile")


@pytest.mark.e2e
def test_two_tabs_and_old_addresses(stack, page):
    page.goto(page.base + "/control", wait_until="load")
    page.locator("[data-control]").wait_for(timeout=15000)
    assert [t.inner_text() for t in page.locator("header nav a").all()] == ["Terminals", "Control"]
    for section in ("projects", "subscriptions", "profiles", "agents", "activity"):
        assert page.locator(f"section#{section}").count() == 1, section
    assert page.locator("[data-project-row=demo]").count() == 1
    assert page.locator("[data-subscription=claude_code]").count() == 1 and page.locator("[data-subscription=codex]").count() == 1
    for old in ("/agents", "/stats", "/hive", "/inbox", "/tasks"):
        page.goto(page.base + old, wait_until="load")
        page.wait_for_url("**/control", timeout=10000)
    page.goto(page.base + "/lead/demo", wait_until="load")
    page.wait_for_url("**/p/demo", timeout=10000)
    page.keyboard.press("Shift+?")
    page.locator("[data-help]").wait_for(timeout=5000)
    assert "Ctrl+Shift+H" in page.locator("[data-help]").inner_text()
    page.keyboard.press("Escape")
    assert not page.errors


@needs_tmux
@pytest.mark.e2e
def test_app_opens_on_a_fresh_chat_and_follows_it_into_the_project_it_starts(stack, app_server, page):
    stack.post("/settings", {"default_profile": "", "chat_model": "", "chat_effort": ""})
    page.goto(page.base + "/", wait_until="load")
    first = wait_chat_terminal(page)
    assert page.locator("[data-tile]").count() == 1, "the window is one terminal"
    assert page.locator("[data-current-workspace=home]").count() == 1
    assert stack.task(first)["task"]["project"] == "_home"
    stack.wait_for(lambda: "chat agent at Home" in screen(stack, first), 20, "the chat agent's prompt")

    page.keyboard.type("start uiflow: Build a tiny unit converter with tests")
    page.keyboard.press("Enter")
    page.wait_for_url("**/p/uiflow", timeout=40000)
    try:
        assert tile(page, "chat").get_attribute("data-tile") == first, "the same chat agent, now in the project's workspace"
        lead = tile(page, "lead")
        lead.wait_for(timeout=30000)
        lead.locator("[data-term=attach] .xterm-screen").wait_for(timeout=40000)
        assert page.locator("[data-current-workspace=uiflow]").count() == 1
        assert stack.task(first)["task"]["project"] == "uiflow"
        stack.wait_for(lambda: "Started uiflow" in screen(stack, first), 20, "the chat agent's confirmation")

        page.keyboard.press("Control+Shift+KeyH")
        page.wait_for_url(re.compile(r".*/$"), timeout=10000)
        second = wait_chat_terminal(page, not_id=first)
        assert stack.task(second)["task"]["project"] == "_home", "Home has a chat agent of its own again"
        assert page.locator("[data-tile]").count() == 1

        page.reload(wait_until="load")
        assert wait_chat_terminal(page) == second, "a reload in the same window keeps the Home chat"

        ctx = page.context.browser.new_context(viewport={"width": 1400, "height": 900})
        other = ctx.new_page()
        try:
            other.goto(page.base + "/", wait_until="load")
            other.locator("[data-tile][data-role=chat] [data-term=attach] .xterm-screen").wait_for(timeout=40000)
            third = other.locator("[data-tile][data-role=chat]").get_attribute("data-tile")
            assert third not in (first, second), "the app opening anew gets a fresh chat agent"
            stack.wait_for(lambda: stack.state(second) == "abandoned", 20, "the previous Home chat to close")
            assert stack.state(first) == "running", "the project's chat agent is untouched"
        finally:
            ctx.close()

        names = list(stack.get("/projects").keys())
        page.keyboard.press(f"Control+Shift+Digit{names.index('uiflow') + 1}")
        page.wait_for_url("**/p/uiflow", timeout=10000)
        assert wait_chat_terminal(page, "uiflow") == first
        page.keyboard.press("Control+Shift+KeyP")
        page.wait_for_url("**/control", timeout=10000)
        row = page.locator("[data-project-row=uiflow]")
        row.wait_for(timeout=10000)
        assert f"Ctrl+Shift+{names.index('uiflow') + 1}" in row.inner_text()
        row.locator("[data-open-project=uiflow]").click()
        page.wait_for_url("**/p/uiflow", timeout=10000)
        assert not page.errors
    finally:
        for t in stack.get("/tasks", project="uiflow"):
            stack.post(f"/tasks/{t['id']}/cancel")
        stack.http.delete("/projects/uiflow")
        httpx.post(app_server.url + "/api/local/materialize", timeout=60)


@needs_tmux
@pytest.mark.e2e
def test_workspace_tiles_attention_focus_zoom_and_details(stack, page):
    repo = make_repo(stack.home / "repos" / "tiles")
    stack.post("/projects", {"name": "tiles", "repo_path": str(repo)})
    try:
        lead = stack.post("/leads", {"project": "tiles", "message": "Add a divide() that raises on zero, with tests"})["id"]
        agent = stack.post("/sessions", {"project": "tiles", "spec": "Session in a tile: improve add.py", "permission_mode": "auto", "origin": "test"})["id"]
        stack.wait_for(lambda: attention(stack, agent) == "permission", 60, "the agent's permission request")
        stack.wait_for(lambda: attention(stack, lead) == "question", 90, "the lead's plan question")
        page.goto(page.base + "/p/tiles", wait_until="load")
        chat_id = wait_chat_terminal(page, "tiles")
        assert stack.task(chat_id)["task"]["project"] == "tiles", "a project opened without a chat agent gets one"
        assert tile(page, "chat").get_attribute("data-focused") == "true", "the chat agent has the keyboard when a workspace opens"
        tile(page, "lead").locator("[data-term=attach] .xterm-screen").wait_for(timeout=30000)
        a = page.locator(f"[data-tile='{agent}']")
        a.wait_for(timeout=15000)
        assert [el.get_attribute("data-col") for el in page.locator("[data-col]").all()] == ["chat", "lead", "agents"]
        box = lambda loc: loc.bounding_box()  # noqa: E731
        assert box(tile(page, "chat"))["x"] < box(tile(page, "lead"))["x"] < box(a)["x"], "chat, then the lead, then the agents"
        assert box(tile(page, "chat"))["width"] > 300 and box(tile(page, "lead"))["width"] > 300

        panel = a.locator("[data-attention=permission]")
        panel.wait_for(timeout=10000)
        assert "Needs approval" in panel.inner_text() and a.locator("[data-needs-you]").count() == 1
        panel.locator("button", has_text="Deny").first.click()
        stack.wait_for(lambda: attention(stack, agent) == "question", 60, "the agent to move on to its question")
        a.locator("[data-attention=question] button", has_text="Add docstring").click()
        stack.wait_for(lambda: attention(stack, agent) in ("input", None) and stack.session(agent).get("turn") == "idle", 60, "the agent to finish its turn")
        page.wait_for_timeout(1500)
        assert a.locator("[data-attention]").count() == 0, "an agent at its own prompt is not marked as needing you"

        lead_panel = tile(page, "lead").locator("[data-attention=question]")
        lead_panel.wait_for(timeout=10000)
        lead_panel.locator("button", has_text="Pause after task 1").click()
        stack.wait_for(lambda: attention(stack, lead) != "question", 30, "the answer to reach the lead")

        assert tile(page, "lead").get_attribute("data-focused") == "true", "clicking in a tile focuses it"
        page.keyboard.press("Control+Shift+ArrowLeft")
        page.wait_for_timeout(300)
        assert tile(page, "chat").get_attribute("data-focused") == "true"
        page.keyboard.press("Control+Shift+ArrowRight")
        page.wait_for_timeout(300)
        assert tile(page, "lead").get_attribute("data-focused") == "true"
        page.keyboard.press("Control+Shift+ArrowRight")
        page.wait_for_timeout(300)
        focused = page.locator("[data-tile][data-focused=true]")
        assert focused.get_attribute("data-role") in ("session", "task")
        page.keyboard.press("Control+Shift+KeyZ")
        page.wait_for_timeout(500)
        z = page.locator("[data-tile][data-zoomed=true]")
        assert z.count() == 1 and box(z)["width"] > 1300, "zoom fills the workspace"
        page.keyboard.press("Control+Shift+KeyZ")
        page.wait_for_timeout(300)
        assert page.locator("[data-tile][data-zoomed=true]").count() == 0
        page.keyboard.press("Control+Shift+ArrowLeft")
        page.keyboard.press("Control+Shift+ArrowLeft")
        page.wait_for_timeout(300)
        assert tile(page, "chat").get_attribute("data-focused") == "true"
        keyboard = lambda: page.evaluate("document.activeElement?.closest?.('[data-tile]')?.dataset.role || null")  # noqa: E731
        for i in range(8):  # two quick moves land where the second one points, and the keyboard goes with them
            page.keyboard.press("Control+Shift+ArrowRight")
            page.keyboard.press("Control+Shift+ArrowRight")
            page.wait_for_timeout(150)
            assert page.locator("[data-tile][data-focused=true]").get_attribute("data-role") in ("session", "task"), f"round {i}"
            page.keyboard.press("Control+Shift+ArrowLeft")
            page.keyboard.press("Control+Shift+ArrowLeft")
            page.wait_for_timeout(150)
            assert tile(page, "chat").get_attribute("data-focused") == "true" and keyboard() == "chat", f"round {i}"

        page.keyboard.press("Control+Shift+KeyD")
        detail = page.locator(f"[data-detail='{chat_id}']")
        detail.wait_for(timeout=10000)
        assert "Chat agent" in detail.inner_text()
        page.keyboard.press("Escape")
        detail.wait_for(state="detached", timeout=5000)

        gutter = page.locator("[data-gutter=col]").first
        g = box(gutter)
        before = box(tile(page, "chat"))["width"]
        page.mouse.move(g["x"] + 1, g["y"] + 200)
        page.mouse.down()
        page.mouse.move(g["x"] + 121, g["y"] + 200, steps=6)
        page.mouse.up()
        page.wait_for_timeout(300)
        after = box(tile(page, "chat"))["width"]
        assert 100 < after - before < 140, "a divider drags"
        page.reload(wait_until="load")
        wait_chat_terminal(page, "tiles")
        assert abs(box(tile(page, "chat"))["width"] - after) < 6, "and the dragged size is kept"
        assert not page.errors
    finally:
        for t in stack.get("/tasks", project="tiles"):
            stack.post(f"/tasks/{t['id']}/cancel")
        stack.http.delete("/projects/tiles")


@needs_tmux
@pytest.mark.e2e
def test_a_chat_tile_that_comes_back_leaves_the_keyboard_where_you_put_it(stack, page):
    repo = make_repo(stack.home / "repos" / "late")
    stack.post("/projects", {"name": "late", "repo_path": str(repo)})
    keyboard = lambda: page.evaluate("document.activeElement?.closest?.('[data-tile]')?.dataset.role || null")  # noqa: E731
    try:
        lead = stack.post("/leads", {"project": "late", "message": "Add a divide() that raises on zero, with tests"})["id"]
        stack.wait_for(lambda: attention(stack, lead) == "question", 90, "the lead's plan question")
        page.goto(page.base + "/p/late", wait_until="load")
        chat_id = wait_chat_terminal(page, "late")
        lead_tile = tile(page, "lead")
        lead_tile.locator("[data-term=attach] .xterm-screen").wait_for(timeout=30000)
        stack.wait_for(lambda: keyboard() == "chat", 10, "the chat agent to have the keyboard")

        def close_chat():
            stack.post(f"/tasks/{chat_id}/cancel")
            page.locator("[data-reopen-chat]").wait_for(timeout=30000)
            stack.wait_for(lambda: lead_tile.get_attribute("data-focused") == "true" and keyboard() == "lead", 10, "the keyboard to fall to the lead")

        # you are working in the lead's terminal when the chat agent comes back from elsewhere: nothing moves
        close_chat()
        lead_tile.locator("[data-term=attach]").click()
        assert stack.post("/chats/project/late", {})["id"] == chat_id, "it reopens with its conversation"
        assert wait_chat_terminal(page, "late") == chat_id
        page.wait_for_timeout(1200)
        assert lead_tile.get_attribute("data-focused") == "true" and tile(page, "chat").get_attribute("data-focused") == "false"
        assert keyboard() == "lead", "a tile that appears late does not take the keyboard"

        # asking for it back with the button hands it the keyboard
        close_chat()
        page.locator("[data-reopen-chat]").click()
        assert wait_chat_terminal(page, "late") == chat_id
        stack.wait_for(lambda: tile(page, "chat").get_attribute("data-focused") == "true" and keyboard() == "chat", 10, "the reopened chat agent to have the keyboard")

        # one that was closed comes back by itself the next time the workspace opens
        close_chat()
        page.keyboard.press("Control+Shift+KeyH")
        page.wait_for_url(re.compile(r".*/$"), timeout=10000)
        wait_chat_terminal(page)
        names = list(stack.get("/projects").keys())
        page.keyboard.press(f"Control+Shift+Digit{names.index('late') + 1}")
        page.wait_for_url("**/p/late", timeout=10000)
        assert wait_chat_terminal(page, "late") == chat_id
        stack.wait_for(lambda: keyboard() == "chat", 10, "the chat agent to have the keyboard again")
        assert not page.errors
    finally:
        for t in stack.get("/tasks", project="late"):
            stack.post(f"/tasks/{t['id']}/cancel")
        for t in stack.get("/tasks", project="_home"):
            stack.post(f"/tasks/{t['id']}/cancel")
        stack.http.delete("/projects/late")


@needs_tmux
@pytest.mark.e2e
def test_finished_work_collapses_to_chips_and_an_untested_one_merges_with_an_acknowledgement(stack, page):
    tid = stack.post("/tasks", {"project": "demo", "spec": "Add a docstring to add() [skip tests]",
                                "acceptance": "python3 -c 'import add'", "origin": "test", "agent": "claude_code"})["id"]
    stack.wait_for(lambda: stack.state(tid) == "done", 120, "the task to finish")
    page.goto(page.base + "/p/demo", wait_until="load")
    chip = page.locator(f"[data-done-chip='{tid}']")
    chip.wait_for(timeout=20000)
    assert "untested" in chip.inner_text()
    assert page.locator(f"[data-tile='{tid}']").count() == 0, "a finished task has no tile"
    chip.click()
    detail = page.locator(f"[data-detail='{tid}']")
    detail.wait_for(timeout=10000)
    panel = detail.locator("[data-attention=untested]")
    panel.wait_for(timeout=10000)
    assert "add.py" in panel.inner_text()
    panel.locator("button", has_text="Open the diff").click()
    detail.locator("text=add.py").first.wait_for(timeout=10000)
    detail.locator("button", has_text="Overview").click()
    panel.locator("button", has_text="Merge anyway").click()
    stack.wait_for(lambda: "merged although untested" in stack.log_text(tid), 30, "the merge")
    page.keyboard.press("Escape")
    assert not page.errors


@pytest.mark.e2e
def test_project_settings_hold_the_roster_the_plan_and_the_deferred_ledger(stack, page):
    tid = stack.post("/tasks", {"project": "demo", "spec": "Add a docstring to add() [defer: move add() into math.py]",
                                "acceptance": "python3 -c 'import add'", "origin": "test", "agent": "claude_code"})["id"]
    stack.wait_for(lambda: stack.state(tid) == "done", 120, "the task to finish")
    stack.wait_for(lambda: any(i["task_id"] == tid for i in stack.get("/projects/demo/deferred")["items"]), 20, "the ledger entry")
    stack.put("/projects/demo/plan", {"text": "## Plan\n1. ship add()\n2. ship subtract()", "by": "test"})
    page.goto(page.base + "/control", wait_until="load")
    page.locator("[data-project-settings=demo]").click()
    dialog = page.locator("[data-project-dialog=demo]")
    dialog.wait_for(timeout=10000)
    row = dialog.locator("[data-deferred-count] >> text=move add() into math.py").first
    row.wait_for(timeout=15000)
    row.hover()
    dialog.locator("[data-deferred-count] button", has_text="resolve").first.click()
    stack.wait_for(lambda: not any(i["task_id"] == tid for i in stack.get("/projects/demo/deferred")["items"]), 20, "the item to be resolved")
    dialog.locator("[data-plan] button").click()
    assert "ship subtract()" in dialog.locator("[data-plan]").inner_text()
    roster = dialog.locator("[data-roster]")
    roster.wait_for(timeout=10000)
    roster.locator("button", has_text="Codex").click()
    stack.wait_for(lambda: stack.get("/projects/demo/settings")["agents"] == ["codex"], 10, "the roster to be saved")
    roster.locator("button", has_text="any").click()
    stack.wait_for(lambda: stack.get("/projects/demo/settings")["agents"] == [], 10, "the roster to be cleared")
    assert "chat" not in roster.inner_text().lower(), "the chat agent is not a lane for work"
    page.keyboard.press("Escape")
    assert not page.errors


@pytest.mark.e2e
def test_agent_tables_editor(stack, page):
    def agent(name: str):
        return next((a for a in stack.get("/agents") if a["agent_id"] == name), None)
    page.goto(page.base + "/control", wait_until="load")
    section = page.locator("[data-local-agents]")
    section.wait_for(timeout=15000)
    section.locator("button", has_text="show").click()
    assert section.locator("[data-agent-row=claude_code]").count() == 1 and section.locator("[data-agent-row=codex]").count() == 1
    section.locator("button", has_text="add agent").click()
    draft = section.locator("[data-agent-draft]")
    draft.locator("input").first.fill("ui_agent")
    draft.locator("input[type=number]").fill("1")
    draft.locator("button", has_text="add").click()
    section.locator("[data-agent-row=ui_agent]").wait_for(timeout=15000)
    stack.wait_for(lambda: (agent("ui_agent") or {}).get("alive"), 40, "the worker to register the table added from the app")
    assert agent("ui_agent")["provider"] == "claude_code"
    section.locator("[data-agent-row=ui_agent] button", has_text="remove").click()
    stack.wait_for(lambda: not (agent("ui_agent") or {}).get("alive"), 40, "the table's lanes to retire")

    import tomllib

    def model(name: str):
        return tomllib.loads((stack.home / "worker.toml").read_text())["agents"][name].get("model")
    picker = section.locator("[data-agent-row=codex] [data-model]")
    picker.locator("[role=combobox]").click()
    page.get_by_role("option", name="gpt-5.4", exact=True).click()
    stack.wait_for(lambda: model("codex") == "gpt-5.4", 10, "the model picked from the list to reach worker.toml")
    stack.wait_for(lambda: (agent("codex") or {}).get("model") == "gpt-5.4", 40, "the worker to re-register with the model")
    picker.locator("[role=combobox]").click()
    page.get_by_role("option", name="custom…").click()
    picker.locator("input").fill("my-own-model")
    picker.locator("input").press("Enter")
    stack.wait_for(lambda: model("codex") == "my-own-model", 10, "a custom model to reach worker.toml")
    picker.locator("input").fill("")
    picker.locator("input").press("Enter")
    stack.wait_for(lambda: model("codex") is None, 10, "an empty model to mean the default again")
    picker.locator("[role=combobox]").wait_for(timeout=5000)
    stack.wait_for(lambda: (agent("codex") or {}).get("model") is None, 40, "the worker to re-register without a model")
    assert not page.errors


@needs_tmux
@pytest.mark.e2e
def test_chat_model_and_history_on_the_control_tab(stack, page):
    page.goto(page.base + "/control", wait_until="load")
    setting = page.locator("[data-chat-setting]")
    setting.wait_for(timeout=15000)
    setting.locator("[data-chat-model] [role=combobox]").click()
    page.get_by_role("option", name="haiku", exact=True).click()
    stack.wait_for(lambda: stack.get("/settings")["chat"]["model"] == "haiku", 10, "the chat model to be saved on the hub")
    setting.locator("[data-chat-effort] [role=combobox]").click()
    page.get_by_role("option", name="low", exact=True).click()
    stack.wait_for(lambda: stack.get("/settings")["chat"]["effort"] == "low", 10, "the chat effort to be saved")
    try:
        chat = stack.post("/chats/home", {"fresh": True, "host": os.uname().nodename})["id"]
        stack.wait_for(lambda: stack.session(chat).get("model") == "haiku" and stack.session(chat).get("effort") == "low", 10, "the next chat agent to use it")
        stack.post(f"/tasks/{chat}/cancel")
    finally:
        stack.post("/settings", {"chat_model": "", "chat_effort": ""})
    history = page.locator("[data-history]")
    history.scroll_into_view_if_needed()
    row = history.locator("[data-history-row]").first
    row.wait_for(timeout=10000)
    tid = row.get_attribute("data-history-row")
    assert stack.task(tid)["task"]["project"] != "_home", "Home's chat is not history"
    row.click()
    page.locator(f"[data-detail='{tid}']").wait_for(timeout=10000)
    page.keyboard.press("Escape")
    assert not page.errors


def test_the_page_is_never_served_from_a_stale_cache(app_server):
    """An engine upgrade must show its own page on the next load: index.html says no-cache, the hashed assets
    are the only thing a browser may keep."""
    index = httpx.get(app_server.url + "/", timeout=10)
    assert index.status_code == 200 and index.headers["cache-control"] == "no-cache"
    assert httpx.get(app_server.url + "/control", timeout=10).headers["cache-control"] == "no-cache", "every route serves index.html"
    assert httpx.head(app_server.url + "/", timeout=10).headers["cache-control"] == "no-cache", "HEAD answers like GET"
    asset = re.search(r'src="(/assets/[^"]+\.js)"', index.text).group(1)
    r = httpx.get(app_server.url + asset, timeout=10)
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    assert '__APP_VERSION__' not in index.text


def test_subscriptions_are_read_from_this_machine(app_server):
    r = httpx.get(app_server.url + "/api/local/subscriptions", timeout=20).json()
    subs = {s["id"]: s for s in r["subscriptions"]}
    assert r["host"] and {"claude_code", "codex"} <= set(subs)
    assert subs["claude_code"]["installed"] and subs["codex"]["installed"], "the fake CLIs are on PATH"
    assert "claude_code" in subs["claude_code"]["tables"] and "codex" in subs["codex"]["tables"] and subs["claude_code"]["fix"]
    assert subs["claude_code"]["signed_in"] in (True, False)
