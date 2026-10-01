"""Projects (create, local copies, delete), the stall watch, the MCP server and the CLI."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_new_project_gets_a_local_copy_and_can_be_deleted(stack):
    r = stack.post("/projects", {"name": "scratch"})
    assert r["ok"] and r["created"]
    local = stack.home / "local" / "scratch"
    stack.wait_for(lambda: (local / ".git").exists(), 60, "the worker to clone a local copy")
    stack.wait_for(lambda: (stack.get("/projects")["scratch"].get("local") or {}).get("path"), 40, "the local copy to be reported")
    t = stack.post("/tasks", {"project": "scratch", "spec": "a task to be swept away", "origin": "test"})
    d = stack.delete("/projects/scratch", purge="true")
    assert d["ok"] and d["repo_removed"] and d["tasks_removed"] == 1
    assert "scratch" not in stack.get("/projects")
    assert stack.http.get(f"/tasks/{t['id']}").status_code == 404
    stack.wait_for(lambda: not local.exists() and any((stack.home / "local" / ".trash").glob("scratch-*")), 60,
                   "the worker to move the local copy to trash")


def test_stall_watch_flags_and_clears():
    """Pure daemon logic against a scratch database: one lane stuck on a tool call gets a `stalled` flag that
    disappears when the result arrives."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.toml"
        cfg.write_text(f'[paths]\ndb = "{tmp}/db.sqlite"\nworktrees = "{tmp}/wt"\n[watch]\nstall_minutes = 3\n')
        os.environ["HIVESWARM_CONFIG"] = str(cfg)
        sys.path.insert(0, str(ROOT / "src"))
        from hiveswarm import db, perf
        from hiveswarm.config import load
        load.cache_clear()
        db.migrate()
        tid = db.new_id()
        now = db.now()
        db.run("INSERT INTO tasks (id, project, spec, repo_path, base_ref, state, claimed_by, attempts, max_attempts, created_at, updated_at, kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               tid, "p", "stall", "/x", "HEAD", "running", "codex", 1, 1, now - 400, now - 400, "task")
        db.run("INSERT INTO task_logs (task_id, seq, ts, source, chunk) VALUES (?,?,?,?,?)", tid, 1, now - 250, "codex:tool", "Bash: python -c 'import x'")
        assert perf.check_stalls() == 1
        flags = db.flags_get(tid)
        assert flags and flags[0]["kind"] == "stalled" and "4 min" in flags[0]["summary"]
        db.log_append(tid, "codex:result", "ok")
        assert perf.check_stalls() == 0
        assert db.flags_get(tid) == []
        m = perf.analyze(perf._agent_rows(tid, "codex"), start=now - 400, now=now)
        assert m["steps"] == 1 and m["step_max_s"] >= 250
        os.environ.pop("HIVESWARM_CONFIG", None)
        load.cache_clear()


def test_mcp_server_lists_tools_and_answers(stack):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def go():
        params = StdioServerParameters(command=sys.executable, args=["-m", "hiveswarm.mcp_server"],
                                       env={**stack.env, "HIVESWARM_ORIGIN": "lead test"})
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = {t.name for t in (await s.list_tools()).tools}
                assert {"hm_status", "hm_advise", "hm_add_tasks", "hm_wait", "hm_directive", "hm_stats", "hm_live", "hm_delete_project"} <= names
                res = await s.call_tool("hm_status", {})
                text = "".join(c.text for c in res.content if getattr(c, "text", None))
                status = json.loads(text)
                assert status["dispatcher_alive"] and any(a["alive"] for a in status["agents"])
                res = await s.call_tool("hm_advise", {"project": "demo", "tasks": [{"spec": "Add subtract() to add.py with a test"}]})
                adv = json.loads("".join(c.text for c in res.content if getattr(c, "text", None)))
                assert adv["tasks"][0]["agent"] in ("claude_code", "codex")
    asyncio.run(go())


def test_cli_init_and_version(tmp_path):
    env = {**os.environ, "HIVESWARM_HOME": str(tmp_path / "home"), "PYTHONPATH": str(ROOT / "src"),
           "PATH": f"{ROOT / 'tests' / 'fakes'}:{os.environ.get('PATH', '')}"}
    for k in ("HIVESWARM_CONFIG", "HIVESWARM_URL", "HIVESWARM_TOKEN", "HIVEMIND_URL", "HIVEMIND_TOKEN", "HIVEMIND_CONFIG"):
        env.pop(k, None)
    r = subprocess.run([sys.executable, "-m", "hiveswarm.cli", "init", "--project", "first"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    home = tmp_path / "home"
    assert (home / "config.toml").exists() and (home / "worker.toml").exists() and (home / "env").exists()
    assert (home / "repos" / "first" / ".git").exists()
    assert "claude_code" in (home / "worker.toml").read_text()
    r2 = subprocess.run([sys.executable, "-m", "hiveswarm.cli", "init"], env=env, capture_output=True, text=True)
    assert r2.returncode == 2 and "already exists" in r2.stderr
    v = subprocess.run([sys.executable, "-m", "hiveswarm.cli", "--version"], env=env, capture_output=True, text=True)
    assert v.stdout.startswith("hiveswarm ")


def test_env_var_compat_shim(tmp_path):
    env = {**os.environ, "HIVESWARM_HOME": str(tmp_path), "PYTHONPATH": str(ROOT / "src"), "HIVEMIND_URL": "http://example:1"}
    env.pop("HIVESWARM_URL", None)
    out = subprocess.run([sys.executable, "-c", "import hiveswarm, os; print(os.environ['HIVESWARM_URL'])"], env=env, capture_output=True, text=True)
    assert out.stdout.strip() == "http://example:1"


@pytest.mark.e2e
def test_web_app_renders(page):
    """The packaged app serves and its pages render (needs `playwright install chromium`)."""
    page.goto(page.base + "/", wait_until="load")
    page.wait_for_timeout(2500)
    assert page.locator("text=Swarm").first.is_visible()
    page.goto(page.base + "/stats", wait_until="load")
    page.wait_for_timeout(2000)
    assert page.locator("text=Agent performance").first.is_visible()
    page.goto(page.base + "/lead/demo", wait_until="load")
    page.wait_for_timeout(2000)
    assert page.locator("text=Lead").first.is_visible()
