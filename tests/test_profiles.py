"""Profiles: which models lead, chat and code for a project, saved on the hub, written into the worker.toml of the
machine running the app, fixed once the project has started (lane counts aside)."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import make_repo  # noqa: E402

DEEP = {"name": "deep", "note": "Opus leads, Opus medium codes, Codex tests",
        "lead": {"adapter": "claude_code", "model": "opus", "effort": "max"},
        "chat": {"model": "sonnet", "effort": "low"},
        "coders": [{"adapter": "claude_code", "model": "opus", "effort": "medium", "count": 2},
                   {"adapter": "codex", "model": "gpt-5.5", "effort": "high", "count": 1}]}


def _agent(stack, name: str):
    return next((a for a in stack.get("/agents") if a["agent_id"] == name), None)


def _tables(stack) -> dict:
    return tomllib.loads((stack.home / "worker.toml").read_text()).get("agents") or {}


def test_profile_normalizes_merges_and_refuses():
    from hiveswarm import profiles
    p = profiles.normalize("deep", {**DEEP, "coders": DEEP["coders"] + [{"adapter": "claude_code", "model": "opus", "effort": "medium"},
                                                                      {"adapter": "prime_agent", "count": 4}]})
    assert list(p["coders"]) == ["claude-opus-medium", "codex-gpt-5-5-high", "prime"]
    assert p["coders"]["claude-opus-medium"]["count"] == 3 and p["coders"]["prime"]["count"] == 1
    assert p["chat"] == {"adapter": "claude_code", "model": "sonnet", "effort": "low"}
    assert profiles.lead_id("deep") == "deep-lead" and profiles.coder_id("deep", "codex-gpt-5-5-high") == "deep-codex-gpt-5-5-high"
    for bad, why in [({"coders": []}, "at least one coder"),
                     ({"lead": {"adapter": "prime_agent"}, "coders": [{"adapter": "codex"}]}, "hub lane"),
                     ({"chat": {"adapter": "codex"}, "coders": [{"adapter": "codex"}]}, "only claude_code"),
                     ({"coders": [{"adapter": "codex", "effort": "max"}]}, "effort for codex"),
                     ({"coders": [{"adapter": "cursor", "effort": "high"}]}, "no effort setting"),
                     ({"coders": [{"adapter": "codex", "count": 99}]}, "between 1 and 32")]:
        with pytest.raises(profiles.ProfileError, match=why):
            profiles.normalize("x", bad)
    with pytest.raises(profiles.ProfileError, match="name"):
        profiles.normalize("bad name!", {"coders": [{"adapter": "codex"}]})


def test_profile_runs_a_project_and_locks_once_started(stack, app_server):
    http = httpx.Client(base_url=app_server.url, timeout=60)
    r = stack.post("/profiles", DEEP)
    assert r["lead"]["agent_id"] == "deep-lead" and [c["agent_id"] for c in r["coders"]] == ["deep-claude-opus-medium", "deep-codex-gpt-5-5-high"]
    assert not r["locked"] and r["used_by"] == []
    assert "deep" in {p["name"] for p in stack.get("/profiles")["profiles"]}
    assert stack.http.post("/profiles", json={**DEEP, "name": "bad", "coders": []}).status_code == 400

    repo = make_repo(stack.home / "repos" / "profiled")
    stack.post("/projects", {"name": "profiled", "repo_path": str(repo)})
    try:
        s = stack.post("/projects/profiled/settings", {"profile": "deep"})
        assert s["profile"] == "deep" and not s["started"]
        assert stack.get("/projects")["profiled"]["profile"] == "deep"
        wanted = stack.get("/profiles/wanted")["tables"]
        assert wanted["deep-lead"]["concurrency"] == 1 and wanted["deep-claude-opus-medium"]["concurrency"] == 2
        assert wanted["deep-codex-gpt-5-5-high"] == {"adapter": "codex", "model": "gpt-5.5", "effort": "high", "concurrency": 1, "profile": "deep", "role": "coder"}

        m = http.post("/api/local/materialize").json()
        assert m["ok"] and "deep-lead added ×1" in m["changes"] and "deep-claude-opus-medium added ×2" in m["changes"], m
        tables = _tables(stack)
        assert tables["deep-claude-opus-medium"]["profile"] == "deep" and tables["deep-claude-opus-medium"]["concurrency"] == 2
        assert tables["deep-lead"]["config_dir"] == tables["claude_code"]["config_dir"], "generated tables borrow the adapter's settings"
        assert http.post("/api/local/materialize").json()["changes"] == [], "a second run changes nothing"
        assert not http.post("/api/local/agents", json={"name": "deep-lead", "concurrency": 5}).json()["ok"], "profile tables are not edited by hand"

        stack.wait_for(lambda: (_agent(stack, "deep-lead") or {}).get("alive"), 40, "the worker to register the lead's table")
        lead = _agent(stack, "deep-lead")
        assert lead["model"] == "opus" and lead["effort"] == "max" and lead["configured"] == 1 and lead["profile"] == "deep"
        stack.wait_for(lambda: (_agent(stack, "deep-claude-opus-medium") or {}).get("capacity") == 2, 40, "two Opus medium lanes")

        other = stack.post("/tasks", {"project": "demo", "spec": "Add a docstring to add()", "acceptance": "python3 -c 'import add'", "origin": "test"})["id"]
        stack.wait_for(lambda: stack.state(other) == "done", 90, "a task of a project without a profile")
        assert stack.task(other)["attempts"][-1]["agent"] in ("claude_code", "codex"), "a profile's lanes serve the projects on it"

        view = stack.get("/projects/profiled/profile")
        assert view["profile"] == "deep" and not view["locked"] and view["resolved"]["lead"]["live"]["alive"]
        assert [c["count"] for c in view["resolved"]["coders"]] == [2, 1]

        tid = stack.post("/tasks", {"project": "profiled", "spec": "Add a docstring to add()", "acceptance": "python3 -c 'import add'", "origin": "test"})["id"]
        stack.wait_for(lambda: stack.state(tid) == "done", 90, "the task to finish on a profile coder")
        assert stack.task(tid)["attempts"][-1]["agent"] in ("deep-claude-opus-medium", "deep-codex-gpt-5-5-high")

        assert stack.get("/projects/profiled/settings")["started"]
        locked = stack.http.post("/projects/profiled/settings", json={"profile": ""})
        assert locked.status_code == 409 and "stays deep" in locked.json()["detail"]
        assert stack.http.post("/projects/profiled/settings", json={"agents": ["codex"]}).status_code == 400
        s = stack.post("/projects/profiled/settings", {"lanes": {"deep-claude-opus-medium": 3}})
        assert s["lanes"] == {"deep-claude-opus-medium": 3}
        assert stack.http.post("/projects/profiled/settings", json={"lanes": {"codex": 3}}).status_code == 400
        assert stack.get("/profiles/wanted")["tables"]["deep-claude-opus-medium"]["concurrency"] == 3
        assert "deep-claude-opus-medium concurrency=3" in http.post("/api/local/materialize").json()["changes"]
        stack.wait_for(lambda: (_agent(stack, "deep-claude-opus-medium") or {}).get("capacity") == 3, 40, "the third lane")
        assert stack.get("/projects/profiled/profile")["resolved"]["coders"][0]["count"] == 3

        changed = stack.http.post("/profiles", json={**DEEP, "coders": DEEP["coders"][:1]})
        assert changed.status_code == 409 and "profiled" in changed.json()["detail"]
        assert stack.post("/profiles", {**DEEP, "note": "a new note"})["note"] == "a new note"
        assert stack.http.delete("/profiles/deep").status_code == 409

        lead_task = stack.post("/leads", {"project": "profiled"})
        assert stack.session(lead_task["id"])["agent"] == "deep-lead"
        stack.post(f"/tasks/{lead_task['id']}/cancel")

        stack.post("/advisor/profiled/talk", {"text": "hello"})
        handed = stack.get("/sessions/commands", host="test-host", advisor_ok="true", wait=0)["advisor"]
        assert handed and handed[0]["model"] == "sonnet" and handed[0]["effort"] == "low"
        stack.post(f"/advisor/turns/{handed[0]['id']}/done", {"host": "test-host", "error": "test"})
    finally:
        stack.http.delete("/projects/profiled")
        http.post("/api/local/materialize")
    assert not any(t.get("profile") for t in _tables(stack).values()), "the project is gone, so are its tables"
    stack.wait_for(lambda: not (_agent(stack, "deep-lead") or {}).get("alive"), 40, "the lead's lanes to retire")
    assert stack.http.delete("/profiles/deep").json()["ok"]
    assert "deep" not in {p["name"] for p in stack.get("/profiles")["profiles"]}


@pytest.mark.e2e
def test_profile_from_the_app(stack, page):
    """Make a profile on the Agents page, put a project on it from the New goal dialog, start its lead, change a
    coder's lanes from the Lead page strip, and find the profile locked for that project."""
    page.goto(page.base + "/agents", wait_until="load")
    page.locator("[data-new-profile]").click()
    editor = page.locator("[data-profile-editor]")
    editor.wait_for(timeout=10000)
    editor.locator("[data-profile-name]").fill("ui")
    lead = editor.locator("[data-role-fields=lead]")
    lead.locator("[data-model] [role=combobox]").click()
    page.get_by_role("option", name="opus", exact=True).click()
    lead.locator("[role=combobox]").nth(2).click()
    page.get_by_role("option", name="max", exact=True).click()
    editor.locator("button", has_text="add coder").click()
    coders = editor.locator("[data-role-fields=coder]")
    assert coders.count() == 2
    coders.nth(1).locator("[role=combobox]").first.click()
    page.get_by_role("option", name="Codex", exact=True).click()
    coders.nth(1).locator("button[aria-label='more lanes']").click()
    editor.locator("button", has_text="Create profile").click()
    card = page.locator("[data-profile-card=ui]")
    card.wait_for(timeout=10000)
    assert "opus" in card.inner_text() and "×2" in card.inner_text()
    prof = next(p for p in stack.get("/profiles")["profiles"] if p["name"] == "ui")
    assert prof["lead"]["model"] == "opus" and prof["lead"]["effort"] == "max"
    assert [(c["agent_id"], c["count"]) for c in prof["coders"]] == [("ui-claude", 1), ("ui-codex", 2)]

    repo = make_repo(stack.home / "repos" / "uiprof")
    stack.post("/projects", {"name": "uiprof", "repo_path": str(repo)})
    lead_task = None
    try:
        page.goto(page.base + "/", wait_until="load")
        page.wait_for_timeout(500)
        page.keyboard.press("g")
        dialog = page.locator("[role=dialog]")
        dialog.wait_for(timeout=10000)
        dialog.locator("[role=combobox]").first.click()
        page.get_by_role("option", name="uiprof", exact=True).click()
        picker = dialog.locator("[data-profile-picker]")
        picker.wait_for(timeout=10000)
        assert picker.get_attribute("data-locked") == "false"
        picker.locator("[role=combobox]").click()
        page.get_by_role("option", name="ui", exact=True).click()
        stack.wait_for(lambda: stack.get("/projects/uiprof/settings")["profile"] == "ui", 10, "the profile to be saved on the project")
        picker.locator("[data-profile-card=ui]").wait_for(timeout=10000)
        stack.wait_for(lambda: "ui-lead" in _tables(stack), 15, "the app to write the profile's tables")
        stack.wait_for(lambda: (_agent(stack, "ui-lead") or {}).get("alive"), 40, "the lead's lane to register")

        dialog.locator("button", has_text="Lead").first.click()
        assert "the lead of profile" in dialog.inner_text()
        dialog.locator("textarea").fill("Say hello and stop.")
        dialog.locator("button", has_text="Start the lead").click()
        stack.wait_for(lambda: stack.get("/projects/uiprof/settings")["started"], 15, "the lead's task to exist")
        lead_task = next(t for t in stack.get("/tasks", project="uiprof") if t["kind"] == "session")
        assert stack.session(lead_task["id"])["agent"] == "ui-lead"

        page.goto(page.base + "/lead/uiprof", wait_until="load")
        strip = page.locator("[data-profile-strip]")
        strip.wait_for(timeout=15000)
        assert "Profile ui" in strip.inner_text() and "fixed since the project started" in strip.inner_text()
        strip.locator("[data-strip-coder=ui-codex] button[aria-label='more Codex lanes']").click()
        stack.wait_for(lambda: stack.get("/projects/uiprof/settings")["lanes"] == {"ui-claude": 1, "ui-codex": 3}, 10, "the count to be saved for the project")
        stack.wait_for(lambda: (_agent(stack, "ui-codex") or {}).get("capacity") == 3, 40, "the third Codex lane")

        page.keyboard.press("g")
        dialog.wait_for(timeout=10000)
        dialog.locator("[role=combobox]").first.click()
        page.get_by_role("option", name="uiprof", exact=True).click()
        picker = dialog.locator("[data-profile-picker][data-locked=true]")
        picker.wait_for(timeout=10000)
        assert "fixed since uiprof started" in picker.inner_text()
        page.keyboard.press("Escape")
        assert not page.errors
    finally:
        if lead_task:
            stack.post(f"/tasks/{lead_task['id']}/cancel")
        stack.http.delete("/projects/uiprof")
        httpx.post(page.base + "/api/local/materialize", timeout=60)
    stack.wait_for(lambda: not (_agent(stack, "ui-lead") or {}).get("alive"), 40, "the profile's lanes to retire")
    assert stack.http.delete("/profiles/ui").json()["ok"]
