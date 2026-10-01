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
        chat = page.locator(".prose-md")
        chat.first.wait_for(timeout=15000)
        assert page.locator(".prose-md strong", has_text="Swarm plan").count() == 1, "the lead's markdown is rendered, not shown raw"
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
