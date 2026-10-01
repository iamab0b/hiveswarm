"""Browser tests of the app against the fake swarm: every page renders without console errors, the lead chat shows
markdown and collapsed tool calls, and the attention panel answers a question."""
from __future__ import annotations

import pytest

PAGES = ["/", "/inbox", "/hive", "/tasks", "/stats", "/agents"]


def attention(stack, tid: str):
    return (stack.session(tid).get("attention") or {}).get("kind")


@pytest.mark.e2e
def test_every_page_renders(page):
    for path in PAGES:
        page.goto(page.base + path, wait_until="load")
        page.wait_for_timeout(800)
        assert page.locator("header nav").first.is_visible(), path
    assert not page.errors


@pytest.mark.e2e
def test_lead_page_renders_markdown_tool_calls_and_answers(stack, page):
    lead = stack.post("/sessions", {"project": "demo", "spec": "Add a divide() that raises on zero, with tests",
                                    "permission_mode": "auto", "lead": True, "persistent": True, "origin": "test"})["id"]
    try:
        stack.wait_for(lambda: attention(stack, lead) == "question", 90, "the lead's plan question")
        page.goto(page.base + "/lead/demo", wait_until="load")
        plan = page.locator(".prose-md strong", has_text="Swarm plan")
        plan.first.wait_for(timeout=20000)
        assert plan.count() == 1, "the lead's markdown is rendered, not shown raw"
        assert page.locator(".prose-md li").count() >= 2
        assert page.locator(".prose-md code").count() >= 1
        tools = page.locator("button", has_text="tool call")
        assert tools.count() >= 1
        tools.first.click()
        page.wait_for_timeout(300)
        assert page.locator("text=▸ hm_status").first.is_visible()
        panel = page.locator("[data-attention=question]").first
        panel.wait_for(timeout=10000)
        assert "Asks you" in panel.inner_text()
        panel.locator("button", has_text="One wave, go").click()
        stack.wait_for(lambda: attention(stack, lead) != "question", 30, "the answer to reach the lead")
        page.wait_for_timeout(1500)
        assert not page.errors
    finally:
        stack.post(f"/tasks/{lead}/cancel")


@pytest.mark.e2e
def test_untested_task_waits_in_the_inbox_and_merges_with_an_acknowledgement(stack, page):
    tid = stack.post("/tasks", {"project": "demo", "spec": "Add a docstring to add() [skip tests]",
                                "acceptance": "python3 -c 'import add'", "origin": "test", "agent": "claude_code"})["id"]
    stack.wait_for(lambda: stack.state(tid) == "done", 120, "the task to finish")
    stack.wait_for(lambda: any(i["id"] == tid for i in stack.get("/inbox")["items"]), 20, "the inbox item")
    page.goto(page.base + "/inbox", wait_until="load")
    card = page.locator(".card", has_text=tid[:8]).first
    card.wait_for(timeout=15000)
    assert "Untested" in card.inner_text() and "add.py" in card.inner_text()
    card.locator("button", has_text="Merge anyway").click()
    stack.wait_for(lambda: not any(i["id"] == tid for i in stack.get("/inbox")["items"]), 30, "the merge to clear the flag")
    assert "merged although untested" in stack.log_text(tid)
    assert not page.errors


@pytest.mark.e2e
def test_deferred_items_show_on_the_lead_page(stack, page):
    tid = stack.post("/tasks", {"project": "demo", "spec": "Add a docstring to add() [defer: move add() into math.py]",
                                "acceptance": "python3 -c 'import add'", "origin": "test", "agent": "claude_code"})["id"]
    stack.wait_for(lambda: stack.state(tid) == "done", 120, "the task to finish")
    stack.wait_for(lambda: any(i["task_id"] == tid for i in stack.get("/projects/demo/deferred")["items"]), 20, "the ledger entry")
    page.goto(page.base + "/lead/demo", wait_until="load")
    row = page.locator("[data-deferred-count] >> text=move add() into math.py").first
    row.wait_for(timeout=15000)
    row.hover()
    page.locator("[data-deferred-count] button", has_text="resolve").first.click()
    stack.wait_for(lambda: not any(i["task_id"] == tid for i in stack.get("/projects/demo/deferred")["items"]), 20, "the item to be resolved")
    assert not page.errors


@pytest.mark.e2e
def test_profiles_editor_and_roster_picker(stack, page):
    def agent(name: str):
        return next((a for a in stack.get("/agents") if a["agent_id"] == name), None)
    page.goto(page.base + "/agents", wait_until="load")
    section = page.locator("[data-profiles]")
    section.wait_for(timeout=15000)
    assert section.locator("[data-profile=claude_code]").count() == 1 and section.locator("[data-profile=codex]").count() == 1
    section.locator("button", has_text="add profile").click()
    draft = section.locator("[data-profile-draft]")
    draft.locator("input").first.fill("ui_profile")
    draft.locator("input[type=number]").fill("1")
    draft.locator("button", has_text="add").click()
    section.locator("[data-profile=ui_profile]").wait_for(timeout=15000)
    stack.wait_for(lambda: (agent("ui_profile") or {}).get("alive"), 40, "the worker to register the profile from the app")
    assert agent("ui_profile")["provider"] == "claude_code"
    section.locator("[data-profile=ui_profile] button", has_text="remove").click()
    stack.wait_for(lambda: agent("ui_profile") is None or not agent("ui_profile")["alive"], 40, "the profile to retire")

    page.goto(page.base + "/", wait_until="load")
    page.wait_for_timeout(500)
    page.keyboard.press("g")
    roster = page.locator("[data-roster]")
    roster.wait_for(timeout=10000)
    roster.locator("button", has_text="Codex").click()
    stack.wait_for(lambda: stack.get("/projects/demo/settings")["agents"] == ["codex"], 10, "the roster to be saved")
    roster.locator("button", has_text="any").click()
    stack.wait_for(lambda: stack.get("/projects/demo/settings")["agents"] == [], 10, "the roster to be cleared")
    page.keyboard.press("Escape")
    assert not page.errors
